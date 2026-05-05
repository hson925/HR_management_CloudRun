import logging
from app.extensions import cache
from app.services.firebase_service import get_firestore_client

from app.utils.time_utils import kst_now as _kst_now, kst_now_iso as _kst_now_iso

logger = logging.getLogger(__name__)

COLLECTION = 'portal_users'


def get_force_logout_at_cached(emp_id: str):
    """force_logout_at 필드를 60초 캐시로 조회.

    cache.get/set 방식으로 구현해 함수 객체 재생성 문제 없이 캐시 키를 고정.
    """
    _eid = emp_id.lower()
    cache_key = f'flo:{_eid}'
    cached = cache.get(cache_key)
    if cached is not None:
        return None if cached == '__none__' else cached
    try:
        doc = get_firestore_client().collection(COLLECTION).document(_eid).get(
            field_paths=['force_logout_at']
        )
        value = (doc.to_dict() or {}).get('force_logout_at')
    except Exception:
        logger.exception('get_force_logout_at_cached fetch failed for emp_id=%s', _eid)
        return None
    cache.set(cache_key, value if value is not None else '__none__', timeout=60)
    return value


def set_force_logout(emp_id: str) -> None:
    """해당 사용자의 모든 활성 세션을 무효화한다.

    portal_users 문서에 force_logout_at 타임스탬프를 기록한다.
    before_request가 60초 캐시로 이 값을 감지해 세션을 강제 종료한다.
    """
    try:
        _eid = emp_id.lower()
        get_firestore_client().collection(COLLECTION).document(_eid).update({
            'force_logout_at': _kst_now_iso(),
        })
        # 캐시 즉시 무효화 — set_force_logout 직후 요청에서 바로 반영
        cache.delete(f'flo:{_eid}')
        logger.info('force_logout_at set for emp_id=%s', _eid)
    except Exception:
        logger.exception('set_force_logout failed for emp_id=%s', emp_id)


def get_firestore_db():
    return get_firestore_client()


def _enrich_user(doc) -> dict:
    """Return doc dict with emp_id filled from doc.id when the field is empty."""
    d = doc.to_dict() or {}
    if not d.get('emp_id'):
        d['emp_id'] = doc.id
    return d


def get_user_by_emp_id(emp_id: str) -> dict | None:
    """Find a user by emp_id. Returns dict or None."""
    try:
        db = get_firestore_db()
        doc = db.collection(COLLECTION).document(emp_id.lower()).get()
        if doc.exists:
            return _enrich_user(doc)
        return None
    except Exception as e:
        logger.exception('get_user_by_emp_id error: %s', e)
        return None


def get_user_by_email(email: str) -> dict | None:
    """Find a user by email field. Returns dict or None."""
    try:
        db = get_firestore_db()
        docs = db.collection(COLLECTION).where('email', '==', email.lower().strip()).limit(1).stream()
        for doc in docs:
            return _enrich_user(doc)
        return None
    except Exception as e:
        logger.exception('get_user_by_email error: %s', e)
        return None


def get_all_users() -> list[dict]:
    """Return all users sorted by name."""
    try:
        db = get_firestore_db()
        docs = db.collection(COLLECTION).stream()
        users = [_enrich_user(doc) for doc in docs]
        users.sort(key=lambda u: u.get('name', ''))
        return users
    except Exception as e:
        logger.exception('get_all_users error: %s', e)
        return []


def register_user(emp_id: str, name: str, role: str, firebase_uid: str, email: str, campus: str = '') -> bool:
    """Create a new user document in Firestore. Returns True on success.
    Uses Firestore transaction to prevent TOCTOU race condition (duplicate registrations).
    """
    try:
        from google.cloud import firestore as _firestore
        db = get_firestore_db()
        now = _kst_now()
        doc_ref = db.collection(COLLECTION).document(emp_id.lower())

        @_firestore.transactional
        def _register_in_tx(tx):
            snap = doc_ref.get(transaction=tx)
            if snap.exists:
                raise ValueError('already_registered')
            tx.set(doc_ref, {
                'emp_id': emp_id.lower(),
                'name': name,
                'role': role,
                'firebase_uid': firebase_uid,
                'email': email.lower().strip(),
                'campus': campus,
                'registered_at': now,
                'updated_at': now,
                'updated_by': '',
                'notes': '',
            })

        _register_in_tx(db.transaction())
        return True
    except ValueError as ve:
        if 'already_registered' in str(ve):
            logger.warning('register_user: emp_id %s already exists (race condition prevented)', emp_id)
        return False
    except Exception as e:
        logger.exception('register_user error: %s', e)
        return False


_FIREBASE_UID_CHECK_TTL_HOURS = 24


def _parse_iso_safe(iso_str: str):
    """ISO8601 문자열을 timezone-aware datetime 으로 파싱. 실패 시 None."""
    from datetime import datetime
    try:
        return datetime.fromisoformat((iso_str or '').strip())
    except (TypeError, ValueError):
        return None


