"""
app/services/role_service.py
portal_roles Firestore CRUD + 60초 TTL 메모리 캐시 + boot-time seed.

설계 기준:
- system role 7개 (admin/MASTER/NET/GS/TL/STL/retired/퇴사) 잠금 — 이름·삭제·수정 불가
- custom role 추가만 허용 (label 만 수정 가능, 삭제 미지원)
- Firestore 장애 시 _FALLBACK_NAMES 응답 — system role 검증만 가능 (fail-closed)
- multi-instance autoscale 시 최대 60초 propagate 지연
"""
import logging
import re
import threading
from datetime import datetime, timedelta, timezone

from app.services.firebase_service import get_firestore_client

logger = logging.getLogger(__name__)

PORTAL_ROLES_COLLECTION = 'portal_roles'

# system role 7개 — 잠금 대상. seed 시 portal_roles 에 idempotent upsert.
SYSTEM_ROLES = (
    ('admin', 'Admin'),
    ('MASTER', 'MASTER (legacy)'),
    ('NET', 'NET'),
    ('GS', 'GS'),
    ('TL', 'TL'),
    ('STL', 'STL'),
    ('retired', 'Retired'),
    ('퇴사', '퇴사'),
)

# Firestore 장애 시 fallback — system role name 만 응답.
# custom role 검증·할당은 거부 (fail-closed).
_FALLBACK_NAMES = frozenset(name for name, _ in SYSTEM_ROLES)

# system role 이름 + reserved sentinel — custom role name 으로 사용 불가
_RESERVED_NAMES = _FALLBACK_NAMES | frozenset({'__public__', '__all__'})

# RETIRED 표기 — get_role_names_excluding_retired 에서 제외
_RETIRED_NAMES = frozenset({'retired', '퇴사'})

# legacy system role — 첫 부팅 시 자동으로 deprecated:true 부여.
# 이미 deprecated 필드가 doc 에 명시되어 있으면 admin 결정 보존 (idempotent guard).
_LEGACY_SYSTEM_NAMES = frozenset({'MASTER', '퇴사'})

# custom role name 정규식: 첫 글자는 알파벳, 이후 영숫자/_/- 허용, 2-31자
_NAME_RE = re.compile(r'^[a-zA-Z][a-zA-Z0-9_-]{1,30}$')

_MAX_LABEL_LEN = 50

# ── Cache ───────────────────────────────────────────────
_TTL_SECONDS = 60
_cache: dict = {
    'roles': None,        # list[dict] — 정렬된 전체 role
    'loaded_at': None,    # datetime UTC
}
_lock = threading.Lock()
_seed_lock = threading.Lock()
_seed_done = False


def _is_expired() -> bool:
    loaded_at = _cache.get('loaded_at')
    if not loaded_at:
        return True
    return datetime.now(timezone.utc) - loaded_at > timedelta(seconds=_TTL_SECONDS)


def _load_from_firestore() -> list:
    """portal_roles 컬렉션 전체 조회 → 정렬된 dict list. Firestore 실패 시 raise."""
    db = get_firestore_client()
    docs = db.collection(PORTAL_ROLES_COLLECTION).stream()
    roles = []
    for snap in docs:
        d = snap.to_dict() or {}
        name = d.get('name') or snap.id
        roles.append({
            'name': name,
            'label': d.get('label') or name,
            'is_system': bool(d.get('is_system', False)),
            'deprecated': bool(d.get('deprecated', False)),
        })
    # 정렬: is_system desc, deprecated asc (active 먼저), name asc — UI 일관성
    roles.sort(key=lambda r: (not r['is_system'], r['deprecated'], r['name']))
    return roles


def _refresh_cache_or_fallback() -> list:
    """캐시 갱신 시도 — Firestore 장애 시 _FALLBACK_NAMES 로 응답.
    fallback 에서도 _LEGACY_SYSTEM_NAMES 는 deprecated:true 로 표시 → 일관된 UX.
    """
    try:
        roles = _load_from_firestore()
        with _lock:
            _cache['roles'] = roles
            _cache['loaded_at'] = datetime.now(timezone.utc)
        return roles
    except Exception:
        logger.exception('role_service._load_from_firestore failed — using _FALLBACK_NAMES')
        return [
            {
                'name': name,
                'label': label,
                'is_system': True,
                'deprecated': name in _LEGACY_SYSTEM_NAMES,
            }
            for name, label in SYSTEM_ROLES
        ]


def _get_cached_or_load() -> list:
    """캐시 hit 면 즉시, 만료면 single-flight 갱신 (다른 thread 는 lock 대기)."""
    if not _is_expired():
        with _lock:
            roles = _cache.get('roles')
            if roles is not None:
                return list(roles)
    with _lock:
        # 더블 체크 — lock 대기 중 다른 thread 가 이미 갱신했을 수 있음
        if not _is_expired():
            roles = _cache.get('roles')
            if roles is not None:
                return list(roles)
    return list(_refresh_cache_or_fallback())


# ── Public API ───────────────────────────────────────────


