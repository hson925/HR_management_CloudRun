import logging
import os
import json
from datetime import datetime, timedelta
from google.oauth2 import service_account
from googleapiclient.discovery import build
from config import SHEET_CONFIG, SERVICE_ACCOUNT_FILE, SCOPES
from app.extensions import cache

logger = logging.getLogger(__name__)

_cached_sheets_service = None

def get_sheets_service():
    global _cached_sheets_service
    if _cached_sheets_service is not None:
        return _cached_sheets_service
    key_info = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if key_info:
        info = json.loads(key_info)
        creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    else:
        creds = service_account.Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
    _cached_sheets_service = build('sheets', 'v4', credentials=creds)
    return _cached_sheets_service

@cache.cached(timeout=300, key_prefix='roster_data')
def fetch_roster_data():
    service = get_sheets_service()
    result = service.spreadsheets().values().get(spreadsheetId=SHEET_CONFIG['ROSTER_ID'], range="'구분'!A7:E").execute()
    return result.get('values', [])


@cache.cached(timeout=3600, key_prefix='emp_db_data')
def fetch_emp_db():
    """DYB + Sub 탭에서 사번(B열), 이름(C열), 여권번호(M열) 전체 가져오기 (1시간 캐시)"""
    try:
        service = get_sheets_service()
        dyb = service.spreadsheets().values().get(
            spreadsheetId=SHEET_CONFIG['EMP_DB_ID'],
            range="'DYB'!B:M"
        ).execute().get('values', [])
        sub = service.spreadsheets().values().get(
            spreadsheetId=SHEET_CONFIG['EMP_DB_ID'],
            range="'Sub'!B:M"
        ).execute().get('values', [])
        # {사번: {'name': 이름, 'passport': 여권번호}} 딕셔너리로 변환
        emp_map = {}
        for row in dyb + sub:
            if len(row) >= 1 and str(row[0]).strip():
                emp_id = str(row[0]).strip()
                name = str(row[1]).strip() if len(row) > 1 else ''
                # M열은 B열 기준 12번째 (인덱스 11)
                passport = str(row[11]).strip().upper() if len(row) > 11 else ''
                emp_map[emp_id] = {'name': name, 'passport': passport}
        return emp_map
    except Exception as e:
        logger.exception('fetch_emp_db error: %s', e)
        return {}

def fetch_registered_emp_ids():
    """Users 탭에서 이미 가입된 사번 목록 가져오기"""
    try:
        service = get_sheets_service()
        result = service.spreadsheets().values().get(
            spreadsheetId=SHEET_CONFIG['ROSTER_ID'],
            range="'Users'!A:A"
        ).execute()
        values = result.get('values', [])
        return [str(row[0]).strip() for row in values if row]
    except Exception as e:
        logger.exception('fetch_registered_emp_ids error: %s', e)
        return []

def register_user_to_sheet(emp_id, name, role, firebase_uid, email):
    """Users 탭에 신규 사용자 등록"""
    try:
        service = get_sheets_service()
        service.spreadsheets().values().append(
            spreadsheetId=SHEET_CONFIG['ROSTER_ID'],
            range="'Users'!A:F",
            valueInputOption='RAW',
            body={'values': [[
                emp_id, name, role, firebase_uid, email,
                datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            ]]}
        ).execute()
        return True
    except Exception as e:
        logger.exception('register_user_to_sheet error: %s', e)
        return False

def fetch_user_by_emp_id(emp_id):
    """Users 탭에서 사번으로 사용자 정보 조회"""
    try:
        service = get_sheets_service()
        result = service.spreadsheets().values().get(
            spreadsheetId=SHEET_CONFIG['ROSTER_ID'],
            range="'Users'!A:F"
        ).execute()
        values = result.get('values', [])
        for i, row in enumerate(values):
            if row and str(row[0]).strip() == emp_id:
                return {
                    'row_index': i + 1,
                    'emp_id': row[0] if len(row) > 0 else '',
                    'name': row[1] if len(row) > 1 else '',
                    'role': row[2] if len(row) > 2 else '',
                    'firebase_uid': row[3] if len(row) > 3 else '',
                    'email': row[4] if len(row) > 4 else '',
                }
        return None
    except Exception as e:
        logger.exception('fetch_user_by_emp_id error: %s', e)
        return None

