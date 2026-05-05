# Architecture — DYB NHR Portal

## Blueprint & Route Map

| Blueprint | File | Key Routes |
|---|---|---|
| `auth_bp` | `app/auth/routes.py` | `/login`, `/register`, `/account`, `/logout`, `/api/auth/*`, `/find-account` |
| `eval_bp` | `app/evaluation/routes.py` | `/eval`, `/admin`, `/campus-dashboard`, `/editor`, `/api/find-employee`, `/api/get-all-evaluations` |
| `eval_v2_bp` | `app/eval_v2/routes.py` | `/eval-v2/`, `/eval-v2/form`, `/eval-v2/admin`, `/eval-v2/status`, `/eval-v2/sub-assignment`, `/eval-v2/annual-eval`, `/eval-v2/analysis`, `/eval-v2/campus-status`, `/eval-v2/my-tasks` |
| `eval_v2_api` | `app/eval_v2/routes.py` (re-exports) | `/api/v2/*` (46+ endpoints, all `@api_admin_required` except `/submit-eval`, `/get-questions`, `/my-tasks/*`) |
| `nt_bp` | `app/nt/routes.py` | `/nt-dashboard`, `/nt-salary`, `/api/nt/*` |
| `users_bp` | `app/users/routes.py` | `/users` |
| `retired_bp` | `app/retired/routes.py` | `/retired` |

**Convenience redirects in `main.py`**:
- `GET /status` → `redirect('/eval-v2/status')`

---

## eval_v2 Module Structure

```
app/eval_v2/
├── blueprints.py          # eval_v2_bp + eval_v2_api Blueprint objects only
├── routes.py              # page routes + bottom-imports of api/ submodules
├── questions.py           # DEFAULT_QUESTIONS, EVAL_TYPE_LABELS
└── api/
    ├── common.py          # kst_now, _batch_delete, _VALID_EVAL_TYPES, get_config/questions/weights
    ├── sub_ctl.py         # 3 routes: /sub-ctl/list, /assign, /history
    ├── config.py          # 7 routes: questions, weights, session-questions config
    ├── responses.py       # 11 routes: submit, get, update, delete, translate, CSV export
    ├── sessions.py        # 5 routes: create, get, close, reopen, delete
    ├── reports.py         # generate, bulk-generate, trash-report, bulk-trash-reports, drive-folder, cache
    ├── notifications.py   # 5 routes: preview, send, schedule, check-scheduled, sync-campus-emails
    ├── users.py           # 6 routes: list, update, delete, create, email-templates (GET+POST)
    └── my_tasks.py        # 4 routes: /my-tasks/sessions, /my-tasks/list, /my-tasks/get-my-response, /my-tasks/update-my-eval — GS/TL/STL/admin 진입 dashboard + 본인 평가 수정
```

> ⚠️ **Circular import rule**: `blueprints.py` imports ONLY from Flask. All api modules import from `blueprints.py`. `routes.py` imports api modules at the **bottom** (after page route definitions).
>
> ⚠️ **새 api 모듈 추가 시**: `app/eval_v2/routes.py` 하단에 `import app.eval_v2.api.새모듈` 추가 필요. 없으면 라우트 미등록.

---

## Services Layer

| File | Key Functions |
|---|---|
| `firebase_service.py` | `initialize_firebase()`, `get_firestore_client()`, `sync_nt_to_firestore()`, `sync_retire_to_firestore()`, `sync_salary_history_to_firestore()`, `get_sync_status()`, `update_sync_status()` |
| `user_service.py` | `get_user_by_emp_id()`, `get_user_by_email()`, `get_all_users()`, `register_user()`, `update_user_role()`, `update_user()`, `delete_user()` |
| `google_sheets.py` | `fetch_nt_info(sheet_name)`, `fetch_retire_info()`, `fetch_emp_db()`, `fetch_master_emails()`, `fetch_all_evaluations_data()`, `fetch_teacher_detail()`, `update_teacher_detail_data()` |
| `otp_service.py` | `generate_otp()`, `store_otp()`, `verify_otp()`, `send_otp_email()`, `send_reset_email()`, `send_eval_reminder_email()` |
| `report_service.py` | `build_report_context()`, `render_report_html()`, `html_to_pdf()`, `calc_ranks_map()` |
| `drive_service.py` | `get_or_create_eval_folder()`, `upload_pdf_to_folder()`, `move_folder_to_retired()`, `restore_folder_from_retired()`, `save_folder_url_to_nt_info()`, `find_eval_folder()`, `find_report_in_folder()`, `find_session_reports()`, `trash_file()` |
| `openai_service.py` | `translate_evaluation()`, `translate_open_answers()` |
| `audit_service.py` | `log_audit(action, actor, target, details)` |
| `nt_cache_service.py` | `get_nt_record(emp_id)`, `refresh_cache()` (24h TTL) |
| `roster_cache_service.py` | `get_roster()`, `refresh_cache()` (1h TTL) |
| `campus_password_service.py` | `verify_campus_password()`, `set_campus_password()` |

