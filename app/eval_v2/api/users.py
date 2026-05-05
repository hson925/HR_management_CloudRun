import logging
from flask import request, session
from app.eval_v2.blueprints import eval_v2_api
from app.auth_utils import api_admin_required
from app.constants import ADMIN_ROLES, RETIRED_ROLES
from app.services import role_service
from app.eval_v2.api.common import kst_now
from app.utils.time_utils import KST
from app.services.firebase_service import get_firestore_client
from app.services.roster_cache_service import get_roster
from app.extensions import limiter
from app.utils.response import success, error

logger = logging.getLogger(__name__)


_EMAIL_TEMPLATE_DEFAULTS = [
    {
        'id': 'default-en',
        'name': 'Password Setup (English)',
        'subject': '[NHR Portal] Set Your Password',
        'body': (
            "Hello {{NAME}},\n\n"
            "Your DYB NHR Portal account has been created.\n"
            "Please click the link below to set your password.\n\n"
            "{{RESET_LINK}}\n\n"
            "※ This link is valid for 1 hour.\n"
            "※ If you did not request this, please contact noreply@example.com immediately."
        ),
    },
    {
        'id': 'default-ko',
        'name': '비밀번호 설정 안내 (한국어)',
        'subject': '[NHR Portal] 비밀번호 설정 안내',
        'body': (
            "안녕하세요, {{NAME}}님.\n\n"
            "DYB NHR 포털 계정이 생성되었습니다.\n"
            "아래 링크를 클릭하여 비밀번호를 설정해 주세요.\n\n"
            "{{RESET_LINK}}\n\n"
            "※ 이 링크는 1시간 동안만 유효합니다.\n"
            "※ 본인이 요청하지 않은 경우 noreply@example.com으로 문의해 주세요."
        ),
    },
]


@eval_v2_api.route('/admin/users', methods=['POST'])
@api_admin_required
def api_admin_users():
    """Return all registered users joined with roster campus info."""
    try:
        from app.services.user_service import get_all_users
        users = get_all_users()
        # Build campus map from roster for users missing campus field
        roster = get_roster()
        campus_map = {}
        for row in roster:
            if len(row) > 4:
                eid = str(row[2]).strip().lower()
                campus_map[eid] = str(row[4]).strip()
        result = []
        for u in users:
            emp_id = u.get('emp_id', '')
            campus = u.get('campus', '') or campus_map.get(emp_id, '')
            result.append({
                'emp_id': emp_id,
                'name': u.get('name', ''),
                'email': u.get('email', ''),
                'role': u.get('role', ''),
                'campus': campus,
                'firebase_uid': u.get('firebase_uid', ''),
                'registered_at': u.get('registered_at', ''),
                'updated_at': u.get('updated_at', ''),
                'updated_by': u.get('updated_by', ''),
                'notes': u.get('notes', ''),
            })
        return success({'users': result})
    except Exception:
        logger.exception('api_admin_users error')
        return error('An internal error occurred.', 500)


