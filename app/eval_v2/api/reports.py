import logging
import re as _re
from flask import request, session as flask_session

logger = logging.getLogger(__name__)
from app.eval_v2.blueprints import eval_v2_api
from app.auth_utils import api_admin_required
from app.eval_v2.api.common import kst_now, get_questions, get_weights, EMP_ID_RE, SESSION_ID_RE
from app.eval_v2.questions import EVAL_TYPE_LABELS
from app.services.firebase_service import get_firestore_client
from app.services.roster_cache_service import get_roster
from app.services.report_service import build_report_context, render_report_html, html_to_pdf
from app.services.drive_service import get_or_create_eval_folder, save_folder_url_to_nt_info
from app.services.nt_cache_service import get_nt_record, get_cache_status, update_nt_record_field
from app.services.audit_service import log_audit
from app.utils.response import success, error
from app.utils.rate_limit import admin_rate_key
from app.extensions import limiter
from app.constants import COL_EVAL_V2_SESSIONS, COL_EVAL_V2_RESPONSES, COL_EVAL_V2_REPORTS


def _audit_safe(action: str, target: str, details: dict = None):
    """audit log 호출 래퍼 — 실패는 warning, 본 작업엔 영향 0."""
    try:
        log_audit(
            action,
            actor=flask_session.get('admin_email', ''),
            target=target,
            details=details or {},
            category='report',
        )
    except Exception:
        logger.debug('log_audit failed for %s', action, exc_info=True)


# ── 보고서 file_id 인덱스 헬퍼 — fast trash path ─────────────────────────────
def _save_report_index(db, emp_id: str, session_id: str, file_id: str, folder_id: str = ''):
    """eval_v2_reports/{emp_id}__{session_id} doc 에 file_id 인덱스 upsert.

    첫 생성 시 created_at 추가 저장 (snap.exists 분기 — read 1회 추가, 보고서 생성
    비용 대비 무시 가능). 갱신 시엔 updated_at 만. 실패 시 호출자가 try/except 로
    non-fatal 처리.
    """
    if not (emp_id and session_id and file_id):
        return
    doc_id = f'{emp_id}__{session_id}'
    ref = db.collection(COL_EVAL_V2_REPORTS).document(doc_id)
    now = kst_now()
    payload = {
        'emp_id': emp_id,
        'session_id': session_id,
        'file_id': file_id,
        'folder_id': folder_id or '',
        'updated_at': now,
    }
    snap = ref.get()
    if not snap.exists:
        payload['created_at'] = now
    ref.set(payload, merge=True)


def _lookup_report_file_id(db, emp_id: str, session_id: str) -> str | None:
    """저장된 file_id 조회. 없으면 None — 호출자가 fallback 진행."""
    if not (emp_id and session_id):
        return None
    doc_id = f'{emp_id}__{session_id}'
    snap = db.collection(COL_EVAL_V2_REPORTS).document(doc_id).get()
    if not snap.exists:
        return None
    return (snap.to_dict() or {}).get('file_id')


