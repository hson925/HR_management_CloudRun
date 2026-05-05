"""공용 HTML/텍스트 sanitizer.

Quill 에디터 출력에 대한 bleach 화이트리스트와 헬퍼 함수를 한 곳에서 관리한다.
legal_bp 와 announcements_bp 가 동일한 정책을 공유한다.
"""
import re

import bleach
from bleach.css_sanitizer import CSSSanitizer


_ALLOWED_TAGS = {
    'p', 'br', 'span', 'div',
    'h1', 'h2', 'h3', 'h4',
    'strong', 'em', 'u', 's', 'sub', 'sup',
    'ol', 'ul', 'li',
    'blockquote', 'pre', 'code',
    'a',
    'img',
}
_ALLOWED_ATTRS = {
    '*': ['class', 'style'],
    'a': ['href', 'title', 'target', 'rel'],
    'img': ['src', 'alt', 'width', 'height', 'class', 'style'],
    'div': ['class', 'style', 'data-yt-idx'],  # YouTube inline marker blot
}
_ALLOWED_PROTOCOLS = ['http', 'https', 'mailto']

_ALLOWED_CSS_PROPS = [
    'color', 'background-color', 'font-size', 'font-weight',
    'font-style', 'text-align', 'text-decoration',
]
_CSS_SANITIZER = CSSSanitizer(allowed_css_properties=_ALLOWED_CSS_PROPS)

_STRIP_BLOCKS_RE = re.compile(
    r'<(script|style)\b[^>]*>.*?</\1\s*>',
    re.IGNORECASE | re.DOTALL,
)


def sanitize_html(raw, max_len=50_000, allow_img=True):
    """Quill 출력 HTML을 bleach 화이트리스트 기반으로 살균한다.

    allow_img=False 이면 img 태그까지 전부 제거 (legal 문서처럼 이미지 없는 본문).
    """
    s = str(raw or '')[:max_len]
    s = _STRIP_BLOCKS_RE.sub('', s)
    tags = set(_ALLOWED_TAGS)
    attrs = dict(_ALLOWED_ATTRS)
    if not allow_img:
        tags.discard('img')
        attrs.pop('img', None)
    return bleach.clean(
        s,
        tags=tags,
        attributes=attrs,
        protocols=_ALLOWED_PROTOCOLS,
        css_sanitizer=_CSS_SANITIZER,
        strip=True,
    )


def strip_to_text(raw, max_len=300):
    """모든 HTML 제거 plain text. 제목/프리뷰/댓글에 사용."""
    s = str(raw or '')[:max_len]
    s = _STRIP_BLOCKS_RE.sub('', s)
    return bleach.clean(
        s,
        tags=set(),
        attributes={},
        strip=True,
    ).strip()
