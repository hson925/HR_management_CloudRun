"""공지사항 서비스 레이어 — 순수 함수, Flask session 불참조."""
import hashlib
import logging
import re
from datetime import datetime, timezone, timedelta

from app.constants import ADMIN_ROLES
from app.extensions import cache
from app.services.firebase_service import get_firestore_client

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────
_ALL_ROLES = {'NET', 'GS', 'TL', 'STL', 'admin'}  # MASTER 제외 (legacy deprecated). _can_read 는 ADMIN_ROLES 별도 사용으로 admin/MASTER 모두 인정.
_SELECTABLE_ROLES = ['NET', 'GS', 'TL', 'STL', 'admin']
_ALL_SENTINEL = '__all__'
_MAX_TITLE_LEN = 300
_MAX_CONTENT_LEN = 50_000
_MAX_YT = 5
_MAX_IMAGES = 30
_MAX_ATTACHMENTS = 10
_MAX_COMMENT_LEN = 2_000
_MAX_POLL_OPTIONS = 10
_MAX_POLL_OPTION_LEN = 200
_MAX_POLL_QUESTION_LEN = 500
_POLL_OPT_ID_RE = re.compile(r'^[0-9a-f]{8}$')


def _can_read(doc, role):
    if not doc:
        return False
    roles = doc.get('allowed_roles') or []
    if not roles or _ALL_SENTINEL in roles:
        return True
    if role in roles:
        return True
    # admin always reads
    if role in ADMIN_ROLES:
        return True
    return False


def _normalize_allowed_roles(raw):
    """입력 배열 → 센티넬 정규화. 빈 선택 = 전체 공개 (`['__all__']`)."""
    if not isinstance(raw, list):
        return [_ALL_SENTINEL]
    cleaned = [r for r in raw if isinstance(r, str) and r in _SELECTABLE_ROLES]
    if not cleaned:
        return [_ALL_SENTINEL]
    # admin 선택 시 MASTER 도 함께 허용 (role migration)
    out = list(dict.fromkeys(cleaned))
    if 'admin' in out and 'MASTER' not in out:
        out.append('MASTER')
    return out


def _validate_refs_multi(raw, allowed_prefixes, max_count, keys):
    """image_refs/attachment_refs 구조 검증 (다중 prefix 허용).

    allowed_prefixes: 허용할 Storage path 프리픽스 튜플.
    keys: 각 항목에서 보존할 키 리스트 (path 는 필수).
    """
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw[:max_count]:
        if not isinstance(item, dict):
            continue
        path = str(item.get('path', ''))
        if '..' in path or '\x00' in path or '//' in path:
            continue
        if not any(path.startswith(p) for p in allowed_prefixes):
            continue
        entry = {'path': path}
        for k in keys:
            if k in item:
                entry[k] = item[k]
        out.append(entry)
    return out


def _doc_to_summary(doc_id, data):
    """목록용 요약 dict."""
    created = data.get('created_at', '')
    preview = data.get('content_text', '') or ''
    return {
        'id': doc_id,
        'title': data.get('title', ''),
        'preview': preview[:200],
        'created_at': created,
        'author_name': data.get('author_name', ''),
        'pinned': bool(data.get('pinned')),
        'status': data.get('status', 'published'),
        'images_count': len(data.get('images') or []),
        'attachments_count': len(data.get('attachments') or []),
        'youtube_count': len(data.get('youtube_videos') or []),
        'is_new': _is_new(created),
        'poll_enabled': bool((data.get('poll') or {}).get('enabled')),
        'comment_authors': ' '.join(data.get('comment_authors') or []),
    }


def _voter_key(email, mode):
    """anonymous mode: sha256(email)[:16] → no email stored; named mode: email itself."""
    if mode == 'anonymous':
        norm = (email or '').lower().strip()
        return hashlib.sha256(norm.encode()).hexdigest()[:16]
    return email


def _is_poll_ended(poll):
    if not poll:
        return False
    ends_at = poll.get('ends_at')
    if not ends_at:
        return False
    try:
        dt = datetime.fromisoformat(ends_at.replace('Z', '+00:00'))
        return datetime.now(timezone.utc) > dt
    except Exception:
        return False


def _is_new(created_at_iso):
    if not created_at_iso:
        return False
    try:
        dt = datetime.fromisoformat(created_at_iso.replace('Z', '+00:00'))
        return (datetime.now(timezone.utc) - dt) <= timedelta(days=7)
    except Exception:
        return False


def _reaction_key(email):
    norm = (email or '').lower().strip()
    return hashlib.sha256(norm.encode('utf-8')).hexdigest()[:16]


def _reaction_count(ref):
    """Count reactions using Firestore aggregation query (1 read, not O(N))."""
    try:
        result = ref.collection('reactions').count().get()
        return int(result[0][0].value)
    except Exception:
        logger.exception('_reaction_count aggregation failed')
        # Fallback: direct stream. Still works, just more expensive.
        return sum(1 for _ in ref.collection('reactions').stream())


class _AnnouncementConflict(Exception):
    def __init__(self, current):
        self.current_version = current


@cache.memoize(timeout=60)
def get_top_announcements_for_user(n, user_role):
    """메인 대시보드 위젯용 상위 공지 N건.

    역할별 캐시(60초). save/delete 시 cache.delete_memoized 로 전 키 무효화.
    단일 필드 정렬만 사용 → 복합 인덱스 배포 불필요.
    """
    try:
        db = get_firestore_client()
        q = (db.collection('announcements')
             .order_by('created_at', direction='DESCENDING')
             .limit(n * 6))  # role 필터 여유분
        # to_dict()는 한 번만 호출 후 (id, data) 튜플로 처리
        docs_data = [(d.id, d.to_dict() or {}) for d in q.stream()]
        # 위젯은 published만 표시 (draft 제외)
        filtered = [(doc_id, data) for doc_id, data in docs_data
                    if data.get('status') == 'published'
                    and _can_read(data, user_role)]
        # Firestore가 이미 created_at DESC로 반환하므로 stable sort로 pinned만 올리면 됨
        filtered.sort(key=lambda x: not bool(x[1].get('pinned')))
        return [_doc_to_summary(doc_id, data) for doc_id, data in filtered[:n]]
    except Exception:
        logger.exception('get_top_announcements_for_user failed role=%s', user_role)
        return []
