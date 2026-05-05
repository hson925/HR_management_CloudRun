import base64
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from flask import request, session

from app.auth_utils import api_admin_required
from app.constants import CAMPUS_KO_TO_CODE
from app.eval_v2.api.common import kst_now
from app.eval_v2.blueprints import eval_v2_api
from app.extensions import limiter
from app.services.firebase_service import (
    get_firestore_client,
    get_session_sub_ctl_map,
    get_sub_ctl_assignments_map,
)
from app.utils.rate_limit import admin_rate_key
from app.utils.response import success, error

logger = logging.getLogger(__name__)
KST = timezone(timedelta(hours=9))

_TEMPLATE_KEYS = ('gsTitle', 'gsBody', 'ctlTitle', 'ctlBody', 'stlTitle', 'stlBody')


# ── Templates ─────────────────────────────────────────────────────────────────

@eval_v2_api.route('/draft-templates', methods=['GET'])
@api_admin_required
@limiter.limit("60 per minute", key_func=admin_rate_key)
def api_get_draft_templates():
    try:
        db = get_firestore_client()
        doc = db.collection('email_settings').document('draft_templates').get()
        data = doc.to_dict() if doc.exists else {}
        return success({k: data.get(k, '') for k in _TEMPLATE_KEYS})
    except Exception:
        logger.exception('get_draft_templates error')
        return error('An internal error occurred.')


@eval_v2_api.route('/draft-templates', methods=['POST'])
@api_admin_required
@limiter.limit("30 per minute", key_func=admin_rate_key)
def api_save_draft_templates():
    try:
        data = request.get_json(silent=True) or {}
        payload = {k: str(data.get(k, '')).strip() for k in _TEMPLATE_KEYS}
        payload['updated_at'] = kst_now()
        payload['updated_by'] = session.get('admin_email', '')
        get_firestore_client().collection('email_settings').document('draft_templates').set(payload)
        return success()
    except Exception:
        logger.exception('save_draft_templates error')
        return error('An internal error occurred.')


# ── Campus emails ──────────────────────────────────────────────────────────────

@eval_v2_api.route('/draft-campus-emails', methods=['GET'])
@api_admin_required
@limiter.limit("60 per minute", key_func=admin_rate_key)
def api_get_draft_campus_emails():
    try:
        db = get_firestore_client()
        doc = db.collection('email_settings').document('campus_emails').get()
        campuses = (doc.to_dict() or {}).get('campuses', []) if doc.exists else []
        return success({'campuses': campuses})
    except Exception:
        logger.exception('get_draft_campus_emails error')
        return error('An internal error occurred.')


@eval_v2_api.route('/draft-campus-emails', methods=['POST'])
@api_admin_required
@limiter.limit("30 per minute", key_func=admin_rate_key)
def api_save_draft_campus_emails():
    try:
        data = request.get_json(silent=True) or {}
        raw = data.get('campuses', [])
        if not isinstance(raw, list):
            return error('campuses must be a list.')
        campuses = [
            {
                'campus_ko': str(c.get('campus_ko', '')).strip(),
                'campus_en': str(c.get('campus_en', '')).strip(),
                'gs_email':  str(c.get('gs_email',  '')).strip(),
                'ctl_email': str(c.get('ctl_email', '')).strip(),
            }
            for c in raw if isinstance(c, dict)
        ]
        get_firestore_client().collection('email_settings').document('campus_emails').set({
            'campuses':   campuses,
            'updated_at': kst_now(),
            'updated_by': session.get('admin_email', ''),
        })
        return success()
    except Exception:
        logger.exception('save_draft_campus_emails error')
        return error('An internal error occurred.')


# ── Draft generation helpers ───────────────────────────────────────────────────

def _is_tenure_ok(start_date_str: str) -> bool:
    """True if start_date is in the past AND tenure >= 180 days."""
    if not start_date_str:
        return False
    for fmt in ('%Y-%m-%d', '%m/%d/%Y', '%Y/%m/%d', '%d-%b-%y', '%d/%m/%Y'):
        try:
            start = datetime.strptime(start_date_str.strip(), fmt).date()
            today = datetime.now(KST).date()
            return start <= today and (today - start).days >= 180
        except ValueError:
            continue
    return False


