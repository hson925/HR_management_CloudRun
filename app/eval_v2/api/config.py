import logging
import re
from flask import request, jsonify, session

from app.eval_v2.blueprints import eval_v2_api
from app.auth_utils import api_admin_required
from app.extensions import limiter
from app.eval_v2.api.common import (
    kst_now, get_config, get_questions, get_weights,
    _MIN_MAX_SCORE, _MAX_MAX_SCORE, _MAX_DESC_LEN,
)
from app.utils.time_utils import kst_today
from app.eval_v2.questions import DEFAULT_QUESTIONS, EVAL_TYPE_LABELS
from app.services.cache_service import invalidate_config, invalidate_sessions
from app.services.firebase_service import get_firestore_client
from app.services.roster_cache_service import get_roster
from app.utils.response import success, error
from app.utils.html_sanitizer import strip_to_text
from app.utils.rate_limit import admin_rate_key, client_ip_key
from app.constants import COL_EVAL_V2_CONFIG, COL_EVAL_V2_SESSIONS
from app.services import role_service

logger = logging.getLogger(__name__)

# NET 교사 사번 포맷: 'N' + 숫자 5자리 (서버는 이미 lower() 로 정규화 후 검증).
# /api/v2/get-questions 는 NET 평가 전용 — 내부직원·외부평가자 ID 포맷은 여기서 차단됨.
_NET_EMP_ID_RE = re.compile(r'^n\d{5}$')


def _sanitize_question(q: dict, fallback_id: str = '') -> dict:
    """문항 1건 정규화: id 보장 + max_score 검증 + descriptions 정화/캡.
    내부 mutation — 입력 dict 자체를 수정하고 반환.
    """
    if not isinstance(q, dict):
        return q
    if not q.get('id') and fallback_id:
        q['id'] = fallback_id

    # max_score: 유효한 정수 [_MIN, _MAX] 만 보존, 그 외 필드 자체 drop
    if 'max_score' in q:
        try:
            v = int(q['max_score'])
            if _MIN_MAX_SCORE <= v <= _MAX_MAX_SCORE:
                q['max_score'] = v
            else:
                q.pop('max_score', None)
        except (ValueError, TypeError):
            q.pop('max_score', None)

    # descriptions: dict 키는 "1".."max_score" 정수, 값은 {ko, en} (둘 다 strip_to_text)
    raw = q.get('descriptions')
    if raw is not None:
        if not isinstance(raw, dict):
            q.pop('descriptions', None)
        else:
            # 클라이언트가 max_score 축소했어도 메모리에 잔존시킬 수 있음 → 서버는 cap 으로 trim.
            # max_score 누락 시 폴백 5.
            cap = q.get('max_score') if isinstance(q.get('max_score'), int) else 5
            cleaned = {}
            for k, v in raw.items():
                try:
                    n = int(k)
                except (ValueError, TypeError):
                    continue
                if not (1 <= n <= cap):
                    continue
                if not isinstance(v, dict):
                    continue
                ko = strip_to_text(v.get('ko', ''), max_len=_MAX_DESC_LEN)
                en = strip_to_text(v.get('en', ''), max_len=_MAX_DESC_LEN)
                if not ko and not en:
                    continue
                cleaned[str(n)] = {'ko': ko, 'en': en}
            if cleaned:
                q['descriptions'] = cleaned
            else:
                q.pop('descriptions', None)

    return q


# portal_role_mappings 에 허용되는 값:
# · 활성 role (Firestore portal_roles, custom role 포함) 중 퇴사(retired/퇴사) 제외 — 퇴사자는 평가자 자격 없음.
# · 추가로 `__public__` sentinel — 비로그인(외부) 평가자도 이 rater role 을 담당.
#   sentinel 은 portal_users.role 에 절대 들어가지 않는 prefix 형태로 격리.
PUBLIC_RATER_SENTINEL: str = '__public__'


def _get_valid_portal_role_mappings() -> set:
    """Lazy lookup — admin 이 추가한 custom role 즉시 반영. 60초 cache TTL."""
    return set(role_service.get_role_names_excluding_retired()) | {PUBLIC_RATER_SENTINEL}


