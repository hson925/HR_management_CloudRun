# UI Patterns — DYB NHR Portal

## 디자인 토큰

```css
--primary: #B01116;          /* DYB 브랜드 레드 */
--primary-container: #c41118;
--surface: #ffffff;
--surface-low: #f4f4f5;
--on-surface: #111114;
--on-surface-variant: #52525b;
--outline: #71717a;
--outline-variant: #d4d4d8;
--error: #ba1a1a;
```

---

## 언어 규칙

- 모든 **사용자 대면 UI 텍스트**: 영어 우선, 한국어 부제목
- 백엔드 주석/변수명: 한국어 허용

---

## 애니메이션 패턴 (사용자 승인 완료)

> ⚠️ `max-height: 0 → 4000px` **고정 상한값** 금지 (콘텐츠에 미달한 구간이 보이지 않는 딜레이로 체감됨).
> ✅ 허용되는 대안 2 가지:
> 1. `grid-template-rows: 0fr → 1fr` 트릭 (브라우저 지원: Chrome 117+ / Firefox 121+ / Safari 17.4+)
> 2. **측정 기반 max-height** — JS 로 `offsetHeight`/`scrollHeight` 측정 후 픽셀값으로 설정 (하위 브라우저 안전)

### 수직 아코디언 (`grid-template-rows`) — 방식 1

콘텐츠 높이에 비례해 즉시 반응. `max-height: 0 → 4000px`는 보이지 않는 구간이 생겨 딜레이처럼 느껴짐.

```css
.accordion-body {
  display: grid;
  grid-template-rows: 0fr;
  overflow: hidden;
  transition: grid-template-rows 0.3s ease;
}
.accordion-body.expanded { grid-template-rows: 1fr; }

/* Tailwind p-* 패딩이 height=0일 때도 공간을 만들므로 명시적 오버라이드 필수 */
.accordion-body > div {
  overflow: hidden;
  min-height: 0;
  padding-top: 0;
  padding-bottom: 0;
}
.accordion-body.expanded > div {
  padding-top: 12px;
  padding-bottom: 12px;
}
```

### 수평 인라인 확장 (`grid-template-columns`)

Position 필터 패널, Bulk Action Bar 등 가로 방향 슬라이드인에 사용.

```css
.panel {
  display: grid;
  grid-template-columns: 0fr;
  opacity: 0;
  overflow: hidden;
  transition: grid-template-columns 0.28s ease, opacity 0.22s ease;
}
.panel.open { grid-template-columns: 1fr; opacity: 1; }
.panel > div { min-width: 0; overflow: hidden; white-space: nowrap; }
```

### 측정 기반 max-height — 방식 2 (섹션 아코디언·서브메뉴·커스텀 드롭다운)

JS 가 펼치기/접기 시점의 픽셀 높이를 **직접 측정**해 주입한다. 브라우저 지원 무관하게 동작.

```javascript
// COLLAPSE (현재 높이 → 0)
body.style.maxHeight = body.offsetHeight + 'px';   // 현재 pixel 로 snap
void body.offsetHeight;                             // reflow 커밋
body.style.maxHeight = '0px';                       // 트랜지션 시작
body.style.paddingTop = '0'; body.style.paddingBottom = '0';
body.style.opacity = '0';

// EXPAND (0 → scrollHeight)
body.style.maxHeight = body.scrollHeight + 'px';    // ⚠️ border 미포함 주의 — 내부 border 있으면 offsetHeight 사용
body.style.paddingTop = ''; body.style.paddingBottom = '';  // 원래 padding 복원
body.style.opacity = '1';
// transitionend 후 cleanup
body.addEventListener('transitionend', function onEnd(e) {
  if (e.propertyName !== 'max-height') return;
  body.style.maxHeight = 'none';  // 이후 콘텐츠 변경 자유
  body.removeEventListener('transitionend', onEnd);
});
```

