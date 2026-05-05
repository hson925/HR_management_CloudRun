// ── eval_v2 admin — Async Actions (Reports · CSV · Submissions · Translation) ──

let _loadedSubSessionId = null;

// ── 전체 제출현황 모달 ──
function openSubmissionsModal() {
  document.getElementById('submissionsModal').style.display = 'flex';
  const subSel = document.getElementById('subSessionSelect');
  if (subSel) {
    subSel.innerHTML = '<option value="">-- 세션 선택 --</option>';
    allSessions.forEach(s => { const opt = document.createElement('option'); opt.value = s.id; opt.textContent = `${s.label} (${s.status === 'active' ? '진행중' : '마감'})`; subSel.appendChild(opt); });
    if (currentSessionFilter) { subSel.value = currentSessionFilter; }
  }
  const body = document.getElementById('submissionsBody');
  const selectedSession = subSel?.value || '';
  if (!selectedSession) {
    body.innerHTML = `<div class="flex flex-col items-center gap-3 py-12 justify-center" style="color:var(--outline)"><i class="bi bi-calendar2-check text-3xl" style="color:var(--outline-variant)"></i><p class="text-sm font-semibold">세션을 선택하면 제출 현황을 확인할 수 있습니다.</p><p class="text-xs opacity-70">세션을 먼저 선택해 주세요.</p></div>`;
    allSubmissions = [];
    _loadedSubSessionId = null;
    return;
  }
  _loadSubmissions(selectedSession, body);
}

function _loadSubmissions(sessionId, body) {
  body.innerHTML = `<div class="flex items-center gap-3 py-8 justify-center" style="color:var(--outline)"><div class="w-5 h-5 rounded-full animate-spin" style="border:2px solid var(--outline-variant);border-top-color:var(--primary);"></div><span class="text-sm">불러오는 중...</span></div>`;
  fetch('/api/v2/get-all-submissions', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ session_id: sessionId }) })
    .then(r => r.json()).then(res => {
      if (res.status !== 'SUCCESS') { body.innerHTML = '<p class="text-center py-8" style="color:var(--error)">불러오기에 실패했습니다.</p>'; return; }
      allSubmissions = res.submissions || [];
      _loadedSubSessionId = sessionId;
      filterSubmissions();
    })
    .catch(err => {
      console.error('get-all-submissions failed', err);
      body.innerHTML = '<p class="text-center py-8" style="color:var(--error)">불러오기에 실패했습니다.</p>';
    });
}

function filterSubmissions() {
  const q = document.getElementById('subSearchInput').value.trim().toLowerCase();
  const sessionId = document.getElementById('subSessionSelect')?.value || '';
  const body = document.getElementById('submissionsBody');
  if (!sessionId) {
    allSubmissions = [];
    _loadedSubSessionId = null;
    body.innerHTML = `<div class="flex flex-col items-center gap-3 py-12 justify-center" style="color:var(--outline)"><i class="bi bi-calendar2-check text-3xl" style="color:var(--outline-variant)"></i><p class="text-sm font-semibold">세션을 선택하면 제출 현황을 확인할 수 있습니다.</p><p class="text-xs opacity-70">세션을 먼저 선택해 주세요.</p></div>`;
    return;
  }
  // 세션이 바뀐 경우에만 다시 로드 (빈 결과여도 _loadedSubSessionId 가 세션을 기억하므로 무한 루프 방지)
  if (_loadedSubSessionId !== sessionId) { _loadSubmissions(sessionId, body); return; }
  let filtered = allSubmissions;
  if (q) filtered = filtered.filter(s =>
    s.rater_name.toLowerCase().includes(q) ||
    (s.emp_name && s.emp_name.toLowerCase().includes(q)) ||
    s.emp_id.toLowerCase().includes(q) ||
    matchesCampusSearch(q, s.emp_campus, s.emp_campus_en)
  );
  renderSubmissions(filtered);
}

