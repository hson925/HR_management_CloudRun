import logging
import os
import hmac
import json
import secrets
import time

logger = logging.getLogger(__name__)
from google.oauth2 import service_account
from googleapiclient.discovery import build
import base64
from email.mime.text import MIMEText

OTP_EXPIRY_SECONDS = 300  # 5분

def generate_otp():
    return str(secrets.randbelow(900000) + 100000)  # 6자리

def store_otp(session, otp):
    """새 OTP 발급·저장. 이전 시도 횟수를 반드시 초기화해 무제한 재발송으로
    누적된 카운터가 신규 OTP 검증을 조기 차단하지 않도록 함."""
    session['otp_code'] = otp
    session['otp_expires_at'] = time.time() + OTP_EXPIRY_SECONDS
    session.pop('otp_attempts', None)

MAX_OTP_ATTEMPTS = 5

def verify_otp(session, input_otp):
    stored = session.get('otp_code')
    expires_at = session.get('otp_expires_at', 0)
    attempts = session.get('otp_attempts', 0)

    if not stored:
        return False, 'OTP not found.'
    if time.time() > expires_at:
        session.pop('otp_code', None)
        session.pop('otp_expires_at', None)
        session.pop('otp_attempts', None)
        return False, 'OTP expired.'
    if attempts >= MAX_OTP_ATTEMPTS:
        session.pop('otp_code', None)
        session.pop('otp_expires_at', None)
        session.pop('otp_attempts', None)
        return False, 'Too many failed attempts. Please request a new OTP.'
    if not hmac.compare_digest(stored, input_otp.strip()):
        session['otp_attempts'] = attempts + 1
        remaining = MAX_OTP_ATTEMPTS - (attempts + 1)
        return False, f'Invalid OTP. {remaining} attempt(s) remaining.'
    session.pop('otp_code', None)
    session.pop('otp_expires_at', None)
    session.pop('otp_attempts', None)
    return True, 'OK'


def _get_gmail_credentials():
    """Gmail 발송용 서비스 계정 credentials 반환.
    FIREBASE_SERVICE_ACCOUNT_JSON(JSON 문자열) 우선,
    없으면 FIREBASE_SERVICE_ACCOUNT(파일 경로) 사용.
    firebase_service.py와 동일한 패턴.
    """
    sender = os.environ.get('GMAIL_SENDER', 'noreply@example.com')
    scopes = ['https://www.googleapis.com/auth/gmail.send']
    key_info = os.environ.get('FIREBASE_SERVICE_ACCOUNT_JSON')
    key_path = os.environ.get('FIREBASE_SERVICE_ACCOUNT')
    if key_info:
        creds = service_account.Credentials.from_service_account_info(
            json.loads(key_info), scopes=scopes
        )
    elif key_path:
        creds = service_account.Credentials.from_service_account_file(
            key_path, scopes=scopes
        )
    else:
        raise RuntimeError(
            'Gmail credentials not set. '
            'Set FIREBASE_SERVICE_ACCOUNT_JSON or FIREBASE_SERVICE_ACCOUNT.'
        )
    return creds.with_subject(sender), sender


def send_otp_email(to_email, otp):
    try:
        creds, sender = _get_gmail_credentials()
        service = build('gmail', 'v1', credentials=creds)

        message_text = f"""
Hello,

You requested a verification code to log in to NHR Portal.

Verification Code: {otp}

※ This code is valid for 5 minutes.
※ If you did not request this, please contact noreply@example.com immediately.
        """

        message = MIMEText(message_text, 'plain', 'utf-8')
        message['to'] = to_email
        message['from'] = f"DYB NHR <{sender}>"
        message['subject'] = '[NHR] Login Verification Code'

        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
        service.users().messages().send(userId='me', body={'raw': raw}).execute()
        try:
            from app.services.audit_service import log_audit
            log_audit('otp_email_sent', to_email, target=to_email, category='email')
        except Exception:
            pass
        return True

    except Exception as e:
        logger.exception('OTP email send failed: %s', e)
        return False

def send_reset_email(to_email, reset_link, subject=None, body=None):
    try:
        creds, sender = _get_gmail_credentials()
        service = build('gmail', 'v1', credentials=creds)

        if body is None:
            body = (
                f"Hello,\n\n"
                f"A password reset was requested for your NHR Portal account.\n\n"
                f"Please click the link below to reset your password:\n{reset_link}\n\n"
                f"※ This link is valid for 1 hour.\n"
                f"※ If you did not request this, please contact noreply@example.com immediately."
            )
        if subject is None:
            subject = '[NHR Portal] Password Reset Request'

        message = MIMEText(body, 'plain', 'utf-8')
        message['to'] = to_email
        message['from'] = f"DYB NHR <{sender}>"
        message['subject'] = subject

        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
        service.users().messages().send(userId='me', body={'raw': raw}).execute()
        try:
            from app.services.audit_service import log_audit
            log_audit('reset_email_sent', to_email, target=to_email, category='email')
        except Exception:
            pass
        return True

    except Exception as e:
        logger.exception('send_reset_email failed: %s', e)
        return False

def send_eval_reminder_email(to_email, subject, body_text):
    """평가 마감 알림 이메일 발송 (HTML 지원)"""
    try:
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText as _MIMEText
        creds, sender = _get_gmail_credentials()
        svc = build('gmail', 'v1', credentials=creds)
        msg = MIMEMultipart('alternative')
        msg['to'] = to_email
        msg['from'] = f"DYB NHR <{sender}>"
        msg['subject'] = subject
        # plain text fallback
        msg.attach(_MIMEText(body_text, 'plain', 'utf-8'))
        # HTML version
        html_body = body_text.replace('\n', '<br>')
        html = f"""<!DOCTYPE html>
<html><body style="font-family:sans-serif;font-size:14px;color:#1f2937;line-height:1.7;padding:24px">
<div style="max-width:560px;margin:0 auto">
  <div style="background:#B01116;border-radius:8px 8px 0 0;padding:16px 24px">
    <span style="color:#fff;font-weight:800;font-size:16px">DYB NHR</span>
  </div>
  <div style="border:1px solid #e5e7eb;border-top:none;border-radius:0 0 8px 8px;padding:24px">
    {html_body}
  </div>
  <p style="font-size:11px;color:#9ca3af;margin-top:16px;text-align:center">
    This is an automated message from DYB NHR Portal. Do not reply to this email.
  </p>
</div>
</body></html>"""
        msg.attach(_MIMEText(html, 'html', 'utf-8'))
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        svc.users().messages().send(userId='me', body={'raw': raw}).execute()
        return True
    except Exception as e:
        logger.exception('send_eval_reminder_email failed: %s', e)
        return False
