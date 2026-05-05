// ── eval_v2 admin — TAB 2: 문항·가중치 설정 ──────────────────────────────────

// ── 평가자 헤더 카운트 helper (객관식·서술형 갯수) ──
function _renderRoleCounts(role) {
  const mc = (role?.questions || []).length;
  const oq = (role?.open_questions || []).length;
  return `${mc}문항${oq ? ` · 서술형 ${oq}` : ''}`;
}
function _refreshRoleCounts(rIdx) {
  const el = document.getElementById(`cfg-counts-${rIdx}`);
  if (!el || !currentCfgData) return;
  el.textContent = _renderRoleCounts(currentCfgData.roles[rIdx]);
}

function onCfgSessionChange(sessionId) {
  currentCfgSession = sessionId;
  const warning = document.getElementById('cfgSessionWarning');
  const warningText = document.getElementById('cfgSessionWarningText');
  if (sessionId) {
    const s = allSessions.find(x => x.id === sessionId); const count = s?.response_count || 0;
    if (count > 0) { warningText.textContent = `This session has ${count} submitted response(s). Editing the snapshot will affect how past evaluations are displayed.`; warning.classList.remove('hidden'); }
    else warning.classList.add('hidden');
  } else warning.classList.add('hidden');
  if (currentCfgType) loadConfig(currentCfgType);
}

function loadConfig(type) {
  currentCfgType = type;
  document.querySelectorAll('[data-cfgtype]').forEach(b => b.classList.toggle('active', b.dataset.cfgtype === type));
  document.getElementById('configPanel').style.display = 'none';
  if (currentCfgSession) {
    const s = allSessions.find(x => x.id === currentCfgSession);
    if (!s) { showToast('Session not found', 'error'); return; }
    const snap = s.questions_snapshot || {}; const snapData = snap[type] || {};
    const rolesRaw = snapData.questions || {}; const weightsRaw = snapData.weights || {};
    const pctW = {};
    Object.entries(weightsRaw).forEach(([k, v]) => { const num = parseFloat(v) || 0; pctW[k] = num <= 1 ? Math.round(num * 100) : Math.round(num); });
    const roles = rolesRaw.map(r => ({ name: r.name || r.role || '', label_ko: r.label_ko || r.label || r.name || '', pill_class: r.pill_class || '', min_count: r.min_count || 1, questions: (r.questions || r.items || []).map(q => ({ id: q.id || '', ko: q.text_ko || q.ko || '', en: q.text_en || q.en || '', max_score: (typeof q.max_score === 'number') ? q.max_score : null, descriptions: q.descriptions || {} })), open_questions: (r.open_questions || []).map(oq => ({ id: oq.id || '', text_ko: oq.text_ko || '', text_en: oq.text_en || '', required: !!oq.required })), portal_role_mappings: Array.isArray(r.portal_role_mappings) ? [...r.portal_role_mappings] : [] }));
    currentCfgWeights = { ...pctW }; currentCfgData = { roles };
    renderConfigRoles(type, pctW, roles); document.getElementById('configPanel').style.display = ''; return;
  }
  Promise.all([
    fetch('/api/v2/get-weights', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ evalType: type }) }).then(r => r.json()),
    fetch('/api/v2/get-questions-config', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ evalType: type }) }).then(r => r.json())
  ]).then(([wRes, qRes]) => {
    if (wRes.status !== 'SUCCESS') { showToast('Failed to load weights', 'error'); return; }
    if (qRes.status !== 'SUCCESS') { showToast('Failed to load questions', 'error'); return; }
    const rawW = wRes.data; const pctW = {};
    Object.entries(rawW).forEach(([k, v]) => { const num = parseFloat(v) || 0; pctW[k] = num <= 1 ? Math.round(num * 100) : Math.round(num); });
    currentCfgWeights = { ...pctW }; currentCfgData = JSON.parse(JSON.stringify(qRes.data));
    renderConfigRoles(type, pctW, qRes.data.roles); document.getElementById('configPanel').style.display = '';
  }).catch(() => showToast('Failed to load config. Please try again.', 'error'));
}

