"""
app/services/drive_service.py
Google Drive 폴더 관리 및 PDF 업로드 서비스
"""
import logging
import os
import io
import json
import re
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
from google.oauth2 import service_account

logger = logging.getLogger(__name__)

# 부모 폴더 ID
EVAL_FOLDER_ID         = '1Deh5T8qOBx49vAs2pT3BJikazRbeNC75'   # NET 개인 평가 파일 폴더
RETIRED_EVAL_FOLDER_ID = '1IqO7M_HmOiWXhi7xWMUSJ3YYsTjQWm0T'  # 퇴직자 이동 폴더

# 평가 폴더가 불필요한 시트 — 해당 시트 직원은 평가 대상 아님 (R&D/SIS 팀).
# preload_bv_url_map / save_folder_url_to_nt_info 에서 건너뛴다.
SHEETS_WITHOUT_EVAL_FOLDER = {'R&D_SIS'}

SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive',
]


DELEGATED_USER = 'noreply@example.com'  # Drive 폴더 소유자 계정

_cached_drive_service = None  # 서비스 객체 캐시 (매번 재빌드 방지)


def _dq(s: str) -> str:
    """Drive API 쿼리용 single-quote 문자열 이스케이프 (single quote → \\').

    backslash + single-quote 만 처리. Drive query 의 다른 operator (contains, in
    등) 보호는 caller 책임 — caller 가 사전 검증한 입력 (emp_id 정규식 통과 /
    safe_name 정규화 후) 만 전달한다는 가정. 검증 안 된 raw 사용자 입력은
    절대 직접 전달 금지.
    """
    return s.replace("\\", "\\\\").replace("'", "\\'")

def get_drive_service():
    global _cached_drive_service
    if _cached_drive_service is not None:
        return _cached_drive_service
    key_info = os.environ.get('GOOGLE_SERVICE_ACCOUNT_JSON')
    if key_info:
        info = json.loads(key_info)
        creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    else:
        from config import SERVICE_ACCOUNT_FILE
        creds = service_account.Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
    creds = creds.with_subject(DELEGATED_USER)
    _cached_drive_service = build('drive', 'v3', credentials=creds)
    return _cached_drive_service


def get_sheets_service_with_drive_scope():
    """Drive scope가 포함된 Sheets 서비스 (BV열 업데이트용)"""
    key_info = os.environ.get('GOOGLE_SERVICE_ACCOUNT_JSON')
    if key_info:
        info = json.loads(key_info)
        creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    else:
        from config import SERVICE_ACCOUNT_FILE
        creds = service_account.Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
    return build('sheets', 'v4', credentials=creds)


def _find_folder_by_emp_prefix(drive, emp_id_upper: str) -> str | None:
    """
    EVAL_FOLDER_ID 하위에서 이름이 '{EMPID}_' 로 시작하거나 정확히 '{EMPID}' 인
    첫 폴더의 ID 반환. 동일 직원의 폴더명이 저장된 이름과 달라도 (예: nt_name 비어
    '사번_사번' 로 잘못 생성되려는 케이스) 기존 폴더를 재사용하기 위함.
    Drive API는 startsWith 연산이 없어 'contains' 로 후보를 받은 뒤 Python 필터링.

    SECURITY: emp_id_upper 는 caller 가 EMP_ID_RE (^[a-zA-Z0-9_\\-]{1,30}$) 로
    사전 검증한 값이라 가정. 정규식 미통과 입력 전달 시 Drive query 인젝션 위험.
    H1 fix (2026-04-30) 후 모든 eval_v2 caller 가 검증된 입력만 사용.
    """
    emp_prefix = f'{emp_id_upper}_'
    query = (
        f"name contains '{_dq(emp_prefix)}' "
        f"and '{EVAL_FOLDER_ID}' in parents "
        f"and mimeType='application/vnd.google-apps.folder' "
        f"and trashed=false"
    )
    results = drive.files().list(
        q=query,
        fields='files(id, name)',
        supportsAllDrives=True,
        includeItemsFromAllDrives=True,
    ).execute()
    for f in results.get('files', []):
        name = str(f.get('name', ''))
        # 'P001_' 로 시작하는 폴더만 (substring 매치 ≠ prefix 매치)
        if name.upper().startswith(emp_prefix):
            return f['id']

    # 이름이 emp_id 단독인 경우도 허용 (full_name 미상이라 접미사 없이 생성된 경우)
    query_exact = (
        f"name='{_dq(emp_id_upper)}' "
        f"and '{EVAL_FOLDER_ID}' in parents "
        f"and mimeType='application/vnd.google-apps.folder' "
        f"and trashed=false"
    )
    results = drive.files().list(
        q=query_exact,
        fields='files(id)',
        supportsAllDrives=True,
        includeItemsFromAllDrives=True,
    ).execute()
    files = results.get('files', [])
    return files[0]['id'] if files else None