CSS:
```css
.collapsible {
  overflow: hidden;
  transition:
    max-height .3s cubic-bezier(.22, 1, .36, 1),
    opacity .22s ease-out,
    padding-top .3s cubic-bezier(.22, 1, .36, 1),
    padding-bottom .3s cubic-bezier(.22, 1, .36, 1);
}
```

**사용처**:
- `annual_eval.html` `.ae-section-body` — 섹션 접기/펴기 (`toggleAeSection`)
- `layout.html` 사이드바 서브메뉴 — Evaluation / NET / Board / Server Settings (`_animSubmenuToggle`)
- `annual_eval.html` `.ae-sel-panel` — 커스텀 드롭다운 패널 (max-height 트랜지션은 제거, 측정값 snap 만 사용)

**주의**:
- `scrollHeight` 는 **border 미포함** → 내부 `border: 2px` 가 있으면 `offsetHeight` 사용 (아니면 하단 테두리가 `overflow: hidden` 에 잘림). 관련 known-issue: `docs/known-issues.md` → scrollHeight vs offsetHeight.
- 빠른 연타 시 이전 `transitionend` 리스너가 살아있어 상태 꼬임 → `_animCleanup` 클로저로 이전 핸들러 해제. 레퍼런스: `_animSubmenuToggle`.

### 페이드 + 슬라이드 드롭다운 (헤더 드롭다운·커스텀 select 패널)

`opacity` + `transform: translateY + scaleY` 조합. 요소는 항상 DOM 에 존재, `.open` 클래스 토글로 표시/숨김.

```css
.floating-panel {
  opacity: 0;
  transform: translateY(-8px) scaleY(.96);
  transform-origin: top right;  /* 헤더 우상단 기준 / 드롭다운은 top center */
  pointer-events: none;
  transition: opacity .18s ease-out, transform .24s cubic-bezier(.22, 1, .36, 1);
}
.floating-panel.open {
  opacity: 1;
  transform: translateY(0) scaleY(1);
  pointer-events: auto;
}
```

**사용처**:
- `#notif-dropdown`, `#profile-dropdown` (layout.html 헤더)
- `.ae-sel-panel` (annual_eval 커스텀 드롭다운) — `position: fixed` + JS 좌표 계산으로 부모 `overflow:hidden` 탈출

### 토글 요소 (체크박스·버튼) — DOM 재렌더 금지

> ⚠️ `innerHTML` 재작성으로 요소를 추가/삭제하면 레이아웃 리플로우가 CSS 트랜지션을 끊음.

**패턴: 항상 DOM에 렌더 + CSS 클래스로 표시/숨김**

```css
/* 래퍼: 비활성 시 width=0, 활성 시 width=20px */
.cb-wrap {
  display: inline-flex;
  align-items: center;
  flex-shrink: 0;
  width: 0;
  overflow: hidden;
  opacity: 0;
  transition: width 0.18s ease, opacity 0.15s ease;
}
.bulk-mode .cb-wrap { width: 20px; opacity: 1; }

/* 버튼: 비활성 시 display:none */
.toggle-btn { display: none; }
.bulk-mode .toggle-btn { display: inline-flex; align-items: center; }
```

```javascript
// 토글 시 DOM 재렌더 없이 클래스만 변경
container.classList.toggle('bulk-mode', isActive);
// 닫을 때 체크박스 상태 초기화
if (!isActive) document.querySelectorAll('.bulk-checkbox').forEach(cb => cb.checked = false);
```

**레퍼런스 구현**: `status.html` + `admin_status.js` + `admin_sessions.js`의 Bulk Report 기능

---

## 컴포넌트 스타일 규칙

### 버튼 클래스
- `.btn-primary` — 주요 액션, 기본 dark
- `.btn-secondary` — 보조 액션, 테두리만
- `.btn-danger` — 삭제/위험 액션, 빨간 테두리
- `.btn-bulk` / `.btn-bulk-action` — Bulk 전용

