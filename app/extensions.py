from flask_caching import Cache
from flask_limiter import Limiter

from app.utils.rate_limit import client_ip_key

cache = Cache()
# 기본 키 함수로 XFF-aware client_ip_key 사용 — Cloud Run 프록시 뒤에서
# flask_limiter.util.get_remote_address 를 쓰면 모든 요청의 IP 가 동일해져
# per-IP 제한이 무의미해지는 문제 방지.
limiter = Limiter(
    key_func=client_ip_key,
    default_limits=["200 per day", "50 per hour"],
    storage_uri="memory://"
)