def update_user_email_in_sheet(emp_id, new_email):
    """Users 탭에서 사번에 해당하는 이메일 업데이트"""
    try:
        service = get_sheets_service()
        result = service.spreadsheets().values().get(
            spreadsheetId=SHEET_CONFIG['ROSTER_ID'],
            range="'Users'!A:A"
        ).execute()
        values = result.get('values', [])
        for i, row in enumerate(values):
            if row and str(row[0]).strip() == emp_id:
                row_num = i + 1
                service.spreadsheets().values().update(
                    spreadsheetId=SHEET_CONFIG['ROSTER_ID'],
                    range=f"'Users'!E{row_num}",
                    valueInputOption='RAW',
                    body={'values': [[new_email]]}
                ).execute()
                return True
        return False
    except Exception as e:
        logger.exception('update_user_email_in_sheet error: %s', e)
        return False

def fetch_user_by_emp_id_by_email(email):
    """Users 탭에서 이메일로 사용자 조회"""
    try:
        service = get_sheets_service()
        result = service.spreadsheets().values().get(
            spreadsheetId=SHEET_CONFIG['ROSTER_ID'],
            range="'Users'!A:F"
        ).execute()
        values = result.get('values', [])
        for row in values:
            padded = row + [''] * (5 - len(row))
            if str(padded[4]).strip().lower() == email.lower() and padded[4].strip():
                return {
                    'emp_id': row[0] if len(row) > 0 else '',
                    'name': row[1] if len(row) > 1 else '',
                    'role': row[2] if len(row) > 2 else '',
                    'firebase_uid': row[3] if len(row) > 3 else '',
                    'email': row[4] if len(row) > 4 else '',
                }
        return None
    except Exception as e:
        logger.exception('fetch_user_by_emp_id_by_email error: %s', e)
        return None

def fetch_user_by_email_from_sheet(email):
    """Users 탭에서 이메일로 사용자 조회"""
    try:
        service = get_sheets_service()
        result = service.spreadsheets().values().get(
            spreadsheetId=SHEET_CONFIG['ROSTER_ID'],
            range="'Users'!A:F"
        ).execute()
        values = result.get('values', [])
        for row in values:
            if len(row) > 4 and str(row[4]).strip().lower() == email.lower():
                return {
                    'emp_id': row[0] if len(row) > 0 else '',
                    'name': row[1] if len(row) > 1 else '',
                    'role': row[2] if len(row) > 2 else 'NET',
                    'firebase_uid': row[3] if len(row) > 3 else '',
                    'email': row[4] if len(row) > 4 else '',
                }
        return None
    # 기존: except Exception as e: pass
        # 👇 변경:
    except Exception as e:
        logger.exception('fetch_user_by_email_from_sheet error (email: %s): %s', email, e)
        return None

