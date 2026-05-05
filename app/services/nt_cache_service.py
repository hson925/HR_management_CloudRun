"""
app/services/nt_cache_service.py
NT Info 메모리 캐시 서비스 (TTL: 24시간)
"""
import logging
import threading
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

_cache = {
    'data': {},        # { emp_id_lower: record }
    'loaded_at': None,
    'ttl_hours': 24,
}
_lock = threading.Lock()


def _load():
    """NT Info 전체를 로드해서 캐시에 저장.
    시트 순회 순서 (SHEET_CONFIG['NT_SHEETS'] = dyb → sub → R&D_SIS → CREO 3종) 의
    앞쪽 시트가 우선권을 가진다. 동일 emp_id 가 여러 시트에 있으면 **먼저 로드된
    시트 값을 유지** (기존엔 마지막에 덮어쓰여 CREO 가 DYB 를 이기던 버그 수정).
    """
    from app.services.google_sheets import fetch_nt_info
    from config import SHEET_CONFIG

    data = {}
    for sheet_name in SHEET_CONFIG.get('NT_SHEETS', []):
        try:
            records = fetch_nt_info(sheet_name)
            for rec in records:
                eid = str(rec.get('emp_id', '')).strip().lower()
                if not eid:
                    continue
                if eid in data:
                    continue  # 우선순위 상위 시트 값 유지
                data[eid] = rec
        except Exception as e:
            logger.warning('NT cache load failed (%s): %s', sheet_name, e)

    with _lock:
        _cache['data'] = data
        _cache['loaded_at'] = datetime.now(timezone.utc)

    logger.info('NT info cache loaded: %d records', len(data))
    return data


def _is_expired() -> bool:
    loaded_at = _cache.get('loaded_at')
    if not loaded_at:
        return True
    return datetime.now(timezone.utc) - loaded_at > timedelta(hours=_cache['ttl_hours'])


def get_nt_record(emp_id: str) -> dict:
    """사번으로 NT Info 레코드 조회. 캐시 만료 시 자동 갱신."""
    if _is_expired():
        _load()
    with _lock:
        return _cache['data'].get(emp_id.strip().lower(), {})


def refresh_cache() -> dict:
    """수동 캐시 갱신. 로드된 인원 수 반환."""
    data = _load()
    return {
        'count': len(data),
        'loaded_at': _cache['loaded_at'].strftime('%Y-%m-%d %H:%M:%S UTC'),
    }


def get_cache_status() -> dict:
    """캐시 상태 조회"""
    loaded_at = _cache.get('loaded_at')
    return {
        'count': len(_cache['data']),
        'loaded_at': loaded_at.strftime('%Y-%m-%d %H:%M:%S UTC') if loaded_at else None,
        'expired': _is_expired(),
    }

def update_nt_record_field(emp_id: str, field: str, value) -> None:
    """캐시에 있는 특정 레코드의 필드를 직접 업데이트"""
    key = emp_id.strip().lower()
    with _lock:
        if key in _cache['data']:
            _cache['data'][key][field] = value