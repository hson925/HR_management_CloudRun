import logging
import os
import re
from urllib.parse import quote
from collections import defaultdict
from flask import request, session, Response
from app.eval_v2.blueprints import eval_v2_api
from app.auth_utils import api_admin_required
from app.extensions import limiter
from app.utils.rate_limit import admin_rate_key
from app.services.firebase_service import get_firestore_client
from app.services.roster_cache_service import get_roster
from app.services.report_service import html_to_pdf
from app.services.audit_service import log_audit
from app.eval_v2.api.common import kst_now, load_snapshot_questions
from app.eval_v2.questions import EVAL_TYPE_LABELS
from app.utils.response import success, error
from app.constants import (
    COL_EVAL_V2_SESSIONS,
    COL_EVAL_V2_RESPONSES,
    COL_EVAL_V2_SUMMARIES,
    COL_EVAL_V2_CAMPUS_SUMMARIES,
)

logger = logging.getLogger(__name__)


_FILENAME_SAFE_RE = re.compile(r'[^A-Za-z0-9._-]+')


def _doc_id(session_id: str, emp_id: str) -> str:
    return f'{session_id}__{emp_id}'


def _safe_ascii(s: str) -> str:
    """Strip to ASCII-safe filename token (letters, digits, dot, underscore, hyphen)."""
    return _FILENAME_SAFE_RE.sub('_', str(s or '')).strip('._-') or 'file'


def _content_disposition(filename_utf8: str) -> str:
    """RFC 5987 compliant Content-Disposition for non-ASCII filenames."""
    ascii_fallback = _safe_ascii(filename_utf8)
    if not ascii_fallback.lower().endswith('.pdf'):
        ascii_fallback += '.pdf'
    encoded = quote(filename_utf8, safe='')
    return f"attachment; filename=\"{ascii_fallback}\"; filename*=UTF-8''{encoded}"


def _roster_map() -> dict:
    """emp_id → {name, campus, eval_type}"""
    m = {}
    for row in get_roster():
        if len(row) < 4:
            continue
        eid = str(row[2]).strip().lower()
        if eid in ('사번', ''):
            continue
        m[eid] = {
            'name': str(row[1]).strip() if len(row) > 1 else '',
            'eval_type': str(row[3]).strip().lower() if len(row) > 3 else '',
            'campus': str(row[4]).strip() if len(row) > 4 else '',
        }
    return m


def _oq_text_map(snapshot: dict, eval_type: str) -> dict:
    """build {qid: text_ko} from open_questions across all roles."""
    oq = {}
    for role_obj in (load_snapshot_questions(snapshot, eval_type) or []):
        if not isinstance(role_obj, dict):
            continue
        for q in role_obj.get('open_questions', []):
            if isinstance(q, dict) and q.get('id'):
                oq[q['id']] = q.get('text_ko') or q.get('text_en') or q['id']
    return oq


def _get_session_info(db, session_id: str) -> dict:
    doc = db.collection(COL_EVAL_V2_SESSIONS).document(session_id).get()
    if doc.exists:
        d = doc.to_dict()
        return {
            'label': d.get('label', session_id),
            'snapshot': d.get('questions_snapshot', {}),
        }
    return {'label': session_id, 'snapshot': {}}


def _build_comments(responses: list, oq_map: dict) -> list:
    """Build comment blocks per rater with open answer text."""
    comments = []
    for resp in responses:
        open_ans = resp.get('open_answers', {})
        open_ans_ko = resp.get('open_answers_ko', {})
        translated = resp.get('translation_status') == 'done'
        answers = []
        for qid, orig in open_ans.items():
            if not orig or not str(orig).strip():
                continue
            if translated and open_ans_ko.get(qid):
                answer_text = open_ans_ko[qid]
                pending = False
            else:
                answer_text = str(orig)
                pending = True
            answers.append({
                'question_ko': oq_map.get(qid, qid),
                'answer': answer_text,
                'translation_pending': pending,
            })
        if answers:
            comments.append({
                'rater_name': resp.get('rater_name', ''),
                'rater_role': resp.get('rater_role', ''),
                'answers': answers,
            })
    return comments


# ── API endpoints ─────────────────────────────────────────────────────────────

