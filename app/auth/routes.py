import logging
import os
import secrets
from urllib.parse import urlparse, parse_qs
from flask import Blueprint, session, redirect, request, render_template, jsonify
from app.extensions import limiter
from app.services.firebase_service import verify_firebase_token, fetch_nickname_from_firestore
from app.services.google_sheets import fetch_emp_db
from app.services.user_service import (
    is_emp_id_registered, register_user, get_user_by_emp_id,
    get_user_by_email, update_user_email, set_force_logout,
)
from app.services.otp_service import generate_otp, store_otp, verify_otp, send_otp_email, send_reset_email, OTP_EXPIRY_SECONDS
from app.utils.time_utils import kst_now_iso

logger = logging.getLogger(__name__)

auth_bp = Blueprint('auth', __name__)


def _set_auth_session(email: str, role: str, name: str, login_type: str, emp_id: str = '', campus: str = '') -> None:
    """인증 성공 시 세션을 재생성 후 변수를 일괄 설정 (세션 고정 공격 방어)."""
    session.clear()
    session['admin_auth']   = True
    session['admin_code']   = role
    session['admin_email']  = email
    session['emp_id']       = emp_id
    session['emp_name']     = name
    session['display_name'] = name
    session['login_type']   = login_type
    session['campus']       = campus
    session['logged_in_at'] = kst_now_iso()
    session.modified        = True


# ============================================================================
# 💡 1. 통합 로그인 페이지 연결
# ============================================================================
@auth_bp.route('/login')
def login_page():
    if session.get('admin_auth'):
        return redirect('/')
    return render_template('auth/login.html')

@auth_bp.route('/register')
def register_page():
    if session.get('admin_auth'):
        return redirect('/')
    return render_template('auth/register.html')

# ============================================================================
# 💡 2. Firebase 통합 인증 (Google + 이메일/비밀번호 공통)
# ============================================================================
@auth_bp.route('/api/auth/firebase', methods=['POST'])

@limiter.limit("5 per minute")
def firebase_auth():
    try:
        data = request.get_json(silent=True)
        if not data:
            return jsonify({'status': 'ERROR', 'message': 'Invalid request body.'}), 400
        id_token = data.get('idToken')
        login_type = data.get('loginType', 'email')  # 'email' or 'google'
        access_token = data.get('accessToken')
        
        # (기존에 무조건 토큰을 저장하던 위치. 보안을 위해 삭제되었습니다.)
            
        if not id_token:
            return jsonify({'status': 'ERROR', 'message': 'No token provided'}), 400

        decoded = verify_firebase_token(id_token)
        if not decoded:
            return jsonify({'status': 'ERROR', 'message': 'Invalid token'}), 401

        email = decoded.get('email', '').lower().strip()

        # Firestore 단일 조회로 사용자 확인 (Google Sheets API 호출 제거)
        user = get_user_by_email(email)
        # role 값 'admin' (신규) 및 'MASTER' (legacy) 모두 인정
        is_admin = user is not None and user.get('role') in ('admin', 'MASTER')

        # Google 로그인은 admin 이면 바로 통과, 일반 직원은 Users 탭 확인
        if login_type == 'google':
            if is_admin:
                _set_auth_session(email, 'admin', user.get('name', ''), 'google', user.get('emp_id', ''), user.get('campus', ''))
                return jsonify({'status': 'OK', 'redirect': '/'})
            elif user:
                # 기존 회원 → 바로 로그인
                role = user.get('role', 'NET')
                _set_auth_session(email, role, user.get('name', ''), 'google', user.get('emp_id', ''), user.get('campus', ''))
                redirect_url = '/retired' if role in ('retired', '퇴사') else '/'
                return jsonify({'status': 'OK', 'redirect': redirect_url})
            else:
                # 신규 → 직원 검증 팝업 요청
                session['pending_google_email'] = email
                session.modified = True
                return jsonify({'status': 'STAFF_VERIFY_REQUIRED', 'message': 'Please verify your identity.'})

        # 이메일 로그인은 OTP 발송 후 2FA 진행
        otp = generate_otp()
        store_otp(session, otp)
        session['pending_email'] = email
        session['pending_admin_code'] = 'admin' if is_admin else 'NET'

        success = send_otp_email(email, otp)
        if not success:
            return jsonify({'status': 'ERROR', 'message': 'Failed to send OTP email.'}), 500

        return jsonify({'status': 'OTP_REQUIRED', 'message': 'OTP sent to your email.'})

    except Exception as e:
        logger.exception('Firebase auth error: %s', e)
        return jsonify({'status': 'ERROR', 'message': 'Authentication failed'}), 400


@auth_bp.route('/api/auth/verify-otp', methods=['POST'])
@limiter.limit("5 per minute")
def verify_otp_route():
    try:
        input_otp = (request.get_json(silent=True) or {}).get('otp', '').strip()
        valid, message = verify_otp(session, input_otp)

        if not valid:
            return jsonify({'status': 'ERROR', 'message': message}), 401

        email = session.pop('pending_email', '')
        session.pop('pending_admin_code', None)  # 세션 값 무시, Firestore 기준

        db_user = get_user_by_email(email)
        if not db_user:
            return jsonify({'status': 'ERROR', 'message': 'User not found.'}), 401

        # Firestore role을 유일한 권한 소스로 사용
        admin_code = db_user.get('role', 'NET')
        if admin_code == 'MASTER':  # legacy → 신규 호환
            admin_code = 'admin'

        name = db_user.get('name', '')
        emp_id = db_user.get('emp_id', '')
        campus = db_user.get('campus', '')
        _set_auth_session(email, admin_code, name, 'email', emp_id, campus)
        redirect_url = '/retired' if admin_code in ('retired', '퇴사') else '/'
        return jsonify({'status': 'OK', 'redirect': redirect_url})

    except Exception as e:
        logger.exception('OTP verify error: %s', e)
        return jsonify({'status': 'ERROR', 'message': 'Verification failed'}), 400

