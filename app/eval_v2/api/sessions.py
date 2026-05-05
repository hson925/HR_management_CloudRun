import logging
from flask import request, session

logger = logging.getLogger(__name__)
from app.eval_v2.blueprints import eval_v2_api
from app.auth_utils import api_admin_required
from app.eval_v2.api.common import kst_now, _batch_delete, get_questions, get_weights
from app.eval_v2.api.passcode_gate import (
    hash_passcode, validate_passcode_format,
)
from app.services.firebase_service import get_firestore_client
from app.services.audit_service import log_audit
from app.extensions import cache, limiter
from app.utils.rate_limit import admin_rate_key
from app.services.cache_service import invalidate_sessions
from app.utils.response import success, error
from app.constants import COL_EVAL_V2_SESSIONS, COL_EVAL_V2_RESPONSES


def _fetch_sessions_data():
    """세션 목록 + 응답 건수 집계. 30초 캐시."""
    db = get_firestore_client()
    # 응답 건수: session_id 필드만 읽어 집계 (전체 문서 대신 select 사용)
    all_responses = db.collection(COL_EVAL_V2_RESPONSES).select(['session_id']).limit(50000).stream()
    session_counts = {}
    for r in all_responses:
        sid = r.to_dict().get('session_id', '')
        if sid:
            session_counts[sid] = session_counts.get(sid, 0) + 1
    sessions = []
    for doc in db.collection(COL_EVAL_V2_SESSIONS).limit(500).stream():
        d = doc.to_dict()
        sessions.append({
            'id': doc.id,
            'label': d.get('label', ''),
            'status': d.get('status', ''),
            'start_date': d.get('start_date', ''),
            'end_date': d.get('end_date', ''),
            'created_at': d.get('created_at', ''),
            'closed_at': d.get('closed_at', ''),
            'response_count': session_counts.get(doc.id, 0),
            'questions_snapshot': d.get('questions_snapshot', {}),
            'notification_schedule': d.get('notification_schedule', {}),
            # passcode_hash 자체는 절대 노출하지 않고, 존재 여부(bool) + version 만.
            'passcode_enabled': bool(d.get('passcode_hash')),
            'passcode_version': int(d.get('passcode_version', 0) or 0),
            'passcode_updated_at': d.get('passcode_updated_at', ''),
            'passcode_updated_by': d.get('passcode_updated_by', ''),
        })
    sessions.sort(key=lambda x: x.get('created_at', ''), reverse=True)
    return sessions


@eval_v2_api.route('/get-sessions', methods=['POST'])
@limiter.limit("30 per minute")
def api_get_sessions():
    try:
        cache_key = 'eval_v2_sessions_list'
        sessions = cache.get(cache_key)
        if sessions is None:
            sessions = _fetch_sessions_data()
            cache.set(cache_key, sessions, timeout=30)
        # 비인증 사용자에게는 민감 정보 제거. passcode_enabled 의 누설은 UX 편의
        # (드롭다운 자물쇠 아이콘) 차원이라 안전하지만, version/updated_* 는 숨김.
        # 로그인된 내부 직원은 bypass 가 작동하므로 passcode_required=false 로 일관.
        from app.eval_v2.api.passcode_gate import is_internal_bypass as _bypass_fn
        _bypass_now = _bypass_fn()
        is_admin = bool(session.get('admin_auth'))
        if not is_admin:
            sessions = [{
                'id': s['id'],
                'label': s['label'],
                'status': s['status'],
                'start_date': s['start_date'],
                'end_date': s['end_date'],
                # passcode_enabled: 세션에 passcode 가 설정돼 있는지 여부 (정보 표시용,
                # 자물쇠 아이콘). bypass 와 무관하게 항상 노출 — 내부 직원도 외부
                # 평가자 입장에서 passcode 가 요구되는 세션인지 시각적으로 판단 가능.
                'passcode_enabled': bool(s.get('passcode_enabled')),
                # passcode_required: bypass 까지 고려한 modal 트리거 플래그.
                'passcode_required': bool(s.get('passcode_enabled')) and not _bypass_now,
            } for s in sessions]
        return success({'sessions': sessions})
    except Exception:
        return error('An internal error occurred.')