@eval_v2_api.route('/generate-report', methods=['POST'])
@api_admin_required
def api_generate_report():
    try:
        data = request.get_json(silent=True) or {}
        emp_id = str(data.get('empId', '')).strip().lower()
        session_id = str(data.get('sessionId', '')).strip()
        eval_type = str(data.get('evalType', '')).strip().lower()
        if not emp_id or not session_id or not eval_type:
            return error('empId, sessionId, evalType required.')
        if not EMP_ID_RE.match(emp_id):
            return error('Invalid empId format.', 400)
        if not SESSION_ID_RE.match(session_id):
            return error('Invalid sessionId format.', 400)

        db = get_firestore_client()

        # 1. 로스터에서 기본 정보 조회
        rows = get_roster()
        teacher_info = None
        for row in rows:
            if len(row) > 2 and str(row[2]).strip().lower() == emp_id:
                teacher_info = {
                    'name': row[1] if len(row) > 1 else '',
                    'campus': row[4] if len(row) > 4 else '',
                }
                break
        if not teacher_info:
            return error('Teacher not found.', 404)

        # 1-1. NT Info 캐시에서 풀네임/닉네임 조회
        nt_rec = get_nt_record(emp_id)
        full_name = nt_rec.get('name', '') or teacher_info['name']
        nickname = nt_rec.get('nickname', '') or ''

        # 2. 세션 정보
        sess_doc = db.collection(COL_EVAL_V2_SESSIONS).document(session_id).get()
        if not sess_doc.exists:
            return error('Session not found.', 404)
        sess_data = sess_doc.to_dict()
        session_label = sess_data.get('label', session_id)
        snap = sess_data.get('questions_snapshot', {})

        # 3. 문항 & 가중치 (스냅샷 우선)
        roles_raw = snap.get(eval_type, {}).get('questions', get_questions(eval_type))
        weights_raw = snap.get(eval_type, {}).get('weights', get_weights(eval_type))
        weights = {}
        for k, v in weights_raw.items():
            v = float(v)
            if v > 1:
                v /= 100
            weights[k] = round(v, 4)

        questions_map = {}
        open_questions_map = {}
        pill_class_map = {}
        min_count_map = {}
        for r in roles_raw:
            if isinstance(r, dict):
                name = r.get('name') or r.get('role', '')
                questions_map[name] = r.get('questions', r.get('items', []))
                open_questions_map[name] = r.get('open_questions', [])
                pill_class_map[name] = r.get('pill_class', '')
                min_count_map[name] = r.get('min_count', 1)

        # 4. 해당 교사 응답 — emp_id / submitted_at 도 포함 (effective filter 가 사용)
        docs = db.collection(COL_EVAL_V2_RESPONSES)\
            .where('emp_id', '==', emp_id)\
            .where('session_id', '==', session_id).stream()
        responses = []
        for doc in docs:
            d = doc.to_dict()
            responses.append({
                'doc_id': doc.id,
                'emp_id': d.get('emp_id', ''),
                'rater_name': d.get('rater_name', ''),
                'rater_role': d.get('rater_role', ''),
                'submitted_at': d.get('submitted_at', ''),
                'scores': d.get('scores', {}),
                'comment_ko': d.get('comment_ko', ''),
                'comment_en': d.get('comment_en', ''),
                'open_answers': d.get('open_answers', {}),
                'open_answers_ko': d.get('open_answers_ko', {}),
                'open_answers_en': d.get('open_answers_en', {}),
                'is_test': d.get('is_test', False),
                'is_manual': d.get('is_manual', False),
            })

        if not responses:
            return error('No responses found for this teacher.', 404)

        # 5. 전체 응답 (순위 계산용)
        all_docs = db.collection(COL_EVAL_V2_RESPONSES).where('session_id', '==', session_id).stream()
        all_responses = [d.to_dict() for d in all_docs]

        # 6. 컨텍스트 빌드
        import os
        template_dir = os.path.join(os.path.dirname(__file__), '..', '..', 'templates', 'eval_v2')
        context = build_report_context(
            emp_id=emp_id,
            eval_type=eval_type,
            eval_type_label=EVAL_TYPE_LABELS.get(eval_type, eval_type.upper()),
            teacher_name=full_name,
            nickname=nickname,
            campus=teacher_info['campus'],
            start_date='',
            session_label=session_label,
            responses=responses,
            questions_map=questions_map,
            open_questions_map=open_questions_map,
            pill_class_map=pill_class_map,
            min_count_map=min_count_map,
            weights=weights,
            all_responses=all_responses,
            session_id=session_id,
        )

        # 7. HTML → PDF
        html_content = render_report_html(context, template_dir)
        pdf_bytes = html_to_pdf(html_content)

        # 8–9. Drive 폴더 생성 + PDF 업로드 (통합 헬퍼)
        safe_name = _re.sub(r'[^a-zA-Z0-9가-힣\s_\-]', '', full_name).strip()
        filename = f"{emp_id.upper()}_{safe_name}_{session_label}_eval.pdf"
        from app.services.drive_service import upload_report_to_eval_folder
        upload_result = upload_report_to_eval_folder(
            emp_id.upper(), full_name, filename, pdf_bytes)

        # 10. NT Info BV열에 폴더 링크 저장
        save_folder_url_to_nt_info(emp_id, upload_result['folder_url'])

        # 11. file_id 인덱스 저장 (fast trash path) — 실패는 보고서 응답 자체엔 영향 0
        try:
            _save_report_index(
                db, emp_id, session_id,
                upload_result.get('file_id', ''),
                upload_result.get('folder_id', ''),
            )
        except Exception:
            logger.exception('_save_report_index failed (non-fatal)')

        _audit_safe('eval_v2_report_generated', target=f'{emp_id}__{session_id}',
                    details={'eval_type': eval_type, 'filename': filename,
                             'file_id': upload_result.get('file_id', '')})

        return success({
            'folderUrl': upload_result['folder_url'],
            'fileUrl': upload_result['file_url'],
            'filename': filename,
        })

    except Exception as e:
        import traceback
        logger.exception('api_generate_report error: %s: %s', type(e).__name__, e)
        logger.debug(traceback.format_exc())
        return error('Report generation failed. Please contact support.', 500)