# ============================================================================
# 💡 3. 회원가입 API
# ============================================================================
@auth_bp.route('/api/auth/verify-emp', methods=['POST'])
@limiter.limit("5 per minute")
def verify_emp():
    try:
        _body = request.get_json(silent=True) or {}
        emp_id = _body.get('empId', '').strip()
        passport = _body.get('passport', '').strip().upper()

        if not emp_id:
            return jsonify({'status': 'ERROR', 'message': 'Please enter your Employee ID.'}), 400
        if not passport:
            return jsonify({'status': 'ERROR', 'message': 'Please enter your Passport Number.'}), 400

        emp_map = fetch_emp_db()
        db_entry = emp_map.get(emp_id)
        db_passport = db_entry['passport'].upper() if db_entry else ''

        # 사번·여권 모두 검증 후 동일 메시지 반환 (사번 존재 여부 노출 방지)
        if not db_entry or not db_passport or db_passport != passport:
            return jsonify({'status': 'ERROR', 'message': 'Employee ID or Passport Number does not match.'}), 401

        if is_emp_id_registered(emp_id):
            return jsonify({'status': 'ERROR', 'message': 'This Employee ID is already registered.'}), 409

        name = emp_map[emp_id]['name']

        def mask_word(word):
            if len(word) <= 2:
                return word[0] + '*'
            return word[:2] + '*' * (len(word) - 2)
        masked_name = ' '.join(mask_word(w) for w in name.split())
        return jsonify({'status': 'OK', 'name': masked_name, 'role': 'NET'})

    except Exception as e:
        logger.exception('verify_emp error: %s', e)
        return jsonify({'status': 'ERROR', 'message': 'Verification failed.'}), 400

@auth_bp.route('/api/auth/verify-staff', methods=['POST'])
@limiter.limit("5 per minute")
def verify_staff():
    """Google 로그인 후 직원 검증 (사번 + 여권번호)"""
    try:
        _body = request.get_json(silent=True) or {}
        emp_id = _body.get('empId', '').strip()
        passport = _body.get('passport', '').strip().upper()

        if not emp_id or not passport:
            return jsonify({'status': 'ERROR', 'message': 'Please fill in all fields.'}), 400

        emp_map = fetch_emp_db()
        db_entry = emp_map.get(emp_id)
        db_passport = db_entry['passport'].upper() if db_entry else ''

        # 사번·여권 모두 검증 후 동일 메시지 반환 (사번 존재 여부 노출 방지)
        if not db_entry or not db_passport or db_passport != passport:
            return jsonify({'status': 'ERROR', 'message': 'Employee ID or Passport Number does not match.'}), 401

        # 검증 성공 → 1회용 토큰으로 세션에 저장 (단순 플래그 대신 재사용 불가 토큰)
        # emp_id/name도 토큰과 함께 묶어서 저장 — 세션 변조로 다른 사번 주입 방지
        name = emp_map[emp_id]['name']
        staff_token = secrets.token_urlsafe(32)
        session['staff_verified'] = {
            'emp_id': emp_id,
            'emp_name': name,
            'token': staff_token,
        }
        session.modified = True

        return jsonify({'status': 'OK', 'message': 'Verified.', 'staffToken': staff_token})

    except Exception as e:
        logger.exception('verify_staff error: %s', e)
        return jsonify({'status': 'ERROR', 'message': 'Verification failed.'}), 400


@auth_bp.route('/api/auth/complete-google-login', methods=['POST'])
@limiter.limit("10 per minute")
def complete_google_login():
    """직원 검증 완료 후 Google 로그인 세션 생성 + Users 탭 저장"""
    try:
        data = request.get_json(silent=True) or {}
        submitted_token = data.get('staffToken', '')
        verified = session.get('staff_verified') or {}
        stored_token = verified.get('token', '')

        # 세션 토큰과 요청 토큰 일치 여부 검증 (1회용)
        if not stored_token or not submitted_token:
            return jsonify({'status': 'ERROR', 'message': 'Verification required.'}), 401
        if not secrets.compare_digest(stored_token, submitted_token):
            return jsonify({'status': 'ERROR', 'message': 'Verification required.'}), 401
        if not session.get('pending_google_email'):
            return jsonify({'status': 'ERROR', 'message': 'Verification required.'}), 401

        email = session.pop('pending_google_email', '')
        emp_id = verified.get('emp_id', '')
        name = verified.get('emp_name', '')
        session.pop('staff_verified', None)

        # Firebase UID 조회
        from firebase_admin import auth as firebase_auth
        try:
            fb_user = firebase_auth.get_user_by_email(email)
            firebase_uid = fb_user.uid
        except Exception:
            firebase_uid = ''

        # Firestore에 저장 — 신규 등록 시도. 이미 문서가 존재하면 False 반환.
        role = 'NET'
        created = register_user(emp_id, name, role, firebase_uid, email)

        # 기존 문서가 이미 있는 케이스 (Google 로그인 기능 도입 이전에 생성된
        # 계정 등): firebase_uid 가 비어있으면 이번에 받은 값으로 백필.
        # register_user 는 기존 문서를 건드리지 않으므로 별도 update 필요.
        if not created and firebase_uid:
            try:
                existing = get_user_by_emp_id(emp_id)
                if existing and not existing.get('firebase_uid'):
                    from app.services.firebase_service import get_firestore_client
                    get_firestore_client().collection('portal_users').document(emp_id.lower()).update({
                        'firebase_uid': firebase_uid,
                        'firebase_uid_checked_at': kst_now_iso(),
                    })
                    logger.info('firebase_uid backfilled on google login: %s', emp_id)
                    # 감사 로그 — heal-on-view 경로와 일관
                    try:
                        from app.services.audit_service import log_audit
                        log_audit('firebase_uid_auto_backfill', actor='system:google_login',
                                  target=emp_id, category='general',
                                  details={'email': email, 'source': 'complete_google_login'})
                    except Exception:
                        logger.exception('log_audit for firebase_uid_auto_backfill failed emp_id=%s', emp_id)
                # 기존 role 이 admin/MASTER 등 non-NET 이면 그 역할 유지
                if existing:
                    existing_role = existing.get('role') or role
                    role = existing_role
            except Exception:
                logger.exception('firebase_uid backfill on google login failed emp_id=%s', emp_id)

        # Firebase Custom Claims 설정 (재로그인 시 활용)
        if firebase_uid:
            firebase_auth.set_custom_user_claims(firebase_uid, {
                'emp_id': emp_id,
                'name': name,
                'role': role
            })

        _set_auth_session(email, role, name, 'google', emp_id, '')
        return jsonify({'status': 'OK', 'redirect': '/'})

    except Exception as e:
        logger.exception('complete_google_login error: %s', e)
        return jsonify({'status': 'ERROR', 'message': 'Login failed.'}), 400

