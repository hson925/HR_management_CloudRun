// ── eval_v2 admin — TAB 3: 회차 관리 & Bulk Report ───────────────────────────

// ── Session data store (XSS-safe — avoids inline JSON.stringify) ──
const _sessionSchedData = {};
function _escAttr(s) { return String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;'); }

function loadSessions() {
  return fetch('/api/v2/get-sessions', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' })
    .then(r => r.json()).then(res => {
      if (res.status !== 'SUCCESS') { showToast('세션을 불러오지 못했습니다.', 'error'); return; }
      allSessions = res.sessions || []; activeSession = allSessions.find(s => s.status === 'active') || null;

      // Config 탭 세션 셀렉트 업데이트
      const cfgSel  = document.getElementById('cfgSessionSelect');
      const cfgMenu = document.getElementById('cfgSessionMenu');
      if (cfgSel && cfgMenu) {
        const prev = cfgSel.value;
        const defaultLabel = '기본값 (전체 적용)';
        cfgSel.innerHTML  = `<option value="">${defaultLabel}</option>`;
        cfgMenu.innerHTML = `<div class="dyb-dd-option${!prev?' selected':''}" data-value="" onclick="dybPick('cfgSession','','${defaultLabel}')">${defaultLabel}</div>`;
        allSessions.forEach(s => {
          const isAct = s.status === 'active';
          const statusText = isAct ? '● 진행중' : '■ 마감';
          const label = `${s.label} ${statusText}`;
          const opt = document.createElement('option');
          opt.value = s.id; opt.textContent = label;
          if (s.id === prev) opt.selected = true;
          cfgSel.appendChild(opt);
          const div = document.createElement('div');
          div.className = 'dyb-dd-option' + (s.id === prev ? ' selected' : '');
          div.dataset.value = s.id;
          div.innerHTML = `<span class="font-bold" style="color:var(--on-surface)">${s.label}</span><span class="ml-auto text-xs font-bold px-2 py-0.5 rounded-full ${isAct?'bg-emerald-100 text-emerald-700':'bg-slate-100 text-slate-500'}">${statusText}</span>`;
          div.onclick = () => dybPick('cfgSession', s.id, s.label);
          cfgMenu.appendChild(div);
        });
        // trigger label 복원
        if (prev) {
          const selSession = allSessions.find(s => s.id === prev);
          if (selSession) {
            const lbl = document.querySelector('#cfgSessionTrigger .dyb-label');
            if (lbl) lbl.textContent = selSession.label;
          }
        }
      }

      // Status 탭 세션 필터 드롭다운 업데이트
      const statusSel = document.getElementById('statusSessionSelect');
      if (statusSel) {
        const prev = statusSel.value;
        statusSel.innerHTML = '<option value="">— 세션 선택 —</option>';
        const dropdown = document.getElementById('statusSessionDropdown');
        if (dropdown) {
          dropdown.innerHTML = '<div class="dyb-dd-option" style="color:var(--outline);font-size:.8rem" onclick="pickSession(\'statusSession\',\'\',\'— 세션 선택 —\')">— 세션 선택 —</div>';
          allSessions.forEach(s => {
            const opt = document.createElement('option'); opt.value = s.id;
            opt.textContent = `${s.label} ${s.status === 'active' ? '🟢 진행중' : '⬛ 마감'}`;
            if (s.id === prev || s.id === currentSessionFilter) opt.selected = true;
            statusSel.appendChild(opt);
            const isAct = s.status === 'active';
            const div = document.createElement('div');
            div.className = 'dyb-dd-option' + (s.id === (prev || currentSessionFilter) ? ' selected' : '');
            div.innerHTML = `<span class="font-bold" style="color:var(--on-surface)">${s.label}</span><span class="ml-auto text-xs font-bold px-2 py-0.5 rounded-full ${isAct ? 'bg-emerald-100 text-emerald-700' : 'bg-slate-100 text-slate-500'}">${isAct ? '● 진행중' : '■ 마감'}</span>`;
            div.onclick = () => pickSession('statusSession', s.id, s.label);
            dropdown.appendChild(div);
          });
          const selSession = allSessions.find(s => s.id === (prev || currentSessionFilter));
          if (selSession) document.getElementById('statusSessionLabel').textContent = selSession.label;
        }
      }

      // Active 세션 뱃지 업데이트
      const badge = document.getElementById('activeSessionBadge');
      if (activeSession) {
        document.getElementById('activeSessionLabel').textContent = activeSession.label;
        document.getElementById('activeSessionDates').textContent = `${activeSession.start_date} ~ ${activeSession.end_date}`;
        badge.classList.remove('hidden'); badge.classList.add('flex');
      } else { badge.classList.add('hidden'); badge.classList.remove('flex'); }

      renderSessions(allSessions);
    });
}

function renderSessions(sessions) {
  const container = document.getElementById('sessionsList');
  // #sessionsList 는 /eval-v2/admin 에만 존재. /eval-v2/status 처럼 admin_sessions.js
  // 가 공유 로딩되는 페이지에선 null 이라 렌더 스킵 (admin_common.js:158 의 자동 호출 대응).
  if (!container) return;
  if (!sessions.length) { container.innerHTML = '<div class="text-center py-10" style="color:var(--outline)"><i class="bi bi-calendar2-x text-3xl block mb-2"></i><p class="text-sm">생성된 세션이 없습니다.</p></div>'; return; }
  container.innerHTML = '';
  sessions.forEach(s => {
    const isActive = s.status === 'active';
    const sched = s.notification_schedule || {};
    const hasSchedule = sched.enabled && (sched.days_before || []).length > 0;
    const schedBadge = hasSchedule
      ? `<span class="sched-badge inline-flex items-center gap-1 text-xs font-bold px-2 py-0.5 rounded-full"><i class="bi bi-clock-fill"></i>Auto D-${(sched.days_before || []).join('/')}</span>`
      : '';
    _sessionSchedData[s.id] = s.notification_schedule || {};
    const sid = _escAttr(s.id), slbl = _escAttr(s.label);
    const notifyBtn = `<button onclick="openNotifyModal('${sid}','${slbl}','${_escAttr(s.end_date)}',_sessionSchedData['${sid}'])" class="btn-secondary text-xs px-3 py-1.5 rounded-lg flex items-center gap-1" title="알림 발송"><i class="bi bi-bell-fill"></i></button>`;
    const editDatesBtn = `<button onclick="openEditSessionDatesModal('${sid}')" class="btn-secondary text-xs px-3 py-1.5 rounded-lg" title="기간 수정"><i class="bi bi-calendar-event"></i></button>`;

    // Passcode 뱃지 + 관리 버튼
    const passcodeEnabled = !!s.passcode_enabled;
    const passcodeBadge = passcodeEnabled
      ? `<span class="passcode-badge inline-flex items-center gap-1 text-xs font-bold px-2 py-0.5 rounded-full" title="Passcode 보호됨 v${s.passcode_version||1}"><i class="bi bi-lock-fill"></i>Passcode</span>`
      : '';
    const passcodeBtn = passcodeEnabled
      ? `<button onclick="regenerateSessionPasscode('${sid}','${slbl}')" class="btn-secondary text-xs px-3 py-1.5 rounded-lg" title="Regenerate passcode&#10;기존 세션 즉시 무효화" style="color:var(--primary-dark);border-color:var(--primary-soft)"><i class="bi bi-arrow-repeat"></i></button>
         <button onclick="removeSessionPasscode('${sid}','${slbl}')" class="btn-secondary text-xs px-3 py-1.5 rounded-lg" title="Remove passcode&#10;공개 세션으로 전환" style="color:var(--outline);border-color:var(--outline-variant)"><i class="bi bi-unlock-fill"></i></button>`
      : `<button onclick="setSessionPasscode('${sid}','${slbl}')" class="btn-secondary text-xs px-3 py-1.5 rounded-lg" title="Set passcode" style="color:var(--on-surface-variant);border-color:var(--outline-variant)"><i class="bi bi-key"></i></button>`;

    const row = document.createElement('div'); row.className = `session-row${isActive ? ' active-session' : ''}`;
    row.innerHTML = `<div class="flex items-center gap-3"><div class="w-10 h-10 rounded-xl flex items-center justify-center flex-shrink-0 ${isActive ? 'bg-emerald-100' : ''}" style="${!isActive ? 'background:var(--surface-low)' : ''}"><i class="bi bi-calendar2${isActive ? '-check' : ''}-fill text-lg ${isActive ? 'text-emerald-600' : ''}" style="${!isActive ? 'color:var(--outline)' : ''}"></i></div><div><div class="flex items-center gap-2 flex-wrap"><span class="font-extrabold text-sm" style="color:var(--on-surface)">${slbl}</span><span class="${isActive ? 'session-badge-active' : 'session-badge-closed'}">${isActive ? '● 진행중' : '■ 마감'}</span>${schedBadge}${passcodeBadge}</div><p class="text-xs mt-0.5" style="color:var(--outline)">${_escAttr(s.start_date)} ~ ${_escAttr(s.end_date)}${s.closed_at ? ` · 마감일: ${_escAttr(s.closed_at)}` : ''}</p></div></div><div class="flex items-center gap-2 flex-wrap"><button onclick="filterBySession('${sid}')" class="btn-secondary text-xs px-3 py-1.5 rounded-lg"><i class="bi bi-bar-chart-fill mr-1"></i>현황 보기</button>${notifyBtn}${editDatesBtn}${passcodeBtn}${isActive ? `<button onclick="closeSession('${sid}','${slbl}')" class="btn-danger text-xs px-3 py-1.5 rounded-lg"><i class="bi bi-stop-circle-fill mr-1"></i>세션 마감</button>` : `<button onclick="reopenSession('${sid}','${slbl}')" class="reopen-btn text-xs font-bold px-3 py-1.5 transition-colors"><i class="bi bi-play-circle-fill mr-1"></i>재개</button>`}<button onclick="trashSessionReports('${sid}','${slbl}')" class="btn-secondary trash-purple-btn text-xs px-3 py-1.5 rounded-lg" title="이 세션의 모든 보고서를 Drive 휴지통으로 이동"><i class="bi bi-file-earmark-x-fill"></i></button><button onclick="deleteSession('${sid}','${slbl}',${s.response_count || 0})" class="btn-secondary text-xs px-3 py-1.5 rounded-lg" title="세션 삭제" style="color:var(--error);border-color:rgba(220,38,38,.30)"><i class="bi bi-trash3-fill"></i></button></div>`;
    container.appendChild(row);
  });
}

function openCreateSessionModal() {
  document.getElementById('sessionLabelInput').value = '';
  document.getElementById('sessionStartInput').value = '';
  document.getElementById('sessionEndInput').value = '';
  const pcChk = document.getElementById('sessionRequirePasscode');
  const pcInput = document.getElementById('sessionPasscodeInput');
  if (pcChk) pcChk.checked = false;
  if (pcInput) pcInput.value = '';
  togglePasscodeSection();
  document.getElementById('createSessionModal').style.display = 'flex';
}

function togglePasscodeSection() {
  const chk = document.getElementById('sessionRequirePasscode');
  const sec = document.getElementById('passcodeSection');
  if (!chk || !sec) return;
  sec.style.display = chk.checked ? '' : 'none';
  if (chk.checked) {
    // 편의: 체크하면 자동 생성 값을 미리 채움 (원하면 admin 이 수동 교체)
    const input = document.getElementById('sessionPasscodeInput');
    if (input && !input.value) input.value = _generatePasscodeClient();
  }
}

// 클라이언트 Auto-generate — 서버 규칙(혼동문자 제거 알파벳) 과 동일.
const _PASSCODE_ALPHABET = 'ABCDEFGHJKMNPQRSTUVWXYZ23456789';
function _generatePasscodeClient(len = 8) {
  const arr = new Uint32Array(len);
  (window.crypto || window.msCrypto).getRandomValues(arr);
  let out = '';
  for (let i = 0; i < len; i++) out += _PASSCODE_ALPHABET[arr[i] % _PASSCODE_ALPHABET.length];
  return out;
}

function autoGeneratePasscode() {
  const input = document.getElementById('sessionPasscodeInput');
  if (input) input.value = _generatePasscodeClient();
}

async function createSession() {
  const label = document.getElementById('sessionLabelInput').value.trim();
  const start = document.getElementById('sessionStartInput').value;
  const end = document.getElementById('sessionEndInput').value;
  if (!label || !start || !end) { showToast('모든 항목을 입력해 주세요.', 'error'); return; }
  if (label.includes('/')) { showToast('세션 이름에 "/"를 사용할 수 없습니다.', 'error'); return; }
  if (start > end) { showToast('종료일이 시작일보다 앞설 수 없습니다.', 'error'); return; }

  const pcChk = document.getElementById('sessionRequirePasscode');
  const pcInput = document.getElementById('sessionPasscodeInput');
  const requirePasscode = !!(pcChk && pcChk.checked);
  let passcodePlain = '';
  if (requirePasscode) {
    passcodePlain = (pcInput && pcInput.value || '').trim();
    if (passcodePlain.length < 4) { showToast('Passcode 는 최소 4자 이상이어야 합니다.', 'error'); return; }
    if (passcodePlain.length > 64) { showToast('Passcode 가 너무 깁니다.', 'error'); return; }
  }

  const body = { label, startDate: start, endDate: end };
  if (requirePasscode) body.passcode = passcodePlain;

  const res = await fetch('/api/v2/create-session', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }).then(r => r.json());
  if (res.status === 'SUCCESS') {
    closeModal('createSessionModal');
    showToast(`세션 "${label}"이(가) 생성되었습니다.`);
    if (requirePasscode && passcodePlain) {
      // 저장 성공 후 평문을 한 번만 표시 (서버엔 이미 hash 만 남음).
      showPasscodeRevealModal(label, passcodePlain);
    }
    loadSessions();
  } else {
    showToast(res.message || '세션 생성에 실패했습니다.', 'error');
  }
}

// ── Passcode 관리 (set/regenerate/remove) ─────────────────────────────────
let _passcodeRevealValue = '';  // 복사용 임시 저장. 닫히면 비움.
function showPasscodeRevealModal(sessionLabel, plain) {
  document.getElementById('passcodeRevealSessionLabel').textContent = sessionLabel;
  document.getElementById('passcodeRevealValue').textContent = plain;
  _passcodeRevealValue = plain;
  document.getElementById('passcodeRevealModal').style.display = 'flex';
}

// 모달 close 전용 함수 — 평문 잔여값을 즉시 지우고 DOM 에서도 제거.
// admin.html 의 close 버튼과 backdrop 에서 closeModal('passcodeRevealModal') 대신
// 이 함수를 호출하도록 연결되어 있음.
function closePasscodeRevealModal() {
  _passcodeRevealValue = '';
  const el = document.getElementById('passcodeRevealValue');
  if (el) el.textContent = '—';
  const modal = document.getElementById('passcodeRevealModal');
  if (modal) modal.style.display = 'none';
}

async function copyRevealedPasscode() {
  if (!_passcodeRevealValue) return;
  try {
    await navigator.clipboard.writeText(_passcodeRevealValue);
    showToast('Passcode 가 클립보드에 복사되었습니다.', 'success');
  } catch (e) {
    showToast('복사 실패 — 수동으로 선택해 복사해 주세요.', 'error');
  }
}

async function setSessionPasscode(sessionId, label) {
  const ok = await showCustomPrompt(
    'Passcode 설정',
    `세션 "${label}"에 8자 passcode 를 자동 생성합니다.\n\n저장 직후 평문이 1회 표시되니 안전한 곳에 보관하세요. 계속할까요?`,
    false, false, '설정'
  );
  if (!ok) return;
  const plain = _generatePasscodeClient();
  const res = await fetch('/api/v2/session/passcode', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ sessionId, action: 'set', passcode: plain })
  }).then(r => r.json());
  if (res.status === 'SUCCESS') {
    showToast('Passcode 가 설정되었습니다.', 'success');
    showPasscodeRevealModal(label, plain);
    loadSessions();
  } else showToast(res.message || 'Passcode 설정에 실패했습니다.', 'error');
}