# Google Sheet (NT_INFO 'dyb'/'sub' 탭) 배경색 매칭
_TL_HEADER_BG = '#d9d9d9'   # 헤더 회색
_TL_BODY_BG   = '#ffffff'
_TL_BORDER    = '#bfbfbf'

# Position 별 배경색 (시트 Q열 서식 기준 + Regular/Position 추가 색상)
_POSITION_BG = {
    'TL':       '#ea9999',
    'STL':      '#f9cb9b',
    'SUB':      '#b7b7b7',
    'REGULAR':  '#fff2cc',
    'POSITION': '#cfe2f3',
}


def _teacher_row(t: dict) -> str:
    name     = t.get('name', '') or t.get('nickname', '')
    emp_id   = t.get('emp_id', '')
    position = (t.get('position', '') or '').strip()
    pos_key  = position.upper()
    # position 이 SUB 인 경우 근무 캠퍼스 숨김
    campus   = '' if pos_key == 'SUB' else t.get('campus', '')
    pos_bg   = _POSITION_BG.get(pos_key, _TL_BODY_BG)

    body_td   = f'style="padding:6px 10px;border:1px solid {_TL_BORDER};background:{_TL_BODY_BG};"'
    name_td   = f'style="padding:6px 10px;border:1px solid {_TL_BORDER};background:{_TL_BODY_BG};font-weight:700;"'
    campus_td = f'style="padding:6px 10px;border:1px solid {_TL_BORDER};background:{_TL_BODY_BG};font-weight:700;"'
    pos_td    = f'style="padding:6px 10px;border:1px solid {_TL_BORDER};background:{pos_bg};"'
    return (f'<tr>'
            f'<td {name_td}>{name}</td>'
            f'<td {body_td}>{emp_id}</td>'
            f'<td {campus_td}>{campus}</td>'
            f'<td {pos_td}>{position}</td>'
            f'</tr>')


def _teacher_list_html(teachers: list) -> str:
    if not teachers:
        return '<p style="color:#6b7280;font-size:13px;">(No eligible teachers)</p>'
    th = (f'style="padding:8px 10px;border:1px solid {_TL_BORDER};'
          f'background:{_TL_HEADER_BG};text-align:left;font-weight:700;"')
    header = (f'<tr>'
              f'<th {th}>Name</th>'
              f'<th {th}>ID</th>'
              f'<th {th}>Campus</th>'
              f'<th {th}>Position</th>'
              f'</tr>')
    rows = ''.join(_teacher_row(t) for t in teachers)
    return (
        '<table style="border-collapse:collapse;font-size:13px;">'
        f'<thead>{header}</thead><tbody>{rows}</tbody></table>'
    )


def _substitute(tmpl: str, campus_ko: str, campus_en: str, teacher_html: str) -> str:
    return (tmpl
            .replace('{{CAMPUS_KO}}', campus_ko)
            .replace('{{CAMPUS_EN}}', campus_en)
            .replace('{{TEACHER_LIST}}', teacher_html))


def _get_gmail_compose_service(impersonate: str = ''):
    """Gmail service with gmail.compose scope (required for drafts.create).

    When `impersonate` is provided, the draft is created under that user's
    Drafts folder (via Domain-Wide Delegation). Otherwise uses GMAIL_SENDER.
    """
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    subject  = impersonate or os.environ.get('GMAIL_SENDER', 'noreply@example.com')
    scopes   = ['https://www.googleapis.com/auth/gmail.compose']
    key_info = os.environ.get('FIREBASE_SERVICE_ACCOUNT_JSON')
    key_path = os.environ.get('FIREBASE_SERVICE_ACCOUNT')
    if key_info:
        creds = service_account.Credentials.from_service_account_info(json.loads(key_info), scopes=scopes)
    elif key_path:
        creds = service_account.Credentials.from_service_account_file(key_path, scopes=scopes)
    else:
        raise RuntimeError('Gmail credentials not configured.')
    return build('gmail', 'v1', credentials=creds.with_subject(subject)), subject


