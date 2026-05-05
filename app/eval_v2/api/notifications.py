import logging
from flask import request, session  # noqa: F401 (session used in sync_campus_emails)

logger = logging.getLogger(__name__)
from app.eval_v2.blueprints import eval_v2_api
from app.auth_utils import api_admin_required
from app.eval_v2.api.common import kst_now
from app.services.firebase_service import get_firestore_client
from app.services.roster_cache_service import get_roster
from app.constants import CAMPUS_KO_TO_CODE, COL_EVAL_V2_SESSIONS, COL_EVAL_V2_RESPONSES
from app.utils.response import success, error


def _get_notify_targets(db, session_id, campus_filter='', incomplete_only=False):
    """알림 대상 평가자 목록 반환. Returns list of {emp_id, name, email, campus, submitted}"""
    from app.services.user_service import get_all_users
    # 1. Firestore에서 전체 등록 사용자 가져오기
    all_users = get_all_users()
    if not all_users:
        return []
    users = {u['emp_id']: u for u in all_users}

    # 2. 로스터에서 emp_id → campus 맵 (campus 필드가 없는 사용자를 위한 fallback)
    roster = get_roster()
    campus_map = {}
    for row in roster:
        if len(row) > 4:
            eid = str(row[2]).strip().lower()
            campus_map[eid] = str(row[4]).strip()

    # 3. 이 세션에서 rater_name 기준으로 이미 제출한 사람 집계
    submitted_names = set()
    if incomplete_only:
        docs = db.collection(COL_EVAL_V2_RESPONSES).where('session_id', '==', session_id).stream()
        for doc in docs:
            rn = doc.to_dict().get('rater_name', '').strip()
            if rn:
                submitted_names.add(rn.lower())

    # 4. 필터 적용 후 목록 생성
    campus_en_map = CAMPUS_KO_TO_CODE
    targets = []
    for eid, u in users.items():
        # campus: stored value takes priority, fall back to roster
        campus = u.get('campus', '') or campus_map.get(eid, '')
        campus_key = campus if not campus.startswith('SUB') else 'SUB'
        campus_en = campus_en_map.get(campus_key, campus)
        # campus 필터
        if campus_filter and campus_filter not in (campus, campus_en):
            continue
        # incomplete_only 필터
        submitted = u['name'].lower() in submitted_names
        if incomplete_only and submitted:
            continue
        targets.append({
            'emp_id': eid,
            'name': u['name'],
            'email': u.get('email', ''),
            'campus': campus,
            'campus_en': campus_en,
            'submitted': submitted,
        })
    targets.sort(key=lambda x: (x['campus'], x['name']))
    return targets


def _render_email_body(template, name, session_label, deadline):
    # 치환 값에서 중괄호 제거하여 추가 치환 인젝션 방지
    safe_name = str(name).replace('{', '').replace('}', '')
    safe_session = str(session_label).replace('{', '').replace('}', '')
    safe_deadline = str(deadline).replace('{', '').replace('}', '')
    return template.replace('{name}', safe_name).replace('{session}', safe_session).replace('{deadline}', safe_deadline)


@eval_v2_api.route('/preview-notification-recipients', methods=['POST'])
@api_admin_required
def api_preview_notification_recipients():
    try:
        data = request.get_json(silent=True) or {}
        session_id = str(data.get('sessionId', '')).strip()
        campus_filter = str(data.get('campusFilter', '')).strip()
        incomplete_only = bool(data.get('incompleteOnly', False))
        if not session_id:
            return error('sessionId is required.')
        db = get_firestore_client()
        targets = _get_notify_targets(db, session_id, campus_filter, incomplete_only)
        with_email = [t for t in targets if t['email']]
        no_email = [t for t in targets if not t['email']]
        return success({'with_email': with_email, 'no_email': no_email})
    except Exception:
        logger.exception('preview_notification_recipients error')
        return error('An internal error occurred.')


