// ── eval_v2 admin — Rendering & UI ──

// ── XSS-safe HTML escaping ──
function _escHtml(str) {
  if (!str) return '';
  return String(str).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}

// ── Manual eval data store (XSS-safe alternative to inline JSON) ──
const _manualEvData = {};

// ── Position Filter 패널 토글 ── (mutex 그룹 헬퍼는 admin_status_data.js)
function togglePositionFilter() {
  const panel = document.getElementById('positionFilterPanel');
  const chevron = document.getElementById('positionFilterChevron');
  const isOpen = panel.classList.contains('open');
  panel.classList.toggle('open');
  chevron.style.transform = isOpen ? '' : 'rotate(180deg)';
  if (!isOpen) _closeOtherFilterToggles('positionFilter');
}

// ── 직책 버튼 토글 (각 직책을 독립 드롭다운 섹션으로 펼침/접기) ──
function setTypeFilter(btn, type) {
  if (type === '') {
    // "All" 버튼: 모든 직책 섹션 닫고 캠퍼스 뷰로
    openTypeSections.clear();
    document.querySelectorAll('[data-filter-type]').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    const badge = document.getElementById('positionFilterBadge');
    if (badge) { badge.textContent = '0'; badge.classList.add('hidden'); }
    activeTypeFilter = '';
    applyFilters();
    return;
  }
  // 해당 직책 섹션 토글
  if (openTypeSections.has(type)) {
    openTypeSections.delete(type);
    btn.classList.remove('active');
  } else {
    openTypeSections.add(type);
    btn.classList.add('active');
  }
  document.querySelector('[data-filter-type=""]')?.classList.toggle('active', openTypeSections.size === 0);
  // 뱃지 업데이트
  const badge = document.getElementById('positionFilterBadge');
  if (badge) {
    badge.textContent = openTypeSections.size;
    badge.classList.toggle('hidden', openTypeSections.size === 0);
  }
  if (openTypeSections.size === 0) { activeTypeFilter = ''; applyFilters(); }
  else renderTypeAccordion();
}