@eval_v2_api.route('/bulk-generate-reports', methods=['POST'])
@api_admin_required
def api_bulk_generate_reports():
    try:
        data = request.get_json(silent=True) or {}
        emp_ids = [str(e).strip().lower() for e in data.get('empIds', [])]
        session_id = str(data.get('sessionId', '')).strip()
        if not emp_ids or not session_id:
            return error('empIds, sessionId required.')
        invalid = [e for e in emp_ids if not EMP_ID_RE.match(e)]
        if invalid:
            return error(f'Invalid empId format: {invalid[:3]}', 400)
        if not SESSION_ID_RE.match(session_id):
            return error('Invalid sessionId format.', 400)

        db = get_firestore_client()
        rows = get_roster()

        # 로스터 맵
        roster_map = {}
        for row in rows:
            if len(row) > 2:
                eid = str(row[2]).strip().lower()
                roster_map[eid] = {
                    'name': row[1] if len(row) > 1 else '',
                    'type': str(row[3]).strip().lower() if len(row) > 3 else '',
                    'campus': row[4] if len(row) > 4 else '',
                }

        # 세션 정보
        sess_doc = db.collection(COL_EVAL_V2_SESSIONS).document(session_id).get()
        if not sess_doc.exists:
            return error('Session not found.', 404)
        sess_data = sess_doc.to_dict()
        session_label = sess_data.get('label', session_id)
        snap = sess_data.get('questions_snapshot', {})

        # 문항/가중치는 교사별 타입에 맞게 루프 내에서 개별 처리

        # 전체 응답 (순위 계산용) — 한 번만 조회
        all_docs = db.collection(COL_EVAL_V2_RESPONSES).where('session_id', '==', session_id).stream()
        all_responses = [d.to_dict() for d in all_docs]

        import os
        template_dir = os.path.join(os.path.dirname(__file__), '..', '..', 'templates', 'eval_v2')

        # eval_type별 랭킹 맵 사전 계산 (루프 전 1회만)
        from app.services.report_service import calc_ranks_map
        ranks_cache = {}  # { eval_type: ranks_map }

        results = []
        for emp_id in emp_ids:
            try:
                teacher_info = roster_map.get(emp_id)
                if not teacher_info:
                    results.append({'empId': emp_id, 'status': 'ERROR', 'message': 'Teacher not found.'})
                    continue

                nt_rec = get_nt_record(emp_id)
                full_name = nt_rec.get('name', '') or teacher_info['name']
                nickname = nt_rec.get('nickname', '') or ''

                # 응답 조회 (테스트 응답 제외)
                docs = db.collection(COL_EVAL_V2_RESPONSES)\
                    .where('emp_id', '==', emp_id)\
                    .where('session_id', '==', session_id).stream()
                responses = []
                for doc in docs:
                    d = doc.to_dict()
                    if d.get('is_test', False):
                        continue
                    responses.append({
                        'doc_id': doc.id,
                        'emp_id': d.get('emp_id', ''),
                        'rater_name': d.get('rater_name', ''),
                        'rater_role': d.get('rater_role', ''),
                        'submitted_at': d.get('submitted_at', ''),
                        'scores': d.get('scores', {}),
                        'comment_ko': d.get('comment_ko', ''),
                        'comment_en': d.get('comment_en', ''),
                        'open_answers': d.get('open_answers', {}),
                        'open_answers_ko': d.get('open_answers_ko', {}),
                        'open_answers_en': d.get('open_answers_en', {}),
                        'is_test': d.get('is_test', False),
                        'is_manual': d.get('is_manual', False),
                    })

                if not responses:
                    results.append({'empId': emp_id, 'status': 'SKIP', 'message': 'No responses.'})
                    continue

                t_eval_type = teacher_info.get('type') or 'regular'
                roles_raw = snap.get(t_eval_type, {}).get('questions', get_questions(t_eval_type))
                weights_raw = snap.get(t_eval_type, {}).get('weights', get_weights(t_eval_type))
                t_weights = {}
                for k, v in weights_raw.items():
                    v = float(v)
                    if v > 1: v /= 100
                    t_weights[k] = round(v, 4)
                t_questions_map = {}
                t_open_questions_map = {}
                t_pill_class_map = {}
                t_min_count_map = {}
                for r in roles_raw:
                    if isinstance(r, dict):
                        name = r.get('name') or r.get('role', '')
                        t_questions_map[name] = r.get('questions', r.get('items', []))
                        t_open_questions_map[name] = r.get('open_questions', [])
                        t_pill_class_map[name] = r.get('pill_class', '')
                        t_min_count_map[name] = r.get('min_count', 1)

                # eval_type별 랭킹 맵 캐시 (최초 1회만 계산)
                if t_eval_type not in ranks_cache:
                    ranks_cache[t_eval_type] = calc_ranks_map(
                        eval_type=t_eval_type,
                        all_responses=all_responses,
                        weights=t_weights,
                        session_id=session_id,
                    )

                context = build_report_context(
                    emp_id=emp_id, eval_type=t_eval_type,
                    eval_type_label=EVAL_TYPE_LABELS.get(t_eval_type, t_eval_type.upper()),
                    teacher_name=full_name, nickname=nickname,
                    campus=teacher_info['campus'], start_date='',
                    session_label=session_label, responses=responses,
                    questions_map=t_questions_map, open_questions_map=t_open_questions_map,
                    pill_class_map=t_pill_class_map,
                    min_count_map=t_min_count_map,
                    weights=t_weights, all_responses=all_responses, session_id=session_id,
                    precomputed_ranks=ranks_cache[t_eval_type],
                )
                html_content = render_report_html(context, template_dir)
                pdf_bytes = html_to_pdf(html_content)

                from app.services.drive_service import upload_report_to_eval_folder
                safe_name = _re.sub(r'[^a-zA-Z0-9가-힣\s_\-]', '', full_name).strip()
                filename = f"{emp_id.upper()}_{safe_name}_{session_label}_eval.pdf"
                upload_result = upload_report_to_eval_folder(
                    emp_id.upper(), full_name, filename, pdf_bytes)
                save_folder_url_to_nt_info(emp_id, upload_result['folder_url'])
                update_nt_record_field(emp_id, 'eval_folder_url', upload_result['folder_url'])

                # file_id 인덱스 저장 (fast trash path) — 실패는 보고서 자체엔 영향 0
                try:
                    _save_report_index(
                        db, emp_id, session_id,
                        upload_result.get('file_id', ''),
                        upload_result.get('folder_id', ''),
                    )
                except Exception:
                    logger.exception('_save_report_index failed for %s (non-fatal)', emp_id)

                _audit_safe('eval_v2_report_generated', target=f'{emp_id}__{session_id}',
                            details={'eval_type': t_eval_type, 'filename': filename,
                                     'file_id': upload_result.get('file_id', ''),
                                     'source': 'bulk'})

                results.append({'empId': emp_id, 'status': 'SUCCESS', 'filename': filename})

            except Exception as e:
                logger.exception('bulk-generate error [%s]: %s', emp_id, e)
                results.append({'empId': emp_id, 'status': 'ERROR', 'message': 'Report generation failed.'})

        n_success = sum(1 for r in results if r['status'] == 'SUCCESS')
        n_skip = sum(1 for r in results if r['status'] == 'SKIP')
        n_error = sum(1 for r in results if r['status'] == 'ERROR')
        return success({
            'summary': {'success': n_success, 'skip': n_skip, 'error': n_error, 'total': len(emp_ids)},
            'results': results,
        })

    except Exception as e:
        import traceback
        logger.exception('api_bulk_generate_reports error: %s', e)
        logger.debug(traceback.format_exc())
        return error('An internal error occurred.', 500)


