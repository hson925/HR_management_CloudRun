# Business Rules — DYB NHR Portal

코드에 없는 운영 맥락. 기능 추가 전 반드시 확인.

---

## eval_type 별 평가 대상

| eval_type | 대상 | 비고 |
|---|---|---|
| `position` | 포지션별 평가 (GS, KT, 분원장, TL) | 캠퍼스별 운영 |
| `regular` | 정기 평가 | 전체 NT 대상 |
| `tl` | Team Leader 평가 | TL 대상 |
| `sub` | SUB 캠퍼스 전용 (CTL, TL) | SUB 캠퍼스만 |
| `stl` | STL 평가 (NET Coordinator, NHR) | SUB STL 대상 |

---

## Rater ↔ Portal role 매핑 (My Tasks 진입 권위 데이터)

평가자가 어떤 rater role 자격을 갖는지는 `eval_v2_config/{eval_type}_questions.roles[*].portal_role_mappings: string[]` 가 결정. admin 이 `/eval-v2/admin` 설정 탭의 chip UI 로 매핑 (예: regular eval 의 'GS' rater role → `['GS', 'admin']`).

- **권위 데이터**: 항상 글로벌 config 가 우선 (세션 snapshot 의 mapping 무시) — admin 이 매핑 변경하면 활성 세션에도 즉시 반영
- **`__public__` sentinel**: 비로그인 외부 평가자 자격을 표시하는 metadata. portal_users.role 에 들어가지 않는 격리 값. 현재는 admin UI 표시·관리 목적 (검증 비즈니스 로직 미적용)
- **portal 로그인 사용자 `/eval-v2/my-tasks` 진입**:
  1. 본인 `portal_users.role` 매핑된 rater role 보유 활성 세션 dropdown
  2. 직원 카드 클릭 → `/eval-v2/form?empId=X&session=Y&fromMyTasks=1`
  3. form.html 의 step-rater 가 매핑된 첫 rater role 자동 선택 + role 선택 UI 숨김 (matched 시) / 노출 (fallback, 매핑 0건)
  4. 본인 제출 여부 = `eval_v2_responses` 의 `rater_emp_id == my_emp_id` (1차) 또는 normalized `rater_name` 매칭 (2차 fallback)
- **비로그인 외부 평가자**는 기존 `public_form` 흐름 유지 (passcode + 자유 입력 + role dropdown 직접 선택)

레퍼런스: `app/eval_v2/api/my_tasks.py`, `app/eval_v2/api/config.py` `_VALID_PORTAL_ROLE_MAPPINGS`, `docs/api-reference.md` `My Tasks 엔드포인트 동작`.

---

## 본인 평가 수정 정책 (My Tasks 의 Edit Mode)

portal 로그인 사용자 (admin/MASTER/GS/TL/STL) 가 my-tasks 의 done 카드 클릭 시 본인이 제출한 평가를 in-place 수정 가능. 정책:

- **권한**: `eval_v2_responses.rater_emp_id == session.emp_id` 인 doc 만 수정 (서버 가드). rater_emp_id 가 빈문자 (Phase E 이전 legacy 응답) 면 수정 불허 — admin 의 update-eval 로 위임
- **시간대**: 세션 status='active' + KST 기간 내 (start_date ≤ today ≤ end_date). closed 또는 만료 세션은 수정 차단 → admin status modal update-eval 로 위임
- **In-place 수정**: 같은 doc_id 의 scores/comment_en/open_answers 만 수정. **rater_role / rater_name / is_manual 변경 안 함** (운영 정책: 단일 매핑이라 role 변경 의미 없음). role 변경 원하면 신규 평가로 재제출
- **comment_ko 처리**: 클라가 commentEn 만 보냄 → 서버가 commentKo 빈문자 reset + background translate 재트리거 (admin update-eval 와 동일 패턴)
- **Optimistic locking**: version+1, payload_hash 재계산. 다른 곳에서 수정 시 VERSION_CONFLICT 응답 → 클라 "Reload latest" 옵션 (showConfirmModal)
- **추적 메타**: `self_edited_at`, `self_edited_by_emp_id` 필드 + audit_logs `eval_self_update` 기록
- **dedup 영향 없음**: in-place 수정이라 select_effective_responses 의 (emp_id, rater_role, normalized rater_name) 키 동일

레퍼런스: `app/eval_v2/api/my_tasks.py` `api_my_tasks_get_my_response` / `api_my_tasks_update_my_eval`. admin update-eval 과 권한 격리 (`/api/v2/update-eval` 는 admin 전용 그대로).

---

## 세션 운영 규칙

- 세션 doc ID = session label 값 그대로 사용 → `/` 포함 불가
- 세션 상태: `active` (진행중) / `closed` (마감)
- 제출 기간: KST 기준 `start_date` ~ `end_date`
- `is_test` 자동 태깅: emp_id에 `test`, `demo`, `tmp`, `dummy` 포함 시 자동으로 `is_test=true`

---

## NET 평가 대상 사번 포맷

- **`/api/v2/get-questions` (평가 양식 로드) 는 NET 교사 평가 전용** — `empId` 는 반드시 `N + 숫자 5자리` (대소문자 무관, 서버에서 `.lower()` 정규화 후 `^n\d{5}$` 검증).
- 내부 직원·외부 평가자 등 다른 ID 포맷은 이 엔드포인트에서 `INVALID_FORMAT` 으로 차단됨. 같은 포맷이 아닌 평가 대상이 추가되면 이 규칙을 먼저 재검토해야 함.
- 클라이언트(`form.html`, `public_form.html`) 의 `NET_EMP_ID_RE` 정규식과 서버 `_NET_EMP_ID_RE` (`app/eval_v2/api/config.py`) 는 동일 규칙 — 한쪽 수정 시 반드시 양쪽 동기화.
- Annual Eval 용 `_EMP_ID_RE = ^[a-zA-Z0-9_\-]{1,30}$` (`app/eval_v2/api/annual_eval/_helpers.py`) 와는 **다른 목적** (anti-injection 가드). 혼동 금지.

---

## 이메일 초안 (Draft) 그룹

- `CAMPUS` 그룹: 선택된 캠퍼스들의 GS/TL 담당자에게 발송
- `SUB` 그룹: SUB 캠퍼스 고정 (STL Draft 전용)

> ⚠️ Draft 생성은 `eval_bp`의 레거시 `/api/create-drafts` 사용 (eval_v2_api 아님)

---

## 퇴직자 처리 흐름

1. NT RETIREMENT 시트 업데이트
2. `POST /api/nt/sync-retire` 호출 → `nt_retire` 컬렉션 동기화
3. `_mark_retired_accounts()` 자동 실행 → `portal_users`의 해당 emp_id role을 `'퇴사'`로 변경
4. 이후 로그인 시 `/retired`로 리다이렉트
5. Drive 개인 평가 폴더 → `move_folder_to_retired()` 로 퇴직자 폴더로 이동 (수동)

---

## 보고서 파일명 규칙

```
{EMP_ID}_{full_name}_{session_label}_eval.pdf
```
예: `ABC123_홍길동_2025-1Q_eval.pdf`

Drive 폴더 구조:
```
EVAL_FOLDER_ID/
└── {EMP_ID}_{full_name}/
    └── {EMP_ID}_{full_name}_{session_label}_eval.pdf
```

---

## 캠퍼스 비밀번호 (Campus Staff 접근)

- `campus_passwords` 컬렉션에 campus_code별 해시 저장
- `campus_password_service.py`의 `verify_campus_password()` / `set_campus_password()` 사용
- 캠퍼스 스태프 세션: `campus_auth=True`, `campus_code='{코드}'`