function renderConfigRoles(type, weights, roles) {
  const container = document.getElementById('configRoles'); container.innerHTML = '';
  Object.keys(weights).forEach(roleName => { if (!roles.find(r => (r.name || r.role) === roleName)) roles.push({ name: roleName, label_ko: roleName, min_count: 1, questions: [], portal_role_mappings: [] }); });
  roles.forEach((role, rIdx) => {
    const roleName = role.name || role.label_ko || ''; const weightPct = Math.round(weights[roleName] || 0);
    const section = document.createElement('div'); section.className = 'role-section'; section.draggable = false; section.dataset.ridx = rIdx;
    // drag-handle 에서 mousedown 한 경우에만 drag 활성화 (textarea/input 안에서의 텍스트 선택·DnD 보호)
    section.addEventListener('mousedown', e => { section.draggable = !!e.target.closest('.drag-handle'); });
    section.addEventListener('dragstart', e => { if (!section.draggable) { e.preventDefault(); return; } e.dataTransfer.effectAllowed = 'move'; e.dataTransfer.setData('text/plain', rIdx); section.classList.add('dragging'); });
    section.addEventListener('dragend', () => { section.classList.remove('dragging'); section.draggable = false; document.querySelectorAll('.role-section').forEach(s => s.classList.remove('drag-over')); });
    section.addEventListener('dragover', e => { e.preventDefault(); e.dataTransfer.dropEffect = 'move'; document.querySelectorAll('.role-section').forEach(s => s.classList.remove('drag-over')); section.classList.add('drag-over'); });
    section.addEventListener('drop', e => { e.preventDefault(); section.classList.remove('drag-over'); const fromIdx = parseInt(e.dataTransfer.getData('text/plain')); const toIdx = rIdx; if (fromIdx === toIdx) return; const roles = currentCfgData.roles; const moved = roles.splice(fromIdx, 1)[0]; roles.splice(toIdx, 0, moved); renderConfigRoles(currentCfgType, { ...currentCfgWeights }, roles); });
    const hdr = document.createElement('div'); hdr.className = 'role-section-header';
    const pillOptions = [{ v: '', label: '⚫ Default' }, { v: 'gs', label: '🔵 Blue' }, { v: 'kt', label: '🟢 Green' }, { v: 'bm', label: '🟣 Purple' }, { v: 'tl', label: '🔴 Red' }];
    const currentPill = role.pill_class || '';
    const pillSelectHtml = `<select id="cfg-pill-${rIdx}" onclick="event.stopPropagation()" onchange="cfgUpdatePillClass(${rIdx},this.value)" class="text-xs border rounded px-1.5 py-0.5 font-bold" style="border-color:var(--outline-variant);background:var(--surface-lowest);color:var(--on-surface)">${pillOptions.map(o => `<option value="${o.v}"${currentPill === o.v ? ' selected' : ''}>${o.label}</option>`).join('')}</select>`;
    hdr.innerHTML = `<div class="drag-handle" title="드래그하여 순서 변경"><i class="bi bi-grip-vertical"></i></div><div class="flex items-center gap-3 flex-1 min-w-0"><span class="w-2 h-7 rounded-full flex-shrink-0" style="background:var(--primary)"></span><div class="flex flex-col min-w-0"><div class="flex items-center gap-2 flex-wrap"><input type="text" value="${role.label_ko || roleName || ''}" class="font-extrabold bg-transparent border-b-2 border-transparent hover:border-gray-300 focus:outline-none transition-colors text-sm w-32" style="color:var(--on-surface);border-color:transparent" onchange="cfgUpdateRoleLabel(${rIdx},this.value)" onclick="event.stopPropagation()"><span class="text-xs font-mono" style="color:var(--outline)">${roleName}</span>${pillSelectHtml}<span class="text-xs font-bold bg-blue-100 text-blue-700 border border-blue-200 px-2 py-0.5 rounded-full">최소 ${role.min_count}명</span><span class="text-xs" id="cfg-counts-${rIdx}" style="color:var(--outline)">${_renderRoleCounts(role)}</span></div></div></div><div class="flex items-center gap-3 flex-shrink-0" onclick="event.stopPropagation()"><div class="flex items-center gap-2"><input type="number" id="cfg-input-${rIdx}" value="${weightPct}" min="0" max="100" step="1" class="weight-input" oninput="cfgSyncWeight(${rIdx},'${roleName}')"><span class="text-xs font-bold" style="color:var(--outline)">%</span></div><div class="flex items-center gap-1.5"><label class="text-xs font-bold hidden sm:block" style="color:var(--on-surface-variant)">Min.</label><input type="number" min="1" max="10" value="${role.min_count}" id="cfg-mincount-${rIdx}" class="mincount-input" onchange="cfgUpdateMinCount(${rIdx},this.value)"></div><button onclick="removeConfigRole(${rIdx},'${roleName}')" class="text-xs px-1.5 py-0.5 rounded hover:bg-red-50 transition-colors" style="color:var(--outline-variant)" title="역할 삭제"><i class="bi bi-x-lg"></i></button><i class="bi bi-chevron-down role-chevron transition-transform" style="color:var(--outline)"></i></div>`;
    // Portal Role 매핑 chip 영역 — header 아래 별도 라인 (헤더 토글 영향 없음)
    const portalRow = document.createElement('div');
    portalRow.className = 'cfg-portal-row';
    portalRow.id = `cfg-portal-row-${rIdx}`;
    portalRow.style.cssText = 'padding:10px 14px;border-top:1px dashed var(--outline-variant);background:var(--surface-lowest);display:flex;align-items:center;gap:10px;flex-wrap:wrap;';
    const portalLabel = document.createElement('div');
    portalLabel.style.cssText = 'font-size:.65rem;font-weight:800;text-transform:uppercase;letter-spacing:.05em;color:var(--text-muted);flex-shrink:0;';
    portalLabel.innerHTML = 'Portal 역할 매핑 <span style="opacity:.7;text-transform:none;font-weight:600">/ Portal Role Mapping</span>';
    portalRow.appendChild(portalLabel);
    const chipsWrap = document.createElement('div');
    chipsWrap.id = `cfg-portal-chips-${rIdx}`;
    chipsWrap.style.cssText = 'display:flex;gap:6px;flex-wrap:wrap;';
    portalRow.appendChild(chipsWrap);
    // chips 렌더는 container append 이후 (DOM attach 후) 수행 — 함수 마지막에 호출.

    const bodyDiv = document.createElement('div'); bodyDiv.className = 'role-section-body';
    const inner = document.createElement('div'); inner.className = 'rsb-inner'; inner.id = `cfg-qs-body-${rIdx}`;
    bodyDiv.appendChild(inner);
    hdr.onclick = (e) => { if (e.target.closest('.drag-handle')) return; if (['INPUT', 'BUTTON', 'I'].includes(e.target.tagName)) return; cfgToggleRoleSection(rIdx); };
    const mcLabel = document.createElement('p'); mcLabel.className = 'text-[10px] font-extrabold uppercase tracking-wider mb-2 mt-1'; mcLabel.style.color = 'var(--outline)'; mcLabel.innerHTML = '<i class="bi bi-list-ol me-1"></i> Multiple Choice <span class="opacity-60">객관식</span>';
    inner.appendChild(mcLabel);
    const mcContainer = document.createElement('div'); mcContainer.id = `cfg-mc-body-${rIdx}`;
    (role.questions || []).forEach((q, qIdx) => mcContainer.appendChild(makeCfgQRow(rIdx, qIdx, q)));
    inner.appendChild(mcContainer);
    const addBtn = document.createElement('button'); addBtn.className = 'mt-3 w-full flex items-center justify-center gap-2 px-4 py-3 border-2 border-dashed rounded-xl text-sm font-bold transition-all'; addBtn.style.cssText = 'border-color:var(--outline-variant);color:var(--outline)'; addBtn.innerHTML = '<i class="bi bi-plus-circle-fill"></i> Add Question <span class="text-[10px] opacity-70">문항 추가</span>'; addBtn.onmouseenter = () => { addBtn.style.borderColor = 'var(--primary)'; addBtn.style.color = 'var(--primary)'; addBtn.style.background = '#fdf3f3'; }; addBtn.onmouseleave = () => { addBtn.style.borderColor = 'var(--outline-variant)'; addBtn.style.color = 'var(--outline)'; addBtn.style.background = ''; }; addBtn.onclick = () => cfgAddQuestion(rIdx);
    inner.appendChild(addBtn);
    const divider = document.createElement('div'); divider.className = 'border-t-2 border-dashed border-blue-100 mt-5 mb-4'; inner.appendChild(divider);
    const oqLabel = document.createElement('p'); oqLabel.className = 'text-[10px] font-extrabold text-blue-400 uppercase tracking-wider mb-2'; oqLabel.innerHTML = '<i class="bi bi-pencil-square me-1"></i> Open-ended <span class="opacity-60">서술형</span>'; inner.appendChild(oqLabel);
    const oqContainer = document.createElement('div'); oqContainer.id = `cfg-oqs-body-${rIdx}`; (role.open_questions || []).forEach((q, qIdx) => oqContainer.appendChild(makeCfgOQRow(rIdx, qIdx, q))); inner.appendChild(oqContainer);
    const addOQBtn = document.createElement('button'); addOQBtn.className = 'mt-2 w-full flex items-center justify-center gap-2 px-4 py-3 border-2 border-dashed border-blue-200 rounded-xl text-sm font-bold text-blue-300 hover:border-blue-400 hover:text-blue-500 hover:bg-blue-50 transition-all'; addOQBtn.innerHTML = '<i class="bi bi-plus-circle-fill"></i> Add Open-ended <span class="text-[10px] opacity-70">서술형 추가</span>'; addOQBtn.onclick = () => cfgAddOpenQuestion(rIdx); inner.appendChild(addOQBtn);
    // 하단 collapse 버튼 — 펼친 콘텐츠가 길 때 스크롤업 없이 닫기
    const collapseBtn = document.createElement('button'); collapseBtn.className = 'mt-5 w-full flex items-center justify-center gap-2 px-4 py-2.5 rounded-lg text-xs font-bold transition-all'; collapseBtn.style.cssText = 'background:#f4f4f5;color:#52525b;border:1.5px solid #d4d4d8;'; collapseBtn.innerHTML = '<i class="bi bi-chevron-up"></i> Collapse section <span class="opacity-70">· 섹션 닫기</span>'; collapseBtn.onmouseenter = () => { collapseBtn.style.background = '#ececed'; collapseBtn.style.borderColor = '#870009'; collapseBtn.style.color = '#870009'; }; collapseBtn.onmouseleave = () => { collapseBtn.style.background = '#f4f4f5'; collapseBtn.style.borderColor = '#d4d4d8'; collapseBtn.style.color = '#52525b'; }; collapseBtn.onclick = (e) => { e.stopPropagation(); cfgToggleRoleSection(rIdx, false); }; inner.appendChild(collapseBtn);
    section.appendChild(hdr); section.appendChild(portalRow); section.appendChild(bodyDiv); container.appendChild(section);
    // chip 렌더는 DOM attach 이후 (chipsWrap 이 document 에 등록된 뒤)
    _renderPortalRoleChips(rIdx, role.portal_role_mappings || []);
  });
  cfgUpdateTotal();
}

