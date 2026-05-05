from functools import wraps
from flask import session, redirect, render_template, jsonify
from app.constants import ADMIN_ROLES


def _is_admin_session():
    return bool(session.get('admin_auth') and session.get('admin_code') in ADMIN_ROLES)


def admin_required(f):
    """Decorator: 비로그인 → /login, 로그인됐지만 admin 아님 → access_denied 페이지"""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('admin_auth'):
            return redirect('/login')
        if not _is_admin_session():
            return render_template('auth/access_denied.html',
                                   user_name=session.get('emp_name', ''),
                                   user_role=session.get('admin_code', ''))
        return f(*args, **kwargs)
    return decorated


def api_admin_required(f):
    """Decorator: API 라우트용 admin 권한 체크 → JSON 에러 반환"""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not _is_admin_session():
            return jsonify({'status': 'ERROR', 'message': 'Admin permission required.'}), 401
        return f(*args, **kwargs)
    return decorated


def role_required(*roles):
    """Decorator factory: 비로그인 → /login, 로그인됐지만 허용 role 아님 → access_denied.
    admin/MASTER도 허용하려면 명시적으로 포함해야 함 (자동 포함 아님)."""
    allowed = set(roles)
    def wrap(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if not session.get('admin_auth'):
                return redirect('/login')
            if session.get('admin_code') not in allowed:
                return render_template('auth/access_denied.html',
                                       user_name=session.get('emp_name', ''),
                                       user_role=session.get('admin_code', ''))
            return f(*args, **kwargs)
        return decorated
    return wrap


def api_role_required(*roles):
    """Decorator factory: API 라우트용 role 체크 → JSON 에러 반환."""
    allowed = set(roles)
    def wrap(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if not session.get('admin_auth'):
                return jsonify({'status': 'ERROR', 'message': 'Login required.'}), 401
            if session.get('admin_code') not in allowed:
                return jsonify({'status': 'ERROR', 'message': 'Permission denied.'}), 403
            return f(*args, **kwargs)
        return decorated
    return wrap

