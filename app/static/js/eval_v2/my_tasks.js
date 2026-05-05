// ── My Tasks — GS/TL/STL/admin 평가 진입 dashboard ───────────────────────────
// PORTAL_ME, IS_ADMIN_ME, EVAL_TYPE_LABELS 는 my_tasks.html 가 inject.

let mtCurrentSessionId = '';
let mtSessions = [];
let mtViewAs = { campus: '', role: '' };  // admin only
let _mtSessionsReqSeq = 0;  // race 가드 — admin 이 dropdown 빠르게 변경 시 stale 응답 폐기
let _mtListReqSeq = 0;
let mtCurrentTeachers = [];  // 마지막 fetch 결과 캐시 (필터 적용용)
let mtTypeFilter = '';       // eval_type 필터 — 세션 변경해도 유지

// ── 페이지 간 state 복원 (form 진입 후 Back to My Tasks 로 돌아올 때) ─────────
// sessionStorage 사용 (탭 닫으면 사라짐, 다른 탭 영향 없음).
const _MT_STATE_KEY = 'dyb_mt_state_v1';
function _mtSaveState() {
  try {
    sessionStorage.setItem(_MT_STATE_KEY, JSON.stringify({
      viewAs: mtViewAs,
      typeFilter: mtTypeFilter,
      sessionId: mtCurrentSessionId,
    }));
  } catch (e) {}
}
function _mtLoadState() {
  try {
    const raw = sessionStorage.getItem(_MT_STATE_KEY);
    if (!raw) return null;
    return JSON.parse(raw);
  } catch (e) { return null; }
}

function _mtEsc(s) {
  return String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}
function _mtShowToast(msg, type) {
  if (typeof showToast === 'function') showToast(msg, type || 'info');
  else console.log('[toast]', type, msg);
}
function _mtFetch(url, body) {
  return fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
    body: JSON.stringify(body || {}),
  }).then(r => r.json());
}
function _mtViewAsBody() {
  const out = {};
  if (IS_ADMIN_ME && mtViewAs.campus) out.as_campus = mtViewAs.campus;
  if (IS_ADMIN_ME && mtViewAs.role) out.as_role = mtViewAs.role;
  return out;
}

// ── Admin view-as ────────────────────────────────────────────────────────────
// 한글 캠퍼스명 → 영문 코드. CAMPUS_EN 매핑 없으면 한글 그대로.
function _mtCampusCode(campusKo) {
  if (!campusKo) return '';
  if (typeof CAMPUS_EN !== 'undefined' && CAMPUS_EN[campusKo]) return CAMPUS_EN[campusKo];
  return campusKo;
}

function _mtPopulateCampusSelect() {
  if (!IS_ADMIN_ME) return;
  const sel = document.getElementById('mtViewAsCampus');
  if (!sel) return;
  const order = (typeof CAMPUS_ORDER !== 'undefined' && Array.isArray(CAMPUS_ORDER)) ? CAMPUS_ORDER : [];
  order.forEach(c => {
    const opt = document.createElement('option');
    opt.value = c;  // value 는 한글 (서버 검증과 일치)
    opt.textContent = _mtCampusCode(c);  // 표시는 영문 코드
    sel.appendChild(opt);
  });
}

