"""캐시 무효화 중앙 관리 모듈.

캐시 키 문자열을 여러 파일에 분산시키지 않고 이 모듈에서 일괄 관리한다.

사용 예:
    from app.services.cache_service import invalidate_sessions, invalidate_config

    invalidate_sessions()
    invalidate_config(eval_type, config_type)
"""
from app.extensions import cache


# ── 캐시 키 상수 ─────────────────────────────────────────
_KEY_SESSIONS_LIST = 'eval_v2_sessions_list'
_KEY_SUB_CTL_LIST  = 'sub_ctl_list'


# ── eval_v2 ───────────────────────────────────────────────

def invalidate_sessions():
    """eval_v2 세션 목록 캐시를 무효화한다."""
    cache.delete(_KEY_SESSIONS_LIST)


def invalidate_config(eval_type: str, config_type: str):
    """eval_v2 설정 캐시를 무효화한다 (get_config memoize)."""
    from app.eval_v2.api.common import get_config
    cache.delete_memoized(get_config, eval_type, config_type)


def invalidate_sub_ctl():
    """수업 담당 목록 캐시를 무효화한다."""
    cache.delete(_KEY_SUB_CTL_LIST)


# ── Announcements ─────────────────────────────────────────

def invalidate_top_announcements():
    """홈 화면 공지사항 캐시를 무효화한다."""
    from app.announcements.routes import get_top_announcements_for_user
    cache.delete_memoized(get_top_announcements_for_user)