@auth_bp.route('/api/auth/register-send-otp', methods=['POST'])

@limiter.limit("3 per minute")
def register_send_otp():
    """회원가입 1단계: 사번/토큰 검증 후 OTP 발송"""
    try:
        _body = request.get_json(silent=True) or {}
        id_token = _body.get('idToken')
        emp_id = _body.get('empId', '').strip()

        if not id_token or not emp_id:
            return jsonify({'status': 'ERROR', 'message': 'Missing required fields.'}), 400

        decoded = verify_firebase_token(id_token)
        if not decoded:
            return jsonify({'status': 'ERROR', 'message': 'Invalid token.'}), 401

        email = decoded.get('email', '').lower().strip()

        emp_map = fetch_emp_db()
        if emp_id not in emp_map:
            return jsonify({'status': 'ERROR', 'message': 'Employee ID not found.'}), 404

        # 여권번호 재검증 (이중 확인)
        passport = _body.get('passport', '').strip().upper()
        db_passport = emp_map[emp_id]['passport'].upper()
        if not db_passport or db_passport != passport:
            return jsonify({'status': 'ERROR', 'message': 'Employee ID or Passport Number does not match.'}), 401

        if is_emp_id_registered(emp_id):
            return jsonify({'status': 'ERROR', 'message': 'This Employee ID is already registered.'}), 409

        # OTP 생성 및 발송
        otp = generate_otp()
        store_otp(session, otp)
        session['pending_register_email'] = email
        session['pending_register_emp_id'] = emp_id
        session['pending_register_uid'] = decoded.get('uid')

        success = send_otp_email(email, otp)
        if not success:
            return jsonify({'status': 'ERROR', 'message': 'Failed to send OTP email.'}), 500

        return jsonify({'status': 'OTP_REQUIRED', 'message': 'OTP sent to your email.'})

    except Exception as e:
        logger.exception('register_send_otp error: %s', e)
        return jsonify({'status': 'ERROR', 'message': 'Registration failed.'}), 400


@auth_bp.route('/api/auth/register-verify-otp', methods=['POST'])
@limiter.limit("5 per minute")
def register_verify_otp():
    """회원가입 2단계: OTP 검증 후 회원가입 완료"""
    try:
        input_otp = (request.get_json(silent=True) or {}).get('otp', '').strip()
        valid, message = verify_otp(session, input_otp)

        if not valid:
            return jsonify({'status': 'ERROR', 'message': message}), 401

        email = session.pop('pending_register_email', '')
        emp_id = session.pop('pending_register_emp_id', '')
        firebase_uid = session.pop('pending_register_uid', '')

        # 🔒 최종 중복 체크 (OTP 검증 후에도 재확인)
        if is_emp_id_registered(emp_id):
            return jsonify({'status': 'ERROR', 'message': 'This Employee ID is already registered.'}), 409

        emp_map = fetch_emp_db()
        name = emp_map.get(emp_id, {}).get('name', '')
        role = 'NET'

        from firebase_admin import auth as firebase_auth
        # UID가 실제로 해당 이메일과 일치하는지 검증 (타 계정 권한 탈취 방지)
        try:
            fb_user = firebase_auth.get_user(firebase_uid)
            if fb_user.email.lower() != email.lower():
                logger.error('UID-email mismatch: uid=%s, expected=%s, got=%s', firebase_uid, email, fb_user.email)
                return jsonify({'status': 'ERROR', 'message': 'Registration failed.'}), 400
        except Exception:
            return jsonify({'status': 'ERROR', 'message': 'Registration failed.'}), 400

        firebase_auth.set_custom_user_claims(firebase_uid, {
            'emp_id': emp_id,
            'name': name,
            'role': role
        })

        success = register_user(emp_id, name, role, firebase_uid, email)
        if not success:
            return jsonify({'status': 'ERROR', 'message': 'Failed to save user data.'}), 500

        from app.services.audit_service import log_audit
        log_audit('user_register', email, target=emp_id, details={'name': name, 'role': role}, category='auth')

        _set_auth_session(email, role, name, 'email', emp_id, '')
        return jsonify({'status': 'OK', 'redirect': '/'})

    except Exception as e:
        logger.exception('register_verify_otp error: %s', e)
        return jsonify({'status': 'ERROR', 'message': 'Verification failed.'}), 400

# ============================================================================
# 💡 5. 계정 찾기 API (로그인 없이 접근 가능)
# ============================================================================
@auth_bp.route('/find-account')
def find_account_page():
    return render_template('auth/find_account.html')

@auth_bp.route('/api/auth/find-email', methods=['POST'])
@limiter.limit("5 per minute")
def find_email():
    """사번으로 이메일 찾기 (마스킹 처리)"""
    try:
        emp_id = (request.get_json(silent=True) or {}).get('empId', '').strip()
        if not emp_id:
            return jsonify({'status': 'ERROR', 'message': 'Please enter your Employee ID.'}), 400

        user = get_user_by_emp_id(emp_id)
        if not user:
            return jsonify({'status': 'ERROR', 'message': 'No account found for this Employee ID.'}), 404

        email = user['email']
        # 이메일 마스킹 처리 (h****@g***.com)
        parts = email.split('@')
        local = parts[0]
        domain_parts = parts[1].split('.') if len(parts) > 1 else ['***']
        masked_local = local[0] + '****' if len(local) > 1 else '****'
        masked_domain = domain_parts[0][0] + '***.' + '.'.join(domain_parts[1:]) if len(domain_parts) > 1 and domain_parts[0] else '****'
        masked = masked_local + '@' + masked_domain

        return jsonify({'status': 'OK', 'maskedEmail': masked, 'name': user['name']})

    except Exception as e:
        logger.exception('find_email error: %s', e)
        return jsonify({'status': 'ERROR', 'message': 'Failed to find account.'}), 400


