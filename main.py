import os
import uuid
import time
import hmac
import logging
from datetime import timedelta
from dotenv import load_dotenv
from flask import Flask, g, render_template, request, session, jsonify, redirect
from werkzeug.middleware.proxy_fix import ProxyFix

logger = logging.getLogger(__name__)

from app.extensions import cache, limiter
from app.services.firebase_service import initialize_firebase
from app.services.user_service import get_force_logout_at_cached

load_dotenv()

app = Flask(__name__, template_folder='app/templates', static_folder='app/static')
_secret_key = os.environ.get("FLASK_SECRET_KEY")
if not _secret_key:
    raise RuntimeError("FLASK_SECRET_KEY environment variable is not set.")
app.secret_key = _secret_key

_RECOMMENDED_ENV_VARS = ('FIREBASE_SERVICE_ACCOUNT_JSON', 'GOOGLE_CLIENT_ID')
for _var in _RECOMMENDED_ENV_VARS:
    if not os.environ.get(_var):
        logger.warning('%s is not set — some features may be unavailable.', _var)

app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(minutes=15)
app.config['MAX_CONTENT_LENGTH'] = 12 * 1024 * 1024  # 12MB (공지사항 이미지 10MB + 여유)
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = True
app.config['SESSION_COOKIE_HTTPONLY'] = True

app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

_FIREBASE_PROJECT_ID = os.environ.get('FIREBASE_PROJECT_ID', 'premium-arc-490609-m4')

cache.init_app(app, config={'CACHE_TYPE': 'SimpleCache', 'CACHE_DEFAULT_TIMEOUT': 60})
limiter.init_app(app)
initialize_firebase()

# portal_roles 컬렉션 system role seed — idempotent, 부팅 실패하지 않도록 try/except.
# multi-thread race 는 role_service 내부 _seed_lock + 1회 플래그로 차단.
try:
    from app.services.role_service import seed_system_roles, get_role_label
    seed_system_roles()
    # Jinja 필터 등록 — `{{ role_name | role_label }}` 사용 가능.
    # 사이드바 / my_tasks 등 비-admin 페이지에서 raw role → label 표시.
    app.jinja_env.filters['role_label'] = get_role_label
except Exception:
    logger.exception('seed_system_roles failed — startup continues with fallback names')

_CSRF_SAFE_METHODS = {'GET', 'HEAD', 'OPTIONS', 'TRACE'}
# CSRF 허용 도메인: ALLOWED_HOSTS(쉼표 구분) 환경변수 설정 시 해당 도메인만 허용 (프로덕션)
# 미설정 시 request.host fallback 사용 (Cloud Shell 등 동적 도메인 환경)
_CSRF_CONFIGURED_HOSTS = set(filter(None, [
    h.strip() for h in os.environ.get('ALLOWED_HOSTS', '').split(',')
]))
_CSRF_LOCAL_HOSTS = {'localhost:8080', 'localhost:5000'}
# 로그인 전 단계 라우트만 면제 — /api/auth/account/* 는 로그인 후이므로 포함하지 않음
# Sync/cleanup 엔드포인트는 prefix 면제가 아닌 X-Sync-Secret 매칭 시에만 bypass
# (admin 세션 CSRF 공격 벡터 방지; Cloud Scheduler 는 secret 제시로 자연 통과)
_CSRF_EXEMPT_PREFIXES = (
    '/api/auth/firebase',
    '/api/auth/verify-otp',
    '/api/auth/verify-emp',
    '/api/auth/verify-staff',
    '/api/auth/complete-google-login',
    '/api/auth/register-send-otp',
    '/api/auth/register-verify-otp',
    '/api/auth/find-email',
    '/api/auth/reset-password-by-emp',
    '/api/find-employee',
)

# 퇴사자가 접근 가능한 경로 — 나머지는 /retired 로 리디렉션
_RETIRED_ALLOWED_PREFIXES = ('/retired', '/api/auth/', '/api/retired/', '/logout', '/account')

@app.before_request
def set_request_id():
    g.request_id = uuid.uuid4().hex[:8]
    g.start_time = time.monotonic()