def _sanitize_portal_role_mappings(value) -> list:
    """role.portal_role_mappings sanitize: 화이트리스트 통과한 항목만 보존, 중복 제거, 순서 유지."""
    if not isinstance(value, list):
        return []
    valid = _get_valid_portal_role_mappings()
    seen = set()
    out = []
    for v in value:
        if not isinstance(v, str):
            continue
        if v not in valid:
            continue
        if v in seen:
            continue
        seen.add(v)
        out.append(v)
    return out


def _ensure_question_ids(roles: list) -> list:
    """모든 문항과 서술형 문항에 고유 id가 없으면 강제 부여 + 점수 척도/설명 정화 + 서술형 필수 플래그 sanitize."""
    for role in roles:
        if not isinstance(role, dict):
            continue
        # XSS 방지 — role 표시 라벨 sanitize (status badge / my_tasks chip 등 sink 노출).
        # 같은 파일의 description ko/en 처리 (line 69-70) 와 동일 패턴 (silent strip).
        if 'label_ko' in role:
            role['label_ko'] = strip_to_text(role.get('label_ko', ''), max_len=50)
        if 'label' in role:
            role['label'] = strip_to_text(role.get('label', ''), max_len=50)
        for i, q in enumerate(role.get('questions', [])):
            _sanitize_question(q, fallback_id=f"q{i+1}")
        for i, oq in enumerate(role.get('open_questions', [])):
            if not isinstance(oq, dict):
                continue
            if not oq.get('id'):
                oq['id'] = f"oq{i+1}"
            oq['required'] = bool(oq.get('required', False))
        # portal_role_mappings: 어떤 portal_users.role 사용자가 이 rater role 자격을 갖는지.
        # 빈 배열은 "매핑 없음" → my-tasks 페이지에서 이 role 표시 안 함.
        role['portal_role_mappings'] = _sanitize_portal_role_mappings(role.get('portal_role_mappings'))
    return roles


def save_config(eval_type, config_type, data):
    try:
        db = get_firestore_client()
        db.collection(COL_EVAL_V2_CONFIG).document(config_type).collection('data').document(eval_type).set(data)
        invalidate_config(eval_type, config_type)
        return True
    except Exception:
        logger.exception('save_config error')
        return False