// View-as role select 옵션 동적 채우기 — portal_roles 의 label 표시.
// value 는 raw role 이름 (서버 검증·매칭 정합성), text 는 label.
// retired/MASTER/admin 은 평가 시점 시뮬레이션 의미가 없어 제외 (View-as 는 GS/TL/STL/NET 등 평가 가능 role 만).
async function _mtPopulateRoleSelect() {
  if (!IS_ADMIN_ME) return;
  const sel = document.getElementById('mtViewAsRole');
  if (!sel) return;
  if (typeof window.dybLoadRoleLabels !== 'function') return;
  try {
    const roles = await window.dybLoadRoleLabels();
    if (!Array.isArray(roles) || !roles.length) return;
    // admin/MASTER/retired/퇴사 제외 + deprecated 제외 — View-as 후보는 평가 가능 role 만
    const _SKIP = new Set(['admin', 'MASTER', 'retired', '퇴사']);
    const candidates = roles.filter(r => !r.deprecated && !_SKIP.has(r.name));
    // 기존 hardcoded option 제거 (— role — placeholder 제외)
    const placeholder = sel.querySelector('option[value=""]');
    sel.innerHTML = '';
    if (placeholder) sel.appendChild(placeholder);
    else {
      const ph = document.createElement('option');
      ph.value = ''; ph.textContent = '— role —';
      sel.appendChild(ph);
    }
    candidates.forEach(r => {
      const opt = document.createElement('option');
      opt.value = r.name;
      opt.textContent = r.label || r.name;
      sel.appendChild(opt);
    });
    // 복원된 mtViewAs.role 이 dropdown 후보에 있으면 selected 동기화
    if (mtViewAs && mtViewAs.role) sel.value = mtViewAs.role;
  } catch (_) { /* hardcoded fallback 유지 */ }
}
// dropdown 변경 시 자동 apply — Apply 버튼 없이 즉시 반영
function mtAutoApplyViewAs() {
  const role = (document.getElementById('mtViewAsRole')?.value || '').trim();
  const campus = (document.getElementById('mtViewAsCampus')?.value || '').trim();
  // 둘 다 비어있으면 admin 본인 시점 복귀, 아니면 즉시 view-as
  mtViewAs = { role, campus };
  _mtUpdateViewAsBadge();
  _mtUpdateViewAsTimingChip();
  _mtSaveState();
  loadMtSessions();
}
function mtClearViewAs() {
  mtViewAs = { role: '', campus: '' };
  const r = document.getElementById('mtViewAsRole'); if (r) r.value = '';
  const c = document.getElementById('mtViewAsCampus'); if (c) c.value = '';
  _mtUpdateViewAsBadge();
  _mtUpdateViewAsTimingChip();
  _mtSaveState();
  loadMtSessions();
}
function _mtUpdateViewAsBadge() {
  const b = document.getElementById('mtViewAsBadge'); if (!b) return;
  if (mtViewAs.role || mtViewAs.campus) {
    b.style.display = 'inline';
    const campusCode = _mtCampusCode(mtViewAs.campus);
    const roleLbl = mtViewAs.role
      ? ((typeof dybRoleLabel === 'function') ? dybRoleLabel(mtViewAs.role) : mtViewAs.role)
      : '(any role)';
    b.textContent = `Viewing as: ${roleLbl} @ ${campusCode || '(any campus)'}`;
  } else {
    b.style.display = 'none';
  }
}
// sessions 영역 옆 chip — admin 본인 시점 vs view-as 시점 시각 구분.
// admin 만 보임 (IS_ADMIN_ME=true). 일반 GS/TL 사용자에겐 chip 자체 비표시.
function _mtUpdateViewAsTimingChip() {
  const c = document.getElementById('mtViewAsTimingChip'); if (!c) return;
  if (!IS_ADMIN_ME) { c.style.display = 'none'; return; }
  c.style.display = 'inline-block';
  if (mtViewAs.role || mtViewAs.campus) {
    const campusCode = _mtCampusCode(mtViewAs.campus);
    const roleLbl = mtViewAs.role
      ? ((typeof dybRoleLabel === 'function') ? dybRoleLabel(mtViewAs.role) : mtViewAs.role)
      : '(any role)';
    c.textContent = `as: ${roleLbl}${campusCode ? ' @ ' + campusCode : ''}`;
    c.style.cssText = 'display:inline-block;background:var(--primary);color:#fff;padding:2px 8px;font-size:10px;font-weight:800;border-radius:9999px;letter-spacing:.03em;';
  } else {
    const ownRole = window.PORTAL_ME?.role || 'admin';
    const ownLbl = (typeof dybRoleLabel === 'function') ? dybRoleLabel(ownRole) : ownRole;
    c.textContent = `as: ${ownLbl} (your role)`;
    c.style.cssText = 'display:inline-block;background:var(--surface-low);color:var(--text-muted);padding:2px 8px;font-size:10px;font-weight:800;border-radius:9999px;border:1px solid var(--outline-variant);';
  }
}