### 카드
```css
.campus-card { border: 2px solid #b8bcc4; border-radius: 6px; box-shadow: 0 1px 4px rgba(0,0,0,.06); }
```

### 배지 클래스
- `.badge-done` — 완료 (초록)
- `.badge-pending` — 대기 (노랑)
- `.badge-inprogress` — 진행중 (파랑)
- `.badge-type-position` / `-regular` / `-tl` / `-sub` / `-stl` — eval_type별

### 필터 칩
```css
.filter-chip { padding: .25rem .75rem; border-radius: 3px; border: 1.5px solid #d4d4d8; }
.filter-chip.active { background: #1c1c1f; border-color: #1c1c1f; color: #fff; }
```

#### 필터 칩 토글 그룹 — 슬라이드인 패널 + chevron + 배지

토글 버튼 클릭 시 패널 안의 chip 들이 가로로 슬라이드인. 평소엔 토글 버튼만 노출되어 화면 정리. `position-filter-panel` 글로벌 CSS 재사용.

**HTML**:
```html
<div class="flex items-center gap-2 overflow-hidden">
  <button id="myFilterBtn" onclick="toggleMyFilter()" class="filter-chip flex items-center gap-1.5 flex-shrink-0" style="padding:7px 14px;">
    <i class="bi bi-funnel-fill text-xs"></i>
    <span>필터 라벨</span>
    <span id="myFilterBadge" class="hidden ml-0.5 text-[10px] font-extrabold px-1.5 py-0 rounded-full bg-[#B01116] text-white">0</span>
    <i class="bi bi-chevron-right text-[10px] ml-0.5 transition-transform duration-200" id="myFilterChevron"></i>
  </button>
  <div id="myFilterPanel" class="position-filter-panel">
    <div>
      <button class="filter-chip active flex-shrink-0" onclick="setMyFilter(this,'')">전체</button>
      <button class="filter-chip flex-shrink-0" onclick="setMyFilter(this,'a')">A</button>
      <button class="filter-chip flex-shrink-0" onclick="setMyFilter(this,'b')">B</button>
    </div>
  </div>
</div>
```

**JS 토글 함수 (chevron 닫힘 → / 열림 ←)**:
```js
function toggleMyFilter() {
  const panel = document.getElementById('myFilterPanel');
  const chevron = document.getElementById('myFilterChevron');
  const isOpen = panel.classList.contains('open');
  panel.classList.toggle('open');
  chevron.style.transform = isOpen ? '' : 'rotate(180deg)';
}
```

**배지 갱신 (활성 시 표시)**:
```js
function setMyFilter(btn, val) {
  myFilter = val;
  document.querySelectorAll('[data-filter-my]').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  const badge = document.getElementById('myFilterBadge');
  if (badge) {
    if (val) { badge.textContent = '1'; badge.classList.remove('hidden'); }
    else { badge.classList.add('hidden'); }
  }
  applyFilters();
}
```

#### Mutex 토글 — 같은 행에 토글 그룹 2+개

같은 행에 토글 그룹 둘 이상이면 둘 다 펼쳐졌을 때 한쪽 chip slide-in 폭이 다른 쪽 영역 침범 → chip 잘림. 한쪽 열면 다른 쪽 자동 닫기:

```js
function toggleMyFilter() {
  const panel = document.getElementById('myFilterPanel');
  const chevron = document.getElementById('myFilterChevron');
  const isOpen = panel.classList.contains('open');
  panel.classList.toggle('open');
  chevron.style.transform = isOpen ? '' : 'rotate(180deg)';
  // 다른 토글 강제 닫기 — chip 잘림 방지
  if (!isOpen) {
    const other = document.getElementById('otherFilterPanel');
    const otherCh = document.getElementById('otherFilterChevron');
    if (other) other.classList.remove('open');
    if (otherCh) otherCh.style.transform = '';
  }
}
```