@eval_v2_api.route('/create-session', methods=['POST'])
@api_admin_required
def api_create_session():
    try:
        data       = request.get_json(silent=True) or {}
        label      = str(data.get('label', '')).strip()
        start_date = str(data.get('startDate', '')).strip()
        end_date   = str(data.get('endDate', '')).strip()
        raw_passcode = data.get('passcode', None)
        if not label or not start_date or not end_date:
            return error('Please fill in all fields: name, start date, end date.')
        # label 검증: Firestore doc ID 안전 문자 + 길이 제한
        import re as _re
        if not _re.match(r'^[\w\-가-힣 .]{1,80}$', label) or label.strip('.' ) == '':
            return error('Session name contains invalid characters or is too long (max 80).')

        # passcode 선택적 — None 또는 빈 문자열이면 public 세션, 문자열이면 해싱 저장.
        passcode_hash = ''
        if raw_passcode is not None and str(raw_passcode).strip() != '':
            ok, msg_or_norm = validate_passcode_format(raw_passcode)
            if not ok:
                return error(msg_or_norm)
            passcode_hash = hash_passcode(msg_or_norm)

        db = get_firestore_client()
        # 현재 문항/가중치 스냅샷 저장
        from app.eval_v2.questions import DEFAULT_QUESTIONS
        snapshot = {}
        for etype in DEFAULT_QUESTIONS.keys():
            snapshot[etype] = {
                'questions': get_questions(etype),
                'weights': get_weights(etype),
            }
        # session_id = 입력한 이름 그대로 사용 (불변)
        if '/' in label:
            return error('Session name cannot contain "/".')
        session_id = label
        sess_ref = db.collection(COL_EVAL_V2_SESSIONS).document(session_id)
        # 동일 이름 동시 생성 방지: check+set을 트랜잭션으로 원자화
        from google.cloud import firestore as _fs

        class _SessionExists(Exception):
            pass

        @_fs.transactional
        def _create_session_txn(tx):
            snap = sess_ref.get(transaction=tx)
            if snap.exists:
                raise _SessionExists()
            doc = {
                'label': label,
                'status': 'active',
                'start_date': start_date,
                'end_date': end_date,
                'questions_snapshot': snapshot,
                'created_at': kst_now(),
                'created_by': session.get('admin_email', ''),
                'closed_at': '',
            }
            if passcode_hash:
                doc['passcode_hash'] = passcode_hash
                doc['passcode_version'] = 1
                doc['passcode_updated_at'] = kst_now()
                doc['passcode_updated_by'] = session.get('admin_email', '')
            tx.set(sess_ref, doc)

        try:
            _create_session_txn(db.transaction())
        except _SessionExists:
            return error(f'Session name "{session_id}" is already in use. Please choose a different name.')
        invalidate_sessions()
        if passcode_hash:
            try:
                log_audit('eval_v2_passcode_set',
                          actor=session.get('admin_email', ''),
                          target=session_id,
                          details={'source': 'create_session'},
                          category='session')
            except Exception:
                logger.debug('log_audit failed for eval_v2_passcode_set', exc_info=True)
        return success({'sessionId': session_id, 'passcodeEnabled': bool(passcode_hash)})
    except Exception:
        logger.exception('api_create_session error')
        return error('An internal error occurred.')


@eval_v2_api.route('/close-session', methods=['POST'])
@api_admin_required
def api_close_session():
    try:
        _body = request.get_json(silent=True) or {}
        session_id = str(_body.get('sessionId', '')).strip()
        if not session_id:
            return error('sessionId is required.')
        db = get_firestore_client()
        db.collection(COL_EVAL_V2_SESSIONS).document(session_id).update({
            'status': 'closed',
            'closed_at': kst_now(),
            'closed_by': session.get('admin_email', ''),
        })
        invalidate_sessions()
        return success()
    except Exception:
        return error('An internal error occurred.')


@eval_v2_api.route('/reopen-session', methods=['POST'])
@api_admin_required
def api_reopen_session():
    try:
        _body = request.get_json(silent=True) or {}
        session_id = str(_body.get('sessionId', '')).strip()
        if not session_id:
            return error('sessionId is required.')
        db = get_firestore_client()
        db.collection(COL_EVAL_V2_SESSIONS).document(session_id).update({
            'status': 'active',
            'closed_at': '',
            'reopened_at': kst_now(),
            'reopened_by': session.get('admin_email', ''),
        })
        invalidate_sessions()
        return success()
    except Exception:
        return error('An internal error occurred.')


