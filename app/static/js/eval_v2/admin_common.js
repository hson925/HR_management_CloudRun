// ── eval_v2 admin — 공통 상태 & 유틸리티 ─────────────────────────────────────

// ── XSS-safe HTML escape ──
// admin_status_render.js 에 동일 시그니처 정의 존재 — 후행 script 가 이를 덮어써도 로직 동일.
// admin_common.js 가 status.html script 로드 순서상 먼저 평가되므로 여기 선언 필수
// (makeStatusBadge 가 같은 파일 내에서 사용).
function _escHtml(str) {
  if (!str) return '';
  return String(str).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}

// ── 전역 상태 ──
let currentCfgType = null, currentCfgWeights = {}, currentCfgData = null;
let currentCfgSession = '';
let allStatusData = {}, statusLoaded = false;
let activeTypeFilter = '';
let openTypeSections = new Set(); // 현재 펼쳐진 직책 섹션들
let allSubmissions = [];
let allSessions = [], activeSession = null, currentSessionFilter = '';
let currentModalTeacher = null, currentModalSessionId = '', currentModalWeights = {};
let allTeachersFlat = [];
let bulkMode = false;
let bulkSelected = new Set();
let _allSelected = false;
let manualEditDocId = '';
let manualEditVersion = 0;
let manualInputRole = '';
let _promptResolve = null;
let _searchDebounceTimer = null;

// ── API 공통 래퍼 ──
// POST JSON → 파싱된 body 반환 (HTTP 4xx/5xx 도 정상적으로 JSON 파싱).
// 서버의 success()/error() 헬퍼와 짝: 정상은 {status:'SUCCESS', ...}, 오류는 {status:'ERROR', message, code?}.
async function apiPost(url, body = {}) {
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  try { return await res.json(); }
  catch { return { status: 'ERROR', message: `Invalid response (HTTP ${res.status}).` }; }
}

// ── 커스텀 프롬프트 ──
// secondaryText 가 주어지면 3-버튼 모드 (취소 / [secondaryText] / [confirmText])
//   취소  → null
//   가운데 → 'secondary'
//   confirm → true (또는 isInput 시 입력값)
function showCustomPrompt(title, message, isInput = false, isDanger = false, confirmText = null, secondaryText = null) {
  return new Promise(resolve => {
    _promptResolve = resolve;
    document.getElementById('promptTitle').innerHTML = `<i class="bi bi-${isInput ? 'plus-circle-fill text-[#B01116]' : 'exclamation-triangle-fill text-red-500'}"></i> ${title}`;
    document.getElementById('promptMessage').textContent = message;
    const inp = document.getElementById('promptInput');
    inp.style.display = isInput ? '' : 'none'; inp.value = '';
    const btn = document.getElementById('promptConfirmBtn');
    btn.textContent = confirmText || (isInput ? 'Add' : 'Delete');
    btn.className = `px-5 py-2.5 text-white text-sm font-bold rounded-xl transition-colors shadow-md ${isDanger ? 'bg-red-600 hover:bg-red-700 shadow-red-200' : 'btn-primary'}`;
    const sBtn = document.getElementById('promptSecondaryBtn');
    if (sBtn) {
      if (secondaryText) {
        sBtn.textContent = secondaryText;
        sBtn.style.display = '';
      } else {
        sBtn.style.display = 'none';
      }
    }
    document.getElementById('customPromptModal').style.display = 'flex';
    if (isInput) setTimeout(() => inp.focus(), 100);
  });
}
function resolveCustomPrompt(type) {
  document.getElementById('customPromptModal').style.display = 'none';
  if (!_promptResolve) return;
  const inp = document.getElementById('promptInput');
  if (type === 'confirm') {
    _promptResolve(inp.style.display !== 'none' ? inp.value.trim() || null : true);
  } else if (type === 'secondary') {
    _promptResolve('secondary');
  } else {
    _promptResolve(null);
  }
  _promptResolve = null;
}

