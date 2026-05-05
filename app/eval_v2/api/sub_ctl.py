from flask import request, session
from app.eval_v2.blueprints import eval_v2_api
from app.auth_utils import api_admin_required
from app.eval_v2.api.common import kst_now, EMP_ID_RE
from app.extensions import cache, limiter
from app.utils.rate_limit import admin_rate_key
from app.constants import COL_EVAL_V2_SESSIONS
from app.utils.response import success, error
from app.services.firebase_service import (
    get_firestore_client,
    get_sub_ctl_assignments_map, set_sub_ctl_assignments, get_sub_ctl_history,
    get_session_sub_ctl_map, set_session_sub_ctl_assignments,
)

_VALID_CAMPUSES = (
    'CMA', 'CMB', 'CMC', 'CMD', 'CME', 'CMF',
    'CMG', 'CMH', 'CMI', 'CMJ', 'CMK', 'CML', 'CMM', 'SUB', '',
)


@eval_v2_api.route('/sub-ctl/list', methods=['GET'])
@api_admin_required
@limiter.limit("120 per minute", key_func=admin_rate_key)
def api_sub_ctl_list():
    try:
        session_id = request.args.get('session_id', '').strip()
        cache_key = f'sub_ctl_list_{session_id}' if session_id else 'sub_ctl_list'
        cached = cache.get(cache_key)
        if cached is not None:
            return success({'data': cached})
        db = get_firestore_client()
        sub_docs = db.collection('nt_sub').limit(1000).stream()
        teachers = []
        for doc in sub_docs:
            d = doc.to_dict()
            pos = d.get('position', '').upper()
            if pos in ('STL', 'TL'):
                continue
            teachers.append({
                'emp_id':     d.get('emp_id', doc.id),
                'name':       d.get('name', ''),
                'nickname':   d.get('nickname', ''),
                'position':   d.get('position', ''),
                'start_date': d.get('start_date', ''),
            })

        # Load assignments: session-specific if session_id provided, else default
        if session_id:
            session_map = get_session_sub_ctl_map(session_id)
            default_map = get_sub_ctl_assignments_map()
            for t in teachers:
                eid = t['emp_id'].lower()
                if eid in session_map:
                    t['assigned_campus'] = session_map[eid]
                    t['assigned_by'] = ''
                    t['assigned_at'] = ''
                else:
                    # Show default assignment as fallback (dimmed in UI)
                    t['assigned_campus'] = default_map.get(eid, '')
                    t['assigned_by'] = ''
                    t['assigned_at'] = ''
                    t['is_default'] = True
        else:
            assign_docs = db.collection('sub_ctl_assignments').limit(1000).stream()
            assignments = {}
            for doc in assign_docs:
                d = doc.to_dict()
                assignments[doc.id] = {
                    'campus':      d.get('campus', ''),
                    'assigned_by': d.get('assigned_by', ''),
                    'assigned_at': d.get('assigned_at', ''),
                }
            for t in teachers:
                a = assignments.get(t['emp_id'].lower(), {})
                t['assigned_campus'] = a.get('campus', '')
                t['assigned_by']     = a.get('assigned_by', '')
                t['assigned_at']     = a.get('assigned_at', '')

        teachers.sort(key=lambda x: x.get('name', ''))
        cache.set(cache_key, teachers, timeout=60)
        return success({'data': teachers})
    except Exception:
        return error('An internal error occurred.', 500)


@eval_v2_api.route('/sub-ctl/assign', methods=['POST'])
@api_admin_required
@limiter.limit("30 per minute", key_func=admin_rate_key)
def api_sub_ctl_assign():
    try:
        body = request.get_json(silent=True) or {}
        emp_ids = body.get('emp_ids', [])
        campus  = body.get('campus', '').strip()
        note    = body.get('note', '').strip()
        assign_session_id = body.get('session_id', '').strip()

        if not emp_ids:
            return error('emp_ids required.', 400)
        if campus not in _VALID_CAMPUSES:
            return error('Invalid campus code.', 400)

        db    = get_firestore_client()
        actor = session.get('admin_email', '') or session.get('emp_id', '')
        if not actor:
            return error('Could not identify actor for audit log.', 500)
        now   = kst_now()

        if assign_session_id:
            # Session-specific assignment
            assignments = [{'emp_id': str(eid).strip().lower(), 'campus': campus}
                           for eid in emp_ids if str(eid).strip()]
            ok, msg = set_session_sub_ctl_assignments(assign_session_id, assignments, actor, now)
            if ok:
                cache.delete(f'sub_ctl_list_{assign_session_id}')
                return success({'updated': len(assignments)})
            return error(msg, 500)
        else:
            # Default assignment (existing behavior)
            assignments_to_set = []
            for eid in emp_ids:
                eid = str(eid).strip().lower()
                if not eid:
                    continue
                cur = db.collection('sub_ctl_assignments').document(eid).get()
                prev_campus = cur.to_dict().get('campus', '') if cur.exists else ''
                assignments_to_set.append({
                    'emp_id': eid, 'campus': campus,
                    'prev_campus': prev_campus, 'note': note
                })
            ok, msg = set_sub_ctl_assignments(assignments_to_set, actor, now)
            if ok:
                from app.services.cache_service import invalidate_sub_ctl
                invalidate_sub_ctl()
                return success({'updated': len(assignments_to_set)})
            return error(msg, 500)
    except Exception:
        return error('An internal error occurred.', 500)


@eval_v2_api.route('/sub-ctl/sessions', methods=['GET'])
@api_admin_required
@limiter.limit("120 per minute", key_func=admin_rate_key)
def api_sub_ctl_sessions():
    """Return list of evaluation sessions for the session dropdown."""
    try:
        db = get_firestore_client()
        docs = (db.collection(COL_EVAL_V2_SESSIONS)
                .order_by('created_at', direction='DESCENDING')
                .limit(20)
                .stream())
        sessions = []
        for doc in docs:
            d = doc.to_dict()
            sessions.append({
                'id': doc.id,
                'label': d.get('label', ''),
                'status': d.get('status', ''),
            })
        return success({'sessions': sessions})
    except Exception:
        return error('An internal error occurred.', 500)


@eval_v2_api.route('/sub-ctl/history', methods=['GET'])
@api_admin_required
@limiter.limit("60 per minute", key_func=admin_rate_key)
def api_sub_ctl_history():
    try:
        emp_id = request.args.get('emp_id', '').strip().lower()
        if not emp_id:
            return error('emp_id required.', 400)
        if not EMP_ID_RE.match(emp_id):
            return error('Invalid emp_id format.', 400)
        history = get_sub_ctl_history(emp_id)
        return success({'data': history})
    except Exception:
        return error('An internal error occurred.', 500)
