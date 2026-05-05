import logging
import re
from flask import Blueprint, render_template, jsonify, request
from app.auth_utils import admin_required, api_admin_required
from app.services.firebase_service import get_firestore_client
from google.cloud.firestore_v1.base_query import FieldFilter

logger = logging.getLogger(__name__)

logs_bp = Blueprint('logs', __name__)

_VALID_CATEGORIES = {'all', 'auth', 'user', 'session', 'response', 'email', 'draft', 'general', 'announcement'}

# ISO 8601 date or datetime prefix (e.g. '2026-04-01' or '2026-04-01T00:00:00')
_ISO_TS_RE = re.compile(r'^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}(:\d{2}(\.\d+)?)?)?(Z|[+-]\d{2}:?\d{2})?$')


def _clean_ts(raw: str) -> str:
    """Validate ISO timestamp prefix. Return '' if malformed."""
    if not raw:
        return ''
    s = str(raw)[:40].strip()
    return s if _ISO_TS_RE.match(s) else ''


@logs_bp.route('/logs')
@admin_required
def logs_page():
    return render_template('logs/index.html')


@logs_bp.route('/api/logs/fetch')
@api_admin_required
def api_fetch_logs():
    """
    Query params:
    - category: 'all' | 'auth' | 'user' | 'session' | 'response' | 'email' | 'draft' | 'general'
    - limit: int (default 200, max 1000)
    - before: ISO timestamp string for cursor-based pagination
    - date_from: ISO datetime prefix string (e.g. '2026-04-01T00:00:00'), UTC
    - date_to:   ISO datetime prefix string (e.g. '2026-04-14T23:59:59'), UTC
    """
    try:
        category = request.args.get('category', 'all').strip()
        if category not in _VALID_CATEGORIES:
            category = 'all'

        try:
            limit = min(int(request.args.get('limit', 200)), 1000)
        except (ValueError, TypeError):
            limit = 200
        if limit <= 0:
            limit = 200

        before    = _clean_ts(request.args.get('before', ''))
        date_from = _clean_ts(request.args.get('date_from', ''))
        date_to   = _clean_ts(request.args.get('date_to', ''))

        db = get_firestore_client()
        query = db.collection('audit_logs').order_by('timestamp', direction='DESCENDING')

        if category != 'all':
            query = query.where(filter=FieldFilter('category', '==', category))

        if date_from:
            query = query.where(filter=FieldFilter('timestamp', '>=', date_from))
        if date_to:
            query = query.where(filter=FieldFilter('timestamp', '<=', date_to))

        if before:
            query = query.start_after({'timestamp': before})

        query = query.limit(limit)
        docs = query.stream()
        logs = [{'id': d.id, **d.to_dict()} for d in docs]

        return jsonify({'status': 'SUCCESS', 'logs': logs, 'count': len(logs)})
    except Exception:
        logger.exception('api_fetch_logs error')
        return jsonify({'status': 'ERROR', 'message': 'An internal error occurred.'})
