import logging
from datetime import datetime, timezone
from app.services.firebase_service import get_firestore_client

logger = logging.getLogger(__name__)


def log_audit(action: str, actor: str, target: str = '', details: dict = None, category: str = 'general'):
    """Firestore audit_logs 컬렉션에 민감한 작업 기록."""
    try:
        db = get_firestore_client()
        db.collection('audit_logs').add({
            'action': action,
            'actor': actor,
            'target': target,
            'details': details or {},
            'category': category,
            'timestamp': datetime.now(timezone.utc).isoformat(),
        })
    except Exception:
        logger.exception('audit_service.log_audit failed (action=%s, actor=%s)', action, actor)