@auth_bp.route('/set-password')
def set_password_page():
    oob_code = request.args.get('oobCode', '')
    if not oob_code:
        return redirect('/login')
    return render_template('auth/set_password.html', oob_code=oob_code)


@auth_bp.route('/api/auth/reset-password-by-emp', methods=['POST'])
@limiter.limit("2 per minute")
def reset_password_by_emp():
    """사번으로 비밀번호 재설정 이메일 발송"""
    try:
        emp_id = (request.get_json(silent=True) or {}).get('empId', '').strip()
        if not emp_id:
            return jsonify({'status': 'ERROR', 'message': 'Please enter your Employee ID.'}), 400

        user = get_user_by_emp_id(emp_id)
        # 사번 존재 여부를 노출하지 않고 동일 메시지 반환
        if not user:
            return jsonify({'status': 'OK', 'message': 'If an account exists for this Employee ID, a reset email has been sent.'})

        from firebase_admin import auth as firebase_auth
        firebase_link = firebase_auth.generate_password_reset_link(user['email'])
        reset_link = _build_custom_reset_link(firebase_link, request.url_root)
        send_reset_email(user['email'], reset_link)
        return jsonify({'status': 'OK', 'message': 'If an account exists for this Employee ID, a reset email has been sent.'})

    except Exception as e:
        logger.exception('reset_password_by_emp error: %s', e)
        return jsonify({'status': 'ERROR', 'message': 'Failed to send reset email.'}), 400


def _build_custom_reset_link(firebase_link, url_root):
    """Firebase reset link에서 oobCode를 추출해 custom /set-password URL로 변환"""
    try:
        params = parse_qs(urlparse(firebase_link).query)
        oob_code = params.get('oobCode', [''])[0]
        if oob_code:
            return f"{url_root.rstrip('/')}/set-password?oobCode={oob_code}"
    except Exception:
        pass
    return firebase_link


# ============================================================================
# 💡 6. 계정 설정 API (로그인 필요)
# ============================================================================
@auth_bp.route('/account')
def account_page():
    if not session.get('admin_auth'):
        return redirect('/login')
    return render_template('auth/account.html')


@auth_bp.route('/api/auth/account/send-otp', methods=['POST'])

@limiter.limit("3 per minute")
def account_send_otp():
    """계정 설정 변경 전 OTP 발송"""
    if not session.get('admin_auth'):
        return jsonify({'status': 'ERROR', 'message': 'Unauthorized'}), 401
    try:
        import time
        otp = generate_otp()
        session['account_otp_code'] = otp
        session['account_otp_expires_at'] = time.time() + OTP_EXPIRY_SECONDS
        session['account_otp_attempts'] = 0  # 새 OTP 발송 시 시도 횟수 초기화
        session.modified = True
        success = send_otp_email(session['admin_email'], otp)
        if not success:
            return jsonify({'status': 'ERROR', 'message': 'Failed to send OTP.'}), 500
        return jsonify({'status': 'OK', 'message': 'OTP sent.'})
    except Exception as e:
        logger.exception('account_send_otp error: %s', e)
        return jsonify({'status': 'ERROR', 'message': 'Failed to send OTP.'}), 400


@auth_bp.route('/api/auth/account/change-email', methods=['POST'])
@limiter.limit("10 per minute")
def change_email():
    """이메일 변경 1단계: 현재 이메일 OTP 검증 후 새 이메일로 인증코드 발송"""
    if not session.get('admin_auth'):
        return jsonify({'status': 'ERROR', 'message': 'Unauthorized'}), 401
    try:
        _body = request.get_json(silent=True) or {}
        otp_input = _body.get('otp', '').strip()
        new_email = _body.get('newEmail', '').strip().lower()

        if not new_email:
            return jsonify({'status': 'ERROR', 'message': 'Please enter a new email.'}), 400

        import time, hmac as _hmac
        stored = session.get('account_otp_code')
        expires_at = session.get('account_otp_expires_at', 0)
        if not stored:
            return jsonify({'status': 'ERROR', 'message': 'OTP not found.'}), 401
        if time.time() > expires_at:
            session.pop('account_otp_code', None)
            session.pop('account_otp_expires_at', None)
            session.pop('account_otp_attempts', None)
            return jsonify({'status': 'ERROR', 'message': 'OTP expired.'}), 401
        attempts = session.get('account_otp_attempts', 0) + 1
        session['account_otp_attempts'] = attempts
        if attempts > 5:
            session.pop('account_otp_code', None)
            session.pop('account_otp_expires_at', None)
            session.pop('account_otp_attempts', None)
            return jsonify({'status': 'ERROR', 'message': 'Too many attempts. Please request a new OTP.'}), 429
        if not _hmac.compare_digest(stored, otp_input):
            return jsonify({'status': 'ERROR', 'message': f'Invalid OTP. {5 - attempts} attempt(s) remaining.'}), 401
        session.pop('account_otp_code', None)
        session.pop('account_otp_expires_at', None)
        session.pop('account_otp_attempts', None)

        # 새 이메일로 인증코드 발송
        new_email_otp = generate_otp()
        session['new_email_otp_code'] = new_email_otp
        session['new_email_otp_expires_at'] = time.time() + OTP_EXPIRY_SECONDS
        session['pending_new_email'] = new_email
        session['new_email_otp_attempts'] = 0
        session.modified = True

        success = send_otp_email(new_email, new_email_otp)
        if not success:
            return jsonify({'status': 'ERROR', 'message': 'Failed to send verification code to new email.'}), 500

        return jsonify({'status': 'NEW_EMAIL_VERIFY_REQUIRED', 'message': 'Verification code sent to new email.'})

    except Exception as e:
        logger.exception('change_email error: %s', e)
        return jsonify({'status': 'ERROR', 'message': 'Failed to update email.'}), 400


@auth_bp.route('/api/auth/account/verify-new-email', methods=['POST'])