def _build_raw(to_email: str, subject: str, body: str, sender: str) -> str:
    msg = MIMEMultipart('alternative')
    msg['To']      = to_email
    msg['From']    = f'DYB NHR <{sender}>'
    msg['Subject'] = subject
    msg.attach(MIMEText(body, 'plain', 'utf-8'))
    html = body.replace('\n', '<br>')
    msg.attach(MIMEText(html, 'html', 'utf-8'))
    return base64.urlsafe_b64encode(msg.as_bytes()).decode()


def _create_draft(svc, raw: str) -> str:
    result = svc.users().drafts().create(userId='me', body={'message': {'raw': raw}}).execute()
    return result.get('id', '')


# ── /api/v2/create-drafts ──────────────────────────────────────────────────────

@eval_v2_api.route('/create-drafts', methods=['POST'])
@api_admin_required
@limiter.limit("5 per minute", key_func=admin_rate_key)
def api_create_drafts():
    try:
        data         = request.get_json(silent=True) or {}
        target_group = str(data.get('targetGroup', 'CAMPUS')).upper()
        campuses     = data.get('campuses', [])
        session_id   = str(data.get('sessionId', '')).strip()

        db = get_firestore_client()

        # Load templates
        tmpl_snap = db.collection('email_settings').document('draft_templates').get()
        tmpl      = tmpl_snap.to_dict() if tmpl_snap.exists else {}

        # ── SUB group: create STL draft in current admin's drafts folder ────
        if target_group == 'SUB':
            admin_email = session.get('admin_email', '')
            if not admin_email:
                return error('Admin email not found in session.')

            sub_docs = db.collection('nt_sub').limit(500).stream()
            sub_teachers = sorted(
                [d.to_dict() for d in sub_docs
                 if (d.to_dict().get('position') or '').upper() not in ('STL', 'TL')
                 and _is_tenure_ok(d.to_dict().get('start_date', ''))],
                key=lambda x: x.get('name', '')
            )
            teacher_html = _teacher_list_html(sub_teachers)
            subject = _substitute(tmpl.get('stlTitle', ''), 'SUB', 'SUB', teacher_html)
            body    = _substitute(tmpl.get('stlBody',  ''), 'SUB', 'SUB', teacher_html)

            try:
                gmail_svc, sender = _get_gmail_compose_service(impersonate=admin_email)
                draft_id = _create_draft(gmail_svc, _build_raw('', subject, body, sender))
            except Exception as e:
                logger.exception('STL draft creation failed')
                return error(f'Failed to create STL draft: {e}')

            return success({
                'message': f'STL draft created in {admin_email} Drafts. ({len(sub_teachers)} teachers listed)',
                'draft_id': draft_id,
            })

        # ── CAMPUS group: create Gmail drafts ───────────────────────────────
        if not campuses:
            return error('No campuses selected.')

        # Load campus email map
        ce_snap = db.collection('email_settings').document('campus_emails').get()
        campus_email_map = {
            c['campus_ko']: c
            for c in (ce_snap.to_dict() or {}).get('campuses', [])
            if c.get('campus_ko')
        } if ce_snap.exists else {}

        # Load sub CTL assignments (session-specific falls back to default)
        sub_ctl_map = get_session_sub_ctl_map(session_id) if session_id else {}
        default_map = get_sub_ctl_assignments_map()
        for eid, campus in default_map.items():
            if eid not in sub_ctl_map:
                sub_ctl_map[eid] = campus

        # Load all nt_sub teachers keyed by emp_id (for TL list augmentation)
        sub_all = {
            (d.to_dict().get('emp_id') or d.id).lower(): d.to_dict()
            for d in db.collection('nt_sub').limit(500).stream()
        }

        # Init Gmail service
        try:
            gmail_svc, sender = _get_gmail_compose_service()
        except Exception as e:
            logger.exception('Gmail compose service init failed')
            return error(
                f'Gmail service error: {e}. '
                'Add https://www.googleapis.com/auth/gmail.compose to the service account '
                'Domain-wide Delegation in Google Admin Console.'
            )

        created, errors = [], []

        for campus_ko in campuses:
            campus_en = CAMPUS_KO_TO_CODE.get(campus_ko, campus_ko)
            ce        = campus_email_map.get(campus_ko, {})
            gs_email  = ce.get('gs_email',  '')
            ctl_email = ce.get('ctl_email', '')

            # Teachers at this campus from nt_dyb
            dyb_teachers = sorted(
                [d.to_dict() for d in
                 db.collection('nt_dyb').where('campus', '==', campus_ko).limit(200).stream()
                 if _is_tenure_ok(d.to_dict().get('start_date', ''))],
                key=lambda x: x.get('name', '')
            )

            # TL list = dyb teachers (minus STL/TL) + eligible SUB teachers assigned here
            tl_base = [t for t in dyb_teachers
                       if (t.get('position') or '').upper() not in ('STL', 'TL')]
            sub_additions = []
            for eid, assigned in sub_ctl_map.items():
                if assigned not in (campus_ko, campus_en):
                    continue
                sub_t = sub_all.get(eid)
                if not sub_t:
                    continue
                if (sub_t.get('position') or '').upper() in ('STL', 'TL'):
                    continue
                if not _is_tenure_ok(sub_t.get('start_date', '')):
                    continue
                sub_additions.append(sub_t)
            tl_teachers = sorted(tl_base + sub_additions, key=lambda x: x.get('name', ''))

            # GS draft
            if gs_email:
                gs_html    = _teacher_list_html(dyb_teachers)
                gs_subject = _substitute(tmpl.get('gsTitle', ''), campus_ko, campus_en, gs_html)
                gs_body    = _substitute(tmpl.get('gsBody',  ''), campus_ko, campus_en, gs_html)
                try:
                    draft_id = _create_draft(gmail_svc, _build_raw(gs_email, gs_subject, gs_body, sender))
                    created.append({'campus': campus_ko, 'type': 'GS', 'to': gs_email, 'draft_id': draft_id})
                except Exception as e:
                    errors.append({'campus': campus_ko, 'type': 'GS', 'error': str(e)})
            else:
                errors.append({'campus': campus_ko, 'type': 'GS', 'error': 'No GS email configured.'})

            # TL draft
            if ctl_email:
                tl_html    = _teacher_list_html(tl_teachers)
                tl_subject = _substitute(tmpl.get('ctlTitle', ''), campus_ko, campus_en, tl_html)
                tl_body    = _substitute(tmpl.get('ctlBody',  ''), campus_ko, campus_en, tl_html)
                try:
                    draft_id = _create_draft(gmail_svc, _build_raw(ctl_email, tl_subject, tl_body, sender))
                    created.append({'campus': campus_ko, 'type': 'TL', 'to': ctl_email, 'draft_id': draft_id})
                except Exception as e:
                    errors.append({'campus': campus_ko, 'type': 'TL', 'error': str(e)})
            else:
                errors.append({'campus': campus_ko, 'type': 'TL', 'error': 'No TL email configured.'})

        # Audit
        try:
            from app.services.audit_service import log_audit
            log_audit('draft_created', session.get('admin_email', ''), target=','.join(campuses),
                      details={'created': len(created), 'errors': len(errors), 'session_id': session_id},
                      category='draft')
        except Exception:
            pass

        if errors and not created:
            return error(
                f'All drafts failed. First error: {errors[0]["error"]}',
                errors=errors,
            )

        msg = f'{len(created)} draft(s) created in Gmail.'
        if errors:
            msg += f' {len(errors)} skipped: ' + ', '.join(f'{e["campus"]} {e["type"]}' for e in errors)
        return success({'message': msg, 'created': created, 'errors': errors})

    except Exception:
        logger.exception('create_drafts error')
        return error('An internal error occurred.')