@eval_v2_api.route('/send-notification', methods=['POST'])
@api_admin_required
def api_send_notification():
    try:
        from app.services.otp_service import send_eval_reminder_email
        data = request.get_json(silent=True) or {}
        session_id     = str(data.get('sessionId', '')).strip()
        campus_filter  = str(data.get('campusFilter', '')).strip()
        incomplete_only = bool(data.get('incompleteOnly', False))
        lang           = str(data.get('lang', 'en')).strip()   # 'en' | 'ko' | 'both'
        subject_en     = str(data.get('subjectEn', '')).strip()
        body_en        = str(data.get('bodyEn', '')).strip()
        subject_ko     = str(data.get('subjectKo', '')).strip()
        body_ko        = str(data.get('bodyKo', '')).strip()
        if not session_id:
            return error('sessionId is required.')
        db = get_firestore_client()
        sess_doc = db.collection(COL_EVAL_V2_SESSIONS).document(session_id).get()
        if not sess_doc.exists:
            return error('Session not found.')
        sess = sess_doc.to_dict()
        session_label = sess.get('label', session_id)
        deadline = sess.get('end_date', '')
        targets = _get_notify_targets(db, session_id, campus_filter, incomplete_only)
        sent, no_email, failed = [], [], []
        for t in targets:
            if not t['email']:
                no_email.append({'name': t['name'], 'emp_id': t['emp_id'], 'campus': t['campus']})
                continue
            # 발송할 메시지 구성
            if lang == 'en':
                subj = _render_email_body(subject_en, t['name'], session_label, deadline)
                body = _render_email_body(body_en, t['name'], session_label, deadline)
            elif lang == 'ko':
                subj = _render_email_body(subject_ko, t['name'], session_label, deadline)
                body = _render_email_body(body_ko, t['name'], session_label, deadline)
            else:  # both
                subj = _render_email_body(subject_en, t['name'], session_label, deadline)
                body = (
                    _render_email_body(body_en, t['name'], session_label, deadline)
                    + '\n\n─────────────────────\n\n'
                    + _render_email_body(body_ko, t['name'], session_label, deadline)
                )
            ok = send_eval_reminder_email(t['email'], subj, body)
            if ok:
                sent.append({'name': t['name'], 'email': t['email'], 'campus': t['campus']})
            else:
                failed.append({'name': t['name'], 'email': t['email'], 'campus': t['campus']})
        # 발송 기록 저장
        db.collection(COL_EVAL_V2_SESSIONS).document(session_id).update({
            'last_notification_sent_at': kst_now(),
            'last_notification_sent_by': session.get('admin_email', ''),
            'last_notification_count': len(sent),
        })
        try:
            from app.services.audit_service import log_audit
            log_audit('notification_email_sent', session.get('admin_email', ''), target=session_id,
                      details={'sent': len(sent), 'failed': len(failed), 'no_email': len(no_email), 'lang': lang}, category='email')
        except Exception:
            pass
        return success({'sent': sent, 'no_email': no_email, 'failed': failed})
    except Exception:
        logger.exception('send_notification error')
        return error('An internal error occurred.')


@eval_v2_api.route('/save-notification-schedule', methods=['POST'])
@api_admin_required
def api_save_notification_schedule():
    try:
        data = request.get_json(silent=True) or {}
        session_id = str(data.get('sessionId', '')).strip()
        if not session_id:
            return error('sessionId is required.')
        schedule = {
            'enabled':         bool(data.get('enabled', False)),
            'days_before':     [int(d) for d in data.get('daysBefore', []) if str(d).isdigit()],
            'lang':            str(data.get('lang', 'en')),
            'campus_filter':   str(data.get('campusFilter', '')),
            'incomplete_only': bool(data.get('incompleteOnly', False)),
            'subject_en':      str(data.get('subjectEn', '')),
            'body_en':         str(data.get('bodyEn', '')),
            'subject_ko':      str(data.get('subjectKo', '')),
            'body_ko':         str(data.get('bodyKo', '')),
            'sent_markers':    data.get('sentMarkers', {}),
            'updated_at':      kst_now(),
            'updated_by':      session.get('admin_email', ''),
        }
        db = get_firestore_client()
        db.collection(COL_EVAL_V2_SESSIONS).document(session_id).update({'notification_schedule': schedule})
        return success()
    except Exception:
        logger.exception('save_notification_schedule error')
        return error('An internal error occurred.')