@limiter.limit("10 per minute")
def verify_new_email():
    """이메일 변경 2단계: 새 이메일 인증코드 검증 후 변경 완료"""
    if not session.get('admin_auth'):
        return jsonify({'status': 'ERROR', 'message': 'Unauthorized'}), 401
    try:
        otp_input = (request.get_json(silent=True) or {}).get('otp', '').strip()

        import time
        stored = session.get('new_email_otp_code')
        expires_at = session.get('new_email_otp_expires_at', 0)
        new_email = session.get('pending_new_email', '')

        import hmac as _hmac
        if not stored:
            return jsonify({'status': 'ERROR', 'message': 'OTP not found.'}), 401
        if time.time() > expires_at:
            session.pop('new_email_otp_code', None)
            session.pop('new_email_otp_expires_at', None)
            session.pop('pending_new_email', None)
            session.pop('new_email_otp_attempts', None)
            return jsonify({'status': 'ERROR', 'message': 'OTP expired.'}), 401
        attempts = session.get('new_email_otp_attempts', 0) + 1
        session['new_email_otp_attempts'] = attempts
        if attempts > 5:
            session.pop('new_email_otp_code', None)
            session.pop('new_email_otp_expires_at', None)
            session.pop('pending_new_email', None)
            session.pop('new_email_otp_attempts', None)
            return jsonify({'status': 'ERROR', 'message': 'Too many attempts. Please request a new OTP.'}), 429
        if not _hmac.compare_digest(stored, otp_input):
            # 실패 시 pending 상태 즉시 초기화 (상태 머신 우회 방지)
            session.pop('new_email_otp_code', None)
            session.pop('new_email_otp_expires_at', None)
            session.pop('pending_new_email', None)
            session.pop('new_email_otp_attempts', None)
            return jsonify({'status': 'ERROR', 'message': 'Invalid OTP. Please request a new verification code.'}), 401

        session.pop('new_email_otp_code', None)
        session.pop('new_email_otp_expires_at', None)
        session.pop('pending_new_email', None)
        session.pop('new_email_otp_attempts', None)

        # 이메일 중복 재확인 (OTP 발송~검증 사이 다른 사용자가 같은 이메일 등록 방지)
        existing = get_user_by_email(new_email)
        if existing and existing.get('emp_id') != session.get('emp_id'):
            return jsonify({'status': 'ERROR', 'message': 'This email is already registered by another user.'}), 409

        # Firebase 이메일 업데이트
        from firebase_admin import auth as firebase_auth
        user = firebase_auth.get_user_by_email(session['admin_email'])
        firebase_auth.update_user(user.uid, email=new_email)

        # Sheets Users 탭 이메일 업데이트
        emp_id = session.get('emp_id', '')
        if emp_id:
            update_user_email(emp_id, new_email)
            # 다른 기기 세션 무효화 — 현재 세션은 logged_in_at 갱신으로 유지
            set_force_logout(emp_id)

        session['admin_email'] = new_email
        session['logged_in_at'] = kst_now_iso()
        session.modified = True
        return jsonify({'status': 'OK', 'message': 'Email updated successfully.'})

    except Exception as e:
        logger.exception('verify_new_email error: %s', e)
        return jsonify({'status': 'ERROR', 'message': 'Failed to update email.'}), 400


@auth_bp.route('/api/auth/account/change-password', methods=['POST'])
@limiter.limit("10 per minute")
def change_password():
    """비밀번호 변경 (OTP 검증 후)"""
    if not session.get('admin_auth'):
        return jsonify({'status': 'ERROR', 'message': 'Unauthorized'}), 401
    try:
        _body = request.get_json(silent=True) or {}
        otp_input = _body.get('otp', '').strip()
        new_password = _body.get('newPassword', '')

        if len(new_password) < 8:
            return jsonify({'status': 'ERROR', 'message': 'Password must be at least 8 characters.'}), 400
        if len(new_password) > 128:
            return jsonify({'status': 'ERROR', 'message': 'Password must not exceed 128 characters.'}), 400

        import time, hmac as _hmac
        stored = session.get('account_otp_code')
        expires_at = session.get('account_otp_expires_at', 0)
        if not stored:
            return jsonify({'status': 'ERROR', 'message': 'OTP not found.'}), 401
        if time.time() > expires_at:
            session.pop('account_otp_code', None)
            session.pop('account_otp_expires_at', None)
            session.pop('account_otp_attempts', None)
            return jsonify({'status': 'ERROR', 'message': 'OTP expired.'}), 401
        attempts = session.get('account_otp_attempts', 0) + 1
        session['account_otp_attempts'] = attempts
        if attempts > 5:
            session.pop('account_otp_code', None)
            session.pop('account_otp_expires_at', None)
            session.pop('account_otp_attempts', None)
            return jsonify({'status': 'ERROR', 'message': 'Too many attempts. Please request a new OTP.'}), 429
        if not _hmac.compare_digest(stored, otp_input):
            return jsonify({'status': 'ERROR', 'message': f'Invalid OTP. {5 - attempts} attempt(s) remaining.'}), 401
        session.pop('account_otp_code', None)
        session.pop('account_otp_expires_at', None)
        session.pop('account_otp_attempts', None)

        from firebase_admin import auth as firebase_auth
        user = firebase_auth.get_user_by_email(session['admin_email'])
        firebase_auth.update_user(user.uid, password=new_password)

        emp_id = session.get('emp_id', '')
        if emp_id:
            set_force_logout(emp_id)

        return jsonify({'status': 'OK', 'message': 'Password updated successfully.'})

    except Exception as e:
        logger.exception('change_password error: %s', e)
        return jsonify({'status': 'ERROR', 'message': 'Failed to update password.'}), 400


