"""Notifications API blueprint.

All endpoints require ``admin_auth`` session (enforced by global
``require_login_for_api`` in main.py — any /api/ path is auto-protected).

Status convention: ``'SUCCESS'`` on success (not ``'OK'`` — that is auth_bp only).
"""
import logging
from flask import Blueprint, jsonify, redirect, render_template, request, session

from app.extensions import limiter
from app.notifications.service import (
    get_notifications,
    get_unread_count,
    mark_read,
    mark_all_read,
    delete_old_notifications,
)

logger = logging.getLogger(__name__)

notifications_bp = Blueprint('notifications_bp', __name__)


# ── Page route ────────────────────────────────────────────────────────────

@notifications_bp.route('/notifications')
def notifications_page():
    if not session.get('admin_auth'):
        return redirect('/login')
    return render_template('notifications/list.html')


def _user_email():
    return (session.get('admin_email') or '').lower().strip()


def _rate_key():
    return _user_email() or request.remote_addr


# ── List recent notifications ──────────────────────────────────────────────

@notifications_bp.route('/api/notifications/list')
@limiter.limit('60 per minute', key_func=_rate_key)
def api_notifications_list():
    try:
        email = _user_email()
        if not email:
            return jsonify({'status': 'ERROR', 'message': 'Not authenticated.'}), 401
        try:
            limit = min(int(request.args.get('limit', 20)), 100)
        except (ValueError, TypeError):
            limit = 20
        cursor = (request.args.get('cursor') or '').strip() or None
        items, next_cursor = get_notifications(email, limit=limit, cursor=cursor)
        return jsonify({
            'status': 'SUCCESS',
            'notifications': items,
            'next_cursor': next_cursor,
            'has_more': next_cursor is not None,
        })
    except Exception:
        logger.exception('api_notifications_list error')
        return jsonify({'status': 'ERROR', 'message': 'An internal error occurred.'})


# ── Unread count ───────────────────────────────────────────────────────────

@notifications_bp.route('/api/notifications/unread-count')
@limiter.limit('120 per minute', key_func=_rate_key)
def api_notifications_unread_count():
    try:
        email = _user_email()
        if not email:
            return jsonify({'status': 'ERROR', 'message': 'Not authenticated.'}), 401
        count = get_unread_count(email)
        return jsonify({'status': 'SUCCESS', 'count': count})
    except Exception:
        logger.exception('api_notifications_unread_count error')
        return jsonify({'status': 'ERROR', 'message': 'An internal error occurred.'})


# ── Mark specific notifications as read ────────────────────────────────────

@notifications_bp.route('/api/notifications/mark-read', methods=['POST'])
@limiter.limit('60 per minute', key_func=_rate_key)
def api_notifications_mark_read():
    try:
        email = _user_email()
        if not email:
            return jsonify({'status': 'ERROR', 'message': 'Not authenticated.'}), 401
        data = request.get_json(silent=True) or {}
        ids = data.get('ids', [])
        if not isinstance(ids, list) or not ids:
            return jsonify({'status': 'ERROR', 'message': 'ids is required (list).'})
        if len(ids) > 100:
            return jsonify({'status': 'ERROR', 'message': 'Too many IDs (max 100).'})
        mark_read(ids, owner_email=email)
        return jsonify({'status': 'SUCCESS'})
    except Exception:
        logger.exception('api_notifications_mark_read error')
        return jsonify({'status': 'ERROR', 'message': 'An internal error occurred.'})


# ── Mark all as read ───────────────────────────────────────────────────────

@notifications_bp.route('/api/notifications/mark-all-read', methods=['POST'])
@limiter.limit('10 per minute', key_func=_rate_key)
def api_notifications_mark_all_read():
    try:
        email = _user_email()
        if not email:
            return jsonify({'status': 'ERROR', 'message': 'Not authenticated.'}), 401
        count = mark_all_read(email)
        return jsonify({'status': 'SUCCESS', 'marked': count})
    except Exception:
        logger.exception('api_notifications_mark_all_read error')
        return jsonify({'status': 'ERROR', 'message': 'An internal error occurred.'})


# ── Scheduled cleanup of old notifications ────────────────────────────────

@notifications_bp.route('/api/notifications/cleanup', methods=['POST'])
def api_notifications_cleanup():
    """Delete notifications older than 30 days.

    Auth: ``X-Sync-Secret`` header (Cloud Scheduler) or admin session.
    """
    import os
    import hmac
    secret = os.environ.get('NT_SYNC_SECRET', '')
    request_secret = request.headers.get('X-Sync-Secret', '')
    is_scheduler = bool(secret and request_secret and hmac.compare_digest(request_secret, secret))
    if not is_scheduler:
        if not session.get('admin_auth') or session.get('admin_code') not in ('admin', 'MASTER'):
            return jsonify({'status': 'ERROR', 'message': 'Unauthorized.'}), 401
    try:
        count = delete_old_notifications(days=30)
        logger.info('Notification cleanup: %d deleted', count)
        return jsonify({'status': 'SUCCESS', 'deleted': count})
    except Exception:
        logger.exception('api_notifications_cleanup error')
        return jsonify({'status': 'ERROR', 'message': 'An internal error occurred.'})


# ── User search (for @mention autocomplete) ────────────────────────────────

@notifications_bp.route('/api/users/search')
@limiter.limit('60 per minute', key_func=_rate_key)
def api_users_search():
    """Search portal_users by name/email prefix for @mention autocomplete."""
    try:
        email = _user_email()
        if not email:
            return jsonify({'status': 'ERROR', 'message': 'Not authenticated.'}), 401
        q = (request.args.get('q') or '').strip().lower()
        try:
            raw_limit = int(request.args.get('limit', 10))
        except (ValueError, TypeError):
            raw_limit = 10
        # Allow large limit for full-list modal (max 300); normal autocomplete capped at 20
        limit = min(raw_limit, 300) if raw_limit > 20 else min(raw_limit, 20)
        from app.services.user_service import get_all_users
        all_users = get_all_users()
        results = []
        for u in all_users:
            name = (u.get('name') or '').lower()
            uemail = (u.get('email') or '').lower()
            campus = (u.get('campus') or '').lower()
            role = (u.get('role') or '').lower()
            if role in ('retired', '퇴사'):
                continue
            if q and q not in name and q not in uemail and q not in campus and q not in role:
                continue
            results.append({
                'emp_id': u.get('emp_id', ''),
                'name': u.get('name', ''),
                'email': u.get('email', ''),
                'campus': u.get('campus', ''),
                'role': u.get('role', ''),
            })
            if len(results) >= limit:
                break
        return jsonify({'status': 'SUCCESS', 'users': results})
    except Exception:
        logger.exception('api_users_search error')
        return jsonify({'status': 'ERROR', 'message': 'An internal error occurred.'})