@eval_v2_api.route('/get-questions', methods=['POST'])
@limiter.limit("10 per minute")
def api_get_questions():
    # 이 엔드포인트는 'OK' / 'NOT_FOUND' / 'NO_ACTIVE_SESSION' 등 non-standard status 를 사용하므로
    # 공용 success/error 헬퍼 대신 jsonify 유지 (프론트 계약 보존).
    try:
        _body = request.get_json(silent=True) or {}
        emp_id = str(_body.get('empId', '')).strip().lower()
        requested_session_id = str(_body.get('sessionId', '')).strip()
        if not emp_id:
            return jsonify({'status': 'ERROR', 'message': 'empId is required.'})
        if not _NET_EMP_ID_RE.fullmatch(emp_id):
            # enumeration / 클라 우회 시도 추적용. emp_id 는 앞 20자만 기록해 PII 축소.
            logger.info(
                'get-questions invalid emp_id format: emp_id=%r ip=%s',
                emp_id[:20], client_ip_key(),
            )
            return jsonify({
                'status': 'INVALID_FORMAT',
                'message': 'Employee ID must be N followed by 5 digits (e.g. N12345).',
            })
        rows = get_roster()
        target = None
        for row in rows:
            if len(row) > 2 and str(row[2]).strip().lower() == emp_id:
                target = {
                    'name': row[1] if len(row) > 1 else '',
                    'emp_id': row[2],
                    'type': str(row[3]).lower() if len(row) > 3 else '',
                    'campus': row[4] if len(row) > 4 else '',
                }
                break
        if not target:
            return jsonify({'status': 'NOT_FOUND'})
        # 활성 회차 목록 조회 (마감일 지난 세션 자동 종료)
        db_check = get_firestore_client()
        today_str = kst_today()
        active_docs = list(db_check.collection(COL_EVAL_V2_SESSIONS).where('status', '==', 'active').stream())
        active_sessions = []
        to_close = []
        # 내부 직원(로그인된 비퇴직자) 면제 여부는 요청 단위로 고정 — 각 세션마다
        # 다시 확인할 필요 없음.
        from app.eval_v2.api.passcode_gate import is_internal_bypass as _bypass_fn, gate_check as _gate_check
        _bypass_now = _bypass_fn()
        # 클라이언트가 특정 sessionId 를 지정한 경우, 해당 세션에 대한 passcode
        # 게이트를 서버에서도 확인 (defense-in-depth — 악의적 클라가 모달을
        # 우회해도 teacher 정보가 노출되지 않도록).
        if requested_session_id:
            try:
                req_snap = db_check.collection(COL_EVAL_V2_SESSIONS).document(requested_session_id).get()
                if req_snap.exists:
                    req_dict = dict(req_snap.to_dict() or {})
                    req_dict['id'] = requested_session_id
                    if not _gate_check(req_dict):
                        return jsonify({
                            'status': 'PASSCODE_REQUIRED',
                            'code': 'PASSCODE_REQUIRED',
                        })
            except Exception:
                logger.debug('passcode gate check failed silently for session %s',
                             requested_session_id, exc_info=True)
        for d in active_docs:
            sess = d.to_dict()
            start_date = sess.get('start_date', '')
            end_date   = sess.get('end_date', '')
            if end_date and end_date < today_str:
                to_close.append(d.id)
            elif start_date and start_date > today_str:
                # pre-start: 상태는 active 유지, 반환 목록에서만 제외 (defense-in-depth)
                continue
            else:
                # passcode_required: 세션에 passcode_hash 가 설정돼 있고, 요청자가
                # 내부 직원(로그인된 비퇴직자) 이 아닐 때만 True.
                requires = bool(sess.get('passcode_hash')) and not _bypass_now
                active_sessions.append({
                    'id': d.id,
                    'label': sess.get('label', ''),
                    'passcode_required': requires,
                })
        # 만료 세션 일괄 종료 (batch write로 원자적 처리)
        if to_close:
            batch = db_check.batch()
            closed_at = kst_now()
            for doc_id in to_close:
                batch.update(db_check.collection(COL_EVAL_V2_SESSIONS).document(doc_id), {
                    'status': 'closed',
                    'closed_at': closed_at,
                    'closed_by': 'system (auto-expired)',
                })
            batch.commit()
        if not active_sessions:
            return jsonify({'status': 'NO_ACTIVE_SESSION', 'message': 'No active evaluation session.\nPlease contact the administrator.'})
        eval_type = target['type']
        if eval_type not in DEFAULT_QUESTIONS:
            return jsonify({'status': 'ERROR', 'message': f'Unknown eval type: {eval_type}'})
        # 세션이 지정된 경우 해당 세션의 questions_snapshot 우선 사용.
        # admin 의 /save-session-questions 변경이 평가 폼에 반영되도록.
        # snapshot 이 없거나 해당 eval_type 항목이 비어 있으면 global config 로 폴백.
        roles_raw = None
        snapshot_weights = None
        if requested_session_id:
            for d in active_docs:
                if d.id == requested_session_id:
                    snap = (d.to_dict() or {}).get('questions_snapshot', {}) or {}
                    snap_for_type = snap.get(eval_type, {}) or {}
                    roles_raw = snap_for_type.get('questions') or None
                    snapshot_weights = snap_for_type.get('weights') or None
                    break
        if roles_raw is None:
            roles_raw = get_questions(eval_type)
        # form.html이 role/items/ko/en 키를 사용하므로 변환
        roles = []
        for r in roles_raw:
            if not isinstance(r, dict): continue
            name = r.get('name') or r.get('role', '')
            label = r.get('label_ko') or r.get('label') or name
            items = []
            for q in r.get('questions', r.get('items', [])):
                items.append({
                    'id': q.get('id', ''),
                    'ko': q.get('text_ko') or q.get('ko', ''),
                    'en': q.get('text_en') or q.get('en', ''),
                    'max_score':    q.get('max_score'),       # None 가능 — 클라가 폴백
                    'descriptions': q.get('descriptions') or {},
                })
            open_questions = []
            for oq in r.get('open_questions', []):
                open_questions.append({
                    'id': oq.get('id', ''),
                    'text_ko': oq.get('text_ko', ''),
                    'text_en': oq.get('text_en', ''),
                    'required': bool(oq.get('required', False)),
                })
            roles.append({
                'role': name,
                'label': label,
                'min_count': r.get('min_count', 1),
                'items': items,
                'open_questions': open_questions,
                'portal_role_mappings': _sanitize_portal_role_mappings(r.get('portal_role_mappings')),
            })
        # weights 도 snapshot 우선 (questions 와 동일 정책).
        if snapshot_weights:
            weights = snapshot_weights
        else:
            try:
                weights = get_weights(eval_type)
            except Exception:
                weights = {}
        return jsonify({
            'status': 'OK',
            'teacher': target,
            'evalType': eval_type,
            'evalTypeLabel': EVAL_TYPE_LABELS.get(eval_type, eval_type),
            'roles': roles,
            'weights': weights,
            'activeSessions': active_sessions,
        })
    except Exception:
        logger.exception('api_get_questions error')
        return jsonify({'status': 'ERROR', 'message': 'An internal error occurred.'})


