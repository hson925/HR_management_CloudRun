"""
app/eval_v2/api/annual_eval/records.py
Annual Eval Records API — 레코드 조회/저장/점수계산/목록
"""
import logging
from datetime import date as _date
from flask import request
from app.eval_v2.blueprints import eval_v2_api
from app.auth_utils import api_admin_required
from app.eval_v2.api.common import kst_now
from app.utils.time_utils import kst_date
from app.constants import (
    CAMPUS_KO_TO_CODE,
    COL_EVAL_V2_SESSIONS,
    COL_NHR_ANNUAL_EVAL, COL_NHR_ANNUAL_EVAL_CONFIG,
    ANNUAL_EVAL_GRACE_DAYS,
)
from app.utils.response import success, error
from app.services.firebase_service import get_firestore_client
from app.extensions import cache
from ._helpers import (
    require_xhr, _admin_email,
    _NT_COLLECTIONS, _EMP_ID_RE, _SESSION_ID_RE, _DATE_RE,
)
from .salary import _calc_eval_cycle, _get_nt_salary, _resolve_current_cycle
from .scoring import _calc_session_score, _calc_composite

logger = logging.getLogger(__name__)


@eval_v2_api.route('/annual-eval/record', methods=['POST'])
@api_admin_required
@require_xhr
def api_annual_eval_record():
    """
    {emp_id} → 현재 사이클 레코드 조회.
    eval_deadline은 nt_start_date 기반으로 자동 계산.
    레코드 없으면 NT Info에서 급여 초기값 로드 후 빈 레코드 반환 (저장 X).
    """
    try:
        data   = request.get_json(silent=True) or {}
        emp_id = str(data.get('emp_id', '')).strip()
        if not emp_id:
            return error('emp_id is required.', 400)
        if not _EMP_ID_RE.match(emp_id):
            return error('Invalid emp_id format.', 400)

        salary = _get_nt_salary(emp_id)
        nt_start = salary.get('nt_start_date', '')

        today_date = kst_date()
        db         = get_firestore_client()

        def _lookup(did):
            snap = db.collection(COL_NHR_ANNUAL_EVAL).document(did).get()
            return snap.to_dict() if snap.exists else None

        resolved = _resolve_current_cycle(
            emp_id, nt_start, today_date,
            _lookup, ANNUAL_EVAL_GRACE_DAYS,
        )
        if not resolved:
            logger.error('annual_eval_record: cannot compute deadline — emp_id=%s, nt_start=%r',
                         emp_id, nt_start)
            return error('Cannot compute eval deadline: invalid or missing start date.', 400)

        eval_deadline  = resolved['eval_deadline']
        eval_sequence  = resolved['eval_sequence']
        days_remaining = resolved['days_remaining']
        doc_id         = resolved['resolved_doc_id']
        existing       = resolved['resolved_record']

        if existing is not None:
            record = existing
            record['doc_id']          = doc_id
            record['eval_deadline']   = eval_deadline
            record['eval_sequence']   = eval_sequence
            record['days_remaining']  = days_remaining
            record['nt_name']         = salary['nt_name']
            record['nt_campus']       = salary['nt_campus']
            record['nt_position']     = salary['nt_position']
            record['nt_start_date']   = nt_start
            record['nt_nationality']  = salary['nt_nationality']
            record['nt_allowance_name'] = salary.get('allowance_name', '')
        else:
            record = {
                'doc_id':           doc_id,
                'emp_id':           emp_id,
                'eval_deadline':    eval_deadline,
                'eval_sequence':    eval_sequence,
                'days_remaining':   days_remaining,
                'eval_type':        '',
                'status':           'not_started',
                'session_1_id':     '',
                'session_2_id':     '',
                'reg_score_1':      None,
                'reg_score_2':      None,
                'reg_final_score':  None,
                'obs_score':        None,
                'obs_date':         '',
                'obs_rater':        '',
                'obs_link':         '',
                'obs_eng':          '',
                'net_score':        None,
                'net_date':         '',
                'net_rater':        '',
                'net_link':         '',
                'net_eng':          '',
                'composite_score':  None,
                'other_eng':        '',
                'allowance_comment': '',
                'base_current':     salary['base_current'],
                'pos_current':      salary['pos_current'],
                'role_current':     salary['role_current'],
                'housing_current':  salary['housing_current'],
                'total_current':    salary['total_current'],
                'base_inc':         0,
                'pos_inc':          0,
                'role_inc':         0,
                'housing_inc':      0,
                'applied_total':    salary['total_current'],
                'nt_name':          salary['nt_name'],
                'nt_campus':        salary['nt_campus'],
                'nt_position':      salary['nt_position'],
                'nt_start_date':    nt_start,
                'nt_nationality':   salary['nt_nationality'],
                'nt_allowance_name': salary.get('allowance_name', ''),
                '_is_new':          True,
            }

        # Grace period 메타 (프론트 배너·배지용)
        record['grace_active']    = resolved['grace_active']
        record['grace_days_left'] = resolved['grace_days_left']
        record['ideal_deadline']  = resolved['ideal_deadline']
        return success({'record': record})
    except Exception:
        logger.exception('api_annual_eval_record error')
        return error('An internal error occurred.', 500)