@eval_v2_api.route('/analysis/list', methods=['POST'])
@api_admin_required
@limiter.limit('30 per minute', key_func=admin_rate_key)
def api_analysis_list():
    try:
        data = request.get_json(silent=True) or {}
        session_id = str(data.get('sessionId', '')).strip()
        if not session_id:
            return error('sessionId is required', 400)

        db = get_firestore_client()

        # Group responses by emp_id — same (emp/role/name) 그룹의 최신 1건만 채택
        from app.services.report_service import select_effective_responses
        all_session_docs = [doc.to_dict() for doc in db.collection(COL_EVAL_V2_RESPONSES)
                            .where('session_id', '==', session_id).stream()]
        by_emp = defaultdict(list)
        for d in select_effective_responses(all_session_docs):
            by_emp[d.get('emp_id', '')].append(d)

        # Summaries for this session
        summaries = {}
        for doc in db.collection(COL_EVAL_V2_SUMMARIES) \
                .where('session_id', '==', session_id).stream():
            d = doc.to_dict()
            summaries[d.get('emp_id', '')] = d

        rmap = _roster_map()

        teachers = []
        for emp_id, resps in by_emp.items():
            if not emp_id:
                continue
            open_count = sum(
                1 for r in resps
                if any(str(v).strip() for v in r.get('open_answers', {}).values())
            )
            # eval_type: prefer roster, fall back to response
            roster_info = rmap.get(emp_id, {})
            eval_type = roster_info.get('eval_type') or (resps[0].get('eval_type', '') if resps else '')
            summ = summaries.get(emp_id)
            teachers.append({
                'emp_id': emp_id,
                'name': roster_info.get('name', emp_id),
                'campus': roster_info.get('campus', ''),
                'eval_type': eval_type,
                'type_label': EVAL_TYPE_LABELS.get(eval_type, eval_type.upper()),
                'open_count': open_count,
                'summary_status': 'generated' if summ else 'none',
                'generated_at': summ.get('generated_at') if summ else None,
            })

        teachers.sort(key=lambda t: t.get('name', ''))
        return success({'teachers': teachers})
    except Exception:
        logger.exception('api_analysis_list error')
        return error('An internal error occurred.', 500)


@eval_v2_api.route('/analysis/generate', methods=['POST'])
@api_admin_required
@limiter.limit('10 per minute', key_func=admin_rate_key)
def api_analysis_generate():
    try:
        from app.services.openai_service import generate_narrative_summary
        data = request.get_json(silent=True) or {}
        session_id = str(data.get('sessionId', '')).strip()
        emp_id = str(data.get('empId', '')).strip().lower()
        if not session_id or not emp_id:
            return error('sessionId and empId are required', 400)

        db = get_firestore_client()
        from app.services.report_service import select_effective_responses
        raw = [d.to_dict() for d in
               db.collection(COL_EVAL_V2_RESPONSES)
               .where('session_id', '==', session_id)
               .where('emp_id', '==', emp_id).stream()]
        resps = select_effective_responses(raw)

        if not resps:
            return error('No responses found.', 404)

        eval_type = resps[0].get('eval_type', '')
        sess = _get_session_info(db, session_id)
        oq_map = _oq_text_map(sess['snapshot'], eval_type)

        open_texts = []
        for resp in resps:
            open_ans = resp.get('open_answers', {})
            open_ans_ko = resp.get('open_answers_ko', {})
            translated = resp.get('translation_status') == 'done'
            for qid, orig in open_ans.items():
                if not orig or not str(orig).strip():
                    continue
                text = (open_ans_ko[qid] if translated and open_ans_ko.get(qid) else str(orig))
                open_texts.append({'question': oq_map.get(qid, qid), 'answer': text.strip()})

        if not open_texts:
            return error('No open answers found.', 404)

        summary_ko = generate_narrative_summary(open_texts)
        now = kst_now()
        db.collection(COL_EVAL_V2_SUMMARIES).document(_doc_id(session_id, emp_id)).set({
            'session_id': session_id,
            'emp_id': emp_id,
            'summary_ko': summary_ko,
            'generated_at': now,
            'generated_by': session.get('admin_email', ''),
        })

        log_audit('analysis_summary_generate', session.get('admin_email', ''),
                  target=emp_id, details={'session_id': session_id}, category='eval')

        return success({'summary_ko': summary_ko, 'generated_at': now})
    except Exception:
        logger.exception('api_analysis_generate error')
        return error('An internal error occurred.', 500)


