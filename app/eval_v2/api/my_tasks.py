"""My Tasks — GS/TL/STL/admin 의 평가 진입 dashboard.

엔드포인트:
- POST /api/v2/my-tasks/sessions — 본인 portal_role 이 매핑된 rater role 을 보유한 활성 세션 목록
- POST /api/v2/my-tasks/list      — 세션의 평가 대상 + 본인 제출 여부

설계:
- 평가자 ↔ 피평가자 매핑은 portal_role_mappings (eval_v2_config 의 questions roles[*] 신규 필드) 가 권위
- 평가 대상 풀은 roster (NT Info 시트) + 본인 campus 필터
- 본인 제출 여부 = eval_v2_responses 에서 (emp_id, rater_emp_id) 또는 (emp_id, rater_role, normalized rater_name) 매칭
  · rater_emp_id 는 Phase E 에서 점진 도입 — 전환 기간엔 fallback 으로 rater_name 매칭 유지

Admin view-as 모드:
- as_campus / as_role param (admin/MASTER 만 허용) → 다른 portal_role/campus 시점으로 미리 보기
- audit_logs 카테고리 'eval' / action 'eval_my_tasks_viewas'
"""

import logging
import re as _re
import time as _time
from flask import request, session

# Firestore document id 화이트리스트 — UUID4 + 일반 alphanumeric_hyphen 허용.
# slash / null / path traversal 차단 (Firestore .document() 가 slash 를 path 구분자로 해석).
DOC_ID_RE = _re.compile(r'^[a-zA-Z0-9_\-]{1,80}$')

from app.eval_v2.blueprints import eval_v2_api
from app.auth_utils import api_role_required
from app.extensions import limiter
from app.eval_v2.api.common import (
    EMP_ID_RE, SESSION_ID_RE, get_questions,
)
from app.eval_v2.questions import EVAL_TYPE_LABELS
from app.utils.time_utils import kst_today
from app.utils.response import success, error
from app.utils.rate_limit import admin_rate_key
from app.constants import (
    COL_EVAL_V2_SESSIONS, COL_EVAL_V2_RESPONSES,
    ADMIN_ROLES, CAMPUS_ORDER,
)
from app.services.firebase_service import get_firestore_client
from app.services.user_service import get_user_by_emp_id, get_user_by_email
from app.services.roster_cache_service import get_roster
from app.services.audit_service import log_audit
from app.services.report_service import select_effective_responses, _normalize_rater_name

logger = logging.getLogger(__name__)

# view-as 화이트리스트 — Firestore portal_roles 기반 lazy lookup (custom role 포함, retired 제외).
# 60초 cache TTL — admin 이 추가한 새 role 즉시 반영.
def _get_viewas_valid_roles() -> set:
    from app.services import role_service
    return set(role_service.get_role_names_excluding_retired())

_VIEWAS_VALID_CAMPUSES = frozenset(CAMPUS_ORDER)

# admin view-as audit log 스팸 방지 — 동일 (admin, target) 5분 cooldown.
# Cloud Run worker=1 전제. multi-worker 시 Flask-Caching 으로 전환.
_VIEWAS_COOLDOWN_SECONDS = 300
_viewas_last_logged: dict = {}


def _should_log_viewas(actor: str, target: str) -> bool:
    if not actor or not target:
        return True
    now = _time.time()
    key = (actor, target)
    last = _viewas_last_logged.get(key)
    if last is not None and (now - last) < _VIEWAS_COOLDOWN_SECONDS:
        return False
    _viewas_last_logged[key] = now
    if len(_viewas_last_logged) > 1000:
        sorted_keys = sorted(_viewas_last_logged, key=_viewas_last_logged.get)
        for k in sorted_keys[:200]:
            _viewas_last_logged.pop(k, None)
    return True


def _resolve_me():
    """현재 로그인 사용자의 portal_users doc 조회 (emp_id 우선, email fallback).
    반환: (me_doc_dict_or_empty, my_emp_id, my_role, my_campus, my_name).
    """
    sess_emp = str(session.get('emp_id', '')).strip().lower()
    me = get_user_by_emp_id(sess_emp) if sess_emp else None
    if not me:
        sess_email = str(session.get('admin_email', '')).strip().lower()
        if sess_email:
            me = get_user_by_email(sess_email)
    me = me or {}
    return (
        me,
        (me.get('emp_id') or sess_emp or '').strip().lower(),
        (me.get('role') or session.get('admin_code', '')).strip(),
        (me.get('campus') or session.get('campus', '')).strip(),
        (me.get('name') or session.get('name', '')).strip(),
    )