// ── 직책별 드롭다운 섹션 렌더 ──
function renderTypeAccordion() {
  if (!statusLoaded) return;
  const search = document.getElementById('searchInput')?.value.trim().toLowerCase() || '';
  const enToKo = search ? Object.fromEntries(Object.entries(CAMPUS_EN).map(([k, v]) => [v.toLowerCase(), k])) : {};
  const matchedKoCampus = search ? (enToKo[search] || null) : null;
  const container = document.getElementById('typeAccordionWrap');
  container.innerHTML = '';
  // 캠퍼스뷰 숨기고 타입뷰 표시 (animation 재실행 위해 display 토글)
  document.getElementById('statusContent').style.display = 'none';
  container.style.display = 'none';
  requestAnimationFrame(() => { container.style.display = ''; });
  const TYPE_ORDER = ['position', 'regular', 'tl', 'sub', 'stl'];
  const allTypes = [...new Set(Object.values(allStatusData).flat().map(t => t.type))];
  const orderedTypes = TYPE_ORDER.filter(t => allTypes.includes(t)).concat(allTypes.filter(t => !TYPE_ORDER.includes(t)));
  orderedTypes.filter(type => openTypeSections.has(type)).forEach(type => {
    const teachers = [];
    Object.entries(allStatusData).forEach(([campus, campusTeachers]) => {
      const campusKey = campus.startsWith('SUB') ? 'SUB' : campus;
      const campusEn = CAMPUS_EN[campusKey] || campusKey;
      const campusMatch = !search || campus.toLowerCase().includes(search) || campusEn.toLowerCase().includes(search) || (matchedKoCampus && campus === matchedKoCampus);
      campusTeachers.filter(t => {
        if (t.type !== type) return false;
        if (completionFilter === 'done' && !t.allDone) return false;
        if (completionFilter === 'pending' && t.allDone) return false;
        return !search || campusMatch || t.name.toLowerCase().includes(search) || t.id.toLowerCase().includes(search);
      }).forEach(t => teachers.push({ ...t, campusEn }));
    });
    if (!teachers.length) return;
    const done = teachers.filter(t => t.allDone).length;
    const pct = teachers.length ? Math.round(done / teachers.length * 100) : 0;
    const typeLabel = EVAL_TYPES?.[type] || type.toUpperCase();
    const section = document.createElement('div'); section.className = 'campus-card';
    const hdr = document.createElement('div'); hdr.className = 'campus-header';
    hdr.innerHTML = `
      <div class="flex items-center gap-3">
        <span class="text-sm font-bold px-2.5 py-1 rounded-full ${TYPE_BADGE[type] || 'bg-gray-100 text-gray-700'}">${typeLabel}</span>
        <span class="text-xs font-bold px-2 py-0.5 rounded-full" style="background:var(--surface-low);color:var(--on-surface-variant)">${teachers.length}명</span>
      </div>
      <div class="flex items-center gap-3">
        <div class="hidden sm:flex items-center gap-2">
          <div class="w-28 h-2.5 rounded-full overflow-hidden" style="background:var(--outline-variant)">
            <div class="h-full rounded-full ${pct === 100 ? 'bg-emerald-500' : 'bg-[#B01116]'}" style="width:${pct}%"></div>
          </div>
          <span class="text-xs font-bold ${done === teachers.length ? 'text-emerald-600' : ''}" style="color:${done === teachers.length ? '' : 'var(--on-surface-variant)'}">${done}/${teachers.length}</span>
        </div>
        <span class="text-xs font-extrabold px-2.5 py-1 rounded-full ${pct === 100 ? 'bg-emerald-100 text-emerald-700' : 'text-[#B01116]'}" style="${pct !== 100 ? 'background:var(--primary-soft)' : ''}"><strong>${pct}%</strong></span>
        <i class="bi bi-chevron-down text-sm chevron-icon transition-transform" style="color:var(--outline);transform:rotate(180deg)"></i>
      </div>`;
    const bodyWrap = document.createElement('div'); bodyWrap.className = 'campus-body expanded';
    const body = document.createElement('div'); body.className = 'p-3 space-y-2';
    bodyWrap.appendChild(body);
    hdr.onclick = () => {
      const isOpen = bodyWrap.classList.contains('expanded');
      bodyWrap.classList.toggle('expanded');
      hdr.querySelector('.chevron-icon').style.transform = isOpen ? '' : 'rotate(180deg)';
    };
    teachers.sort((a, b) => (a.campusEn || '').localeCompare(b.campusEn || '') || a.name.localeCompare(b.name)).forEach(t => {
      const row = document.createElement('div'); row.className = `teacher-row ${t.allDone ? 'done' : ''}`;
      row.title = '클릭하여 세부 평가 내역 보기';
      const statusHtml = (t.status || []).map(s => makeStatusBadge(s.role, s.current, s.required, s.label)).join('');
      row.innerHTML = `
        <div class="teacher-info flex items-center gap-2 min-w-0">
          <span class="bulk-cb-wrap"><input type="checkbox" class="bulk-checkbox w-4 h-4" data-empid="${_escHtml(t.id)}" onclick="event.stopPropagation();toggleBulkSelect('${_escHtml(t.id)}')" /></span>
          <div class="w-8 h-8 rounded-full flex items-center justify-center flex-shrink-0" style="background:var(--primary-soft);border:2px solid var(--outline-variant)"><i class="bi bi-person-fill text-sm" style="color:var(--primary)"></i></div>
          <div class="min-w-0">
            <div class="flex items-center gap-1.5 flex-wrap">
              <span class="font-extrabold text-sm" style="color:var(--on-surface)">${_escHtml(t.name)}</span>
              <span class="text-xs font-mono" style="color:var(--outline)">${_escHtml(t.id.toUpperCase())}</span>
              <span class="text-xs font-bold px-1.5 py-0 rounded-full" style="background:var(--surface-low);color:var(--on-surface-variant);border:1px solid var(--outline-variant)">${_escHtml(t.campusEn)}</span>
            </div>
          </div>
        </div>
        <div class="teacher-status">
          ${statusHtml || '<span class="text-xs" style="color:var(--outline)">평가 없음</span>'}
          <i class="bi bi-chevron-right text-xs ml-1" style="color:var(--outline-variant)"></i>
        </div>`;
      row.onclick = (e) => {
        if (bulkMode) {
          if (!e.target.classList.contains('bulk-checkbox')) {
            toggleBulkSelect(t.id);
            const cb = row.querySelector('.bulk-checkbox');
            if (cb) cb.checked = bulkSelected.has(t.id);
          }
        } else openDetailModal(t);
      };
      body.appendChild(row);
    });
    section.appendChild(hdr); section.appendChild(bodyWrap); container.appendChild(section);
  });
}