// ── Portal Role 매핑 chip ────────────────────────────────────────────────────
// 어떤 portal_users.role 사용자가 이 rater role 의 자격을 갖는지 admin 이 선택.
// 빈 배열은 "매핑 없음" — my-tasks 페이지에서 이 role 표시 안 함.
//
// 옵션 소스: GET /api/v2/admin/roles (Firestore portal_roles, retired 제외) + __public__ sentinel.
// admin 이 추가한 custom role 즉시 반영 (60초 cache TTL).
let _portalRoleOptions = null;  // [{v, label}] — 1회 fetch 후 캐시

async function _loadPortalRoleOptions() {
  if (_portalRoleOptions) return _portalRoleOptions;
  try {
    const res = await fetch('/api/v2/admin/roles');
    const data = await res.json();
    if (data.status === 'SUCCESS' && data.data && Array.isArray(data.data.roles)) {
      const RETIRED = new Set(['retired', '퇴사']);
      _portalRoleOptions = data.data.roles
        .filter(r => !RETIRED.has(r.name))
        .map(r => ({ v: r.name, label: r.label || r.name }));
      _portalRoleOptions.push({ v: '__public__', label: '비로그인 / Public' });
      return _portalRoleOptions;
    }
  } catch (_) {}
  // Fallback — fetch 실패 시 활성 system role 만으로 chip 렌더.
  // 'MASTER' (legacy) 는 deprecated 로 처리되어 정상 fetch 응답에서 제외되므로
  // fallback 도 일관성 있게 제외 (정상 응답 6개 + __public__ 와 동일한 구성).
  _portalRoleOptions = [
    { v: 'admin',      label: 'Admin' },
    { v: 'NET',        label: 'NET' },
    { v: 'GS',         label: 'GS' },
    { v: 'TL',         label: 'TL' },
    { v: 'STL',        label: 'STL' },
    { v: '__public__', label: '비로그인 / Public' },
  ];
  return _portalRoleOptions;
}