def _is_admin():
    return session.get('admin_code', '') in ADMIN_ROLES


def _resolve_view_context(body):
    """as_campus / as_role 가 admin/MASTER 일 때만 적용. 나머지는 본인 정보.
    반환: (effective_role, effective_campus, my_emp_id, viewing_as, viewas_error).
    viewing_as: True 면 admin 이 view-as 모드로 진입.
    viewas_error: 화이트리스트 위반 시 에러 메시지 (None 이면 정상).
    """
    me, my_emp_id, my_role, my_campus, _my_name = _resolve_me()
    as_campus = str(body.get('as_campus', '') or '').strip()
    as_role = str(body.get('as_role', '') or '').strip()
    if _is_admin() and (as_campus or as_role):
        # 화이트리스트 검증 — admin 이라도 임의 문자열 차단 (audit pollution 방지)
        if as_role and as_role not in _get_viewas_valid_roles():
            return (my_role, my_campus, my_emp_id, False, 'Invalid as_role.')
        if as_campus and as_campus not in _VIEWAS_VALID_CAMPUSES:
            return (my_role, my_campus, my_emp_id, False, 'Invalid as_campus.')
        eff_role = as_role or my_role
        eff_campus = as_campus or my_campus
        # 5분 cooldown 으로 audit 스팸 방지 (카드 클릭마다 발생하지 않게)
        actor = session.get('admin_email', '')
        target = f'{eff_role}@{eff_campus}'
        if _should_log_viewas(actor, target):
            try:
                log_audit(
                    'eval_my_tasks_viewas',
                    actor=actor,
                    target=target,
                    category='eval',
                )
            except Exception:
                logger.debug('audit log failed', exc_info=True)
        return (eff_role, eff_campus, my_emp_id, True, None)
    return (my_role, my_campus, my_emp_id, False, None)


def _roles_with_my_mapping(eval_type, my_role, session_snapshot=None):
    """주어진 eval_type 의 questions roles 중 portal_role_mappings 에 my_role 포함하는 것만.

    매핑은 **항상 글로벌 config 가 권위** — 세션 snapshot 은 questions/weights 잠금
    용도일 뿐 mapping 은 admin 이 언제든 변경 가능해야 활성 세션에도 즉시 반영됨.

    role.name 식별은 snapshot 우선 (세션 생성 후 admin 이 role 추가/삭제해도 그 세션의
    "유효 role 집합" 은 snapshot 기준), 매핑은 글로벌 config 의 같은 role.name 에서 룩업.
    글로벌에 그 role 이 없으면 snapshot 의 mapping 으로 fallback.
    """
    snap_roles = None
    if session_snapshot:
        snap_roles = (session_snapshot.get(eval_type) or {}).get('questions') or None
    global_roles = get_questions(eval_type)
    # 글로벌 role.name → mapping 매핑 dict
    global_mapping = {}
    for r in (global_roles or []):
        if not isinstance(r, dict):
            continue
        nm = r.get('name') or r.get('role', '')
        if nm:
            global_mapping[nm] = r.get('portal_role_mappings') or []
    # 유효 role 집합: snapshot 우선, 없으면 글로벌
    base_roles = snap_roles if snap_roles else global_roles
    matched = []
    for r in (base_roles or []):
        if not isinstance(r, dict):
            continue
        name = r.get('name') or r.get('role', '')
        if not name:
            continue
        # 글로벌 mapping 우선, 없으면 snapshot 자체의 mapping
        mappings = global_mapping.get(name)
        if mappings is None:
            mappings = r.get('portal_role_mappings') or []
        if not isinstance(mappings, list):
            continue
        if my_role in mappings:
            matched.append({
                'role': name,
                'label': r.get('label_ko') or r.get('label') or name,
                'min_count': r.get('min_count', 1),
            })
    return matched