async function regenerateSessionPasscode(sessionId, label) {
  const ok = await showCustomPrompt(
    'Passcode 재발급',
    `세션 "${label}"의 passcode 를 재발급합니다.\n\n⚠ 기존 passcode 로 진입한 평가자는 즉시 로그아웃되며, 새 passcode 입력이 필요합니다.\n재발급 직후 새 평문이 1회 표시됩니다. 계속할까요?`,
    false, true, '재발급'
  );
  if (!ok) return;
  const plain = _generatePasscodeClient();
  const res = await fetch('/api/v2/session/passcode', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ sessionId, action: 'regenerate', passcode: plain })
  }).then(r => r.json());
  if (res.status === 'SUCCESS') {
    showToast('Passcode 가 재발급되었습니다.', 'success');
    showPasscodeRevealModal(label, plain);
    loadSessions();
  } else showToast(res.message || 'Passcode 재발급에 실패했습니다.', 'error');
}

async function removeSessionPasscode(sessionId, label) {
  const ok = await showCustomPrompt(
    'Passcode 제거',
    `세션 "${label}"의 passcode 를 제거합니다.\n\n이후 이 세션은 passcode 없이 누구나 사번으로 접근할 수 있습니다. 계속할까요?`,
    false, true, '제거'
  );
  if (!ok) return;
  const res = await fetch('/api/v2/session/passcode', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ sessionId, action: 'remove' })
  }).then(r => r.json());
  if (res.status === 'SUCCESS') {
    showToast('Passcode 가 제거되었습니다.', 'success');
    loadSessions();
  } else showToast(res.message || 'Passcode 제거에 실패했습니다.', 'error');
}