@eval_v2_api.route('/admin/users/update', methods=['POST'])
@api_admin_required
def api_admin_users_update():
    """Update user role and/or notes."""
    try:
        from app.services.user_service import update_user, get_user_by_emp_id
        data = request.get_json(silent=True) or {}
        emp_id = data.get('empId', '').strip().lower()
        if not emp_id:
            return error('empId is required', 400)
        updates = {}
        if 'role' in data:
            updates['role'] = data['role']
        if 'campus' in data:
            updates['campus'] = data['campus']
        if 'notes' in data:
            updates['notes'] = data['notes']
        if not updates:
            return error('No fields to update', 400)

        # role 화이트리스트 검증 — Firestore portal_roles 기반 lazy lookup (custom role 포함).
        # 'MASTER' 는 legacy 호환용 — system role seed 에 포함되므로 통과.
        _VALID_ROLES = set(role_service.get_role_names())
        _ADMIN_ROLES = ADMIN_ROLES
        if 'role' in updates and updates['role'] not in _VALID_ROLES:
            return error('Invalid role value.', 400)

        # 자기 자신의 role 변경 금지
        if 'role' in updates and emp_id == session.get('emp_id', '').lower():
            return error('You cannot change your own role.', 403)

        # 이전 role 기록 — 변경 감지용 (M1: 동일 값 재저장 시 세션 무효화 방지)
        # emp_id 필드 항상 포함 — doc.id 와 동일하므로 idempotent (GS/admin 빈 필드 복구)
        previous_role = None
        _pre_doc = get_user_by_emp_id(emp_id)
        updates.setdefault('emp_id', emp_id)
        if 'role' in updates:
            previous_role = _pre_doc.get('role') if _pre_doc else None

        # 마지막 admin 강등/삭제 방지 (TOCTOU 방지: 트랜잭션으로 check+write 원자화)
        if 'role' in updates and updates['role'] not in _ADMIN_ROLES:
            from app.services.firebase_service import get_firestore_client
            from google.cloud import firestore as _fs
            from google.cloud.firestore_v1.base_query import FieldFilter as _FF

            db = get_firestore_client()
            users_ref  = db.collection('portal_users')
            target_ref = users_ref.document(emp_id)

            class _LastAdmin(Exception):
                pass

            @_fs.transactional
            def _demote_txn(tx):
                snap = target_ref.get(transaction=tx)
                if not snap.exists:
                    return False
                cur_role = (snap.to_dict() or {}).get('role')
                if cur_role not in _ADMIN_ROLES:
                    return False  # 이미 admin 아님 → 일반 경로로 진행
                # 트랜잭션 내부에서 admin 수 집계 (읽기 락 확보)
                admin_count = 0
                for role_val in _ADMIN_ROLES:
                    q = users_ref.where(filter=_FF('role', '==', role_val))
                    admin_count += sum(1 for _ in q.get(transaction=tx))
                if admin_count <= 1:
                    raise _LastAdmin()
                # 원자적으로 role(+ 동반 필드) 업데이트
                from datetime import datetime
                payload = dict(updates)
                payload['updated_at'] = datetime.now(KST).isoformat()
                payload['updated_by'] = session.get('admin_email', '')
                tx.update(target_ref, payload)
                return True

            try:
                demoted_in_txn = _demote_txn(db.transaction())
            except _LastAdmin:
                return error('Cannot remove the last admin account.', 403)
        else:
            demoted_in_txn = False

        # 역할별 캠퍼스 규칙 검증
        _CAMPUS_REQUIRED = ('NET', 'GS', 'TL')
        _CAMPUS_FIXED    = {'STL': 'SUB'}
        role_to_check   = updates.get('role') or data.get('currentRole', '')
        campus_to_check = updates.get('campus') or data.get('currentCampus', '')
        if role_to_check in _CAMPUS_REQUIRED and not campus_to_check:
            return error(f'{role_to_check} role requires a campus.', 400)
        if role_to_check in _CAMPUS_FIXED and not updates.get('campus'):
            updates['campus'] = _CAMPUS_FIXED[role_to_check]
        # campus-required 역할 변경 시 currentCampus 를 campus 필드로 승격 (JS가 campus 키를
        # 생략한 경우 대비 — Firestore portal_users.campus 가 반드시 기록되도록)
        if ('role' in updates and updates['role'] in _CAMPUS_REQUIRED
                and 'campus' not in updates and data.get('currentCampus', '')):
            updates['campus'] = data['currentCampus']
        updates['updated_by'] = session.get('admin_email', '')
        if not demoted_in_txn:
            ok = update_user(emp_id, updates)
            if not ok:
                return error('Update failed', 500)

        # 역할이 실제로 변경된 경우에만 강제 로그아웃 + Custom Claims 갱신
        role_changed = 'role' in data and updates.get('role') != previous_role
        custom_claims_warning = None
        if role_changed:
            from app.services.user_service import set_force_logout
            set_force_logout(emp_id)

            user_doc = get_user_by_emp_id(emp_id)
            firebase_uid = user_doc.get('firebase_uid', '') if user_doc else ''
            if firebase_uid:
                try:
                    from firebase_admin import auth as firebase_auth
                    firebase_auth.set_custom_user_claims(firebase_uid, {
                        'emp_id': emp_id,
                        'name': user_doc.get('name', ''),
                        'role': data['role'],
                    })
                except Exception:
                    logger.exception(f'Custom Claims 갱신 실패 — 해당 사용자의 권한이 즉시 반영되지 않을 수 있음 (emp_id={emp_id})')
                    custom_claims_warning = 'Role updated but token refresh required. User must re-login to apply changes.'

        from app.services.audit_service import log_audit
        log_audit('user_update', session.get('admin_email', ''), target=emp_id, details=updates, category='user')
        extra = {'warning': custom_claims_warning} if custom_claims_warning else {}
        return success(extra if extra else None)
    except Exception:
        logger.exception('api_admin_users_update error')
        return error('An internal error occurred.', 500)