@eval_v2_api.route('/annual-eval/save', methods=['POST'])
@api_admin_required
@require_xhr
def api_annual_eval_save():
    """
    레코드 upsert. doc ID = {emp_id}__{eval_deadline}.
    composite_score는 reg_final_score / obs_score / net_score 변경 시 자동 재계산.
    """
    try:
        data          = request.get_json(silent=True) or {}
        emp_id        = str(data.get('emp_id', '')).strip()
        eval_deadline = str(data.get('eval_deadline', '')).strip()
        if not emp_id or not eval_deadline:
            return error('emp_id and eval_deadline are required.', 400)
        if not _EMP_ID_RE.match(emp_id):
            return error('Invalid emp_id format.', 400)
        if not _DATE_RE.match(eval_deadline):
            return error('eval_deadline must be YYYY-MM-DD format.', 400)

        db     = get_firestore_client()
        doc_id = f'{emp_id}__{eval_deadline}'

        _INT_FIELDS  = {'base_current', 'pos_current', 'role_current', 'housing_current',
                        'total_current', 'base_inc', 'pos_inc', 'role_inc', 'housing_inc',
                        'applied_total'}
        _INC_FIELDS  = {'base_inc', 'pos_inc', 'role_inc', 'housing_inc'}
        _FLOAT_FIELDS = {'obs_score', 'net_score', 'reg_score_1', 'reg_score_2',
                         'reg_final_score', 'composite_score'}
        _VALID_EVAL_TYPES = {'position', 'regular', 'tl', 'sub', 'stl', 'annual'}
        _STR_FIELDS  = {'status', 'session_1_id', 'session_2_id',
                        'obs_date', 'obs_link', 'obs_eng', 'obs_eng_ko', 'obs_rater',
                        'net_date', 'net_link', 'net_eng', 'net_eng_ko', 'net_rater',
                        'other_eng', 'other_eng_ko', 'allowance_comment',
                        'nt_name', 'nt_campus', 'nt_position', 'nt_start_date', 'nt_nationality'}

        payload = {'emp_id': emp_id, 'eval_deadline': eval_deadline}

        if 'eval_sequence' in data:
            try:
                seq = int(data['eval_sequence'])
                if seq > 0:
                    payload['eval_sequence'] = seq
            except (ValueError, TypeError):
                pass

        if 'eval_type' in data:
            et = str(data['eval_type']).strip().lower()
            if et and et not in _VALID_EVAL_TYPES:
                return error('Invalid eval_type.', 400)
            payload['eval_type'] = et

        _cfg_snap = db.collection(COL_NHR_ANNUAL_EVAL_CONFIG).document('settings').get()
        _cfg_data = _cfg_snap.to_dict() if _cfg_snap.exists else {}
        _allowed_raters: list[str] | None = _cfg_data.get('raters')
        _weights = _cfg_data.get('score_weights', {'reg_eval': 50, 'obs_eval': 30, 'net_eval': 20})

        for rater_field in ('obs_rater', 'net_rater'):
            if rater_field in data:
                rv = str(data[rater_field]).strip()[:100]
                if rv and _allowed_raters and rv not in _allowed_raters:
                    return error(f'{rater_field} is not in the configured raters list.', 400)
                payload[rater_field] = rv

        if 'allowance_comment' in data:
            payload['allowance_comment'] = str(data['allowance_comment']).strip()[:120]

        for f in _STR_FIELDS:
            if f in data:
                payload[f] = str(data[f]).strip()[:2000]
        _CURRENT_FIELDS = {'base_current', 'pos_current', 'role_current', 'housing_current',
                           'total_current', 'applied_total'}
        for f in _INT_FIELDS:
            if f in data:
                try:
                    val = int(data[f]) if data[f] is not None else 0
                    if f in _INC_FIELDS:
                        val = max(-99999, min(99999, val))
                    if f in _CURRENT_FIELDS:
                        val = max(0, val)
                    payload[f] = val
                except (ValueError, TypeError):
                    payload[f] = 0
        for f in _FLOAT_FIELDS:
            if f in data:
                try:
                    payload[f] = round(float(data[f]), 2) if data[f] is not None else None
                except (ValueError, TypeError):
                    payload[f] = None

        from google.cloud.firestore import transactional as _transactional

        @_transactional
        def _save_in_tx(tx):
            doc_ref = db.collection(COL_NHR_ANNUAL_EVAL).document(doc_id)
            existing_snap = doc_ref.get(transaction=tx)
            existing = existing_snap.to_dict() if existing_snap.exists else {}

            if 'reg_score_1' in payload or 'reg_score_2' in payload:
                s1 = payload.get('reg_score_1', existing.get('reg_score_1'))
                s2 = payload.get('reg_score_2', existing.get('reg_score_2'))
                vals = [v for v in [s1, s2] if v is not None and float(v) > 0]
                payload['reg_final_score'] = round(sum(vals) / len(vals), 2) if vals else None

            score_changed = any(f in payload for f in ('reg_final_score', 'obs_score', 'net_score'))
            if score_changed:
                merged = {**existing, **payload}
                payload['composite_score'] = _calc_composite(merged, _weights)

            payload['updated_at'] = kst_now()
            payload['updated_by'] = _admin_email()

            if not existing_snap.exists:
                payload['created_at'] = payload['updated_at']
                payload['created_by'] = payload['updated_by']

            tx.set(doc_ref, payload, merge=True)

        _save_in_tx(db.transaction())
        cache.delete('ae_list_base_data')
        return success({'doc_id': doc_id, 'composite_score': payload.get('composite_score')})
    except Exception:
        logger.exception('api_annual_eval_save error')
        return error('An internal error occurred.', 500)


