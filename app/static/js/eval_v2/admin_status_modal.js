// ── eval_v2 admin — Modal Interactions & Manual Score Input ──

// ── 상세 모달 ──
function openDetailModal(teacher) {
  currentModalTeacher = teacher;
  currentModalSessionId = currentSessionFilter;
  const sel = document.getElementById('modalSessionSelect');
  const dropdown = document.getElementById('modalSessionDropdown');
  sel.innerHTML = '<option value="">전체 세션</option>';
  dropdown.innerHTML = '';
  const allOpt = document.createElement('div');
  allOpt.className = 'dyb-dd-option' + (currentSessionFilter === '' ? ' selected' : '');
  allOpt.textContent = '전체 세션';
  allOpt.onclick = () => pickModalSession('', '전체 세션');
  dropdown.appendChild(allOpt);
  allSessions.forEach(s => {
    const opt = document.createElement('option'); opt.value = s.id; opt.textContent = s.label;
    if (s.id === currentSessionFilter) opt.selected = true;
    sel.appendChild(opt);
    const isAct = s.status === 'active';
    const div = document.createElement('div');
    div.className = 'dyb-dd-option' + (s.id === currentSessionFilter ? ' selected' : '');
    div.innerHTML = `<span style="color:var(--on-surface)">${_escHtml(s.label)}</span><span class="ml-auto text-[10px] font-bold px-1.5 py-0.5 rounded-full ${isAct ? 'bg-emerald-100 text-emerald-700' : 'bg-slate-100 text-slate-500'}">${isAct ? '●' : '■'}</span>`;
    div.onclick = () => pickModalSession(s.id, s.label);
    dropdown.appendChild(div);
  });
  const currentLabel = allSessions.find(s => s.id === currentSessionFilter)?.label || '전체 세션';
  document.getElementById('modalSessionLabel').textContent = currentLabel;
  document.getElementById('modalName').textContent = teacher.name;
  const badge = document.getElementById('modalTypeBadge');
  badge.textContent = teacher.typeLabel || teacher.type;
  badge.className = `text-xs font-bold px-2.5 py-0.5 rounded-full ${TYPE_BADGE[teacher.type] || 'bg-gray-100 text-gray-700'}`;
  document.getElementById('modalMeta').textContent = `${teacher.id.toUpperCase()} · ${CAMPUS_EN[teacher.campus] || teacher.campus}`;
  const body = document.getElementById('modalBody');
  body.innerHTML = '';
  const statusWrap = document.createElement('div');
  statusWrap.className = 'flex flex-wrap gap-2 pb-4 border-b-2'; statusWrap.style.borderColor = 'var(--outline-variant)';
  statusWrap.id = 'modalStatusWrap';
  (teacher.status || []).forEach(s => { statusWrap.insertAdjacentHTML('beforeend', makeStatusBadge(s.role, s.current, s.required)); });
  if (!teacher.status || !teacher.status.length) statusWrap.innerHTML = '<span class="text-sm" style="color:var(--outline)">평가 데이터가 없습니다.</span>';
  body.appendChild(statusWrap);
  const loadingDiv = document.createElement('div');
  loadingDiv.className = 'flex items-center gap-3 py-6 justify-center'; loadingDiv.style.color = 'var(--outline)';
  loadingDiv.innerHTML = `<div class="w-5 h-5 rounded-full animate-spin" style="border:2px solid var(--outline-variant);border-top-color:var(--primary);"></div><span class="text-sm">평가 내역을 불러오는 중...</span>`;
  body.appendChild(loadingDiv);
  document.getElementById('detailModal').style.display = 'flex';
  loadModalResponses(teacher, currentModalSessionId, body, statusWrap, loadingDiv);
}