@eval_v2_api.route('/delete-session', methods=['POST'])
@api_admin_required
def api_delete_session():
    try:
        _body = request.get_json(silent=True) or {}
        session_id = str(_body.get('sessionId', '')).strip()
        delete_responses = bool(_body.get('deleteResponses', False))
        if not session_id:
            return error('sessionId is required.')
        db = get_firestore_client()
        deleted_count = 0
        if delete_responses:
            docs = list(db.collection(COL_EVAL_V2_RESPONSES).where('session_id', '==', session_id).stream())
            deleted_count = _batch_delete(db, docs)
        db.collection(COL_EVAL_V2_SESSIONS).document(session_id).delete()
        invalidate_sessions()
        log_audit('eval_session_delete', session.get('admin_email', ''), target=session_id, details={'deleted_responses': deleted_count}, category='session')
        return success({'deletedResponses': deleted_count})
    except Exception:
        logger.exception('api_delete_session error')
        return error('An internal error occurred.')


@eval_v2_api.route('/session/passcode', methods=['POST'])
@api_admin_required
@limiter.limit('10 per minute', key_func=admin_rate_key)
def api_session_passcode():
    """Manage a session's passcode. Admin-only.

    Request: {sessionId, action: 'set'|'regenerate'|'remove', passcode?: str}
    Response: success({passcodeEnabled, passcodeVersion}) | error(msg)

    - set/regenerate 둘 다 평문 passcode 를 받는다 (서버가 생성하지 않음 — 클라이언트
      가 Auto-generate 버튼을 눌러 평문을 만들고 함께 전송). 평문은 절대 저장하지
      않고 응답에도 에코하지 않는다. 평문을 가진 쪽은 요청을 보낸 관리자 브라우저뿐.
    - regenerate 는 passcode_version 을 +1 → 기존 Flask 세션 토큰 자동 무효화.
    - remove 는 passcode_hash / version / updated_* 필드를 일괄 삭제.
    """
    from google.cloud import firestore as _fs
    try:
        data = request.get_json(silent=True) or {}
        session_id = str(data.get('sessionId', '')).strip()
        action = str(data.get('action', '')).strip().lower()
        raw_passcode = data.get('passcode', None)
        if not session_id:
            return error('sessionId is required.')
        if action not in ('set', 'regenerate', 'remove'):
            return error('Invalid action.')

        db = get_firestore_client()
        sess_ref = db.collection(COL_EVAL_V2_SESSIONS).document(session_id)

        if action == 'remove':
            @_fs.transactional
            def _remove_txn(tx):
                snap = sess_ref.get(transaction=tx)
                if not snap.exists:
                    raise ValueError('not_found')
                tx.update(sess_ref, {
                    'passcode_hash': _fs.DELETE_FIELD,
                    'passcode_version': _fs.DELETE_FIELD,
                    'passcode_updated_at': kst_now(),
                    'passcode_updated_by': session.get('admin_email', ''),
                })
            try:
                _remove_txn(db.transaction())
            except ValueError:
                return error('Session not found.', 404)
            invalidate_sessions()
            log_audit('eval_v2_passcode_removed',
                      actor=session.get('admin_email', ''),
                      target=session_id,
                      category='session')
            return success({'passcodeEnabled': False, 'passcodeVersion': 0})

        # set / regenerate 공통: passcode 필수
        ok, msg_or_norm = validate_passcode_format(raw_passcode)
        if not ok:
            return error(msg_or_norm)
        new_hash = hash_passcode(msg_or_norm)

        class _Missing(Exception):
            pass

        @_fs.transactional
        def _set_txn(tx):
            snap = sess_ref.get(transaction=tx)
            if not snap.exists:
                raise _Missing()
            existing = snap.to_dict() or {}
            current_version = int(existing.get('passcode_version', 0) or 0)
            # set: 기존 passcode 없을 때만 허용 — 실수로 regenerate 를 set 으로 보내
            #      기존 코드가 덮여 평문 분실되는 상황 방지.
            if action == 'set' and existing.get('passcode_hash'):
                raise ValueError('already_set')
            # regenerate: 기존 passcode 가 있을 때만 허용 — "재발급" 의미상 자명하고,
            #             set 과 혼용되어 의도치 않게 version=1 로 리셋되는 것을 방지.
            if action == 'regenerate' and not existing.get('passcode_hash'):
                raise ValueError('not_set')
            new_version = current_version + 1 if action == 'regenerate' else 1
            tx.update(sess_ref, {
                'passcode_hash': new_hash,
                'passcode_version': new_version,
                'passcode_updated_at': kst_now(),
                'passcode_updated_by': session.get('admin_email', ''),
            })
            return new_version

        try:
            new_version = _set_txn(db.transaction())
        except _Missing:
            return error('Session not found.', 404)
        except ValueError as ve:
            if str(ve) == 'already_set':
                return error('Passcode already set. Use regenerate instead.', 409)
            if str(ve) == 'not_set':
                return error('No existing passcode to regenerate. Use set instead.', 409)
            raise
        invalidate_sessions()
        log_audit(
            f'eval_v2_passcode_{action}',  # 'eval_v2_passcode_set' or 'eval_v2_passcode_regenerate'
            actor=session.get('admin_email', ''),
            target=session_id,
            details={'version': new_version},
            category='session',
        )
        return success({'passcodeEnabled': True, 'passcodeVersion': new_version})
    except (ValueError, TypeError, KeyError) as e:
        logger.warning('api_session_passcode input error: %s', e)
        return error('Invalid input.', 400)
    except Exception:
        logger.exception('api_session_passcode unexpected error')
        return error('An internal error occurred.')