function renderSubmissions(submissions) {
  const body = document.getElementById('submissionsBody');
  if (!submissions.length) { body.innerHTML = '<p class="text-center py-8 text-sm" style="color:var(--outline)">제출 내역이 없습니다.</p>'; return; }
  const byRater = {};
  submissions.forEach(s => { if (!byRater[s.rater_name]) byRater[s.rater_name] = []; byRater[s.rater_name].push(s); });
  body.innerHTML = '';
  Object.entries(byRater).sort((a, b) => a[0].localeCompare(b[0])).forEach(([rater, items]) => {
    const section = document.createElement('div');
    section.className = 'border-2 rounded-xl overflow-hidden mb-2'; section.style.borderColor = 'var(--outline-variant)';
    section.innerHTML = `<div class="flex items-center gap-2 px-4 py-3 border-b" style="background:var(--surface-low);border-color:var(--outline-variant)"><div class="w-7 h-7 rounded-full flex items-center justify-center flex-shrink-0" style="background:var(--primary-soft);border:1px solid var(--outline-variant)"><i class="bi bi-person-fill text-xs" style="color:var(--primary)"></i></div><span class="font-extrabold text-sm" style="color:var(--on-surface)">${rater}</span><span class="text-xs px-2 py-0.5 rounded-full" style="background:var(--surface-low);color:var(--on-surface-variant)">${items.length}건</span></div><div class="divide-y" style="border-color:var(--outline-variant)">${items.map(s => `<div class="flex items-center justify-between px-4 py-2.5"><div class="flex items-center gap-2"><span class="text-sm font-bold" style="color:var(--on-surface)">${s.emp_name || s.emp_id}</span><span class="text-xs font-mono" style="color:var(--outline)">${s.emp_id.toUpperCase()}</span><span class="text-xs font-bold px-2 py-0.5 rounded-full ${TYPE_BADGE[s.eval_type] || 'bg-gray-100 text-gray-600'}">${EVAL_TYPES[s.eval_type] || s.eval_type}</span></div><div class="flex items-center gap-2"><span class="text-xs font-bold px-2 py-0.5 rounded-full" style="background:var(--surface-low);color:var(--on-surface-variant)">${_escHtml((typeof dybRoleLabel==='function')?dybRoleLabel(s.rater_role):s.rater_role)}</span><span class="text-xs" style="color:var(--outline)">${s.submitted_at || ''}</span></div></div>`).join('')}</div>`;
    body.appendChild(section);
  });
}

// ── Drive 폴더 & 리포트 생성 ──
async function openDriveFolder() {
  if (!currentModalTeacher) { showToast('선생님을 선택해 주세요.', 'error'); return; }
  const btn = document.getElementById('driveFolderBtn'); if (btn.disabled) return;
  btn.disabled = true; const origHtml = btn.innerHTML; btn.innerHTML = '<div class="w-3 h-3 rounded-full animate-spin" style="border:2px solid #a7f3d0;border-top-color:#059669;"></div>';
  try {
    const res = await fetch('/api/v2/get-drive-folder', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ empId: currentModalTeacher.id }) }).then(r => r.json());
    if (res.status === 'SUCCESS' && res.folderUrl) window.open(res.folderUrl, '_blank');
    else showToast('Drive 폴더를 찾을 수 없습니다. 먼저 보고서를 생성해 주세요.', 'error');
  } catch (e) { showToast('오류가 발생했습니다.', 'error'); } finally { btn.innerHTML = origHtml; btn.disabled = false; }
}

async function generateReport() {
  if (!currentModalTeacher) { showToast('선생님을 선택해 주세요.', 'error'); return; }
  if (!currentModalSessionId) { showToast('세션을 선택해 주세요.', 'error'); return; }
  const ok = await showCustomPrompt('보고서 생성', `${currentModalTeacher.name} (${currentModalTeacher.id.toUpperCase()}) — 세션 ${currentModalSessionId}의 보고서를 생성하여 Drive에 저장하시겠습니까?`, false, true, '생성');
  if (!ok) return;
  const btn = document.getElementById('generateReportBtn'); const origHtml = btn.innerHTML;
  btn.innerHTML = '<div class="w-3.5 h-3.5 rounded-full animate-spin flex-shrink-0" style="border:2px solid #bfdbfe;border-top-color:#2563eb;"></div> 생성 중...'; btn.disabled = true;
  try {
    const res = await fetch('/api/v2/generate-report', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ empId: currentModalTeacher.id, evalType: currentModalTeacher.type, sessionId: currentModalSessionId }) }).then(r => r.json());
    if (res.status === 'SUCCESS') showToast('보고서가 Drive에 저장되었습니다.', 'success');
    else showToast(res.message || '보고서 생성에 실패했습니다.', 'error');
  } catch (e) { showToast('오류가 발생했습니다.', 'error'); } finally { btn.innerHTML = origHtml; btn.disabled = false; }
}