@eval_v2_api.route('/check-scheduled-notifications', methods=['POST'])
@api_admin_required
def api_check_scheduled_notifications():
    try:
        from app.services.otp_service import send_eval_reminder_email
        import datetime as _dt
        today_str = _dt.date.today().isoformat()
        db = get_firestore_client()
        docs = db.collection(COL_EVAL_V2_SESSIONS).where('status', '==', 'active').stream()
        results = []
        for doc in docs:
            d = doc.to_dict()
            sched = d.get('notification_schedule', {})
            if not sched.get('enabled'):
                continue
            end_date_str = d.get('end_date', '')
            if not end_date_str:
                continue
            try:
                end_date = _dt.date.fromisoformat(end_date_str)
                today = _dt.date.today()
                days_until = (end_date - today).days
            except ValueError:
                continue
            days_before_list = sched.get('days_before', [])
            sent_markers = sched.get('sent_markers', {})
            if days_until not in days_before_list:
                continue
            marker_key = str(days_until)
            if sent_markers.get(marker_key) == today_str:
                continue  # 오늘 이미 발송됨
            # 발송 실행
            session_label = d.get('label', doc.id)
            deadline = end_date_str
            campus_filter = sched.get('campus_filter', '')
            incomplete_only = sched.get('incomplete_only', False)
            lang = sched.get('lang', 'en')
            subject_en = sched.get('subject_en', '')
            body_en = sched.get('body_en', '')
            subject_ko = sched.get('subject_ko', '')
            body_ko = sched.get('body_ko', '')
            targets = _get_notify_targets(db, doc.id, campus_filter, incomplete_only)
            sent_count, failed_count = 0, 0
            for t in targets:
                if not t['email']:
                    continue
                if lang == 'en':
                    subj = _render_email_body(subject_en, t['name'], session_label, deadline)
                    body = _render_email_body(body_en, t['name'], session_label, deadline)
                elif lang == 'ko':
                    subj = _render_email_body(subject_ko, t['name'], session_label, deadline)
                    body = _render_email_body(body_ko, t['name'], session_label, deadline)
                else:
                    subj = _render_email_body(subject_en, t['name'], session_label, deadline)
                    body = (_render_email_body(body_en, t['name'], session_label, deadline)
                            + '\n\n─────────────────────\n\n'
                            + _render_email_body(body_ko, t['name'], session_label, deadline))
                ok = send_eval_reminder_email(t['email'], subj, body)
                if ok:
                    sent_count += 1
                else:
                    failed_count += 1
            # 발송 마커 업데이트
            sent_markers[marker_key] = today_str
            db.collection(COL_EVAL_V2_SESSIONS).document(doc.id).update({
                'notification_schedule.sent_markers': sent_markers,
                'last_notification_sent_at': kst_now(),
                'last_notification_count': sent_count,
            })
            results.append({'session': session_label, 'sent': sent_count, 'failed': failed_count, 'days_before': days_until})
        return success({'results': results, 'checked_at': kst_now()})
    except Exception:
        logger.exception('check_scheduled_notifications error')
        return error('An internal error occurred.')


@eval_v2_api.route('/sync-campus-emails', methods=['POST'])
@api_admin_required
def sync_campus_emails():
    """portal_users의 role(GS/TL/STL)과 campus를 기반으로 campus_emails 자동 동기화."""
    try:
        from app.services.user_service import get_all_users
        users = get_all_users()

        # campus별로 GS/TL 이메일 수집
        campus_gs   = {}   # campus_ko → [email, ...]
        campus_tl   = {}   # campus_ko → [email, ...]

        for u in users:
            campus = (u.get('campus') or '').strip()
            role   = (u.get('role')   or '').strip().upper()
            email  = (u.get('email')  or '').strip()
            if not campus or not email or role not in ('GS', 'TL', 'STL'):
                continue
            if role == 'GS':
                campus_gs.setdefault(campus, []).append(email)
            else:  # TL or STL
                campus_tl.setdefault(campus, []).append(email)

        all_campuses = sorted(set(list(campus_gs.keys()) + list(campus_tl.keys())))
        campuses = [
            {
                'campus_ko': c,
                'campus_en': CAMPUS_KO_TO_CODE.get(c, c),
                'gs_email':  ', '.join(campus_gs.get(c, [])),
                'ctl_email': ', '.join(campus_tl.get(c, [])),
            }
            for c in all_campuses
        ]

        db = get_firestore_client()
        db.collection('email_settings').document('campus_emails').set({
            'campuses': campuses,
            'updated_at': kst_now(),
            'updated_by': session.get('admin_email', ''),
        })

        return success({'campuses': campuses, 'count': len(campuses)})
    except Exception:
        logger.exception('sync_campus_emails error')
        return error('An internal error occurred.')