// ── 세션 기간 수정 (start_date / end_date) ─────────────────────────────────
function openEditSessionDatesModal(sessionId) {
  const s = (allSessions || []).find(x => x.id === sessionId);
  if (!s) { showToast('세션을 찾을 수 없습니다.', 'error'); return; }
  document.getElementById('editDatesSessionLabel').textContent = s.label || sessionId;
  // datepicker UI 와 hidden input 동기화 보장 — 직접 .value 설정 대신 setValue API
  DYBDatepicker.setValue('editDatesStartInput', s.start_date || '');
  DYBDatepicker.setValue('editDatesEndInput', s.end_date || '');
  window._editingSessionId = sessionId;
  document.getElementById('editSessionDatesModal').style.display = 'flex';
}

async function submitEditSessionDates() {
  const sessionId = window._editingSessionId;
  if (!sessionId) { showToast('세션이 선택되지 않았습니다.', 'error'); return; }
  const start = DYBDatepicker.getValue('editDatesStartInput');
  const end = DYBDatepicker.getValue('editDatesEndInput');
  if (!start || !end) { showToast('두 날짜를 모두 입력해 주세요.', 'error'); return; }
  if (start > end) { showToast('종료일이 시작일보다 앞설 수 없습니다.', 'error'); return; }

  const btn = document.getElementById('editDatesSaveBtn');
  const origHtml = btn ? btn.innerHTML : '';
  if (btn) {
    btn.innerHTML = '<div class="w-3.5 h-3.5 rounded-full animate-spin flex-shrink-0" style="border:2px solid var(--outline-variant);border-top-color:var(--primary);"></div> 저장 중...';
    btn.disabled = true;
  }
  try {
    const res = await fetch('/api/v2/session/dates', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
      body: JSON.stringify({ sessionId, startDate: start, endDate: end })
    }).then(r => r.json());
    if (res.status !== 'SUCCESS') {
      showToast(res.message || '기간 수정에 실패했습니다.', 'error');
      return;
    }
    closeModal('editSessionDatesModal');
    showToast('기간이 수정되었습니다.', 'success');
    loadSessions();
  } catch (e) {
    console.error('submitEditSessionDates error:', e);
    showToast('네트워크 오류가 발생했습니다.', 'error');
  } finally {
    if (btn) { btn.innerHTML = origHtml; btn.disabled = false; }
  }
}