**레퍼런스**: `/eval-v2/status` 페이지의 직책 (`togglePositionFilter` in `admin_status_render.js`) + 진행 상태 (`toggleCompletionFilter` in `admin_status_data.js`) — 두 토글이 서로의 패널/chevron 닫음.

### 모달
```css
.modal-backdrop { position: fixed; inset: 0; z-index: 9000; }
.modal-box { border-radius: 4px; animation: modalIn .15s ease; }
@keyframes modalIn { from { opacity:0; transform:scale(.97) translateY(6px) } to { opacity:1; transform:none } }
```

### 2-옵션 confirm 모달 — `showConfirmModal` 헬퍼

`modal_icons.js` 의 `showModal(icon, title, text)` 는 단일 옵션 (확인). 2개 액션 (예: VERSION_CONFLICT 의 "Reload latest" / "Cancel") 필요 시:

```js
showConfirmModal({
  icon: 'warning',
  title: 'Version Conflict\n버전 충돌',
  text: '...',
  confirmLabel: 'Reload latest',
  cancelLabel: 'Cancel',
  onConfirm: () => {...},
  onCancel: () => {...},
});
```

`.modal-backdrop` 글로벌 클래스 (layout.html:103-121) 재사용 — backdrop-filter / `--sb-w` / `--hdr-h` 자동 적용. DOM 동적 생성 + 닫힐 때 제거 (singleton 충돌 회피).

### Edit Mode 배지 — `.badge-edit-mode`

수정 모드 진입 시 사용자가 "지금 수정 중" 임을 인지하도록 모든 step 의 page header 에 영구 표시. **amber 색상은 `.grace-warn-box` (annual_eval.html:298-299) 자산 재사용** — 신규 색상 정의 안 함.

```css
#editModeBadge { display: none; }
html[data-edit-mode="1"] #editModeBadge { display: inline-flex; }
.badge-edit-mode {
  background: rgba(245,158,11,.14); color: #92400e;
  border: 1.5px solid rgba(245,158,11,.45);
  border-radius: 9999px; padding: 3px 10px;
  font-size: 11px; font-weight: 800;
}
html[data-theme="dark"] .badge-edit-mode { color: #fcd34d; background: rgba(245,158,11,.18); }
```

`<html data-edit-mode="1">` dataset 라이프사이클 (단순 show/hide → 클래스 결합 불필요). `_setEditModeUI(on)` 함수로 토글.

### 페이지 페인트 즉시 로딩 오버레이 (URL param 진입 흐름)

다른 페이지에서 URL param 으로 진입 (예: my-tasks → `/eval-v2/form?fromMyTasks=1`) 시, fetch 응답 도착 전까지 빈 화면 깜박임을 차단하는 패턴.

**구조**:
1. **Head inline script** (페이지 페인트 전 실행) — URL param 검출 → `<html data-from-my-tasks="1">` set
2. **CSS rule** (no `!important`) — `html[data-from-my-tasks="1"] #fullScreenLoading { display: flex }` 로 페인트 시점 visible
3. **`#fullScreenLoading` 의 인라인 `style="display:none"` 제거** — default `#fullScreenLoading { display: none }` 로 처리
4. **showLoading / hideLoading 함수는 그대로** — inline `style.display` set 이 CSS rule 이김 (specificity)

**금지 사항**:
- `display: flex !important` — hideLoading 의 inline `display: none` 이 못 이김 → 오버레이 영구 잔존 사고
- `html[data-from-my-tasks="1"] #main-wrapper { filter: blur }` 단독 — dataset 라이프사이클이 fallback path 에서 비우기 어려워 blur + pointer-events 영구 잔존 → 사용자 클릭 차단 사고. 반드시 **`#main-wrapper.loading-blur-active` 클래스 결합**으로 라이프사이클 동기화