@eval_v2_api.route('/bulk-trash-reports', methods=['POST'])
@api_admin_required
@limiter.limit('30 per minute', key_func=admin_rate_key)
def api_bulk_trash_reports():
    """선택된 emp_ids 의 보고서를 chunk 단위로 trash. BulkRunner.run() 호출자용.

    Body: {sessionId, empIds: [emp_id, ...]} — 보통 chunkSize=10 (클라가 분할).
    Response: {results: [{empId, status: SUCCESS/SKIP/ERROR, fileId?, message?}, ...],
               summary: {success, skip, error, total}}.

    각 emp 마다 fast path (eval_v2_reports lookup) 우선 + fallback. trash_file 의
    404 가드로 이미 trash 된 파일은 SUCCESS 처리. audit log 는 emp 마다 (per-item).
    """
    try:
        data = request.get_json(silent=True) or {}
        session_id = str(data.get('sessionId', '')).strip()
        emp_ids = [str(e).strip().lower() for e in data.get('empIds', []) if str(e).strip()]
        if not session_id or not emp_ids:
            return error('sessionId and empIds required.')
        invalid = [e for e in emp_ids if not EMP_ID_RE.match(e)]
        if invalid:
            return error(f'Invalid empId format: {invalid[:3]}', 400)
        if not SESSION_ID_RE.match(session_id):
            return error('Invalid sessionId format.', 400)

        db = get_firestore_client()
        from app.services.drive_service import (
            trash_file, find_eval_folder, find_report_in_folder
        )

        # 세션 라벨 (fallback 시 필요) — 한 번만 read
        sess_doc = db.collection(COL_EVAL_V2_SESSIONS).document(session_id).get()
        if not sess_doc.exists:
            return error('Session not found.', 404)
        session_label = sess_doc.to_dict().get('label', session_id)

        results = []
        for emp_id in emp_ids:
            try:
                # Fast path
                stored = _lookup_report_file_id(db, emp_id, session_id)
                if stored:
                    trash_file(stored)
                    _audit_safe('eval_v2_report_trashed', target=f'{emp_id}__{session_id}',
                                details={'file_id': stored, 'path': 'fast', 'source': 'bulk'})
                    results.append({'empId': emp_id, 'status': 'SUCCESS', 'fileId': stored})
                    continue

                # Fallback — full_name + filename 재구성
                nt_rec = get_nt_record(emp_id)
                full_name = nt_rec.get('name', '')
                if not full_name:
                    for row in get_roster():
                        if len(row) > 2 and str(row[2]).strip().lower() == emp_id:
                            full_name = row[1]
                            break
                if not full_name:
                    results.append({'empId': emp_id, 'status': 'SKIP', 'message': 'Teacher not found.'})
                    continue

                folder_id = find_eval_folder(emp_id.upper(), full_name)
                if not folder_id:
                    results.append({'empId': emp_id, 'status': 'SKIP', 'message': 'Folder not found.'})
                    continue

                safe_name = _re.sub(r'[^a-zA-Z0-9가-힣\s_\-]', '', full_name).strip()
                filename = f"{emp_id.upper()}_{safe_name}_{session_label}_eval.pdf"
                file_id = find_report_in_folder(folder_id, filename)
                if not file_id:
                    results.append({'empId': emp_id, 'status': 'SKIP', 'message': 'Report not found.'})
                    continue

                # Backfill — 다음부터 fast path
                try:
                    _save_report_index(db, emp_id, session_id, file_id, folder_id)
                except Exception:
                    logger.exception('backfill _save_report_index failed for %s (non-fatal)', emp_id)

                trash_file(file_id)
                _audit_safe('eval_v2_report_trashed', target=f'{emp_id}__{session_id}',
                            details={'file_id': file_id, 'filename': filename,
                                     'path': 'fallback', 'source': 'bulk'})
                results.append({'empId': emp_id, 'status': 'SUCCESS', 'fileId': file_id})
            except Exception as e:
                logger.exception('bulk-trash error [%s]: %s', emp_id, e)
                results.append({'empId': emp_id, 'status': 'ERROR', 'message': 'Trash failed.'})

        n_success = sum(1 for r in results if r['status'] == 'SUCCESS')
        n_skip    = sum(1 for r in results if r['status'] == 'SKIP')
        n_error   = sum(1 for r in results if r['status'] == 'ERROR')
        return success({
            'summary': {'success': n_success, 'skip': n_skip, 'error': n_error, 'total': len(emp_ids)},
            'results': results,
        })
    except Exception:
        logger.exception('api_bulk_trash_reports error')
        return error('An internal error occurred.', 500)