@eval_v2_api.route('/admin/users/delete', methods=['POST'])
@api_admin_required
def api_admin_users_delete():
    """Delete a user from Firestore and Firebase Auth."""
    try:
        from app.services.user_service import delete_user, get_user_by_emp_id
        data = request.get_json(silent=True) or {}
        emp_id = data.get('empId', '').strip().lower()
        if not emp_id:
            return error('empId is required', 400)

        # Firebase Auth 삭제 (Firestore 삭제 전에 uid 조회)
        user_doc = get_user_by_emp_id(emp_id)
        firebase_uid = user_doc.get('firebase_uid', '') if user_doc else ''
        firebase_auth_deleted = False
        if firebase_uid:
            try:
                from firebase_admin import auth as firebase_auth
                firebase_auth.delete_user(firebase_uid)
                firebase_auth_deleted = True
            except Exception:
                logger.exception(f'Firebase Auth 삭제 실패 (emp_id={emp_id}, uid={firebase_uid}) — Firestore 삭제 계속 진행하지만 orphaned Auth 레코드가 남을 수 있음')

        ok = delete_user(emp_id)
        if ok:
            from app.services.audit_service import log_audit
            log_audit('user_delete', session.get('admin_email', ''), target=emp_id,
                      details={'firebase_auth_deleted': firebase_auth_deleted, 'firebase_uid': firebase_uid or None},
                      category='user')
            extra = {}
            if firebase_uid and not firebase_auth_deleted:
                extra['warning'] = 'User deleted but Firebase Auth record may remain. Contact admin if login issues persist.'
            return success(extra if extra else None)
        return error('Delete failed', 500)
    except Exception:
        logger.exception('api_admin_users_delete error')
        return error('An internal error occurred.', 500)