async function _renderPortalRoleChips(rIdx, mappings) {
  const wrap = document.getElementById(`cfg-portal-chips-${rIdx}`);
  if (!wrap) return;
  const set = new Set(Array.isArray(mappings) ? mappings : []);
  wrap.innerHTML = '';
  const options = await _loadPortalRoleOptions();
  options.forEach(opt => {
    const active = set.has(opt.v);
    const chip = document.createElement('button');
    chip.type = 'button';
    chip.dataset.portalRole = opt.v;
    chip.textContent = opt.label;
    chip.title = active ? `클릭하여 해제 / Click to remove` : `클릭하여 추가 / Click to add`;
    chip.style.cssText = active
      ? 'border:1.5px solid var(--primary);background:var(--primary);color:#fff;padding:3px 10px;border-radius:9999px;font-size:11px;font-weight:700;cursor:pointer;line-height:1.2;'
      : 'border:1.5px solid var(--outline-variant);background:var(--surface);color:var(--text-muted);padding:3px 10px;border-radius:9999px;font-size:11px;font-weight:700;cursor:pointer;line-height:1.2;transition:border-color .15s, color .15s;';
    chip.onmouseenter = () => { if (!active) { chip.style.borderColor = 'var(--primary)'; chip.style.color = 'var(--primary)'; } };
    chip.onmouseleave = () => { if (!active) { chip.style.borderColor = 'var(--outline-variant)'; chip.style.color = 'var(--text-muted)'; } };
    chip.onclick = (e) => { e.stopPropagation(); cfgTogglePortalRole(rIdx, opt.v); };
    wrap.appendChild(chip);
  });
}

function cfgTogglePortalRole(rIdx, value) {
  if (!currentCfgData || !currentCfgData.roles[rIdx]) return;
  const role = currentCfgData.roles[rIdx];
  if (!Array.isArray(role.portal_role_mappings)) role.portal_role_mappings = [];
  const idx = role.portal_role_mappings.indexOf(value);
  if (idx >= 0) role.portal_role_mappings.splice(idx, 1);
  else role.portal_role_mappings.push(value);
  _renderPortalRoleChips(rIdx, role.portal_role_mappings);
}

function cfgSyncWeight(rIdx, roleName) { const val = Math.min(100, Math.max(0, parseInt(document.getElementById(`cfg-input-${rIdx}`).value) || 0)); document.getElementById(`cfg-input-${rIdx}`).value = val; currentCfgWeights[roleName] = val; cfgUpdateTotal(); }
function cfgUpdateTotal() {
  const total = Object.values(currentCfgWeights).reduce((s, v) => s + (parseInt(v) || 0), 0);
  document.getElementById('cfgWeightsTotal').textContent = total;
  const badge = document.getElementById('cfgWeightsTotalBadge');
  if (Math.abs(total - 100) < 1) { badge.textContent = '✓ OK'; badge.className = 'text-xs font-bold px-2.5 py-1 rounded-full bg-emerald-100 text-emerald-700 border border-emerald-200'; }
  else { badge.textContent = `${total > 100 ? 'Over' : 'Short'} ${Math.abs(total - 100)}%`; badge.className = 'text-xs font-bold px-2.5 py-1 rounded-full bg-red-100 border border-red-200'; badge.style.color = 'var(--error)'; }
}
function cfgToggleRoleSection(rIdx, force) {
  // force === true → 펼침, false → 접힘, undefined → 토글
  const section = document.querySelector(`.role-section[data-ridx="${rIdx}"]`);
  if (!section) return;
  const body = section.querySelector('.role-section-body');
  const chevron = section.querySelector('.role-chevron');
  const isOpen = body.classList.contains('open');
  const willOpen = (typeof force === 'boolean') ? force : !isOpen;
  body.classList.toggle('open', willOpen);
  if (chevron) chevron.classList.toggle('open', willOpen);
  section.classList.toggle('expanded', willOpen);
  // 닫을 때 헤더로 스크롤 (스크롤업 부담 해소)
  if (!willOpen) {
    const hdr = section.querySelector('.role-section-header');
    if (hdr) {
      const rect = hdr.getBoundingClientRect();
      // 헤더가 viewport 위쪽 밖이거나 너무 아래쪽이면 스크롤
      if (rect.top < 0 || rect.top > window.innerHeight - 100) {
        hdr.scrollIntoView({ behavior: 'smooth', block: 'start' });
      }
    }
  }
}

