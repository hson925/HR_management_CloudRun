"""
app/eval_v2/api/annual_eval/_helpers.py
Annual Eval 서브모듈 공통 상수 및 데코레이터/유틸.
정산 함수는 salary.py, 점수 계산은 scoring.py 로 분리.
"""
import logging
import re
from functools import wraps
from flask import request, session

from app.utils.response import error

# 단일 source of truth: firebase_service.NT_COLLECTIONS_BY_PRIORITY (priority 순 튜플).
# 하드코딩 drift 방지 + 중복 사번 해결 시 priority 순회가 자동 적용됨.
from app.services.firebase_service import NT_COLLECTIONS_BY_PRIORITY as _NT_COLLECTIONS

# 평가 폴더가 필요 없는 컬렉션 (R&D/SIS 팀). folder-status·bv-audit·create-folders
# 에서 이 컬렉션 문서는 제외한다.
_COLLECTIONS_WITHOUT_EVAL_FOLDER = {'nt_rnd'}
_EVAL_FOLDER_COLLECTIONS = tuple(c for c in _NT_COLLECTIONS if c not in _COLLECTIONS_WITHOUT_EVAL_FOLDER)

_EMP_ID_RE     = re.compile(r'^[a-zA-Z0-9_\-]{1,30}$')
_SESSION_ID_RE = re.compile(r'^[^/]{1,100}$')
_DATE_RE       = re.compile(r'^\d{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])$')

logger = logging.getLogger(__name__)


def _admin_email() -> str:
    """세션에서 admin 이메일 반환. 없으면 'unknown'."""
    return session.get('admin_email') or 'unknown'


def require_xhr(fn):
    """
    CSRF 경량 방어: POST 요청에 X-Requested-With: XMLHttpRequest 헤더 필수.
    JS fetch 호출은 모두 이 헤더를 포함해야 함.
    """
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if request.method == 'POST' and \
                request.headers.get('X-Requested-With') != 'XMLHttpRequest':
            return error('Forbidden.', 403)
        return fn(*args, **kwargs)
    return wrapper