@eval_v2_api.route('/admin/users/create', methods=['POST'])
@api_admin_required
@limiter.limit("10 per minute")
def api_admin_users_create():
    """Admin이 직접 사용자를 생성합니다 (Firebase Auth + Firestore + 비밀번호 재설정 이메일)."""
    try:
        import secrets
        from firebase_admin import auth as firebase_auth
        from firebase_admin.auth import EmailAlreadyExistsError
        from app.services.user_service import register_user, get_user_by_emp_id, is_emp_id_registered
        from app.services.user_service import get_user_by_email
        from app.services.otp_service import send_reset_email
        from app.services.audit_service import log_audit

        data = request.get_json(silent=True) or {}
        emp_id      = str(data.get('empId', '')).strip().lower()
        name        = str(data.get('name', '')).strip()
        email       = str(data.get('email', '')).strip().lower()
        role        = str(data.get('role', '')).strip()
        # legacy 'MASTER' / 'master' / 'admin' 입력은 'admin' 으로 정규화 (DB migration 잔여 호환).
        # 그 외 입력은:
        # · system role (NET/GS/TL/STL) 의 lowercase 변형 → uppercase 정규화 (manual API 호환)
        # · 그 외 (custom role) 은 case 그대로 보존 — Firestore doc id 와 정확히 매칭
        if role.upper() == 'MASTER' or role.lower() == 'admin':
            role = 'admin'
        elif role.upper() in {'NET', 'GS', 'TL', 'STL'}:
            role = role.upper()
        campus      = str(data.get('campus', '')).strip()
        send_email  = bool(data.get('sendEmail', False))
        template_id = str(data.get('templateId', '')).strip()

        # 필수 필드 검증
        if not name:
            return error('name is required.', 400)
        if not email:
            return error('email is required.', 400)
        # 신규 사용자 생성 가능 role: 모든 활성 role - retired/퇴사 - MASTER (legacy).
        # custom role 도 직접 할당 가능 (admin 결정).
        _CREATABLE_ROLES = set(role_service.get_role_names()) - RETIRED_ROLES - {'MASTER'}
        if role not in _CREATABLE_ROLES:
            return error('Invalid role for new user.', 400)

        # 사번 미입력 시 자동 생성.
        # · GS 직급은 NT Info 시트에 사번이 없는 경우가 많아 `gs_<8 hex>` 임의 발급.
        #   prefix 로 user management 페이지에서 시각 식별 + EMP_ID_RE 통과.
        # · 그 외 role 은 기존대로 이메일 앞부분 기반 (영숫자만, 중복 시 suffix).
        if not emp_id:
            if role == 'GS':
                import secrets as _sec
                emp_id = f'gs_{_sec.token_hex(4)}'
                while is_emp_id_registered(emp_id):
                    emp_id = f'gs_{_sec.token_hex(4)}'
            else:
                base = email.split('@')[0].lower()
                # 영문/숫자만 남기기
                import re as _re
                base = _re.sub(r'[^a-z0-9]', '', base) or 'user'
                candidate = base
                suffix = 1
                while is_emp_id_registered(candidate):
                    candidate = f'{base}{suffix}'
                    suffix += 1
                emp_id = candidate

        # 중복 검사
        if is_emp_id_registered(emp_id):
            return error(f'Employee ID "{emp_id}" is already registered.', 409)
        if get_user_by_email(email):
            return error(f'Email "{email}" is already registered.', 409)

        # Firebase Auth 계정 생성
        firebase_uid = None
        try:
            fb_user = firebase_auth.create_user(
                email=email,
                password=secrets.token_urlsafe(16),
                display_name=name,
                email_verified=False,
            )
            firebase_uid = fb_user.uid
        except EmailAlreadyExistsError:
            fb_user = firebase_auth.get_user_by_email(email)
            firebase_uid = fb_user.uid

        # Custom Claims 설정 (role 재검증 — 방어적 이중 확인, lazy lookup 일관)
        if role not in _CREATABLE_ROLES:
            return error('Invalid role.', 400)
        try:
            firebase_auth.set_custom_user_claims(firebase_uid, {
                'emp_id': emp_id, 'name': name, 'role': role,
            })
        except Exception:
            logger.exception(f'Custom Claims 설정 실패 (emp_id={emp_id})')

        # Firestore 등록
        register_user(emp_id=emp_id, name=name, role=role,
                      firebase_uid=firebase_uid, email=email, campus=campus)

        # 비밀번호 재설정 이메일 발송 (선택)
        email_sent = False
        if send_email:
            try:
                from urllib.parse import urlparse, parse_qs as _parse_qs
                _firebase_link = firebase_auth.generate_password_reset_link(email)
                _params = _parse_qs(urlparse(_firebase_link).query)
                _oob = _params.get('oobCode', [''])[0]
                import os as _os
                _hosts_str = _os.environ.get('ALLOWED_HOSTS', '')
                _hosts_list = [h.strip() for h in _hosts_str.split(',') if h.strip()]
                # 커스텀 도메인 우선, 없으면 Cloud Run URL
                _app_host = _hosts_list[0] if _hosts_list else 'example-service-000000000000.region.run.app'
                _app_base = f'https://{_app_host}'
                reset_link = (f"{_app_base}/set-password?oobCode={_oob}"
                              if _oob else _firebase_link)
                # Look up the selected template by ID from Firestore, fall back to defaults
                _subject = None
                _body    = None
                if template_id:
                    try:
                        _db = get_firestore_client()
                        _tmpl_doc = _db.collection('email_settings').document('email_templates').get()
                        _tmpl_data = _tmpl_doc.to_dict() if _tmpl_doc.exists else {}
                        _templates = _tmpl_data.get('templates', _EMAIL_TEMPLATE_DEFAULTS)
                        for t in _templates:
                            if t.get('id') == template_id:
                                _subject = t.get('subject', '')
                                _body    = t.get('body', '')
                                break
                    except Exception:
                        logger.exception(f"Template '{template_id}' load failed, using default")
                if not _subject or not _body:
                    # Fall back to first default template
                    _subject = _EMAIL_TEMPLATE_DEFAULTS[0]['subject']
                    _body    = _EMAIL_TEMPLATE_DEFAULTS[0]['body']
                _body = _body.replace('{{NAME}}', name).replace('{{RESET_LINK}}', reset_link)
                send_reset_email(email, reset_link, subject=_subject, body=_body)
                email_sent = True
            except Exception:
                logger.exception(f'Password reset email failed (email={email})')

        log_audit('user_create_by_admin', session.get('admin_email', ''), target=emp_id, category='user')
        msg = f'{name} ({emp_id}) account created successfully.'
        if send_email:
            msg += ' Password setup email sent.' if email_sent else ' ⚠️ Email delivery failed — user must reset password manually.'
        return success({'message': msg, 'emailSent': email_sent if send_email else None})
    except Exception:
        logger.exception('api_admin_users_create error')
        return error('An internal error occurred.', 500)