function loadModalResponses(teacher, sessionId, body, statusWrap, loadingDiv) {
  if (!loadingDiv) {
    loadingDiv = document.createElement('div');
    loadingDiv.className = 'flex items-center gap-3 py-6 justify-center'; loadingDiv.style.color = 'var(--outline)';
    loadingDiv.innerHTML = `<div class="w-5 h-5 rounded-full animate-spin" style="border:2px solid var(--outline-variant);border-top-color:var(--primary);"></div><span class="text-sm">평가 내역을 불러오는 중...</span>`;
    Array.from(body.children).forEach(c => { if (c.id !== 'modalStatusWrap') c.remove(); });
    body.appendChild(loadingDiv);
  }
  fetch('/api/v2/get-responses', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ empId: teacher.id, evalType: teacher.type, sessionId }) })
    .then(r => r.json()).then(res => {
      loadingDiv.remove();
      Array.from(body.children).forEach(c => { if (c.id !== 'modalStatusWrap') c.remove(); });
      if (res.status !== 'SUCCESS' || !res.responses || !res.responses.length) {
        // Reset status badges to 0 for the selected session
        if (teacher && teacher.status && teacher.status.length) {
          statusWrap.innerHTML = '';
          teacher.status.forEach(s => {
            statusWrap.insertAdjacentHTML('beforeend', makeStatusBadge(s.role, 0, s.required));
          });
        } else {
          statusWrap.innerHTML = '<span class="text-sm" style="color:var(--outline)">평가 데이터가 없습니다.</span>';
        }
        const empty = document.createElement('div'); empty.className = 'text-center py-8'; empty.style.color = 'var(--outline)';
        empty.innerHTML = '<i class="bi bi-inbox text-3xl block mb-2"></i><p class="text-sm font-medium">제출된 평가가 없습니다.</p>';
        body.appendChild(empty);
        _appendAddRoleButton(body, teacher);
        return;
      }
      if (teacher.status) {
        const roleCounts = {};
        res.responses.forEach(r => { if (!r.is_test) roleCounts[r.rater_role] = (roleCounts[r.rater_role] || 0) + 1; });
        statusWrap.innerHTML = '';
        teacher.status.forEach(s => {
          const current = roleCounts[s.role] || 0;
          statusWrap.insertAdjacentHTML('beforeend', makeStatusBadge(s.role, current, s.required));
        });
      }
      currentModalWeights = res.weights || {};
      renderRoleAverages(body, res.responses, currentModalWeights, teacher);
      renderResponses(body, res.responses, res.questions || {}, res.open_questions || {}, teacher);
      _appendAddRoleButton(body, teacher);
    }).catch(() => {
      loadingDiv.remove();
      const errDiv = document.createElement('div');
      errDiv.className = 'text-center py-8';
      errDiv.innerHTML = '<i class="bi bi-wifi-off text-3xl block mb-2" style="color:var(--outline)"></i><p class="text-sm font-bold" style="color:var(--error)">데이터를 불러오지 못했습니다.</p><p class="text-xs mt-1" style="color:var(--outline)">네트워크 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.</p>';
      body.appendChild(errDiv);
    });
}

function onModalSessionChange(sessionId) {
  currentModalSessionId = sessionId;
  if (!currentModalTeacher) return;
  const body = document.getElementById('modalBody');
  const statusWrap = document.getElementById('modalStatusWrap');
  loadModalResponses(currentModalTeacher, sessionId, body, statusWrap, null);
}

// ── 수동 점수 입력 ──
function openEditManualModal(docId, role, ev) {
  manualEditDocId = docId; manualInputRole = role;
  // Optimistic locking: 편집 시작 시점의 버전을 저장, 저장 시 함께 전송
  manualEditVersion = Number.isFinite(ev?.version) ? ev.version : 0;
  document.getElementById('manualInputSubtitle').textContent = `[수정] ${currentModalTeacher.name} (${currentModalTeacher.id.toUpperCase()}) — 역할: ${(typeof dybRoleLabel==='function')?dybRoleLabel(role):role}`;
  document.getElementById('manualRaterName').value = ev.rater_name || '';
  document.getElementById('manualReason').value = ev.manual_reason || '';
  document.getElementById('manualComment').value = ev.comment_en || ev.comment_ko || '';
  _loadManualQuestions(role, ev.scores || {}, ev.open_answers || {});
}