// ── 세션 로드 ────────────────────────────────────────────────────────────────
async function loadMtSessions() {
  const myToken = ++_mtSessionsReqSeq;  // 가장 최근 요청만 렌더 — view-as 빠른 변경 시 stale 폐기
  const sel = document.getElementById('mtSessionSelect');
  sel.innerHTML = '<option value="" disabled selected>Loading...</option>';
  sel.disabled = true;
  const list = document.getElementById('mtList');
  list.innerHTML = `<div class="flex items-center gap-3 py-8 justify-center" style="color:var(--text-muted)">
      <div class="w-5 h-5 rounded-full animate-spin" style="border:2px solid var(--outline-variant);border-top-color:var(--primary);"></div>
      <span class="text-sm">Loading sessions...</span>
    </div>`;
  try {
    const res = await _mtFetch('/api/v2/my-tasks/sessions', _mtViewAsBody());
    if (myToken !== _mtSessionsReqSeq) return;  // 더 새로운 요청이 진행 중 → 이 응답 폐기
    if (res.status !== 'SUCCESS') {
      _mtShowToast(res.message || 'Failed to load sessions', 'error');
      sel.innerHTML = '<option value="" disabled selected>Failed to load</option>';
      list.innerHTML = '<div class="text-sm py-8 text-center" style="color:var(--text-muted)">Failed to load sessions.</div>';
      return;
    }
    mtSessions = (res.data?.sessions) || [];
    sel.innerHTML = '';
    sel.disabled = false;
    if (!mtSessions.length) {
      sel.innerHTML = '<option value="" disabled selected>No sessions assigned</option>';
      const dbg = res.data?.debug || {};
      const me = res.data?.me || {};
      // 진단 메시지 — 어디서 fail 했는지 명확히
      let reason = '';
      if (dbg.active_total === 0) {
        reason = `현재 active 세션이 0건입니다. (admin 이 세션을 생성하지 않았거나 모두 closed)<br>
                  <span style="opacity:.6">No active session exists.</span>`;
      } else if (dbg.in_period === 0) {
        reason = `Active 세션 ${dbg.active_total}건은 있지만 모두 기간 외 (시작 전 또는 종료됨). today=${dbg.today_kst}<br>
                  <span style="opacity:.6">${dbg.active_total} active session(s) but all outside of date range.</span>`;
      } else {
        const _meLbl = (typeof dybRoleLabel === 'function') ? dybRoleLabel(me.role) : (me.role || '(none)');
        reason = `기간 내 세션 ${dbg.in_period}건이 있지만 본인 portal role <b>"${_mtEsc(_meLbl || '(none)')}"</b> 에 매핑된 rater role 이 없습니다.<br>
                  <span style="opacity:.6">${dbg.in_period} session(s) in period but no rater role mapped to your portal role.</span>`;
      }
      // admin / 본인이 admin 이면 글로벌 매핑 dump 도 표시 (진단 도움)
      let mapDump = '';
      if (me.is_admin && dbg.global_mappings_per_type) {
        const lines = [];
        Object.keys(dbg.global_mappings_per_type).forEach(et => {
          const rs = dbg.global_mappings_per_type[et] || [];
          rs.forEach(r => {
            const m = (r.mappings && r.mappings.length) ? r.mappings.join(', ') : '(empty)';
            lines.push(`<code style="font-size:10px">${_mtEsc(et)}.${_mtEsc(r.role)} → [${_mtEsc(m)}]</code>`);
          });
        });
        if (lines.length) {
          mapDump = `<details class="mt-3" style="text-align:left;max-width:520px;margin:12px auto 0;"><summary style="font-size:11px;color:var(--text-muted);cursor:pointer">▶ Admin debug — current global mappings</summary><div class="mt-2" style="font-size:11px;color:var(--text-muted);line-height:1.6;">${lines.join('<br>')}</div></details>`;
        }
      }
      list.innerHTML = `<div class="text-sm py-8 text-center" style="color:var(--text-muted)">
        ${reason}
        <div class="mt-3 text-xs" style="opacity:.6;">
          진단: active=${dbg.active_total} · in-period=${dbg.in_period} · matched=${dbg.matched} · my role=<b>${_mtEsc(((typeof dybRoleLabel==='function')?dybRoleLabel(me.role):me.role) || '(none)')}</b>
        </div>
        ${mapDump}
      </div>`;
      return;
    }
    mtSessions.forEach(s => {
      const opt = document.createElement('option');
      opt.value = s.id;
      // 회차 이름만 노출 — 직책 정보는 카드의 chip 으로 별도 표시
      opt.textContent = s.label || s.id;
      sel.appendChild(opt);
    });
    // sessionStorage 복원된 sessionId 가 현재 sessions 에 있으면 그것 선택, 아니면 첫 번째.
    const restoreId = mtCurrentSessionId && mtSessions.some(s => s.id === mtCurrentSessionId)
      ? mtCurrentSessionId : mtSessions[0].id;
    mtCurrentSessionId = restoreId;
    sel.value = restoreId;
    loadMtList();
  } catch (e) {
    console.error(e);
    _mtShowToast('Network error.', 'error');
    sel.innerHTML = '<option value="" disabled selected>Error</option>';
  }
}

