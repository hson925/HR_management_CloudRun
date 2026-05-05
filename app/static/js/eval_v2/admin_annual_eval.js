// ── Annual Eval Admin — 입사일 기준 연봉 평가 ───────────────────────────────

let aeCurrentEmpId    = null;
let aeCurrentDeadline = null;  // YYYY-MM-DD
let aeCurrentSequence = null;  // eval_sequence (int)
let aeCurrentRecord   = null;
let _aeHasSaved       = false; // 저장 발생 시 true → 닫을 때만 목록 새로고침
let _aeOpenToken      = 0;     // 더블클릭 레이스 컨디션 방지: 가장 최근 open 요청만 렌더링
let _aeApplyingPolicy = false; // aeApplyRaisePolicy ↔ aeSalaryCalc 무한 루프 방지
let _aeRendering      = false; // render 중 aeSalaryCalc가 save 큐잉하지 않도록 가드
let aeConfig = { raters: [], score_weights: { reg_eval: 50, obs_eval: 30, net_eval: 20 }, raise_policy: [] };
let _aeSaveDebounceTimers = {};
let _aePendingFields = {}; // 닫을 때 flush할 미저장 필드
let _aeTranslateTimers = {}; // 번역 디바운스 타이머
let _aeCfgTab = 'weights';
let _aeRatersLocal = [];
let _aeRaisePolicyLocal = [];
let _aeListCache = null; // { search, campus, position, timestamp, teachers }
const AE_LIST_CACHE_TTL = 60000; // 1분
let _aeStatusFilter = ''; // '' | 'overdue' | 'due_30' | 'in_progress' | 'done' — dashboard 카드 클릭으로 토글

// 에디터 모달 내 탭 상태
let _aeEditorTab = 'eval';            // 'eval' | 'history'
let _aeEditorHistoryCache = null;     // { empId, data } — 교사 전환/모달 종료 시 null
let _aeHistReqSeq = 0;                // 최신 history 요청만 렌더링하도록 구분하는 단조 증가 카운터

