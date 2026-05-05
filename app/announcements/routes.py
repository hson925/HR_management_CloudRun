"""공지사항(Announcements) 블루프린트.

Firestore 컬렉션: `announcements` / 서브컬렉션 `comments`
Firebase Storage 경로: `announcements/{post_id}/images|files/...`
권한: 역할 기반 읽기 (`allowed_roles` + `__all__` 센티넬), 작성/편집/삭제는 admin.
"""
import logging
from datetime import datetime, timezone

from flask import Blueprint, render_template, jsonify, session, abort
from google.cloud import firestore

from app.auth_utils import admin_required, _is_admin_session
from app.constants import ADMIN_ROLES
from app.extensions import limiter
from app.services.firebase_service import get_firestore_client
from app.utils.storage import is_storage_enabled
from app.utils.time_utils import kst_now_iso
from app.announcements.service import (
    _SELECTABLE_ROLES, _ALL_SENTINEL, _can_read, get_top_announcements_for_user,
)

logger = logging.getLogger(__name__)

announcements_bp = Blueprint('announcements', __name__)


# ── Flask-context helpers ─────────────────────────────────

def _user_rate_key():
    # Cloud Run 프록시 뒤 XFF-aware 클라이언트 IP 사용 (rate_limit.client_ip_key).
    from app.utils.rate_limit import client_ip_key
    return session.get('admin_email') or session.get('emp_id') or client_ip_key()


def _now_iso():
    return kst_now_iso()


def _session_role():
    return session.get('admin_code') or 'NET'


def _is_admin():
    return _is_admin_session()


# ── Page routes ───────────────────────────────────────────

@announcements_bp.route('/announcements')
def list_page():
    if not session.get('admin_auth'):
        return render_template('auth/access_denied.html',
                               user_name='', user_role='')
    return render_template('announcements/list.html',
                           is_admin=_is_admin())


@announcements_bp.route('/announcements/<post_id>')
def detail_page(post_id):
    if not session.get('admin_auth'):
        return render_template('auth/access_denied.html',
                               user_name='', user_role='')
    try:
        db = get_firestore_client()
        ref = db.collection('announcements').document(post_id)
        snap = ref.get()
        if not snap.exists:
            abort(404)
        data = snap.to_dict() or {}
        if data.get('status') != 'published' and not _is_admin():
            abort(404)
        if not _can_read(data, _session_role()):
            abort(404)
        data['id'] = post_id
        # Increment view count (fire-and-forget, don't block render)
        try:
            ref.update({'views': firestore.Increment(1)})
            data['views'] = (data.get('views') or 0) + 1
        except Exception:
            pass
        target_user_emails = {t.get('email','') for t in (data.get('target_users') or [])}
        user_email = (session.get('admin_email') or '').lower().strip()
        return render_template('announcements/detail.html',
                               post=data, is_admin=_is_admin(),
                               is_target_user=user_email in target_user_emails)
    except Exception:
        logger.exception('detail_page error id=%s', post_id)
        abort(500)


@announcements_bp.route('/admin/announcements/new')
@admin_required
def new_page():
    return render_template('announcements/editor.html',
                           post=None,
                           selectable_roles=_SELECTABLE_ROLES,
                           storage_enabled=is_storage_enabled())


@announcements_bp.route('/admin/announcements/<post_id>/edit')
@admin_required
def edit_page(post_id):
    try:
        db = get_firestore_client()
        snap = db.collection('announcements').document(post_id).get()
        if not snap.exists:
            abort(404)
        data = snap.to_dict() or {}
        data['id'] = post_id
        return render_template('announcements/editor.html',
                               post=data,
                               selectable_roles=_SELECTABLE_ROLES,
                               storage_enabled=is_storage_enabled())
    except Exception:
        logger.exception('edit_page error id=%s', post_id)
        abort(500)


# Re-export for backward compat (main.py imports this from here)
# get_top_announcements_for_user is already imported above from service.py

# Trigger API route registration (must be last — api.py imports from this module)
from app.announcements import api as _api  # noqa: F401