@eval_v2_api.route('/admin/email-templates', methods=['GET'])
@api_admin_required
def api_get_email_templates():
    try:
        db = get_firestore_client()
        doc = db.collection('email_settings').document('email_templates').get()
        data = doc.to_dict() if doc.exists else {}
        templates = data.get('templates', _EMAIL_TEMPLATE_DEFAULTS)
        return success({'templates': templates})
    except Exception:
        logger.exception('api_get_email_templates error')
        return error('An internal error occurred.', 500)


@eval_v2_api.route('/admin/email-templates', methods=['POST'])
@api_admin_required
def api_save_email_templates():
    try:
        data = request.get_json(silent=True) or {}
        templates = data.get('templates', [])
        if not isinstance(templates, list):
            return error('templates must be an array.', 400)
        for t in templates:
            if not t.get('name') or not t.get('subject') or not t.get('body'):
                return error('Each template requires name, subject, and body.', 400)
            if '{{RESET_LINK}}' not in t['body']:
                return error(f'Template "{t["name"]}" body must contain {{{{RESET_LINK}}}}.', 400)
        db = get_firestore_client()
        db.collection('email_settings').document('email_templates').set({
            'templates': templates,
            'updated_at': kst_now(),
            'updated_by': session.get('admin_email', ''),
        })
        return success({'message': 'Templates saved successfully.'})
    except Exception:
        logger.exception('api_save_email_templates error')
        return error('An internal error occurred.', 500)


# ============================================================================
# 💡 사용자 상세 모달 — 약관 동의 상태 / 계정 보안 / NT 연동 종합
# ============================================================================

import re as _re_user_details
import time as _time_user_details
_EMP_ID_SAFE_RE = _re_user_details.compile(r'^[a-zA-Z0-9_\-@.]{1,60}$')

# 상세 모달 조회 감사 로그 스팸 방지 — 동일 (admin, target) 에 대해
# 5분 내 중복 호출은 audit_logs 에 기록하지 않음. Cloud Run worker=1 전제라
# 모듈 레벨 dict 로 족함 (다중 worker 시 Flask-Caching 으로 전환 필요).
_USER_VIEW_COOLDOWN_SECONDS = 300
_user_view_last_logged: dict = {}


