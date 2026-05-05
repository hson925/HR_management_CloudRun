# HR / 평가 포털 — Flask + Firebase

> **Note** — 본 저장소는 약 150명 규모 조직을 위해 1인 풀스택으로 설계·개발·운영한 사내 HR 포털의 **공개용 미러** 입니다. 코드 구조와 구현 품질을 검토하실 수 있도록 실제 시트 ID, 내부 이메일, 캠퍼스 명, 평가 문항, 급여 대시보드 모듈은 placeholder 로 치환하거나 제거했습니다. 원본 저장소는 비공개입니다.

직원 온보딩, 역할 기반 접근 제어, 다회차·다언어 동료/관리자 평가, PDF 리포트 생성, 사내 공지사항을 통합한 풀스택 웹 애플리케이션. 설계부터 배포·운영까지 담당했습니다.

---

## 한눈에 보기

| 항목 | 내용 |
|---|---|
| **규모** | 활성 사용자 ~150명 · Flask 블루프린트 13개 · Python 약 17K LOC · Jinja 템플릿 31개 · JS 모듈 26개 |
| **기술 스택** | Python 3 · Flask · Firebase (Firestore + Auth + Storage) · Google Cloud Run · Docker · Tailwind CSS · Vanilla JS · WeasyPrint · OpenAI API |
| **외부 연동** | Google OAuth (사내·외부 평가자 분리 클라이언트 2개) · Google Sheets API · Google Drive API (Domain-Wide Delegation) · Gmail API |
| **역할** | 단독 엔지니어 — 설계 / 구현 / 배포 / 운영 / 장애 대응 전부 |

---

## 주요 기능

### 1. 인증 및 접근 제어
- **Firebase Auth** 기반 이메일·비밀번호 로그인 + Google OAuth 병행
- **OAuth 클라이언트 2개 분리** — 사내 직원용(전체 포털) / 외부 평가자용(평가 화면만). 토큰 audience 자체가 권한 경계 역할
- **OTP 이메일 인증** (Gmail API) — 비밀번호 재설정 및 민감 작업
- **회차별 Passcode 게이트** — 평가 회차 진입 시 토글 가능, 사내 직원은 면제
- **역할 기반 권한** — admin / manager / staff + 관리자가 런타임에 편집 가능한 Custom Role 레지스트리 (`app/admin/role_routes.py`)
- **Rate limiting** — Flask-Limiter 사용. 관리자 엔드포인트는 별도 키(`admin_rate_key`) 로 분리하여 일반 활동이 글로벌 50/hr 제한에 갇히지 않도록 처리

### 2. 평가 시스템 (eval v2)
Google Sheets 기반의 v1 을 Firestore 기반으로 전면 재설계한 다회차·다평가자 엔진입니다.

- **가변 점수 척도** — 문항별 `max_score` 2~10 자유 설정, 점수별 설명 텍스트 첨부 가능, GPT-4o-mini 로 한↔영 자동 번역
- **다중 평가 유형** — 한 회차 안에 동료(KT), 분원장, 팀장, 슈퍼바이저(STL), 본인 평가가 공존
- **워크플로우** — 임시저장 → 제출 → 관리자 잠금. 응답 문서에 `version` 필드(optimistic lock) 로 동시 편집 충돌 처리
- **My Tasks 대시보드** — 각 사용자의 회차별 미제출 항목 한 화면 집계
- **Status 대시보드** — 캠퍼스/직책별 제출 현황 실시간 + CSV 내보내기
- **연간 평가 리포트** — 교사별 A4 PDF (WeasyPrint), 점수 분해 + 인상정책 매핑 + 양언어 본문

### 3. 공지사항
- 리치 텍스트 에디터 (Quill) + 이미지 업로드는 Firebase Storage 직접 업로드, 서버단에서 Pillow 로 리사이즈 (1920px / quality 82, 10MB cap)
- 대상 지정 — 캠퍼스별 / 역할별 / `__all__` 센티넬 (전사)
- 읽음 추적 + 홈 대시보드 미열람 배지

### 4. 운영 및 관측
- **Cloud Run** 배포, Gunicorn 1 worker × 8 threads, Docker 단일 단계 빌드
- **구조화 JSON 로깅** → Cloud Logging
- **Stale-API 탐지기** — `main.py` 의 404 핸들러가 제거된 엔드포인트로 향한 JS fetch 를 감지하여 `stale-api-reference:` 경고 로그를 남김. 블루프린트 제거 후 배포 직후 누락 참조 발견용
- **Scheduler bypass 헤더** (`X-Sync-Secret`) — cron 트리거 동기화 엔드포인트 보호

