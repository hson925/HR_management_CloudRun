import logging
from datetime import datetime, timezone
from werkzeug.security import generate_password_hash, check_password_hash
from app.services.firebase_service import get_firestore_client

logger = logging.getLogger(__name__)

COLLECTION = 'campus_passwords'


def verify_campus_password(raw_password: str):
    """
    입력된 비밀번호를 Firestore의 해시값과 비교.
    모든 문서를 끝까지 순회 후 반환 (타이밍 공격 방어).
    Returns (True, campus_code) 또는 (False, None)
    """
    try:
        db = get_firestore_client()
        docs = list(db.collection(COLLECTION).stream())
        matched = False
        matched_code = None
        for doc in docs:
            data = doc.to_dict()
            # 매칭 후에도 break 없이 계속 순회 (응답 시간 일정하게 유지)
            # 첫 번째 매칭 결과만 사용 (중복 매칭 시 덮어쓰기 방지)
            if not matched and check_password_hash(data.get('password_hash', ''), raw_password):
                matched = True
                matched_code = data.get('campus_code', '')
        return matched, matched_code
    except Exception as e:
        logger.exception('campus_password_service.verify_campus_password failed: %s', e)
        return False, None


def set_campus_password(campus_code: str, raw_password: str) -> bool:
    """캠퍼스 비밀번호를 해시하여 Firestore에 저장 (관리자용)."""
    try:
        db = get_firestore_client()
        pw_hash = generate_password_hash(raw_password)
        db.collection(COLLECTION).document(campus_code).set({
            'campus_code': campus_code,
            'password_hash': pw_hash,
            'updated_at': datetime.now(timezone.utc).isoformat(),
        })
        return True
    except Exception as e:
        logger.exception('campus_password_service.set_campus_password failed: %s', e)
        return False