def _should_log_user_view(actor: str, target: str) -> bool:
    if not actor or not target:
        return True
    now = _time_user_details.time()
    key = (actor, target)
    last = _user_view_last_logged.get(key)
    if last is not None and (now - last) < _USER_VIEW_COOLDOWN_SECONDS:
        return False
    _user_view_last_logged[key] = now
    # 간단 LRU — dict 이 1000 개 이상이면 가장 오래된 1/5 를 정리
    if len(_user_view_last_logged) > 1000:
        sorted_keys = sorted(_user_view_last_logged, key=_user_view_last_logged.get)
        for k in sorted_keys[:200]:
            _user_view_last_logged.pop(k, None)
    return True


def _current_tos_versions_safe(db):
    """legal_docs 에서 현재 발효 privacy/terms version 조회. 실패 시 0."""
    out = {'privacy': 0, 'terms': 0}
    for key, doc_id in (('privacy', 'privacy_policy'), ('terms', 'terms_of_service')):
        try:
            snap = db.collection('legal_docs').document(doc_id).get()
            if snap.exists:
                out[key] = int((snap.to_dict() or {}).get('version', 0) or 0)
        except Exception:
            logger.exception('_current_tos_versions_safe: %s failed', doc_id)
    return out


def _tos_status_tag(agreed_ver: int, current_ver: int) -> str:
    """동의 상태 간단 태그 — none / outdated / current / unpublished."""
    if current_ver < 1:
        return 'unpublished'
    if agreed_ver < 1:
        return 'none'
    if agreed_ver < current_ver:
        return 'outdated'
    return 'current'


def _serialize_tos_history(snap) -> dict:
    d = snap.to_dict() or {}
    return {
        'id':              snap.id,
        'agreed_at':       d.get('agreed_at', ''),
        'privacy_version': int(d.get('privacy_version', 0) or 0),
        'terms_version':   int(d.get('terms_version', 0) or 0),
        'agreed_language': d.get('agreed_language', ''),
        'ip':              d.get('ip', ''),
        'user_agent':      (d.get('user_agent') or '')[:200],
    }