@app.before_request
def require_login_for_api():
    # X-Sync-Secret 매칭 시 CSRF + global API auth 모두 bypass (Cloud Scheduler 경로)
    # 메서드/경로와 무관하게 매 요청 계산 — 비용 미미 (env read + hmac 한 번)
    _sync_secret = os.environ.get('NT_SYNC_SECRET', '')
    _req_sync_secret = request.headers.get('X-Sync-Secret', '')
    _sync_bypass = bool(
        _sync_secret and _req_sync_secret
        and hmac.compare_digest(_req_sync_secret, _sync_secret)
    )

    if session.get('admin_auth'):
        session.permanent = True
        session.modified = True

        # ── 강제 로그아웃 체크 (role 변경 / 비밀번호 변경 / 계정 정지 시 세션 무효화) ──
        emp_id = session.get('emp_id', '').lower()
        logged_in_at = session.get('logged_in_at', '')
        if emp_id and logged_in_at:
            force_at = get_force_logout_at_cached(emp_id)
            if force_at and force_at > logged_in_at:
                session.clear()
                if request.path.startswith('/api/'):
                    return jsonify({'status': 'ERROR', 'code': 'FORCE_LOGOUT',
                                    'message': 'Your session has been terminated. Please log in again.'}), 401
                return redirect('/login?reason=force_logout')

    # ── CSRF: Origin / Referer 검증 (POST/PUT/DELETE) ────────────────────────
    # Origin 없음 → Referer fallback → 둘 다 없으면 차단
    if request.method not in _CSRF_SAFE_METHODS:
        if not _sync_bypass and not any(request.path.startswith(p) for p in _CSRF_EXEMPT_PREFIXES):
            origin = request.headers.get('Origin')
            referer = request.headers.get('Referer')
            # 허용 Origin 목록 구성:
            # - ALLOWED_HOSTS 설정 시: 해당 도메인만 (프로덕션)
            # - 미설정 시: request.host fallback (Cloud Shell 등 동적 환경)
            allowed_hosts = set(_CSRF_LOCAL_HOSTS)
            if _CSRF_CONFIGURED_HOSTS:
                allowed_hosts.update(_CSRF_CONFIGURED_HOSTS)
            else:
                allowed_hosts.add(request.host)
            allowed_origins = set()
            for h in allowed_hosts:
                allowed_origins.add(f'https://{h}')
                allowed_origins.add(f'http://{h}')
            if origin is not None:
                if origin not in allowed_origins:
                    logger.warning('CSRF blocked: Origin=%s not in allowed list', origin)
                    return jsonify({'status': 'ERROR', 'message': 'Forbidden'}), 403
            elif referer is not None:
                from urllib.parse import urlparse
                ref_parsed = urlparse(referer)
                ref_origin = f'{ref_parsed.scheme}://{ref_parsed.netloc}'
                if ref_origin not in allowed_origins:
                    logger.warning('CSRF blocked: Referer=%s not in allowed list', ref_origin)
                    return jsonify({'status': 'ERROR', 'message': 'Forbidden'}), 403
            else:
                # Origin도 Referer도 없는 변경 요청 차단
                return jsonify({'status': 'ERROR', 'message': 'Forbidden'}), 403

    # ── 퇴사자 접근 제한 ─────────────────────────────────────────────────────
    if session.get('admin_auth') and session.get('admin_code') in ('retired', '퇴사'):
        if not any(request.path == p or request.path.startswith(p) for p in _RETIRED_ALLOWED_PREFIXES):
            if request.path.startswith('/api/'):
                return jsonify({'status': 'ERROR', 'message': 'Access restricted for inactive accounts.'}), 403
            return redirect('/retired')

    # ── API 인증 ─────────────────────────────────────────────────────────────
    allowed_api_paths = ['/api/auth/', '/api/find-employee',
                         '/api/v2/get-sessions', '/api/v2/get-questions', '/api/v2/submit-eval',
                         '/api/v2/verify-passcode']

    if request.path.startswith('/api/'):
        if not any(request.path.startswith(path) for path in allowed_api_paths):
            if not session.get('admin_auth') and not _sync_bypass:
                return jsonify({'status': 'ERROR', 'message': 'Admin permission required.'}), 401