@auth_bp.route('/api/auth/account/delete', methods=['POST'])
@limiter.limit("5 per minute")
def delete_account():
    """계정 탈퇴 (OTP 검증 후)"""
    if not session.get('admin_auth'):
        return jsonify({'status': 'ERROR', 'message': 'Unauthorized'}), 401
    try:
        otp_input = (request.get_json(silent=True) or {}).get('otp', '').strip()
        import time, hmac as _hmac
        stored = session.get('account_otp_code')
        expires_at = session.get('account_otp_expires_at', 0)
        if not stored:
            return jsonify({'status': 'ERROR', 'message': 'OTP not found.'}), 401
        if time.time() > expires_at:
            session.pop('account_otp_code', None)
            session.pop('account_otp_expires_at', None)
            session.pop('account_otp_attempts', None)
            return jsonify({'status': 'ERROR', 'message': 'OTP expired.'}), 401
        attempts = session.get('account_otp_attempts', 0) + 1
        session['account_otp_attempts'] = attempts
        if attempts > 5:
            session.pop('account_otp_code', None)
            session.pop('account_otp_expires_at', None)
            session.pop('account_otp_attempts', None)
            return jsonify({'status': 'ERROR', 'message': 'Too many attempts. Please request a new OTP.'}), 429
        if not _hmac.compare_digest(stored, otp_input):
            return jsonify({'status': 'ERROR', 'message': f'Invalid OTP. {5 - attempts} attempt(s) remaining.'}), 401
        session.pop('account_otp_code', None)
        session.pop('account_otp_expires_at', None)
        session.pop('account_otp_attempts', None)

        from firebase_admin import auth as firebase_auth
        user = firebase_auth.get_user_by_email(session['admin_email'])
        firebase_auth.delete_user(user.uid)

        session.clear()
        return jsonify({'status': 'OK', 'redirect': '/login'})

    except Exception as e:
        logger.exception('delete_account error: %s', e)
        return jsonify({'status': 'ERROR', 'message': 'Failed to delete account.'}), 400

# ============================================================================
# 💡 7. 구글 계정 변경 API
# ============================================================================
# 💡 7. 구글 계정 변경 API
# ============================================================================
@auth_bp.route('/api/auth/account/change-google', methods=['POST'])
@limiter.limit("10 per minute")
def change_google_account():
    """구글 계정 변경 1단계: OTP 검증 + 새 구글 계정 선택 + 새 이메일로 인증코드 발송"""
    if not session.get('admin_auth'):
        return jsonify({'status': 'ERROR', 'message': 'Unauthorized'}), 401
    if session.get('login_type') != 'google':
        return jsonify({'status': 'ERROR', 'message': 'This feature is only available for Google accounts.'}), 403
    try:
        _body = request.get_json(silent=True) or {}
        id_token = _body.get('idToken')
        otp_input = _body.get('otp', '').strip()

        import time, hmac as _hmac
        stored = session.get('account_otp_code')
        expires_at = session.get('account_otp_expires_at', 0)
        if not stored:
            return jsonify({'status': 'ERROR', 'message': 'OTP not found.'}), 401
        if time.time() > expires_at:
            session.pop('account_otp_code', None)
            session.pop('account_otp_expires_at', None)
            session.pop('account_otp_attempts', None)
            return jsonify({'status': 'ERROR', 'message': 'OTP expired.'}), 401
        attempts = session.get('account_otp_attempts', 0) + 1
        session['account_otp_attempts'] = attempts
        if attempts > 5:
            session.pop('account_otp_code', None)
            session.pop('account_otp_expires_at', None)
            session.pop('account_otp_attempts', None)
            return jsonify({'status': 'ERROR', 'message': 'Too many attempts. Please request a new OTP.'}), 429
        if not _hmac.compare_digest(stored, otp_input):
            return jsonify({'status': 'ERROR', 'message': f'Invalid OTP. {5 - attempts} attempt(s) remaining.'}), 401
        session.pop('account_otp_code', None)
        session.pop('account_otp_expires_at', None)
        session.pop('account_otp_attempts', None)

        decoded = verify_firebase_token(id_token)
        if not decoded:
            return jsonify({'status': 'ERROR', 'message': 'Invalid Google token.'}), 401

        new_email = decoded.get('email', '').lower().strip()
        new_uid = decoded.get('uid', '')

        # 새 이메일이 이미 다른 사람 계정으로 등록됐는지 확인
        existing = get_user_by_email(new_email)
        if existing and existing.get('emp_id') != session.get('emp_id'):
            return jsonify({'status': 'ERROR', 'message': 'This Google account is already registered by another user.'}), 409

        # 새 구글 계정으로 인증코드 발송
        new_google_otp = generate_otp()
        session['new_google_otp_code'] = new_google_otp
        session['new_google_otp_expires_at'] = time.time() + OTP_EXPIRY_SECONDS
        session['pending_new_google_email'] = new_email
        session['pending_new_google_uid'] = new_uid
        session['new_google_otp_attempts'] = 0
        session.modified = True

        success = send_otp_email(new_email, new_google_otp)
        if not success:
            return jsonify({'status': 'ERROR', 'message': 'Failed to send verification code to new Google account.'}), 500

        return jsonify({'status': 'NEW_GOOGLE_VERIFY_REQUIRED', 'message': 'Verification code sent to new Google account email.'})

    except Exception as e:
        logger.exception('change_google_account error: %s', e)
        return jsonify({'status': 'ERROR', 'message': 'Failed to change Google account.'}), 400