function mtOnSessionChange() {
  const sel = document.getElementById('mtSessionSelect');
  mtCurrentSessionId = sel.value;
  _mtSaveState();
  loadMtList();
}

// 직책 필터 (eval_type) — 세션/캠퍼스 변경 후에도 유지. 'All' 선택 시 빈문자.
function mtOnTypeFilterChange() {
  mtTypeFilter = (document.getElementById('mtTypeFilter')?.value || '').trim();
  _mtSaveState();
  _mtRenderFiltered();
}

function _mtRenderFiltered() {
  const list = document.getElementById('mtList');
  const filtered = mtTypeFilter
    ? mtCurrentTeachers.filter(t => t.eval_type === mtTypeFilter)
    : mtCurrentTeachers;
  if (!filtered.length) {
    if (mtTypeFilter) {
      list.innerHTML = `<div class="text-sm py-8 text-center" style="color:var(--text-muted)">
        No teachers match this position filter.<br>
        <button onclick="mtClearTypeFilter()" class="font-bold underline mt-2" style="color:var(--primary)">Clear filter</button>
      </div>`;
    } else {
      list.innerHTML = '<div class="text-sm py-8 text-center" style="color:var(--text-muted)">No teachers found.</div>';
    }
    return;
  }
  renderMtList(filtered);
}

function mtClearTypeFilter() {
  mtTypeFilter = '';
  const f = document.getElementById('mtTypeFilter'); if (f) f.value = '';
  _mtRenderFiltered();
}