@eval_v2_api.route('/annual-eval/calc-scores', methods=['POST'])
@api_admin_required
@require_xhr
def api_annual_eval_calc_scores():
    """
    eval_v2_responses에서 회차별 교사 점수 자동 계산.
    session_id == '__manual__' 이면 nhr_annual_eval doc 의 기존 reg_score_X 를 읽어 평균에 포함.
    test 세션 슬롯은 _calc_session_score 로 응답 기반 계산.
    반환: {score_1: float|None, score_2: float|None, reg_final: float|None}
    """
    try:
        data       = request.get_json(silent=True) or {}
        emp_id     = str(data.get('emp_id', '')).strip()
        eval_type  = str(data.get('eval_type', '')).strip().lower()
        session_1  = str(data.get('session_1_id', '')).strip()
        session_2  = str(data.get('session_2_id', '')).strip()
        eval_deadline = str(data.get('eval_deadline', '')).strip()

        if not emp_id:
            return error('emp_id is required.', 400)
        if not _EMP_ID_RE.match(emp_id):
            return error('Invalid emp_id format.', 400)
        if not eval_deadline or not _DATE_RE.match(eval_deadline):
            return error('eval_deadline is required (YYYY-MM-DD).', 400)
        for _sid in (session_1, session_2):
            if _sid and _sid != '__manual__' and not _SESSION_ID_RE.match(_sid):
                return error('Invalid session_id format.', 400)

        existing = {}
        if session_1 == '__manual__' or session_2 == '__manual__':
            db = get_firestore_client()
            doc_id = f'{emp_id}__{eval_deadline}'
            snap = db.collection(COL_NHR_ANNUAL_EVAL).document(doc_id).get()
            if snap.exists:
                existing = snap.to_dict() or {}

        def _slot_score(sid: str, slot_idx: int):
            if sid == '__manual__':
                v = existing.get(f'reg_score_{slot_idx}')
                try:
                    return float(v) if v is not None else None
                except (ValueError, TypeError):
                    return None
            return _calc_session_score(emp_id, eval_type, sid) if sid else None

        score_1 = _slot_score(session_1, 1)
        score_2 = _slot_score(session_2, 2)

        vals = [v for v in [score_1, score_2] if v is not None and v > 0]
        reg_final = round(sum(vals) / len(vals), 2) if vals else None

        return success({'score_1': score_1, 'score_2': score_2, 'reg_final': reg_final})
    except Exception:
        logger.exception('api_annual_eval_calc_scores error')
        return error('An internal error occurred.', 500)