def _eval_folder_name(emp_id_upper: str, full_name: str) -> str:
    """
    full_name 이 비어있거나 emp_id 와 같으면 접미사를 붙이지 않음 ('P001_P001' 방지).
    """
    fn = (full_name or '').strip()
    if not fn or fn.upper() == emp_id_upper:
        return emp_id_upper
    return f'{emp_id_upper}_{fn}'


def extract_folder_id(url: str) -> str | None:
    """Drive 폴더 URL에서 folder_id 추출."""
    m = re.search(r'/folders/([a-zA-Z0-9_\-]+)', url)
    return m.group(1) if m else None


def get_folder_url_from_nt_info(emp_id: str) -> str | None:
    """NT INFO 파일 BV열(74번째 열)에서 개인 평가 폴더 링크 조회. 없으면 None.
    단일 조회용 — bulk 생성 시 preload_bv_url_map() 을 사용해 API 호출을 한 번으로 합칠 것.
    """
    try:
        url_map = preload_bv_url_map()
        return url_map.get(emp_id.strip().lower()) or None
    except Exception:
        logger.exception('get_folder_url_from_nt_info error')
        return None


def preload_bv_url_map() -> dict:
    """NT INFO 전체 시트의 B(사번)·BV(폴더 URL) 매핑을 한 번의 API 호출로 로드.
    반환: { 'emp_id_lower': 'folder_url', ... }
    bulk 리포트 생성 등 다중 조회 시 get_or_create_eval_folder(bv_url_map=...) 로 주입.
    헤더(1번 행) 는 B2:BV 범위로 명시적으로 제외. R&D/SIS 시트는 평가 대상이 아니므로 스킵.
    """
    url_map: dict[str, str] = {}
    try:
        from config import SHEET_CONFIG
        sheets = get_sheets_service_with_drive_scope()
        nt_info_id = SHEET_CONFIG['NT_INFO_ID']
        for sheet_name in SHEET_CONFIG.get('NT_SHEETS', ['dyb', 'sub']):
            if sheet_name in SHEETS_WITHOUT_EVAL_FOLDER:
                continue
            result = sheets.spreadsheets().values().get(
                spreadsheetId=nt_info_id,
                range=f"'{sheet_name}'!B2:BV",
            ).execute()
            for row in result.get('values', []):
                if not row:
                    continue
                eid = str(row[0]).strip().lower()
                if not eid:
                    continue
                if eid in url_map:
                    continue  # 우선순위 상위 시트 값 유지 (SHEET_CONFIG['NT_SHEETS'] 순서)
                # B2:BV 범위에서 BV는 인덱스 72 (BV=74열, B=2열, 74-2=72)
                url = str(row[72]).strip() if len(row) > 72 else ''
                if url:
                    url_map[eid] = url
    except Exception:
        logger.exception('preload_bv_url_map error')
    return url_map


