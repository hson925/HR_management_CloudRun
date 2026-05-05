# Data Models — DYB NHR Portal

## Firestore Collections

| Collection | Doc ID | Purpose |
|---|---|---|
| `portal_users` | emp_id.lower() | All user accounts (emp_id, name, role, firebase_uid, email, campus, registered_at, updated_at, updated_by, notes) |
| `nt_dyb` / `nt_sub` / `nt_rnd` / `nt_brand_x` / `nt_brand_y` / `nt_brand_z` | emp_id | Current NT staff per campus (27+ fields from NT Info sheet) |
| `nt_retire` | emp_id | Currently retired staff (emp_id, retire_date, name, campus, start_date, sheet) + Drive archive meta (eval_folder_url, eval_folder_id, previous_parent_folder_id, archived_at, already_archived). 재입사 시 NT sync 가 자동으로 이 문서를 삭제하므로 "현재 퇴사 상태" 만 표현. 이력 자체는 retire 시트(Google Sheets)에 영구 보관. |
| `nt_salary_history` | YYYY-MM → subcollection `records/{emp_id}` | Monthly salary snapshots |
| `nt_sync_status` | nt / retire / salary | Last sync timestamp + cooldown tracking |
| `eval_v2_sessions` | UUID | Eval sessions (label, status, dates, eval_types, weights, notification_schedule) |
| `eval_v2_responses` | UUID | Individual eval responses (scores, comments, open_answers, translation_status) |
| `eval_v2_reports` | `{emp_id}__{session_id}` | PDF 보고서 file_id 인덱스 (file_id, folder_id, created_at, updated_at). fast trash path — Drive `find_eval_folder` + `find_report_in_folder` 회피 |
| `eval_v2_config` | questions / weights | Custom questions & weights per eval type |
| `email_settings` | templates / campus_emails / email_templates | Email notification config |
| `campus_passwords` | campus_code | Hashed campus staff passwords |
| `audit_logs` | auto-ID | Audit trail (action, actor, target, details, timestamp) |

> ⚠️ **eval_v2_sessions doc ID = session label**: Firestore doc ID로 label 값을 그대로 사용하므로 `/` 포함 불가. 세션 생성 시 label 검증 필요.

---

## Google Sheets IDs

| ID | Purpose |
|---|---|
| `REDACTED_ROSTER_SHEET_ID` | ROSTER — teacher roster, portal Users tab, campus passwords |
| `1aIMLmkZgiZ7x3Punq_dqwHXh-aXklagJJlYkIOgLsJc` | DATA — evaluation tracking (Main sheet) |
| `REDACTED_EMP_DB_SHEET_ID` | NT_INFO / EMP_DB — current NT staff + employee passport DB |
| `REDACTED_RETIRE_SHEET_ID` | RETIRE — retirement records (시트: 퇴직 2024, 퇴직 2025, ...) |

### NT INFO Sheet → Firestore Collection Mapping

| Sheet Name | Collection |
|---|---|
| `dyb` | `nt_dyb` |
| `sub` | `nt_sub` |
| `R&D_SIS` | `nt_rnd` |
| `Brand X` | `nt_brand_x` |
| `Brand Y` | `nt_brand_y` |
| `Brand Z` | `nt_brand_z` |
| `퇴직 YYYY` | `nt_retire` |

---

## Campus Codes

| Code | Campus | Code | Campus |
|---|---|---|---|
| CMA | Campus A | CMB | Campus B |
| CMC | Campus C | CMD | Campus D |
| CME | Campus E | CMF | Campus F |
| CMG | Campus G | CMH | Campus H |
| CMI | Campus I | CMJ | Campus J |
| CMK | Campus K | CML | Campus L |
| CMM | Campus M | SUB | SUB |

---

## Google Drive Folder Structure

- **Eval folder parent** (`EVAL_FOLDER_ID`): `1Deh5T8qOBx49vAs2pT3BJikazRbeNC75`
  - 하위에 개인별 폴더: `{EMP_ID}_{full_name}/`
    - 하위에 평가 PDF: `{EMP_ID}_{full_name}_{session_label}_eval.pdf`
- **Retired folder parent** (`RETIRED_EVAL_FOLDER_ID`): `1IqO7M_HmOiWXhi7xWMUSJ3YYsTjQWm0T`
- **Delegated Drive user**: `noreply@example.com`

### Drive API 제약 (Shared Drive)
- `in ancestors` 쿼리 → **400 에러**. 절대 사용 금지.
- 2-step `in parents` 패턴 사용: ① 하위 폴더 목록 → ② 각 폴더에서 파일 검색 → ③ Python 패턴 매칭.
- 레퍼런스 구현: `drive_service.py` → `find_session_reports()`
- 파일명 내 `.` 포함 검색어도 Drive API 오류 가능 → MIME type 필터 + Python 패턴 매칭으로 대체.