function cfgUpdateMinCount(rIdx, val) { if (currentCfgData) currentCfgData.roles[rIdx].min_count = parseInt(val) || 1; }
function cfgUpdateRoleLabel(rIdx, val) { if (currentCfgData) currentCfgData.roles[rIdx].label_ko = val.trim(); }
function cfgUpdatePillClass(rIdx, val) { if (currentCfgData) currentCfgData.roles[rIdx].pill_class = val; }

async function addConfigRole() {
  const name = await showCustomPrompt('Add Role', 'Enter the role name to add (e.g. VP, NHR)', true, false, 'Add');
  if (!name) return;
  if (currentCfgWeights[name] !== undefined) { showToast('This role already exists.', 'error'); return; }
  currentCfgWeights[name] = 0; if (!currentCfgData) currentCfgData = { roles: [] };
  currentCfgData.roles.push({ name, label_ko: name, pill_class: '', min_count: 1, questions: [], portal_role_mappings: [] });
  renderConfigRoles(currentCfgType, { ...currentCfgWeights }, currentCfgData.roles);
}
async function removeConfigRole(rIdx, roleName) {
  if (Object.keys(currentCfgWeights).length <= 1) { showToast('At least one role is required.', 'error'); return; }
  const ok = await showCustomPrompt('Delete Role', `Delete role '${roleName}'? This cannot be undone.`, false, true, 'Delete');
  if (!ok) return;
  delete currentCfgWeights[roleName]; if (currentCfgData) currentCfgData.roles.splice(rIdx, 1);
  renderConfigRoles(currentCfgType, currentCfgWeights, currentCfgData ? currentCfgData.roles : []);
}

function _descToggleInner(count) {
  return count > 0
    ? `<i class="bi bi-list dtb-icon"></i> Score descriptions <span class="dtb-count">${count}</span>`
    : `<i class="bi bi-plus-circle dtb-icon"></i> Add score descriptions`;
}

function makeCfgQRow(rIdx, qIdx, q) {
  const row = document.createElement('div'); row.className = 'q-row-card p-4 space-y-3 mb-3'; row.id = `cfg-q-row-${rIdx}-${qIdx}`;
  // 초기 렌더 카운트도 cap 이하만 — _refreshDescToggleBtn 과 동일 규칙 (I3 일관성)
  const _cap = (typeof q.max_score === 'number') ? q.max_score : _DEFAULT_MAX_SCORE;
  const descCount = Object.keys(q.descriptions || {}).filter(k => parseInt(k, 10) <= _cap).length;
  const tglClass = descCount > 0 ? 'desc-toggle-btn has-desc' : 'desc-toggle-btn';
  const tglInner = _descToggleInner(descCount);
  row.innerHTML = `<div class="flex items-center justify-between"><span class="text-xs font-extrabold px-2.5 py-0.5 rounded-full" style="background:#fdf3f3;color:var(--primary);border:1px solid var(--outline-variant)">Q${qIdx + 1}</span><button onclick="cfgRemoveQuestion(${rIdx},${qIdx})" class="flex items-center gap-1 text-xs font-bold px-2 py-1 rounded-lg hover:bg-red-50 transition-colors" style="color:var(--outline)"><i class="bi bi-trash3-fill"></i> Delete</button></div><div><label class="text-[10px] font-extrabold uppercase tracking-wider mb-1.5 flex items-center gap-1.5" style="color:var(--outline)"><span class="w-2.5 h-2.5 rounded-sm inline-block" style="background:var(--outline)"></span> Korean <span class="opacity-60">한국어</span></label><textarea rows="2" class="q-textarea" oninput="cfgUpdateQuestion(${rIdx},${qIdx},'text_ko',this.value)">${q.ko || q.text_ko || ''}</textarea></div><div><label class="text-[10px] font-extrabold uppercase tracking-wider mb-1.5 flex items-center gap-1.5" style="color:var(--outline)"><span class="w-2.5 h-2.5 rounded-sm bg-blue-400 inline-block"></span> English</label><textarea rows="2" class="q-textarea" oninput="cfgUpdateQuestion(${rIdx},${qIdx},'text_en',this.value)">${q.en || q.text_en || ''}</textarea></div><div><button type="button" id="cfg-desc-toggle-${rIdx}-${qIdx}" class="${tglClass}" onclick="cfgToggleDescriptions(${rIdx},${qIdx})">${tglInner}</button><div id="cfg-desc-body-${rIdx}-${qIdx}" class="desc-body"></div></div>`;
  return row;
}

function _refreshDescToggleBtn(rIdx, qIdx) {
  const tgl = document.getElementById(`cfg-desc-toggle-${rIdx}-${qIdx}`);
  if (!tgl || !currentCfgData) return;
  const q = currentCfgData.roles?.[rIdx]?.questions?.[qIdx];
  if (!q) return;
  const cap = (typeof q.max_score === 'number') ? q.max_score : _DEFAULT_MAX_SCORE;
  const visibleCount = Object.keys(q.descriptions || {}).filter(k => parseInt(k, 10) <= cap).length;
  tgl.classList.toggle('has-desc', visibleCount > 0);
  tgl.innerHTML = _descToggleInner(visibleCount);
}
function cfgUpdateQuestion(rIdx, qIdx, field, val) { if (currentCfgData) currentCfgData.roles[rIdx].questions[qIdx][field] = val; }