@eval_v2_api.route('/get-weights', methods=['POST'])
@api_admin_required
@limiter.limit("60 per minute", key_func=admin_rate_key)
def api_get_weights():
    try:
        _body = request.get_json(silent=True) or {}
        eval_type = str(_body.get('evalType', '')).strip().lower()
        weights = get_weights(eval_type)
        # 저장값이 어떤 형태든 항상 0~1 float로 정규화해서 반환
        data = {}
        for k, v in weights.items():
            v = float(v)
            if v > 100:      # 2500, 5000 같은 잘못된 값
                v = v / 100
            elif v > 1:      # 25, 50 같은 % 정수
                v = v / 100
            data[k] = round(v, 4)
        return success({'data': data})
    except Exception:
        return error('An internal error occurred.')


@eval_v2_api.route('/save-weights', methods=['POST'])
@api_admin_required
@limiter.limit("30 per minute", key_func=admin_rate_key)
def api_save_weights():
    try:
        data      = request.get_json(silent=True) or {}
        eval_type = str(data.get('evalType', '')).strip().lower()
        weights   = data.get('weights', {})
        # 개별 값 범위 검증
        for k, v in weights.items():
            fv = float(v)
            if fv < 0:
                return error(f'Weight cannot be negative. ({k}: {v})')
        total     = sum(float(v) for v in weights.values())
        # 0~1 범위(소수)이면 *100으로 변환 (모든 값이 0~1 이내일 때만)
        if total <= 1.01 and all(0 <= float(v) <= 1 for v in weights.values()):
            weights = {k: round(float(v) * 100, 1) for k, v in weights.items()}
            total = sum(weights.values())
        # 100 초과(잘못된 값)면 정규화
        if total > 110:
            weights = {k: round(float(v) / (total/100), 1) for k, v in weights.items()}
            total = sum(weights.values())
        if abs(total - 100) > 0.5:
            return error(f'Weight sum must equal 100. (Current: {total:.1f})')
        save_config(eval_type, 'weights', {
            'weights': weights, 'updated_at': kst_now(),
            'updated_by': session.get('admin_email', ''),
        })
        # 문항 설정에 없는 역할은 빈 문항으로 자동 추가
        existing_roles = get_questions(eval_type)
        existing_names = set()
        for r in existing_roles:
            if isinstance(r, dict):
                existing_names.add(r.get('name') or r.get('role', ''))
        new_roles_added = False
        for role_name in weights.keys():
            if role_name not in existing_names:
                existing_roles.append({
                    'name': role_name, 'label_ko': role_name,
                    'min_count': 1, 'questions': [],
                    'portal_role_mappings': [],
                })
                new_roles_added = True
        if new_roles_added:
            save_config(eval_type, 'questions', {
                'roles': _ensure_question_ids(existing_roles),
                'updated_at': kst_now(),
                'updated_by': session.get('admin_email', ''),
            })
        return success({'rolesAdded': new_roles_added})
    except Exception:
        logger.exception('api_save_weights error')
        return error('An internal error occurred.')


