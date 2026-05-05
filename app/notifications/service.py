"""Notification service — create / query / mark-read helpers.

Firestore collection: ``notifications``
Document schema:
    user_email  str   (indexed)
    type        str   announcement | evaluation | system | mention
    title       str
    message     str
    link        str   e.g. /announcements/abc123
    read        bool  default False
    created_at  str   ISO KST
"""
import logging
from datetime import datetime, timedelta

from app.services.firebase_service import get_firestore_client
from app.utils.time_utils import KST

logger = logging.getLogger(__name__)

_COLLECTION = 'notifications'
_BATCH_LIMIT = 400


def _kst_now():
    return datetime.now(KST).isoformat(timespec='seconds')


def create_notification(user_email, ntype, title, message, link=''):
    """Create a single notification document."""
    try:
        db = get_firestore_client()
        db.collection(_COLLECTION).add({
            'user_email': user_email.lower().strip(),
            'type': ntype,
            'title': title,
            'message': message,
            'link': link,
            'read': False,
            'created_at': _kst_now(),
        })
    except Exception:
        logger.exception('create_notification failed for %s', user_email)


def create_bulk_notifications(user_emails, ntype, title, message, link=''):
    """Batch-write notifications for multiple users (400-doc chunks)."""
    if not user_emails:
        return 0
    db = get_firestore_client()
    now = _kst_now()
    emails = list({e.lower().strip() for e in user_emails if e})
    total = 0
    for i in range(0, len(emails), _BATCH_LIMIT):
        batch = db.batch()
        chunk = emails[i:i + _BATCH_LIMIT]
        for email in chunk:
            ref = db.collection(_COLLECTION).document()
            batch.set(ref, {
                'user_email': email,
                'type': ntype,
                'title': title,
                'message': message,
                'link': link,
                'read': False,
                'created_at': now,
            })
        try:
            batch.commit()
            total += len(chunk)
        except Exception:
            logger.exception('create_bulk_notifications batch commit failed (offset %d)', i)
    return total


def get_notifications(user_email, limit=20, cursor=None):
    """Return notifications for a user, newest first.

    Args:
        cursor: ISO timestamp string — fetch items older than this value.

    Returns:
        ``(items, next_cursor)`` where *next_cursor* is the ``created_at`` of
        the last item when more results exist, or ``None``.
    """
    db = get_firestore_client()
    q = (db.collection(_COLLECTION)
         .where('user_email', '==', user_email.lower().strip())
         .order_by('created_at', direction='DESCENDING'))
    if cursor:
        q = q.start_after({'created_at': cursor})
    docs = list(q.limit(limit + 1).stream())
    has_more = len(docs) > limit
    items = []
    for d in docs[:limit]:
        data = d.to_dict()
        data['id'] = d.id
        items.append(data)
    next_cursor = items[-1]['created_at'] if has_more and items else None
    return items, next_cursor


def get_unread_count(user_email):
    """Return count of unread notifications."""
    db = get_firestore_client()
    query = (db.collection(_COLLECTION)
             .where('user_email', '==', user_email.lower().strip())
             .where('read', '==', False))
    try:
        agg = query.count().get()
        return agg[0][0].value
    except Exception:
        # Fallback: stream and count
        return sum(1 for _ in query.stream())


def mark_read(notification_ids, owner_email=None):
    """Mark specific notifications as read.

    If owner_email is provided, only notifications belonging to that user are
    marked — prevents one user from marking another user's notifications read.
    """
    if not notification_ids:
        return
    db = get_firestore_client()
    # Filter to only IDs that belong to owner_email (fetch in chunks of 30
    # to stay well under Firestore's 30-doc-per-get limit per call).
    if owner_email:
        owner = owner_email.lower().strip()
        verified_ids = []
        chunk_size = 30
        for i in range(0, len(notification_ids), chunk_size):
            chunk = notification_ids[i:i + chunk_size]
            try:
                docs = db.get_all([db.collection(_COLLECTION).document(nid) for nid in chunk])
                for doc in docs:
                    if doc and doc.exists and (doc.to_dict() or {}).get('user_email') == owner:
                        verified_ids.append(doc.id)
            except Exception:
                logger.exception('mark_read ownership check failed (offset %d)', i)
        ids_to_mark = verified_ids
    else:
        ids_to_mark = list(notification_ids)

    for i in range(0, len(ids_to_mark), _BATCH_LIMIT):
        batch = db.batch()
        for nid in ids_to_mark[i:i + _BATCH_LIMIT]:
            ref = db.collection(_COLLECTION).document(nid)
            batch.update(ref, {'read': True})
        try:
            batch.commit()
        except Exception:
            logger.exception('mark_read batch commit failed')


def delete_old_notifications(days=30):
    """Delete notifications older than ``days`` days. Returns count deleted."""
    cutoff = (datetime.now(KST) - timedelta(days=days)).isoformat(timespec='seconds')
    db = get_firestore_client()
    old_docs = list(
        db.collection(_COLLECTION)
        .where('created_at', '<', cutoff)
        .stream()
    )
    if not old_docs:
        return 0
    deleted = 0
    for i in range(0, len(old_docs), _BATCH_LIMIT):
        batch = db.batch()
        chunk = old_docs[i:i + _BATCH_LIMIT]
        for doc in chunk:
            batch.delete(doc.reference)
        try:
            batch.commit()
            deleted += len(chunk)
        except Exception:
            logger.exception('delete_old_notifications batch failed (offset %d)', i)
    return deleted


def mark_all_read(user_email):
    """Mark all notifications as read for a user."""
    db = get_firestore_client()
    docs = (db.collection(_COLLECTION)
            .where('user_email', '==', user_email.lower().strip())
            .where('read', '==', False)
            .stream())
    ids = [d.id for d in docs]
    if ids:
        # owner_email already verified via the query — no extra check needed
        mark_read(ids)
    return len(ids)
