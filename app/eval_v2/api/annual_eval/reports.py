"""
app/eval_v2/api/annual_eval/reports.py
Annual Eval Reports API — 보고서 생성 및 번역
"""
import logging
import os
import re as _re
from flask import request
from app.eval_v2.blueprints import eval_v2_api
from app.auth_utils import api_admin_required
from app.eval_v2.api.common import kst_now
from app.constants import (
    COL_EVAL_V2_SESSIONS,
    COL_NHR_ANNUAL_EVAL, COL_NHR_ANNUAL_EVAL_CONFIG,
)
from app.utils.response import success, error
from app.services.firebase_service import get_firestore_client
from app.services.asset_service import get_static_data_uri
from app.extensions import cache
from ._helpers import require_xhr, _admin_email, _EMP_ID_RE, _DATE_RE
from .scoring import _calc_contributions, _calc_composite

logger = logging.getLogger(__name__)


def _calc_tenure(start_date_str: str, today) -> str:
    """입사일부터 today까지의 근무 기간을 '년/개월' 형태로 반환."""
    from datetime import date as _date
    if not start_date_str:
        return '—'
    try:
        s = str(start_date_str).strip()[:10].replace('.', '-').replace('/', '-')
        start = _date.fromisoformat(s)
    except (ValueError, TypeError):
        return '—'
    months = (today.year - start.year) * 12 + (today.month - start.month)
    if today.day < start.day:
        months -= 1
    if months < 0:
        return '—'
    years, rem = divmod(months, 12)
    if years > 0 and rem > 0:
        return f'{years}년 {rem}개월'
    if years > 0:
        return f'{years}년'
    return f'{rem}개월' if rem > 0 else '1개월 미만'


def _score_to_rank(score) -> str:
    """하드코딩 폴백 (raise_policy 없을 때만 사용)."""
    if score is None:
        return '—'
    try:
        s = float(score)
    except (ValueError, TypeError):
        return '—'
    if s >= 90: return 'S'
    if s >= 80: return 'A'
    if s >= 70: return 'B'
    if s >= 60: return 'C'
    return 'D'


def _match_policy_rank(base_current, composite_score, raise_policy) -> str:
    """raise_policy 구조에서 base/score 구간 매칭 → tier.note 반환.
    admin_annual_eval.js 의 _aeUpdateGradeBadge 와 동일 로직으로 UI/리포트 일관성 보장.
    매칭 실패 또는 policy 없음 시 _score_to_rank 폴백.
    """
    try:
        base = int(base_current or 0)
        composite = float(composite_score) if composite_score is not None else None
    except (ValueError, TypeError):
        return _score_to_rank(composite_score)
    if composite is None or not base or not raise_policy:
        return _score_to_rank(composite_score)
    for group in raise_policy:
        g_min = int(group.get('base_min') or 0)
        g_max = int(group.get('base_max') or 0)   # 0 = 상한 없음
        if base < g_min: continue
        if g_max > 0 and base > g_max: continue
        for tier in (group.get('tiers') or []):
            try:
                s_min = float(tier.get('score_min', 0))
                s_max = float(tier.get('score_max', 100))
            except (ValueError, TypeError):
                continue
            if composite < s_min or composite > s_max:
                continue
            note = str(tier.get('note') or '').strip()
            if note:
                return note
    return _score_to_rank(composite_score)