// ── 캠퍼스별 렌더 (단일 컬럼, 각 카드 독립 드롭다운) ──
function renderStatus(data) {
  const container = document.getElementById('statusContent');
  container.innerHTML = ''; container.style.display = '';
  const merged = {};
  Object.entries(data).forEach(([campus, teachers]) => {
    const key = campus.startsWith('SUB') ? 'SUB' : campus;
    if (!merged[key]) merged[key] = [];
    merged[key].push(...teachers);
  });
  allTeachersFlat = Object.values(merged).flat();
  const sorted = Object.keys(merged).sort((a, b) => {
    const ia = CAMPUS_ORDER.indexOf(a), ib = CAMPUS_ORDER.indexOf(b);
    if (ia !== -1 && ib !== -1) return ia - ib;
    if (ia !== -1) return -1; if (ib !== -1) return 1;
    return a.localeCompare(b);
  });
  let totalTeachers = 0, totalDone = 0;
  sorted.forEach(campus => {
    const teachers = merged[campus];
    if (!teachers.length) return;
    totalTeachers += teachers.length;
    const campusDone = teachers.filter(t => t.allDone).length;
    totalDone += campusDone;
    const pct = Math.round(campusDone / teachers.length * 100);
    const campusEn = CAMPUS_EN[campus] || campus;
    const sorted2 = [...teachers].sort((a, b) => {
      if (campus === 'SUB') { if (a.type === 'stl' && b.type !== 'stl') return -1; if (b.type === 'stl' && a.type !== 'stl') return 1; }
      return 0;
    });
    const card = document.createElement('div'); card.className = 'campus-card';
    const hdr = document.createElement('div'); hdr.className = 'campus-header';
    const campusIds = sorted2.map(t => t.id);
    hdr.innerHTML = `
      <div class="flex items-center gap-2 min-w-0">
        <i class="bi bi-building text-sm flex-shrink-0" style="color:var(--primary)"></i>
        <span class="font-extrabold text-sm truncate" style="color:var(--on-surface)">${campusEn}</span>
        <span class="text-xs font-bold px-1.5 py-0.5 rounded-full flex-shrink-0" style="background:var(--surface-low);color:var(--on-surface-variant)">${teachers.length}</span>
        <button class="campus-bulk-btn text-xs font-bold px-2 py-0.5 flex-shrink-0 transition-colors" style="border:1.5px solid var(--border);border-radius:3px;color:var(--text);background:var(--surface)"><i class="bi bi-check2-square"></i> 전체 선택</button>
      </div>
      <div class="flex items-center gap-2 flex-shrink-0">
        <div class="flex items-center gap-2">
          <div class="w-28 h-2 rounded-full overflow-hidden" style="background:var(--outline-variant)">
            <div class="h-full rounded-full ${pct === 100 ? 'bg-emerald-500' : 'bg-[#B01116]'}" style="width:${pct}%"></div>
          </div>
          <span class="text-xs font-extrabold ${pct === 100 ? 'text-emerald-600' : 'text-[#B01116]'}">${campusDone}/${teachers.length}</span>
        </div>
        <span class="text-xs font-extrabold px-2.5 py-1 rounded-full ${pct === 100 ? 'bg-emerald-100 text-emerald-700' : 'text-[#B01116]'}" style="${pct !== 100 ? 'background:var(--primary-soft)' : ''}">${pct}%</span>
        <i class="bi bi-chevron-down text-xs chevron-icon transition-transform" style="color:var(--outline)"></i>
      </div>`;
    // 외부 래퍼(overflow/max-height 담당) + 내부 div(padding 담당) → 접혔을 때 패딩 노출 방지
    const bodyWrap = document.createElement('div'); bodyWrap.className = 'campus-body';
    const body = document.createElement('div'); body.className = 'p-3 space-y-2';
    bodyWrap.appendChild(body);
    hdr.onclick = (e) => {
      if (e.target.closest('.campus-bulk-btn')) return;
      const isOpen = bodyWrap.classList.contains('expanded');
      bodyWrap.classList.toggle('expanded');
      hdr.querySelector('.chevron-icon').style.transform = isOpen ? '' : 'rotate(180deg)';
    };
    // campus-bulk-btn은 항상 DOM에 존재, CSS .bulk-mode로 표시/숨김
    const bulkBtn = hdr.querySelector('.campus-bulk-btn');
    if (bulkBtn) { bulkBtn.addEventListener('click', (e) => { e.stopPropagation(); toggleCampusBulk(campusIds, bulkBtn); }); }
    sorted2.forEach(t => {
      const row = document.createElement('div'); row.className = `teacher-row ${t.allDone ? 'done' : ''}`;
      row.title = '클릭하여 세부 평가 내역 보기';
      const statusHtml = (t.status || []).map(s => makeStatusBadge(s.role, s.current, s.required, s.label)).join('');
      row.innerHTML = `
        <div class="teacher-info flex items-center gap-2 min-w-0">
          <span class="bulk-cb-wrap"><input type="checkbox" class="bulk-checkbox w-4 h-4" data-empid="${_escHtml(t.id)}" onclick="event.stopPropagation();toggleBulkSelect('${_escHtml(t.id)}')" /></span>
          <div class="w-8 h-8 rounded-full flex items-center justify-center flex-shrink-0" style="background:var(--primary-soft);border:2px solid var(--outline-variant)"><i class="bi bi-person-fill text-sm" style="color:var(--primary)"></i></div>
          <div class="min-w-0">
            <div class="flex items-center gap-1.5 flex-wrap">
              <span class="font-extrabold text-sm" style="color:var(--on-surface)">${_escHtml(t.name)}</span>
              <span class="text-xs font-mono" style="color:var(--outline)">${_escHtml(t.id.toUpperCase())}</span>
              <span class="text-[11px] font-bold px-1.5 py-0.5 rounded-full ${TYPE_BADGE[t.type] || 'bg-gray-100 text-gray-700'}">${_escHtml(t.typeLabel || t.type)}</span>
            </div>
          </div>
        </div>
        <div class="teacher-status">
          ${statusHtml || '<span class="text-xs" style="color:var(--outline)">평가 없음</span>'}
          <i class="bi bi-chevron-right text-xs ml-1" style="color:var(--outline-variant)"></i>
        </div>`;
      row.onclick = (e) => {
        if (bulkMode) {
          if (!e.target.classList.contains('bulk-checkbox')) {
            toggleBulkSelect(t.id);
            const cb = row.querySelector('.bulk-checkbox');
            if (cb) cb.checked = bulkSelected.has(t.id);
          }
        } else openDetailModal(t);
      };
      body.appendChild(row);
    });
    card.appendChild(hdr); card.appendChild(bodyWrap); container.appendChild(card);
  });
  const summary = document.createElement('div');
  const totalPct = totalTeachers ? Math.round(totalDone / totalTeachers * 100) : 0;
  summary.className = 'rounded-2xl px-6 py-5 mb-2 flex flex-wrap gap-8 items-center shadow-sm border-2';
  summary.style.cssText = 'background:var(--surface-lowest);border-color:var(--outline-variant)';
  summary.innerHTML = `
    <div class="flex items-center gap-3"><div class="w-10 h-10 rounded-xl flex items-center justify-center" style="background:var(--primary-soft)"><i class="bi bi-people-fill text-lg" style="color:var(--primary)"></i></div><div><p class="text-xs uppercase tracking-wider font-bold" style="color:var(--on-surface-variant)">전체</p><p class="text-2xl font-extrabold" style="color:var(--on-surface)">${totalTeachers}</p></div></div>
    <div class="flex items-center gap-3"><div class="w-10 h-10 bg-emerald-50 rounded-xl flex items-center justify-center"><i class="bi bi-check-circle-fill text-emerald-600 text-lg"></i></div><div><p class="text-xs uppercase tracking-wider font-bold" style="color:var(--on-surface-variant)">완료</p><p class="text-2xl font-extrabold text-emerald-600">${totalDone}</p></div></div>
    <div class="flex-1 min-w-40"><div class="flex justify-between text-xs font-bold mb-1" style="color:var(--outline)"><span>진행률</span><span>${totalDone}/${totalTeachers} · ${totalPct}%</span></div><div class="h-3 rounded-full overflow-hidden border" style="background:var(--surface-low);border-color:var(--outline-variant)"><div class="h-full rounded-full transition-all ${totalPct === 100 ? 'bg-emerald-500' : 'bg-[#B01116]'}" style="width:${totalPct}%"></div></div></div>`;
  container.insertBefore(summary, container.firstChild);
}

