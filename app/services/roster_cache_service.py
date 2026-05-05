"""
app/services/roster_cache_service.py
Roster 메모리 캐시 서비스 (TTL: 1시간)
"""
import logging
import threading
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

_cache = {
    'data': [],        # fetch_roster_data() 결과 리스트
    'loaded_at': None,
    'ttl_hours': 1,
}
_lock = threading.Lock()


def _load():
    from app.services.google_sheets import fetch_roster_data
    data = fetch_roster_data()
    with _lock:
        _cache['data'] = data
        _cache['loaded_at'] = datetime.now(timezone.utc)
    logger.info('Roster cache loaded: %d records', len(data))
    return data


def _is_expired() -> bool:
    loaded_at = _cache.get('loaded_at')
    if not loaded_at:
        return True
    return datetime.now(timezone.utc) - loaded_at > timedelta(hours=_cache['ttl_hours'])


def get_roster() -> list:
    """Roster 전체 조회. 캐시 만료 시 자동 갱신."""
    if _is_expired():
        _load()
    with _lock:
        return list(_cache['data'])


def refresh_cache() -> dict:
    """수동 캐시 갱신."""
    data = _load()
    return {
        'count': len(data),
        'loaded_at': _cache['loaded_at'].strftime('%Y-%m-%d %H:%M:%S UTC'),
    }


def get_cache_status() -> dict:
    loaded_at = _cache.get('loaded_at')
    return {
        'count': len(_cache['data']),
        'loaded_at': loaded_at.strftime('%Y-%m-%d %H:%M:%S UTC') if loaded_at else None,
        'expired': _is_expired(),
    }