def get_or_create_eval_folder(emp_id: str, full_name: str, bv_url_map: dict | None = None) -> dict:
    """
    개인 평가 폴더를 조회하거나 없으면 생성.
    조회 순서: (1) NT INFO BV열 → (2) Drive emp_id prefix 탐색 → (3) 신규 생성.
    폴더를 찾거나 생성하면 BV열이 비어있을 경우 자동으로 저장.
    bv_url_map: bulk 호출 시 preload_bv_url_map() 결과를 전달하면 Sheets API 중복 호출 방지.
    반환: {
        'folder_id': str, 'folder_url': str, 'created': bool,
        'bv_written': bool | None,  # True=쓰기 성공, False=쓰기 실패, None=쓰기 시도 안 함(BV에 이미 존재)
    }
    """
    drive = get_drive_service()
    emp_id_upper = emp_id.upper()

    # 1. NT INFO BV열 우선 조회 (bulk 시 캐시된 맵 사용)
    if bv_url_map is not None:
        bv_url = bv_url_map.get(emp_id.strip().lower())
    else:
        bv_url = get_folder_url_from_nt_info(emp_id)
    if bv_url:
        folder_id = extract_folder_id(bv_url)
        if folder_id:
            return {
                'folder_id': folder_id,
                'folder_url': bv_url,
                'created': False,
                'bv_written': None,
            }

    # 2. Drive에서 emp_id prefix 로 탐색
    existing_id = _find_folder_by_emp_prefix(drive, emp_id_upper)
    if existing_id:
        folder_url = f'https://drive.google.com/drive/folders/{existing_id}'
        bv_written = None
        if not bv_url:
            bv_written = save_folder_url_to_nt_info(emp_id, folder_url)
        return {
            'folder_id': existing_id,
            'folder_url': folder_url,
            'created': False,
            'bv_written': bv_written,
        }

    # 3. 없으면 생성
    folder_name = _eval_folder_name(emp_id_upper, full_name)
    meta = {
        'name': folder_name,
        'mimeType': 'application/vnd.google-apps.folder',
        'parents': [EVAL_FOLDER_ID],
    }
    folder = drive.files().create(
        body=meta,
        fields='id',
        supportsAllDrives=True,
    ).execute()
    folder_id = folder['id']
    folder_url = f'https://drive.google.com/drive/folders/{folder_id}'
    bv_written = save_folder_url_to_nt_info(emp_id, folder_url)
    return {
        'folder_id': folder_id,
        'folder_url': folder_url,
        'created': True,
        'bv_written': bv_written,
    }


def upload_pdf_to_folder(folder_id: str, filename: str, pdf_bytes: bytes) -> dict:
    """
    PDF 바이트를 Drive 폴더에 업로드.
    동일 이름 파일이 있으면 새 버전으로 덮어쓰기.
    반환: {'file_id': str, 'file_url': str}
    """
    drive = get_drive_service()

    # 동일 이름 파일 기존 여부 확인
    query = (
        f"name='{_dq(filename)}' "
        f"and '{folder_id}' in parents "
        f"and trashed=false"
    )
    results = drive.files().list(
        q=query,
        fields='files(id)',
        supportsAllDrives=True,
        includeItemsFromAllDrives=True,
    ).execute()
    existing = results.get('files', [])

    media = MediaIoBaseUpload(
        io.BytesIO(pdf_bytes),
        mimetype='application/pdf',
        resumable=False,
    )

    if existing:
        # 기존 파일 업데이트
        file_id = existing[0]['id']
        drive.files().update(
            fileId=file_id,
            media_body=media,
            supportsAllDrives=True,
        ).execute()
    else:
        # 새 파일 생성
        meta = {
            'name': filename,
            'parents': [folder_id],
        }
        result = drive.files().create(
            body=meta,
            media_body=media,
            fields='id',
            supportsAllDrives=True,
        ).execute()
        file_id = result['id']

    return {
        'file_id': file_id,
        'file_url': f'https://drive.google.com/file/d/{file_id}/view',
    }