@eval_v2_api.route('/admin/users/<emp_id>/details', methods=['GET'])
@api_admin_required
def api_admin_user_details(emp_id):
    """사용자 상세 정보 종합 조회. 약관 동의 상태 · 계정 보안 · NT 연동.
    모달 팝업용 — 접근 자체를 audit_logs 에 기록.
    """
    try:
        emp_id = (emp_id or '').strip()
        if not emp_id or not _EMP_ID_SAFE_RE.match(emp_id):
            return error('Invalid emp_id.', 400)

        db = get_firestore_client()

        # 1. portal_users 기본 문서
        ref = db.collection('portal_users').document(emp_id)
        snap = ref.get()
        if not snap.exists:
            return error('User not found.', 404)
        user = snap.to_dict() or {}

        # Heal-on-view: firebase_uid 필드가 비어있으면 이메일로 실제 존재 확인
        # 후 백필. Google 로그인 기능 도입 이전에 생성된 문서 등에서 "Not
        # registered" 로 잘못 표시되던 문제 자동 복구. Firebase API 가 빈 값
        # 반환하면 실제로 미가입이므로 그대로 표시.
        if not user.get('firebase_uid') and user.get('email'):
            try:
                from app.services.user_service import backfill_firebase_uid_if_empty
                healed = backfill_firebase_uid_if_empty(emp_id, user['email'])
                if healed:
                    user['firebase_uid'] = healed
            except Exception:
                logger.exception('firebase_uid heal-on-view failed emp_id=%s', emp_id)

        # 2. TOS 현재 버전 + 상태 분류
        current = _current_tos_versions_safe(db)
        agreed_privacy = int(user.get('agreed_privacy_version', 0) or 0)
        agreed_terms   = int(user.get('agreed_terms_version',   0) or 0)
        privacy_tag = _tos_status_tag(agreed_privacy, current['privacy'])
        terms_tag   = _tos_status_tag(agreed_terms,   current['terms'])

        # 3. tos_history 최근 10건 (agreed_at desc)
        history = []
        try:
            from google.cloud import firestore as _fs
            query = (ref.collection('tos_history')
                        .order_by('agreed_at', direction=_fs.Query.DESCENDING)
                        .limit(10))
            for h_snap in query.stream():
                history.append(_serialize_tos_history(h_snap))
        except Exception:
            logger.exception('tos_history query failed emp_id=%s', emp_id)

        # 4. NT Info 연동 (캐시 활용 — Sheets API 호출 없음)
        nt_present = False
        eval_folder_url = ''
        nt_name = ''
        nt_campus = ''
        nt_sheet = ''
        try:
            from app.services.nt_cache_service import get_nt_record
            rec = get_nt_record(emp_id) or {}
            if rec:
                nt_present = True
                eval_folder_url = (rec.get('eval_folder_url') or '').strip()
                nt_name   = rec.get('name', '')
                nt_campus = rec.get('campus', '')
                nt_sheet  = rec.get('sheet', '')
        except Exception:
            logger.exception('NT cache lookup failed emp_id=%s', emp_id)

        # 5. 퇴직 상태
        is_retired = False
        retire_date = ''
        try:
            r_snap = db.collection('nt_retire').document(emp_id).get()
            if r_snap.exists:
                is_retired = True
                retire_date = (r_snap.to_dict() or {}).get('retire_date', '')
        except Exception:
            logger.exception('nt_retire lookup failed emp_id=%s', emp_id)

        # 6. Audit 로그 — 관리자가 이 사용자 상세를 조회한 행위.
        # 5분 쿨다운으로 빠른 여닫기 스팸 방지 (첫 조회만 기록, 감사용 가치 유지).
        actor_email = session.get('admin_email', '')
        if _should_log_user_view(actor_email, emp_id):
            try:
                from app.services.audit_service import log_audit
                log_audit('user_details_view',
                          actor_email,
                          target=emp_id, category='general',
                          details={'scope': 'tos+security+nt'})
            except Exception:
                logger.exception('log_audit failed for user_details_view')

        return success({
            'identity': {
                'emp_id':       emp_id,
                'name':         user.get('name', ''),
                'email':        user.get('email', ''),
                'role':         user.get('role', ''),
                'campus':       user.get('campus', ''),
                'notes':        user.get('notes', ''),
                'registered_at': user.get('registered_at', ''),
                'updated_at':    user.get('updated_at', ''),
                'updated_by':    user.get('updated_by', ''),
            },
            'tos': {
                'current':           current,
                'agreed': {
                    'privacy_version': agreed_privacy,
                    'terms_version':   agreed_terms,
                    'at':              user.get('tos_agreed_at', ''),
                },
                'privacy_status':    privacy_tag,
                'terms_status':      terms_tag,
                'history':           history,
                'history_truncated': len(history) >= 10,
            },
            'security': {
                'firebase_uid':     user.get('firebase_uid', ''),
                'force_logout_at':  user.get('force_logout_at', ''),
                'last_login_at':    user.get('last_login_at', ''),
                'login_type':       user.get('login_type', ''),
            },
            'nt': {
                'present':          nt_present,
                'name':             nt_name,
                'campus':           nt_campus,
                'sheet':            nt_sheet,
                'eval_folder_url':  eval_folder_url,
            },
            'retire': {
                'is_retired':       is_retired,
                'retire_date':      retire_date,
            },
        })
    except Exception:
        logger.exception('api_admin_user_details error emp_id=%s', emp_id)
        return error('An internal error occurred.', 500)