### 5. UX 디테일
- 사전 컴파일된 Tailwind + **완전 토큰화된 다크 모드** (`html[data-theme="dark"]`), 시맨틱 컴포넌트 클래스 (`.btn-primary`, `.dyb-card`, `.dyb-table`, `.dyb-datepicker`, `.dyb-pagination`)
- `grid-template-rows: 0fr → 1fr` 애니메이션 패턴으로 토글 시 layout-thrashing 회피 (max-height 측정 불필요)
- 두 가지 툴팁 메커니즘 공존 — `.tt` (CSS only, `pre-wrap`) / `.ae-tooltip` (JS 구동, `pre-line`)
- KO ↔ EN 토글 시 DOM 재렌더 없이 `main / sub` 라벨 swap

---

## 아키텍처

```
┌─────────────────────────────────────────────────────────┐
│                       Browser                           │
│  Jinja2 + Tailwind + vanilla JS · Firebase Web SDK      │
└──────────────────────┬──────────────────────────────────┘
                       │ HTTPS
┌──────────────────────▼──────────────────────────────────┐
│              Flask app (Cloud Run, Tokyo)               │
│  ┌─────────┐ ┌─────────┐ ┌─────────────┐ ┌────────────┐ │
│  │  auth   │ │ eval_v2 │ │announcements│ │   admin    │ │
│  └─────────┘ └─────────┘ └─────────────┘ └────────────┘ │
│  ┌─────────┐ ┌─────────┐ ┌─────────────┐ ┌────────────┐ │
│  │  users  │ │ retired │ │   notif.    │ │   logs     │ │
│  └─────────┘ └─────────┘ └─────────────┘ └────────────┘ │
│  Service layer: firebase, drive, otp, cache, openai     │
└──┬─────────┬─────────────┬───────────────┬──────────────┘
   │         │             │               │
   ▼         ▼             ▼               ▼
┌─────┐ ┌─────────┐  ┌──────────┐   ┌─────────────┐
│ FB  │ │  Cloud  │  │  Google  │   │   OpenAI    │
│Auth │ │Firestore│  │ Sheets/  │   │  GPT-4o-mini│
│     │ │ Storage │  │  Drive   │   │  (번역)      │
└─────┘ └─────────┘  └──────────┘   └─────────────┘
```

핵심 서비스 모듈 (`app/services/`):
- `firebase_service.py` — Firestore 클라이언트, env-var 로 주입된 JSON 으로 Admin SDK 초기화
- `drive_service.py` — Domain-Wide Delegation, Shared Drive 상에서 `in parents` 2-step 탐색 (Drive 의 `in ancestors` 는 Shared Drive 에서 HTTP 400 — 회피용)
- `otp_service.py` — Gmail API 발신 + Firestore 기반 코드 저장 (TTL 적용)
- `cache_service.py` — 자주 조회되는 데이터(공지 피드 등) 인메모리 캐시

---

## 디렉터리 구조

```
.
├── app/
│   ├── auth/             # 이메일/비밀번호, OAuth (클라이언트 2종), OTP, 세션
│   ├── eval_v2/          # 평가 엔진, 임시저장, 리포트, 관리자
│   │   └── api/          # JSON 엔드포인트 (drafts, users, scoring)
│   ├── admin/            # Role Admin (Custom Role 추가/Legacy deprecation)
│   ├── announcements/    # 공지사항 + 이미지 업로드
│   ├── users/            # 직원 명단, 프로필 편집
│   ├── retired/          # 퇴직자 아카이브
│   ├── notifications/    # 인앱 알림
│   ├── logs/             # 관리자 감사 로그 뷰어
│   ├── legal/            # 개인정보 / 약관
│   ├── services/         # 외부 연동 (Firebase, Drive, OTP, OpenAI)
│   ├── utils/            # 인증 헬퍼, 시간대, 포매팅
│   ├── templates/        # Jinja2 템플릿 (31개)
│   └── static/           # Tailwind CSS, JS 모듈, 폰트
├── docs/
│   ├── architecture.md       # 블루프린트 / 서비스 맵
│   ├── api-reference.md      # 엔드포인트 카탈로그
│   ├── data-models.md        # Firestore 스키마, Sheets 레이아웃
│   ├── business-rules.md     # 평가 규칙, 인상 정책
│   └── ui-patterns.md        # 디자인 토큰, 애니메이션, 컴포넌트
├── main.py                   # Flask 앱 팩토리, 블루프린트 등록
├── config.py                 # 상수 (시트 ID / 캠퍼스 코드 — 본 미러에서는 REDACTED)
├── requirements.txt
├── Dockerfile                # Cloud Run 용 Gunicorn 엔트리포인트
├── firebase.json             # Firestore rules + indexes 설정
├── firestore.rules
├── firestore.indexes.json
├── tailwind.config.js
└── package.json              # Tailwind 빌드 전용
```