// ── 점수별 설명 (Score descriptions) ──────────────────────────────────────
const _DEFAULT_MAX_SCORE = 5;

function cfgToggleDescriptions(rIdx, qIdx) {
  const body = document.getElementById(`cfg-desc-body-${rIdx}-${qIdx}`);
  if (!body) return;
  const opening = !body.classList.contains('open');
  body.classList.toggle('open', opening);
  if (opening && !body.dataset.rendered) {
    _renderDescBody(rIdx, qIdx);
    body.dataset.rendered = '1';
  }
}

function _renderDescBody(rIdx, qIdx) {
  const body = document.getElementById(`cfg-desc-body-${rIdx}-${qIdx}`);
  if (!body) return;
  const q = currentCfgData?.roles?.[rIdx]?.questions?.[qIdx];
  if (!q) return;
  const cap = (typeof q.max_score === 'number') ? q.max_score : _DEFAULT_MAX_SCORE;
  const opts = [];
  for (let n = 2; n <= 10; n++) opts.push(`<option value="${n}"${cap === n ? ' selected' : ''}>${n}-point</option>`);
  body.innerHTML = `
    <div class="desc-body-head">
      <label>Max score</label>
      <select class="desc-maxscore-sel" onchange="cfgUpdateMaxScore(${rIdx},${qIdx},this.value)">${opts.join('')}</select>
      <button type="button" class="desc-translate-btn" onclick="cfgTranslateDescriptions(${rIdx},${qIdx})">🔮 Translate KO→EN</button>
    </div>
    <div id="cfg-desc-rows-${rIdx}-${qIdx}"></div>
    <div id="cfg-desc-hint-${rIdx}-${qIdx}" class="desc-hidden-hint" style="display:none"></div>
  `;
  renderDescriptionRows(rIdx, qIdx);
}

function renderDescriptionRows(rIdx, qIdx) {
  const container = document.getElementById(`cfg-desc-rows-${rIdx}-${qIdx}`);
  if (!container) return;
  const q = currentCfgData?.roles?.[rIdx]?.questions?.[qIdx];
  if (!q) return;
  const cap = (typeof q.max_score === 'number') ? q.max_score : _DEFAULT_MAX_SCORE;
  const descs = q.descriptions || {};
  const rows = [];
  for (let n = 1; n <= cap; n++) {
    const d = descs[String(n)] || { ko: '', en: '' };
    rows.push(`
      <div class="desc-score-row">
        <span class="score-pill">${n}</span>
        <textarea rows="1" placeholder="한국어 설명 (선택)" oninput="cfgUpdateDescription(${rIdx},${qIdx},${n},'ko',this.value)">${_escDescVal(d.ko)}</textarea>
        <textarea rows="1" placeholder="English description (optional)" oninput="cfgUpdateDescription(${rIdx},${qIdx},${n},'en',this.value)">${_escDescVal(d.en)}</textarea>
      </div>`);
  }
  container.innerHTML = rows.join('');

  // Hidden hint: cap 초과 키 (메모리 잔존) 안내
  const hint = document.getElementById(`cfg-desc-hint-${rIdx}-${qIdx}`);
  if (hint) {
    const hiddenCount = Object.keys(descs).filter(k => parseInt(k, 10) > cap).length;
    if (hiddenCount > 0) {
      hint.style.display = '';
      hint.textContent = `${hiddenCount} description(s) hidden (above max score) — they will be removed when you save.`;
    } else {
      hint.style.display = 'none';
    }
  }
}