@eval_v2_api.route('/session/dates', methods=['POST'])
@api_admin_required
@limiter.limit('10 per minute', key_func=admin_rate_key)
def api_session_dates():
    """Update a session's start_date / end_date. Admin-only.

    Request: {sessionId, startDate, endDate} — both ISO YYYY-MM-DD.
    Response: success({startDate, endDate}) | error(msg).

    - status (active/closed) 와 독립 — closed 세션의 종료일 정정도 허용.
    - 기간 단축 시 기존 응답은 보존 — 미래 submit 만 KST 범위 검증으로 차단.
    """
    from datetime import date
    from google.cloud import firestore as _fs
    try:
        data = request.get_json(silent=True) or {}
        session_id = str(data.get('sessionId', '')).strip()
        start_str = str(data.get('startDate', '')).strip()
        end_str = str(data.get('endDate', '')).strip()
        if not session_id:
            return error('sessionId is required.')
        try:
            start_d = date.fromisoformat(start_str)
            end_d = date.fromisoformat(end_str)
        except ValueError:
            return error('Invalid date format (expected YYYY-MM-DD).')
        if start_d > end_d:
            return error('Start date must not be after end date.')

        db = get_firestore_client()
        sess_ref = db.collection(COL_EVAL_V2_SESSIONS).document(session_id)

        class _Missing(Exception):
            pass

        @_fs.transactional
        def _update_txn(tx):
            snap = sess_ref.get(transaction=tx)
            if not snap.exists:
                raise _Missing()
            prev = snap.to_dict() or {}
            tx.update(sess_ref, {
                'start_date': start_str,
                'end_date': end_str,
                'dates_updated_at': kst_now(),
                'dates_updated_by': session.get('admin_email', ''),
            })
            return prev.get('start_date', ''), prev.get('end_date', '')

        try:
            prev_start, prev_end = _update_txn(db.transaction())
        except _Missing:
            return error('Session not found.', 404)

        invalidate_sessions()
        log_audit(
            'eval_v2_session_dates_updated',
            actor=session.get('admin_email', ''),
            target=session_id,
            details={
                'prev_start': prev_start,
                'prev_end': prev_end,
                'new_start': start_str,
                'new_end': end_str,
            },
            category='session',
        )
        return success({'startDate': start_str, 'endDate': end_str})
    except (ValueError, TypeError, KeyError) as e:
        logger.warning('api_session_dates input error: %s', e)
        return error('Invalid input.', 400)
    except Exception:
        logger.exception('api_session_dates unexpected error')
        return error('An internal error occurred.')