@eval_v2_api.route('/my-tasks/sessions', methods=['POST'])
@api_role_required('admin', 'MASTER', 'GS', 'TL', 'STL')
@limiter.limit('60 per minute', key_func=admin_rate_key)
def api_my_tasks_sessions():
    """본인 portal_role 이 매핑된 rater role 을 보유한 활성 세션 목록."""
    try:
        body = request.get_json(silent=True) or {}
        eff_role, eff_campus, my_emp_id, viewing_as, viewas_err = _resolve_view_context(body)
        if viewas_err:
            return error(viewas_err, 400)
        if not eff_role:
            return error('Your portal role is not set. Contact admin.', 403, code='NO_ROLE')

        db = get_firestore_client()
        today_str = kst_today()
        active_docs = list(db.collection(COL_EVAL_V2_SESSIONS).where('status', '==', 'active').stream())
        sessions_out = []
        # 디버그 카운터 — 세션 0건 진단용. UI 에서 admin 에게 표시.
        dbg = {
            'active_total': len(active_docs),
            'in_period': 0,
            'matched': 0,
            'eval_types_seen': set(),
            'global_mappings_per_type': {},
        }
        for d in active_docs:
            data = d.to_dict() or {}
            start_date = data.get('start_date', '')
            end_date = data.get('end_date', '')
            # 기간 내 세션만 (pre-start / post-end 제외)
            if start_date and start_date > today_str:
                continue
            if end_date and end_date < today_str:
                continue
            dbg['in_period'] += 1
            # 세션의 eval_types 는 questions_snapshot 의 keys 가 권위 (sessions.py 가
            # 세션 생성 시 DEFAULT_QUESTIONS.keys() 를 모두 snapshot 함). 별도 eval_types
            # 필드는 schema 에 없음.
            snapshot = data.get('questions_snapshot') or {}
            eval_types_in_session = list(snapshot.keys()) if isinstance(snapshot, dict) else []
            if not eval_types_in_session:
                # fallback: 글로벌 DEFAULT_QUESTIONS (snapshot 손상/누락)
                from app.eval_v2.questions import DEFAULT_QUESTIONS as _DQ
                eval_types_in_session = list(_DQ.keys())
            my_rater_per_type = {}
            for et in eval_types_in_session:
                et_str = str(et).strip().lower()
                if not et_str:
                    continue
                dbg['eval_types_seen'].add(et_str)
                # 글로벌 매핑 진단 — 어떤 role 들이 어떤 portal_role 에 매핑돼 있는지.
                if et_str not in dbg['global_mappings_per_type']:
                    g_roles = get_questions(et_str) or []
                    dbg['global_mappings_per_type'][et_str] = [
                        {'role': r.get('name') or r.get('role', ''),
                         'mappings': r.get('portal_role_mappings') or []}
                        for r in g_roles if isinstance(r, dict)
                    ]
                matched_roles = _roles_with_my_mapping(et_str, eff_role, session_snapshot=snapshot)
                if matched_roles:
                    my_rater_per_type[et_str] = {
                        'eval_type_label': EVAL_TYPE_LABELS.get(et_str, et_str.upper()),
                        'rater_roles': matched_roles,
                    }
            if not my_rater_per_type:
                continue
            dbg['matched'] += 1
            sessions_out.append({
                'id': d.id,
                'label': data.get('label', ''),
                'start_date': start_date,
                'end_date': end_date,
                'passcode_enabled': bool(data.get('passcode_hash')),
                'eval_types': list(my_rater_per_type.keys()),
                'my_rater_per_type': my_rater_per_type,
            })

        return success({
            'data': {
                'me': {
                    'emp_id': my_emp_id,
                    'role': eff_role,
                    'campus': eff_campus,
                    'is_admin': _is_admin(),
                    'viewing_as': viewing_as,
                },
                'sessions': sessions_out,
                'debug': {
                    'active_total': dbg['active_total'],
                    'in_period': dbg['in_period'],
                    'matched': dbg['matched'],
                    'eval_types_seen': sorted(list(dbg['eval_types_seen'])),
                    'global_mappings_per_type': dbg['global_mappings_per_type'],
                    'today_kst': today_str,
                },
            }
        })
    except Exception:
        logger.exception('api_my_tasks_sessions error')
        return error('An internal error occurred.', 500)