**라이프사이클 정리 헬퍼** (lookupEmployee 실패 fallback 등):
```js
function _mtRevealStepId() {
  document.documentElement.dataset.fromMyTasks = '';
  // URL 의 fromMyTasks 도 history.replaceState 로 제거 (Re-enter 라벨 stale 방지)
  const url = new URL(location.href);
  if (url.searchParams.has('fromMyTasks')) {
    url.searchParams.delete('fromMyTasks');
    history.replaceState(null, '', url.toString());
  }
  if (typeof hideLoading === 'function') hideLoading();
}
```

레퍼런스: `app/templates/eval_v2/form.html` `<style>` 의 `html[data-from-my-tasks="1"] #fullScreenLoading` rule + `_mtRevealStepId` 함수. 함정 상세는 `docs/known-issues.md` "페이지 페인트 즉시 로딩 오버레이 — `data-*` attribute 라이프사이클" 항목.

### 공용 진행률 모달 — `ProgressModal`

일괄 작업 (다수 fetch sequential) 의 진행률을 시각화하는 글로벌 컴포넌트. `app/static/js/common/progress_modal.js` 가 `layout.html` 에 글로벌 로드되어 모든 페이지에서 `ProgressModal.open(...)` 호출 가능.

**기본 사용 패턴**:
```js
const items = [...];
const pm = ProgressModal.open({
  title: 'Generating reports',
  subtitle: `${items.length} items`,
  total: items.length,
});

let success = 0, error = 0;
for (let i = 0; i < items.length; i++) {
  if (pm.cancelled) break;
  try {
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ item: items[i] }),
      signal: pm.signal,   // ← AbortController — Cancel 즉시 fetch abort
    }).then(r => r.json());
    if (res.status === 'SUCCESS') success++; else error++;
  } catch (e) {
    if (e && e.name === 'AbortError') break;   // 사용자 취소 — error 로 안 침
    error++;
  }
  pm.update(i + 1, { success, error, currentLabel: items[i].name });
}

pm.done({ success: error === 0, summary: `${success} 성공 · ${error} 실패` });
```

**자동 적용 기능**:
- **회전 spinner** — `bi-arrow-repeat` + `.pm-spin` keyframe (login 페이지의 `.spinning` 패턴과 동일 — 0.8s 회전)
- **bar fill stripe** — 게이지 안에 흐르는 줄무늬 (`.pm-stripes`) — 게이지가 멈춰있어도 진행 중 시각 시그널 유지
- **AbortController** — `pm.signal` 을 fetch 에 전달 시 Cancel 클릭 → 진행 중 fetch 즉시 abort. 다음 chunk 안 보냄
- **다크 모드 토큰화** — `var(--surface-lowest)` (box bg) / `var(--primary)` (spinner/fill) / `var(--outline-variant)` (border) / 시맨틱 chip 클래스 (`.pm-chip-success/skip/error`) 가 라이트·다크 양쪽 정의
- **`done()` 시 시그널 정지** — spinner 숨김 + stripe 클래스 제거 + bar gradient 가 success 초록 / error 빨강 으로 전환

**API**:
| 메서드 / getter | 설명 |
|---|---|
| `ProgressModal.open(opts)` | `{title, subtitle, total}` 받아 모달 표시 + instance 반환 |
| `pm.update(processed, stats)` | `stats: {success, skip, error, currentLabel}` 갱신 |
| `pm.done(opts)` | 완료 — `{success: boolean, summary: string}`. spinner/stripe 정지 |
| `pm.signal` (getter) | fetch 에 전달할 AbortSignal |
| `pm.cancelled` (getter) | 사용자 Cancel 눌렀는지 |
| `pm.cancel()` | 프로그래매틱 취소 (state.cancelled + abort) |

