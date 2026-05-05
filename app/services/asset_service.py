"""
app/services/asset_service.py
PDF 결과지에 임베드되는 정적 자산을 base64 data URI 로 인코딩 + 캐싱.
WeasyPrint 의 base_url 의존성 회피 (file:// 스키마 + url() fetcher 함정).
"""
import base64
import logging
import os
from typing import Dict

logger = logging.getLogger(__name__)

_CACHE: Dict[str, str] = {}

_MIME_MAP = {
    '.png':  'image/png',
    '.jpg':  'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.gif':  'image/gif',
    '.svg':  'image/svg+xml',
    '.webp': 'image/webp',
}


def get_static_data_uri(filename: str, mime: str | None = None) -> str:
    """app/static/<filename> 을 base64 data URI 로 인코딩 + 모듈 레벨 캐싱.

    Gunicorn worker 1 환경 안전. 로드 실패 시 빈 문자열 + 로그.
    템플릿은 {% if uri %} 가드로 시각 깨짐 방지.

    Args:
        filename: app/static/ 기준 상대 경로 (예: 'logo-white.png')
        mime: 명시 mime. None 이면 확장자로 추론
    """
    if filename in _CACHE:
        return _CACHE[filename]
    try:
        path = os.path.join(os.path.dirname(__file__), '..', 'static', filename)
        path = os.path.normpath(path)
        if mime is None:
            ext = os.path.splitext(filename)[1].lower()
            mime = _MIME_MAP.get(ext, 'application/octet-stream')
        with open(path, 'rb') as f:
            uri = f'data:{mime};base64,' + base64.b64encode(f.read()).decode()
    except Exception:
        logger.exception('static asset 로드 실패: %s', filename)
        uri = ''
    _CACHE[filename] = uri
    return uri