// ── 역할 평균 렌더 ──
// 서버 select_effective_responses (report_service.py:17-51) 와 동일 규칙으로
// dedup 한 effective 응답만 평균 산출 — 모달 상단 평균 ↔ 서버 보고서 평균 일치.
function renderRoleAverages(body, responses, weights, teacher) {
  const byKey = {};
  responses.forEach(r => {
    if (r.is_test) return;
    const role = r.rater_role || '';
    const key = r.is_manual
      ? `${role}__manual__${r.doc_id}`
      : `${role}__${_normRater(r.rater_name)}`;
    const existing = byKey[key];
    if (!existing || String(r.submitted_at || '') > String(existing.submitted_at || '')) {
      byKey[key] = r;
    }
  });
  const effective = Object.values(byKey);

  const roleScores = {}; const roleHasManual = {};
  effective.forEach(ev => {
    const role = ev.rater_role;
    if (!roleScores[role]) roleScores[role] = { sum: 0, count: 0 };
    const vals = Object.values(ev.scores || {}).map(Number).filter(v => v > 0);
    if (vals.length) { roleScores[role].sum += vals.reduce((a, b) => a + b, 0) / vals.length; roleScores[role].count++; }
    if (ev.is_manual) roleHasManual[role] = true;
  });
  if (!Object.keys(roleScores).length) return;
  const hasAnyManual = effective.some(ev => ev.is_manual);
  if (hasAnyManual) {
    const banner = document.createElement('div');
    banner.className = 'flex items-start gap-2.5 bg-amber-50 border-2 border-amber-200 rounded-xl px-4 py-3 mb-3 text-xs font-bold text-amber-700';
    banner.innerHTML = `<i class="bi bi-exclamation-triangle-fill text-amber-500 mt-0.5 flex-shrink-0"></i><div><p class="font-extrabold text-amber-800 mb-0.5">관리자 점수 포함</p><p class="font-medium text-amber-600">이 평가에는 관리자가 수동으로 입력한 점수가 포함되어 있습니다: ${Object.keys(roleHasManual).join(', ')}</p></div>`;
    body.appendChild(banner);
  }
  const roleKeys = Object.keys(roleScores);
  let weightedSum = 0, weightTotal = 0;
  roleKeys.forEach(role => {
    const avg = roleScores[role].count ? roleScores[role].sum / roleScores[role].count : 0;
    const w = weights[role] != null ? parseFloat(weights[role]) : (1 / roleKeys.length);
    weightedSum += avg * w; weightTotal += w;
  });
  const totalAvg = weightTotal > 0 ? weightedSum / weightTotal : 0;
  const wrap = document.createElement('div');
  wrap.className = 'flex flex-wrap gap-2 py-3 border-b-2'; wrap.style.borderColor = 'var(--outline-variant)';
  Object.entries(roleScores).forEach(([role, data]) => {
    const avg = data.count ? data.sum / data.count : 0;
    const w = weights[role] != null ? Math.round(parseFloat(weights[role]) * 100) : null;
    const color = avg >= 4 ? 'bg-emerald-50 border-emerald-200 text-emerald-700' : avg >= 3 ? 'bg-blue-50 border-blue-200 text-blue-700' : 'bg-red-50 border-red-200 text-red-700';
    const chip = document.createElement('div');
    chip.className = `flex items-center gap-1.5 px-3 py-1.5 rounded-xl border-2 text-xs font-bold ${color}`;
    const _roleLbl = (typeof dybRoleLabel === 'function') ? dybRoleLabel(role) : role;
    chip.innerHTML = `<i class="bi bi-bar-chart-fill opacity-70"></i> ${_escHtml(_roleLbl)}${w != null ? ` <span class="opacity-50 font-medium">${w}%</span>` : ''} <span class="font-extrabold">${avg.toFixed(1)}</span>${roleHasManual[role] ? '<i class="bi bi-exclamation-triangle-fill text-amber-400 ml-1"></i>' : ''}`;
    wrap.appendChild(chip);
  });
  const totalChip = document.createElement('div');
  totalChip.className = 'flex items-center gap-1.5 px-3 py-1.5 rounded-xl border-2 text-xs font-bold ml-auto';
  totalChip.style.cssText = 'background:var(--surface-low);border-color:var(--outline-variant);color:var(--on-surface-variant)';
  totalChip.innerHTML = `<i class="bi bi-trophy-fill opacity-70"></i> 종합 <span class="font-extrabold">${totalAvg.toFixed(1)}</span>`;
  wrap.appendChild(totalChip);
  body.appendChild(wrap);
}