@eval_v2_api.route('/admin/users/<emp_id>/tos-history', methods=['GET'])
@api_admin_required
def api_admin_user_tos_history(emp_id):
    """사용자 TOS 동의 이력 전체 (또는 limit 까지). "더 보기" 버튼용."""
    try:
        emp_id = (emp_id or '').strip()
        if not emp_id or not _EMP_ID_SAFE_RE.match(emp_id):
            return error('Invalid emp_id.', 400)
        try:
            limit = max(1, min(int(request.args.get('limit', 100)), 500))
        except (ValueError, TypeError):
            limit = 100

        db = get_firestore_client()
        ref = db.collection('portal_users').document(emp_id)
        if not ref.get().exists:
            return error('User not found.', 404)

        from google.cloud import firestore as _fs
        query = (ref.collection('tos_history')
                    .order_by('agreed_at', direction=_fs.Query.DESCENDING)
                    .limit(limit))
        history = [_serialize_tos_history(s) for s in query.stream()]
        return success({'history': history, 'count': len(history), 'limit': limit})
    except Exception:
        logger.exception('api_admin_user_tos_history error emp_id=%s', emp_id)
        return error('An internal error occurred.', 500)


def _csv_safe(val) -> str:
    """CSV Injection 방어 — Excel/LibreOffice 가 수식으로 해석하는 셀 방지.
    `=`, `+`, `-`, `@`, TAB, CR 로 시작하면 앞에 작은따옴표 prepend.
    user_agent / ip 등 사용자 제어 가능 필드에 반드시 적용."""
    s = '' if val is None else str(val)
    if s and s[0] in ('=', '+', '-', '@', '\t', '\r'):
        return "'" + s
    return s


@eval_v2_api.route('/admin/users/<emp_id>/tos-history.csv', methods=['GET'])
@api_admin_required
def api_admin_user_tos_history_csv(emp_id):
    """TOS 동의 이력 CSV 다운로드 (법적 감사 요청 대응).
    모든 셀에 CSV Injection 방어 적용. 파일명은 `.` 확장자 충돌 방지를 위해 sanitize.
    """
    import csv
    import re as _re_csv
    from io import StringIO
    from flask import Response
    try:
        emp_id = (emp_id or '').strip()
        if not emp_id or not _EMP_ID_SAFE_RE.match(emp_id):
            return error('Invalid emp_id.', 400)

        db = get_firestore_client()
        ref = db.collection('portal_users').document(emp_id)
        if not ref.get().exists:
            return error('User not found.', 404)

        from google.cloud import firestore as _fs
        query = (ref.collection('tos_history')
                    .order_by('agreed_at', direction=_fs.Query.DESCENDING))
        rows = [_serialize_tos_history(s) for s in query.stream()]

        buf = StringIO()
        writer = csv.writer(buf)
        writer.writerow(['emp_id', 'agreed_at', 'privacy_version', 'terms_version',
                         'agreed_language', 'ip', 'user_agent'])
        for r in rows:
            # 모든 셀에 CSV Injection 방어 (사용자 제어 가능한 user_agent/ip 포함)
            writer.writerow([
                _csv_safe(emp_id),
                _csv_safe(r['agreed_at']),
                _csv_safe(r['privacy_version']),
                _csv_safe(r['terms_version']),
                _csv_safe(r['agreed_language']),
                _csv_safe(r['ip']),
                _csv_safe(r['user_agent']),
            ])

        # 감사: CSV 다운로드도 기록
        try:
            from app.services.audit_service import log_audit
            log_audit('user_tos_history_csv_export',
                      session.get('admin_email', ''),
                      target=emp_id, category='general',
                      details={'rows': len(rows)})
        except Exception:
            logger.exception('log_audit failed for csv export')

        csv_data = buf.getvalue()
        # 파일명 sanitize — emp_id 가 `.` 포함 (이메일 형식) 시 브라우저가 확장자를
        # 잘못 파싱하는 것 방지. 영문/숫자/언더스코어/하이픈만 허용.
        safe_eid = _re_csv.sub(r'[^a-zA-Z0-9_\-]', '_', emp_id) or 'user'
        filename = f'tos_history_{safe_eid}.csv'
        return Response(
            '﻿' + csv_data,  # UTF-8 BOM for Excel 호환
            mimetype='text/csv; charset=utf-8',
            headers={'Content-Disposition': f'attachment; filename="{filename}"'},
        )
    except Exception:
        logger.exception('api_admin_user_tos_history_csv error emp_id=%s', emp_id)
        return error('An internal error occurred.', 500)