@auth_bp.route('/api/auth/account/verify-new-google', methods=['POST'])
@limiter.limit("10 per minute")
def verify_new_google():
    """구글 계정 변경 2단계: 새 구글 이메일 인증코드 검증 후 변경 완료"""
    if not session.get('admin_auth'):
        return jsonify({'status': 'ERROR', 'message': 'Unauthorized'}), 401
    try:
        otp_input = (request.get_json(silent=True) or {}).get('otp', '').strip()

        import time, hmac as _hmac
        stored = session.get('new_google_otp_code')
        expires_at = session.get('new_google_otp_expires_at', 0)
        new_email = session.get('pending_new_google_email', '')
        new_uid = session.get('pending_new_google_uid', '')

        if not stored:
            return jsonify({'status': 'ERROR', 'message': 'OTP not found.'}), 401
        if time.time() > expires_at:
            session.pop('new_google_otp_code', None)
            session.pop('new_google_otp_expires_at', None)
            session.pop('pending_new_google_email', None)
            session.pop('pending_new_google_uid', None)
            session.pop('new_google_otp_attempts', None)
            return jsonify({'status': 'ERROR', 'message': 'OTP expired.'}), 401
        attempts = session.get('new_google_otp_attempts', 0) + 1
        session['new_google_otp_attempts'] = attempts
        if attempts > 5:
            session.pop('new_google_otp_code', None)
            session.pop('new_google_otp_expires_at', None)
            session.pop('pending_new_google_email', None)
            session.pop('pending_new_google_uid', None)
            session.pop('new_google_otp_attempts', None)
            return jsonify({'status': 'ERROR', 'message': 'Too many attempts. Please request a new OTP.'}), 429
        if not _hmac.compare_digest(stored, otp_input):
            return jsonify({'status': 'ERROR', 'message': f'Invalid OTP. {5 - attempts} attempt(s) remaining.'}), 401

        session.pop('new_google_otp_code', None)
        session.pop('new_google_otp_expires_at', None)
        session.pop('pending_new_google_email', None)
        session.pop('pending_new_google_uid', None)
        session.pop('new_google_otp_attempts', None)

        old_email = session['admin_email']
        emp_id = session.get('emp_id', '')
        name = session.get('emp_name', '')
        role = session.get('admin_code', 'NET')

        from firebase_admin import auth as firebase_auth
        try:
            old_user = firebase_auth.get_user_by_email(old_email)
            firebase_auth.delete_user(old_user.uid)
        except Exception as e:
            logger.exception('Old account delete error: %s', e)

        firebase_auth.set_custom_user_claims(new_uid, {
            'emp_id': emp_id,
            'name': name,
            'role': role
        })

        update_user_email(emp_id, new_email)
        # 다른 기기 세션 무효화 — 현재 세션은 logged_in_at 갱신으로 유지
        if emp_id:
            set_force_logout(emp_id)

        session['admin_email'] = new_email
        session['logged_in_at'] = kst_now_iso()
        session.modified = True

        return jsonify({'status': 'OK', 'message': 'Google account updated successfully.'})

    except Exception as e:
        logger.exception('verify_new_google error: %s', e)
        return jsonify({'status': 'ERROR', 'message': 'Failed to update Google account.'}), 400

# ============================================================================
# 💡 개인정보 처리방침 / 이용약관 동의
# ============================================================================

def _get_current_tos_versions():
    """legal_docs 컬렉션에서 현재 발효 중인 privacy/terms 버전 + 중대개정 임계값 조회.

    반환: {
      'privacy': int,  # 현재 발행 버전
      'terms':   int,
      'force_privacy': int,  # 중대 개정 기준 — 기존 동의자 agreed < force 면 재동의 필요
      'force_terms':   int,
    }
    문서가 없으면 version=0 (미발행 → 동의 모달 미표시).
    force_*_version 필드가 없으면 0 (중대 개정 플래그 없음 = 재동의 불필요).
    """
    from app.services.firebase_service import get_firestore_client
    db = get_firestore_client()
    versions = {'privacy': 0, 'terms': 0, 'force_privacy': 0, 'force_terms': 0}
    for key, doc_id in (('privacy', 'privacy_policy'), ('terms', 'terms_of_service')):
        try:
            snap = db.collection('legal_docs').document(doc_id).get()
            if snap.exists:
                d = snap.to_dict() or {}
                versions[key] = int(d.get('version', 0) or 0)
                versions[f'force_{key}'] = int(d.get('force_agreement_version', 0) or 0)
        except Exception:
            logger.exception('_get_current_tos_versions: %s read failed', doc_id)
    return versions


def _get_user_agreed_versions(emp_id: str):
    """portal_users/{emp_id} 에서 agreed_privacy_version / agreed_terms_version 조회.
    없으면 0 (미동의).
    """
    if not emp_id:
        return {'privacy': 0, 'terms': 0}
    from app.services.firebase_service import get_firestore_client
    db = get_firestore_client()
    try:
        snap = db.collection('portal_users').document(emp_id).get()
        if snap.exists:
            d = snap.to_dict() or {}
            return {
                'privacy': int(d.get('agreed_privacy_version', 0) or 0),
                'terms':   int(d.get('agreed_terms_version', 0) or 0),
            }
    except Exception:
        logger.exception('_get_user_agreed_versions: read failed emp_id=%s', emp_id)
    return {'privacy': 0, 'terms': 0}


@auth_bp.route('/api/auth/tos-content', methods=['GET'])
def api_tos_content():
    """약관 모달 UI 에 표시할 개인정보 처리방침 + 이용약관 내용 반환.
    legal_docs/privacy_policy, legal_docs/terms_of_service 의 articles 배열을 그대로 전달.
    """
    try:
        from app.services.firebase_service import get_firestore_client
        db = get_firestore_client()
        out = {'status': 'OK'}
        for key, doc_id in (('privacy', 'privacy_policy'), ('terms', 'terms_of_service')):
            snap = db.collection('legal_docs').document(doc_id).get()
            if snap.exists:
                d = snap.to_dict() or {}
                out[key] = {
                    'version':        int(d.get('version', 0) or 0),
                    'effective_date': d.get('effective_date', ''),
                    'articles':       d.get('articles', []) or [],
                }
            else:
                out[key] = {'version': 0, 'effective_date': '', 'articles': []}
        return jsonify(out)
    except Exception:
        logger.exception('api_tos_content error')
        return jsonify({'status': 'ERROR', 'message': 'Failed to load agreement content.'}), 500


