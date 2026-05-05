"""Eval v2 passcode 검증 엔드포인트.

- POST /api/v2/verify-passcode  (public, rate-limited)
  평가자가 세션 선택 후 모달에서 passcode 를 제출하면 호출.
  성공 시 Flask 세션에 토큰을 발급(grant_token). 클라이언트는 이후 질문 로드·
  제출 요청을 평소대로 진행하면 서버 게이트(passcode_gate.gate_check) 가 토큰을
  확인해 통과시킨다.
"""
import logging
from flask import request, jsonify, session

from app.eval_v2.blueprints import eval_v2_api
from app.extensions import limiter
from app.services.firebase_service import get_firestore_client
from app.services.audit_service import log_audit
from app.utils.rate_limit import client_ip_key
from app.constants import COL_EVAL_V2_SESSIONS
from app.eval_v2.api.passcode_gate import (
    verify_passcode, grant_token, is_internal_bypass,
)

logger = logging.getLogger(__name__)


@eval_v2_api.route('/verify-passcode', methods=['POST'])
@limiter.limit('5 per minute', key_func=client_ip_key)
def api_verify_passcode():
    """Verify a session passcode and grant a short-lived Flask session token.

    Request : {empId: str, sessionId: str, passcode: str}
    Response: {status: 'OK'} on success,
              {status: 'ERROR', message: 'Invalid passcode.'} otherwise.

    사번은 토큰 key 로 사용하지 않는다 (사번 enumeration 방지를 위해 에러 메시지도
    모호). 토큰은 세션 단위로 발급되며, 동일 브라우저 세션 동안만 유효.
    """
    try:
        data = request.get_json(silent=True) or {}
        session_id = str(data.get('sessionId', '')).strip()
        passcode = str(data.get('passcode', '')).strip()
        emp_id = str(data.get('empId', '')).strip().lower()

        if not session_id or not passcode:
            # 입력 검증 실패도 401 로 통일 — 세션 존재 여부 / passcode 정답 여부를
            # HTTP status 로 구별할 수 없게 함 (enumeration 방어).
            return jsonify({'status': 'ERROR', 'message': 'Invalid passcode.'}), 401

        db = get_firestore_client()
        sess_doc = db.collection(COL_EVAL_V2_SESSIONS).document(session_id).get()
        if not sess_doc.exists:
            # 존재하지 않는 세션도 동일 메시지 + 동일 HTTP status — enumeration 방지
            return jsonify({'status': 'ERROR', 'message': 'Invalid passcode.'}), 401

        sess_data = sess_doc.to_dict() or {}
        stored_hash = sess_data.get('passcode_hash', '') or ''

        # 세션이 확인된 후에만 bypass/no-passcode 분기를 평가. 이 순서가 중요 —
        # 먼저 bypass 로 빠지면 존재하지 않는 sessionId 에 대해서도 성공이 반환되어
        # API 계약이 모호해진다. enumeration 방어 관점에서도 세션 로드를 먼저.
        if not stored_hash:
            # passcode 없는 공개 세션에 대해 verify 를 호출한 비정상 요청 — 성공 처리.
            # 클라이언트는 이 분기에서 모달을 열 일이 없지만, 토큰 발급은 하지 않음
            # (게이트가 passcode_hash 없음으로 자동 통과).
            return jsonify({'status': 'OK', 'no_passcode': True})

        if is_internal_bypass():
            # 내부 직원은 passcode 검증을 거치지 않고 자동 통과. gate_check 에서도
            # 면제되므로 질문 로드·제출 시 모달 없이 진행됨. 토큰 발급 불필요.
            return jsonify({'status': 'OK', 'bypass': True})

        if verify_passcode(passcode, stored_hash):
            version = int(sess_data.get('passcode_version', 1) or 1)
            grant_token(session_id, version)
            return jsonify({'status': 'OK'})

        # 실패 감사 로그 — IP, emp_id(있으면), session_id. client_ip_key 는
        # XFF 의 가장 왼쪽(실제 클라이언트) 를 반환 + remote_addr fallback.
        ip = client_ip_key()
        try:
            log_audit(
                'eval_v2_passcode_failed',
                actor=emp_id or 'public',
                target=session_id,
                details={'ip': ip},
                category='session',
            )
        except Exception:
            logger.debug('log_audit failed for eval_v2_passcode_failed', exc_info=True)

        return jsonify({'status': 'ERROR', 'message': 'Invalid passcode.'}), 401
    except (ValueError, TypeError) as e:
        # 입력 파싱 실패 — enumeration 방어 유지, 401 반환.
        logger.warning('api_verify_passcode input error: %s', e)
        return jsonify({'status': 'ERROR', 'message': 'Invalid passcode.'}), 401
    except Exception:
        # 예기치 않은 코드 버그 / Firestore 장애. 500 으로 구분해 모니터링 신호 유지.
        logger.exception('api_verify_passcode unexpected error')
        return jsonify({'status': 'ERROR', 'message': 'Internal error.'}), 500