async function closeSession(sessionId, label) {
  const ok = await showCustomPrompt('세션 마감', `"${label}"을(를) 마감하시겠습니까? 이후 새 제출이 차단됩니다.`, false, true, '마감');
  if (!ok) return;
  const res = await fetch('/api/v2/close-session', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ sessionId }) }).then(r => r.json());
  if (res.status === 'SUCCESS') { showToast('세션이 마감되었습니다.'); loadSessions(); statusLoaded = false; }
  else showToast(res.message || '세션 마감에 실패했습니다.', 'error');
}

async function reopenSession(sessionId, label) {
  const ok = await showCustomPrompt('세션 재개', `"${label}"을(를) 재개하시겠습니까? 다시 제출을 받을 수 있습니다.`, false, false, '재개');
  if (!ok) return;
  const res = await fetch('/api/v2/reopen-session', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ sessionId }) }).then(r => r.json());
  if (res.status === 'SUCCESS') { showToast(`세션 "${label}"이(가) 재개되었습니다.`); loadSessions(); statusLoaded = false; }
  else showToast(res.message || '세션 재개에 실패했습니다.', 'error');
}

async function deleteSession(sessionId, label, responseCount) {
  let deleteResponses = false;
  if (responseCount > 0) {
    // 3-버튼: 취소 / 응답 보존 (세션만 삭제) / 모두 삭제
    const choice = await showCustomPrompt(
      '세션 삭제',
      `세션 "${label}"에 응답 ${responseCount}건이 연결되어 있습니다. 어떻게 처리할까요?`,
      false, true, '응답까지 모두 삭제', '세션만 삭제 (응답 보존)'
    );
    if (choice === null) return;            // 취소
    deleteResponses = (choice === true);    // confirm = 모두 삭제 / 'secondary' = 세션만
  } else {
    const ok = await showCustomPrompt('세션 삭제', `세션 "${label}"을(를) 삭제하시겠습니까? 이 작업은 되돌릴 수 없습니다.`, false, true, '삭제');
    if (!ok) return;
  }
  const res = await fetch('/api/v2/delete-session', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ sessionId, deleteResponses }) }).then(r => r.json());
  if (res.status === 'SUCCESS') {
    const msg = deleteResponses ? `세션 및 응답 ${res.deletedResponses}건이 삭제되었습니다.` : `세션 "${label}"이(가) 삭제되었습니다.`;
    showToast(msg, 'success'); loadSessions(); statusLoaded = false;
    if (currentSessionFilter === sessionId) { currentSessionFilter = ''; loadStatus(); }
  } else showToast(res.message || '세션 삭제에 실패했습니다.', 'error');
}

function onStatusSessionChange(sessionId) { currentSessionFilter = sessionId; statusLoaded = false; loadStatus(); }
function filterBySession(sessionId) {
  window.location.href = '/eval-v2/status?session=' + encodeURIComponent(sessionId);
}
function clearSessionFilter() { currentSessionFilter = ''; statusLoaded = false; const sel = document.getElementById('statusSessionSelect'); if (sel) sel.value = ''; loadStatus(); }

async function deleteAllTestResponses() {
  const ok = await showCustomPrompt('테스트 응답 삭제', `테스트로 표시된 응답${activeSession ? ` (세션: "${activeSession.label}")` : ''}을 모두 삭제하시겠습니까? 이 작업은 되돌릴 수 없습니다.`, false, true, '모두 삭제');
  if (!ok) return;
  const res = await fetch('/api/v2/delete-test-responses', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ sessionId: currentSessionFilter }) }).then(r => r.json());
  if (res.status === 'SUCCESS') { showToast(`테스트 응답 ${res.deletedCount}건이 삭제되었습니다.`); statusLoaded = false; }
  else showToast(res.message || '삭제에 실패했습니다.', 'error');
}

async function trashSessionReports(sessionId, label) {
  const ok = await showCustomPrompt(
    '세션 보고서 휴지통 이동',
    `세션 "${label}"의 모든 Drive 보고서를 휴지통으로 이동하시겠습니까?\n\nPDF 파일에만 영향을 미치며, 응답 데이터는 삭제되지 않습니다.`,
    false, true, '휴지통으로 이동'
  );
  if (!ok) return;

  // 1. 파일 목록 조회 (read-only)
  let files = [];
  try {
    const res = await fetch('/api/v2/list-session-reports', {
      method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
      body: JSON.stringify({ sessionId })
    }).then(r => r.json());
    if (res.status !== 'SUCCESS') { showToast(res.message || '목록 조회에 실패했습니다.', 'error'); return; }
    files = res.files || [];
  } catch (e) { showToast('오류가 발생했습니다.', 'error'); return; }

  if (files.length === 0) {
    showToast('이 세션의 보고서 파일이 없습니다.', 'info');
    return;
  }

  // 2. 진행률 모달 열기
  const modal = document.getElementById('trashProgressModal');
  const labelEl = document.getElementById('trashProgressLabel');
  const fillEl = document.getElementById('trashProgressFill');
  const countEl = document.getElementById('trashProgressCount');
  labelEl.textContent = `세션 "${label}"의 보고서 ${files.length}건 이동 중...`;
  fillEl.style.width = '0%';
  countEl.textContent = `0 / ${files.length}`;
  modal.style.display = 'flex';

  // 3. 순차 trash + 진행률 갱신
  let success = 0, failed = 0;
  for (let i = 0; i < files.length; i++) {
    try {
      const res = await fetch('/api/v2/trash-file-by-id', {
        method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
        body: JSON.stringify({ fileId: files[i].id })
      }).then(r => r.json());
      if (res.status === 'SUCCESS') success++; else failed++;
    } catch (e) { failed++; }
    const done = i + 1;
    fillEl.style.width = `${(done / files.length) * 100}%`;
    countEl.textContent = `${done} / ${files.length}`;
  }

  // 4. 자동 닫기 + toast
  await new Promise(r => setTimeout(r, 500));
  modal.style.display = 'none';
  if (failed === 0) showToast(`보고서 ${success}건이 Drive 휴지통으로 이동되었습니다.`, 'success');
  else if (success === 0) showToast(`이동에 실패했습니다 (${failed}건).`, 'error');
  else showToast(`성공 ${success}건, 실패 ${failed}건.`, 'warning');
}