def upload_report_to_eval_folder(emp_id: str, full_name: str,
                                  filename: str, pdf_bytes: bytes,
                                  bv_url_map: dict | None = None) -> dict:
    """
    평가 보고서 PDF를 개인 평가 폴더에 업로드하는 통합 헬퍼.
    폴더가 없으면 생성. 동일 파일명이면 덮어쓰기.
    bv_url_map: bulk 호출 시 preload_bv_url_map() 결과 전달.
    반환: {'file_url': str, 'folder_url': str, 'folder_id': str, 'file_id': str}
    """
    folder_info = get_or_create_eval_folder(emp_id, full_name, bv_url_map=bv_url_map)
    file_info   = upload_pdf_to_folder(folder_info['folder_id'], filename, pdf_bytes)
    return {
        'file_url':   file_info['file_url'],
        'file_id':    file_info['file_id'],
        'folder_url': folder_info['folder_url'],
        'folder_id':  folder_info['folder_id'],
    }


def move_folder_to_retired(folder_id: str) -> dict:
    """개인 평가 폴더를 퇴직자 폴더로 이동.

    반환: {'success': bool, 'previous_parents': list[str], 'already_archived': bool}
    - RETIRED_EVAL_FOLDER_ID 가 이미 parent 에 있으면 idempotent skip (already_archived=True)
    - Drive API 실패 시 success=False, 빈 리스트 반환
    """
    try:
        drive = get_drive_service()
        file_meta = drive.files().get(
            fileId=folder_id,
            fields='parents',
            supportsAllDrives=True,
        ).execute()
        prev_parents = [p for p in file_meta.get('parents', []) if p]
        if RETIRED_EVAL_FOLDER_ID in prev_parents:
            return {'success': True, 'previous_parents': [p for p in prev_parents if p != RETIRED_EVAL_FOLDER_ID], 'already_archived': True}

        drive.files().update(
            fileId=folder_id,
            addParents=RETIRED_EVAL_FOLDER_ID,
            removeParents=','.join(prev_parents),
            supportsAllDrives=True,
            fields='id, parents',
        ).execute()
        return {'success': True, 'previous_parents': prev_parents, 'already_archived': False}
    except Exception:
        logger.exception('move_folder_to_retired failed folder_id=%s', folder_id)
        return {'success': False, 'previous_parents': [], 'already_archived': False}


def restore_folder_from_retired(folder_id: str, target_parent_id: str) -> dict:
    """퇴직자 폴더(RETIRED_EVAL_FOLDER_ID)에 있는 개인 평가 폴더를 target_parent_id 로 복원.

    반환: {'success': bool, 'already_restored': bool, 'gone': bool}
    - RETIRED_EVAL_FOLDER_ID 가 parent 에 없으면 idempotent skip (already_restored=True)
    - 폴더가 실제로 삭제된 경우(404) gone=True + ERROR 로그 — 호출자가 nt_retire 정리
    """
    from googleapiclient.errors import HttpError

    try:
        drive = get_drive_service()
        file_meta = drive.files().get(
            fileId=folder_id,
            fields='parents',
            supportsAllDrives=True,
        ).execute()
        parents = file_meta.get('parents', []) or []
        if RETIRED_EVAL_FOLDER_ID not in parents:
            return {'success': True, 'already_restored': True, 'gone': False}

        drive.files().update(
            fileId=folder_id,
            addParents=target_parent_id,
            removeParents=RETIRED_EVAL_FOLDER_ID,
            supportsAllDrives=True,
            fields='id, parents',
        ).execute()
        return {'success': True, 'already_restored': False, 'gone': False}
    except HttpError as e:
        if e.resp.status == 404:
            logger.error('restore_folder_from_retired: folder gone folder_id=%s (manual cleanup)', folder_id)
            return {'success': False, 'already_restored': False, 'gone': True}
        logger.exception('restore_folder_from_retired HttpError folder_id=%s target=%s', folder_id, target_parent_id)
        return {'success': False, 'already_restored': False, 'gone': False}
    except Exception:
        logger.exception('restore_folder_from_retired failed folder_id=%s target=%s', folder_id, target_parent_id)
        return {'success': False, 'already_restored': False, 'gone': False}