def backfill_firebase_uid_if_empty(emp_id: str, email: str) -> str:
    """portal_users/{emp_id}.firebase_uid 가 비어있으면 이메일로 Firebase Auth
    에서 실제 UID 를 조회해 저장. Google 로그인 기능 도입 이전에 생성된 문서,
    또는 complete_google_login 최초 실행 시 Firebase API 일시 실패로 빈 값이
    저장된 케이스 자가 복구.

    반환:
        - 실제 UID (이미 있었거나 방금 백필한 값)
        - 빈 문자열: Firebase Auth 에 해당 이메일 계정이 없거나 조회 실패

    동작 세부:
    - M-A: 진짜 미가입(UserNotFoundError) 으로 판정된 경우 `firebase_uid_checked_at`
      마커 저장. 이후 24h 동안은 반복 조회 skip → Firebase API 낭비 방지.
      사용자가 나중에 가입해도 24h 후 자동 재확인.
    - M-B: 성공적으로 백필하면 audit_logs 에 'firebase_uid_auto_backfill' 기록.
    - M-C: UserNotFoundError 만 "진짜 미가입" 으로 간주 — 마커 저장 후 False
      return. 네트워크/타임아웃 등 기타 Exception 은 마커 저장하지 않고
      빈 문자열 반환 → 다음 호출에서 재시도 유도 (최종 일관성).
    """
    email = (email or '').strip().lower()
    if not emp_id or not email:
        return ''
    try:
        from datetime import datetime, timedelta
        db = get_firestore_db()
        doc_ref = db.collection(COLLECTION).document(emp_id.lower())
        snap = doc_ref.get()
        if not snap.exists:
            return ''
        data = snap.to_dict() or {}
        current = data.get('firebase_uid', '')
        if current:
            return current  # 이미 값 있음 — no-op

        # M-A: 최근 24h 내에 "확인했지만 Firebase 에 없었음" 기록이 있으면 skip
        checked_at_iso = data.get('firebase_uid_checked_at', '')
        checked_at = _parse_iso_safe(checked_at_iso)
        if checked_at is not None:
            try:
                now = datetime.now(checked_at.tzinfo) if checked_at.tzinfo else datetime.now()
                if (now - checked_at) < timedelta(hours=_FIREBASE_UID_CHECK_TTL_HOURS):
                    return ''  # 최근 확인 완료 — 재조회 skip
            except Exception:
                pass  # 비교 실패 시 그냥 계속 진행

        # Firebase Admin SDK 로 실제 존재 확인
        from firebase_admin import auth as firebase_auth
        try:
            from firebase_admin.auth import UserNotFoundError as _UserNotFound
        except ImportError:
            _UserNotFound = None  # type: ignore

        try:
            fb_user = firebase_auth.get_user_by_email(email)
            uid = fb_user.uid
        except Exception as e:
            # M-C: UserNotFoundError 만 "진짜 미가입" — 마커 저장 후 빈 값 반환.
            # 타 Exception (네트워크·타임아웃 등) 은 일시 장애 → 마커 저장하지
            # 않아 다음 호출에서 재시도.
            if _UserNotFound is not None and isinstance(e, _UserNotFound):
                try:
                    doc_ref.update({'firebase_uid_checked_at': _kst_now_iso()})
                except Exception:
                    logger.exception('firebase_uid_checked_at marker write failed emp_id=%s', emp_id)
            else:
                logger.warning('backfill_firebase_uid: transient Firebase failure emp_id=%s: %s', emp_id, e)
            return ''

        # 백필 성공 — firebase_uid + 체크 마커 모두 업데이트. updated_at/by 는
        # 시스템 자가 복구라 건드리지 않음.
        doc_ref.update({
            'firebase_uid': uid,
            'firebase_uid_checked_at': _kst_now_iso(),
        })
        logger.info('firebase_uid backfilled emp_id=%s email=%s', emp_id, email)

        # M-B: audit_logs 감사 기록 — 언제 이 UID 가 생겼는지 추적 가능
        try:
            from app.services.audit_service import log_audit
            log_audit('firebase_uid_auto_backfill', actor='system:heal',
                      target=emp_id, category='general',
                      details={'email': email, 'source': 'backfill_firebase_uid_if_empty'})
        except Exception:
            logger.exception('log_audit for firebase_uid_auto_backfill failed emp_id=%s', emp_id)
        return uid
    except Exception:
        logger.exception('backfill_firebase_uid_if_empty failed emp_id=%s', emp_id)
        return ''


def update_user_email(emp_id: str, new_email: str) -> bool:
    """Update the email field for a user. Returns True on success."""
    try:
        db = get_firestore_db()
        db.collection(COLLECTION).document(emp_id.lower()).update({
            'email': new_email.lower().strip(),
            'updated_at': _kst_now(),
        })
        return True
    except Exception as e:
        logger.exception('update_user_email error: %s', e)
        return False


def update_user_role(emp_id: str, role: str, updated_by: str = '') -> bool:
    """Update the role field for a user. Returns True on success."""
    try:
        db = get_firestore_db()
        db.collection(COLLECTION).document(emp_id.lower()).update({
            'role': role,
            'updated_at': _kst_now(),
            'updated_by': updated_by,
        })
        return True
    except Exception as e:
        logger.exception('update_user_role error: %s', e)
        return False


def update_user(emp_id: str, updates: dict) -> bool:
    """Generic partial update for a user document. Returns True on success."""
    try:
        db = get_firestore_db()
        updates['updated_at'] = _kst_now()
        db.collection(COLLECTION).document(emp_id.lower()).update(updates)
        return True
    except Exception as e:
        logger.exception('update_user error: %s', e)
        return False


def delete_user(emp_id: str) -> bool:
    """Delete a user document from Firestore. Returns True on success."""
    try:
        db = get_firestore_db()
        db.collection(COLLECTION).document(emp_id.lower()).delete()
        return True
    except Exception as e:
        logger.exception('delete_user error: %s', e)
        return False


def is_emp_id_registered(emp_id: str) -> bool:
    """Check if an emp_id already exists in Firestore."""
    try:
        db = get_firestore_db()
        doc = db.collection(COLLECTION).document(emp_id.lower()).get()
        return doc.exists
    except Exception as e:
        logger.exception('is_emp_id_registered error: %s', e)
        return False



