"""YouTube URL → 11자 video ID 추출.

허용 패턴: youtu.be/ID, youtube.com/watch?v=ID, shorts/ID, embed/ID, youtube-nocookie.com/embed/ID
ID 형식: `[A-Za-z0-9_-]{11}`
"""
import re
from urllib.parse import urlparse, parse_qs

_ID_RE = re.compile(r'^[A-Za-z0-9_-]{11}$')
_ALLOWED_HOSTS = {
    'youtu.be',
    'www.youtu.be',
    'youtube.com',
    'www.youtube.com',
    'm.youtube.com',
    'youtube-nocookie.com',
    'www.youtube-nocookie.com',
}


def extract_youtube_id(url):
    """URL에서 11자 video ID만 추출. 실패 시 None."""
    if not url or not isinstance(url, str):
        return None
    try:
        u = urlparse(url.strip())
    except Exception:
        return None
    if u.scheme not in ('http', 'https'):
        return None
    host = (u.hostname or '').lower()
    if host not in _ALLOWED_HOSTS:
        return None

    candidate = None
    if host.endswith('youtu.be'):
        candidate = u.path.lstrip('/').split('/', 1)[0]
    else:
        path = u.path or ''
        if path.startswith('/watch'):
            qs = parse_qs(u.query or '')
            vals = qs.get('v') or []
            candidate = vals[0] if vals else None
        elif path.startswith('/shorts/'):
            candidate = path[len('/shorts/'):].split('/', 1)[0]
        elif path.startswith('/embed/'):
            candidate = path[len('/embed/'):].split('/', 1)[0]
        elif path.startswith('/v/'):
            candidate = path[len('/v/'):].split('/', 1)[0]

    if candidate and _ID_RE.match(candidate):
        return candidate
    return None


def normalize_youtube_urls(raw_list, limit=5):
    """입력 URL 리스트를 {url, id} 딕셔너리 리스트로 정규화.

    잘못된 URL은 조용히 제외. limit 개수로 잘라낸다.
    """
    out = []
    if not isinstance(raw_list, list):
        return out
    seen = set()
    for raw in raw_list[: limit * 2]:
        vid = extract_youtube_id(raw)
        if not vid or vid in seen:
            continue
        seen.add(vid)
        out.append({'url': str(raw).strip()[:500], 'id': vid})
        if len(out) >= limit:
            break
    return out