def _build_my_submission_map(session_id, my_emp_id, my_normalized_name):
    """본인이 이 세션에서 이미 제출한 (emp_id, rater_role) → doc_id 매핑.
    L-3: 수정 모드 진입 시 클라가 doc_id 를 알아야 form 페이지로 editDocId param 전달.

    매핑 정책:
    - **rater_emp_id == my_emp_id 매칭** (Phase E 후 정확): doc_id 채움 → 카드 클릭 → form 진입 → 수정 가능
    - **fallback (rater_name 매칭)**: legacy 응답 (rater_emp_id 빈문자). doc_id 도 채움 →
      `_verify_doc_owner` 가 fallback 까지 허용 (admin update-eval 권한 수준). 동명이인 위험은
      portal_users.name 정확도에 의존.
    """
    if not session_id or not (my_emp_id or my_normalized_name):
        return {}
    db = get_firestore_client()
    q = db.collection(COL_EVAL_V2_RESPONSES).where('session_id', '==', session_id)
    raw = []
    for doc in q.limit(20000).stream():
        d = doc.to_dict() or {}
        d['_doc_id'] = doc.id  # select_effective_responses 가 dict 그대로 보존 → 후속 사용 가능
        raw.append(d)
    eff = select_effective_responses(raw)
    out = {}
    for d in eff:
        eid = (d.get('emp_id') or '').strip().lower()
        role = (d.get('rater_role') or '').strip()
        if not eid or not role:
            continue
        doc_id = d.get('_doc_id', '')
        rater_eid = (d.get('rater_emp_id') or '').strip().lower()
        if rater_eid:
            if rater_eid == my_emp_id:
                out[(eid, role)] = doc_id
            continue
        # fallback: normalized rater_name (legacy 응답 — 수정 가능, _verify_doc_owner fallback 허용)
        rname_norm = _normalize_rater_name(d.get('rater_name', ''))
        if rname_norm and rname_norm == my_normalized_name:
            out[(eid, role)] = doc_id  # 본인 응답으로 간주 (동명이인 위험 = portal_users.name 정확도)
    return out


# 호환성 유지: 기존 _build_my_submission_set 호출 코드가 set 만 보면 됨.
def _build_my_submission_set(session_id, my_emp_id, my_normalized_name):
    return set(_build_my_submission_map(session_id, my_emp_id, my_normalized_name).keys())