// ── 탭 전환 ──
function switchTab(name) {
  ['status', 'config', 'sessions', 'drafts', 'folders'].forEach(t => {
    const panel = document.getElementById('panel-' + t);
    const tab = document.getElementById('tab-' + t);
    if (panel) panel.style.display = t === name ? '' : 'none';
    if (tab) tab.classList.toggle('active', t === name);
  });
  if (name === 'status') {
    if (!statusLoaded) loadStatus();
    else { document.getElementById('statusLoading').style.display = 'none'; document.getElementById('statusContent').style.display = ''; }
  }
  if (name === 'sessions') loadSessions();
  if (name === 'drafts') initDraftsTab();
  if (name === 'folders' && typeof loadFolderManager === 'function') loadFolderManager();
}
function forceReloadStatus() { switchTab('status'); statusLoaded = false; loadStatus(); }

// ── 토스트 ──
function showToast(msg, type = 'success') {
  const container = document.getElementById('toastContainer');
  const t = document.createElement('div');
  t.style.cssText = `pointer-events:auto;display:flex;align-items:flex-start;gap:10px;padding:12px 16px;border-radius:6px;box-shadow:0 4px 16px rgba(0,0,0,.18);font-size:.875rem;font-weight:600;color:#fff;min-width:260px;max-width:380px;line-height:1.4;opacity:0;transform:translateX(24px);transition:all .25s;background:${type === 'success' ? '#059669' : type === 'info' ? '#2563eb' : '#B01116'};border:1.5px solid ${type === 'success' ? '#047857' : type === 'info' ? '#1d4ed8' : '#7a0b0f'};`;
  t.innerHTML = `<i class="bi bi-${type === 'success' ? 'check-circle-fill' : type === 'info' ? 'info-circle-fill' : 'exclamation-circle-fill'}" style="flex-shrink:0;margin-top:2px"></i><span style="flex:1">${msg}</span><button onclick="this.parentElement.remove()" style="background:none;border:none;color:rgba(255,255,255,.7);cursor:pointer;padding:0;margin-left:4px;font-size:1rem;line-height:1;flex-shrink:0">×</button>`;
  container.appendChild(t);
  requestAnimationFrame(() => { t.style.opacity = '1'; t.style.transform = 'translateX(0)'; });
  const dur = type === 'error' ? 5000 : 3500;
  setTimeout(() => { t.style.opacity = '0'; t.style.transform = 'translateX(24px)'; setTimeout(() => t.remove(), 280); }, dur);
}
function closeModal(id) { document.getElementById(id).style.display = 'none'; }

// ── 상태 뱃지 ──
// 호출 시그니처: makeStatusBadge(role, current, required, label?)
// label 인자 우선 — 백엔드 _build_status_result 가 eval admin_config 의 label_ko 또는
// portal_roles.label 을 미리 결정해서 응답에 넣어줌. 누락 시 dybRoleLabel fallback.
function makeStatusBadge(role, current, required, label) {
  let cls, icon, extra = '';
  if (current === 0) { cls = 'badge-pending'; icon = 'hourglass'; }
  else if (current < required) { cls = 'badge-inprogress'; icon = 'hourglass-split'; }
  else if (current === required) { cls = 'badge-done'; icon = 'check-circle-fill'; }
  else { cls = 'badge-over'; icon = 'check-circle-fill'; extra = ` <span class="opacity-80">+${current - required}</span>`; }
  const roleDisplay = label
    || ((typeof dybRoleLabel === 'function') ? dybRoleLabel(role) : role)
    || role;
  return `<span class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs font-bold border ${cls}"><i class="bi bi-${icon}"></i>${_escHtml(roleDisplay)}: ${current}/${required}${extra}</span>`;
}