async function trashReport() {
  if (!currentModalTeacher) { showToast('선생님을 선택해 주세요.', 'error'); return; }
  if (!currentModalSessionId) { showToast('세션을 선택해 주세요.', 'error'); return; }
  const ok = await showCustomPrompt(
    '보고서 휴지통 이동',
    `${currentModalTeacher.name} (${currentModalTeacher.id.toUpperCase()}) — 세션 ${currentModalSessionId}의 보고서를 Drive 휴지통으로 이동하시겠습니까?`,
    false, true, '휴지통으로 이동'
  );
  if (!ok) return;
  const btn = document.getElementById('trashReportBtn'); const origHtml = btn.innerHTML;
  btn.innerHTML = '<div class="w-3.5 h-3.5 rounded-full animate-spin flex-shrink-0" style="border:2px solid #fecaca;border-top-color:#e11d48;"></div> 삭제 중...'; btn.disabled = true;
  try {
    const res = await fetch('/api/v2/trash-report', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ empId: currentModalTeacher.id, sessionId: currentModalSessionId }) }).then(r => r.json());
    if (res.status === 'SUCCESS') showToast(`보고서가 Drive 휴지통으로 이동되었습니다: ${res.trashedFile}`, 'success');
    else showToast(res.message || '보고서 이동에 실패했습니다.', 'error');
  } catch (e) { showToast('오류가 발생했습니다.', 'error'); } finally { btn.innerHTML = origHtml; btn.disabled = false; }
}

// ── 응답 삭제 & 테스트 마크 ──
async function deleteResponse(docId, btn) {
  const ok = await showCustomPrompt('응답 삭제', '이 평가 응답을 삭제하시겠습니까? 이 작업은 되돌릴 수 없습니다.', false, true, '삭제');
  if (!ok) return;
  const res = await fetch('/api/v2/delete-response', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ docId }) }).then(r => r.json());
  if (res.status === 'SUCCESS') { showToast('삭제되었습니다.'); btn.closest('.p-4').remove(); statusLoaded = false; }
  else showToast(res.message || '삭제에 실패했습니다.', 'error');
}

async function toggleTestMark(docId, isTest, btn) {
  const res = await fetch('/api/v2/mark-test', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ docId, isTest }) }).then(r => r.json());
  if (res.status === 'SUCCESS') {
    showToast(isTest ? '테스트 응답으로 표시되었습니다.' : '테스트 표시가 해제되었습니다.'); statusLoaded = false;
    const evalDiv = btn.closest('.p-4'); const nameSpan = evalDiv.querySelector('span.font-bold.text-sm'); const testBadge = evalDiv.querySelector('.bg-amber-100');
    if (isTest && !testBadge) { const b = document.createElement('span'); b.className = 'text-[10px] font-extrabold bg-amber-100 text-amber-600 border border-amber-200 px-2 py-0.5 rounded-full'; b.textContent = 'TEST'; nameSpan.after(b); }
    else if (!isTest && testBadge) testBadge.remove();
    btn.innerHTML = `<i class="bi bi-flag${isTest ? '-fill' : ''}"></i>`;
    btn.className = `text-[10px] font-bold px-2 py-1 rounded-lg border transition-colors ${isTest ? 'border-amber-300 text-amber-600 hover:bg-amber-50' : 'hover:text-amber-500 hover:border-amber-300'}`;
    if (!isTest) btn.style.cssText = 'border-color:var(--outline-variant);color:var(--outline)';
    btn.onclick = () => toggleTestMark(docId, !isTest, btn);
  } else showToast(res.message || '작업에 실패했습니다.', 'error');
}