@eval_v2_api.route('/my-tasks/list', methods=['POST'])
@api_role_required('admin', 'MASTER', 'GS', 'TL', 'STL')
@limiter.limit('60 per minute', key_func=admin_rate_key)
def api_my_tasks_list():
    """세션의 평가 대상 + 본인 제출 여부 (campus 필터 적용).
    body: {session_id, eval_type?, as_campus?, as_role?}
    """
    try:
        body = request.get_json(silent=True) or {}
        session_id = str(body.get('session_id', '')).strip()
        if not session_id or not SESSION_ID_RE.match(session_id):
            return error('Invalid session_id.', 400)

        eff_role, eff_campus, my_emp_id, viewing_as, viewas_err = _resolve_view_context(body)
        if viewas_err:
            return error(viewas_err, 400)
        if not eff_role:
            return error('Your portal role is not set. Contact admin.', 403, code='NO_ROLE')

        # 세션 조회 + 권한 검증 (이 세션이 사용자 매핑된 rater role 을 가지고 있는지)
        db = get_firestore_client()
        sess_doc = db.collection(COL_EVAL_V2_SESSIONS).document(session_id).get()
        if not sess_doc.exists:
            return error('Session not found.', 404)
        sess_data = sess_doc.to_dict() or {}
        if sess_data.get('status') != 'active':
            return error('Session is not active.', 400)

        # 세션의 eval_types — questions_snapshot keys 가 권위 (sessions.py 가 모든
        # DEFAULT_QUESTIONS 를 snapshot). 별도 eval_types 필드는 schema 에 없음.
        snap_keys = sess_data.get('questions_snapshot') or {}
        eval_types_in_session = list(snap_keys.keys()) if isinstance(snap_keys, dict) else []
        if not eval_types_in_session:
            from app.eval_v2.questions import DEFAULT_QUESTIONS as _DQ
            eval_types_in_session = list(_DQ.keys())

        # eval_type 필터: 사용자가 특정 eval_type 의 리스트만 보고 싶을 때.
        wanted_eval_type = str(body.get('eval_type', '') or '').strip().lower()
        if wanted_eval_type and wanted_eval_type not in eval_types_in_session:
            return error('eval_type not in this session.', 400)

        snapshot = sess_data.get('questions_snapshot') or {}
        # 사용자가 본 세션에서 평가 가능한 rater role 들을 eval_type 별로 묶어서 응답.
        per_type_info = {}
        for et in eval_types_in_session:
            et_str = str(et).strip().lower()
            if not et_str:
                continue
            if wanted_eval_type and et_str != wanted_eval_type:
                continue
            matched_roles = _roles_with_my_mapping(et_str, eff_role, session_snapshot=snapshot)
            if matched_roles:
                per_type_info[et_str] = matched_roles

        if not per_type_info:
            return error('You have no assigned rater role for this session.', 403, code='NO_RATER_ROLE')

        # 본인 제출 여부 룩업 (해당 세션 전체 — eval_type 무관, 모든 rater role 동시 검사)
        my_normalized_name = _normalize_rater_name(
            (_resolve_me()[4] if not viewing_as else '')  # view-as 모드에선 fallback 비활성 (admin 본인 이름과 다른 사람의 제출이 섞이지 않도록)
        )
        # L-3: set 대신 map 사용 → done role 별 doc_id 도 응답에 포함 (수정 모드 진입용)
        submitted_map = _build_my_submission_map(session_id, my_emp_id, my_normalized_name)

        # roster 에서 평가 대상 추출. campus 필터 + eval_type 필터.
        teachers_out = []
        for row in get_roster():
            if len(row) < 4:
                continue
            eid = str(row[2]).strip().lower()
            etype = str(row[3]).strip().lower()
            if eid in ('사번', ''):
                continue
            if etype not in per_type_info:
                continue
            campus = str(row[4]).strip() if len(row) > 4 else ''
            # campus 필터: admin 의 view-as 또는 기본 본인 campus
            if eff_campus and campus != eff_campus:
                continue
            # 본인 매핑된 rater role 들 중 어느 하나라도 미제출이면 클릭 가능
            my_rater_roles = [r['role'] for r in per_type_info[etype]]
            # 표시용 label 병행 — frontend chip 이 raw role 대신 label 사용 (eval admin_config label_ko).
            my_rater_role_labels = {r['role']: r.get('label') or r['role'] for r in per_type_info[etype]}
            done_roles = [r for r in my_rater_roles if (eid, r) in submitted_map]
            done_role_doc_ids = {r: submitted_map[(eid, r)] for r in done_roles if submitted_map.get((eid, r))}
            all_done = (len(done_roles) == len(my_rater_roles)) if my_rater_roles else False
            teachers_out.append({
                'emp_id': eid,
                'name': row[1] if len(row) > 1 else '',
                'campus': campus,
                'eval_type': etype,
                'eval_type_label': EVAL_TYPE_LABELS.get(etype, etype.upper()),
                'my_rater_roles': my_rater_roles,
                'my_rater_role_labels': my_rater_role_labels,  # role name → label 매핑 (chip 표시용)
                'done_roles': done_roles,
                'done_role_doc_ids': done_role_doc_ids,  # L-3: 수정 모드 진입용
                'all_done': all_done,
            })

        # 기본 정렬: 미제출 우선, 그 다음 이름
        teachers_out.sort(key=lambda t: (t['all_done'], t.get('name', '')))

        return success({
            'data': {
                'session': {
                    'id': session_id,
                    'label': sess_data.get('label', ''),
                    'start_date': sess_data.get('start_date', ''),
                    'end_date': sess_data.get('end_date', ''),
                    'eval_types': list(per_type_info.keys()),
                },
                'me': {
                    'emp_id': my_emp_id,
                    'role': eff_role,
                    'campus': eff_campus,
                    'is_admin': _is_admin(),
                    'viewing_as': viewing_as,
                },
                'per_type_info': per_type_info,
                'teachers': teachers_out,
                'debug_self_match': {
                    'my_emp_id': my_emp_id,
                    'matched_by_rater_emp_id': sum(1 for v in submitted_map.values() if v),
                    'matched_by_name_fallback_legacy': sum(1 for v in submitted_map.values() if not v),
                    'total_pairs_in_map': len(submitted_map),
                },
            }
        })
    except Exception:
        logger.exception('api_my_tasks_list error')
        return error('An internal error occurred.', 500)


# ── L-1/L-2: 본인 평가 수정 (GS/TL/STL/admin 본인 응답 in-place 수정) ────────

def _resolve_my_emp_id():
    """본인 portal_users.emp_id 반환. 없으면 빈문자."""
    sess_emp = str(session.get('emp_id', '')).strip().lower()
    if sess_emp:
        return sess_emp
    sess_email = str(session.get('admin_email', '')).strip().lower()
    if sess_email:
        me = get_user_by_email(sess_email)
        if me:
            return (me.get('emp_id') or '').strip().lower()
    return ''