// ── Bulk Report ──
function toggleBulkMode() {
  bulkMode = !bulkMode; bulkSelected.clear(); _allSelected = false;
  document.getElementById('bulkActionBar').classList.toggle('open', bulkMode);
  // DOM 재렌더 없이 CSS 클래스만 토글 — 체크박스는 항상 DOM에 존재하고 CSS로 표시/숨김
  document.getElementById('statusContent').classList.toggle('bulk-mode', bulkMode);
  document.getElementById('typeAccordionWrap').classList.toggle('bulk-mode', bulkMode);
  // 닫을 때 모든 체크박스 상태 초기화
  if (!bulkMode) {
    document.querySelectorAll('.bulk-checkbox').forEach(cb => { cb.checked = false; });
  }
  const btn = document.getElementById('bulkModeBtn');
  if (btn) {
    btn.querySelector('span.flex').innerHTML = bulkMode
      ? `<i class="bi bi-x-lg text-sm"></i> <span class="text-sm font-bold">닫기</span>`
      : `<i class="bi bi-check2-square text-sm"></i> <span class="text-sm font-bold">일괄 보고서</span>`;
    btn.style.background = bulkMode ? 'var(--primary)' : '';
    btn.style.borderColor = bulkMode ? 'var(--primary)' : '';
  }
  updateBulkCount();
}

function toggleBulkSelect(empId) { if (bulkSelected.has(empId)) bulkSelected.delete(empId); else bulkSelected.add(empId); updateBulkCount(); }

function updateBulkCount() {
  document.getElementById('bulkSelectedCount').textContent = `${bulkSelected.size}명 선택`;
  const empty = bulkSelected.size === 0;
  const gen = document.getElementById('bulkGenerateBtn'); if (gen) gen.disabled = empty;
  const trash = document.getElementById('bulkTrashBtn'); if (trash) trash.disabled = empty;
}

function syncCheckboxes() {
  document.querySelectorAll('.bulk-checkbox').forEach(cb => {
    const id = cb.dataset.empid || cb.getAttribute('onclick')?.match(/'([^']+)'\)/)?.[1];
    if (id) cb.checked = bulkSelected.has(id);
  });
  updateBulkCount();
}
function selectAllBulk() { allTeachersFlat.forEach(t => bulkSelected.add(t.id)); syncCheckboxes(); }
function deselectAllBulk() { bulkSelected.clear(); syncCheckboxes(); }

function toggleSelectAll() {
  const btn = document.getElementById('toggleSelectBtn');
  if (_allSelected) {
    deselectAllBulk(); _allSelected = false;
    btn.innerHTML = '<i class="bi bi-check2-all"></i> 전체 선택';
    btn.style.cssText = 'border:1.5px solid var(--outline-variant);border-radius:3px;color:var(--text);background:var(--surface-lowest)';
  } else {
    selectAllBulk(); _allSelected = true;
    btn.innerHTML = '<i class="bi bi-x-square"></i> 전체 해제';
    btn.style.cssText = 'border:1.5px solid var(--primary-dark);border-radius:3px;color:var(--primary-dark);background:var(--primary-soft)';
  }
}

function toggleCampusBulk(campusTeacherIds, btn) {
  const allSelected = campusTeacherIds.every(id => bulkSelected.has(id));
  if (allSelected) campusTeacherIds.forEach(id => bulkSelected.delete(id));
  else campusTeacherIds.forEach(id => bulkSelected.add(id));
  syncCheckboxes();
  if (btn) {
    if (!allSelected) { btn.innerHTML = '<i class="bi bi-check2-square"></i> 전체 해제'; btn.style.cssText = 'border:1.5px solid var(--primary-dark);border-radius:3px;color:var(--primary-dark);background:var(--primary-soft)'; }
    else { btn.innerHTML = '<i class="bi bi-check2-square"></i> 전체 선택'; btn.style.cssText = 'border:1.5px solid var(--outline-variant);border-radius:3px;color:var(--text);background:var(--surface-lowest)'; }
  }
}