---

## 짚어볼 만한 설계 결정

궁금해할 만한 비자명한 선택들:

- **OAuth 클라이언트 왜 둘로 분리?** 외부 평가자가 직원 포털을 보면 안 됩니다. 토큰 audience 자체가 1차 방어선이 되도록 설계 — 앱 레벨 역할 체크에만 의존하지 않기 위함.
- **평가 데이터에 Postgres 가 아닌 Firestore 를 쓴 이유?** 평가 회차마다 문항별 점수 + 자유 코멘트가 sparse + 회차마다 증가하는 형태라 document model 이 적합. 기존 Firebase 프로젝트 재사용으로 추가 인프라도 불필요.
- **토글 애니메이션에 `grid-template-rows: 0fr → 1fr` 트릭을 쓴 이유?** 전통적인 `max-height: 0 → measured-height` 방식은 JS 측정 패스가 필요하고, 컨텐츠 변경 시 재측정/재플로우 발생. grid 트릭은 순수 CSS, 부드러운 애니메이션, 컨텐츠 변경에도 안정적.
- **Drive 검색에 `in ancestors` 가 아닌 `in parents` 2-step 을 쓴 이유?** Shared Drive 에서 `in ancestors` 는 HTTP 400 반환. 폴더→파일 두 단계로 나눠 호출하는 것이 정식 방법. 한 번 시행착오로 학습 후 영구 패턴으로 등록.
- **평가 응답에 `version` 필드를 둔 이유?** 관리자와 사용자가 동시 편집하는 드문 경우를 위한 optimistic lock. 매번 Firestore 트랜잭션을 거는 것보다 비용이 낮음.
- **pytz 미사용** — 배포 환경에 pytz 가 없어 KST 처리는 `datetime.timezone(timedelta(hours=9))` 로 직접 구성.

---

## 로컬 실행

본 공개 미러는 자체 자격 증명 없이는 end-to-end 동작하지 않지만, 셋업 패턴은 다음과 같습니다.

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # 본인의 값으로 채워주세요
npm install && npx tailwindcss -i tailwind.input.css -o app/static/css/tailwind.css
python3 main.py               # http://localhost:5000
```

필요한 자격증명:
- Firebase 프로젝트 (Firestore + Auth + Storage 활성화)
- Google Cloud 서비스 계정 JSON (Sheets/Drive 용 — `.env.example` 참고)
- OpenAI API key (한↔영 자동 번역에만 사용)
- Google OAuth 클라이언트 자격증명 (외부 평가자 경로까지 쓸 경우 2개)

---

## 본 공개 미러에서 제거/치환된 항목

운영 회사의 데이터와 IP 보호를 위해 다음 항목들이 제거되거나 placeholder 로 치환되었습니다.

- **HR / NET / 급여 블루프린트** (`app/nt/`) — 가장 민감한 UI 모듈 전체
- 실제 Google Sheets ID, GCP 프로젝트 번호, Cloud Run 서비스 URL, 내부 이메일 도메인, Firebase Web API key
- 실제 평가 문항 — `q1..q5` 일반 placeholder 로 대체. 런타임에 Firestore 가 override 하므로 동작 영향 없음
- 실제 캠퍼스 명 → `Campus A..M`, 캠퍼스 3-letter code → `CMA..CMM`
- 내부 문서 — admin/user 로그인 매뉴얼, `known-issues.md` (60KB 사내 디버깅 일기), 일회성 데이터 마이그레이션 스크립트, 사내 AI 페어 프로그래밍 컨텍스트 파일

본 공개 미러의 git history 는 fresh start 입니다 — 원본 비공개 저장소의 history 는 노출되지 않습니다.

---

## 라이선스

MIT — [LICENSE](./LICENSE) 참고.