def _verify_doc_owner(doc_data, my_emp_id, my_normalized_name=''):
    """doc 가 본인 응답인지 검증.
    1차: rater_emp_id == my_emp_id (Phase E 이후 정확)
    2차: legacy 응답 (rater_emp_id 빈문자) 인 경우 normalized rater_name 매칭 — admin update-eval 권한 수준.
       동명이인 위험은 portal_users.name 정확도에 의존. portal 로그인 사용자의 본인 정확 매칭만 허용.
    반환: (matched: bool, via: str) — via 는 'rater_emp_id' / 'name_fallback' / ''
    """
    if not (my_emp_id or my_normalized_name):
        return (False, '')
    rater_eid = (doc_data.get('rater_emp_id') or '').strip().lower()
    if rater_eid:
        return (bool(my_emp_id) and rater_eid == my_emp_id, 'rater_emp_id')
    # legacy fallback — rater_emp_id 빈문자 + name 정확 매칭. 동명이인 위험 추적용 audit.
    if my_normalized_name:
        rname_norm = _normalize_rater_name(doc_data.get('rater_name', ''))
        if rname_norm and rname_norm == my_normalized_name:
            return (True, 'name_fallback')
    return (False, '')


@eval_v2_api.route('/my-tasks/get-my-response', methods=['POST'])
@api_role_required('admin', 'MASTER', 'GS', 'TL', 'STL')
@limiter.limit('60 per minute', key_func=admin_rate_key)
def api_my_tasks_get_my_response():
    """본인이 제출한 응답 1건 조회 — form 수정 모드 미리 채움용.
    body: {docId} (URL ?editDocId 에서 클라가 직접 전달).
    검증:
    1. docId 형식 (UUID 또는 alphanumeric_hyphen)
    2. doc 의 rater_emp_id == 본인 emp_id (rater_emp_id 빈문자 = legacy → 거절)
    응답: {scores, comment_en, comment_ko, open_answers, version, rater_role, rater_name, session_id, eval_type}
    """
    try:
        body = request.get_json(silent=True) or {}
        doc_id = str(body.get('docId', '')).strip()
        if not doc_id or not DOC_ID_RE.match(doc_id):
            return error('Invalid docId.', 400)
        my_emp_id = _resolve_my_emp_id()
        my_name = (_resolve_me()[4] or '').strip()
        my_normalized_name = _normalize_rater_name(my_name)
        if not my_emp_id and not my_normalized_name:
            return error('Your portal account has no emp_id or name. Contact admin.', 403, code='NO_IDENTITY')

        db = get_firestore_client()
        ref = db.collection(COL_EVAL_V2_RESPONSES).document(doc_id)
        snap = ref.get()
        if not snap.exists:
            return error('Response not found.', 404)
        doc_data = snap.to_dict() or {}
        owner_ok, via = _verify_doc_owner(doc_data, my_emp_id, my_normalized_name)
        if not owner_ok:
            return error('You can only edit your own responses.', 403, code='NOT_OWNER')
        # H 보강: legacy fallback (name 매칭) 경로 audit 기록 — 동명이인 위험 추적
        if via == 'name_fallback':
            try:
                log_audit('eval_self_owner_check_fallback',
                          actor=session.get('admin_email', '') or my_emp_id,
                          target=doc_id,
                          details={'via': 'name_fallback', 'op': 'get'},
                          category='response')
            except Exception:
                logger.debug('audit log failed for name_fallback', exc_info=True)

        return success({
            'data': {
                'doc_id': doc_id,
                'session_id': doc_data.get('session_id', ''),
                'eval_type': doc_data.get('eval_type', ''),
                'rater_role': doc_data.get('rater_role', ''),
                'rater_name': doc_data.get('rater_name', ''),
                'scores': doc_data.get('scores', {}),
                'comment_en': doc_data.get('comment_en', ''),
                'comment_ko': doc_data.get('comment_ko', ''),
                'open_answers': doc_data.get('open_answers', {}),
                'version': int(doc_data.get('version') or 0),
                'emp_id': doc_data.get('emp_id', ''),  # 피평가자 사번
            }
        })
    except Exception:
        logger.exception('api_my_tasks_get_my_response error')
        return error('An internal error occurred.', 500)