@app.after_request
def add_security_headers(response):
    # ── Request ID + 응답 시간 로깅 ────────────────────────────────────────
    req_id = getattr(g, 'request_id', '')
    if req_id:
        response.headers['X-Request-ID'] = req_id
    start = getattr(g, 'start_time', None)
    if start and request.path.startswith('/api/'):
        duration_ms = int((time.monotonic() - start) * 1000)
        logger.info('%s %s %s %dms', request.method, request.path, response.status_code, duration_ms)

    # API 응답은 캐시 금지 (개인정보·평가 데이터 브라우저/프록시 캐시 방지)
    if request.path.startswith('/api/'):
        response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        response.headers['Pragma'] = 'no-cache'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    response.headers['Permissions-Policy'] = 'camera=(), microphone=(), geolocation=()'
    response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains; preload'
    response.headers['Cross-Origin-Opener-Policy'] = 'same-origin-allow-popups'
    response.headers['Content-Security-Policy'] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' "
            "https://cdn.tailwindcss.com https://cdn.jsdelivr.net "
            "https://www.gstatic.com https://apis.google.com; "
        "style-src 'self' 'unsafe-inline' "
            "https://cdn.jsdelivr.net https://fonts.googleapis.com; "
        "font-src 'self' https://cdn.jsdelivr.net https://fonts.gstatic.com; "
        "img-src 'self' data: blob: https://lh3.googleusercontent.com https://www.gstatic.com "
            "https://firebasestorage.googleapis.com https://storage.googleapis.com; "
        "connect-src 'self' https://cdn.jsdelivr.net https://www.gstatic.com "
            "https://www.googleapis.com https://firestore.googleapis.com "
            "https://identitytoolkit.googleapis.com https://securetoken.googleapis.com "
            "https://firebasestorage.googleapis.com https://storage.googleapis.com "
            f"https://{_FIREBASE_PROJECT_ID}.firebaseapp.com; "
        f"frame-src 'self' https://{_FIREBASE_PROJECT_ID}.firebaseapp.com "
            "https://www.youtube-nocookie.com https://www.youtube.com; "
        "object-src 'none'; "
        "base-uri 'self';"
    )
    return response

@app.errorhandler(404)
def handle_404(e):
    # /api/* 경로의 404는 "legacy 엔드포인트 잔존 참조" 신호 — 추적을 위해 로그 남김
    if request.path.startswith('/api/'):
        logger.warning(
            'stale-api-reference: %s %s referrer=%s',
            request.method, request.path, request.referrer or '-',
        )
        return jsonify({'status': 'ERROR', 'message': 'Not Found'}), 404
    return e


@app.route('/')
def index():
    announcements = []
    last_read_at = None
    try:
        from app.announcements.routes import get_top_announcements_for_user
        if session.get('admin_auth'):
            announcements = get_top_announcements_for_user(
                4, session.get('admin_code') or 'NET',
            )
            me = (session.get('admin_email') or '').lower().strip()
            if me:
                try:
                    from app.services.firebase_service import get_firestore_client
                    snap = (get_firestore_client()
                            .collection('announcement_reads').document(me).get())
                    if snap.exists:
                        last_read_at = (snap.to_dict() or {}).get('last_read_at')
                except Exception:
                    last_read_at = None
    except Exception:
        announcements = []
    return render_template(
        'main/index.html',
        announcements=announcements,
        last_read_at=last_read_at,
    )

@app.route('/health')
def health_check():
    """Cloud Run 헬스체크 — 인증 불필요"""
    return jsonify({'status': 'healthy'}), 200

@app.route('/eval')
def public_eval_form():
    """portal 로그인 사용자 → form.html (portal_me inject, 자동 역할 매칭).
    비로그인 → public_form.html (외부 평가자 passcode/OTP 흐름 유지)."""
    sess_emp = str(session.get('emp_id', '')).strip().lower()
    sess_email = str(session.get('admin_email', '')).strip().lower()
    if sess_emp or sess_email:
        from app.eval_v2.routes import build_portal_me
        return render_template('eval_v2/form.html', portal_me=build_portal_me())
    return render_template('eval_v2/public_form.html')

@app.route('/status')
def status_redirect():
    """/status 직접 접근 시 /eval-v2/status로 리디렉션 (비admin 은 access_denied 표시)"""
    return redirect('/eval-v2/status')

@app.route('/google0cfa68fe7a0154f4.html')
def google_verify():
    return render_template('main/google0cfa68fe7a0154f4.html')

from app.auth.routes import auth_bp
from app.eval_v2.routes import eval_v2_bp, eval_v2_api
from app.users.routes import users_bp
from app.retired.routes import retired_bp
from app.logs.routes import logs_bp
from app.legal.routes import legal_bp
from app.announcements.routes import announcements_bp
from app.notifications.api import notifications_bp
from app.admin.role_routes import admin_bp

app.register_blueprint(auth_bp)
app.register_blueprint(eval_v2_bp)
app.register_blueprint(eval_v2_api)
app.register_blueprint(users_bp)
app.register_blueprint(retired_bp)
app.register_blueprint(logs_bp)
app.register_blueprint(legal_bp)
app.register_blueprint(announcements_bp)
app.register_blueprint(notifications_bp)
app.register_blueprint(admin_bp)

if __name__ == '__main__':
    # 로컬 개발용 실행 경로 — Cloud Run 은 gunicorn 으로 구동되어 이 분기 무관.
    # Jinja 템플릿 캐시를 끄지 않으면 인라인 <style>/JS 편집이 서버 재시작 전까지 반영되지 않음.
    app.config['TEMPLATES_AUTO_RELOAD'] = True
    app.jinja_env.auto_reload = True
    app.run(debug=False, host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))