@eval_v2_api.route('/trash-report', methods=['POST'])
@api_admin_required
def api_trash_report():
    """특정 직원의 특정 세션 보고서를 Drive 휴지통으로 이동.

    Fast path: eval_v2_reports/{emp_id}__{session_id} 의 stored file_id 사용 (1 Drive call).
    Fallback: stored 없으면 구 로직 (find_eval_folder + find_report_in_folder) → 발견 시
    backfill (실패해도 trash 는 진행 — 인덱스 저장 실패는 trash 자체의 실패가 아님).
    응답 schema: {trashedFileId, trashedFile} — fast path 는 trashedFile 빈 문자열.
    """
    try:
        _body      = request.get_json(silent=True) or {}
        emp_id     = str(_body.get('empId', '')).strip().lower()
        session_id = str(_body.get('sessionId', '')).strip()
        if not emp_id or not session_id:
            return error('empId, sessionId required.')
        if not EMP_ID_RE.match(emp_id):
            return error('Invalid empId format.', 400)
        if not SESSION_ID_RE.match(session_id):
            return error('Invalid sessionId format.', 400)

        db = get_firestore_client()
        from app.services.drive_service import trash_file

        # Fast path — stored file_id (3 Drive call → 1)
        stored_file_id = _lookup_report_file_id(db, emp_id, session_id)
        if stored_file_id:
            trash_file(stored_file_id)
            _audit_safe('eval_v2_report_trashed', target=f'{emp_id}__{session_id}',
                        details={'file_id': stored_file_id, 'path': 'fast'})
            return success({'trashedFileId': stored_file_id, 'trashedFile': ''})

        # Fallback — 구 로직: session label / full_name / filename 재구성
        sess_doc = db.collection(COL_EVAL_V2_SESSIONS).document(session_id).get()
        if not sess_doc.exists:
            return error('Session not found.', 404)
        session_label = sess_doc.to_dict().get('label', session_id)

        nt_rec    = get_nt_record(emp_id)
        full_name = nt_rec.get('name', '')
        if not full_name:
            for row in get_roster():
                if len(row) > 2 and str(row[2]).strip().lower() == emp_id:
                    full_name = row[1]
                    break
        if not full_name:
            return error('Teacher not found.', 404)

        from app.services.drive_service import find_eval_folder, find_report_in_folder
        folder_id = find_eval_folder(emp_id.upper(), full_name)
        if not folder_id:
            return error('Drive folder not found.', 404)

        safe_name = _re.sub(r'[^a-zA-Z0-9가-힣\s_\-]', '', full_name).strip()
        filename = f"{emp_id.upper()}_{safe_name}_{session_label}_eval.pdf"
        file_id  = find_report_in_folder(folder_id, filename)
        if not file_id:
            return error('Report file not found.', 404)

        # Backfill — 다음 trash 부터 fast path
        try:
            _save_report_index(db, emp_id, session_id, file_id, folder_id)
        except Exception:
            logger.exception('backfill _save_report_index failed (non-fatal)')

        trash_file(file_id)
        _audit_safe('eval_v2_report_trashed', target=f'{emp_id}__{session_id}',
                    details={'file_id': file_id, 'filename': filename, 'path': 'fallback'})
        return success({'trashedFileId': file_id, 'trashedFile': filename})
    except Exception as e:
        logger.exception('api_trash_report error: %s', e)
        return error('An internal error occurred.', 500)