@eval_v2_api.route('/get-questions-config', methods=['POST'])
@api_admin_required
@limiter.limit("120 per minute", key_func=admin_rate_key)
def api_get_questions_config():
    try:
        _body = request.get_json(silent=True) or {}
        eval_type = str(_body.get('evalType', '')).strip().lower()
        session_id_param = str(_body.get('sessionId', '')).strip()
        if session_id_param:
            try:
                db_check = get_firestore_client()
                sess_doc = db_check.collection(COL_EVAL_V2_SESSIONS).document(session_id_param).get()
                if sess_doc.exists:
                    snap = sess_doc.to_dict().get('questions_snapshot', {})
                    roles_raw = snap.get(eval_type, {}).get('questions', get_questions(eval_type))
                else:
                    roles_raw = get_questions(eval_type)
            except Exception:
                roles_raw = get_questions(eval_type)
        else:
            roles_raw = get_questions(eval_type)
        # 프론트 형식에 맞게 필드명 변환: role->name, label->label_ko
        roles = []
        for r in roles_raw:
            if not isinstance(r, dict): continue
            name = r.get('name') or r.get('role', '')
            label = r.get('label_ko') or r.get('label') or name
            questions = []
            for q in r.get('questions', r.get('items', [])):
                questions.append({
                    'id': q.get('id', ''),
                    'ko': q.get('text_ko') or q.get('ko', ''),
                    'en': q.get('text_en') or q.get('en', ''),
                    'max_score':    q.get('max_score'),
                    'descriptions': q.get('descriptions') or {},
                })
            open_questions = []
            for oq in r.get('open_questions', []):
                open_questions.append({
                    'id': oq.get('id', ''),
                    'text_ko': oq.get('text_ko', ''),
                    'text_en': oq.get('text_en', ''),
                    'required': bool(oq.get('required', False)),
                })
            roles.append({
                'name': name,
                'label_ko': label,
                'pill_class': r.get('pill_class', ''),
                'min_count': r.get('min_count', 1),
                'questions': questions,
                'open_questions': open_questions,
                'portal_role_mappings': _sanitize_portal_role_mappings(r.get('portal_role_mappings')),
            })
        return success({'data': {'roles': roles}})
    except Exception:
        return error('An internal error occurred.')


@eval_v2_api.route('/save-questions', methods=['POST'])
@api_admin_required
@limiter.limit("30 per minute", key_func=admin_rate_key)
def api_save_questions():
    try:
        data      = request.get_json(silent=True) or {}
        eval_type = str(data.get('evalType', '')).strip().lower()
        roles     = data.get('roles', [])
        if eval_type not in EVAL_TYPE_LABELS:
            return error(f'Invalid evalType: {eval_type}')
        if not roles:
            return error('Roles data is required.')
        save_config(eval_type, 'questions', {
            'roles': _ensure_question_ids(roles), 'updated_at': kst_now(),
            'updated_by': session.get('admin_email', ''),
        })
        return success()
    except Exception:
        logger.exception('api_save_questions error')
        return error('An internal error occurred.')


@eval_v2_api.route('/reset-questions', methods=['POST'])
@api_admin_required
@limiter.limit("10 per minute", key_func=admin_rate_key)
def api_reset_questions():
    try:
        _body = request.get_json(silent=True) or {}
        eval_type = str(_body.get('evalType', '')).strip().lower()
        db = get_firestore_client()
        db.collection(COL_EVAL_V2_CONFIG).document('questions').collection('data').document(eval_type).delete()
        invalidate_config(eval_type, 'questions')
        return success({'message': 'Reset to default questions.'})
    except Exception:
        logger.exception('api_reset_questions error')
        return error('An internal error occurred.')