// ── 직원 리스트 로드 ─────────────────────────────────────────────────────────
async function loadMtList() {
  if (!mtCurrentSessionId) return;
  const myToken = ++_mtListReqSeq;  // race 가드 — 빠른 세션 전환 시 stale 응답 폐기
  const list = document.getElementById('mtList');
  list.innerHTML = `<div class="flex items-center gap-3 py-8 justify-center" style="color:var(--text-muted)">
      <div class="w-5 h-5 rounded-full animate-spin" style="border:2px solid var(--outline-variant);border-top-color:var(--primary);"></div>
      <span class="text-sm">Loading teachers...</span>
    </div>`;
  try {
    const body = { session_id: mtCurrentSessionId, ..._mtViewAsBody() };
    const res = await _mtFetch('/api/v2/my-tasks/list', body);
    if (myToken !== _mtListReqSeq) return;
    if (res.status !== 'SUCCESS') {
      list.innerHTML = `<div class="text-sm py-8 text-center" style="color:var(--text-muted)">${_mtEsc(res.message || 'Failed to load')}</div>`;
      return;
    }
    const teachers = (res.data?.teachers) || [];
    const sessInfo = res.data?.session;
    const info = document.getElementById('mtSessionInfo');
    if (info && sessInfo) {
      const dateRange = (sessInfo.start_date && sessInfo.end_date) ? `${sessInfo.start_date} ~ ${sessInfo.end_date}` : '';
      info.textContent = dateRange ? `· ${dateRange}` : '';
    }
    mtCurrentTeachers = teachers;
    if (!teachers.length) {
      list.innerHTML = '<div class="text-sm py-8 text-center" style="color:var(--text-muted)">No teachers in your campus for this session.</div>';
      return;
    }
    _mtRenderFiltered();
  } catch (e) {
    console.error(e);
    list.innerHTML = '<div class="text-sm py-8 text-center" style="color:var(--text-muted)">Network error. Please retry.</div>';
  }
}

function renderMtList(teachers) {
  const container = document.getElementById('mtList');
  const summary = `<div class="text-xs mb-3" style="color:var(--text-muted)">
    <span class="font-bold" style="color:var(--text-strong)">${teachers.length}</span> teacher(s) ·
    <span style="color:#15803d;font-weight:700">${teachers.filter(t => t.all_done).length}</span> submitted /
    <span style="color:var(--primary);font-weight:700">${teachers.filter(t => !t.all_done).length}</span> pending
  </div>`;
  let html = summary + '<div style="display:flex;flex-direction:column;gap:6px;">';
  teachers.forEach(t => {
    const safeName = _mtEsc(t.name || t.emp_id);
    const safeEmpId = _mtEsc((t.emp_id || '').toUpperCase());
    const safeCampus = _mtEsc(_mtCampusCode(t.campus) || '—');
    const typeLabel = _mtEsc(t.eval_type_label || '');
    const doneSet = new Set(t.done_roles || []);
    const labelMap = t.my_rater_role_labels || {};
    const chipsHtml = (t.my_rater_roles || []).map(r => {
      const isDone = doneSet.has(r);
      // 우선순위: 백엔드에서 미리 계산한 label (eval admin_config label_ko)
      //          > portal_roles.label fallback
      //          > raw role name
      const _lbl = labelMap[r]
        || ((typeof dybRoleLabel === 'function') ? dybRoleLabel(r) : r)
        || r;
      return `<span class="mt-rater-chip ${isDone ? 'done' : ''}">${isDone ? '✓ ' : ''}${_mtEsc(_lbl)}</span>`;
    }).join('');
    const allDone = t.all_done;
    const statusBadge = allDone
      ? `<span class="mt-status-badge done"><i class="bi bi-check-circle-fill"></i> Submitted</span>`
      : `<span class="mt-status-badge pending"><i class="bi bi-clock-fill"></i> Pending</span>`;
    // L-4: done 카드도 editable doc_id 가 있으면 클릭 활성 (수정 모드).
    // 운영 정책 단일 매핑이라 done_role_doc_ids 가 1개. 첫 doc_id 추출.
    const doneDocIds = t.done_role_doc_ids || {};
    const firstEditableDocId = Object.values(doneDocIds)[0] || '';
    const editable = allDone && firstEditableDocId;
    const onclick = allDone
      ? (editable ? `onclick="mtGoToEditEval('${_mtEsc(t.emp_id)}', '${_mtEsc(firstEditableDocId)}')"` : '')
      : `onclick="mtGoToEval('${_mtEsc(t.emp_id)}')"`;
    const cardClasses = `mt-card ${allDone ? 'done' : ''} ${editable ? 'editable' : ''}`.trim();
    html += `
      <div class="${cardClasses}" ${onclick}>
        <div style="min-width:0;flex:1;">
          <div style="display:flex;align-items:center;gap:8px;margin-bottom:3px;flex-wrap:wrap;">
            <p class="text-sm font-bold" style="color:var(--text-strong);">${safeName}</p>
            <span class="text-xs font-mono" style="color:var(--text-muted)">${safeEmpId}</span>
            <span class="text-xs" style="color:var(--text-dim)">·</span>
            <span class="text-xs font-semibold" style="color:var(--text-muted)">${safeCampus}</span>
            ${typeLabel ? `<span class="badge-type-${_mtEsc(t.eval_type || 'regular')} text-[10px] font-bold px-1.5 py-0.5 rounded">${typeLabel}</span>` : ''}
          </div>
          <div style="display:flex;align-items:center;gap:4px;flex-wrap:wrap;">${chipsHtml}</div>
        </div>
        <div style="flex-shrink:0;">${statusBadge}</div>
      </div>`;
  });
  html += '</div>';
  container.innerHTML = html;
}