async function generateBulkReports() {
  if (!bulkSelected.size) { showToast('선생님을 한 명 이상 선택해 주세요.', 'error'); return; }
  if (!currentSessionFilter) { showToast('세션을 먼저 선택해 주세요.', 'error'); return; }
  const typeGroups = {};
  Array.from(bulkSelected).forEach(id => {
    const t = allTeachersFlat.find(t => t.id === id); if (!t) return;
    if (!typeGroups[t.type]) typeGroups[t.type] = [];
    typeGroups[t.type].push(id);
  });
  const typeList = Object.keys(typeGroups);
  const mixedMsg = typeList.length > 1 ? `\n\n※ 평가 유형 ${typeList.length}종 감지됨 (${typeList.join(', ')}). 유형별로 처리됩니다.` : '';
  const ok = await showCustomPrompt('일괄 보고서 생성', `세션 "${currentSessionFilter}"에서 선생님 ${bulkSelected.size}명의 보고서를 생성하시겠습니까? 시간이 걸릴 수 있습니다.${mixedMsg}`, false, true, '생성');
  if (!ok) return;
  const btn = document.getElementById('bulkGenerateBtn'); btn.disabled = true;
  btn.innerHTML = '<span class="flex items-center gap-1.5"><i class="bi bi-file-earmark-pdf-fill text-sm"></i> <span class="text-sm font-bold">생성 중...</span></span>';
  const beforeUnloadHandler = (e) => { e.preventDefault(); e.returnValue = ''; };
  window.addEventListener('beforeunload', beforeUnloadHandler);

  const total = bulkSelected.size;
  const pm = ProgressModal.open({
    title: 'Generating session reports',
    subtitle: `세션 "${currentSessionFilter}" — ${total}명의 보고서 생성 중...`,
    total,
  });

  const restoreBtn = () => {
    btn.disabled = bulkSelected.size === 0;
    btn.innerHTML = '<span class="flex items-center gap-1.5"><i class="bi bi-file-earmark-pdf-fill text-sm"></i> <span class="text-sm font-bold">생성</span></span><span class="text-[10px] opacity-70 hidden sm:block">생성</span>';
    window.removeEventListener('beforeunload', beforeUnloadHandler);
  };

  const CHUNK = 1;  // 평가자 1명 단위 게이지 갱신 — 부드러운 진행률
  let totalSuccess = 0, totalSkip = 0, totalError = 0, chunkErrors = 0, processed = 0;
  outer: for (const evalType of typeList) {
    const ids = typeGroups[evalType];
    for (let i = 0; i < ids.length; i += CHUNK) {
      if (pm.cancelled) break outer;
      const chunk = ids.slice(i, i + CHUNK);
      try {
        const res = await fetch('/api/v2/bulk-generate-reports', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
          body: JSON.stringify({ empIds: chunk, sessionId: currentSessionFilter, evalType }),
          signal: pm.signal,
        }).then(r => r.json());
        if (res.status === 'SUCCESS') {
          totalSuccess += res.summary.success;
          totalSkip    += res.summary.skip;
          totalError   += res.summary.error;
        } else {
          totalError += chunk.length;
          chunkErrors++;
          console.error(`Chunk (${evalType}) failed:`, res.message);
        }
      } catch (e) {
        if (e && e.name === 'AbortError') break outer;   // 사용자 취소 — 즉시 중단
        totalError += chunk.length;
        chunkErrors++;
        console.error(`Chunk (${evalType}) network error:`, e);
      }
      processed += chunk.length;
      pm.update(processed, {
        success: totalSuccess,
        skip: totalSkip,
        error: totalError,
        currentLabel: `${evalType}`,
      });
    }
  }

  const allDone = totalError === 0 && chunkErrors === 0 && !pm.cancelled;
  const summaryParts = [];
  if (totalSuccess > 0) summaryParts.push(`${totalSuccess} 저장`);
  if (totalSkip    > 0) summaryParts.push(`${totalSkip} 건너뜀`);
  if (totalError   > 0) summaryParts.push(`${totalError} 실패`);
  if (pm.cancelled)     summaryParts.push('cancelled');
  pm.done({ success: allDone, summary: summaryParts.join(' · ') || 'No changes', autoCloseMs: 1500 });

  const toastType = chunkErrors > 0 && totalSuccess === 0 ? 'error' : chunkErrors > 0 ? 'error' : 'success';
  showToast(`완료: 저장 ${totalSuccess}건, 건너뜀 ${totalSkip}건, 실패 ${totalError}건 (전체 ${total}건)`, toastType);
  restoreBtn();
  if (totalSuccess > 0) toggleBulkMode();
}

// ── Bulk Trash Reports ──
async function bulkTrashSelectedReports() {
  if (!currentSessionFilter) { showToast('세션을 먼저 선택하세요.', 'error'); return; }
  if (bulkSelected.size === 0) return;

  const ids = Array.from(bulkSelected);
  const total = ids.length;
  // 사용자 sanity check 위해 처음 5명 + 나머지 카운트 표시 — 잘못 선택 시 cancel 가능
  const sample = ids.slice(0, 5).map(id => id.toUpperCase()).join(', ');
  const more = ids.length > 5 ? ` 외 ${ids.length - 5}명` : '';
  const ok = await showCustomPrompt(
    '선택 보고서 일괄 휴지통 이동',
    `선생님 ${total}명의 Drive 보고서를 휴지통으로 이동하시겠습니까?\n\n` +
    `대상: ${sample}${more}\n\n` +
    `PDF 파일에만 영향을 미치며, 응답 데이터는 삭제되지 않습니다.\n` +
    `보고서가 없는 선생님은 자동 skip됩니다.`,
    false, true, '휴지통으로 이동'
  );
  if (!ok) return;

  // BulkRunner — chunkSize=10 으로 N×3 → N×1 + HTTP round-trip 50 → 5
  const result = await BulkRunner.run({
    items: ids,
    chunkSize: 10,
    title: 'Trashing reports',
    subtitle: `세션 "${currentSessionFilter}" — ${total}명의 보고서 이동 중...`,
    url: '/api/v2/bulk-trash-reports',
    bodyKey: 'empIds',
    extraBody: { sessionId: currentSessionFilter },
    tallyFn: (res, chunk) => {
      // 서버 응답 schema: {summary: {success, skip, error, total}, results: [...]}
      if (!res || res.status !== 'SUCCESS') {
        return { error: chunk.length };
      }
      const s = res.summary || {};
      return {
        success: s.success || 0,
        skip:    s.skip    || 0,
        error:   s.error   || 0,
        currentLabel: (chunk[chunk.length - 1] || '').toString().toUpperCase(),
      };
    },
  });

  const toastType = result.error > 0 ? (result.success > 0 ? 'warning' : 'error') : 'success';
  showToast(
    `완료: 이동 ${result.success}건, 건너뜀 ${result.skip}건, 실패 ${result.error}건 (전체 ${total}건)`,
    toastType
  );
  if (result.success > 0) toggleBulkMode();
}

// ── 평가 마감 알림 모달 ────────────────────────────────────────────────────────

let _notifySessionId = '';
let _notifyScheduleDays = [3, 1];  // 기본 D-3, D-1

// 기본 이메일 템플릿
const NOTIFY_DEFAULT_SUBJECT_EN = '[NHR] Evaluation Reminder — {session} ends {deadline}';
const NOTIFY_DEFAULT_BODY_EN =
`Dear {name},

This is a friendly reminder that the evaluation period for "{session}" will close on {deadline}.

Please log in to the NHR Portal and complete any outstanding evaluations before the deadline.

Thank you for your participation.

NHR Team`;

const NOTIFY_DEFAULT_SUBJECT_KO = '[NHR] 평가 마감 안내 — {session} ({deadline})';
const NOTIFY_DEFAULT_BODY_KO =
`{name} 선생님께,

"{session}" 평가 마감일은 {deadline}입니다.

마감 전에 NHR 포털에 로그인하시어 미완료 평가를 완료해 주시기 바랍니다.

감사합니다.

NHR 팀`;