def get_all_roles(include_deprecated: bool = False) -> list:
    """name + label + is_system + deprecated dict list (정렬됨). UI 표시용.
    기본은 deprecated 제외 — 모든 fetch 호출자가 자동으로 legacy 배제.
    admin UI "Show legacy" 토글 시 True 로 호출.
    """
    roles = _get_cached_or_load()
    if include_deprecated:
        return list(roles)
    return [r for r in roles if not r.get('deprecated', False)]


def get_role_names(include_deprecated: bool = False) -> list:
    """활성 role name list (cached). 검증·드롭다운 후보용. 기본은 deprecated 제외."""
    return [r['name'] for r in get_all_roles(include_deprecated=include_deprecated)]


def get_role_names_excluding_retired(include_deprecated: bool = False) -> list:
    """retired/퇴사 제외 — chip UI / portal_role_mappings 후보용 (__public__ 별도).
    기본은 deprecated 도 제외.
    """
    return [
        r['name'] for r in get_all_roles(include_deprecated=include_deprecated)
        if r['name'] not in _RETIRED_NAMES
    ]


def get_role_label(name) -> str:
    """portal_roles 캐시에서 단일 role 의 label 조회. fail-soft.

    - name 이 None / 빈 문자열 / 비-string → name 그대로 반환 (또는 빈 문자열).
    - 캐시 hit: portal_roles.label 반환.
    - 캐시 miss + Firestore 정상: 새로 로드 후 lookup.
    - Firestore 장애: SYSTEM_ROLES 튜플 fallback.
    - 결국 매칭 실패: name 그대로 반환 (custom role 이 캐시에 없거나 deprecated).

    Jinja 필터 / report_service / 어디서나 호출 가능. 매 호출당 평균 캐시 메모리 read 1회.
    """
    if not isinstance(name, str) or not name:
        return name or ''
    # include_deprecated=True — deprecated 사용자 카드도 raw role 그대로 보지 않고 label 보임
    for r in get_all_roles(include_deprecated=True):
        if r.get('name') == name:
            return r.get('label') or name
    return name


def invalidate_cache() -> None:
    with _lock:
        _cache['roles'] = None
        _cache['loaded_at'] = None


def seed_system_roles() -> None:
    """boot-time idempotent upsert. multi-thread race safe (seed_lock + 1회 플래그)."""
    global _seed_done
    if _seed_done:
        return
    with _seed_lock:
        if _seed_done:
            return
        try:
            db = get_firestore_client()
            now = datetime.now(timezone.utc).isoformat()
            for name, label in SYSTEM_ROLES:
                ref = db.collection(PORTAL_ROLES_COLLECTION).document(name)
                snap = ref.get()
                if snap.exists:
                    existing = snap.to_dict() or {}
                    # is_system 누락 시 강제 보정 + label 누락 시 초기 기본 — 그 외 보존
                    patch = {}
                    if not existing.get('is_system'):
                        patch['is_system'] = True
                    if not existing.get('label'):
                        patch['label'] = label
                    if not existing.get('name'):
                        patch['name'] = name
                    # deprecated 필드 누락 시에만 자동 부여 — admin 의 Restore 결정 보존 (idempotent)
                    if 'deprecated' not in existing:
                        patch['deprecated'] = name in _LEGACY_SYSTEM_NAMES
                    if patch:
                        patch['updated_at'] = now
                        ref.update(patch)
                else:
                    ref.set({
                        'name': name,
                        'label': label,
                        'is_system': True,
                        'deprecated': name in _LEGACY_SYSTEM_NAMES,
                        'created_at': now,
                        'updated_at': now,
                        'created_by': 'system_seed',
                    })
            _seed_done = True
            invalidate_cache()
            logger.info('seed_system_roles: %d system roles ensured', len(SYSTEM_ROLES))
        except Exception:
            # 부팅 계속 — 다음 호출 (lazy lookup) 시 fallback 으로 우회
            logger.exception('seed_system_roles failed — startup continues with fallback')


def _validate_name(name: str) -> str:
    """name 정규화 + 검증. 위반 시 ValueError(메시지)."""
    if not isinstance(name, str):
        raise ValueError('name must be a string')
    name = name.strip()
    if not _NAME_RE.match(name):
        raise ValueError('name must match ^[a-zA-Z][a-zA-Z0-9_-]{1,30}$')
    if name in _RESERVED_NAMES:
        raise ValueError(f'name "{name}" is reserved')
    return name


def _validate_label(label: str) -> str:
    if not isinstance(label, str):
        raise ValueError('label must be a string')
    label = label.strip()
    if not label:
        raise ValueError('label is required')
    if len(label) > _MAX_LABEL_LEN:
        raise ValueError(f'label exceeds {_MAX_LABEL_LEN} chars')
    # XSS 방지 — HTML 태그 시작/종료 char 거부.
    # `<` `>` 만 차단해 정상 문장의 apostrophe ("It's"), quote, ampersand 는 통과.
    # render sink 측은 _escHtml 등으로 항상 escape (defense-in-depth).
    if '<' in label or '>' in label:
        raise ValueError('label must not contain HTML tag characters (< or >)')
    return label


