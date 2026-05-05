"""공통 시간 유틸리티. KST 변환은 반드시 이 모듈에서만 수행.

Usage:
    from app.utils.time_utils import KST, kst_now, kst_today, kst_date, parse_date
"""
from datetime import date, datetime, timezone, timedelta
from typing import Optional

KST = timezone(timedelta(hours=9))


def kst_now() -> str:
    """현재 시각을 KST 기준 문자열로 반환 (YYYY-MM-DD HH:MM:SS)."""
    return datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S')


def kst_today() -> str:
    """오늘 날짜를 KST 기준 YYYY-MM-DD 문자열로 반환."""
    return datetime.now(KST).strftime('%Y-%m-%d')


def kst_date() -> date:
    """오늘 날짜를 KST 기준 date 객체로 반환."""
    return datetime.now(KST).date()


def parse_date(s: str) -> Optional[date]:
    """YYYY-MM-DD 문자열을 date 객체로 파싱. 실패 시 None 반환."""
    if not s or not isinstance(s, str):
        return None
    try:
        return date.fromisoformat(s.strip())
    except (ValueError, TypeError):
        return None


def kst_now_iso() -> str:
    """현재 시각을 KST 기준 ISO 8601 문자열로 반환."""
    return datetime.now(KST).isoformat()


def utc_now_iso() -> str:
    """현재 시각을 UTC ISO 8601 문자열로 반환."""
    return datetime.now(timezone.utc).isoformat()


def to_kst_str(dt: datetime) -> str:
    """datetime 객체를 KST 문자열로 변환."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(KST).strftime('%Y-%m-%d %H:%M:%S')
