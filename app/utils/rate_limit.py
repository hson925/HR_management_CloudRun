"""Rate-limit key helpers for flask-limiter.

Cloud Run 환경 대응:
flask_limiter.util.get_remote_address 는 `request.remote_addr` 만 읽는다.
Cloud Run 은 Google 프록시 뒤에서 동작하므로 모든 요청의 remote_addr 이
동일한 프록시 IP 가 되어, per-IP rate limit 이 의미를 잃는다.
`client_ip_key()` 가 `X-Forwarded-For` 를 우선 사용하도록 래핑.

XFF 신뢰는 앞단이 **신뢰 가능한 프록시 (Cloud Run)** 로 고정된 경우에만 안전.
외부 클라이언트가 임의로 XFF 헤더를 붙여 보내도 Cloud Run 이 이를 덮어쓰므로
조작 불가능. 로컬 dev 에선 XFF 가 없어 remote_addr fallback.
"""
from flask import request, session


def client_ip_key() -> str:
    """실제 클라이언트 IP 반환. Cloud Run 프록시 뒤를 고려한 XFF 우선."""
    xff = request.headers.get('X-Forwarded-For', '')
    if xff:
        # XFF 는 콤마 구분의 체인 ("client, proxy1, proxy2"). 가장 왼쪽이 실제 클라.
        ip = xff.split(',')[0].strip()
        if ip:
            return ip
    return request.remote_addr or '0.0.0.0'


def admin_rate_key() -> str:
    """로그인한 admin 이메일 기준, 미로그인 시 client_ip_key() fallback."""
    return session.get('admin_email') or client_ip_key()