def find_eval_folder(emp_id: str, full_name: str) -> str | None:
    """개인 평가 폴더 ID 조회. 없으면 None 반환 (생성하지 않음).
    full_name 인자는 하위 호환용 — 실제 탐색은 emp_id prefix 기반.
    """
    drive = get_drive_service()
    return _find_folder_by_emp_prefix(drive, emp_id.upper())


def find_report_in_folder(folder_id: str, filename: str) -> str | None:
    """폴더 내 특정 파일 ID 조회. 없으면 None 반환."""
    drive = get_drive_service()
    query = (
        f"name='{_dq(filename)}' "
        f"and '{folder_id}' in parents "
        f"and trashed=false"
    )
    results = drive.files().list(
        q=query,
        fields='files(id)',
        supportsAllDrives=True,
        includeItemsFromAllDrives=True,
    ).execute()
    files = results.get('files', [])
    return files[0]['id'] if files else None


def find_session_reports(session_label: str) -> list:
    """세션 라벨을 포함한 모든 보고서 파일 목록 반환 (EVAL_FOLDER_ID 직속 하위 폴더 탐색).

    Drive API의 'in ancestors'는 Shared Drive에서 400 에러를 반환하므로,
    Step 1: EVAL_FOLDER_ID의 직속 하위 폴더(개인 평가 폴더)를 'in parents'로 조회.
    Step 2: 각 하위 폴더에서 PDF 파일을 'in parents'로 조회 후 Python에서 패턴 매칭.
    """
    drive = get_drive_service()
    pattern = f'_{session_label}_eval.pdf'

    # Step 1: 개인 평가 하위 폴더 목록
    subfolders = []
    page_token = None
    while True:
        kwargs = dict(
            q=(
                f"'{EVAL_FOLDER_ID}' in parents "
                f"and mimeType='application/vnd.google-apps.folder' "
                f"and trashed=false"
            ),
            fields='nextPageToken, files(id)',
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
            pageSize=200,
        )
        if page_token:
            kwargs['pageToken'] = page_token
        results = drive.files().list(**kwargs).execute()
        subfolders.extend(results.get('files', []))
        page_token = results.get('nextPageToken')
        if not page_token:
            break

    if not subfolders:
        return []

    # Step 2: 각 하위 폴더에서 PDF 검색 (50개씩 묶어 OR 쿼리)
    folder_ids = [f['id'] for f in subfolders]
    matched = []
    chunk_size = 50
    for i in range(0, len(folder_ids), chunk_size):
        chunk = folder_ids[i:i + chunk_size]
        parents_clause = ' or '.join(f"'{fid}' in parents" for fid in chunk)
        q = f"mimeType='application/pdf' and ({parents_clause}) and trashed=false"
        pt = None
        while True:
            kw = dict(
                q=q,
                fields='nextPageToken, files(id, name)',
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
                pageSize=200,
            )
            if pt:
                kw['pageToken'] = pt
            res = drive.files().list(**kw).execute()
            for f in res.get('files', []):
                if pattern in f['name']:
                    matched.append(f)
            pt = res.get('nextPageToken')
            if not pt:
                break

    return matched


def trash_file(file_id: str) -> bool:
    """파일을 Drive 휴지통으로 이동. 404 (없는 file / 이미 trash) 는 idempotent — True 반환."""
    from googleapiclient.errors import HttpError
    drive = get_drive_service()
    try:
        drive.files().update(
            fileId=file_id,
            body={'trashed': True},
            supportsAllDrives=True,
        ).execute()
        return True
    except HttpError as e:
        if getattr(e, 'resp', None) is not None and e.resp.status == 404:
            logger.warning('trash_file: file_id not found (already trashed/deleted): %s', file_id)
            return True
        raise