// ── XSS 방지 유틸 ─────────────────────────────────────────────────────────────
function _aeEscHtml(str) {
  return String(str ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

// ── CSRF 방어 fetch 헬퍼 ────────────────────────────────────────────────────
function _aeFetch(url, body = null) {
  const opts = {
    method: body !== null ? 'POST' : 'GET',
    headers: {
      'Content-Type': 'application/json',
      'X-Requested-With': 'XMLHttpRequest',
    },
  };
  if (body !== null) opts.body = JSON.stringify(body);
  return fetch(url, opts);
}

const AE_EVAL_TYPES = { position: 'Position', regular: 'Regular', tl: 'TL', sub: 'Sub', stl: 'STL' };
// CAMPUS_ORDER, CAMPUS_EN loaded from campus_constants.js
const AE_CAMPUS_CODE = CAMPUS_EN;

// ── 초기화 ─────────────────────────────────────────────────────────────────

function loadAnnualEval() {
  _initAeCampusSelect();
  _aeEnhanceToolbarSelects();
  loadAeConfig().then(() => loadAnnualEvalList());
}

function _initAeCampusSelect() {
  const sel = document.getElementById('aeCampusSelect');
  if (!sel || sel.options.length > 1) return;
  sel.innerHTML = '<option value="">All</option>';
  CAMPUS_ORDER.forEach(c => {
    const opt = document.createElement('option');
    opt.value = c;
    opt.textContent = AE_CAMPUS_CODE[c] || c;
    sel.appendChild(opt);
  });
}

// 페이지 상단 툴바 필터 3 개(campus / position / sort) 에도 동일한 애니메이션 적용.
// _initAeCampusSelect 이후 호출되어야 campus 옵션이 모두 포함된 상태로 enhance 됨.
function _aeEnhanceToolbarSelects() {
  ['aeCampusSelect', 'aePositionFilter', 'aeSortSelect'].forEach(id => {
    const sel = document.getElementById(id);
    if (sel) _aeEnhanceSelect(sel);
  });
}

// ── 교사 목록 로드 ─────────────────────────────────────────────────────────

async function loadAnnualEvalList(forceRefresh = false) {
  const search   = (document.getElementById('aeSearchInput')?.value || '').trim().toLowerCase();
  const campus   = document.getElementById('aeCampusSelect')?.value || '';
  const position = document.getElementById('aePositionFilter')?.value || '';
  const container = document.getElementById('aeTeacherList');

  // 캐시 확인
  const now = Date.now();
  if (!forceRefresh && _aeListCache
      && _aeListCache.search === search
      && _aeListCache.campus === campus
      && _aeListCache.position === position
      && (now - _aeListCache.timestamp) < AE_LIST_CACHE_TTL) {
    renderAnnualEvalList(_aeListCache.teachers);
    return;
  }

  container.innerHTML = `<div class="flex items-center gap-3 py-8 justify-center" style="color:var(--text-muted)">
    <div class="w-5 h-5 rounded-full animate-spin" style="border:2px solid var(--outline-variant);border-top-color:var(--primary-dark);"></div>
    <span class="text-sm">Loading...</span></div>`;

  try {
    const res = await _aeFetch('/api/v2/annual-eval/list', { search, campus, position });
    const data = await res.json();
    if (data.status !== 'SUCCESS') throw new Error(data.message);
    _aeListCache = { search, campus, position, timestamp: Date.now(), teachers: data.teachers };
    // 정렬 + status filter 같은 파이프라인으로 렌더링 (aeApplySort → renderAnnualEvalList)
    aeApplySort();
  } catch (e) {
    container.innerHTML = `<div class="text-sm text-red-600 py-6 text-center">${_aeEscHtml(e.message)}</div>`;
  }
}

function _aeDeadlineLabel(daysRemaining, deadline, graceActive, graceDaysLeft) {
  if (deadline == null) return '';
  // Grace period: 이전 사이클을 연장 편집 중 — 주황색으로 강조하고 남은 grace 일수 표시
  if (graceActive) {
    return `<span style="font-size:.7rem;font-weight:800;color:#ea580c">⚠ Grace: ${graceDaysLeft}d left</span>
            <span style="font-size:.7rem;color:var(--text-dim);margin-left:3px">${_aeEscHtml(deadline)}</span>`;
  }
  const abs = Math.abs(daysRemaining);
  let color, label;
  if (daysRemaining < 0) {
    color = '#dc2626';
    label = `${abs}d overdue`;
  } else if (daysRemaining === 0) {
    color = '#dc2626';
    label = 'Due today';
  } else if (daysRemaining <= 30) {
    color = '#d97706';
    label = `${daysRemaining}d left`;
  } else {
    color = '#71717a';
    label = `${daysRemaining}d left`;
  }
  return `<span style="font-size:.7rem;font-weight:700;color:${color}">${label}</span>
          <span style="font-size:.7rem;color:var(--text-dim);margin-left:3px">${_aeEscHtml(deadline)}</span>`;
}

// 교사 1명이 어느 status 버킷에 속하는지 판정 (대시보드 카드 카운팅 로직과 1:1 일치).
// 한 사람이 여러 버킷에 속할 수 있음 (예: in_progress + overdue) — 카드 카운팅 동작과 동일.
function _aeMatchesBucket(t, bucket) {
  if (!bucket) return true;
  const st = t.record ? t.record.status : '';
  const dr = t.days_remaining;
  switch (bucket) {
    case 'overdue':     return st !== 'done' && dr != null && dr < 0;
    case 'due_30':      return st !== 'done' && dr != null && dr >= 0 && dr <= 30;
    case 'in_progress': return st === 'in_progress';
    case 'done':        return st === 'done';
    default:            return true;
  }
}

function aeApplySort() {
  if (!_aeListCache || !_aeListCache.teachers) return;
  const mode = document.getElementById('aeSortSelect')?.value || 'deadline';
  // 1) status filter (대시보드 카드 토글) → 2) sort
  let list = [..._aeListCache.teachers];
  if (_aeStatusFilter) list = list.filter(t => _aeMatchesBucket(t, _aeStatusFilter));
  const _status_order = { done: 0, in_progress: 1, not_started: 2 };

  switch (mode) {
    case 'deadline':
      list.sort((a, b) => (a.days_remaining ?? 9999) - (b.days_remaining ?? 9999));
      break;
    case 'deadline_desc':
      list.sort((a, b) => (b.days_remaining ?? -9999) - (a.days_remaining ?? -9999));
      break;
    case 'name':
      list.sort((a, b) => (a.name || '').localeCompare(b.name || ''));
      break;
    case 'score':
      list.sort((a, b) => {
        const sa = a.record?.composite_score ?? -1;
        const sb = b.record?.composite_score ?? -1;
        return sb - sa;
      });
      break;
    case 'status':
      list.sort((a, b) => {
        const sa = _status_order[a.record?.status] ?? 2;
        const sb = _status_order[b.record?.status] ?? 2;
        return sa !== sb ? sa - sb : (a.days_remaining ?? 9999) - (b.days_remaining ?? 9999);
      });
      break;
  }
  renderAnnualEvalList(list);
}

// 대시보드 카드 클릭 — 같은 버킷 다시 누르면 해제, Total('') 누르면 명시적 해제.
function aeApplyStatusFilter(bucket) {
  if (!bucket) {
    _aeStatusFilter = '';
  } else {
    _aeStatusFilter = (_aeStatusFilter === bucket) ? '' : bucket;
  }
  // 카드 aria-pressed 동기화
  document.querySelectorAll('.ae-dash-card').forEach(el => {
    const b = el.getAttribute('data-bucket') || '';
    const active = (b === _aeStatusFilter) || (b === '' && !_aeStatusFilter);
    // Total 카드는 필터 해제 상태일 때만 활성
    el.setAttribute('aria-pressed', active ? 'true' : 'false');
  });
  // 정렬·필터 재적용 (sort 가 list pipeline 의 입구)
  aeApplySort();
}

document.addEventListener('keydown', function(ev) {
  // 카드에 키보드 포커스 + Enter/Space → 클릭 동등 처리 (a11y)
  if ((ev.key === 'Enter' || ev.key === ' ') && document.activeElement?.classList.contains('ae-dash-card')) {
    ev.preventDefault();
    document.activeElement.click();
  }
});

function renderAnnualEvalList(teachers) {
  const container = document.getElementById('aeTeacherList');

  // 카운트는 항상 cache 의 전체 기준 (필터 적용 전) — 카드를 보고 다른 버킷으로 전환 가능해야 의미.
  aeUpdateDashboard(_aeListCache?.teachers || teachers);

  if (!teachers || teachers.length === 0) {
    const filterMsg = _aeStatusFilter
      ? `<div class="text-sm py-8 text-center" style="color:var(--text-muted)">No teachers in this status. <button onclick="aeApplyStatusFilter('')" class="font-bold underline ml-1" style="color:var(--primary)">Clear filter</button></div>`
      : '<div class="text-sm py-8 text-center" style="color:var(--text-muted)">No teachers found.</div>';
    container.innerHTML = filterMsg;
    return;
  }

  let html = '<div style="display:flex;flex-direction:column;gap:6px;">';
  teachers.forEach(t => {
    const rec    = t.record;
    const isDone = rec && rec.status === 'done';
    const hasRec = !!rec;
    const score  = rec && rec.composite_score != null ? rec.composite_score.toFixed(1) : null;
    const safeEmpIdAttr = _aeEscHtml(t.emp_id);
    const safeName  = _aeEscHtml(t.name || t.emp_id);
    const safeEmpId = _aeEscHtml((t.emp_id || '').toUpperCase());
    const safeCampus = _aeEscHtml(t.campus || '—');
    const seq    = t.eval_sequence != null ? `#${t.eval_sequence}` : '';
    const seqColor = '#6366f1';

    const deadlineHtml = _aeDeadlineLabel(t.days_remaining, t.eval_deadline, t.grace_active, t.grace_days_left);
    const noDeadline = !t.eval_deadline;
    const graceBadge = t.grace_active
      ? `<span class="text-xs font-bold px-2 py-0.5 rounded" style="background:rgba(249,115,22,.12);border:1px solid #f59e0b;color:#92400e;" data-tip="Grace period: deadline passed, 14 days to finalize&#10;유예 기간: 마감 후 14일 내 완료 가능">⚠ GRACE</span>`
      : '';

    html += `
      <div class="teacher-row ${isDone ? 'done' : ''}" ${noDeadline ? '' : `data-ae-open="${safeEmpIdAttr}"`}
           style="cursor:${noDeadline ? 'default' : 'pointer'};opacity:${noDeadline ? '0.45' : '1'};border-radius:4px;padding:10px 14px;display:flex;align-items:center;justify-content:space-between;gap:8px;"
           ${noDeadline ? 'title="입사일 정보가 없어 평가를 시작할 수 없습니다 (NT Info → start_date 확인 필요)"' : ''}>
        <div style="min-width:0;flex:1;">
          <div style="display:flex;align-items:center;gap:6px;margin-bottom:3px;">
            <p class="text-sm font-bold" style="color:var(--text-strong);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${safeName}</p>
            ${seq ? `<span style="font-size:.65rem;font-weight:800;color:${seqColor};background:rgba(99,102,241,.14);padding:1px 5px;border-radius:3px;white-space:nowrap;">${seq}</span>` : ''}
          </div>
          <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
            <span class="text-xs font-mono" style="color:var(--text-muted)">${safeEmpId}</span>
            <span class="text-xs" style="color:var(--text-dim)">·</span>
            <span class="text-xs font-semibold" style="color:var(--text-muted)">${safeCampus}</span>
          </div>
        </div>
        <div style="display:flex;flex-direction:column;align-items:flex-end;gap:4px;flex-shrink:0;">
          <div style="display:flex;align-items:center;gap:4px;">
            ${graceBadge}
            ${isDone
              ? `<span class="text-xs font-bold px-2 py-0.5 rounded badge-done" style="border-width:1px">● DONE</span>
                 ${score != null ? `<span class="text-sm font-extrabold" style="color:#15803d">${score}</span>` : ''}`
              : hasRec && rec.status === 'in_progress'
              ? `<span class="text-xs font-bold px-2 py-0.5 rounded badge-inprogress" style="border-width:1px">○ In Progress</span>`
              : `<span class="text-xs font-semibold" style="color:var(--text-dim);">Not Started</span>`
            }
          </div>
          <div style="display:flex;align-items:center;gap:4px;">${deadlineHtml}</div>
        </div>
      </div>`;
  });

  html += '</div>';
  container.innerHTML = html;
  // 대시보드는 함수 상단에서 cache 전체 기준으로 이미 갱신됨 (필터 적용 전 카운트 유지).

  if (!container.dataset.aeOpenBound) {
    container.addEventListener('click', (ev) => {
      const row = ev.target.closest('[data-ae-open]');
      if (!row || !container.contains(row)) return;
      const empId = row.dataset.aeOpen;
      if (empId) openAeEditor(empId);
    });
    container.dataset.aeOpenBound = '1';
  }
}

// ── 에디터 열기/닫기 ───────────────────────────────────────────────────────

async function openAeEditor(empId) {
  // 더블클릭 레이스 컨디션 방지: 이 요청의 고유 토큰을 발급
  const myToken = ++_aeOpenToken;

  aeCurrentEmpId    = empId;
  aeCurrentDeadline = null;
  aeCurrentSequence = null;
  aeCurrentRecord   = null;
  _aeHasSaved       = false;

  // 탭은 Evaluation 으로 리셋, 이전 교사 History 캐시 무효화
  _aeEditorHistoryCache = null;
  switchAeEditorTab('eval');

  // 세션 목록이 없으면 먼저 로드 (드롭다운 빈값 방지)
  if (typeof loadSessions === 'function' && (!allSessions || !allSessions.length)) {
    await loadSessions();
  }

  // 이미 더 새로운 요청이 시작됐으면 중단
  if (myToken !== _aeOpenToken) return;

  // 사이드바·상단바 기준 모달 중앙 배치
  const overlay   = document.getElementById('aeEditorOverlay');
  const sidebarEl = document.getElementById('sidebar');
  const sidebarW  = sidebarEl ? sidebarEl.getBoundingClientRect().width : 0;
  overlay.style.paddingLeft = sidebarW + 'px';
  overlay.style.paddingTop  = '64px';  // 상단바 h-16
  overlay.style.display     = 'flex';

  // 로딩 표시 + 이전 교사 데이터 전체 초기화 (잔상 방지)
  _aeClearPanelFields();
  document.getElementById('aePanelName').textContent = 'Loading...';
  document.getElementById('aePanelMeta').textContent = '';

  try {
    const res = await _aeFetch('/api/v2/annual-eval/record', { emp_id: empId });
    const data = await res.json();

    // 응답이 돌아왔을 때 더 새로운 요청이 있으면 버림
    if (myToken !== _aeOpenToken) return;

    if (data.status !== 'SUCCESS') throw new Error(data.message);
    aeCurrentDeadline = data.record.eval_deadline || null;
    aeCurrentSequence = data.record.eval_sequence || null;
    aeCurrentRecord   = data.record;
    renderAeEditor(data.record);
    _aeRenderGraceBanner(data.record);
  } catch (e) {
    if (myToken !== _aeOpenToken) return;
    // start_date 누락 오류인 경우 모달을 닫고 toast로 안내
    if (e.message && e.message.includes('start date')) {
      document.getElementById('aeEditorOverlay').style.display = 'none';
      showToast('This teacher has no start date in NT Info. Please update NT Info first.', 'error');
      return;
    }
    document.getElementById('aePanelName').textContent = 'Error';
    document.getElementById('aePanelMeta').textContent = e.message;
  }
}

// Grace period 경고 배너 렌더 — record.grace_active 시 주황색 배너에 bilingual 설명.
function _aeRenderGraceBanner(record) {
  const el   = document.getElementById('aeGraceWarning');
  const text = document.getElementById('aeGraceWarningText');
  if (!el || !text) return;
  if (record && record.grace_active) {
    const d  = _aeEscHtml(record.eval_deadline || '');
    const id = _aeEscHtml(record.ideal_deadline || '');
    const n  = record.grace_days_left;
    const dayWord = n === 1 ? 'day' : 'days';
    text.innerHTML =
      `<strong>Grace period:</strong> deadline <strong>${d}</strong> has passed. ` +
      `<strong>${n} ${dayWord}</strong> remaining to complete this evaluation before it rolls over to cycle ending <strong>${id}</strong>.` +
      `<br><span style="color:#9a3412">유예 기간: 마감일 <strong>${d}</strong> 이(가) 지났습니다. ` +
      `다음 사이클(<strong>${id}</strong>) 시작 전까지 <strong>${n}일</strong> 남았습니다.</span>`;
    el.style.display = 'block';
  } else {
    el.style.display = 'none';
    text.textContent = '';
  }
}

// 교사 전환 시 이전 데이터 잔상 제거용
// DOM 순회 기반 — 새 필드 추가 시 clear 누락 방지. 특수 리셋이 필요한 요소만 명시 처리.
function _aeClearPanelFields() {
  const panel = document.getElementById('aeEditorPanel');
  if (!panel) return;

  // 1. 일반 input/select/textarea 전체 초기화 (lock/flag 체크는 lockable 배지로 처리되므로 안전)
  //    enhance 된 <select>(._aeSelInst)는 트리거 라벨도 동기화해야 잔상 방지.
  panel.querySelectorAll('input:not([type=button]):not([type=submit]), select, textarea').forEach(el => {
    if (el.type === 'checkbox' || el.type === 'radio') el.checked = false;
    else el.value = '';
    if (el._aeSelInst) el._aeSelInst.sync();
  });

  // 2. 텍스트 표시 요소 — data-ae-placeholder 속성으로 초기값 지정 (기본 '—')
  panel.querySelectorAll('[data-ae-placeholder]').forEach(el => {
    el.textContent = el.dataset.aePlaceholder || '—';
  });

  // 3. Profile 기본 '—'
  ['aeProfileName','aeProfileEmpId','aeProfileCampus','aeProfilePosition',
   'aeProfileStartDate','aeProfileNationality','aeProfileDeadline','aeProfileSequence',
   'aeScore1Display','aeScore2Display','aeRegFinalDisplay']
    .forEach(id => { const el = document.getElementById(id); if (el) el.textContent = '—'; });

  // 4. 번역 박스 / 코멘트 카운터 / Ko 텍스트
  ['aeObsKo','aeNetKo','aeOtherKo'].forEach(id => {
    const el = document.getElementById(id); if (el) el.textContent = '';
  });
  const cc = document.getElementById('aeAllowanceCommentCount');
  if (cc) cc.textContent = '0 / 120';

  // 5. Datepicker 커스텀 컴포넌트 내부 상태 리셋
  if (typeof DYBDatepicker !== 'undefined') {
    DYBDatepicker.setValue('aeObsDate', '');
    DYBDatepicker.setValue('aeNetDate', '');
  }

  // 6. Folder link 숨김
  const fl = document.getElementById('aeFolderLink');
  if (fl) fl.style.display = 'none';

  // Save indicator 숨김
  const si = document.getElementById('aeSaveIndicator');
  if (si) si.classList.add('hidden');

  // Policy match info + grade badge 이전 교사 상태 제거
  const matchInfo = document.getElementById('aePolicyMatchInfo');
  if (matchInfo) { matchInfo.style.display = 'none'; matchInfo.innerHTML = ''; }
  if (typeof _aeUpdateGradeBadge === 'function') _aeUpdateGradeBadge(null);
}

async function _doCloseAeEditor() {
  // 대기 중인 debounce 저장을 즉시 실행 + 완료까지 await — list refresh 가
  // stale data 받지 않도록 (#7 'done' 이 in-progress 로 보이는 race fix)
  await _aeFlushPendingSaves();
  // 열려있는 커스텀 드롭다운 닫아 document 레벨 이벤트 리스너 리크 방지
  _closeAllAeSelects();
  // History 캐시·탭 상태 초기화
  _aeEditorHistoryCache = null;
  _aeEditorTab = 'eval';
  // Grace 배너 숨김
  const graceEl = document.getElementById('aeGraceWarning');
  if (graceEl) graceEl.style.display = 'none';
  document.getElementById('aeEditorOverlay').style.display = 'none';
  // 저장이 실제로 발생한 경우에만 목록 새로고침 (불필요한 깜빡임 방지)
  if (_aeHasSaved) {
    _aeListCache = null;
    loadAnnualEvalList();
  }
}

async function _aeFlushPendingSaves() {
  // pending 데이터가 있으면 한 번에 저장 — promise 반환으로 caller 가 await 가능
  // (close race fix — #7)
  const pending = { ..._aePendingFields };
  _aePendingFields = {};
  Object.keys(_aeSaveDebounceTimers).forEach(k => clearTimeout(_aeSaveDebounceTimers[k]));
  _aeSaveDebounceTimers = {};
  if (Object.keys(pending).length > 0 && aeCurrentEmpId && aeCurrentDeadline) {
    await aeDirectSave(pending);
  }
}

async function closeAeEditor() {
  await _doCloseAeEditor();
}

// ── 에디터 렌더링 ──────────────────────────────────────────────────────────

function renderAeEditor(rec) {
  // 렌더 중에는 aeSalaryCalc 등이 save를 큐잉하지 않도록 가드
  _aeRendering = true;
  try {
    _renderAeEditorBody(rec);
  } finally {
    _aeRendering = false;
  }
}

function _renderAeEditorBody(rec) {
  // 헤더
  document.getElementById('aePanelName').textContent = rec.nt_name || rec.emp_id.toUpperCase();
  const seqLabel = rec.eval_sequence ? ` · #${rec.eval_sequence} eval` : '';
  document.getElementById('aePanelMeta').textContent =
    `${rec.emp_id.toUpperCase()} · ${rec.nt_campus || '—'}${seqLabel} · Due ${rec.eval_deadline || '—'}`;

  // Profile
  document.getElementById('aeProfileName').textContent        = rec.nt_name || '—';
  document.getElementById('aeProfileEmpId').textContent       = rec.emp_id.toUpperCase();
  document.getElementById('aeProfileCampus').textContent      = rec.nt_campus || '—';
  document.getElementById('aeProfilePosition').textContent    = rec.nt_position || '—';
  document.getElementById('aeProfileStartDate').textContent   = rec.nt_start_date || '—';
  document.getElementById('aeProfileNationality').textContent = rec.nt_nationality || '—';
  document.getElementById('aeProfileDeadline').textContent    = rec.eval_deadline || '—';
  document.getElementById('aeProfileSequence').textContent    =
    rec.eval_sequence ? `#${rec.eval_sequence}` : '—';

  // Status select
  const statusSel = document.getElementById('aeStatusSelect');
  statusSel.value = rec.status || 'not_started';
  _aeEnhanceSelect(statusSel);

  // Sessions dropdowns
  _populateSessionSelects(rec);

  // Regular scores
  _setAeRegScore(1, rec.session_1_id, rec.reg_score_1);
  _setAeRegScore(2, rec.session_2_id, rec.reg_score_2);
  _updateRegFinal(rec.reg_final_score);

  // Class Observation
  document.getElementById('aeObsScore').value = rec.obs_score != null ? rec.obs_score : '';
  if (typeof DYBDatepicker !== 'undefined') DYBDatepicker.setValue('aeObsDate', rec.obs_date || '');
  document.getElementById('aeObsLink').value  = rec.obs_link || '';
  document.getElementById('aeObsEng').value   = rec.obs_eng  || '';

  // NET Evaluation
  document.getElementById('aeNetScore').value = rec.net_score != null ? rec.net_score : '';
  if (typeof DYBDatepicker !== 'undefined') DYBDatepicker.setValue('aeNetDate', rec.net_date || '');
  document.getElementById('aeNetLink').value  = rec.net_link || '';
  document.getElementById('aeNetEng').value   = rec.net_eng  || '';

  // Rater dropdowns
  _populateRaterSelects(rec);

  // Salary
  document.getElementById('aeBaseCurrent').value    = rec.base_current    ?? '';
  document.getElementById('aeBaseInc').value         = rec.base_inc         ?? 0;
  document.getElementById('aePoseCurrent').value    = rec.pos_current     ?? '';
  document.getElementById('aePosInc').value          = rec.pos_inc          ?? 0;
  document.getElementById('aeRoleCurrent').value    = rec.role_current    ?? '';
  document.getElementById('aeRoleInc').value         = rec.role_inc         ?? 0;
  document.getElementById('aeHousingCurrent').value = rec.housing_current ?? '';
  document.getElementById('aeHousingInc').value      = rec.housing_inc      ?? 0;
  aeSalaryCalc();

  // Overall — Current Allowances (NT Info U열, read-only) + Future Allowances (editable)
  const ntAllowance = rec.nt_allowance_name || '';
  const ntEl = document.getElementById('aeNtAllowanceName');
  if (ntEl) ntEl.textContent = ntAllowance || '—';
  const acVal = rec.allowance_comment || '';
  document.getElementById('aeAllowanceComment').value = acVal;
  document.getElementById('aeAllowanceCommentCount').textContent = `${acVal.length} / 120`;
  document.getElementById('aeOtherEng').value = rec.other_eng || '';

  // Folder link (헤더에 항상 표시)
  const folderLink = document.getElementById('aeFolderLink');
  if (rec.folder_url) {
    folderLink.href = rec.folder_url;
    folderLink.style.display = '';
  } else {
    folderLink.style.display = 'none';
  }

  // 번역 박스 초기화 후 기존 데이터 표시
  document.getElementById('aeObsKo').textContent = rec.obs_eng_ko || '';
  document.getElementById('aeNetKo').textContent = rec.net_eng_ko || '';
  document.getElementById('aeOtherKo').textContent = rec.other_eng_ko || '';

  // Composite summary + 정책 자동 적용
  aeUpdateCompositeSummary();
  // aeUpdateCompositeSummary 안에서 aeApplyRaisePolicy() 호출됨

  // Done → 필드 잠금 (status 드롭다운은 항상 편집 가능)
  _aeSetFieldLock(rec.status === 'done');
}

function _populateSessionSelects(rec) {
  const baseOptions = `<option value="">— Select —</option><option value="__manual__">Direct Input</option>`;

  function _render() {
    const sessionOptions = (allSessions || []).map(s =>
      `<option value="${_aeEscHtml(s.id)}">${_aeEscHtml(s.label)}</option>`
    ).join('');

    ['aeSession1Select', 'aeSession2Select'].forEach((id, idx) => {
      const sel = document.getElementById(id);
      if (!sel) return;
      const prev = sel.value;
      sel.innerHTML = baseOptions + sessionOptions;
      const sid = idx === 0 ? rec.session_1_id : rec.session_2_id;
      if (sid) sel.value = sid;
      else if (prev) sel.value = prev;
      _aeEnhanceSelect(sel);
    });
  }

  _render();

  // 세션이 아직 없으면 비동기 로드 후 다시 렌더
  if ((!allSessions || allSessions.length === 0) && typeof loadSessions === 'function') {
    loadSessions().then(() => _render());
  }
}

function _populateRaterSelects(rec) {
  const raters = aeConfig.raters || [];
  const baseOpt = '<option value="">— Select —</option>';
  const opts = raters.map(r => `<option value="${_aeEscHtml(r)}">${_aeEscHtml(r)}</option>`).join('');

  const obsSel = document.getElementById('aeObsRater');
  obsSel.innerHTML = baseOpt + opts;
  if (rec.obs_rater) obsSel.value = rec.obs_rater;
  _aeEnhanceSelect(obsSel);

  const netSel = document.getElementById('aeNetRater');
  netSel.innerHTML = baseOpt + opts;
  if (rec.net_rater) netSel.value = rec.net_rater;
  _aeEnhanceSelect(netSel);
}

// ── 커스텀 애니메이션 드롭다운 (네이티브 <select> enhance) ───────────────────
// 네이티브 <select>는 브라우저 위젯이라 CSS 트랜지션이 안 먹힘.
// 네이티브는 hidden으로 유지(폼 호환·기존 onchange·disabled 연동)하고
// 옆에 버튼 트리거 + grid-template-rows 애니메이션 패널을 생성.
function _aeEnhanceSelect(select) {
  if (!select) return;
  // 이미 enhance 된 경우: 옵션·값만 재동기화
  if (select._aeSelInst) { select._aeSelInst.sync(); return; }

  const wrap = document.createElement('div');
  wrap.className = 'ae-sel';
  select.parentNode.insertBefore(wrap, select);
  wrap.appendChild(select);
  select.classList.add('ae-sel-native');
  select.setAttribute('tabindex', '-1');
  select.setAttribute('aria-hidden', 'true');

  const trigger = document.createElement('button');
  trigger.type = 'button';
  trigger.className = 'ae-sel-trigger';
  trigger.setAttribute('aria-haspopup', 'listbox');
  trigger.setAttribute('aria-expanded', 'false');
  trigger.innerHTML = '<span class="ae-sel-label"></span><i class="bi bi-chevron-down ae-sel-caret"></i>';
  wrap.appendChild(trigger);

  const panel = document.createElement('div');
  panel.className = 'ae-sel-panel';
  panel.setAttribute('role', 'listbox');
  const inner = document.createElement('div');
  inner.className = 'ae-sel-panel-inner';
  panel.appendChild(inner);
  wrap.appendChild(panel);

  let highlighted = -1;

  function syncLabel() {
    const label = trigger.querySelector('.ae-sel-label');
    const opt   = select.options[select.selectedIndex];
    label.textContent = opt ? opt.textContent : '';
    label.classList.toggle('placeholder', !select.value);
  }

  function renderOptions() {
    inner.innerHTML = '';
    Array.from(select.options).forEach((opt, i) => {
      const item = document.createElement('button');
      item.type = 'button';
      item.className = 'ae-sel-option';
      item.setAttribute('role', 'option');
      const isSelected = i === select.selectedIndex;
      item.setAttribute('aria-selected', isSelected ? 'true' : 'false');
      item.dataset.index = String(i);
      item.textContent = opt.textContent;
      if (isSelected) item.classList.add('selected');
      inner.appendChild(item);
    });
  }

  function updateHighlight() {
    inner.querySelectorAll('.ae-sel-option').forEach((el, i) => {
      el.classList.toggle('highlighted', i === highlighted);
      // scrollIntoView 는 scroll 이벤트를 캡처로 받는 close 핸들러를 잘못 발화시킬 수 있어
      // 컨테이너 내부 수동 스크롤 로직으로 대체.
      if (i === highlighted && el.offsetParent) {
        const cTop = inner.scrollTop;
        const cBot = cTop + inner.clientHeight;
        const eTop = el.offsetTop;
        const eBot = eTop + el.offsetHeight;
        if (eTop < cTop) inner.scrollTop = eTop;
        else if (eBot > cBot) inner.scrollTop = eBot - inner.clientHeight;
      }
    });
  }

  function isOpen() { return wrap.classList.contains('open'); }

  // 패널을 viewport 좌표로 배치 — 부모 overflow/scroll 에 의해 잘리지 않도록.
  // 트리거 너비보다 좁으면 어색하니 min-width 로 맞춤. 아래 공간이 부족하면 위로 뒤집음.
  function positionPanel() {
    const rect = trigger.getBoundingClientRect();
    const panelMax = 320;
    const vh = window.innerHeight || document.documentElement.clientHeight;
    const spaceBelow = vh - rect.bottom;
    const spaceAbove = rect.top;
    const openUp = spaceBelow < panelMax + 16 && spaceAbove > spaceBelow;
    if (openUp) {
      panel.style.top = 'auto';
      panel.style.bottom = (vh - rect.top + 4) + 'px';
      panel.style.transformOrigin = 'bottom center';
    } else {
      panel.style.bottom = 'auto';
      panel.style.top    = (rect.bottom + 4) + 'px';
      panel.style.transformOrigin = 'top center';
    }
    panel.style.left     = rect.left + 'px';
    panel.style.minWidth = rect.width + 'px';
  }

  function onOutsideScroll(ev) {
    // 패널 내부 스크롤(옵션이 많을 때)은 무시 — 외부 컨테이너 스크롤 시에만 닫음.
    if (panel.contains(ev.target)) return;
    close();
  }

  function open() {
    if (select.disabled || isOpen()) return;
    renderOptions();
    positionPanel();
    // max-height 를 먼저 snap — 트랜지션이 제거돼 있으므로 즉시 반영됨.
    // offsetHeight 는 border 포함 → scrollHeight 로 측정 시 하단 2px border 가
    // overflow:hidden 에 잘리던 문제 해결. 패널-inner 자체 max-height 280 + border 4 + 여유 6.
    panel.style.maxHeight = 'none';
    const innerEl = panel.firstElementChild; // .ae-sel-panel-inner
    const measured = innerEl ? innerEl.offsetHeight : 0;
    const contentH = Math.min(measured, 290);
    panel.style.maxHeight = contentH + 'px';
    void panel.offsetHeight;
    wrap.classList.add('open');
    trigger.setAttribute('aria-expanded', 'true');
    highlighted = select.selectedIndex;
    updateHighlight();
    document.addEventListener('click', outsideClick, true);
    document.addEventListener('keydown', onKey, true);
    document.addEventListener('scroll', onOutsideScroll, true);
    window.addEventListener('resize', close);
  }

  function close() {
    if (!isOpen()) return;
    wrap.classList.remove('open');
    // max-height 를 0 으로 되돌려 다음 open 시 0→contentH 로 애니메이션
    panel.style.maxHeight = '0px';
    trigger.setAttribute('aria-expanded', 'false');
    document.removeEventListener('click', outsideClick, true);
    document.removeEventListener('keydown', onKey, true);
    document.removeEventListener('scroll', onOutsideScroll, true);
    window.removeEventListener('resize', close);
  }

  function outsideClick(ev) {
    if (!wrap.contains(ev.target) && !panel.contains(ev.target)) close();
  }

  function pick(index) {
    if (index < 0 || index >= select.options.length) return;
    select.selectedIndex = index;
    select.dispatchEvent(new Event('change', { bubbles: true }));
    syncLabel();
    close();
    trigger.focus();
  }

  function onKey(ev) {
    const n = select.options.length;
    if (!n) return;
    switch (ev.key) {
      case 'Escape':    ev.preventDefault(); close(); trigger.focus(); break;
      case 'ArrowDown': ev.preventDefault(); highlighted = (highlighted + 1) % n; updateHighlight(); break;
      case 'ArrowUp':   ev.preventDefault(); highlighted = (highlighted - 1 + n) % n; updateHighlight(); break;
      case 'Home':      ev.preventDefault(); highlighted = 0; updateHighlight(); break;
      case 'End':       ev.preventDefault(); highlighted = n - 1; updateHighlight(); break;
      case 'Enter':     ev.preventDefault(); if (highlighted >= 0) pick(highlighted); break;
      case 'Tab':       close(); break;
    }
  }

  trigger.addEventListener('click', (ev) => {
    ev.stopPropagation();
    isOpen() ? close() : open();
  });
  inner.addEventListener('click', (ev) => {
    const opt = ev.target.closest('.ae-sel-option');
    if (!opt) return;
    pick(Number(opt.dataset.index));
  });

  select._aeSelInst = { sync: syncLabel, close: close };
  syncLabel();
}

// 모달 닫힘·페이지 전환 등 외부 상황에서 열려있는 모든 ae-sel 드롭다운을 안전 종료.
// 열린 상태로 남아있으면 document 에 등록된 click/keydown/scroll 리스너가 리크됨.
function _closeAllAeSelects() {
  document.querySelectorAll('.ae-sel.open').forEach(wrap => {
    const nat = wrap.querySelector('select.ae-sel-native');
    if (nat && nat._aeSelInst && typeof nat._aeSelInst.close === 'function') {
      nat._aeSelInst.close();
    }
  });
}

function _setAeRegScore(idx, sessionId, score) {
  const display = document.getElementById(`aeScore${idx}Display`);
  const manual  = document.getElementById(`aeScore${idx}Manual`);
  if (sessionId === '__manual__') {
    display.classList.add('hidden');
    manual.classList.remove('hidden');
    manual.value = score != null ? score : '';
  } else {
    display.classList.remove('hidden');
    manual.classList.add('hidden');
    display.textContent = score != null ? score : '—';
    // hidden manual input 의 잔존값이 클라사이드 reg_final 즉시 계산(aeFieldChanged
    // line 840-846)을 오염시키는 것 방지 — test session 변경 시 비움.
    manual.value = '';
  }
}

function _updateRegFinal(score) {
  const el = document.getElementById('aeRegFinalDisplay');
  el.textContent = score != null ? score.toFixed(2) : '—';
}

// ── 세션 변경 → 점수 자동 계산 ───────────────────────────────────────────

async function aeOnSessionChange(idx) {
  if (!aeCurrentRecord) return;
  const sel   = document.getElementById(`aeSession${idx}Select`);
  const sid   = sel.value;
  const field = `session_${idx}_id`;

  aeDirectSave({ [field]: sid });

  if (sid === '__manual__') {
    _setAeRegScore(idx, '__manual__', null);
    return;
  }
  if (!sid) {
    _setAeRegScore(idx, '', null);
    aeDirectSave({ [`reg_score_${idx}`]: null });
    return;
  }

  // eval_type을 공란으로 보내면 서버가 응답 문서에서 역할별 eval_type을 자동 감지.
  // 프론트가 교사 역할(regular/position/tl/stl/sub)을 확실히 알 때만 지정.
  const evalType = aeCurrentRecord.eval_type || '';

  const s1 = document.getElementById('aeSession1Select').value;
  const s2 = document.getElementById('aeSession2Select').value;

  try {
    const res = await _aeFetch('/api/v2/annual-eval/calc-scores',
      { emp_id: aeCurrentEmpId, eval_type: evalType,
        session_1_id: s1, session_2_id: s2,
        eval_deadline: aeCurrentDeadline });
    const data = await res.json();
    if (data.status !== 'SUCCESS') return;

    _setAeRegScore(1, s1, data.score_1);
    _setAeRegScore(2, s2, data.score_2);
    _updateRegFinal(data.reg_final);

    aeDirectSave({
      reg_score_1: data.score_1, reg_score_2: data.score_2,
      reg_final_score: data.reg_final,
    });
  } catch (e) { console.error('aeAutoCalcReg error:', e); }
}

// ── 필드 변경 → 디바운스 저장 ─────────────────────────────────────────────

// 코멘트 필드 → 번역 대상 div 매핑
const _AE_TRANSLATE_MAP = { obs_eng: 'aeObsKo', net_eng: 'aeNetKo', other_eng: 'aeOtherKo' };

function aeFieldChanged(field, value) {
  // status 변경 시 즉시 잠금/해제 반영
  if (field === 'status') {
    if (aeCurrentRecord) aeCurrentRecord.status = value;
    _aeSetFieldLock(value === 'done');
  }

  clearTimeout(_aeSaveDebounceTimers[field]);
  // pending에 기록 (닫을 때 flush용)
  _aePendingFields[field] = value;
  _aeSaveDebounceTimers[field] = setTimeout(() => {
    let saveValue = value;
    if (['reg_score_1','reg_score_2','obs_score','net_score','reg_final_score'].includes(field)) {
      const parsed = parseFloat(value);
      if (!isNaN(parsed)) {
        saveValue = Math.min(Math.max(parsed, 0), 100);
      }
    }
    delete _aePendingFields[field]; // 저장 완료 → pending에서 제거
    aeDirectSave({ [field]: saveValue });
    if (['reg_final_score','obs_score','net_score','reg_score_1','reg_score_2'].includes(field)) {
      if (field === 'reg_score_1' || field === 'reg_score_2') {
        // visible 인 곳의 점수 read — manual visible 이면 input value, test session 이면 display textContent.
        // 둘 중 하나만 visible 이라 hidden 잔존값에 오염되지 않음.
        const _readSlot = (idx) => {
          const manual  = document.getElementById(`aeScore${idx}Manual`);
          const display = document.getElementById(`aeScore${idx}Display`);
          const raw = !manual.classList.contains('hidden') ? manual.value : (display.textContent || '');
          const v = parseFloat(raw);
          return isNaN(v) ? 0 : Math.min(Math.max(v, 0), 100);
        };
        const s1 = _readSlot(1);
        const s2 = _readSlot(2);
        const vals = [s1, s2].filter(v => v > 0);
        const final = vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : null;
        _updateRegFinal(final);
      }
      aeUpdateCompositeSummary();
    }
  }, 1500);

  // 코멘트 필드 → 자동 번역 (3초 디바운스)
  const targetDiv = _AE_TRANSLATE_MAP[field];
  if (targetDiv) {
    clearTimeout(_aeTranslateTimers[field]);
    _aeTranslateTimers[field] = setTimeout(() => {
      const textareaId = field === 'obs_eng' ? 'aeObsEng' : field === 'net_eng' ? 'aeNetEng' : 'aeOtherEng';
      aeTranslateComment(textareaId, targetDiv);
    }, 3000);
  }
}

async function aeDirectSave(fields) {
  if (!aeCurrentEmpId || !aeCurrentDeadline) {
    showToast('Editor not ready — please wait for teacher data to load.', 'error');
    return;
  }
  const indicator = document.getElementById('aeSaveIndicator');
  indicator.textContent = '...saving';
  indicator.classList.remove('hidden');

  try {
    const res = await _aeFetch('/api/v2/annual-eval/save',
      { emp_id: aeCurrentEmpId, eval_deadline: aeCurrentDeadline,
        eval_sequence: aeCurrentSequence, ...fields });
    const data = await res.json();
    if (data.status !== 'SUCCESS') throw new Error(data.message);

    _aeHasSaved = true;
    // 저장으로 Firestore 가 변경됐으므로 History 캐시를 무효화 → 다음 History 탭 클릭 시
    // 최신 데이터 재조회. 같은 모달 세션 내에서 stale 타임라인이 표시되는 걸 방지.
    _aeEditorHistoryCache = null;
    if (data.composite_score != null) {
      aeUpdateCompositeSummary(data.composite_score);
    }
    indicator.textContent = '✓ Saved';
    setTimeout(() => indicator.classList.add('hidden'), 2000);
  } catch (e) {
    indicator.textContent = '⚠ Save failed';
    indicator.style.color = '#dc2626';
    setTimeout(() => { indicator.classList.add('hidden'); indicator.style.color = ''; }, 3000);
  }
}

// ── Salary 계산 ────────────────────────────────────────────────────────────

function aeSalaryCalc() {
  const base    = parseInt(document.getElementById('aeBaseCurrent').value)    || 0;
  const baseInc = parseInt(document.getElementById('aeBaseInc').value)         || 0;
  const pos     = parseInt(document.getElementById('aePoseCurrent').value)    || 0;
  const posInc  = parseInt(document.getElementById('aePosInc').value)          || 0;
  const role    = parseInt(document.getElementById('aeRoleCurrent').value)    || 0;
  const roleInc = parseInt(document.getElementById('aeRoleInc').value)         || 0;
  const hous    = parseInt(document.getElementById('aeHousingCurrent').value) || 0;
  const housInc = parseInt(document.getElementById('aeHousingInc').value)      || 0;

  document.getElementById('aeBaseApplied').textContent    = base + baseInc;
  document.getElementById('aePosApplied').textContent     = pos + posInc;
  document.getElementById('aeRoleApplied').textContent    = role + roleInc;
  document.getElementById('aeHousingApplied').textContent = hous + housInc;

  const totalCurr = base + pos + role + hous;
  const totalInc  = baseInc + posInc + roleInc + housInc;
  const totalApp  = totalCurr + totalInc;

  document.getElementById('aeTotalCurrent').textContent = totalCurr;
  document.getElementById('aeTotalInc').textContent     = totalInc;
  document.getElementById('aeTotalApplied').textContent = totalApp;

  // render 중에는 save를 큐잉하지 않음 (유령 저장 방지 — 닫을 때 리스트 유령 새로고침 유발)
  if (!_aeRendering) {
    clearTimeout(_aeSaveDebounceTimers['_salary']);
    _aeSaveDebounceTimers['_salary'] = setTimeout(() => {
      aeDirectSave({
        base_current: base,   base_inc: baseInc,
        pos_current: pos,     pos_inc: posInc,
        role_current: role,   role_inc: roleInc,
        housing_current: hous, housing_inc: housInc,
        total_current: totalCurr, applied_total: totalApp,
      });
    }, 1500);
  }
  // 기본급 변경 시 정책 재적용 (aeSalaryCalc는 base_current 변경 시도 호출됨)
  aeApplyRaisePolicy();
}

// ── Composite Score 요약 ───────────────────────────────────────────────────

function aeUpdateCompositeSummary(overrideComposite) {
  const w = aeConfig.score_weights || { reg_eval: 50, obs_eval: 30, net_eval: 20 };
  const wReg = w.reg_eval || 0;
  const wObs = w.obs_eval || 0;
  const wNet = w.net_eval || 0;

  // None/empty와 0을 구분: 입력값이 있으면 (0 포함) 가중치에 포함
  const regText = document.getElementById('aeRegFinalDisplay').textContent.trim();
  const obsText = document.getElementById('aeObsScore').value.trim();
  const netText = document.getElementById('aeNetScore').value.trim();
  const regScore = regText && regText !== '—' ? parseFloat(regText) : null;
  const obsScore = obsText !== '' ? parseFloat(obsText) : null;
  const netScore = netText !== '' ? parseFloat(netText) : null;

  document.getElementById('aeCompWReg').textContent = wReg;
  document.getElementById('aeCompWObs').textContent = wObs;
  document.getElementById('aeCompWNet').textContent = wNet;
  document.getElementById('aeCompRegScore').textContent = regScore != null ? regScore.toFixed(2) : '—';
  document.getElementById('aeCompObsScore').textContent = obsScore != null ? obsScore.toFixed(2) : '—';
  document.getElementById('aeCompNetScore').textContent = netScore != null ? netScore.toFixed(2) : '—';

  let wSum = 0, vSum = 0;
  if (regScore != null && !isNaN(regScore)) { vSum += regScore * wReg; wSum += wReg; }
  if (obsScore != null && !isNaN(obsScore)) { vSum += obsScore * wObs; wSum += wObs; }
  if (netScore != null && !isNaN(netScore)) { vSum += netScore * wNet; wSum += wNet; }

  const composite = overrideComposite != null
    ? overrideComposite
    : (wSum > 0 ? Math.round(vSum / wSum * 100) / 100 : null);

  // 재정규화된 기여 점수 (합 = composite)
  document.getElementById('aeCompRegContrib').textContent =
    regScore != null && wSum > 0 ? (regScore * wReg / wSum).toFixed(2) : '—';
  document.getElementById('aeCompObsContrib').textContent =
    obsScore != null && wSum > 0 ? (obsScore * wObs / wSum).toFixed(2) : '—';
  document.getElementById('aeCompNetContrib').textContent =
    netScore != null && wSum > 0 ? (netScore * wNet / wSum).toFixed(2) : '—';

  document.getElementById('aeCompFinal').textContent =
    composite != null ? composite.toFixed(2) : '—';

  // 점수 변경 시 정책 재적용 (등급 배지도 정책 매칭 결과로 표시)
  aeApplyRaisePolicy();
}

// ── Grade 표시 (Raise Policy tier의 note 값 기반) ───────────────────────────

const _AE_GRADE_CLS = { S:'ae-grade-S', A:'ae-grade-A', B:'ae-grade-B', C:'ae-grade-C', D:'ae-grade-D', F:'ae-grade-F' };

function _aeUpdateGradeBadge(grade) {
  const row   = document.getElementById('aeGradeBadgeRow');
  const badge = document.getElementById('aeGradeBadge');
  if (!row || !badge) return;

  if (!grade) {
    row.style.display = 'none';
    return;
  }

  const letter = grade.trim().toUpperCase();
  const cls = _AE_GRADE_CLS[letter] || 'ae-grade-F';
  badge.textContent = letter;
  badge.className   = 'ae-grade-badge ' + cls;
  row.style.display = '';
}

// ── 섹션 접기/펴기 ─────────────────────────────────────────────────────────

// 섹션 접기/펴기 — display:none 즉시 전환 대신 max-height + padding + opacity
// 트랜지션으로 부드럽게. 현재 offsetHeight 와 목표 scrollHeight 를 JS 가 계산.
function toggleAeSection(header) {
  const body = header.nextElementSibling;
  const chevron = header.querySelector('.ae-chevron');
  const isCollapsed = body.dataset.aeCollapsed === '1';

  if (isCollapsed) {
    // EXPAND: 0 → scrollHeight
    body.dataset.aeCollapsed = '0';
    body.style.maxHeight     = body.scrollHeight + 'px';
    body.style.opacity       = '1';
    body.style.paddingTop    = '';  // 인라인 style="padding:14px 16px" 복원
    body.style.paddingBottom = '';
    // 트랜지션 종료 후 max-height 제거 → 이후 콘텐츠 높이 변경 자유
    const onEnd = (e) => {
      if (e.propertyName !== 'max-height') return;
      if (body.dataset.aeCollapsed === '0') body.style.maxHeight = 'none';
      body.removeEventListener('transitionend', onEnd);
    };
    body.addEventListener('transitionend', onEnd);
    // Fallback — transitionend 가 다른 property (background hover 등) 영향으로 누락
    // 시 max-height 가 stuck → 콘텐츠 일부 잘림. 트랜지션 .32s + buffer 후 강제 해제.
    setTimeout(() => {
      if (body.dataset.aeCollapsed === '0' && body.style.maxHeight !== 'none') {
        body.style.maxHeight = 'none';
        body.removeEventListener('transitionend', onEnd);
      }
    }, 400);
  } else {
    // COLLAPSE: 현재 pixel height → 0
    body.style.maxHeight = body.offsetHeight + 'px';
    void body.offsetHeight;                  // 현재 높이를 pixel 로 커밋
    body.dataset.aeCollapsed = '1';
    body.style.maxHeight     = '0px';
    body.style.opacity       = '0';
    body.style.paddingTop    = '0';
    body.style.paddingBottom = '0';
  }
  if (chevron) chevron.style.transform = isCollapsed ? '' : 'rotate(180deg)';
}

// ── Report 생성 ────────────────────────────────────────────────────────────

async function aeGenerateReport() {
  const btn = document.getElementById('aeGenerateBtn');
  btn.disabled = true;
  btn.innerHTML = '<div class="w-3.5 h-3.5 rounded-full animate-spin flex-shrink-0 inline-block align-middle" style="border:2px solid var(--outline-variant);border-top-color:var(--primary);"></div> Generating...';
  try {
    const res = await _aeFetch('/api/v2/annual-eval/generate-report',
      { emp_id: aeCurrentEmpId, eval_deadline: aeCurrentDeadline });
    const data = await res.json();
    if (data.status !== 'SUCCESS') throw new Error(data.message);

    // 헤더의 폴더 링크 업데이트
    const folderLink = document.getElementById('aeFolderLink');
    if (data.folder_url) { folderLink.href = data.folder_url; folderLink.style.display = ''; }

    if (aeCurrentRecord) { aeCurrentRecord.report_url = data.url; }
    _aeHasSaved = true;
    showToast('Report generated successfully.', 'success');
  } catch (e) {
    showToast('Report generation failed: ' + e.message, 'error');
  } finally {
    btn.disabled = false;
    btn.innerHTML = '<i class="bi bi-file-earmark-pdf-fill"></i> Generate Report';
  }
}

// ── Raise Policy 자동 적용 ─────────────────────────────────────────────────

function aeApplyRaisePolicy() {
  // aeSalaryCalc 내부에서도 호출되므로 재진입 방지
  if (_aeApplyingPolicy) return;
  _aeApplyingPolicy = true;

  const matchInfo   = document.getElementById('aePolicyMatchInfo');
  const baseIncInput = document.getElementById('aeBaseInc');
  if (!baseIncInput) { _aeApplyingPolicy = false; return; }

  // 초기화
  baseIncInput.readOnly = false;
  baseIncInput.style.background = '';
  baseIncInput.style.cursor = '';
  if (matchInfo) { matchInfo.style.display = 'none'; matchInfo.innerHTML = ''; }
  _aeUpdateGradeBadge(null); // 매칭 전 등급 숨기기

  const policy = aeConfig.raise_policy || [];
  if (!policy.length) { _aeApplyingPolicy = false; return; }

  const baseCurrent   = parseInt(document.getElementById('aeBaseCurrent')?.value) || 0;
  const compositeText = document.getElementById('aeCompFinal')?.textContent || '—';
  const composite     = parseFloat(compositeText);
  if (!baseCurrent || isNaN(composite) || compositeText === '—') { _aeApplyingPolicy = false; return; }

  for (const group of policy) {
    const gMin = group.base_min || 0;
    const gMax = group.base_max || 0;   // 0 = 이상(제한없음)
    if (baseCurrent < gMin) continue;
    if (gMax > 0 && baseCurrent > gMax) continue;

    for (const tier of (group.tiers || [])) {
      if (composite < tier.score_min || composite > tier.score_max) continue;

      // 매칭! — note 값을 등급으로 표시
      _aeUpdateGradeBadge(tier.note || null);

      if (tier.manual_input) {
        // 면담 후 직접 입력 — 편집 가능 유지, 안내 표시
        baseIncInput.readOnly = false;
        baseIncInput.style.background = '';
        baseIncInput.style.cursor = '';
        if (matchInfo) {
          matchInfo.style.cssText = 'display:block;margin-bottom:8px;font-size:.75rem;padding:7px 11px;border-radius:3px;background:rgba(217,119,6,.18);color:#92400e;border:1.5px solid #fde68a;';
          matchInfo.innerHTML = '<i class="bi bi-person-lines-fill"></i> Interview required — enter raise amount manually <span style="font-size:.65rem;color:#b45309;display:block;margin-top:2px;">면담 후 직접 입력 구간</span>';
        }
      } else {
        // 자동 입력 + 잠금 — pending debounce 정리 후 덮어쓰기
        clearTimeout(_aeSaveDebounceTimers['base_inc']);
        delete _aePendingFields['base_inc'];
        baseIncInput.value    = tier.base_inc;
        baseIncInput.readOnly = true;
        baseIncInput.style.background = '#f4f4f5';
        baseIncInput.style.cursor     = 'not-allowed';
        if (matchInfo) {
          matchInfo.style.cssText = 'display:block;margin-bottom:8px;font-size:.75rem;padding:7px 11px;border-radius:3px;background:rgba(34,197,94,.10);color:#15803d;border:1.5px solid #86efac;';
          matchInfo.innerHTML = `<i class="bi bi-check-circle-fill"></i> Policy applied · Base raise <strong>+${tier.base_inc}</strong> <span style="font-size:.65rem;color:#166534;display:block;margin-top:2px;">정책 자동 적용</span>`;
        }
        aeSalaryCalc();
      }
      _aeApplyingPolicy = false;
      return; // 첫 번째 매칭 사용
    }
  }
  _aeApplyingPolicy = false;
}

// ── Raise Policy 팝오버 ────────────────────────────────────────────────────

function showRaisePolicy() {
  const box = document.getElementById('aeRaisePolicyBox');
  if (box.style.display !== 'none') { box.style.display = 'none'; return; }
  const policy = aeConfig.raise_policy || [];
  if (!policy.length) {
    box.innerHTML = '<span style="color:var(--text-muted)">No policy configured. Set up in Config → Raise Policy.<br><span style="font-size:.65rem;color:var(--text-dim)">정책이 설정되지 않았습니다.</span></span>';
  } else {
    const baseCurrent   = parseInt(document.getElementById('aeBaseCurrent')?.value) || 0;
    const compositeText = document.getElementById('aeCompFinal')?.textContent || '—';
    const composite     = parseFloat(compositeText);

    // 이 선생님의 기본급에 해당하는 구간만 필터링
    const matched = policy.filter(g => {
      const gMax = g.base_max || 0;
      return baseCurrent >= (g.base_min || 0) && (gMax === 0 || baseCurrent <= gMax);
    });

    if (!matched.length) {
      box.innerHTML = `<span style="color:var(--text-muted)">No matching policy for current base salary (${baseCurrent} 만원).<br><span style="font-size:.65rem;color:var(--text-dim)">현재 기본급에 해당하는 정책이 없습니다.</span></span>`;
    } else {
      box.innerHTML = matched.map(g => {
        const gMax    = g.base_max || 0;
        const rangeEn = gMax ? `Base ${g.base_min}–${gMax}` : `Base ${g.base_min}+`;
        const rangeKr = gMax ? `기본급 ${g.base_min}~${gMax}만원` : `기본급 ${g.base_min}만원 이상`;
        return `<div style="margin-bottom:8px;">
          <div style="font-weight:700;font-size:.78rem;margin-bottom:3px;color:#1d4ed8">
            ▶ ${_aeEscHtml(rangeEn)}${g.label ? ' · ' + _aeEscHtml(g.label) : ''}
            <span style="font-weight:400;font-size:.65rem;color:var(--text-dim);margin-left:4px;">${_aeEscHtml(rangeKr)}</span>
          </div>
          ${(g.tiers || []).map(t => {
            const inTier = !isNaN(composite) && composite >= t.score_min && composite <= t.score_max;
            return `<div style="display:flex;justify-content:space-between;padding:3px 8px;border-bottom:1px solid #dbeafe;${inTier ? 'background:#eff6ff;font-weight:700;' : ''}">
              <span>Score ${t.score_min}–${t.score_max}</span>
              <span style="color:${inTier ? '#1d4ed8' : '#374151'}">${t.manual_input ? 'Interview required · <span style="font-size:.65rem;color:var(--text-dim)">면담 후 결정</span>' : '+' + t.base_inc}${t.note ? ' · ' + _aeEscHtml(t.note) : ''}</span>
            </div>`;
          }).join('')}
        </div>`;
      }).join('');
    }
  }
  box.style.display = 'block';
}

// ── Config Modal ───────────────────────────────────────────────────────────

function openAeConfigModal() {
  loadAeConfig().then(() => {
    _aeRenderConfigModal();
    document.getElementById('aeConfigModal').style.display = 'flex';
  });
}

async function loadAeConfig() {
  try {
    const res = await _aeFetch('/api/v2/annual-eval/config');
    const data = await res.json();
    if (data.status === 'SUCCESS') {
      aeConfig = data.config;
      _aeRatersLocal = [...(aeConfig.raters || [])];
      // 구버전 flat 형식(score_min at root) → 새 grouped 형식으로 마이그레이션
      const rawPolicy = aeConfig.raise_policy || [];
      _aeRaisePolicyLocal = JSON.parse(JSON.stringify(rawPolicy)).map(item => {
        if ('score_min' in item && !('tiers' in item)) {
          return {
            base_min: 0, base_max: 0, label: item.label || '',
            tiers: [{
              score_min: item.score_min || 0,
              score_max: item.score_max || 100,
              base_inc:  item.suggested_base_inc || 0,
              manual_input: false,
              note: item.note || '',
            }],
          };
        }
        return item;
      });
    }
  } catch (e) { console.error('aeLoadConfig error:', e); }
}

function _aeRenderConfigModal() {
  const sw = aeConfig.score_weights || { reg_eval: 50, obs_eval: 30, net_eval: 20 };
  document.getElementById('aeWtReg').value = sw.reg_eval;
  document.getElementById('aeWtObs').value = sw.obs_eval;
  document.getElementById('aeWtNet').value = sw.net_eval;
  aeWeightTotal();
  _aeRenderRatersList();
  _aeRenderRaisePolicyList();
  switchAeCfgTab(_aeCfgTab);
}

function switchAeCfgTab(name) {
  _aeCfgTab = name;
  ['weights', 'raters', 'raise', 'history'].forEach(t => {
    const panel = document.getElementById(`ae-cfg-panel-${t}`);
    const tab   = document.getElementById(`ae-cfg-tab-${t}`);
    if (panel) panel.style.display = t === name ? '' : 'none';
    if (tab) tab.classList.toggle('active', t === name);
  });
  if (name === 'history') renderAeHistory([]);
}

// Weights
function aeWeightTotal() {
  const reg = parseInt(document.getElementById('aeWtReg').value) || 0;
  const obs = parseInt(document.getElementById('aeWtObs').value) || 0;
  const net = parseInt(document.getElementById('aeWtNet').value) || 0;
  const total = reg + obs + net;
  const el = document.getElementById('aeWtTotal');
  el.textContent = `${total} %`;
  el.style.color = total === 100 ? '#15803d' : '#dc2626';
  document.getElementById('aeWtSaveBtn').disabled = total !== 100;
}

async function saveAeWeights() {
  const reg = parseInt(document.getElementById('aeWtReg').value) || 0;
  const obs = parseInt(document.getElementById('aeWtObs').value) || 0;
  const net = parseInt(document.getElementById('aeWtNet').value) || 0;
  try {
    const res = await _aeFetch('/api/v2/annual-eval/config',
      { score_weights: { reg_eval: reg, obs_eval: obs, net_eval: net } });
    const data = await res.json();
    if (data.status !== 'SUCCESS') throw new Error(data.message);
    aeConfig.score_weights = { reg_eval: reg, obs_eval: obs, net_eval: net };
    showToast('Weights saved.', 'success');
    aeUpdateCompositeSummary();
  } catch (e) { showToast(e.message, 'error'); }
}

// Raters
function _aeRenderRatersList() {
  const container = document.getElementById('aeRatersList');
  container.innerHTML = _aeRatersLocal.map((r, i) =>
    `<div style="display:flex;align-items:center;gap:8px;border:1.5px solid #d4d4d8;border-radius:3px;padding:7px 12px;background:#fff;">
      <span class="flex-1 text-sm font-semibold" style="color:var(--text-strong)">${_aeEscHtml(r)}</span>
      <button onclick="_aeRemoveRater(${i})" class="text-red-500 hover:text-red-700" style="background:none;border:none;cursor:pointer;font-size:1rem;line-height:1;">×</button>
    </div>`
  ).join('') || '<p class="text-xs text-gray-400">No raters added.</p>';
}

function _aeRemoveRater(i) {
  _aeRatersLocal.splice(i, 1);
  _aeRenderRatersList();
}

function aeAddRater() {
  const inp = document.getElementById('aeRaterInput');
  const name = inp.value.trim();
  if (!name) return;
  _aeRatersLocal.push(name);
  inp.value = '';
  _aeRenderRatersList();
}

async function saveAeRaters() {
  try {
    const res = await _aeFetch('/api/v2/annual-eval/config', { raters: _aeRatersLocal });
    const data = await res.json();
    if (data.status !== 'SUCCESS') throw new Error(data.message);
    aeConfig.raters = [..._aeRatersLocal];
    showToast('Raters saved.', 'success');
    if (aeCurrentRecord) _populateRaterSelects(aeCurrentRecord);
  } catch (e) { showToast(e.message, 'error'); }
}

// Raise Policy (그룹 구조: 기본급 구간 > 점수 구간 여러 개)
function _aeRenderRaisePolicyList() {
  const container = document.getElementById('aeRaisePolicyList');
  if (!_aeRaisePolicyLocal.length) {
    container.innerHTML = '<p class="text-xs" style="color:var(--text-dim);margin-bottom:4px;">정책 없음 — 아래 버튼으로 기본급 구간을 추가하세요.</p>';
    return;
  }
  container.innerHTML = _aeRaisePolicyLocal.map((g, gi) => {
    const gMax = g.base_max || 0;
    const tierRows = (g.tiers || []).map((tier, ti) => `
      <div style="display:grid;grid-template-columns:auto 1fr auto auto auto;align-items:center;gap:6px;padding:7px 0;border-bottom:1px solid #f4f4f5;">
        <div style="display:flex;align-items:center;gap:4px;white-space:nowrap;">
          <span style="font-size:.7rem;font-weight:700;color:var(--on-surface-variant);">점수</span>
          <input type="number" value="${tier.score_min ?? 0}" min="0" max="100"
            oninput="_aeRaisePolicyLocal[${gi}].tiers[${ti}].score_min=parseFloat(this.value)||0"
            style="border:1.5px solid #d4d4d8;border-radius:3px;width:52px;text-align:right;padding:3px 6px;font-size:.8rem;">
          <span style="font-size:.7rem;color:var(--text-muted);">~</span>
          <input type="number" value="${tier.score_max ?? 100}" min="0" max="100"
            oninput="_aeRaisePolicyLocal[${gi}].tiers[${ti}].score_max=parseFloat(this.value)||100"
            style="border:1.5px solid #d4d4d8;border-radius:3px;width:52px;text-align:right;padding:3px 6px;font-size:.8rem;">
        </div>
        <div style="display:flex;align-items:center;gap:5px;">
          ${tier.manual_input
            ? `<span style="font-size:.7rem;font-weight:700;padding:2px 8px;border-radius:3px;background:rgba(217,119,6,.18);color:#92400e;white-space:nowrap;">면담 직접 입력</span>`
            : `<span style="font-size:.7rem;font-weight:700;color:var(--on-surface-variant);white-space:nowrap;">→ +</span>
               <input type="number" value="${tier.base_inc || 0}"
                 oninput="_aeRaisePolicyLocal[${gi}].tiers[${ti}].base_inc=parseInt(this.value)||0"
                 style="border:1.5px solid #d4d4d8;border-radius:3px;width:58px;text-align:right;padding:3px 6px;font-size:.8rem;font-weight:700;">
               <span style="font-size:.7rem;color:var(--text-muted);">만원</span>`
          }
        </div>
        <label style="display:flex;align-items:center;gap:3px;cursor:pointer;white-space:nowrap;font-size:.7rem;color:var(--text-muted);">
          <input type="checkbox" ${tier.manual_input ? 'checked' : ''}
            onchange="_aeRaisePolicyLocal[${gi}].tiers[${ti}].manual_input=this.checked;_aeRenderRaisePolicyList()">
          면담
        </label>
        <input type="text" value="${_aeEscHtml(tier.note || '')}" maxlength="100"
          oninput="_aeRaisePolicyLocal[${gi}].tiers[${ti}].note=this.value"
          style="border:1.5px solid #d4d4d8;border-radius:3px;padding:3px 6px;font-size:.75rem;min-width:0;width:100%;" placeholder="등급 (S, A, B...)">
        <button onclick="_aeRaisePolicyLocal[${gi}].tiers.splice(${ti},1);_aeRenderRaisePolicyList()"
          style="background:none;border:none;cursor:pointer;color:#dc2626;font-size:1rem;line-height:1;padding:0 2px;">×</button>
      </div>`).join('');

    return `<div style="border:1.5px solid #d4d4d8;border-radius:4px;overflow:hidden;margin-bottom:10px;">
      <div style="padding:9px 12px;display:flex;align-items:center;gap:6px;background:var(--surface-low);border-bottom:1.5px solid #d4d4d8;flex-wrap:wrap;">
        <span style="font-size:.75rem;font-weight:700;color:var(--on-surface-variant);white-space:nowrap;">기본급</span>
        <input type="number" value="${g.base_min || 0}" min="0"
          oninput="_aeRaisePolicyLocal[${gi}].base_min=parseInt(this.value)||0"
          style="border:1.5px solid #d4d4d8;border-radius:3px;width:68px;text-align:right;padding:4px 7px;font-size:.8rem;font-weight:700;background:#fff;" placeholder="0">
        <span style="font-size:.75rem;color:var(--text-muted);">~</span>
        <input type="number" value="${gMax || ''}" min="0"
          oninput="_aeRaisePolicyLocal[${gi}].base_max=parseInt(this.value)||0"
          style="border:1.5px solid #d4d4d8;border-radius:3px;width:68px;text-align:right;padding:4px 7px;font-size:.8rem;font-weight:700;background:#fff;" placeholder="이상(∞)">
        <span style="font-size:.75rem;color:var(--text-muted);">만원</span>
        <input type="text" value="${_aeEscHtml(g.label || '')}" maxlength="50"
          oninput="_aeRaisePolicyLocal[${gi}].label=this.value"
          style="border:1.5px solid #d4d4d8;border-radius:3px;flex:1;min-width:80px;padding:4px 7px;font-size:.8rem;background:#fff;" placeholder="구간 이름 (선택)">
        <button onclick="_aeRaisePolicyLocal.splice(${gi},1);_aeRenderRaisePolicyList()"
          style="font-size:.7rem;font-weight:700;padding:3px 9px;border-radius:3px;background:#fee2e2;color:#dc2626;border:none;cursor:pointer;white-space:nowrap;">구간 삭제</button>
      </div>
      <div style="padding:6px 12px 10px;background:#fff;">
        ${tierRows || '<p style="font-size:.75rem;color:var(--text-dim);padding:6px 0;">점수 구간 없음</p>'}
        <button onclick="aeAddRaiseTier(${gi})"
          style="margin-top:8px;width:100%;padding:5px;font-size:.75rem;font-weight:600;border:1.5px dashed #d4d4d8;border-radius:3px;background:#fff;cursor:pointer;color:var(--on-surface-variant);">
          <i class="bi bi-plus-lg"></i> 점수 구간 추가
        </button>
      </div>
    </div>`;
  }).join('');
}

function aeAddRaiseGroup() {
  _aeRaisePolicyLocal.push({ base_min: 0, base_max: 0, label: '', tiers: [] });
  _aeRenderRaisePolicyList();
}

function aeAddRaiseTier(gi) {
  if (!_aeRaisePolicyLocal[gi].tiers) _aeRaisePolicyLocal[gi].tiers = [];
  _aeRaisePolicyLocal[gi].tiers.push({ score_min: 0, score_max: 100, base_inc: 0, manual_input: false, note: '' });
  _aeRenderRaisePolicyList();
}

async function saveAeRaisePolicy() {
  try {
    const res = await _aeFetch('/api/v2/annual-eval/config', { raise_policy: _aeRaisePolicyLocal });
    const data = await res.json();
    if (data.status !== 'SUCCESS') throw new Error(data.message);
    aeConfig.raise_policy = JSON.parse(JSON.stringify(_aeRaisePolicyLocal));
    showToast('Raise policy saved.', 'success');
  } catch (e) { showToast(e.message, 'error'); }
}

// ── 코멘트 번역 (GPT) ──────────────────────────────────────────────────────

async function aeTranslateComment(textareaId, targetDivId) {
  const text = document.getElementById(textareaId)?.value?.trim();
  const target = document.getElementById(targetDivId);
  if (!target) return;
  if (!text) { target.textContent = ''; return; }

  target.innerHTML = '<span style="color:var(--text-dim);font-size:.75rem;">Translating...</span>';
  try {
    const res = await _aeFetch('/api/v2/annual-eval/translate', { text });
    const data = await res.json();
    if (data.status !== 'SUCCESS') throw new Error(data.message);
    target.textContent = data.translation;

    // 번역 결과를 Firestore에도 저장
    const koFieldMap = { aeObsEng: 'obs_eng_ko', aeNetEng: 'net_eng_ko', aeOtherEng: 'other_eng_ko' };
    const koField = koFieldMap[textareaId];
    if (koField) aeDirectSave({ [koField]: data.translation });
  } catch (e) {
    target.innerHTML = `<span style="color:#dc2626;font-size:.75rem;">Translation failed</span>`;
  }
}

// ── 필드 잠금 (done + report 생성 후) ──────────────────────────────────────

function _aeSetFieldLock(locked) {
  const fields = [
    'aeObsScore', 'aeObsEng', 'aeNetScore', 'aeNetEng', 'aeOtherEng',
    'aeBaseInc', 'aePosInc', 'aeRoleInc', 'aeHousingInc', 'aeAllowanceComment',
  ];
  // aeStatusSelect는 잠금 대상에서 제외 — 항상 편집 가능
  const selects = ['aeObsRater', 'aeNetRater', 'aeSession1Select', 'aeSession2Select'];
  const lockStyle = locked ? '#f4f4f5' : '';
  const cursor    = locked ? 'not-allowed' : '';

  fields.forEach(id => {
    const el = document.getElementById(id);
    if (el) { el.readOnly = locked; el.style.background = lockStyle; el.style.cursor = cursor; }
  });
  selects.forEach(id => {
    const el = document.getElementById(id);
    if (el) { el.disabled = locked; el.style.opacity = locked ? '0.6' : ''; }
  });
  const genBtn = document.getElementById('aeGenerateBtn');
  if (genBtn) {
    genBtn.disabled = locked;
    genBtn.innerHTML = locked
      ? '<i class="bi bi-lock-fill"></i> Locked (Done)'
      : '<i class="bi bi-file-earmark-pdf-fill"></i> Generate Report';
  }
  const unlockBtn = document.getElementById('aeUnlockBtn');
  if (unlockBtn) unlockBtn.style.display = locked ? 'inline-flex' : 'none';
}

// Done 상태를 In Progress 로 되돌려 잠금 해제. Status dropdown 변경과 동일한 경로를 거쳐
// _aeSetFieldLock / 자동 저장이 일괄 처리되도록 aeFieldChanged('status', 'in_progress') 호출.
function aeUnlockRecord() {
  if (!aeCurrentRecord) return;
  if (!confirm('Unlock this record and revert status to In Progress?\n평가 상태를 In Progress 로 되돌리고 필드를 다시 편집 가능하게 합니다.')) return;
  const statusSel = document.getElementById('aeStatusSelect');
  if (statusSel) statusSel.value = 'in_progress';
  if (statusSel && statusSel._aeSelInst) statusSel._aeSelInst.sync();
  aeFieldChanged('status', 'in_progress');
}

// ── Excel/CSV 내보내기 ─────────────────────────────────────────────────────

async function aeExportExcel() {
  if (!confirm('Download the current teacher list as CSV?\n현재 교사 목록을 CSV로 다운로드하시겠습니까?')) return;

  try {
    // 현재 목록 데이터 사용 (캐시 또는 새로 로드)
    const search   = (document.getElementById('aeSearchInput')?.value || '').trim().toLowerCase();
    const campus   = document.getElementById('aeCampusSelect')?.value || '';
    const position = document.getElementById('aePositionFilter')?.value || '';

    const res = await _aeFetch('/api/v2/annual-eval/list', { search, campus, position });
    const data = await res.json();
    if (data.status !== 'SUCCESS') throw new Error(data.message);

    const teachers = data.teachers || [];
    if (!teachers.length) { showToast('No data to export.', 'error'); return; }

    // CSV 생성
    const headers = ['Emp ID','Name','Campus','Position','Status','Eval Deadline','Days Remaining',
      'Reg Score 1','Reg Score 2','Reg Final','Obs Score','NET Score','Composite Score',
      'Base Current','Base Inc','Pos Current','Pos Inc','Role Current','Role Inc',
      'Housing Current','Housing Inc','Total Current','Applied Total'];
    const rows = teachers.map(t => {
      const r = t.record || {};
      return [
        (t.emp_id || '').toUpperCase(), t.name || '', t.campus || '', t.position || '',
        r.status || 'not_started', t.eval_deadline || '', t.days_remaining ?? '',
        r.reg_score_1 ?? '', r.reg_score_2 ?? '', r.reg_final_score ?? '',
        r.obs_score ?? '', r.net_score ?? '', r.composite_score ?? '',
        r.base_current ?? '', r.base_inc ?? '', r.pos_current ?? '', r.pos_inc ?? '',
        r.role_current ?? '', r.role_inc ?? '', r.housing_current ?? '', r.housing_inc ?? '',
        r.total_current ?? '', r.applied_total ?? '',
      ];
    });

    let csv = '\uFEFF'; // BOM for Excel Korean
    csv += headers.map(h => `"${h}"`).join(',') + '\n';
    rows.forEach(row => {
      csv += row.map(v => `"${String(v).replace(/"/g, '""')}"`).join(',') + '\n';
    });

    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `annual_eval_export_${new Date().toISOString().slice(0,10)}.csv`;
    a.click();
    URL.revokeObjectURL(url);
    showToast('Export downloaded.', 'success');
  } catch (e) {
    showToast('Export failed: ' + e.message, 'error');
  }
}

// ── Dashboard Summary ─────────────────────────────────────────────────────

function aeUpdateDashboard(teachers) {
  const dash = document.getElementById('aeDashboard');
  if (!dash) return;
  if (!teachers || !teachers.length) { dash.style.display = 'none'; return; }
  dash.style.display = '';

  let overdue = 0, thisMonth = 0, inProgress = 0, done = 0;
  teachers.forEach(t => {
    const st = t.record ? t.record.status : '';
    if (st === 'done') { done++; return; }
    if (t.days_remaining != null && t.days_remaining < 0) overdue++;
    else if (t.days_remaining != null && t.days_remaining <= 30) thisMonth++;
    if (st === 'in_progress') inProgress++;
  });

  document.getElementById('aeDashOverdue').textContent = overdue;
  document.getElementById('aeDashThisMonth').textContent = thisMonth;
  document.getElementById('aeDashInProgress').textContent = inProgress;
  document.getElementById('aeDashDone').textContent = done;
  document.getElementById('aeDashTotal').textContent = teachers.length;
}

// ── Bulk Generate ─────────────────────────────────────────────────────────

async function aeBulkGenerate() {
  // Count done teachers from cache
  const teachers = _aeListCache ? _aeListCache.teachers : null;
  const doneList = teachers ? teachers.filter(t => t.record && t.record.status === 'done') : [];
  const count = doneList.length;

  if (count === 0) {
    showToast('No teachers with "done" status found.', 'error');
    return;
  }
  if (!confirm(`Generate reports for ${count} teacher(s) with "done" status?\n상태가 "done"인 ${count}명의 교사 보고서를 일괄 생성하시겠습니까?`)) return;

  // PDF 생성이 무거우므로 chunk 3 로 작게 분할 — WeasyPrint 부담 완화.
  const empIds = doneList.map(t => t.emp_id);
  const result = await BulkRunner.run({
    items: empIds,
    chunkSize: 3,
    title: 'Generating annual evaluation reports',
    subtitle: `${count}명의 연간 평가 보고서 생성 중...`,
    url: '/api/v2/annual-eval/bulk-generate',
    bodyKey: 'emp_ids',
    tallyFn: (res, chunk) => {
      if (!res || res.status !== 'SUCCESS') {
        return { error: chunk.length };
      }
      const results = res.results || [];
      let success = 0, error = 0;
      for (const r of results) {
        if (r.status === 'SUCCESS') success++;
        else error++;
      }
      return { success, error };
    },
  });

  const parts = [];
  if (result.success > 0) parts.push(`${result.success} generated`);
  if (result.error   > 0) parts.push(`${result.error} failed`);
  if (result.cancelled)   parts.push('cancelled');
  const msg = parts.length ? parts.join(', ') + '.' : 'No changes.';
  showToast(msg, result.error > 0 ? 'error' : 'success');

  _aeListCache = null;
  loadAnnualEvalList(true);
}

// ── History Timeline ──────────────────────────────────────────────────────

async function searchAeHistory() {
  const q = document.getElementById('aeHistorySearch').value.trim();
  const container = document.getElementById('aeHistoryResults');
  if (!q) return;
  container.innerHTML = '<p class="text-xs" style="color:var(--text-dim);">Searching...</p>';

  // emp_id로 history 조회
  let historyErr = null;
  let historyData = null;
  try {
    const res = await _aeFetch('/api/v2/annual-eval/history', { emp_id: q });
    const data = await res.json();
    if (data.status !== 'SUCCESS') historyErr = new Error(data.message);
    else historyData = data.history || [];
  } catch (e) { historyErr = e; }

  // 매치된 이력이 있으면 즉시 렌더
  if (historyData && historyData.length > 0) {
    renderAeHistory(historyData);
    return;
  }

  // 이력 없음(이름 검색이거나 미평가 교사) → 교사 목록 fallback
  try {
    const res2 = await _aeFetch('/api/v2/annual-eval/list', { search: q });
    const data2 = await res2.json();
    if (data2.status === 'SUCCESS' && data2.teachers && data2.teachers.length > 0) {
      // 정확히 1명이고 emp_id 일치 → 이력 없음 안내
      if (data2.teachers.length === 1 &&
          (data2.teachers[0].emp_id || '').toLowerCase() === q.toLowerCase()) {
        renderAeHistory([]);
        return;
      }
      container.innerHTML = '<p class="text-xs" style="color:var(--text-muted);margin-bottom:8px;">Select a teacher to view timeline:</p>' +
        data2.teachers.map(t => {
          const name = _aeEscHtml(t.name || t.emp_id);
          const eid  = _aeEscHtml((t.emp_id||'').toUpperCase());
          return `<div onclick="document.getElementById('aeHistorySearch').value='${_aeEscHtml(t.emp_id)}';searchAeHistory()"
            style="border:1.5px solid #d4d4d8;border-radius:3px;padding:8px 12px;background:#fff;cursor:pointer;transition:border-color .15s;"
            onmouseover="this.style.borderColor='var(--primary-dark)'" onmouseout="this.style.borderColor='#d4d4d8'">
            <p class="text-sm font-bold" style="color:var(--text-strong)">${name} <span class="font-mono text-xs" style="color:var(--text-muted)">${eid}</span></p>
            <p class="text-xs" style="color:var(--text-muted)">${_aeEscHtml(t.campus)} · ${_aeEscHtml(t.position)}</p>
          </div>`;
        }).join('');
      return;
    }
    // list 도 비어있음 — history 가 비어있었다면 "미평가" 안내
    if (historyData !== null) { renderAeHistory([]); return; }
    container.innerHTML = `<p class="text-xs" style="color:#dc2626;">${_aeEscHtml((historyErr && historyErr.message) || 'Not found.')}</p>`;
  } catch {
    if (historyData !== null) { renderAeHistory([]); return; }
    container.innerHTML = `<p class="text-xs" style="color:#dc2626;">${_aeEscHtml((historyErr && historyErr.message) || 'Search failed.')}</p>`;
  }
}

function _aeFormatMoney(v) {
  if (v == null || v === 0) return '—';
  return Number(v).toLocaleString();
}

// history 타임라인 렌더러 — config 모달(기본) 과 editor 모달(currentDeadline 하이라이트)
// 두 곳에서 공통 사용. containerId 와 currentDeadline 은 선택 파라미터.
function renderAeHistory(history, containerId = 'aeHistoryResults', currentDeadline = null) {
  const container = document.getElementById(containerId);
  if (!container) return;
  if (!history.length) {
    container.innerHTML = '<p class="text-xs" style="color:var(--text-dim);">No evaluation history found for this teacher.</p>';
    return;
  }

  // Timeline: each entry is a cycle
  container.innerHTML = history.map((h, i) => {
    const score = h.composite_score != null ? h.composite_score.toFixed(1) : '—';
    const isDone = h.status === 'done';
    const dotColor = isDone ? '#15803d' : h.status === 'in_progress' ? '#1d4ed8' : '#a1a1aa';
    const statusLabel = isDone ? 'Done' : h.status === 'in_progress' ? 'In Progress' : 'Not Started';
    const seq = h.eval_sequence || (i + 1);
    const hasReport = !!h.report_url;
    const isCurrent = currentDeadline && h.eval_deadline === currentDeadline;

    const cardStyle = isCurrent
      ? 'flex:1;border:2px solid #870009;border-radius:4px;padding:10px 14px;background:#fef2f2;margin-bottom:8px;'
      : 'flex:1;border:1.5px solid #d4d4d8;border-radius:4px;padding:10px 14px;background:#fff;margin-bottom:8px;';
    const currentBadge = isCurrent
      ? '<span style="font-size:.62rem;font-weight:800;color:#fff;background:#870009;padding:1px 6px;border-radius:3px;margin-right:6px;letter-spacing:.05em;">CURRENT</span>'
      : '';

    return `<div style="display:flex;gap:12px;position:relative;">
      <!-- Timeline line -->
      <div style="display:flex;flex-direction:column;align-items:center;flex-shrink:0;width:20px;">
        <div style="width:12px;height:12px;border-radius:50%;background:${dotColor};border:2px solid #fff;box-shadow:0 0 0 2px ${dotColor};z-index:1;"></div>
        ${i < history.length - 1 ? '<div style="flex:1;width:2px;background:#d4d4d8;margin:4px 0;"></div>' : ''}
      </div>
      <!-- Card -->
      <div style="${cardStyle}">
        <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:6px;">
          <div>
            ${currentBadge}<span style="font-size:.72rem;font-weight:800;color:#6366f1;background:rgba(99,102,241,.14);padding:1px 6px;border-radius:3px;">#${seq}</span>
            <span style="font-size:.78rem;font-weight:700;color:var(--text-strong);margin-left:6px;">Due: ${_aeEscHtml(h.eval_deadline)}</span>
          </div>
          <div style="display:flex;align-items:center;gap:6px;">
            <span style="font-size:.7rem;font-weight:700;color:${dotColor}">${statusLabel}</span>
            ${hasReport ? `<a href="${_aeEscHtml(h.report_url)}" target="_blank" rel="noopener" style="font-size:.7rem;color:#870009;text-decoration:none;font-weight:700;" title="View Report"><i class="bi bi-file-earmark-text"></i></a>` : ''}
          </div>
        </div>
        <!-- Scores -->
        <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:4px;font-size:.72rem;">
          <div style="text-align:center;padding:4px;background:var(--surface-low);border-radius:3px;">
            <div style="font-weight:700;color:var(--text-strong);">${h.reg_final_score != null ? h.reg_final_score.toFixed(1) : '—'}</div>
            <div style="color:var(--text-dim);font-size:.62rem;">Regular</div>
          </div>
          <div style="text-align:center;padding:4px;background:var(--surface-low);border-radius:3px;">
            <div style="font-weight:700;color:var(--text-strong);">${h.obs_score != null ? h.obs_score.toFixed(1) : '—'}</div>
            <div style="color:var(--text-dim);font-size:.62rem;">Observation</div>
          </div>
          <div style="text-align:center;padding:4px;background:var(--surface-low);border-radius:3px;">
            <div style="font-weight:700;color:var(--text-strong);">${h.net_score != null ? h.net_score.toFixed(1) : '—'}</div>
            <div style="color:var(--text-dim);font-size:.62rem;">NET Coord</div>
          </div>
          <div style="text-align:center;padding:4px;background:${isDone ? '#f0fdf4' : '#f4f4f5'};border-radius:3px;border:1px solid ${isDone ? '#86efac' : 'transparent'};">
            <div style="font-weight:900;color:${isDone ? '#15803d' : '#111114'};">${score}</div>
            <div style="color:var(--text-dim);font-size:.62rem;">Composite</div>
          </div>
        </div>
        <!-- Salary -->
        ${h.base_current || h.applied_total ? `
        <div style="display:flex;gap:12px;margin-top:6px;font-size:.7rem;color:var(--text-muted);">
          <span>Base: ${_aeFormatMoney(h.base_current)}${h.base_inc ? ` <span style="color:${h.base_inc>0?'#15803d':'#dc2626'};">${h.base_inc>0?'+':''}${_aeFormatMoney(h.base_inc)}</span>` : ''}</span>
          <span>Total: ${_aeFormatMoney(h.applied_total || h.total_current)}</span>
        </div>` : ''}
      </div>
    </div>`;
  }).join('');
}

// ── 에디터 모달 내 탭 전환 ───────────────────────────────────────────────
function switchAeEditorTab(name) {
  _aeEditorTab = name;
  const evalBody = document.getElementById('aeEditorBodyEval');
  const histBody = document.getElementById('aeEditorBodyHistory');
  const evalTab  = document.getElementById('ae-ed-tab-eval');
  const histTab  = document.getElementById('ae-ed-tab-history');
  if (evalBody) evalBody.style.display = name === 'eval' ? '' : 'none';
  if (histBody) histBody.style.display = name === 'history' ? '' : 'none';
  if (evalTab)  evalTab.classList.toggle('active', name === 'eval');
  if (histTab)  histTab.classList.toggle('active', name === 'history');

  if (name === 'history') {
    // 캐시가 다른 교사 것이면 무효화
    if (_aeEditorHistoryCache && _aeEditorHistoryCache.empId !== aeCurrentEmpId) {
      _aeEditorHistoryCache = null;
    }
    if (_aeEditorHistoryCache) {
      renderAeHistory(_aeEditorHistoryCache.data, 'aeEditorHistoryResults', aeCurrentDeadline);
    } else {
      loadAeEditorHistory();
    }
  }
}

async function loadAeEditorHistory() {
  const container = document.getElementById('aeEditorHistoryResults');
  if (!container) return;
  if (!aeCurrentEmpId) {
    container.innerHTML = '<p class="text-xs" style="color:var(--text-dim);">No teacher selected.</p>';
    return;
  }
  // empId + 단조 증가 시퀀스로 stale 응답 드롭 (A→B→A 레이스 대비)
  const reqEmpId = aeCurrentEmpId;
  const reqSeq   = ++_aeHistReqSeq;
  container.innerHTML = '<div class="flex items-center gap-2 py-6 justify-center" style="color:var(--text-dim);">' +
    '<span class="spin">⟳</span><span class="text-xs">Loading history...</span></div>';
  try {
    const res = await _aeFetch('/api/v2/annual-eval/history', { emp_id: reqEmpId });
    const ctype = res.headers.get('content-type') || '';
    if (!res.ok || !ctype.includes('application/json')) {
      throw new Error(`Server returned ${res.status}`);
    }
    const data = await res.json();
    if (data.status !== 'SUCCESS') throw new Error(data.message || 'Failed to load history.');
    if (reqSeq !== _aeHistReqSeq || reqEmpId !== aeCurrentEmpId) return;
    _aeEditorHistoryCache = { empId: reqEmpId, data: data.history || [] };
    renderAeHistory(_aeEditorHistoryCache.data, 'aeEditorHistoryResults', aeCurrentDeadline);
  } catch (e) {
    if (reqSeq !== _aeHistReqSeq || reqEmpId !== aeCurrentEmpId) return;
    container.innerHTML =
      '<div style="padding:14px;background:#fef2f2;border:1.5px solid #fca5a5;border-radius:4px;color:#b91c1c;font-size:.8rem;">' +
      `<p style="font-weight:700;margin-bottom:6px;"><i class="bi bi-exclamation-triangle-fill"></i> Failed to load history</p>` +
      `<p style="font-size:.72rem;color:#7f1d1d;margin-bottom:8px;">${_aeEscHtml(e.message || 'Unknown error')}</p>` +
      '<button onclick="loadAeEditorHistory()" class="btn-secondary text-xs px-3 py-1">Retry</button>' +
      '</div>';
  }
}