@eval_v2_api.route('/annual-eval/list', methods=['POST'])
@api_admin_required
@require_xhr
def api_annual_eval_list():
    """
    입사일 기준으로 각 교사의 현재 평가 사이클 계산.
    파라미터: {search?, campus?, position?}
    반환: 데드라인 기준 오름차순 정렬된 교사 목록
    """
    try:
        data            = request.get_json(silent=True) or {}
        search          = str(data.get('search', '')).strip().lower()
        campus_filter   = str(data.get('campus', '')).strip()
        position_filter = str(data.get('position', '')).strip().lower()

        today_date = kst_date()

        db = get_firestore_client()

        cached = cache.get('ae_list_base_data')
        if cached:
            all_records, teachers = cached
        else:
            all_records: dict[str, dict] = {}
            for doc in db.collection(COL_NHR_ANNUAL_EVAL).limit(5000).stream():
                all_records[doc.id] = {**doc.to_dict(), 'doc_id': doc.id}

            teachers: dict[str, dict] = {}
            _seen_lower: set[str] = set()
            # _NT_COLLECTIONS 는 firebase_service.NT_COLLECTIONS_BY_PRIORITY alias — priority 순.
            # 첫 hit 유지로 중복 사번 발생 시 DYB > SUB > CREO 순으로 authoritative 선택.
            for col in _NT_COLLECTIONS:
                for doc in db.collection(col).limit(1000).stream():
                    emp_id = doc.id.strip()
                    if not emp_id or not _EMP_ID_RE.match(emp_id):
                        continue
                    if emp_id.lower() in _seen_lower:
                        continue
                    _seen_lower.add(emp_id.lower())
                    d = doc.to_dict()
                    raw_sd = d.get('start_date', '')
                    norm_sd = ''
                    if hasattr(raw_sd, 'year'):
                        try:
                            norm_sd = _date(raw_sd.year, raw_sd.month, raw_sd.day).isoformat()
                        except (ValueError, TypeError, AttributeError):
                            logger.warning('annual_eval_list: bad start_date object for %s: %r', emp_id, raw_sd)
                    elif raw_sd:
                        s = str(raw_sd).strip()[:10].replace('.', '-').replace('/', '-')
                        try:
                            _date.fromisoformat(s)
                            norm_sd = s
                        except (ValueError, TypeError):
                            logger.warning('annual_eval_list: bad start_date string for %s: %r', emp_id, raw_sd)
                    teachers[emp_id] = {
                        'emp_id':        emp_id,
                        'name':          d.get('name', ''),
                        'campus':        d.get('campus', ''),
                        'position':      d.get('position', ''),
                        'nt_start_date': norm_sd,
                    }
            # TTL 5분 — 데이터 변경은 save/report/bulk/NT-sync 핸들러들이 명시적으로
            # cache.delete 하므로 stale 위험 없음. roster_data(300s) 와 convention 일치.
            cache.set('ae_list_base_data', (all_records, teachers), timeout=300)

        result = []
        for emp_id, t in teachers.items():
            if campus_filter and t['campus'] != campus_filter:
                continue
            if position_filter and t['position'].strip().lower() != position_filter:
                continue
            if search:
                campus_code = CAMPUS_KO_TO_CODE.get(t['campus'], '')
                haystack = (t['name'] + ' ' + emp_id + ' ' + t['campus'] + ' ' + campus_code + ' ' + t['position']).lower()
                if search not in haystack:
                    continue

            if not t['nt_start_date']:
                logger.warning('annual_eval_list: skipping %s — no start_date', emp_id)
                continue

            resolved = _resolve_current_cycle(
                emp_id, t['nt_start_date'], today_date,
                all_records.get, ANNUAL_EVAL_GRACE_DAYS,
            )
            if not resolved:
                logger.warning('annual_eval_list: skipping %s — unparseable start_date=%r',
                               emp_id, t['nt_start_date'])
                continue

            result.append({
                'emp_id':          emp_id,
                'name':            t['name'],
                'campus':          t['campus'],
                'position':        t['position'],
                'nt_start_date':   t['nt_start_date'],
                'eval_deadline':   resolved['eval_deadline'],
                'eval_sequence':   resolved['eval_sequence'],
                'days_remaining':  resolved['days_remaining'],
                'grace_active':    resolved['grace_active'],
                'grace_days_left': resolved['grace_days_left'],
                'ideal_deadline':  resolved['ideal_deadline'],
                'record':          resolved['resolved_record'],
            })

        result.sort(key=lambda x: x['days_remaining'] if x['days_remaining'] is not None else 9999)

        return success({'teachers': result})
    except Exception:
        logger.exception('api_annual_eval_list error')
        return error('An internal error occurred.', 500)