def save_folder_url_to_nt_info(emp_id: str, folder_url: str):
    """
    NT Info 파일의 해당 사번 행 BV열(74번째 열)에 폴더 링크 저장.
    B열(인덱스1)에서 사번을 찾아 같은 행의 BV열에 씀.

    **중복 시나리오 주의**: 동일 emp_id 가 여러 시트에 있을 경우,
    SHEET_CONFIG['NT_SHEETS'] 순서(= DYB > SUB > CREO) 상 첫 매칭 시트에만
    기록하고 즉시 반환한다. 이는 "primary 시트만 authoritative" 라는 우선순위
    정책(NT_COLLECTION_PRIORITY)과 일관됨 — secondary 시트 행의 BV는 공백으로
    유지된다. R&D_SIS 시트는 평가 대상 아님 → 항상 skip.

    반환: True = 성공, False = 실패 (사번 unmatched 또는 Sheets API 에러).
    실패 케이스는 emp_id/sheet/row 포함하여 warning 로그 남김 (호출자가 무시해도 추적 가능).
    """
    from googleapiclient.errors import HttpError
    from config import SHEET_CONFIG
    sheets = get_sheets_service_with_drive_scope()
    nt_info_id = SHEET_CONFIG['NT_INFO_ID']
    emp_lower = emp_id.strip().lower()

    matched_sheet = None
    matched_row = None
    for sheet_name in SHEET_CONFIG.get('NT_SHEETS', ['dyb', 'sub']):
        # R&D/SIS 시트는 평가 대상 아님 — BV 쓰기 시도 자체를 건너뜀.
        if sheet_name in SHEETS_WITHOUT_EVAL_FOLDER:
            continue
        try:
            # 헤더(1번 행) 명시적으로 제외 — B2:B 로 실제 데이터부터 읽는다.
            result = sheets.spreadsheets().values().get(
                spreadsheetId=nt_info_id,
                range=f"'{sheet_name}'!B2:B",
            ).execute()
        except HttpError as e:
            logger.warning('save_folder_url_to_nt_info: B:B read failed emp_id=%s sheet=%s status=%s reason=%s',
                           emp_id, sheet_name, e.resp.status, (e.resp.reason or ''))
            continue
        except Exception:
            logger.exception('save_folder_url_to_nt_info: B:B read exception emp_id=%s sheet=%s', emp_id, sheet_name)
            continue

        rows = result.get('values', [])
        for i, row in enumerate(rows):
            if row and str(row[0]).strip().lower() == emp_lower:
                matched_sheet = sheet_name
                # B2 부터 읽었으므로 시트상 실제 행번호는 i + 2
                matched_row = i + 2
                break
        if matched_sheet:
            break

    if not matched_sheet:
        logger.warning('save_folder_url_to_nt_info: emp_id not matched in any NT sheet — BV write skipped emp_id=%s', emp_id)
        return False

    try:
        sheets.spreadsheets().values().update(
            spreadsheetId=nt_info_id,
            range=f"'{matched_sheet}'!BV{matched_row}",
            valueInputOption='USER_ENTERED',
            body={'values': [[folder_url]]},
        ).execute()
        return True
    except HttpError as e:
        # Google API 가 반환하는 상세 메시지 (예: grid 범위 초과) 추출.
        detail = ''
        try:
            detail = (e.content or b'').decode('utf-8', errors='replace')[:500]
        except Exception:
            detail = ''
        logger.error(
            'save_folder_url_to_nt_info: BV write failed emp_id=%s sheet=%s row=%s status=%s reason=%s detail=%s',
            emp_id, matched_sheet, matched_row, e.resp.status, (e.resp.reason or ''), detail,
        )
        return False
    except Exception:
        logger.exception('save_folder_url_to_nt_info: BV write exception emp_id=%s sheet=%s row=%s',
                         emp_id, matched_sheet, matched_row)
        return False