@eval_v2_api.route('/list-session-reports', methods=['POST'])
@limiter.limit("60 per minute", key_func=admin_rate_key)
@api_admin_required
def api_list_session_reports():
    """세션의 모든 보고서 PDF 목록을 반환 (read-only). 진행률 모달용."""
    try:
        session_id = str((request.get_json(silent=True) or {}).get('sessionId', '')).strip()
        if not session_id:
            return error('sessionId required.')

        db = get_firestore_client()
        sess_doc = db.collection(COL_EVAL_V2_SESSIONS).document(session_id).get()
        if not sess_doc.exists:
            return error('Session not found.', 404)
        session_label = sess_doc.to_dict().get('label', session_id)

        from app.services.drive_service import find_session_reports
        files = find_session_reports(session_label)
        return success({'files': [{'id': f['id'], 'name': f['name']} for f in files]})
    except Exception as e:
        logger.exception('api_list_session_reports error: %s', e)
        return error('An internal error occurred.', 500)


@eval_v2_api.route('/trash-file-by-id', methods=['POST'])
@limiter.limit("120 per minute", key_func=admin_rate_key)
@api_admin_required
def api_trash_file_by_id():
    """file_id 만 받아 단건 trash. sequential progress 용."""
    try:
        file_id = str((request.get_json(silent=True) or {}).get('fileId', '')).strip()
        if not file_id:
            return error('fileId required.')
        from app.services.drive_service import trash_file
        trash_file(file_id)
        return success({})
    except Exception as e:
        logger.exception('api_trash_file_by_id error: %s', e)
        return error('An internal error occurred.', 500)