function _escDescVal(s) {
  // textarea content escaping (HTML entities)
  return String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function cfgUpdateMaxScore(rIdx, qIdx, val) {
  if (!currentCfgData) return;
  const q = currentCfgData.roles[rIdx].questions[qIdx];
  let n = parseInt(val, 10);
  if (!(n >= 2 && n <= 10)) n = _DEFAULT_MAX_SCORE;
  q.max_score = n;
  // R7: cap 초과 키는 즉시 drop 안 함 — 메모리 보존, 저장 시 서버가 trim.
  // 표시 범위만 1..cap 으로 제한.
  renderDescriptionRows(rIdx, qIdx);
  _refreshDescToggleBtn(rIdx, qIdx);
}

function cfgUpdateDescription(rIdx, qIdx, scoreN, lang, val) {
  if (!currentCfgData) return;
  const q = currentCfgData.roles[rIdx].questions[qIdx];
  if (!q.descriptions) q.descriptions = {};
  const k = String(scoreN);
  if (!q.descriptions[k]) q.descriptions[k] = { ko: '', en: '' };
  q.descriptions[k][lang] = val;
  // 양쪽 다 비면 키 제거
  const cur = q.descriptions[k];
  if (!(cur.ko || '').trim() && !(cur.en || '').trim()) {
    delete q.descriptions[k];
  }
  _refreshDescToggleBtn(rIdx, qIdx);
}

async function cfgTranslateDescriptions(rIdx, qIdx) {
  if (!currentCfgData) return;
  const q = currentCfgData.roles[rIdx].questions[qIdx];
  const cap = (typeof q.max_score === 'number') ? q.max_score : _DEFAULT_MAX_SCORE;
  const descs = q.descriptions || {};
  // 비어있지 않은 KO 만 수집 (R2)
  const descriptionsKo = {};
  for (let n = 1; n <= cap; n++) {
    const d = descs[String(n)];
    const ko = (d && d.ko ? String(d.ko).trim() : '');
    if (ko) descriptionsKo[String(n)] = ko;
  }
  if (!Object.keys(descriptionsKo).length) {
    showToast('No Korean descriptions to translate.', 'error');
    return;
  }
  const btn = document.querySelector(`#cfg-desc-body-${rIdx}-${qIdx} .desc-translate-btn`);
  if (btn) { btn.disabled = true; btn.textContent = '⏳ Translating...'; }
  try {
    const res = await fetch('/api/v2/translate-question-descriptions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        evalType: currentCfgType,
        questionContext: q.text_ko || q.ko || q.text_en || q.en || '',
        maxScore: cap,
        descriptionsKo,
      }),
    }).then(r => r.json());
    if (res.status !== 'SUCCESS') {
      showToast(res.message || 'Translation failed.', 'error');
      return;
    }
    const en = res.descriptions_en || {};
    let updated = 0;
    // 응답 키만 EN 갱신 — KO 비어있는 점수의 EN 보존 (R2)
    for (const [k, v] of Object.entries(en)) {
      if (!q.descriptions[k]) q.descriptions[k] = { ko: descriptionsKo[k] || '', en: '' };
      q.descriptions[k].en = String(v || '');
      updated += 1;
    }
    renderDescriptionRows(rIdx, qIdx);
    _refreshDescToggleBtn(rIdx, qIdx);
    showToast(`Translated ${updated} description(s).`);
  } catch (e) {
    showToast('Network error during translation.', 'error');
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = '🔮 Translate KO→EN'; }
  }
}
function cfgAddQuestion(rIdx) {
  if (!currentCfgData) return; const qs = currentCfgData.roles[rIdx].questions;
  const newQ = { id: `q${qs.length + 1}`, text_ko: '', text_en: '' }; qs.push(newQ);
  const container = document.getElementById(`cfg-mc-body-${rIdx}`); if (!container) return;
  container.appendChild(makeCfgQRow(rIdx, qs.length - 1, newQ));
  _refreshRoleCounts(rIdx);
}
function cfgRemoveQuestion(rIdx, qIdx) {
  if (!currentCfgData) return;
  if (currentCfgData.roles[rIdx].questions.length <= 1) { showToast('At least one question is required.', 'error'); return; }
  currentCfgData.roles[rIdx].questions.splice(qIdx, 1);
  const container = document.getElementById(`cfg-mc-body-${rIdx}`); if (!container) return;
  container.innerHTML = ''; currentCfgData.roles[rIdx].questions.forEach((q, qi) => container.appendChild(makeCfgQRow(rIdx, qi, q)));
  _refreshRoleCounts(rIdx);
}

function makeCfgOQRow(rIdx, qIdx, q) {
  const row = document.createElement('div'); row.className = 'q-row-card p-4 space-y-3 mb-3 border-l-4 border-blue-300'; row.id = `cfg-oq-row-${rIdx}-${qIdx}`;
  const isReq = !!q.required;
  row.innerHTML = `<div class="flex items-center justify-between gap-2 flex-wrap"><span class="text-xs font-extrabold text-blue-600 bg-blue-50 border border-blue-200 px-2.5 py-0.5 rounded-full">OQ${qIdx + 1}</span><label class="flex items-center gap-1.5 text-xs font-bold cursor-pointer select-none" style="color:var(--on-surface-variant)"><input type="checkbox" ${isReq ? 'checked' : ''} onchange="cfgUpdateOpenQuestionRequired(${rIdx},${qIdx},this.checked)" class="cursor-pointer" style="accent-color:#B01116;width:14px;height:14px"><span>Required <span class="opacity-60 font-normal">· 필수</span></span></label><button onclick="cfgRemoveOpenQuestion(${rIdx},${qIdx})" class="flex items-center gap-1 text-xs font-bold px-2 py-1 rounded-lg hover:bg-red-50 transition-colors ml-auto" style="color:var(--outline)"><i class="bi bi-trash3-fill"></i> Delete</button></div><div><label class="text-[10px] font-extrabold uppercase tracking-wider mb-1.5 flex items-center gap-1.5" style="color:var(--outline)"><span class="w-2.5 h-2.5 rounded-sm inline-block" style="background:var(--outline)"></span> Korean <span class="opacity-60">한국어</span></label><textarea rows="2" class="q-textarea" oninput="cfgUpdateOpenQuestion(${rIdx},${qIdx},'text_ko',this.value)">${q.text_ko || ''}</textarea></div><div><label class="text-[10px] font-extrabold uppercase tracking-wider mb-1.5 flex items-center gap-1.5" style="color:var(--outline)"><span class="w-2.5 h-2.5 rounded-sm bg-blue-400 inline-block"></span> English</label><textarea rows="2" class="q-textarea" oninput="cfgUpdateOpenQuestion(${rIdx},${qIdx},'text_en',this.value)">${q.text_en || ''}</textarea></div>`;
  return row;
}
function cfgUpdateOpenQuestion(rIdx, qIdx, field, val) { if (currentCfgData) currentCfgData.roles[rIdx].open_questions[qIdx][field] = val; }
function cfgUpdateOpenQuestionRequired(rIdx, qIdx, checked) {
  if (!currentCfgData) return;
  currentCfgData.roles[rIdx].open_questions[qIdx].required = !!checked;
  // global config 편집 중이면 (세션 미선택) 기존 세션 snapshot 미반영 안내 — 한 번만 표시
  if (!currentCfgSession && !window._cfgRequiredHintShown && (typeof allSessions !== 'undefined') && Array.isArray(allSessions) && allSessions.length > 0) {
    showToast('Required toggle applies to NEW sessions only. To update existing sessions, edit each session snapshot. · 기존 세션은 snapshot 잠금 — 세션별로 직접 수정 필요', 'info');
    window._cfgRequiredHintShown = true;
  }
}
function cfgAddOpenQuestion(rIdx) {
  if (!currentCfgData) return;
  if (!currentCfgData.roles[rIdx].open_questions) currentCfgData.roles[rIdx].open_questions = [];
  const oqs = currentCfgData.roles[rIdx].open_questions;
  const newQ = { id: `oq${oqs.length + 1}`, text_ko: '', text_en: '', required: false }; oqs.push(newQ);
  const container = document.getElementById(`cfg-oqs-body-${rIdx}`); container.appendChild(makeCfgOQRow(rIdx, oqs.length - 1, newQ));
  _refreshRoleCounts(rIdx);
}
function cfgRemoveOpenQuestion(rIdx, qIdx) {
  if (!currentCfgData) return; const oqs = currentCfgData.roles[rIdx].open_questions || [];
  oqs.splice(qIdx, 1); const container = document.getElementById(`cfg-oqs-body-${rIdx}`);
  container.innerHTML = ''; oqs.forEach((q, qi) => container.appendChild(makeCfgOQRow(rIdx, qi, q)));
  _refreshRoleCounts(rIdx);
}