function openNotifyModal(sessionId, label, endDate, schedule) {
  _notifySessionId = sessionId;
  document.getElementById('notifySessionLabel').textContent = label;
  document.getElementById('notifySessionDeadline').textContent = endDate || '—';

  // 캠퍼스 필터 초기화
  document.getElementById('notifyCampusFilter').value = schedule.campus_filter || '';
  document.getElementById('notifyIncompleteOnly').checked = !!schedule.incomplete_only;

  // 언어 선택 복원
  const lang = schedule.lang || 'en';
  document.querySelector(`input[name="notifyLang"][value="${lang}"]`).checked = true;

  // 이메일 내용 복원 (저장된 값 또는 기본 템플릿)
  document.getElementById('notifySubjectEn').value = schedule.subject_en || NOTIFY_DEFAULT_SUBJECT_EN;
  document.getElementById('notifyBodyEn').value    = schedule.body_en    || NOTIFY_DEFAULT_BODY_EN;
  document.getElementById('notifySubjectKo').value = schedule.subject_ko || NOTIFY_DEFAULT_SUBJECT_KO;
  document.getElementById('notifyBodyKo').value    = schedule.body_ko    || NOTIFY_DEFAULT_BODY_KO;

  // 스케줄 복원
  _notifyScheduleDays = [...(schedule.days_before || [3, 1])];
  document.getElementById('notifyScheduleEnabled').checked = !!schedule.enabled;
  toggleNotifySchedule();
  renderNotifyScheduleDays();

  // 결과 영역 초기화
  document.getElementById('notifyPreviewArea').style.display = 'none';
  document.getElementById('notifySendResultArea').style.display = 'none';

  updateNotifyLangView();
  document.getElementById('notifyModal').style.display = 'flex';
}

function updateNotifyLangView() {
  const lang = document.querySelector('input[name="notifyLang"]:checked')?.value || 'en';
  document.getElementById('notifyEnSection').style.display = (lang === 'ko') ? 'none' : '';
  document.getElementById('notifyKoSection').style.display = (lang === 'en') ? 'none' : '';
}

function toggleNotifySchedule() {
  const enabled = document.getElementById('notifyScheduleEnabled').checked;
  document.getElementById('notifyScheduleBody').style.display = enabled ? '' : 'none';
}

function renderNotifyScheduleDays() {
  const container = document.getElementById('notifyScheduleDaysList');
  container.innerHTML = '';
  _notifyScheduleDays.forEach((d, i) => {
    const row = document.createElement('div');
    row.className = 'flex items-center gap-2';
    row.innerHTML = `<span class="text-sm font-bold" style="color:var(--on-surface-variant)">D-</span>
      <input type="number" min="1" max="30" value="${d}" onchange="_notifyScheduleDays[${i}]=parseInt(this.value)||1"
        class="w-20 border-2 rounded-lg px-2 py-1.5 text-sm text-center font-mono focus:outline-none"
        style="border-color:var(--outline-variant);background:var(--surface-lowest)">
      <span class="text-sm" style="color:var(--outline)">days before deadline</span>
      <button onclick="_notifyScheduleDays.splice(${i},1);renderNotifyScheduleDays()"
        class="w-6 h-6 flex items-center justify-center rounded-md text-xs transition-colors"
        style="background:var(--surface-low);color:var(--error)"><i class="bi bi-x-lg"></i></button>`;
    container.appendChild(row);
  });
}

function addNotifyScheduleDay() {
  _notifyScheduleDays.push(1);
  renderNotifyScheduleDays();
}

async function previewNotifyRecipients() {
  const btn = document.getElementById('notifyPreviewBtn');
  const origHtml = btn.innerHTML;
  btn.innerHTML = '<div class="w-4 h-4 rounded-full animate-spin flex-shrink-0" style="border:2px solid var(--outline-variant);border-top-color:var(--primary);"></div> 불러오는 중...';
  btn.disabled = true;
  try {
    const res = await fetch('/api/v2/preview-notification-recipients', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        sessionId: _notifySessionId,
        campusFilter: document.getElementById('notifyCampusFilter').value,
        incompleteOnly: document.getElementById('notifyIncompleteOnly').checked,
      })
    }).then(r => r.json());
    if (res.status !== 'SUCCESS') { showToast(res.message || '미리보기 불러오기에 실패했습니다.', 'error'); return; }
    renderNotifyPreview(res.with_email, res.no_email);
  } finally {
    btn.innerHTML = origHtml; btn.disabled = false;
  }
}

function renderNotifyPreview(withEmail, noEmail) {
  const area = document.getElementById('notifyPreviewArea');
  const body = document.getElementById('notifyPreviewBody');
  const count = document.getElementById('notifyPreviewCount');
  area.style.display = '';
  count.textContent = `${withEmail.length}명 발송 예정 · ${noEmail.length}명 이메일 없음`;
  body.innerHTML = '';
  // t.* 는 Google Sheets 유래 값 (HR 이 편집) — shared-trust 지만
  // 시트에 `<script>` 같은 페이로드가 섞여 들어오면 admin 세션 하이재킹 가능.
  // innerHTML 주입 지점마다 _escAttr() 로 방어.
  withEmail.forEach(t => {
    const el = document.createElement('div');
    el.className = 'flex items-center gap-2 py-1.5 border-b text-sm';
    el.style.borderColor = 'var(--outline-variant)';
    el.innerHTML = `<i class="bi bi-check-circle-fill text-emerald-500 flex-shrink-0"></i>
      <span class="font-bold" style="color:var(--on-surface)">${_escAttr(t.name)}</span>
      <span class="text-xs font-mono" style="color:var(--outline)">${_escAttr(t.email)}</span>
      <span class="ml-auto text-xs px-1.5 py-0.5 rounded font-bold" style="background:var(--surface-low);color:var(--outline)">${_escAttr(t.campus_en || t.campus)}</span>`;
    body.appendChild(el);
  });
  if (noEmail.length) {
    const sep = document.createElement('div');
    sep.className = 'text-xs font-bold pt-2 pb-1'; sep.style.color = 'var(--outline)';
    sep.innerHTML = `<i class="bi bi-exclamation-triangle-fill text-amber-500"></i> 이메일 미등록 (${noEmail.length}명)`;
    body.appendChild(sep);
    noEmail.forEach(t => {
      const el = document.createElement('div');
      el.className = 'flex items-center gap-2 py-1 text-sm opacity-60';
      el.innerHTML = `<i class="bi bi-exclamation-circle-fill text-amber-400 flex-shrink-0"></i>
        <span class="font-bold" style="color:var(--on-surface)">${_escAttr(t.name)}</span>
        <span class="text-xs" style="color:var(--outline)">${_escAttr(String(t.emp_id ?? '').toUpperCase())}</span>
        <span class="ml-auto text-xs px-1.5 py-0.5 rounded font-bold" style="background:var(--surface-low);color:var(--outline)">${_escAttr(t.campus_en || t.campus)}</span>`;
      body.appendChild(el);
    });
  }
  area.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

async function saveNotificationSchedule() {
  const btn = document.getElementById('notifyScheduleSaveBtn');
  const origHtml = btn.innerHTML;
  btn.innerHTML = '<div class="w-3.5 h-3.5 rounded-full animate-spin flex-shrink-0" style="border:2px solid var(--outline-variant);border-top-color:var(--primary);"></div> 저장 중...';
  btn.disabled = true;
  try {
    const lang = document.querySelector('input[name="notifyLang"]:checked')?.value || 'en';
    const existingSched = allSessions.find(s => s.id === _notifySessionId)?.notification_schedule || {};
    const res = await fetch('/api/v2/save-notification-schedule', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        sessionId:      _notifySessionId,
        enabled:        document.getElementById('notifyScheduleEnabled').checked,
        daysBefore:     _notifyScheduleDays,
        lang,
        campusFilter:   document.getElementById('notifyCampusFilter').value,
        incompleteOnly: document.getElementById('notifyIncompleteOnly').checked,
        subjectEn:      document.getElementById('notifySubjectEn').value,
        bodyEn:         document.getElementById('notifyBodyEn').value,
        subjectKo:      document.getElementById('notifySubjectKo').value,
        bodyKo:         document.getElementById('notifyBodyKo').value,
        sentMarkers:    existingSched.sent_markers || {},
      })
    }).then(r => r.json());
    if (res.status === 'SUCCESS') { showToast('스케줄이 저장되었습니다.'); loadSessions(); }
    else showToast(res.message || '저장에 실패했습니다.', 'error');
  } finally { btn.innerHTML = origHtml; btn.disabled = false; }
}