// ── 커스텀 셀렉트 (상태 탭 세션 필터) ──
// _closeAllDyb / outside-click 은 layout.html 에서 전역 정의됨.
function toggleCustomSelect(id) {
  const trigger = document.getElementById(id + 'Trigger');
  const dropdown = document.getElementById(id + 'Dropdown');
  const isOpen = dropdown.classList.contains('open');
  _closeAllDyb();
  if (!isOpen) { dropdown.classList.add('open'); trigger.classList.add('open'); }
}
function pickSession(id, value, label) {
  document.getElementById(id + 'Label').textContent = label;
  document.getElementById(id + 'Select').value = value;
  document.getElementById(id + 'Dropdown').classList.remove('open');
  document.getElementById(id + 'Trigger').classList.remove('open');
  if (id === 'statusSession') onStatusSessionChange(value);
}

// ── 모달 내 세션 셀렉트 ──
function toggleModalSelect() {
  const dropdown = document.getElementById('modalSessionDropdown');
  const trigger = document.getElementById('modalSessionTrigger');
  const isOpen = dropdown.classList.contains('open');
  _closeAllDyb();
  if (!isOpen) { dropdown.classList.add('open'); trigger.classList.add('open'); }
}
function pickModalSession(value, label) {
  document.getElementById('modalSessionLabel').textContent = label;
  document.getElementById('modalSessionSelect').value = value;
  document.getElementById('modalSessionDropdown').classList.remove('open');
  document.getElementById('modalSessionTrigger').classList.remove('open');
  onModalSessionChange(value);
}

// ── 이벤트 리스너 ──
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') { closeModal('detailModal'); closeModal('submissionsModal'); closeModal('manualInputModal'); closeModal('notifyModal'); closeModal('exportModal'); }
});
document.addEventListener('DOMContentLoaded', () => {
  const defaultTab = document.getElementById('panel-status') ? 'status' : 'config';
  switchTab(defaultTab);
  if (typeof loadSessions === 'function') loadSessions();
});

// ── NT Info 메모리 캐시 / Roster 캐시 수동 갱신 ──
// admin.html 과 status.html 양쪽에서 쓰이는 우측 상단 버튼. 동일한 서버 endpoint
// (/api/v2/nt-cache/refresh, /api/v2/roster-cache/refresh) 호출.
async function refreshNtCache() {
  const btn = document.getElementById('ntCacheBtn');
  if (!btn) return;
  const orig = btn.innerHTML;
  btn.innerHTML = '<div class="w-3.5 h-3.5 rounded-full animate-spin flex-shrink-0" style="border:2px solid #f5d0d1;border-top-color:#B01116;"></div> 새로고침 중...';
  btn.disabled = true;
  try {
    const r = await apiFetch('/api/v2/nt-cache/refresh', {
      operation: 'Refresh NT cache',
    });
    if (r.ok) {
      showToast(`NT 캐시 갱신 완료 — ${r.data.count}건 (${r.data.loaded_at})`, 'success');
    } else {
      showToast(r.errorMessage, 'error');
    }
  } finally {
    btn.innerHTML = orig;
    btn.disabled = false;
  }
}

async function refreshRosterCache() {
  const btn = document.getElementById('rosterCacheBtn');
  if (!btn) return;
  const orig = btn.innerHTML;
  btn.innerHTML = '<div class="w-3.5 h-3.5 rounded-full animate-spin flex-shrink-0" style="border:2px solid #bfdbfe;border-top-color:#2563eb;"></div> 새로고침 중...';
  btn.disabled = true;
  try {
    const r = await apiFetch('/api/v2/roster-cache/refresh', {
      operation: 'Refresh roster cache',
    });
    if (r.ok) {
      showToast(`명단 캐시 갱신 완료 — ${r.data.count}건 (${r.data.loaded_at})`, 'success');
    } else {
      showToast(r.errorMessage, 'error');
    }
  } finally {
    btn.innerHTML = orig;
    btn.disabled = false;
  }
}