@eval_v2_api.route('/analysis/get', methods=['POST'])
@api_admin_required
@limiter.limit('30 per minute', key_func=admin_rate_key)
def api_analysis_get():
    try:
        data = request.get_json(silent=True) or {}
        session_id = str(data.get('sessionId', '')).strip()
        emp_id = str(data.get('empId', '')).strip().lower()
        if not session_id or not emp_id:
            return error('sessionId and empId are required', 400)

        db = get_firestore_client()

        summ_doc = db.collection(COL_EVAL_V2_SUMMARIES).document(_doc_id(session_id, emp_id)).get()
        summ = summ_doc.to_dict() if summ_doc.exists else {}

        from app.services.report_service import select_effective_responses
        raw = [d.to_dict() for d in
               db.collection(COL_EVAL_V2_RESPONSES)
               .where('session_id', '==', session_id)
               .where('emp_id', '==', emp_id).stream()]
        resps = select_effective_responses(raw)

        eval_type = resps[0].get('eval_type', '') if resps else ''
        sess = _get_session_info(db, session_id)
        oq_map = _oq_text_map(sess['snapshot'], eval_type)
        roster_info = _roster_map().get(emp_id, {})

        return success({
            'teacher': {
                'emp_id': emp_id,
                'name': roster_info.get('name', emp_id),
                'campus': roster_info.get('campus', ''),
                'eval_type': eval_type,
                'type_label': EVAL_TYPE_LABELS.get(eval_type, eval_type.upper()),
                'session_label': sess['label'],
            },
            'summary_ko': summ.get('summary_ko', ''),
            'generated_at': summ.get('generated_at', ''),
            'comments': _build_comments(resps, oq_map),
        })
    except Exception:
        logger.exception('api_analysis_get error')
        return error('An internal error occurred.', 500)


def _parse_summary_sections(summary_ko: str) -> list:
    """
    '## 강점\\n본문\\n## 보완점\\n본문...' 형식을 [{'title':..., 'body':...}] 로 파싱.
    헤더가 없으면 단일 섹션으로 반환.
    """
    if not summary_ko or not summary_ko.strip():
        return []
    lines = summary_ko.strip().split('\n')
    sections = []
    current = None
    for ln in lines:
        stripped = ln.strip()
        if stripped.startswith('## '):
            if current:
                sections.append(current)
            current = {'title': stripped[3:].strip(), 'body': ''}
        else:
            if current is None:
                current = {'title': '', 'body': ''}
            current['body'] += (ln + '\n')
    if current:
        sections.append(current)
    for s in sections:
        s['body'] = s['body'].strip()
    return [s for s in sections if s['body'] or s['title']]


@eval_v2_api.route('/analysis/pdf', methods=['POST'])
@api_admin_required
@limiter.limit('20 per minute', key_func=admin_rate_key)
def api_analysis_pdf():
    try:
        data = request.get_json(silent=True) or {}
        session_id = str(data.get('sessionId', '')).strip()
        emp_id = str(data.get('empId', '')).strip().lower()
        include_original = bool(data.get('includeOriginal', False))
        if not session_id or not emp_id:
            return error('sessionId and empId are required', 400)

        db = get_firestore_client()

        summ_doc = db.collection(COL_EVAL_V2_SUMMARIES).document(_doc_id(session_id, emp_id)).get()
        if not summ_doc.exists:
            return error('Summary not generated yet.', 404)
        summ = summ_doc.to_dict()

        from app.services.report_service import select_effective_responses
        raw = [d.to_dict() for d in
               db.collection(COL_EVAL_V2_RESPONSES)
               .where('session_id', '==', session_id)
               .where('emp_id', '==', emp_id).stream()]
        resps = select_effective_responses(raw)

        eval_type = resps[0].get('eval_type', '') if resps else ''
        sess = _get_session_info(db, session_id)
        oq_map = _oq_text_map(sess['snapshot'], eval_type)
        roster_info = _roster_map().get(emp_id, {})
        teacher_name = roster_info.get('name', emp_id)

        summary_sections = _parse_summary_sections(summ.get('summary_ko', ''))
        comments = _build_comments(resps, oq_map) if include_original else []

        context = {
            'teacher_name': teacher_name,
            'emp_id': emp_id.upper(),
            'campus': roster_info.get('campus', ''),
            'eval_type': eval_type,
            'type_label': EVAL_TYPE_LABELS.get(eval_type, eval_type.upper()),
            'session_label': sess['label'],
            'summary_sections': summary_sections,
            'generated_at': summ.get('generated_at', ''),
            'comments': comments,
            'include_original': include_original,
        }

        from jinja2 import Environment, FileSystemLoader, select_autoescape
        template_dir = os.path.join(os.path.dirname(__file__), '..', '..', 'templates', 'eval_v2')
        env = Environment(
            loader=FileSystemLoader(template_dir),
            autoescape=select_autoescape(['html', 'xml']),
        )
        html = env.get_template('analysis_report_template.html').render(**context)
        pdf_bytes = html_to_pdf(html)

        session_tag = _safe_ascii(session_id)
        emp_tag = _safe_ascii(emp_id)
        filename = f'{session_tag}_{emp_tag}_{teacher_name}_analysis.pdf'

        log_audit('analysis_summary_pdf', session.get('admin_email', ''),
                  target=emp_id,
                  details={'session_id': session_id, 'include_original': include_original},
                  category='eval')

        return Response(
            pdf_bytes,
            mimetype='application/pdf',
            headers={'Content-Disposition': _content_disposition(filename)},
        )
    except Exception:
        logger.exception('api_analysis_pdf error')
        return error('An internal error occurred.', 500)