// ── 응답 목록 렌더 ──
// 동명이인/같은 평가자 재제출 시각 표시:
//   - 같은 (역할, 정규화 rater_name) 그룹의 submitted_at 최신 1건 = ⚠ "동명이인 가능성 (평균 채택)"
//   - 그 외 = ⛔ "평균 미채택" + opacity 0.6
//   서버 평균 산출은 select_effective_responses() 가 같은 규칙으로 처리 → UI 와 일치.
function _normRater(name) {
  return String(name || '').trim().toLowerCase().normalize('NFC');
}
function renderResponses(body, responses, questionsMap, openQuestionsMap, teacher) {
  const byRole = {};
  responses.forEach(r => { if (!byRole[r.rater_role]) byRole[r.rater_role] = []; byRole[r.rater_role].push(r); });
  Object.entries(byRole).forEach(([role, evals]) => {
    // 같은 정규화 이름의 등장 횟수 (test/manual 제외)
    const nameCounts = {};
    evals.forEach(e => {
      if (e.is_test || e.is_manual) return;
      const k = _normRater(e.rater_name);
      if (!k) return;
      nameCounts[k] = (nameCounts[k] || 0) + 1;
    });
    const dupNames = new Set(Object.entries(nameCounts).filter(([_,c]) => c >= 2).map(([k]) => k));
    // submitted_at 내림차순 정렬 — 최신본이 위. 같은 그룹의 첫 등장이 채택본.
    evals.sort((a, b) => String(b.submitted_at || '').localeCompare(String(a.submitted_at || '')));
    const seenLatest = new Set();
    const section = document.createElement('div');
    section.className = 'rounded-2xl border-2 overflow-hidden'; section.style.cssText = 'background:var(--surface-low);border-color:var(--outline-variant)';
    const roleStatus = teacher?.status?.find(s => s.role === role);
    // effective 카운트: dup 그룹은 1로 합산
    const effectiveCount = evals.filter(e => !e.is_test).length - Math.max(0, Array.from(dupNames).reduce((acc, k) => acc + (nameCounts[k] - 1), 0));
    const needMore = roleStatus && (effectiveCount < roleStatus.required);
    const dupBadge = dupNames.size > 0
      ? `<span class="text-[10px] font-extrabold bg-amber-100 text-amber-600 border border-amber-200 px-2 py-0.5 rounded-full" title="같은 이름의 평가자가 ${dupNames.size}그룹 — 동명이인 또는 재제출. 최신 1건만 평균에 포함.">⚠ 동명이인 ${dupNames.size}건</span>`
      : '';
    const _sectionRoleLbl = (typeof dybRoleLabel === 'function') ? dybRoleLabel(role) : role;
    section.innerHTML = `<div class="detail-role-header expanded flex items-center justify-between px-4 py-3 border-b-2" style="background:var(--surface-lowest);border-color:var(--outline-variant)"><div class="flex items-center gap-2"><span class="w-2 h-6 rounded-full" style="background:var(--primary)"></span><span class="font-extrabold" style="color:var(--on-surface)">${_escHtml(_sectionRoleLbl)}</span><span class="text-xs font-bold px-2 py-0.5 rounded-full" style="background:var(--surface-low);color:var(--on-surface-variant)">${evals.length}명</span>${dupBadge}</div><div class="flex items-center gap-2">${needMore ? `<button onclick="openManualInputModal('${role}')" class="flex items-center gap-1 text-[11px] font-bold px-3 py-1.5 rounded-lg border-2 border-orange-300 text-orange-600 hover:bg-orange-50 transition-colors"><i class="bi bi-plus-circle-fill"></i> 추가</button>` : ''}${!needMore && roleStatus ? `<button onclick="openManualInputModal('${role}')" class="flex items-center gap-1 text-[11px] font-bold px-3 py-1.5 rounded-lg border transition-colors" style="border-color:var(--outline-variant);color:var(--outline)"><i class="bi bi-plus-circle"></i> 추가</button>` : ''}<i class="bi bi-chevron-down chevron-icon text-sm" style="color:var(--outline)"></i></div></div>`;
    const roleHeader = section.firstElementChild;
    const roleBodyWrap = document.createElement('div');
    roleBodyWrap.className = 'detail-role-body expanded';
    const roleBodyInner = document.createElement('div');
    roleBodyWrap.appendChild(roleBodyInner);
    section.appendChild(roleBodyWrap);
    roleHeader.addEventListener('click', (e) => {
      if (e.target.closest('button')) return;
      const isOpen = roleBodyWrap.classList.toggle('expanded');
      roleHeader.classList.toggle('expanded', isOpen);
    });
    evals.forEach((ev, idx) => {
      const evalDiv = document.createElement('div');
      evalDiv.className = `p-4 ${idx < evals.length - 1 ? 'border-b' : ''}`; evalDiv.style.borderColor = 'var(--outline-variant)';
      const isTest = ev.is_test || false;
      // 동명이인 분류
      const k = _normRater(ev.rater_name);
      const isInDup = dupNames.has(k) && !isTest && !ev.is_manual;
      const isLatestOfGroup = isInDup && !seenLatest.has(k);
      if (isInDup) seenLatest.add(k);
      const isShadowed = isInDup && !isLatestOfGroup;
      if (isShadowed) evalDiv.style.opacity = '0.55';
      const dupRowBadge = isLatestOfGroup
        ? '<span class="text-[10px] font-extrabold bg-amber-100 text-amber-600 border border-amber-200 px-2 py-0.5 rounded-full" title="같은 이름의 평가자 응답이 또 있음 — 이 응답이 최신이라 평균에 채택됨.">⚠ 동명이인?</span>'
        : (isShadowed ? '<span class="text-[10px] font-extrabold bg-slate-200 text-slate-500 border border-slate-300 px-2 py-0.5 rounded-full" title="같은 이름의 더 최근 응답이 있어 자동으로 평균에서 제외됨.">⛔ 평균 미채택</span>' : '');
      const evalHeader = document.createElement('div');
      evalHeader.className = 'detail-eval-header flex items-center justify-between';
      evalHeader.innerHTML = `
        <div class="flex items-center gap-2">
          <div class="w-7 h-7 rounded-full flex items-center justify-center" style="background:var(--primary-soft);border:1px solid var(--outline-variant)"><i class="bi bi-person-fill text-xs" style="color:var(--primary)"></i></div>
          <span class="font-bold text-sm" style="color:var(--on-surface);min-width:80px;max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;display:inline-block">${_escHtml(ev.rater_name)}</span>
          ${dupRowBadge}
          ${isTest ? '<span class="text-[10px] font-extrabold bg-amber-100 text-amber-600 border border-amber-200 px-2 py-0.5 rounded-full">TEST</span>' : ''}
          ${ev.is_manual ? (ev.promoted_from_self_submit
            ? `<span class="text-[10px] font-extrabold bg-emerald-100 text-emerald-700 border border-emerald-200 px-2 py-0.5 rounded-full" title="Self-submit 응답이 동명이인 dedup 정정 차원에서 수동 채택으로 승격됨.">↑ Promoted</span>`
            : `<span class="text-[10px] font-extrabold bg-orange-100 text-orange-600 border border-orange-200 px-2 py-0.5 rounded-full" title="${_escHtml(ev.manual_reason)}">Admin</span>`
          ) : ''}
        </div>
        <div class="flex items-center gap-2">
          <span class="text-xs" style="color:var(--outline)">${ev.submitted_at || ''}</span>
          ${ev.is_manual ? ((_manualEvData[ev.doc_id] = ev), `<button onclick="openEditManualModal('${_escHtml(ev.doc_id)}','${_escHtml(role)}',_manualEvData['${_escHtml(ev.doc_id)}'])" title="Edit" class="icon-action-btn text-[10px] font-bold px-2 py-1 rounded-lg border transition-colors hover:text-orange-500 hover:border-orange-300" style="border-color:var(--outline-variant);color:var(--outline)"><i class="bi bi-pencil-fill"></i></button>`) : ''}
          <button onclick="toggleTestMark('${ev.doc_id}',${!isTest},this)" title="${isTest ? 'Remove test mark' : 'Mark as test'}" class="icon-action-btn text-[10px] font-bold px-2 py-1 rounded-lg border transition-colors ${isTest ? 'border-amber-300 text-amber-600 hover:bg-amber-50' : 'hover:text-amber-500 hover:border-amber-300'}" style="${!isTest ? 'border-color:var(--outline-variant);color:var(--outline)' : ''}">
            <i class="bi bi-flag${isTest ? '-fill' : ''}"></i>
          </button>
          ${isShadowed ? `<button onclick="promoteResponse('${ev.doc_id}',this)" title="수동 채택으로 변환 — 평균에 포함시키기" class="icon-action-btn text-[10px] font-bold px-2 py-1 rounded-lg border transition-colors hover:text-emerald-600 hover:border-emerald-300" style="border-color:var(--outline-variant);color:var(--outline)"><i class="bi bi-arrow-up-circle-fill"></i></button>` : ''}
          ${ev.promoted_from_self_submit ? `<button onclick="depromoteResponse('${ev.doc_id}',this)" title="수동 채택 해제 — self-submit 으로 되돌리기" class="icon-action-btn text-[10px] font-bold px-2 py-1 rounded-lg border transition-colors hover:text-amber-600 hover:border-amber-300" style="border-color:var(--outline-variant);color:var(--outline)"><i class="bi bi-arrow-down-circle-fill"></i></button>` : ''}
          <button onclick="deleteResponse('${ev.doc_id}',this)" title="Delete response" class="icon-action-btn text-[10px] font-bold px-2 py-1 rounded-lg border transition-colors hover:text-red-500 hover:border-red-300" style="border-color:var(--outline-variant);color:var(--outline)">
            <i class="bi bi-trash3-fill"></i>
          </button>
          <i class="bi bi-chevron-down chevron-icon text-xs" style="color:var(--outline)"></i>
        </div>`;
      evalDiv.appendChild(evalHeader);
      const detailsWrap = document.createElement('div');
      detailsWrap.className = 'detail-eval-details';
      const detailsInner = document.createElement('div');
      detailsWrap.appendChild(detailsInner);
      evalDiv.appendChild(detailsWrap);
      evalHeader.addEventListener('click', (e) => {
        if (e.target.closest('button')) return;
        const isOpen = detailsWrap.classList.toggle('expanded');
        evalHeader.classList.toggle('expanded', isOpen);
      });
      const scores = ev.scores || {}; const scoreKeys = Object.keys(scores);
      if (scoreKeys.length) {
        const scoreGrid = document.createElement('div'); scoreGrid.className = 'space-y-2 mb-3';
        scoreKeys.forEach((qId, qi) => {
          const score = scores[qId];
          const qText = (questionsMap[role] && questionsMap[role][qi]) ? questionsMap[role][qi].ko : qId;
          const qTextEn = (questionsMap[role] && questionsMap[role][qi]) ? questionsMap[role][qi].en : '';
          const scoreRow = document.createElement('div'); scoreRow.className = 'flex items-start gap-2.5';
          scoreRow.innerHTML = `<span class="text-xs font-bold w-6 flex-shrink-0 pt-0.5" style="color:var(--outline)">Q${qi + 1}</span><span class="score-badge s${score}">${score}</span><div class="flex-1 min-w-0"><p class="text-xs font-medium leading-snug" style="color:var(--on-surface)">${_escHtml(qText)}</p>${qTextEn ? `<p class="text-xs leading-snug mt-0.5" style="color:var(--outline)">${_escHtml(qTextEn)}</p>` : ''}</div>`;
          scoreGrid.appendChild(scoreRow);
        });
        detailsInner.appendChild(scoreGrid);
      }
      if (ev.comment_en || ev.comment_ko) {
        const comment = document.createElement('div'); comment.className = 'rounded-xl border p-3 mt-2'; comment.style.cssText = 'background:var(--surface-lowest);border-color:var(--outline-variant)';
        comment.innerHTML = `<p class="text-[10px] font-bold uppercase tracking-wider mb-1" style="color:var(--outline)">코멘트</p>${ev.comment_en ? `<p class="text-sm leading-relaxed" style="color:var(--on-surface)">${_escHtml(ev.comment_en)}</p>` : ''}${ev.comment_ko ? `<p class="text-sm leading-relaxed mt-1.5 pt-1.5 border-t" style="color:var(--on-surface-variant);border-color:var(--outline-variant)">${_escHtml(ev.comment_ko)}</p>` : ''}`;
        detailsInner.appendChild(comment);
      }
      const openAnswers   = ev.open_answers    || {};
      const openAnswersKo = ev.open_answers_ko  || {};
      const openAnswersEn = ev.open_answers_en  || {};
      const tmplStatus    = ev.translation_status || 'skipped';
      const openQList = (openQuestionsMap && openQuestionsMap[role]) || [];
      if (openQList.length && Object.keys(openAnswers).length) {
        const oqBlock = document.createElement('div'); oqBlock.className = 'mt-3 space-y-2';

        // 헤더: 제목 + 번역 상태 배지 / 버튼
        const oqHeader = document.createElement('div'); oqHeader.className = 'flex items-center justify-between mb-1.5';
        let statusBadge = '';
        if      (tmplStatus === 'pending') statusBadge = '<span class="text-[10px] font-bold bg-yellow-100 text-yellow-700 border border-yellow-200 px-1.5 py-0.5 rounded-full flex items-center gap-1"><span class="w-2 h-2 rounded-full bg-yellow-400 animate-pulse inline-block"></span>Translating...</span>';
        else if (tmplStatus === 'done')    statusBadge = '<span class="text-[10px] font-bold bg-green-100 text-green-700 border border-green-200 px-1.5 py-0.5 rounded-full">KO · EN ✓</span>';
        else if (tmplStatus === 'failed' || tmplStatus === 'skipped')
          statusBadge = `<button onclick="retranslateResponse('${ev.doc_id}',this)" class="text-[10px] font-bold bg-slate-100 text-slate-600 border border-slate-200 px-2 py-0.5 rounded-full hover:bg-blue-50 hover:text-blue-600 hover:border-blue-200 transition-colors"><i class="bi bi-translate mr-0.5"></i>Translate (KO · EN)</button>`;
        oqHeader.innerHTML = `<p class="text-[10px] font-extrabold text-blue-400 uppercase tracking-wider"><i class="bi bi-pencil-square me-1"></i> Open-ended <span class="opacity-60">서술형</span></p>${statusBadge}`;
        oqBlock.appendChild(oqHeader);

        openQList.forEach((oq, oqi) => {
          const ans   = openAnswers[oq.id]   || ''; if (!ans) return;
          const ansKo = openAnswersKo[oq.id] || '';
          const ansEn = openAnswersEn[oq.id] || '';
          const oqRow = document.createElement('div'); oqRow.className = 'bg-blue-50 rounded-xl border border-blue-100 p-3 space-y-2';

          // 원문 + 문항 텍스트
          let html = `<p class="text-xs font-bold text-blue-600 mb-0.5"><span class="text-[10px] font-extrabold bg-blue-100 border border-blue-200 px-1.5 py-0.5 rounded-full mr-1">OQ${oqi + 1}</span>${_escHtml(oq.ko)}</p>`;
          if (oq.en) html += `<p class="text-[10px] text-blue-300 italic mb-1">${_escHtml(oq.en)}</p>`;
          html += `<p class="text-sm leading-relaxed whitespace-pre-wrap" style="color:var(--on-surface)">${_escHtml(ans)}</p>`;

          // 번역 섹션 (MASTER 전용) — 항상 표시
          html += `<div class="border-t border-blue-100 pt-2 mt-1">`;

          if (tmplStatus === 'pending' && !ansKo && !ansEn) {
            html += `<p class="text-xs italic" style="color:var(--outline)">번역 처리 중...</p>`;
          } else if ((tmplStatus === 'skipped' || tmplStatus === 'failed') && !ansKo && !ansEn) {
            html += `<p class="text-xs italic" style="color:var(--outline)">위 버튼을 눌러 번역하세요.</p>`;
          } else {
            // KO / EN 나란히 표시
            html += `<div class="grid grid-cols-2 gap-3">`;

            // Korean
            html += `<div>`;
            html += `<div class="flex items-center justify-between mb-0.5">`;
            html += `  <p class="text-[10px] font-bold text-blue-400 uppercase tracking-wider">Korean</p>`;
            if (tmplStatus === 'done' || ansKo)
              html += `  <button onclick="openTranslationEdit('${ev.doc_id}','${oq.id}','ko',this)" class="text-[10px] font-bold px-1.5 py-0.5 rounded border transition-colors hover:text-blue-600 hover:border-blue-300" style="color:var(--outline);border-color:var(--outline-variant)"><i class="bi bi-pencil-fill"></i></button>`;
            html += `</div>`;
            html += `<p class="text-sm leading-relaxed whitespace-pre-wrap translation-text" data-doc="${ev.doc_id}" data-qid="${oq.id}" data-lang="ko" style="color:var(--on-surface-variant)">${ansKo ? _escHtml(ansKo) : '<span class="italic opacity-40 text-xs">—</span>'}</p>`;
            html += `</div>`;

            // English
            html += `<div>`;
            html += `<div class="flex items-center justify-between mb-0.5">`;
            html += `  <p class="text-[10px] font-bold text-blue-400 uppercase tracking-wider">English</p>`;
            if (tmplStatus === 'done' || ansEn)
              html += `  <button onclick="openTranslationEdit('${ev.doc_id}','${oq.id}','en',this)" class="text-[10px] font-bold px-1.5 py-0.5 rounded border transition-colors hover:text-blue-600 hover:border-blue-300" style="color:var(--outline);border-color:var(--outline-variant)"><i class="bi bi-pencil-fill"></i></button>`;
            html += `</div>`;
            html += `<p class="text-sm leading-relaxed whitespace-pre-wrap translation-text" data-doc="${ev.doc_id}" data-qid="${oq.id}" data-lang="en" style="color:var(--on-surface-variant)">${ansEn ? _escHtml(ansEn) : '<span class="italic opacity-40 text-xs">—</span>'}</p>`;
            html += `</div>`;

            html += `</div>`; // grid
          }
          html += `</div>`; // border-t
          oqRow.innerHTML = html;
          oqBlock.appendChild(oqRow);
        });
        if (oqBlock.children.length > 1) detailsInner.appendChild(oqBlock);
      }
      roleBodyInner.appendChild(evalDiv);
    });
    body.appendChild(section);
  });
}