function mtGoToEval(empId) {
  if (!empId || !mtCurrentSessionId) return;
  const url = `/eval-v2/form?empId=${encodeURIComponent(empId)}&session=${encodeURIComponent(mtCurrentSessionId)}&fromMyTasks=1`;
  window.location.href = url;
}

// L-4: 수정 모드 진입 — done 카드 클릭 시 editDocId URL param 추가
function mtGoToEditEval(empId, docId) {
  if (!empId || !mtCurrentSessionId || !docId) return;
  const url = `/eval-v2/form?empId=${encodeURIComponent(empId)}&session=${encodeURIComponent(mtCurrentSessionId)}&fromMyTasks=1&editDocId=${encodeURIComponent(docId)}`;
  window.location.href = url;
}

document.addEventListener('DOMContentLoaded', () => {
  // portal_roles 캐시 워밍 — chip 텍스트 + 진단 메시지 의 dybRoleLabel 즉시 사용 가능.
  if (typeof window.dybLoadRoleLabels === 'function') window.dybLoadRoleLabels();
  // 페이지 헤더의 본인 campus 라벨을 영문 코드로 (server inject 한글 → 코드 변환)
  const myCampusLabel = document.getElementById('mtMyCampusLabel');
  if (myCampusLabel && window.PORTAL_ME && window.PORTAL_ME.campus) {
    myCampusLabel.textContent = '· ' + _mtCampusCode(window.PORTAL_ME.campus);
  }
  if (IS_ADMIN_ME) {
    _mtPopulateCampusSelect();
    _mtPopulateRoleSelect();
  }
  // sessionStorage 에서 이전 state 복원 (form 평가 후 Back to My Tasks 로 돌아왔을 때)
  const saved = _mtLoadState();
  if (saved) {
    if (IS_ADMIN_ME && saved.viewAs && (saved.viewAs.role || saved.viewAs.campus)) {
      mtViewAs = { role: saved.viewAs.role || '', campus: saved.viewAs.campus || '' };
      const r = document.getElementById('mtViewAsRole'); if (r) r.value = mtViewAs.role;
      const c = document.getElementById('mtViewAsCampus'); if (c) c.value = mtViewAs.campus;
      _mtUpdateViewAsBadge();
    }
    if (saved.typeFilter) {
      mtTypeFilter = saved.typeFilter;
      const f = document.getElementById('mtTypeFilter'); if (f) f.value = mtTypeFilter;
    }
    if (saved.sessionId) mtCurrentSessionId = saved.sessionId;  // loadMtSessions 가 매칭 옵션 자동 선택
  }
  if (IS_ADMIN_ME) _mtUpdateViewAsTimingChip();  // chip 초기 표시 (복원된 mtViewAs 반영)
  loadMtSessions();
});
