# API Reference — DYB NHR Portal

## Response Status Standards

블루프린트별 성공 응답 status 값이 다름. JS에서 체크 시 반드시 구분할 것.

| Blueprint | 성공 status | 실패 status |
|---|---|---|
| `eval_v2_api` (`/api/v2/*`) | `'SUCCESS'` | `'ERROR'` |
| `eval_bp` (legacy `/api/*`) | `'SUCCESS'` | `'ERROR'` |
| `auth_bp` (`/api/auth/*`) | `'OK'` | `'ERROR'` |

> ⚠️ `auth_bp`만 `'OK'` 사용. 나머지는 모두 `'SUCCESS'`. JS 조건문 작성 시 블루프린트 확인 필수.

---

## Auth Utilities (`app/auth_utils.py`)

| Decorator | Target | Behavior |
|---|---|---|
| `@admin_required` | Page routes | Redirect `/login` if no session; render `access_denied.html` if not admin |
| `@api_admin_required` | API routes | Return JSON `{'status': 'ERROR', 'message': 'Admin permission required.'}` 401 if not admin |

---

## Sync Endpoints

| Endpoint | Phases | Cooldown | Scheduler Bypass |
|---|---|---|---|
| `POST /api/nt/sync-nt` | NT sheets (재입사 자동 처리 포함) | 10 min | `X-Sync-Secret` header |
| `POST /api/nt/sync-retire` | retire sheet → `nt_retire` + Drive archive | 60 min | `X-Sync-Secret` header |
| `POST /api/nt/sync-salary` | NT sheets → `nt_salary_history/{YYYY-MM}` | 60 min | `X-Sync-Secret` header |
| `POST /api/nt/sync` | **NT + retire + salary 순차 실행** (각 phase 독립 try) | 없음 | `X-Sync-Secret` header |

**스케줄러 권장**: `/api/nt/sync` 단일 Job 으로 통합 (Cloud Scheduler 3-Job Always Free 한도 절약). NT phase 의 재입사 처리 → retire phase 의 신규 퇴사/아카이브 → salary phase 의 월별 스냅샷 순. salary 는 `month_key = YYYY-MM` 로 덮어쓰기 → 매일 호출해도 같은 달 문서만 갱신.

개별 엔드포인트(`sync-nt`, `sync-retire`, `sync-salary`) 는 관리자 수동 sync UI 용으로 유지. 각자 쿨다운 적용.

`sync-retire` 및 통합 `/api/nt/sync` 의 retire phase 성공 시 `_mark_retired_accounts()` 자동 실행 (portal_users role → `'retired'`).

---

## Report Deletion (Drive Trash)

- `POST /api/v2/trash-report` — 개별 PDF를 Drive 휴지통으로 이동 (empId + sessionId 필수; eval_v2_reports 인덱스 fast path → 미존재 시 NT 레코드에서 full_name 조회해 파일명 재구성 fallback)
- `POST /api/v2/bulk-trash-reports` — 선택 emp_ids 의 PDF chunk 단위 trash (sessionId + empIds[] 필수; per-emp fast path/fallback + per-item audit). 클라는 BulkRunner.run() 통해 chunkSize=10 사용 권장.
- Files are trashed (not permanently deleted). Recoverable from Drive trash within 30 days.
- UI: "Delete Report" button on `status.html` per-teacher row; trash icon on `admin_sessions.js` session row.

---

## eval_v2 API — 세션 제출 기간 검증

`POST /api/v2/submit-eval` 에서 KST 기준으로 세션 기간 검증:
- `today < start_date` → `ERROR: 'This session has not started yet.'`
- `today > end_date` → `ERROR: 'This session period has ended.'`
- KST 처리: `datetime.timezone(timedelta(hours=9))` (pytz 사용 금지 — 미설치)

---

## eval_v2 API — 주요 엔드포인트 목록 (`/api/v2/*`)

모든 엔드포인트는 `@api_admin_required` 적용 (`/submit-eval` 제외).

| Module | Endpoints |
|---|---|
| `sessions.py` | `create-session`, `get-sessions`, `close-session`, `reopen-session`, `delete-session` |
| `responses.py` | `submit-eval`, `get-responses`, `get-status`, `update-response`, `delete-response`, `translate-response`, `translate-all`, `export-csv` |
| `reports.py` | `generate-report`, `bulk-generate`, `trash-report`, `bulk-trash-reports`, `get-drive-folder`, `refresh-report-cache` |
| `config.py` | `get-config`, `update-questions`, `update-weights`, `get-session-questions`, `set-session-questions`, `reset-session-questions`, `get-session-weights` |
| `users.py` | `list-users`, `update-user`, `delete-user`, `create-user`, `get-email-templates`, `update-email-templates` |
| `notifications.py` | `preview-notification`, `send-notification`, `schedule-notification`, `check-scheduled`, `sync-campus-emails` |
| `sub_ctl.py` | `sub-ctl/list`, `sub-ctl/assign`, `sub-ctl/history` |
| `my_tasks.py` | `my-tasks/sessions`, `my-tasks/list` — `@api_role_required('admin','MASTER','GS','TL','STL')` (admin-only 아님). admin 만 `as_role` / `as_campus` body param 으로 view-as 가능 (audit_logs `eval_my_tasks_viewas` 5분 cooldown) |

### My Tasks 엔드포인트 동작
- **`POST /api/v2/my-tasks/sessions`** — 본인 portal_role 이 `eval_v2_config/{eval_type}_questions.roles[*].portal_role_mappings` 에 매핑된 rater role 을 보유한 활성 세션 (status=='active' + 기간 내) 만 반환
- **`POST /api/v2/my-tasks/list`** — body: `{session_id, as_campus?, as_role?}`. 평가 대상 = (eval_type 풀 from roster) ∩ (본인 campus). 본인 제출 여부 = `(emp_id, rater_role)` 페어가 `eval_v2_responses` 의 `(rater_emp_id == my_emp_id)` (1차) 또는 normalized `rater_name` (2차 fallback) 와 매칭. 응답에 `done_role_doc_ids: {role: doc_id}` 포함 (수정 모드 진입용)
- **`POST /api/v2/my-tasks/get-my-response`** — body: `{docId}`. 본인이 제출한 응답 1건 조회 (form 수정 모드 미리 채움용). rater_emp_id == session.emp_id 가드. 응답: `{scores, comment_en, comment_ko, open_answers, version, rater_role, rater_name, session_id, eval_type}`
- **`POST /api/v2/my-tasks/update-my-eval`** — body: `{docId, scores, commentEn, openAnswers, version}`. in-place 수정 (rater_role/rater_name/is_manual 보존), version+1 optimistic locking, commentKo 빈문자 reset + background translate 재트리거. 본인 doc 만 + 세션 활성 + 기간 내. VERSION_CONFLICT 응답 시 `currentVersion` 포함
- **debug 응답 필드**: `active_total / in_period / matched / global_mappings_per_type / today_kst` — 세션 0건일 때 진단 표시용