@eval_v2_api.route('/get-drive-folder', methods=['POST'])
@api_admin_required
def api_get_drive_folder():
    try:
        emp_id = str((request.get_json(silent=True) or {}).get('empId', '')).strip().lower()
        if not emp_id:
            return error('empId required.')
        from app.services.nt_cache_service import get_nt_record
        nt_rec = get_nt_record(emp_id)
        # 1. 캐시에 폴더 URL이 있으면 즉시 반환 (Drive API 호출 없음)
        folder_url = nt_rec.get('eval_folder_url', '')
        if folder_url:
            return success({'folderUrl': folder_url})
        # 2. 캐시에 없으면 Drive API로 폴더 조회/생성
        from app.services.drive_service import get_or_create_eval_folder
        rows = get_roster()
        full_name = ''
        for row in rows:
            if len(row) > 2 and str(row[2]).strip().lower() == emp_id:
                full_name = row[1]
                break
        if not full_name:
            return error('Teacher not found.', 404)
        full_name = nt_rec.get('name', '') or full_name
        folder_info = get_or_create_eval_folder(emp_id.upper(), full_name)
        # 폴더 URL을 항상 캐시에 저장 (신규/기존 무관) — 다음 요청은 Drive API 호출 없이 즉시 반환
        update_nt_record_field(emp_id, 'eval_folder_url', folder_info['folder_url'])
        if folder_info['created']:
            save_folder_url_to_nt_info(emp_id, folder_info['folder_url'])
        return success({'folderUrl': folder_info['folder_url']})
    except Exception:
        return error('An internal error occurred.', 500)


@eval_v2_api.route('/roster-cache/refresh', methods=['POST'])
@api_admin_required
def api_roster_cache_refresh():
    try:
        from app.services.roster_cache_service import refresh_cache
        result = refresh_cache()
        return success(result)
    except Exception:
        return error('An internal error occurred.', 500)


@eval_v2_api.route('/nt-cache/status', methods=['GET'])
@api_admin_required
def api_nt_cache_status():
    return success(get_cache_status())


@eval_v2_api.route('/nt-cache/refresh', methods=['POST'])
@api_admin_required
def api_nt_cache_refresh():
    try:
        from app.services.nt_cache_service import refresh_cache as refresh_nt_cache
        result = refresh_nt_cache()
        return success(result)
    except Exception:
        return error('An internal error occurred.', 500)