@eval_v2_api.route('/save-session-questions', methods=['POST'])
@api_admin_required
@limiter.limit("30 per minute", key_func=admin_rate_key)
def api_save_session_questions():
    try:
        data       = request.get_json(silent=True) or {}
        session_id = str(data.get('sessionId', '')).strip()
        eval_type  = str(data.get('evalType', '')).strip().lower()
        roles      = data.get('roles', [])
        weights    = data.get('weights', {})
        if not session_id or not eval_type or not roles:
            return error('sessionId, evalType, roles are required.')
        db = get_firestore_client()
        sess_ref = db.collection(COL_EVAL_V2_SESSIONS).document(session_id)
        sess_doc = sess_ref.get()
        if not sess_doc.exists:
            return error('Session not found.')
        snapshot = sess_doc.to_dict().get('questions_snapshot', {})
        snapshot[eval_type] = {'questions': _ensure_question_ids(roles), 'weights': weights}
        sess_ref.update({'questions_snapshot': snapshot})
        # 세션 목록 캐시 (_fetch_sessions_data, 30초) 를 무효화해 업데이트된
        # questions_snapshot 이 즉시 반영되게 함.
        invalidate_sessions()
        return success()
    except Exception:
        logger.exception('api_save_session_questions error')
        return error('An internal error occurred.')


@eval_v2_api.route('/translate-question-descriptions', methods=['POST'])
@api_admin_required
@limiter.limit('10 per minute', key_func=admin_rate_key)
def api_translate_question_descriptions():
    """문항의 점수별 설명을 한국어 → 영어 일괄 번역.
    요청: {evalType, questionContext, maxScore, descriptionsKo: {"1":"...","3":"..."}}
    응답: {status:'SUCCESS', descriptions_en: {"1":"...","3":"..."}, model, input_tokens, output_tokens}
    빈 KO 키는 클라이언트가 미리 제거해서 전송. 응답엔 입력 키만 포함됨.
    """
    from app.services.openai_service import translate_score_descriptions, EvalTranslateError
    from app.services.audit_service import log_audit
    try:
        data = request.get_json(silent=True) or {}
        eval_type = str(data.get('evalType', '')).strip().lower()
        question_context = str(data.get('questionContext', '')).strip()
        max_score_raw = data.get('maxScore', 5)
        descriptions_ko = data.get('descriptionsKo', {})

        if eval_type and eval_type not in EVAL_TYPE_LABELS:
            return error(f'Invalid evalType: {eval_type}', 400)
        if not isinstance(descriptions_ko, dict) or not descriptions_ko:
            return error('descriptionsKo is empty.', 400)

        # 빈 값 방어 (정상이면 클라이언트가 이미 제거)
        cleaned_ko = {str(k): str(v).strip() for k, v in descriptions_ko.items() if str(v).strip()}
        if not cleaned_ko:
            return error('descriptionsKo is empty.', 400)

        try:
            max_score = int(max_score_raw)
            if not (_MIN_MAX_SCORE <= max_score <= _MAX_MAX_SCORE):
                max_score = 5
        except (ValueError, TypeError):
            max_score = 5

        try:
            result = translate_score_descriptions(question_context, max_score, cleaned_ko)
        except EvalTranslateError as e:
            return error(str(e), e.http_status)

        log_audit(
            'eval_v2_descriptions_translated',
            session.get('admin_email', ''),
            target=f'{eval_type}/score_descriptions',
            details={
                'eval_type': eval_type,
                'model': result.get('model'),
                'input_tokens': result.get('input_tokens', 0),
                'output_tokens': result.get('output_tokens', 0),
                'score_count': len(cleaned_ko),
            },
            category='eval',
        )

        return success({
            'descriptions_en': result.get('descriptions_en', {}),
            'model':           result.get('model'),
            'input_tokens':    result.get('input_tokens', 0),
            'output_tokens':   result.get('output_tokens', 0),
        })
    except Exception:
        logger.exception('api_translate_question_descriptions error')
        return error('An internal error occurred.', 500)