# ── Campus-level aggregated report ────────────────────────────────────────────


def _campus_doc_id(session_id: str, campus: str) -> str:
    # URL-encode 로 non-ASCII 캠퍼스명 보존 (한글 등). _safe_ascii 는 모든 한글을
    # 'file' fallback 으로 squash 해서 서로 다른 캠퍼스끼리 doc ID 충돌 발생.
    slug = quote((campus or '').strip(), safe='').lower() or 'unknown'
    return f'{session_id}__{slug}'


def _campus_teachers(session_id: str, campus: str, db) -> list:
    """
    해당 세션·캠퍼스에 속한 교사 목록 반환.
    responses 에서 emp_id 추출 → roster_map 에서 campus 필드 매칭.
    반환: [{emp_id, name, eval_type, type_label, has_summary, summary_ko}, ...]
    """
    campus_norm = (campus or '').strip()
    rmap = _roster_map()

    # Group responses by emp_id — 같은 (emp/역할/이름) 그룹의 최신 1건만 채택
    from app.services.report_service import select_effective_responses
    raw = [doc.to_dict() for doc in db.collection(COL_EVAL_V2_RESPONSES)
           .where('session_id', '==', session_id).stream()]
    by_emp = defaultdict(list)
    for d in select_effective_responses(raw):
        emp_id = d.get('emp_id', '')
        if emp_id:
            by_emp[emp_id].append(d)

    # Summaries for this session
    summaries = {}
    for doc in db.collection(COL_EVAL_V2_SUMMARIES) \
            .where('session_id', '==', session_id).stream():
        d = doc.to_dict()
        summaries[d.get('emp_id', '')] = d

    teachers = []
    for emp_id, resps in by_emp.items():
        roster_info = rmap.get(emp_id, {})
        if (roster_info.get('campus') or '').strip() != campus_norm:
            continue
        eval_type = roster_info.get('eval_type') or (resps[0].get('eval_type', '') if resps else '')
        summ = summaries.get(emp_id)
        teachers.append({
            'emp_id': emp_id,
            'name': roster_info.get('name', emp_id),
            'eval_type': eval_type,
            'type_label': EVAL_TYPE_LABELS.get(eval_type, eval_type.upper()),
            'has_summary': bool(summ),
            'summary_ko': (summ or {}).get('summary_ko', ''),
            'responses': resps,
        })
    teachers.sort(key=lambda t: t.get('name', ''))
    return teachers


@eval_v2_api.route('/analysis/campus/list', methods=['POST'])
@api_admin_required
@limiter.limit('30 per minute', key_func=admin_rate_key)
def api_analysis_campus_list():
    """
    캠퍼스별 teacher/summary 집계는 클라이언트가 /analysis/list 응답으로 계산한다.
    서버는 eval_v2_campus_summaries 메타데이터만 반환해 중복 쿼리를 제거.
    """
    try:
        data = request.get_json(silent=True) or {}
        session_id = str(data.get('sessionId', '')).strip()
        if not session_id:
            return error('sessionId is required', 400)

        db = get_firestore_client()

        campus_summaries = {}
        for doc in db.collection(COL_EVAL_V2_CAMPUS_SUMMARIES) \
                .where('session_id', '==', session_id).stream():
            d = doc.to_dict()
            campus_key = (d.get('campus') or '').strip()
            if not campus_key:
                continue
            campus_summaries[campus_key] = {
                'campus_summary_status': 'generated',
                'generated_at': d.get('generated_at'),
                'generated_teacher_count': d.get('teacher_count', 0),
            }

        return success({'campus_summaries': campus_summaries})
    except Exception:
        logger.exception('api_analysis_campus_list error')
        return error('An internal error occurred.', 500)