// 저장 버튼 disable + spinner 표시 헬퍼
// 원인 분석: save-questions / save-weights 가 _ensure_question_ids + Firestore set + invalidate_config
// 호출 → Cloud Run cold start 또는 Firestore latency 시 ~수 초. UX 차원에서 진행 표시.
function _cfgSetSavingUI(saving) {
  const btns = ['saveConfigBtnTop', 'saveConfigBtnBottom'];
  btns.forEach(id => {
    const btn = document.getElementById(id);
    if (!btn) return;
    btn.disabled = saving;
    const icon = btn.querySelector('.cfg-save-icon');
    const label = btn.querySelector('.cfg-save-label');
    if (saving) {
      if (icon) {
        icon.classList.remove('bi-floppy-fill');
        icon.classList.add('bi-arrow-repeat');  // .cfg-save-icon.bi-arrow-repeat 가 page-local keyframe 으로 회전
      }
      if (label) label.textContent = ' Saving... / 저장 중';
      btn.style.opacity = '.7';
      btn.style.cursor = 'wait';
    } else {
      if (icon) {
        icon.classList.remove('bi-arrow-repeat');
        icon.classList.add('bi-floppy-fill');
      }
      if (label) label.innerHTML = '전체 저장 <span style="font-size:10px;opacity:.7;font-weight:500;">/ Save All</span>';
      btn.style.opacity = '';
      btn.style.cursor = '';
    }
  });
}

async function saveConfig() {
  if (!currentCfgType) { showToast('Please select a position first.', 'error'); return; }
  const weights = {};
  if (currentCfgData) { currentCfgData.roles.forEach((role, rIdx) => { const roleName = role.name || ''; if (!roleName) return; const val = Math.min(100, Math.max(0, parseInt(document.getElementById(`cfg-input-${rIdx}`)?.value) || 0)); weights[roleName] = val; role.min_count = parseInt(document.getElementById(`cfg-mincount-${rIdx}`)?.value) || 1; const pillEl = document.getElementById(`cfg-pill-${rIdx}`); if (pillEl) role.pill_class = pillEl.value; if (!Array.isArray(role.portal_role_mappings)) role.portal_role_mappings = []; }); }
  const total = Object.values(weights).reduce((s, v) => s + v, 0);
  if (Math.abs(total - 100) > 0.5) { showToast(`Weights must total 100% (current: ${total}%)`, 'error'); return; }
  _cfgSetSavingUI(true);
  try {
    if (currentCfgSession) {
      const res = await fetch('/api/v2/save-session-questions', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ sessionId: currentCfgSession, evalType: currentCfgType, roles: currentCfgData?.roles || [], weights }) }).then(r => r.json());
      if (res.status === 'SUCCESS') { const s = allSessions.find(x => x.id === currentCfgSession); if (s) { if (!s.questions_snapshot) s.questions_snapshot = {}; s.questions_snapshot[currentCfgType] = { questions: currentCfgData?.roles || [], weights }; } showToast('Session snapshot saved.'); }
      else showToast(res.message || 'Failed to save', 'error');
      return;
    }
    const [wRes, qRes] = await Promise.all([
      fetch('/api/v2/save-weights', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ evalType: currentCfgType, weights }) }).then(r => r.json()),
      fetch('/api/v2/save-questions', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ evalType: currentCfgType, roles: currentCfgData?.roles || [] }) }).then(r => r.json())
    ]);
    if (wRes.status === 'SUCCESS' && qRes.status === 'SUCCESS') showToast('Weights and questions saved.');
    else showToast((wRes.message || qRes.message || 'Failed to save'), 'error');
  } catch (e) {
    showToast('Network error. Please try again.', 'error');
  } finally {
    _cfgSetSavingUI(false);
  }
}