def fetch_nt_info(sheet_name):
    """NT Info 시트에서 특정 시트 데이터 읽기.

    AA 컬럼(legacy `eval_link` HYPERLINK formula) 은 더 이상 사용하지 않음 —
    평가 폴더 URL 은 BV 컬럼(`eval_folder_url`) 단일 출처로 통합됨.
    """
    try:
        service = get_sheets_service()
        sheet_id = SHEET_CONFIG['NT_INFO_ID']

        # 전체 데이터를 FORMATTED_VALUE 로 한 번에 읽기.
        main_result = service.spreadsheets().values().get(
            spreadsheetId=sheet_id,
            range=f"'{sheet_name}'!A1:BV",
            valueRenderOption='FORMATTED_VALUE'
        ).execute()
        rows = main_result.get('values', [])
        if len(rows) < 2:
            return []

        def get_col(row, idx, default=''):
            return str(row[idx]).strip() if len(row) > idx and str(row[idx]).strip() else default

        records = []
        for i, row in enumerate(rows[1:], start=1):  # 2행부터
            emp_id = get_col(row, 1)
            if not emp_id:
                continue

            record = {
                'seq':                get_col(row, 0),
                'emp_id':             emp_id,
                'name':               get_col(row, 2),
                'campus':             get_col(row, 4),
                'start_date':         get_col(row, 5),
                'phone':              get_col(row, 7),
                'arc':                get_col(row, 8),
                'gender':             get_col(row, 9),
                'visa':               get_col(row, 10),
                'nationality':        get_col(row, 11),
                'passport':           get_col(row, 12),
                'email':              get_col(row, 13),
                'hire_path':          get_col(row, 14),
                'education':          get_col(row, 15),
                'position':           get_col(row, 16),
                'salary_type':        get_col(row, 17),
                'salary_day':         get_col(row, 18),
                'salary_history':     get_col(row, 19),
                'allowance_name':     get_col(row, 20),
                'base_salary':        get_col(row, 21),
                'position_allowance': get_col(row, 22),
                'role_allowance':     get_col(row, 23),
                'housing_allowance':  get_col(row, 24),
                'total_salary':       get_col(row, 25),
                'transfer_history':   get_col(row, 27),
                'photo_url':          get_col(row, 71),  # BT열
                'nickname':           get_col(row, 72),  # BU열
                'eval_folder_url':    get_col(row, 73),  # BV열 — 평가 폴더 단일 출처
                'sheet':              sheet_name,
                'synced_at':          datetime.utcnow().isoformat(),
            }
            records.append(record)
        return records
    except Exception as e:
        logger.exception('fetch_nt_info failed (%s): %s', sheet_name, e)
        return []
        
def fetch_retire_info():
    """퇴직 시트에서 2024년 이후 퇴사자 데이터 읽기"""
    try:
        service = get_sheets_service()
        sheet_id = SHEET_CONFIG['RETIRE_ID']
        start_year = SHEET_CONFIG['RETIRE_START_YEAR']
        current_year = datetime.now().year

        # 스프레드시트 메타데이터에서 시트 이름 목록 조회
        meta = service.spreadsheets().get(spreadsheetId=sheet_id).execute()
        sheet_names = [s['properties']['title'] for s in meta.get('sheets', [])]

        # 대상 시트 필터링 (퇴직 2024 이상)
        target_sheets = []
        for name in sheet_names:
            for year in range(start_year, current_year + 1):
                if name == f'퇴직 {year}':
                    target_sheets.append(name)

        records = []
        for sheet_name in target_sheets:
            result = service.spreadsheets().values().get(
                spreadsheetId=sheet_id,
                range=f"'{sheet_name}'!A1:BV",
                valueRenderOption='FORMATTED_VALUE'
            ).execute()
            rows = result.get('values', [])
            if len(rows) < 2:
                continue

            for row in rows[1:]:
                def get_col(idx, default=''):
                    return str(row[idx]).strip() if len(row) > idx and str(row[idx]).strip() else default

                emp_id = get_col(2)  # C열 = 인덱스 2
                if not emp_id:
                    continue

                records.append({
                    'emp_id':     emp_id,
                    'retire_date': get_col(1),   # B열: 퇴사일
                    'name':       get_col(3),    # D열: 이름
                    'campus':     get_col(5),    # F열: 캠퍼스 (NT Info 기준 E열+1)
                    'start_date': get_col(6),    # G열: 입사일
                    'sheet':      sheet_name,
                })
        return records
    except Exception as e:
        logger.exception('fetch_retire_info failed: %s', e)
        return []