@eval_v2_api.route('/my-tasks/update-my-eval', methods=['POST'])
@api_role_required('admin', 'MASTER', 'GS', 'TL', 'STL')
@limiter.limit('30 per minute', key_func=admin_rate_key)
def api_my_tasks_update_my_eval():
    """본인이 제출한 응답 in-place 수정.
    body: {docId, scores, commentEn, openAnswers, version}
    검증:
    1. doc 의 rater_emp_id == session.emp_id (본인 doc 만 허용)
    2. doc 의 session_id 가 status='active' + 기간 내 (closed 세션 수정 차단)
    3. rater_role / rater_name / is_manual 변경 안 함 (in-place 정책)
    4. commentEn 변경 → commentKo 빈문자 reset → background translate 가 다시 채움
    5. version+1 optimistic locking (admin update-eval 동일 패턴)
    """
    import datetime as _dt
    try:
        from app.eval_v2.api.common import (
            _MAX_TEXT_LEN, _MAX_NAME_LEN,
            load_snapshot_questions, extract_max_scores, extract_valid_qids,
        )
        from app.eval_v2.api.responses import (
            _calc_payload_hash, _translation_pool, _bg_translate_response,
        )
        from app.eval_v2.api.common import kst_now

        data = request.get_json(silent=True) or {}
        doc_id = str(data.get('docId', '')).strip()
        if not doc_id or not DOC_ID_RE.match(doc_id):
            return error('Invalid docId.', 400)
        scores = data.get('scores', {})
        comment_en = str(data.get('commentEn', '')).strip()
        open_answers = data.get('openAnswers', {})
        if len(comment_en) > _MAX_TEXT_LEN:
            return error('Comment too long.', 400)
        if not isinstance(open_answers, dict):
            return error('Invalid open answers format.', 400)
        for ans_id, ans_text in open_answers.items():
            if len(str(ans_text)) > _MAX_TEXT_LEN:
                return error(f'Open answer too long: {ans_id}', 400)
        if not isinstance(scores, dict):
            return error('Invalid scores format.', 400)

        my_emp_id = _resolve_my_emp_id()
        my_name = (_resolve_me()[4] or '').strip()
        my_normalized_name = _normalize_rater_name(my_name)
        if not my_emp_id and not my_normalized_name:
            return error('Your portal account has no emp_id or name. Contact admin.', 403, code='NO_IDENTITY')

        client_version_raw = data.get('version', None)
        try:
            client_version = int(client_version_raw) if client_version_raw is not None else None
        except (ValueError, TypeError):
            client_version = None

        # 1. doc 조회 + 본인 가드 + 세션 활성/기간 검증
        db = get_firestore_client()
        ref = db.collection(COL_EVAL_V2_RESPONSES).document(doc_id)
        pre_snap = ref.get()
        if not pre_snap.exists:
            return error('Response not found.', 404)
        pre_data = pre_snap.to_dict() or {}
        owner_ok, owner_via = _verify_doc_owner(pre_data, my_emp_id, my_normalized_name)
        if not owner_ok:
            return error('You can only edit your own responses.', 403, code='NOT_OWNER')

        pre_session_id = pre_data.get('session_id', '')
        pre_eval_type = pre_data.get('eval_type', '')
        pre_rater_role = pre_data.get('rater_role', '')
        pre_rater_name = pre_data.get('rater_name', '')
        if not pre_session_id:
            return error('Response has no session.', 400)

        sess_snap = db.collection(COL_EVAL_V2_SESSIONS).document(pre_session_id).get()
        if not sess_snap.exists:
            return error('Session not found.', 404)
        sess_data = sess_snap.to_dict() or {}
        if sess_data.get('status') != 'active':
            return error('Session is not active. Contact admin to edit closed session.', 400)
        try:
            _KST = _dt.timezone(_dt.timedelta(hours=9))
            today_kst = _dt.datetime.now(_KST).date()
            start_date_str = sess_data.get('start_date', '')
            end_date_str = sess_data.get('end_date', '')
            if start_date_str:
                start_d = _dt.date.fromisoformat(start_date_str)
                if today_kst < start_d:
                    return error(f'This session has not started yet. Opens {start_date_str}.', 400)
            if end_date_str:
                end_d = _dt.date.fromisoformat(end_date_str)
                if today_kst > end_d:
                    return error(f'This session period has ended. Closed {end_date_str}.', 400)
        except (ValueError, Exception):
            pass

        # 2. scores / OQ 검증 — submit-eval 과 동일하게 키 화이트리스트 + 범위 검증
        snapshot = sess_data.get('questions_snapshot') or {}
        roles_list = load_snapshot_questions(snapshot, pre_eval_type)
        max_scores = extract_max_scores(roles_list)
        valid_qids = extract_valid_qids(roles_list)
        if not valid_qids:
            return error('Could not load evaluation questions.', 500)
        required_oq_ids = []
        for role_obj in (roles_list or []):
            if not isinstance(role_obj, dict):
                continue
            role_name = role_obj.get('name') or role_obj.get('role', '')
            if role_name != pre_rater_role:
                continue
            for oq in role_obj.get('open_questions', []) or []:
                if isinstance(oq, dict) and oq.get('required') and oq.get('id'):
                    required_oq_ids.append(oq['id'])
        # 보안: scores / open_answers 키 화이트리스트 — submit-eval 과 동일 (admin update-eval 보다 strict)
        for qid, val in scores.items():
            if qid not in valid_qids:
                return error(f'Invalid question ID: {qid}', 400)
            try:
                fval = float(val)
                cap = max_scores.get(qid) or 5
                if fval != 0 and not (1 <= fval <= cap):
                    return error(f'Scores must be 0 or in range 1-{cap}. ({qid}: {val})', 400)
            except (ValueError, TypeError):
                return error(f'Invalid score format. ({qid}: {val})', 400)
        for ans_id in open_answers:
            if ans_id not in valid_qids:
                return error(f'Invalid open answer ID: {ans_id}', 400)
        for oq_id in required_oq_ids:
            if not str(open_answers.get(oq_id, '')).strip():
                return error(f'Required open answer missing: {oq_id}', 400)

        # 3. payload — rater_role/rater_name/is_manual 보존, commentKo 는 빈문자 reset (번역 재트리거)
        has_open = bool(open_answers and any(str(v).strip() for v in open_answers.values()))
        new_payload_hash = _calc_payload_hash(scores, comment_en, '', open_answers)
        base_update = {
            'scores': scores,
            'comment_en': comment_en,
            'comment_ko': '',  # 번역으로 재채움
            'open_answers': open_answers,
            'updated_at': kst_now(),
            'updated_by': session.get('admin_email', '') or pre_rater_name,
            'payload_hash': new_payload_hash,
            'self_edited_at': kst_now(),  # 본인 수정 추적
            'self_edited_by_emp_id': my_emp_id,
            'translation_status': 'pending' if has_open or comment_en else 'skipped',
        }

        # 4. version+1 optimistic locking (admin update-eval 동일 패턴)
        from google.cloud import firestore as _fs

        class _VersionConflict(Exception):
            def __init__(self, current_version):
                self.current_version = current_version

        @_fs.transactional
        def _update_txn(tx):
            snap = ref.get(transaction=tx)
            if not snap.exists:
                raise ValueError('not_found')
            existing = snap.to_dict() or {}
            stored_version = int(existing.get('version') or 0)
            if client_version is not None and client_version != stored_version:
                raise _VersionConflict(stored_version)
            new_version = stored_version + 1
            tx.update(ref, {**base_update, 'version': new_version})
            return new_version

        try:
            new_version = _update_txn(db.transaction())
        except _VersionConflict as e:
            return error(
                'This evaluation was modified elsewhere. Please reload and try again.',
                409,
                code='VERSION_CONFLICT',
                currentVersion=e.current_version,
            )
        except ValueError as ve:
            if 'not_found' in str(ve):
                return error('Response not found.', 404)
            raise

        # 5. background translate 재호출 (commentEn / open_answers 갱신 시)
        if has_open or comment_en:
            try:
                _translation_pool.submit(_bg_translate_response, doc_id, open_answers)
            except Exception:
                logger.debug('translation pool submit failed for %s', doc_id, exc_info=True)

        # 6. audit — via 정보 포함 (legacy fallback 추적용)
        try:
            log_audit('eval_self_update',
                      actor=session.get('admin_email', '') or my_emp_id,
                      target=doc_id,
                      details={
                          'version': new_version,
                          'session_id': pre_session_id,
                          'rater_role': pre_rater_role,
                          'owner_via': owner_via,  # 'rater_emp_id' or 'name_fallback' (동명이인 위험 추적)
                      },
                      category='response')
        except Exception:
            logger.debug('audit log failed for eval_self_update', exc_info=True)

        return success({'version': new_version})
    except Exception:
        logger.exception('api_my_tasks_update_my_eval error')
        return error('An internal error occurred.', 500)