@eval_v2_api.route('/analysis/campus/generate', methods=['POST'])
@api_admin_required
@limiter.limit('10 per minute', key_func=admin_rate_key)
def api_analysis_campus_generate():
    try:
        from app.services.openai_service import generate_campus_summary
        data = request.get_json(silent=True) or {}
        session_id = str(data.get('sessionId', '')).strip()
        campus = str(data.get('campus', '')).strip()
        if not session_id or not campus:
            return error('sessionId and campus are required', 400)

        db = get_firestore_client()
        teachers = _campus_teachers(session_id, campus, db)
        with_summary = [t for t in teachers if t['has_summary'] and t['summary_ko'].strip()]

        if not with_summary:
            return error(
                'No individual teacher summaries available for this campus. Generate them first.',
                400,
                code='NO_SUMMARIES',
            )

        gpt_input = [{'name': t['name'], 'summary': t['summary_ko']} for t in with_summary]
        summary_ko = generate_campus_summary(gpt_input)
        now = kst_now()
        source_emp_ids = [t['emp_id'] for t in with_summary]

        db.collection(COL_EVAL_V2_CAMPUS_SUMMARIES).document(_campus_doc_id(session_id, campus)).set({
            'session_id': session_id,
            'campus': campus,
            'summary_ko': summary_ko,
            'teacher_count': len(with_summary),
            'source_emp_ids': source_emp_ids,
            'generated_at': now,
            'generated_by': session.get('admin_email', ''),
        })

        log_audit('analysis_campus_generate', session.get('admin_email', ''),
                  target=campus,
                  details={'session_id': session_id, 'teacher_count': len(with_summary)},
                  category='eval')

        return success({
            'summary_ko': summary_ko,
            'teacher_count': len(with_summary),
            'generated_at': now,
        })
    except Exception:
        logger.exception('api_analysis_campus_generate error')
        return error('An internal error occurred.', 500)


@eval_v2_api.route('/analysis/campus/pdf', methods=['POST'])
@api_admin_required
@limiter.limit('20 per minute', key_func=admin_rate_key)
def api_analysis_campus_pdf():
    try:
        data = request.get_json(silent=True) or {}
        session_id = str(data.get('sessionId', '')).strip()
        campus = str(data.get('campus', '')).strip()
        include_original = bool(data.get('includeOriginal', False))
        if not session_id or not campus:
            return error('sessionId and campus are required', 400)

        db = get_firestore_client()

        cs_doc = db.collection(COL_EVAL_V2_CAMPUS_SUMMARIES).document(_campus_doc_id(session_id, campus)).get()
        if not cs_doc.exists:
            return error('Campus summary not generated yet.', 404)
        cs = cs_doc.to_dict()

        teachers = _campus_teachers(session_id, campus, db)
        with_summary = [t for t in teachers if t['has_summary'] and t['summary_ko'].strip()]
        if not with_summary:
            return error('No teacher summaries to render.', 404)

        sess = _get_session_info(db, session_id)

        # Build per-teacher blocks
        teacher_blocks = []
        for t in with_summary:
            oq_map = _oq_text_map(sess['snapshot'], t['eval_type'])
            comments = _build_comments(t['responses'], oq_map) if include_original else []
            teacher_blocks.append({
                'name': t['name'],
                'emp_id': t['emp_id'].upper(),
                'eval_type': t['eval_type'],
                'type_label': t['type_label'],
                'summary_sections': _parse_summary_sections(t['summary_ko']),
                'comments': comments,
            })

        context = {
            'campus': campus,
            'session_label': sess['label'],
            'generated_at': cs.get('generated_at', ''),
            'teacher_count': len(with_summary),
            'campus_summary_sections': _parse_summary_sections(cs.get('summary_ko', '')),
            'teacher_blocks': teacher_blocks,
            'include_original': include_original,
            'teacher_list_inline': ', '.join(
                f"{t['name']} ({t['type_label']})" for t in with_summary
            ),
        }

        from jinja2 import Environment, FileSystemLoader, select_autoescape
        template_dir = os.path.join(os.path.dirname(__file__), '..', '..', 'templates', 'eval_v2')
        env = Environment(
            loader=FileSystemLoader(template_dir),
            autoescape=select_autoescape(['html', 'xml']),
        )
        html = env.get_template('campus_report_template.html').render(**context)
        pdf_bytes = html_to_pdf(html)

        session_tag = _safe_ascii(session_id)
        campus_tag = _safe_ascii(campus)
        filename = f'{session_tag}_{campus_tag}_{campus}_campus_report.pdf'

        log_audit('analysis_campus_pdf', session.get('admin_email', ''),
                  target=campus,
                  details={
                      'session_id': session_id,
                      'include_original': include_original,
                      'teacher_count': len(with_summary),
                  },
                  category='eval')

        return Response(
            pdf_bytes,
            mimetype='application/pdf',
            headers={'Content-Disposition': _content_disposition(filename)},
        )
    except Exception:
        logger.exception('api_analysis_campus_pdf error')
        return error('An internal error occurred.', 500)