// ── 동명이인 정정 — Promote / Depromote ──
async function promoteResponse(docId, btn) {
  const ok = await showCustomPrompt(
    '수동 채택으로 변환',
    '이 응답을 manual entry 로 승격하면 평균 산출에 다시 포함됩니다.\n동명이인 케이스 정정에 사용하세요.\n\n계속하시겠습니까?',
    false, true, '변환'
  );
  if (!ok) return;
  if (btn) btn.disabled = true;
  try {
    const res = await fetch('/api/v2/promote-response', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ docId }),
    }).then(r => r.json());
    if (res.status === 'SUCCESS') {
      showToast('수동 채택으로 변환되었습니다. 평균에 다시 포함됩니다.', 'success');
      statusLoaded = false;
      const body = document.getElementById('modalBody');
      const statusWrap = document.getElementById('modalStatusWrap');
      if (body && statusWrap && currentModalTeacher) {
        loadModalResponses(currentModalTeacher, currentModalSessionId, body, statusWrap, null);
      }
    } else {
      showToast(res.message || '변환에 실패했습니다.', 'error');
      if (btn) btn.disabled = false;
    }
  } catch (e) {
    showToast('오류가 발생했습니다.', 'error');
    if (btn) btn.disabled = false;
  }
}

async function depromoteResponse(docId, btn) {
  const ok = await showCustomPrompt(
    '수동 채택 해제',
    '이 응답을 원래 self-submit 상태로 되돌립니다.\n동명이인 그룹의 dedup 규칙에 다시 들어가므로,\n같은 이름의 더 최신 응답이 있으면 평균에서 다시 빠질 수 있습니다.\n\n계속하시겠습니까?',
    false, true, '되돌리기'
  );
  if (!ok) return;
  if (btn) btn.disabled = true;
  try {
    const res = await fetch('/api/v2/depromote-response', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ docId }),
    }).then(r => r.json());
    if (res.status === 'SUCCESS') {
      showToast('수동 채택이 해제되었습니다.', 'success');
      statusLoaded = false;
      const body = document.getElementById('modalBody');
      const statusWrap = document.getElementById('modalStatusWrap');
      if (body && statusWrap && currentModalTeacher) {
        loadModalResponses(currentModalTeacher, currentModalSessionId, body, statusWrap, null);
      }
    } else {
      showToast(res.message || '해제에 실패했습니다.', 'error');
      if (btn) btn.disabled = false;
    }
  } catch (e) {
    showToast('오류가 발생했습니다.', 'error');
    if (btn) btn.disabled = false;
  }
}

// ── Excel 내보내기 ──
function exportCsv() {
  const sessionId = currentSessionFilter || '';
  if (!sessionId) { showToast('세션을 먼저 선택해 주세요.', 'error'); return; }
  const sessionLabel = allSessions.find(s => s.id === sessionId)?.label || sessionId;
  document.getElementById('exportModalSessionLabel').textContent = sessionLabel;
  document.getElementById('exportIncludeTest').checked = false;
  document.getElementById('exportModal').style.display = 'flex';
}

async function _doExportCsv() {
  const sessionId = currentSessionFilter || '';
  const includeTest = document.getElementById('exportIncludeTest').checked;
  closeModal('exportModal');
  showToast('Excel 다운로드를 준비 중입니다...', 'info');
  try {
    const res = await fetch('/api/v2/export-csv', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ sessionId, includeTest }) });
    if (!res.ok) { showToast('Excel 내보내기에 실패했습니다.', 'error'); return; }
    const blob = await res.blob();
    const cd = res.headers.get('Content-Disposition') || '';
    const match = cd.match(/filename="([^"]+)"/);
    const filename = match ? match[1] : `eval_v2_${sessionId}.xlsx`;
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a'); a.href = url; a.download = filename; document.body.appendChild(a); a.click(); document.body.removeChild(a); URL.revokeObjectURL(url);
    showToast('Excel 파일이 다운로드되었습니다.', 'success');
  } catch (e) { showToast('내보내기 중 오류가 발생했습니다.', 'error'); }
}

// ── 번역 기능 ──

