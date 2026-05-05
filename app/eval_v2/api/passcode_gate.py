"""Eval v2 회차별 passcode 인증 — 서버 게이트 + Flask 세션 토큰 관리.

설계 (plan: passcode-wise-planet.md):
- Firestore `eval_v2_sessions/{id}` 에 `passcode_hash` 가 있으면 "passcode 필요",
  없거나 빈 문자열이면 "public 세션".
- 로그인된 비퇴직자 (portal 가입자) 는 내부 직원으로 간주하여 passcode 면제.
- 검증 성공 시 Flask `session['eval_passcode_tokens'][session_id]` 에
  `{version, expires_at}` 저장 (TTL 30분). 이후 동일 세션 요청은 토큰으로 통과.
- admin 이 Regenerate 하면 Firestore 의 `passcode_version` 증가 → 기존 토큰의
  version 과 불일치 → 자동 재인증 강제.

재사용: werkzeug.security (기존 campus_password_service 와 동일 패턴).
"""
import logging
import secrets
from datetime import datetime, timedelta

from flask import session
from werkzeug.security import generate_password_hash, check_password_hash

from app.constants import RETIRED_ROLES
from app.utils.time_utils import KST

logger = logging.getLogger(__name__)

# Unambiguous alphabet — O/0/1/I/l 제거하여 이메일·프린트에서 혼동 방지.
_PASSCODE_ALPHABET = 'ABCDEFGHJKMNPQRSTUVWXYZ23456789'
_AUTO_PASSCODE_LEN = 8

_TOKEN_TTL_MINUTES = 30
_MIN_PASSCODE_LEN = 4
_MAX_PASSCODE_LEN = 64


def generate_passcode() -> str:
    """Auto-generate 8-char alphanumeric passcode (대문자+숫자, 혼동문자 제외)."""
    return ''.join(secrets.choice(_PASSCODE_ALPHABET) for _ in range(_AUTO_PASSCODE_LEN))


def hash_passcode(plain: str) -> str:
    """passcode 평문 → scrypt hash 문자열."""
    return generate_password_hash(plain, method='scrypt')


def verify_passcode(plain: str, hashed: str) -> bool:
    """clock-safe 비교 via werkzeug."""
    try:
        return check_password_hash(hashed, plain)
    except Exception:
        return False


def validate_passcode_format(raw) -> tuple[bool, str]:
    """admin 수동 입력 passcode 유효성 검증. (ok, normalized_or_msg)."""
    if not isinstance(raw, str):
        return False, 'Passcode must be a string.'
    s = raw.strip()
    if len(s) < _MIN_PASSCODE_LEN:
        return False, f'Passcode must be at least {_MIN_PASSCODE_LEN} characters.'
    if len(s) > _MAX_PASSCODE_LEN:
        return False, f'Passcode exceeds maximum length ({_MAX_PASSCODE_LEN}).'
    return True, s


def is_internal_bypass() -> bool:
    """로그인된 비퇴직자 = 내부 직원 = passcode 면제 대상.
    admin/MASTER 뿐 아니라 NET/GS/TL/STL 등 모든 portal 가입자가 면제.
    RETIRED_ROLES('retired','퇴사') 만 제외.
    """
    if not session.get('admin_auth'):
        return False
    return session.get('admin_code', '') not in RETIRED_ROLES


def passcode_required_for(session_doc: dict) -> bool:
    """이 세션이 '현재 이 요청자' 에게 passcode 입력을 요구하는가.

    UI (드롭다운 자물쇠 아이콘) 와 서버 게이트 양쪽에서 동일 규칙으로 판단.
    """
    if not session_doc:
        return False
    if not session_doc.get('passcode_hash'):
        return False
    return not is_internal_bypass()


def _get_tokens() -> dict:
    tokens = session.get('eval_passcode_tokens')
    if not isinstance(tokens, dict):
        return {}
    return tokens


def has_valid_token(session_id: str, required_version: int) -> bool:
    tokens = _get_tokens()
    tok = tokens.get(session_id)
    if not isinstance(tok, dict):
        return False
    if int(tok.get('version', 0)) != int(required_version or 0):
        return False
    try:
        expires = datetime.fromisoformat(tok.get('expires_at', ''))
    except (ValueError, TypeError):
        return False
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=KST)
    return datetime.now(KST) < expires


def grant_token(session_id: str, version: int, ttl_minutes: int = _TOKEN_TTL_MINUTES) -> None:
    tokens = dict(_get_tokens())
    tokens[session_id] = {
        'version': int(version or 0),
        'expires_at': (datetime.now(KST) + timedelta(minutes=ttl_minutes)).isoformat(),
    }
    session['eval_passcode_tokens'] = tokens
    session.modified = True


def revoke_token(session_id: str) -> None:
    tokens = dict(_get_tokens())
    if session_id in tokens:
        tokens.pop(session_id, None)
        session['eval_passcode_tokens'] = tokens
        session.modified = True


def gate_check(session_doc: dict) -> bool:
    """서버 측 최종 게이트. 요청이 세션에 접근 가능한지 판정.

    통과 조건:
    1) passcode 가 설정되지 않은 세션이거나,
    2) 요청자가 내부 직원이거나,
    3) 해당 세션에 대한 유효 토큰(같은 version, 만료 전) 이 있음.
    """
    if not session_doc:
        return False
    if not session_doc.get('passcode_hash'):
        return True
    if is_internal_bypass():
        return True
    required_version = int(session_doc.get('passcode_version', 0) or 0)
    session_id = session_doc.get('id') or session_doc.get('session_id') or ''
    if not session_id:
        return False
    return has_valid_token(session_id, required_version)