**더 단순한 헬퍼 — `BulkRunner.run(...)`**:
items + chunkSize + url + bodyKey + tallyFn 만 넘기면 자동으로 ProgressModal + chunk loop. 카운트 누적도 tallyFn 으로 위임. `bulk_runner.js` 참조. 사용 사례: `admin_annual_eval.js` 의 일괄 보고서 생성, `admin.html` 의 BV-fix / Create folders.

**레퍼런스**:
- `app/static/js/common/progress_modal.js` — 컴포넌트 정의
- `app/static/js/common/bulk_runner.js` — 더 추상화된 wrapper
- `app/static/js/eval_v2/admin_sessions.js` — `generateBulkReports` (직접 ProgressModal) / `bulkTrashSelectedReports` (직접) / `trashSessionReports` (자체 모달 — 공용 trashProgressModal HTML)

---

## 드롭다운 (공통 pill 디자인)

> 모든 드롭다운은 `layout.html` 의 canonical 클래스를 사용한다. 페이지별로 새로운 `<select>` 스타일을 정의하지 말 것.

### Canonical 클래스

| 클래스 | 용도 |
|---|---|
| `.dyb-select` | 네이티브 `<select>` — 자동 chevron (SVG background-image) |
| `.dyb-dd-trigger` | 커스텀 버튼 트리거 `<div>` — 내부 `<i class="bi bi-chevron-down">` 자동 회전 (`.open` 클래스) |
| `.dyb-dd-menu` | 커스텀 popover 메뉴. `.open` 으로 표시. `.to-right` 로 우측 정렬 |
| `.dyb-dd-option` | 메뉴 내 항목. `.selected` 로 활성 표시 |

### 사이즈 변형

- 기본: 32px / 12px font / 7px 13 font-weight
- `.dyb-dd-sm` — 28px / 11px font (필터·테이블 셀용)
- `.dyb-dd-lg` — 40px / 13px font (폼 primary 입력용)

### 페이지별 accent color

기본은 neutral gray (`#374151`). 페이지별로 변경이 필요할 때만 `<style>` 블록에 CSS 변수 override:

```css
:root { --dyb-dd-accent: #870009; }   /* 브랜드 레드로 */
```

변수: `--dyb-dd-border` (기본 테두리), `--dyb-dd-border-hover` (호버), `--dyb-dd-accent` (focus/open/selected).

### 커스텀 트리거 HTML 템플릿

```html
<div class="relative inline-block">
  <div class="dyb-dd-trigger" id="myTrigger" onclick="toggleCustomSelect('myTrigger')">
    <span id="myLabel" style="flex:1;overflow:hidden;text-overflow:ellipsis;">— Select —</span>
    <i class="bi bi-chevron-down"></i>
  </div>
  <div class="dyb-dd-menu" id="myMenu">
    <div class="dyb-dd-option" onclick="pickItem(...)">Item</div>
  </div>
  <select id="myHiddenSelect" class="sr-only" onchange="..."></select>
</div>
```

> ⚠️ 커스텀 트리거에 `background-image` chevron 을 별도로 넣지 말 것 — 내부 `<i>` 가 자동 회전하도록 이미 CSS 가 처리함 (이중 chevron 방지).

**레퍼런스**: `eval_v2/status.html` → statusSession / modalSession. JS 헬퍼는 `admin_common.js` 의 `toggleCustomSelect` / `pickSession` / `toggleModalSelect` / `pickModalSession`.

### JS 로 동적 생성되는 필 (semantic color 유지)

role/campus 필처럼 의미 있는 색상이 필요한 경우, 기본 shape 는 `.dyb-select.dyb-dd-sm` 를 그대로 쓰고 색상만 인라인 `style` 로 override:

```javascript
`<select class="dyb-select dyb-dd-sm" style="${roleStyle};border:none;background-image:${_ROLE_ARROW}">`
```

레퍼런스: `admin_users.js` → 역할/캠퍼스 필 렌더링.

### 열림/닫힘 애니메이션

