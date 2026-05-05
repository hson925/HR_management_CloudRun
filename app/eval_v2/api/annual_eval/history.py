"""
app/eval_v2/api/annual_eval/history.py
Annual Eval History API — 교사별 연간 평가 타임라인
"""
import logging
from flask import request
from app.eval_v2.blueprints import eval_v2_api
from app.auth_utils import api_admin_required
from app.constants import COL_NHR_ANNUAL_EVAL
from app.utils.response import success, error
from app.services.firebase_service import get_firestore_client
from ._helpers import require_xhr, _EMP_ID_RE

logger = logging.getLogger(__name__)


@eval_v2_api.route('/annual-eval/history', methods=['POST'])
@api_admin_required
@require_xhr
def api_annual_eval_history():
    """
    특정 교사의 연간 평가 이력 (1차→2차→3차...) 타임라인.
    요청: {emp_id: str}
    """
    try:
        data   = request.get_json(silent=True) or {}
        emp_id = str(data.get('emp_id', '')).strip()
        if not emp_id:
            return error('emp_id required.', 400)
        if not _EMP_ID_RE.match(emp_id):
            return error('Invalid emp_id format.', 400)

        db = get_firestore_client()
        variants = list(dict.fromkeys([emp_id, emp_id.upper(), emp_id.lower()]))
        snaps = []
        for variant in variants:
            found = list(
                db.collection(COL_NHR_ANNUAL_EVAL)
                  .where('emp_id', '==', variant)
                  .stream()
            )
            if found:
                snaps = found
                break

        history = []
        for snap in sorted(snaps, key=lambda s: s.to_dict().get('eval_deadline', '')):
            r = snap.to_dict()
            history.append({
                'eval_deadline':   r.get('eval_deadline', ''),
                'eval_sequence':   r.get('eval_sequence'),
                'status':          r.get('status', 'not_started'),
                'composite_score': r.get('composite_score'),
                'reg_final_score': r.get('reg_final_score'),
                'obs_score':       r.get('obs_score'),
                'net_score':       r.get('net_score'),
                'base_current':    r.get('base_current', 0),
                'base_inc':        r.get('base_inc', 0),
                'total_current':   r.get('total_current', 0),
                'applied_total':   r.get('applied_total', 0),
                'report_url':      r.get('report_url', ''),
                'updated_at':      r.get('updated_at', ''),
            })

        return success({'history': history})
    except Exception:
        logger.exception('api_annual_eval_history error')
        return error('An internal error occurred.', 500)