def add_role(name: str, label: str, created_by: str) -> dict:
    """custom role 추가. system 예약어·중복·정규식 위반 시 ValueError. 성공 시 dict 반환."""
    name = _validate_name(name)
    label = _validate_label(label)
    db = get_firestore_client()
    ref = db.collection(PORTAL_ROLES_COLLECTION).document(name)
    snap = ref.get()
    if snap.exists:
        raise ValueError(f'role "{name}" already exists')
    now = datetime.now(timezone.utc).isoformat()
    payload = {
        'name': name,
        'label': label,
        'is_system': False,
        'created_at': now,
        'updated_at': now,
        'created_by': created_by or 'unknown',
    }
    ref.set(payload)
    invalidate_cache()
    return {'name': name, 'label': label, 'is_system': False, 'deprecated': False}


def update_role_label(name: str, label: str, updated_by: str) -> dict:
    """role 의 label 수정. system role 도 허용 (식별자 name 은 변경 불가).
    name 자체 변경은 미지원 — 코드 전체의 raw role 값 정합성 보존을 위해.
    """
    if not isinstance(name, str):
        raise ValueError('name must be a string')
    name = name.strip()
    label = _validate_label(label)
    db = get_firestore_client()
    ref = db.collection(PORTAL_ROLES_COLLECTION).document(name)
    snap = ref.get()
    if not snap.exists:
        raise LookupError(f'role "{name}" not found')
    existing = snap.to_dict() or {}
    now = datetime.now(timezone.utc).isoformat()
    ref.update({
        'label': label,
        'updated_at': now,
        'updated_by': updated_by or 'unknown',
    })
    invalidate_cache()
    return {
        'name': name,
        'label': label,
        'is_system': bool(existing.get('is_system', False)),
        'deprecated': bool(existing.get('deprecated', False)),
    }


def set_role_deprecated(name: str, deprecated: bool, actor: str) -> dict:
    """role 의 deprecated 토글. system + custom 모두 가능. True ↔ False 양방향.
    - 권한 게이트는 영향 없음 (raw role 값 매칭)
    - admin UI 후보 옵션에서만 노출 제어
    - doc 없음 → LookupError
    """
    if not isinstance(name, str):
        raise ValueError('name must be a string')
    name = name.strip()
    if not name:
        raise ValueError('name is required')
    deprecated = bool(deprecated)
    db = get_firestore_client()
    ref = db.collection(PORTAL_ROLES_COLLECTION).document(name)
    snap = ref.get()
    if not snap.exists:
        raise LookupError(f'role "{name}" not found')
    existing = snap.to_dict() or {}
    now = datetime.now(timezone.utc).isoformat()
    payload = {
        'deprecated': deprecated,
        'updated_at': now,
    }
    if deprecated:
        payload['deprecated_at'] = now
        payload['deprecated_by'] = actor or 'unknown'
    ref.update(payload)
    invalidate_cache()
    return {
        'name': name,
        'label': existing.get('label') or name,
        'is_system': bool(existing.get('is_system', False)),
        'deprecated': deprecated,
    }


def count_users_with_role(name: str) -> int:
    """portal_users 중 role==name 사용자 수. UI 표시용 (정확한 수)."""
    if not isinstance(name, str):
        raise ValueError('name must be a string')
    name = name.strip()
    db = get_firestore_client()
    try:
        # Firestore aggregation count() — metadata-only, 매우 저렴
        agg = db.collection('portal_users').where('role', '==', name).count().get()
        # AggregationQuery 반환 형식: [[<AggregationResult value=N>]]
        return int(agg[0][0].value)
    except Exception:
        # aggregation 미지원/실패 시 fallback: stream 으로 카운트 (소규모: ~150 사용자)
        logger.exception('count_users_with_role aggregation failed, falling back to stream')
        return sum(1 for _ in db.collection('portal_users').where('role', '==', name).stream())


def delete_role(name: str) -> None:
    """custom role 영구 삭제. 트랜잭션으로 TOCTOU race 차단.
    - system role → PermissionError
    - 보유자 1명 이상 → RuntimeError (admin 이 먼저 사용자를 다른 role 로 이동해야 함)
    - role 없음 → LookupError
    호출자가 audit log 기록.
    """
    if not isinstance(name, str):
        raise ValueError('name must be a string')
    name = name.strip()
    if not name:
        raise ValueError('name is required')
    from google.cloud import firestore as _fs
    db = get_firestore_client()
    role_ref = db.collection(PORTAL_ROLES_COLLECTION).document(name)
    users_q = db.collection('portal_users').where('role', '==', name).limit(1)

    @_fs.transactional
    def _delete_txn(tx):
        snap = role_ref.get(transaction=tx)
        if not snap.exists:
            raise LookupError(f'role "{name}" not found')
        if (snap.to_dict() or {}).get('is_system'):
            raise PermissionError('system role cannot be deleted')
        # 보유자 검사 — 트랜잭션 내부에서 read 하여 동시 할당과 race 방지
        for _ in users_q.get(transaction=tx):
            raise RuntimeError(f'role "{name}" is still assigned to users')
        tx.delete(role_ref)

    _delete_txn(db.transaction())
    invalidate_cache()