function openManualInputModal(role) {
  if (!currentModalTeacher) return;
  manualEditDocId = ''; manualInputRole = role;
  manualEditVersion = 0;
  document.getElementById('manualInputSubtitle').textContent = `${currentModalTeacher.name} (${currentModalTeacher.id.toUpperCase()}) — 역할: ${(typeof dybRoleLabel==='function')?dybRoleLabel(role):role}`;
  document.getElementById('manualRaterName').value = '';
  document.getElementById('manualReason').value = '';
  document.getElementById('manualComment').value = '';
  _loadManualQuestions(role, {}, {});
}

function _loadManualQuestions(role, existingScores, existingOpenAnswers) {
  existingOpenAnswers = existingOpenAnswers || {};
  const grid = document.getElementById('manualScoreGrid');
  grid.innerHTML = `<div class="flex items-center gap-2 text-sm py-4 justify-center" style="color:var(--outline)"><div class="w-4 h-4 rounded-full animate-spin" style="border:2px solid var(--outline-variant);border-top-color:#f97316;"></div> 문항 불러오는 중...</div>`;
  document.getElementById('manualInputModal').style.display = 'flex';
  fetch('/api/v2/get-questions-config', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ evalType: currentModalTeacher.type, sessionId: currentModalSessionId || '' }) })
    .then(r => r.json()).then(res => {
      if (res.status !== 'SUCCESS') { grid.innerHTML = '<p class="text-red-500 text-sm text-center">문항을 불러오지 못했습니다.</p>'; return; }
      const roleData = (res.data?.roles || []).find(r => r.name === role);
      const hasRating = roleData?.questions?.length > 0;
      const hasOpen   = roleData?.open_questions?.length > 0;
      if (!roleData || (!hasRating && !hasOpen)) { grid.innerHTML = '<p class="text-sm text-center py-4" style="color:var(--outline)">문항이 없습니다.</p>'; return; }
      grid.innerHTML = '';

      // ── 점수 문항 (rating) ──
      if (hasRating) {
        roleData.questions.forEach((q, i) => {
          const row = document.createElement('div');
          row.className = 'rounded-xl border p-3'; row.style.cssText = 'background:var(--surface-low);border-color:var(--outline-variant)';
          const existingScore = existingScores[q.id] || 0;
          row.innerHTML = `<p class="text-xs font-bold mb-0.5" style="color:var(--on-surface)"><span class="text-[10px] font-extrabold mr-1" style="color:var(--outline)">Q${i + 1}</span>${_escHtml(q.ko || q.id)}</p>${q.en ? `<p class="text-[10px] italic mb-2" style="color:var(--outline)">${_escHtml(q.en)}</p>` : '<div class="mb-2"></div>'}<div class="flex gap-2">${[1, 2, 3, 4, 5].map(v => `<button type="button" onclick="selectManualScore(this,'${_escHtml(q.id)}')" data-qid="${_escHtml(q.id)}" data-val="${v}" ${v === existingScore ? 'data-selected="1"' : ''} class="manual-score-btn flex-1 py-1.5 rounded-lg border-2 text-xs font-extrabold transition-all" style="border-color:var(--outline-variant);color:var(--on-surface-variant)${v === existingScore ? ';border-color:#f97316;color:#ea580c;background:#fff7ed' : ''}">${v}</button>`).join('')}</div>`;
          grid.appendChild(row);
        });
      }

      // ── 서술형 문항 (open) ──
      if (hasOpen) {
        const divider = document.createElement('div');
        divider.style.cssText = 'margin:12px 0 8px;font-size:10px;font-weight:800;letter-spacing:.08em;text-transform:uppercase;color:var(--outline)';
        divider.textContent = '서술형 문항 · Open Questions';
        grid.appendChild(divider);
        roleData.open_questions.forEach((oq, i) => {
          const row = document.createElement('div');
          row.className = 'rounded-xl border p-3'; row.style.cssText = 'background:var(--surface-low);border-color:var(--outline-variant)';
          const existingText = existingOpenAnswers[oq.id] || '';
          row.innerHTML = `<p class="text-xs font-bold mb-0.5" style="color:var(--on-surface)"><span class="text-[10px] font-extrabold mr-1" style="color:#2563eb">OQ${i + 1}</span>${_escHtml(oq.text_ko || oq.id)}</p>${oq.text_en ? `<p class="text-[10px] italic mb-2" style="color:var(--outline)">${_escHtml(oq.text_en)}</p>` : '<div class="mb-2"></div>'}<textarea data-oqid="${_escHtml(oq.id)}" rows="3" placeholder="서술형 답변을 입력하세요..." class="open-answer-textarea w-full rounded-lg border px-3 py-2 text-xs resize-none outline-none transition-all" style="border-color:var(--outline-variant);background:var(--surface);color:var(--on-surface);font-family:inherit">${_escHtml(existingText)}</textarea>`;
          grid.appendChild(row);
        });
      }
    }).catch(() => { grid.innerHTML = '<p class="text-red-500 text-sm text-center">문항을 불러오지 못했습니다.</p>'; });
}