---

## Auth Flows

### Google OAuth
```
Firebase Client → ID token → POST /api/auth/firebase
→ is admin? → session set → redirect /
→ is existing user? → session set → redirect / or /retired
→ new user? → STAFF_VERIFY_REQUIRED → verify-staff → complete-google-login
```

### Email + OTP (2FA)
```
Firebase Client → ID token → POST /api/auth/firebase
→ OTP_REQUIRED → send_otp_email()
→ POST /api/auth/verify-otp → look up DB role → session set → redirect / or /retired
```

**OTP**: 6-digit, 5 min expiry, max 5 attempts

### Session Fields
```python
admin_auth     # bool
admin_code     # 'admin' | 'NET' | 'retired' (legacy: '퇴사')
admin_email    # str
emp_id         # str
emp_name       # str
display_name   # str
login_type     # 'google' | 'email'
logged_in_at   # ISO8601 KST 문자열 — force-logout 기준 시각
campus_auth    # bool (campus staff)
campus_code    # str
```

`auth/routes.py`의 `_set_auth_session(email, role, name, login_type, emp_id)` 헬퍼가 `logged_in_at` 포함 8개 필드를 원자적으로 세팅. 새 로그인 경로 추가 시 이 함수 사용.

### Force Logout 메커니즘
관리자가 role/비밀번호를 변경하면 `portal_users.force_logout_at` (ISO8601 KST) 가 기록되고, 다음 요청에서 `logged_in_at < force_logout_at` 인 세션은 즉시 무효화된다. 자세한 동작/트리거 위치는 `docs/known-issues.md` 참고.

---

## User Roles & Access

| Role | Description | Redirect |
|---|---|---|
| `admin` | Super admin | `/` (full access) |
| `NET` | Regular staff | `/` (standard portal) |
| `retired` | Former employee | `/retired` (restricted) |

### Retired Account Automation
- `POST /api/nt/sync-retire` → syncs NT RETIREMENT sheet → `nt_retire` collection → auto-calls `_mark_retired_accounts()` in `app/nt/routes.py`
- `_mark_retired_accounts()`: cross-checks `nt_retire` emp_ids against `portal_users`, updates `role='retired'` via `update_user_role(emp_id, 'retired', 'system')`
- `main.py` `before_request`: redirects `retired` (또는 legacy `'퇴사'`) users away from all routes except `/retired`, `/logout`, `/account`, `/api/auth/`, `/api/retired/`
- Legacy `'퇴사'` 값: Firestore 기존 데이터 호환을 위해 `RETIRED_ROLES = {'retired', '퇴사'}` (`app/constants.py`)로 병행 허용. 신규 기록은 `'retired'` 사용.

---

## Extensions & Middleware

- **Flask-Caching**: SimpleCache, 60s default. NT cache: 24h, Roster cache: 1h
- **Flask-Limiter**: 200/day, 50/hour global. Per-endpoint: 3–10/min on auth routes
- **Session**: 15 min lifetime, Secure + HttpOnly + SameSite=Lax
- **CSRF**: Origin header check on POST/PUT/DELETE (exempt: `/api/auth/`, `/api/find-employee`)
- **Security headers**: X-Frame-Options DENY, CSP, HSTS, etc.