@auth_bp.route('/api/auth/tos-status', methods=['GET'])
def api_tos_status():
    """현재 약관/개인정보 동의 상태 조회.

    팝업 표시 판정 정책 (2026-04-23):
    1) **신규 유저** (`agreed_*_version` 모두 0) → first-time 동의 필요, 팝업 표시.
    2) **중대 개정 (major revision)** — legal_docs 의 `force_agreement_version`
       이 올라갔고 기존 동의자의 `agreed_*_version` 이 그보다 낮으면 재동의 필요.
    3) **일반 개정** — 기존 동의자는 팝업 없이 우측 상단 🔔 알림만 받음
       (`legal/routes.py:_notify_users_of_legal_update`).

    반환: {
      status: 'OK',
      current: {privacy, terms, force_privacy, force_terms},
      agreed:  {privacy, terms},
      needs_agreement: bool,  # 케이스 1 또는 2
      first_time: bool,       # 케이스 1 전용 — 팝업 헤더 문구 분기 용
      major_revision: bool,   # 케이스 2 전용
    }
    비로그인 사용자는 needs_agreement=False.
    """
    try:
        emp_id = (session.get('emp_id') or '').strip()
        if not session.get('admin_auth') or not emp_id:
            return jsonify({
                'status': 'OK', 'needs_agreement': False,
                'current': {}, 'agreed': {},
                'first_time': False, 'major_revision': False,
            })

        current = _get_current_tos_versions()
        agreed = _get_user_agreed_versions(emp_id)

        has_any_prior_agreement = (
            agreed.get('privacy', 0) >= 1 or agreed.get('terms', 0) >= 1
        )
        has_any_current_doc = (
            current.get('privacy', 0) >= 1 or current.get('terms', 0) >= 1
        )
        first_time = has_any_current_doc and not has_any_prior_agreement

        # 중대 개정 재동의 체크 — 기존 동의자가 새 force_*_version 보다 낮은
        # 경우. 기존 동의 없는 신규 유저는 first_time 경로로 이미 처리됨.
        major_revision = False
        if has_any_prior_agreement:
            force_p = current.get('force_privacy', 0)
            force_t = current.get('force_terms', 0)
            if force_p >= 1 and agreed.get('privacy', 0) < force_p:
                major_revision = True
            elif force_t >= 1 and agreed.get('terms', 0) < force_t:
                major_revision = True

        needs = first_time or major_revision

        return jsonify({
            'status': 'OK',
            'current': current,
            'agreed': agreed,
            'needs_agreement': needs,
            'first_time': first_time,
            'major_revision': major_revision,
        })
    except Exception:
        logger.exception('api_tos_status error')
        return jsonify({'status': 'ERROR', 'message': 'Failed to load agreement status.'}), 500


@auth_bp.route('/api/auth/agree-tos', methods=['POST'])
def api_agree_tos():
    """현재 약관 버전 동의 기록. portal_users/{emp_id} 에 agreed_*_version 및 timestamp 저장.
    요청 body: {privacy_version: N, terms_version: M}  (각 current 와 일치해야 함)
    """
    try:
        from app.services.firebase_service import get_firestore_client
        from google.cloud import firestore

        emp_id = (session.get('emp_id') or '').strip()
        if not session.get('admin_auth') or not emp_id:
            return jsonify({'status': 'ERROR', 'message': 'Login required.'}), 401

        data = request.get_json(silent=True) or {}
        try:
            req_privacy = int(data.get('privacy_version', 0))
            req_terms   = int(data.get('terms_version', 0))
        except (TypeError, ValueError):
            return jsonify({'status': 'ERROR', 'message': 'Invalid version values.'}), 400

        # 동의 당시 사용자가 보고 있던 언어 — 감사 기록용.
        agreed_language = str(data.get('agreed_language') or 'ko').strip().lower()
        if agreed_language not in ('ko', 'en'):
            agreed_language = 'ko'

        current = _get_current_tos_versions()
        # 클라이언트가 제시한 버전이 현재 발효 버전과 같지 않으면 stale — 다시 불러오게 함
        if req_privacy != current['privacy'] or req_terms != current['terms']:
            return jsonify({
                'status': 'ERROR', 'code': 'VERSION_STALE',
                'message': 'Agreement document was updated. Please reload to view the latest version.',
                'current': current,
            }), 409

        # 둘 중 하나라도 미발행(0) 이면 동의 기록 불필요
        if current['privacy'] < 1 and current['terms'] < 1:
            return jsonify({'status': 'ERROR', 'message': 'No agreement documents are currently published.'}), 400

        db = get_firestore_client()
        ref = db.collection('portal_users').document(emp_id)
        # portal_users 문서가 존재해야 동의 기록 허용 — 로그인 플로우는 항상 문서를
        # 먼저 생성하므로, 없다면 비정상 상태(수동 삭제 등). merge=True 로 부분 문서를
        # 만들면 emp_id/name/role 없는 반쪽짜리가 생기므로 거부하고 재로그인 유도.
        existing = ref.get()
        if not existing.exists:
            logger.warning('api_agree_tos: portal_users doc missing for emp_id=%s — refusing to create partial doc', emp_id)
            session.clear()
            return jsonify({
                'status': 'ERROR',
                'code': 'ACCOUNT_MISSING',
                'message': 'Account record not found. Please sign in again.',
            }), 409

        ref.update({
            'agreed_privacy_version': current['privacy'],
            'agreed_terms_version':   current['terms'],
            'tos_agreed_at':          kst_now_iso(),
        })

        # 이력 서브컬렉션에도 append (감사용). agreed_language 는 사용자가 본 언어
        # (동의한 문서 언어) 를 기록 — 번역 분쟁 대응.
        try:
            ref.collection('tos_history').add({
                'privacy_version': current['privacy'],
                'terms_version':   current['terms'],
                'agreed_at':       kst_now_iso(),
                'agreed_language': agreed_language,
                'ip':              (request.headers.get('X-Forwarded-For') or '').split(',')[0].strip(),
                'user_agent':      (request.headers.get('User-Agent') or '')[:300],
            })
        except Exception:
            logger.exception('tos_history append failed emp_id=%s', emp_id)

        return jsonify({'status': 'OK', 'current': current})
    except Exception:
        logger.exception('api_agree_tos error')
        return jsonify({'status': 'ERROR', 'message': 'Failed to record agreement.'}), 500


# ============================================================================
# 💡 4. 로그아웃 처리
# ============================================================================
@auth_bp.route('/logout')
def logout():
    session.clear()
    next_url = request.args.get('next', '/')
    # Only allow relative paths to prevent open redirect
    if not next_url.startswith('/') or next_url.startswith('//') or '://' in next_url:
        next_url = '/'
    return redirect(next_url)