@eval_v2_api.route('/annual-eval/generate-report', methods=['POST'])
@api_admin_required
@require_xhr
def api_annual_eval_generate_report():
    """
    {emp_id, eval_deadline} → 보고서 HTML 렌더링 → PDF → Drive 업로드 → URL 반환.
    """
    try:
        data          = request.get_json(silent=True) or {}
        emp_id        = str(data.get('emp_id', '')).strip()
        eval_deadline = str(data.get('eval_deadline', '')).strip()
        if not emp_id or not eval_deadline:
            return error('emp_id and eval_deadline are required.', 400)
        if not _EMP_ID_RE.match(emp_id):
            return error('Invalid emp_id format.', 400)
        if not _DATE_RE.match(eval_deadline):
            return error('eval_deadline must be YYYY-MM-DD format.', 400)

        db     = get_firestore_client()
        doc_id = f'{emp_id}__{eval_deadline}'
        doc    = db.collection(COL_NHR_ANNUAL_EVAL).document(doc_id).get()
        if not doc.exists:
            logger.warning('api_annual_eval_generate_report: record not found [%s]', doc_id)
            return error('No annual eval record found.', 404)

        record = doc.to_dict()

        # teacher_name 해결 — 여러 emp_id variant 로 Firestore 직접 조회 (대소문자 이슈 회피)
        from .salary import _get_nt_salary
        from app.services.nt_cache_service import get_nt_record
        salary_info = _get_nt_salary(emp_id)
        nt_rec = get_nt_record(emp_id) or {}
        teacher_name = (salary_info.get('nt_name', '')
                        or nt_rec.get('name', '')
                        or record.get('nt_name', '')
                        or '')

        cfg_doc = db.collection(COL_NHR_ANNUAL_EVAL_CONFIG).document('settings').get()
        config  = cfg_doc.to_dict() if cfg_doc.exists else {}
        weights = config.get('score_weights', {'reg_eval': 50, 'obs_eval': 30, 'net_eval': 20})

        _session_label_cache: dict[str, str] = {}
        def _get_session_label(sid: str) -> str:
            if sid == '__manual__':
                return '직접 입력'
            if not sid:
                return ''
            if sid in _session_label_cache:
                return _session_label_cache[sid]
            try:
                s = db.collection(COL_EVAL_V2_SESSIONS).document(sid).get()
                label = s.to_dict().get('label', sid) if s.exists else sid
            except Exception:
                label = sid
            _session_label_cache[sid] = label
            return label

        eval_sequence = record.get('eval_sequence', '')
        from app.utils.time_utils import kst_date
        from datetime import datetime, timezone, timedelta
        KST = timezone(timedelta(hours=9))
        now_kst      = datetime.now(KST)
        issue_date   = now_kst.strftime('%Y-%m-%d')
        issue_time   = now_kst.strftime('%H:%M')
        issue_datetime = f'{issue_date} {issue_time}'

        # 개인 정보 — salary_info → record → nt_cache 3단계 fallback
        info_campus      = (salary_info.get('nt_campus') or record.get('nt_campus') or record.get('campus') or nt_rec.get('campus') or '')
        info_nationality = (salary_info.get('nt_nationality') or record.get('nt_nationality') or nt_rec.get('nationality') or '')
        info_start_date  = (salary_info.get('nt_start_date') or record.get('nt_start_date') or nt_rec.get('start_date') or '')
        info_salary_day  = (salary_info.get('salary_day') or nt_rec.get('salary_day') or record.get('salary_day') or '')

        tenure_text = _calc_tenure(info_start_date, kst_date())
        # 수당 종류 — NT Info U열(allowance_name). salary_info (batch get_all) → nt_cache 순 폴백
        allowance_name_raw = (
            str(salary_info.get('allowance_name') or '').strip()
            or str(nt_rec.get('allowance_name') or '').strip()
        )
        if allowance_name_raw:
            current_allowances = [
                s.strip() for s in _re.split(r'[,\n/、·]+', allowance_name_raw) if s.strip()
            ]
        else:
            # 폴백 — current > 0 인 항목 자동 추론
            current_allowances = []
            if (record.get('base_current')    or 0) > 0: current_allowances.append('기본급')
            if (record.get('pos_current')     or 0) > 0: current_allowances.append('직책 수당')
            if (record.get('role_current')    or 0) > 0: current_allowances.append('역할 수당')
            if (record.get('housing_current') or 0) > 0: current_allowances.append('주거 수당')

        # composite_score 폴백 — record에 저장된 값 없으면 개별 점수로 즉석 계산
        composite_score_val = record.get('composite_score')
        if composite_score_val is None:
            composite_score_val = _calc_composite(record, weights)
        # 랭크는 admin 팝업과 동일하게 raise_policy tier.note 우선 매칭
        raise_policy   = config.get('raise_policy') or []
        composite_rank = _match_policy_rank(record.get('base_current'), composite_score_val, raise_policy)

        ctx = {
            'emp_id':           emp_id.upper(),
            'eval_deadline':    eval_deadline,
            'eval_sequence':    eval_sequence,
            'issue_date':       issue_date,
            'issue_time':       issue_time,
            'issue_datetime':   issue_datetime,
            'teacher_name':     teacher_name,
            'logo_data_uri':    get_static_data_uri('logo.png'),
            'tenure_text':      tenure_text,
            'current_allowances': current_allowances,
            'nt_nationality':   info_nationality,
            'nt_start_date':    info_start_date,
            'salary_day':       info_salary_day,
            'composite_rank':   composite_rank,
            'campus':           info_campus,
            'eval_type':        record.get('eval_type', ''),
            'status':           record.get('status', ''),
            'session_1_label':  _get_session_label(record.get('session_1_id', '')),
            'session_2_label':  _get_session_label(record.get('session_2_id', '')),
            'session_1_id':     record.get('session_1_id', ''),
            'session_2_id':     record.get('session_2_id', ''),
            'reg_score_1':      record.get('reg_score_1'),
            'reg_score_2':      record.get('reg_score_2'),
            'reg_final_score':  record.get('reg_final_score'),
            'obs_score':        record.get('obs_score'),
            'obs_date':         record.get('obs_date', ''),
            'obs_rater':        record.get('obs_rater', ''),
            'obs_link':         record.get('obs_link', ''),
            'obs_eng':          record.get('obs_eng', ''),
            'net_score':        record.get('net_score'),
            'net_date':         record.get('net_date', ''),
            'net_rater':        record.get('net_rater', ''),
            'net_link':         record.get('net_link', ''),
            'net_eng':          record.get('net_eng', ''),
            'composite_score':  composite_score_val,
            'other_eng':        record.get('other_eng', ''),
            'obs_eng_ko':       record.get('obs_eng_ko', ''),
            'net_eng_ko':       record.get('net_eng_ko', ''),
            'other_eng_ko':     record.get('other_eng_ko', ''),
            'allowance_comment': record.get('allowance_comment', ''),
            'base_current':     record.get('base_current', 0),
            'pos_current':      record.get('pos_current', 0),
            'role_current':     record.get('role_current', 0),
            'housing_current':  record.get('housing_current', 0),
            'total_current':    record.get('total_current', 0),
            'base_inc':         record.get('base_inc', 0),
            'pos_inc':          record.get('pos_inc', 0),
            'role_inc':         record.get('role_inc', 0),
            'housing_inc':      record.get('housing_inc', 0),
            'applied_total':    record.get('applied_total', 0),
            'weight_reg':       weights.get('reg_eval', 50),
            'weight_obs':       weights.get('obs_eval', 30),
            'weight_net':       weights.get('net_eval', 20),
            **_calc_contributions(record, weights),
        }

        from app.services.report_service import html_to_pdf
        from app.services.drive_service import upload_report_to_eval_folder
        from jinja2 import Environment, FileSystemLoader

        template_dir = os.path.join(os.path.dirname(__file__), '..', '..', '..', 'templates', 'eval_v2')
        env = Environment(loader=FileSystemLoader(template_dir), autoescape=True)
        template = env.get_template('annual_eval_report.html')
        html_content = template.render(**ctx)
        pdf_bytes = html_to_pdf(html_content)

        safe_name    = _re.sub(r'[^a-zA-Z0-9가-힣\s_\-]', '', teacher_name).strip()
        name_part    = f'_{safe_name}' if safe_name and safe_name.upper() != emp_id.upper() else ''
        seq_label    = f'_{eval_sequence}th' if eval_sequence else ''
        filename     = f"{emp_id.upper()}{name_part}{seq_label}_annual_eval.pdf"

        upload_result = upload_report_to_eval_folder(
            emp_id.upper(), teacher_name, filename, pdf_bytes)
        report_url = upload_result['file_url']
        folder_url = upload_result['folder_url']
        generated_at = kst_now()
        generated_by = _admin_email()
        doc_ref = db.collection(COL_NHR_ANNUAL_EVAL).document(doc_id)
        doc_ref.update({
            'report_url':        report_url,
            'folder_url':        folder_url,
            'report_updated_at': generated_at,
            'report_updated_by': generated_by,
        })

        doc_ref.collection('audit_log').add({
            'event':      'report_generated',
            'timestamp':  generated_at,
            'by':         generated_by,
            'filename':   filename,
            'url':        report_url,
        })

        # 전역 audit_logs 기록 — 누가·언제 연간 평가지를 출력했는지 중앙 집중 로그.
        # 실패 시 Firestore 문서에 audit_failed 플래그를 남겨 감사 체계 우회를 탐지 가능하게 함.
        try:
            from app.services.audit_service import log_audit
            log_audit(
                action='annual_eval_report_generated',
                actor=generated_by,
                target=emp_id.upper(),
                details={
                    'eval_deadline': eval_deadline,
                    'eval_sequence': eval_sequence,
                    'filename':      filename,
                    'report_url':    report_url,
                },
                category='eval',
            )
        except Exception:
            logger.exception('annual_eval_report audit_log failed')
            try:
                doc_ref.update({
                    'audit_failed':    True,
                    'audit_failed_at': generated_at,
                })
            except Exception:
                logger.exception('annual_eval_report audit_failed flag write failed')

        cache.delete('ae_list_base_data')
        return success({'url': report_url, 'folder_url': folder_url, 'filename': filename})
    except Exception:
        logger.exception('api_annual_eval_generate_report error')
        return error('An internal error occurred.', 500)


@eval_v2_api.route('/annual-eval/translate', methods=['POST'])
@api_admin_required
@require_xhr
def api_annual_eval_translate():
    """
    영문 코멘트를 한국어로 번역 (OpenAI GPT).
    요청: {text: str}
    반환: {status: 'SUCCESS', translation: str}
    """
    try:
        data = request.get_json(silent=True) or {}
        text = str(data.get('text', '')).strip()
        if not text:
            return error('No text provided.', 400)

        from app.services.openai_service import translate_evaluation
        result = translate_evaluation(text)
        return success({'translation': result})
    except Exception:
        logger.exception('api_annual_eval_translate error')
        return error('Translation failed.', 500)
