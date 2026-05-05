// ── eval_v2 admin — Data Loading & Filtering ──

let completionFilter = '';   // '' = 전체, 'done' = 완료, 'pending' = 미완료

// ── 필터 토글 그룹 mutex 헬퍼 ──
// 두 토글이 동시에 열리면 우측 chip 이 잘림 → 한 토글 열 때 같은 그룹의 다른 토글들 강제 닫기.
// 신규 토글 추가 절차:
//   1) `_filterToggleGroup` 배열에 ID prefix 추가 (Panel/Chevron 자동 조합)
//   2) 신규 토글 함수에서 _closeOtherFilterToggles(currentId) 호출 — 다른 토글 ID 직접 명시 X
const _filterToggleGroup = ['positionFilter', 'completionFilter'];

function _closeOtherFilterToggles(currentId) {
  _filterToggleGroup.filter(id => id !== currentId).forEach(id => {
    const panel = document.getElementById(id + 'Panel');
    const chevron = document.getElementById(id + 'Chevron');
    if (panel) panel.classList.remove('open');
    if (chevron) chevron.style.transform = '';
  });
}

function toggleCompletionFilter() {
  const panel = document.getElementById('completionFilterPanel');
  const chevron = document.getElementById('completionFilterChevron');
  const isOpen = panel.classList.contains('open');
  panel.classList.toggle('open');
  chevron.style.transform = isOpen ? '' : 'rotate(180deg)';
  if (!isOpen) _closeOtherFilterToggles('completionFilter');
}

function setCompletionFilter(btn, val) {
  completionFilter = val;
  document.querySelectorAll('[data-filter-completion]').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  const badge = document.getElementById('completionFilterBadge');
  if (badge) {
    if (val) { badge.textContent = '1'; badge.classList.remove('hidden'); }
    else { badge.classList.add('hidden'); }
  }
  applyFilters();
}

function loadStatus() {
  document.getElementById('statusLoading').style.display = 'none';
  document.getElementById('statusContent').style.display = 'none';
  if (!currentSessionFilter) {
    const content = document.getElementById('statusContent');
    content.style.display = '';
    content.innerHTML = `<div class="flex flex-col items-center justify-center py-16" style="color:var(--outline)">
      <i class="bi bi-funnel text-4xl mb-3"></i>
      <p class="text-base font-bold mb-1" style="color:var(--on-surface-variant)">세션을 선택하세요</p>
      <p class="text-sm"><strong>회차 관리</strong> 탭으로 이동하여 <strong>"현황 보기"</strong>를 클릭하면 세션별로 필터링됩니다.</p>
    </div>`;
    return;
  }
  document.getElementById('statusLoading').style.display = 'flex';
  // portal_roles 캐시 + status fetch 병렬. 첫 렌더부터 dybRoleLabel 캐시 hit 보장 (race 차단).
  const _rolesP = (typeof window.dybLoadRoleLabels === 'function') ? window.dybLoadRoleLabels() : Promise.resolve();
  const _statusP = fetch('/api/v2/get-status', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ sessionId: currentSessionFilter }) })
    .then(r => r.json());
  Promise.all([_statusP, _rolesP]).then(([res]) => {
    document.getElementById('statusLoading').style.display = 'none';
    if (res.status !== 'SUCCESS') { showToast('현황을 불러오지 못했습니다: ' + res.message, 'error'); return; }
    allStatusData = res.data; statusLoaded = true; applyFilters();
  }).catch(() => { document.getElementById('statusLoading').style.display = 'none'; showToast('서버 오류가 발생했습니다.', 'error'); });
}

function debouncedApplyFilters() {
  clearTimeout(_searchDebounceTimer);
  _searchDebounceTimer = setTimeout(applyFilters, 300);
}

function applyFilters() {
  if (!statusLoaded) return;
  if (openTypeSections.size > 0) { renderTypeAccordion(); return; }
  // 타입 섹션 없을 때 → 타입뷰 숨기고 캠퍼스뷰 복원
  document.getElementById('typeAccordionWrap').style.display = 'none';
  const search = document.getElementById('searchInput').value.trim().toLowerCase();
  // 영어 코드 → 한글 캠퍼스 역매핑 (e.g. "dae" → "Campus A")
  const enToKo = search ? Object.fromEntries(Object.entries(CAMPUS_EN).map(([k, v]) => [v.toLowerCase(), k])) : {};
  const matchedKoCampus = search ? (enToKo[search] || null) : null;
  const filtered = {};
  Object.entries(allStatusData).forEach(([campus, teachers]) => {
    const campusKey = campus.startsWith('SUB') ? 'SUB' : campus;
    const campusEn = (CAMPUS_EN[campusKey] || campusKey).toLowerCase();
    const campusMatch = !search || campus.toLowerCase().includes(search) || campusEn.includes(search) || (matchedKoCampus && campus === matchedKoCampus);
    const result = teachers.filter(t => {
      const matchSearch = !search || campusMatch || t.name.toLowerCase().includes(search) || t.id.toLowerCase().includes(search);
      if (!matchSearch) return false;
      if (completionFilter === 'done' && !t.allDone) return false;
      if (completionFilter === 'pending' && t.allDone) return false;
      return true;
    });
    if (result.length) filtered[campus] = result;
  });
  renderStatus(filtered);
}

// ── 캠퍼스 역검색 헬퍼 (영어코드 → 한글, 한글 → 영어코드 모두 매칭) ──
function matchesCampusSearch(q, campusKo, campusEn) {
  if (!q) return true;
  if (campusKo && campusKo.toLowerCase().includes(q)) return true;
  if (campusEn && campusEn.toLowerCase().includes(q)) return true;
  // 영어 코드로 한글 캠퍼스 역검색: e.g. "dae" → "Campus A"
  const enToKo = Object.fromEntries(Object.entries(CAMPUS_EN).map(([k, v]) => [v.toLowerCase(), k]));
  const matchedKo = enToKo[q];
  if (matchedKo && campusKo === matchedKo) return true;
  return false;
}