async function sendNotification() {
  const lang = document.querySelector('input[name="notifyLang"]:checked')?.value || 'en';
  const subjectEn = document.getElementById('notifySubjectEn').value.trim();
  const bodyEn    = document.getElementById('notifyBodyEn').value.trim();
  const subjectKo = document.getElementById('notifySubjectKo').value.trim();
  const bodyKo    = document.getElementById('notifyBodyKo').value.trim();
  if ((lang === 'en' || lang === 'both') && (!subjectEn || !bodyEn)) { showToast('영문 제목과 본문을 입력해 주세요.', 'error'); return; }
  if ((lang === 'ko' || lang === 'both') && (!subjectKo || !bodyKo)) { showToast('한국어 제목과 본문을 입력해주세요.', 'error'); return; }
  const ok = await showCustomPrompt('알림 발송', `이 세션의 평가 마감 안내 이메일을 발송하시겠습니까? 조건에 맞는 수신자 전원에게 즉시 발송됩니다.`, false, false, '발송');
  if (!ok) return;
  const btn = document.getElementById('notifySendBtn');
  const origHtml = btn.innerHTML;
  btn.innerHTML = '<div class="w-3.5 h-3.5 rounded-full animate-spin flex-shrink-0" style="border:2px solid rgba(37,99,235,.30);border-top-color:var(--info);"></div> 발송 중...';
  btn.disabled = true;
  try {
    const res = await fetch('/api/v2/send-notification', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        sessionId:      _notifySessionId,
        campusFilter:   document.getElementById('notifyCampusFilter').value,
        incompleteOnly: document.getElementById('notifyIncompleteOnly').checked,
        lang, subjectEn, bodyEn, subjectKo, bodyKo,
      })
    }).then(r => r.json());
    if (res.status !== 'SUCCESS') { showToast(res.message || '발송에 실패했습니다.', 'error'); return; }
    renderNotifySendResult(res.sent, res.no_email, res.failed);
    showToast(`발송: ${res.sent.length}명, 이메일 없음: ${res.no_email.length}명, 실패: ${res.failed.length}명`, res.failed.length > 0 ? 'error' : 'success');
  } finally { btn.innerHTML = origHtml; btn.disabled = false; }
}

function renderNotifySendResult(sent, noEmail, failed) {
  const area = document.getElementById('notifySendResultArea');
  const body = document.getElementById('notifySendResultBody');
  area.style.display = '';
  body.innerHTML = '';
  sent.forEach(t => {
    const el = document.createElement('div');
    el.className = 'flex items-center gap-2 py-1.5 border-b text-sm';
    el.style.borderColor = 'var(--outline-variant)';
    el.innerHTML = `<i class="bi bi-check2-circle text-emerald-500 flex-shrink-0"></i>
      <span class="font-bold" style="color:var(--on-surface)">${t.name}</span>
      <span class="text-xs font-mono" style="color:var(--outline)">${t.email}</span>
      <span class="ml-auto text-xs px-1.5 py-0.5 rounded font-bold" style="background:var(--surface-low);color:var(--outline)">${t.campus}</span>`;
    body.appendChild(el);
  });
  [...noEmail.map(t => ({...t, _type:'noemail'})), ...failed.map(t => ({...t, _type:'failed'}))].forEach(t => {
    const el = document.createElement('div');
    el.className = 'flex items-center gap-2 py-1.5 border-b text-sm opacity-70';
    el.style.borderColor = 'var(--outline-variant)';
    el.innerHTML = `<i class="bi bi-${t._type === 'noemail' ? 'exclamation-circle-fill text-amber-400' : 'x-circle-fill text-red-500'} flex-shrink-0"></i>
      <span class="font-bold" style="color:var(--on-surface)">${t.name}</span>
      <span class="text-xs" style="color:var(--outline)">${t._type === 'noemail' ? '이메일 미등록' : '발송 실패'}</span>
      <span class="ml-auto text-xs px-1.5 py-0.5 rounded font-bold" style="background:var(--surface-low);color:var(--outline)">${t.campus}</span>`;
    body.appendChild(el);
  });
  area.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

async function runScheduledNotificationCheck() {
  const btn = document.getElementById('schedCheckBtn');
  const origHtml = btn.innerHTML;
  btn.innerHTML = '<div class="w-3.5 h-3.5 rounded-full animate-spin flex-shrink-0" style="border:2px solid var(--outline-variant);border-top-color:var(--primary);"></div> 확인 중...';
  btn.disabled = true;
  try {
    const res = await fetch('/api/v2/check-scheduled-notifications', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}'
    }).then(r => r.json());
    if (res.status !== 'SUCCESS') { showToast(res.message || '확인에 실패했습니다.', 'error'); return; }
    if (!res.results.length) { showToast('오늘 발송 예정인 스케줄 알림이 없습니다.', 'info'); }
    else {
      const summary = res.results.map(r => `${r.session}: ${r.sent}건 발송`).join(', ');
      showToast(`스케줄 확인 완료 — ${summary}`, 'success');
    }
    loadSessions();
  } finally { btn.innerHTML = origHtml; btn.disabled = false; }
}