async function retranslateResponse(docId, btn) {
  const orig = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = '<span class="w-2 h-2 rounded-full bg-yellow-400 animate-pulse inline-block mr-1"></span>시작 중...';
  try {
    const res = await fetch(`/api/v2/responses/${docId}/translate`, { method: 'POST' }).then(r => r.json());
    if (res.status === 'SUCCESS') {
      showToast('번역이 시작되었습니다. 잠시 후 새로고침하면 결과를 확인할 수 있습니다.', 'success');
      btn.outerHTML = '<span class="text-[10px] font-bold bg-yellow-100 text-yellow-700 border border-yellow-200 px-1.5 py-0.5 rounded-full flex items-center gap-1"><span class="w-2 h-2 rounded-full bg-yellow-400 animate-pulse inline-block"></span>번역 중...</span>';
    } else {
      showToast(res.message || '번역 시작에 실패했습니다.', 'error');
      btn.disabled = false; btn.innerHTML = orig;
    }
  } catch (e) { showToast('네트워크 오류가 발생했습니다.', 'error'); btn.disabled = false; btn.innerHTML = orig; }
}

// 번역 인라인 편집 — lang: 'ko' | 'en'
function openTranslationEdit(docId, qId, lang, editBtn) {
  // editBtn sits inside a flex header <div>; its parent is the column wrapper <div>
  const langWrapper = editBtn.closest('div').parentElement;
  const textEl = langWrapper.querySelector(`.translation-text[data-lang="${lang}"]`);
  if (!textEl) return;

  // 이미 편집 중이면 취소
  const existing = langWrapper.querySelector('textarea.translation-edit-area');
  if (existing) {
    existing.remove();
    langWrapper.querySelector('.translation-save-btn')?.remove();
    editBtn.innerHTML = '<i class="bi bi-pencil-fill"></i>';
    textEl.style.display = '';
    return;
  }

  const currentText = textEl.querySelector('span.italic') ? '' : textEl.textContent.trim();
  textEl.style.display = 'none';
  editBtn.innerHTML = '<i class="bi bi-x-lg"></i>';

  const ta = document.createElement('textarea');
  ta.className = 'translation-edit-area w-full text-sm px-2 py-1.5 border-2 rounded-lg font-mono resize-y focus:outline-none mt-1';
  ta.style.cssText = 'border-color:var(--outline-variant);min-height:72px;';
  ta.value = currentText;
  langWrapper.appendChild(ta);

  const saveBtn = document.createElement('button');
  saveBtn.className = 'translation-save-btn mt-1 text-[11px] font-bold px-2 py-0.5 rounded-lg text-white';
  saveBtn.style.background = '#15803d';
  saveBtn.innerHTML = '<i class="bi bi-floppy-fill mr-0.5"></i>저장';
  saveBtn.onclick = () => saveTranslationEdit(docId, qId, lang, ta, textEl, editBtn, langWrapper);
  langWrapper.appendChild(saveBtn);
}

async function saveTranslationEdit(docId, qId, lang, ta, textEl, editBtn, langWrapper) {
  const newText = ta.value.trim();
  const saveBtn = langWrapper.querySelector('.translation-save-btn');
  if (saveBtn) { saveBtn.disabled = true; saveBtn.innerHTML = '<i class="bi bi-hourglass-split"></i>'; }

  // 현재 화면에 표시된 모든 번역을 수집 (다른 문항 덮어쓰기 방지)
  const fieldKey = lang === 'ko' ? 'open_answers_ko' : 'open_answers_en';
  const updated = {};
  document.querySelectorAll(`.translation-text[data-doc="${docId}"][data-lang="${lang}"]`).forEach(el => {
    const qid = el.dataset.qid;
    updated[qid] = el.querySelector('span.italic') ? '' : el.textContent.trim();
  });
  updated[qId] = newText;

  try {
    const res = await fetch(`/api/v2/responses/${docId}/update-translation`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ [fieldKey]: updated }),
    }).then(r => r.json());

    if (res.status === 'SUCCESS') {
      textEl.textContent = newText || '';
      textEl.style.display = '';
      ta.remove(); saveBtn?.remove();
      editBtn.innerHTML = '<i class="bi bi-pencil-fill"></i>';
      showToast('번역이 저장되었습니다.', 'success');
    } else {
      showToast(res.message || '저장에 실패했습니다.', 'error');
      if (saveBtn) { saveBtn.disabled = false; saveBtn.innerHTML = '<i class="bi bi-floppy-fill mr-0.5"></i>저장'; }
    }
  } catch (e) {
    showToast('네트워크 오류가 발생했습니다.', 'error');
    if (saveBtn) { saveBtn.disabled = false; saveBtn.innerHTML = '<i class="bi bi-floppy-fill mr-0.5"></i>저장'; }
  }
}