function selectManualScore(btn, qid) {
  const parent = btn.closest('.flex.gap-2');
  parent.querySelectorAll('.manual-score-btn').forEach(b => {
    b.style.cssText = 'border-color:var(--outline-variant);color:var(--on-surface-variant)';
    b.removeAttribute('data-selected');
  });
  btn.style.cssText = 'border-color:#f97316;color:#ea580c;background:#fff7ed;';
  btn.setAttribute('data-selected', '1');
}

async function submitManualInput() {
  const raterName = document.getElementById('manualRaterName').value.trim();
  const reason = document.getElementById('manualReason').value.trim();
  const comment = document.getElementById('manualComment').value.trim();
  if (!raterName) { showToast('평가자 이름을 입력해 주세요.', 'error'); return; }
  const scores = {}; let allAnswered = true;
  document.querySelectorAll('#manualScoreGrid .flex.gap-2').forEach(row => {
    const btns = row.querySelectorAll('.manual-score-btn');
    const qid = btns[0]?.dataset?.qid;
    const activeBtn = Array.from(btns).find(b => b.hasAttribute('data-selected'));
    if (activeBtn && qid) scores[qid] = parseInt(activeBtn.dataset.val);
    else if (qid) allAnswered = false;
  });
  if (!allAnswered) { showToast('모든 문항에 점수를 입력해 주세요.', 'error'); return; }
  // 서술형 답변 수집
  const openAnswers = {};
  document.querySelectorAll('#manualScoreGrid .open-answer-textarea').forEach(ta => {
    const oqid = ta.dataset.oqid;
    if (oqid) openAnswers[oqid] = ta.value.trim();
  });
  const hasRatingQuestions = Object.keys(scores).length > 0 || document.querySelectorAll('#manualScoreGrid .manual-score-btn').length > 0;
  if (hasRatingQuestions && !Object.keys(scores).length) { showToast('문항을 찾을 수 없습니다.', 'error'); return; }
  const isEdit = !!manualEditDocId;
  if (!isEdit && !currentModalSessionId) { showToast('세션을 선택해 주세요.', 'error'); return; }
  const payload = isEdit
    ? { docId: manualEditDocId, scores, commentEn: comment, raterName, manualReason: reason, openAnswers, version: manualEditVersion }
    : { empId: currentModalTeacher.id, evalType: currentModalTeacher.type, raterName, raterRole: manualInputRole, scores, commentEn: comment, openAnswers, sessionId: currentModalSessionId, isManual: true, manualReason: reason };
  const saveBtn = document.querySelector('#manualInputModal button[onclick="submitManualInput()"]');
  const origBtnHtml = saveBtn?.innerHTML;
  if (saveBtn) { saveBtn.disabled = true; saveBtn.innerHTML = '<div class="w-3.5 h-3.5 rounded-full animate-spin flex-shrink-0" style="border:2px solid #fed7aa;border-top-color:#f97316;"></div> 저장 중...'; }
  try {
    const httpRes = await fetch(isEdit ? '/api/v2/update-eval' : '/api/v2/submit-eval', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
    const res = await httpRes.json();
    if (res.status === 'SUCCESS') {
      showToast('저장되었습니다.', 'success'); closeModal('manualInputModal');
      const body = document.getElementById('modalBody'); const statusWrap = document.getElementById('modalStatusWrap');
      loadModalResponses(currentModalTeacher, currentModalSessionId, body, statusWrap, null);
    } else if (httpRes.status === 409 && res.code === 'VERSION_CONFLICT') {
      showToast('다른 관리자가 수정했습니다. 최신 내용으로 새로고침합니다...', 'error');
      closeModal('manualInputModal');
      const body = document.getElementById('modalBody'); const statusWrap = document.getElementById('modalStatusWrap');
      loadModalResponses(currentModalTeacher, currentModalSessionId, body, statusWrap, null);
    } else { showToast(res.message || '저장에 실패했습니다.', 'error'); }
  } catch (e) { showToast('오류가 발생했습니다.', 'error'); }
  finally { if (saveBtn) { saveBtn.disabled = false; saveBtn.innerHTML = origBtnHtml; } }
}

// ── 역할 선택 후 수동 점수 추가 ──
function _appendAddRoleButton(body, teacher) {
  const existing = document.getElementById('addRoleWrap');
  if (existing) existing.remove();
  const wrap = document.createElement('div');
  wrap.id = 'addRoleWrap';
  wrap.style.cssText = 'margin-top:16px;padding-top:16px;border-top:2px solid var(--outline-variant)';
  wrap.innerHTML = `
    <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;">
      <span style="font-size:12px;font-weight:700;color:var(--outline);">다른 역할로 점수 추가</span>
      <div style="display:flex;align-items:center;gap:8px;">
        <select id="addRoleSelect" style="font-size:12px;font-weight:700;border:2px solid var(--outline-variant);border-radius:8px;padding:6px 10px;background:var(--surface-lowest);color:var(--on-surface);outline:none;">
          <option value="">역할 선택...</option>
        </select>
        <button id="addRoleBtn" onclick="_startAddRoleScore()" style="display:flex;align-items:center;gap:6px;font-size:12px;font-weight:800;padding:6px 14px;border-radius:8px;border:2px solid #f97316;color:#ea580c;background:transparent;cursor:pointer;transition:background .15s;" onmouseover="this.style.background='#fff7ed'" onmouseout="this.style.background='transparent'">
          <i class="bi bi-plus-circle-fill"></i> 추가
        </button>
      </div>
    </div>`;
  body.appendChild(wrap);

  // 역할 목록 비동기 로드
  fetch('/api/v2/get-questions-config', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ evalType: teacher.type, sessionId: currentModalSessionId || '' })
  }).then(r => r.json()).then(res => {
    if (res.status !== 'SUCCESS') return;
    const sel = document.getElementById('addRoleSelect');
    if (!sel) return;
    (res.data?.roles || []).forEach(r => {
      const opt = document.createElement('option');
      opt.value = r.name;
      opt.textContent = r.label_ko || r.name;
      sel.appendChild(opt);
    });
  }).catch(e => console.error('loadModalRoles error:', e));
}

function _startAddRoleScore() {
  const sel = document.getElementById('addRoleSelect');
  const role = sel?.value;
  if (!role) { showToast('역할을 선택해 주세요.', 'error'); return; }
  openManualInputModal(role);
}