`.dyb-dd-menu` 는 `.open` 클래스 토글 시 overshoot easing 으로 부드럽게 등장/소멸:

- 컨테이너: `.18s cubic-bezier(.22,.68,0,1.2)` 로 `opacity + translateY(-6px) scale(.97) → (0)(1)`
- 각 `.dyb-dd-option`: 순차 fade-in stagger (`.02s` 간격, 6개 이상은 `.12s` 로 클램프)
- 닫힘은 같은 transition 역방향으로 재생 (`display:none` 즉시 소멸 금지)

`transform-origin` 은 `top center` (기본) / `top right` (`.to-right` 일 때). 이 easing 은 `/nt-dashboard` 캠퍼스 드롭다운의 `campusDropIn` 과 동일한 값.

---

## 커스텀 애니메이션 드롭다운 (`ae-sel-*` wrapper)

네이티브 `<select>` 는 브라우저 OS 위젯이라 CSS 트랜지션이 먹히지 않는다. 부드러운 열기/닫기가 필요한 경우 아래 wrapper 패턴 사용.

**현재 사용처**: `eval_v2/annual_eval.html` 모달 내부(Status/Session 1·2/Rater) + 페이지 상단 툴바 필터(Campus/Position/Sort).

### 핵심 특징
- 네이티브 `<select>` 는 DOM 에 유지 (hidden) → 폼 제출·`onchange` 인라인 핸들러 호환
- 버튼 `<button class="ae-sel-trigger">` + 패널 `<div class="ae-sel-panel">` 생성
- 패널은 `position: fixed` + JS `getBoundingClientRect()` 좌표 계산 → 부모 `overflow:hidden` 에 잘리지 않음
- 아래 공간 부족 시 자동 위로 flip (`transform-origin: bottom center`)
- 키보드 지원: ↑↓/Home/End 탐색, Enter 선택, Esc/Tab 닫기

### 클래스 prefix 규칙
> ⚠️ **`.dyb-select` 는 이미 layout.html 에서 네이티브 pill 스타일 전용**. 새 커스텀 래퍼 만들 때 이 클래스명 재사용 금지 — 타원형 테두리 + flex 레이아웃이 덮여 이중 테두리·내부 깨짐 발생. 고유 prefix(`ae-sel-`, `xyz-select-` 등) 사용.

### 레퍼런스
`admin_annual_eval.js` → `_aeEnhanceSelect(select)`:
- 모달 오픈 시 `_populateRaterSelects` / `_populateSessionSelects` / `renderAeEditor` 내부에서 호출
- 재호출 시 `select._aeSelInst.sync()` 로 라벨만 갱신 (wrapper 재생성 방지)
- `_aeClearPanelFields` 에서도 `._aeSelInst.sync()` 호출해 교사 전환 시 잔상 제거

### 애니메이션 상세
- max-height 트랜지션 **제거** — 옵션이 즉시 보이도록 JS 가 측정값으로 snap
- `opacity .18s` + `transform translateY(-6px) scaleY(.96) → 1` `.24s cubic-bezier(.22, 1, .36, 1)` 만 애니메이션

---

## 사이드바 서브메뉴 토글 (`_animSubmenuToggle`)

`layout.html` 전역. Tailwind `.hidden` (`display:none`) 은 트랜지션 불가 → 열기 시 클래스 제거 후 측정 기반 max-height 로 펼치고, 닫기 시 `transitionend` 에서 `.hidden` 복귀.

- max-height + opacity + margin-top 동시 트랜지션 (`.3s / .22s / .3s`)
- 중복 클릭 안전: `submenu._animCleanup` 으로 진행중 리스너 정리
- chevron 회전은 JS 가 inline `transition: transform .3s cubic-bezier(.22, 1, .36, 1)` 주입하여 높이 애니메이션과 리듬 동기화

대상: `board-submenu`, `pagesettings-submenu`, `net-submenu`, `evalv2-submenu` 4 개.
