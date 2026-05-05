// ── Users Management ──────────────────────────────────────────────────────

let allAdminUsers = [];
let usersLoaded = false;

async function loadAdminUsers() {
  if (usersLoaded) {
    filterAdminUsers();
    return;
  }
  const body = document.getElementById('usersTableBody');
  if (body) {
    body.innerHTML = `<div class="flex items-center gap-3 py-8 justify-center" style="color:var(--outline)">
      <div class="w-5 h-5 rounded-full animate-spin" style="border:2px solid var(--outline-variant);border-top-color:var(--primary);"></div>
      <span class="text-sm">Loading...</span>
    </div>`;
  }
  try {
    // 사용자 fetch 와 portal_roles 캐시 워밍 병렬 — 첫 렌더 시 label 즉시 사용 가능.
    const [res] = await Promise.all([
      fetch('/api/v2/admin/users', { method: 'POST' }),
      _ensureRoleDropdownCandidates(),
    ]);
    const data = await res.json();
    if (data.status !== 'SUCCESS') throw new Error(data.message || 'Failed to load users');
    allAdminUsers = data.users || [];
    usersLoaded = true;
    filterAdminUsers();
  } catch (e) {
    if (body) body.innerHTML = `<div class="py-8 text-center text-sm" style="color:var(--error)">Failed to load users: ${e.message}</div>`;
  }
}

function filterAdminUsers() {
  const q = (document.getElementById('usersSearchInput')?.value || '').toLowerCase();
  const role = document.getElementById('usersRoleFilter')?.value || '';
  const campus = document.getElementById('usersCampusFilter')?.value || '';

  let filtered = allAdminUsers.filter(u => {
    if (role && u.role !== role) return false;
    if (campus && u.campus !== campus) return false;
    if (q) {
      const haystack = `${u.name} ${u.emp_id} ${u.email}`.toLowerCase();
      if (!haystack.includes(q)) return false;
    }
    return true;
  });

  renderAdminUsers(filtered);

  const countEl = document.getElementById('usersCount');
  if (countEl) {
    countEl.textContent = `Showing ${filtered.length} of ${allAdminUsers.length} users`;
  }
}

function _initials(name) {
  if (!name) return '?';
  const parts = name.trim().split(/\s+/);
  if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
  return name.slice(0, 2).toUpperCase();
}

const ROLE_COLORS = {
  admin:   'background-color:#7c3aed;color:#fff',
  MASTER:  'background-color:#7c3aed;color:#fff',
  GS:      'background-color:#0369a1;color:#fff',
  TL:      'background-color:#b45309;color:#fff',
  STL:     'background-color:#0f766e;color:#fff',
  NET:     'background-color:#059669;color:#fff',
  retired: 'background-color:#6b7280;color:#fff',
  '퇴사':  'background-color:#6b7280;color:#fff',
};
// role 필 (흰색 글자 배경) 용 흰색 chevron — campus 필 용은 layout.html 의 .dyb-select 기본값(회색) 사용
const _ROLE_ARROW = `url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 24 24' fill='none' stroke='%23ffffff' stroke-width='2.5' stroke-linecap='round'%3E%3Cpath d='M6 9l6 6 6-6'/%3E%3C/svg%3E")`;

// CAMPUS_EN, CAMPUS_ORDER, CAMPUS_REQUIRED_ROLES, CAMPUS_FIXED_ROLES
// are loaded from campus_constants.js
const CAMPUS_OPTIONS = CAMPUS_ORDER;

// ── Role dropdown — portal_roles 동적 (custom role + label 표시 + retired/퇴사 등 통합) ──
// admin/MASTER 와 retired/퇴사 는 동일 옵션으로 통합 (legacy 와 alias 가 동일 권한).
function _renderRoleDropdown(u, empId) {
  const roleStyle = ROLE_COLORS[u.role] || 'background-color:var(--surface-low);color:var(--on-surface)';
  const roles = (typeof window.dybLoadRoleLabels === 'function' && window._dybRolesCache) ||
                (typeof dybRoleLabel === 'function' ? null : null);
  // dybRoleLabel cache 가 채워졌으면 그대로 사용. 비었으면 system role fallback.
  const fallback = [
    { name: 'NET', label: 'NET' },
    { name: 'GS', label: 'GS' },
    { name: 'TL', label: 'TL' },
    { name: 'STL', label: 'STL' },
    { name: 'admin', label: 'Admin' },
    { name: 'retired', label: 'Retired' },
  ];
  // dybLoadRoleLabels 가 캐시를 채웠으면 window 어딘가 노출되지 않으므로,
  // 실제 옵션은 dybRoleLabel(name) 으로 매 렌더 시 lookup. 옵션 후보는 fallback + custom.
  // custom role 추가 지원: window.__allRoles (admin_users.js 자체 캐시)
  const candidates = (window.__allRolesForDropdown && window.__allRolesForDropdown.length)
    ? window.__allRolesForDropdown
    : fallback;
  const opts = candidates.map(r => {
    const isSelected = (u.role === r.name)
      || (r.name === 'admin' && u.role === 'MASTER')
      || (r.name === 'retired' && u.role === '퇴사');
    const display = (typeof dybRoleLabel === 'function') ? dybRoleLabel(r.name) : (r.label || r.name);
    return `<option value="${r.name}" ${isSelected ? 'selected' : ''}>${display}</option>`;
  }).join('');
  return `<div class="flex-shrink-0">
    <select onchange="saveUserRole('${empId}', this.value, '${u.campus || ''}')"
      class="dyb-select dyb-dd-sm"
      style="${roleStyle};border:none;background-image:${_ROLE_ARROW}">
      ${opts}
    </select>
  </div>`;
}

// 페이지 init 시 admin_users 로드 직전 호출 — portal_roles 캐시 워밍 + dropdown 후보 갱신.
async function _ensureRoleDropdownCandidates() {
  if (typeof window.dybLoadRoleLabels !== 'function') return;
  try {
    const roles = await window.dybLoadRoleLabels();
    if (Array.isArray(roles)) {
      // MASTER / 퇴사 는 admin / retired 로 통합하므로 후보에서 제외 (단, 보유자 selection 은 위 로직에서 처리)
      window.__allRolesForDropdown = roles.filter(r =>
        !r.deprecated && r.name !== 'MASTER' && r.name !== '퇴사'
      );
    }
  } catch (_) { /* fallback 사용 */ }
}

function renderAdminUsers(users) {
  const body = document.getElementById('usersTableBody');
  if (!body) return;

  if (!users.length) {
    body.innerHTML = `<div class="py-10 text-center text-sm" style="color:var(--outline)">No users found.</div>`;
    return;
  }

  body.innerHTML = users.map(u => {
    const initials = _initials(u.name);
    const roleStyle = ROLE_COLORS[u.role] || 'background-color:var(--surface-low);color:var(--on-surface)';
    const empId = u.emp_id || '';
    const notes = (u.notes || '').replace(/"/g, '&quot;');
    const isRequired  = CAMPUS_REQUIRED_ROLES.includes(u.role);
    const fixedCampus = CAMPUS_FIXED_ROLES[u.role];
    const campusOpts = `<option value="">— 미지정 —</option>` +
      CAMPUS_OPTIONS.map(c => `<option value="${c}" ${u.campus === c ? 'selected' : ''}>${c} (${CAMPUS_EN[c] || c})</option>`).join('');
    const campusPillStyle = isRequired && !u.campus
      ? `background-color:rgba(220,38,38,.12);color:var(--danger);border:1.5px solid rgba(220,38,38,.45)`
      : `background-color:var(--surface-low);color:var(--text);border:1px solid var(--outline-variant)`;
    const campusCell = fixedCampus
      ? `<div class="flex-shrink-0">
          <span class="text-xs font-bold flex items-center gap-1"
            style="background-color:rgba(20,184,166,.14);color:#5eead4;border:none;padding:4px 10px;border-radius:9999px">
            <i class="bi bi-lock-fill" style="font-size:9px"></i>${fixedCampus}
          </span>
         </div>`
      : `<div class="flex-shrink-0" style="min-width:144px">
          <select onchange="saveUserCampus('${empId}', this.value, '${u.role}')"
            class="dyb-select dyb-dd-sm w-full"
            style="${campusPillStyle}"
            title="${isRequired ? 'Campus required' : ''}">
            ${campusOpts}
          </select>
         </div>`;

    return `
    <div class="flex flex-wrap items-center gap-4 px-5 py-4" style="background:var(--surface-lowest)">
      <!-- Avatar + name -->
      <div class="flex items-center gap-3 flex-1 min-w-[180px]">
        <div class="w-9 h-9 rounded-full flex items-center justify-center text-sm font-extrabold flex-shrink-0"
          style="background:var(--surface-low);color:var(--on-surface-variant)">${initials}</div>
        <div class="min-w-0">
          <p class="text-sm font-bold truncate" style="color:var(--on-surface)">${u.name || '—'}</p>
          <p class="text-xs font-mono" style="color:var(--outline)">${empId}</p>
        </div>
      </div>
      <!-- Email -->
      <div class="flex-1 min-w-[180px]">
        <p class="text-xs truncate" style="color:var(--on-surface-variant)">${u.email || '—'}</p>
      </div>
      <!-- Campus -->
      ${campusCell}
      <!-- Role dropdown -->
      ${_renderRoleDropdown(u, empId)}
      <!-- Notes -->
      <div class="flex-1 min-w-[140px]">
        <input type="text" value="${notes}" placeholder="Admin notes..."
          onblur="saveUserNotes('${empId}', this.value)"
          class="w-full text-xs px-2 py-1.5 border-2 rounded-lg focus:outline-none"
          style="border-color:var(--outline-variant);background:var(--surface-lowest);color:var(--on-surface)">
      </div>
      <!-- Delete -->
      <button onclick="deleteAdminUser('${empId}', '${(u.name || '').replace(/'/g, "\\'")}')"
        class="flex-shrink-0 w-8 h-8 flex items-center justify-center rounded-lg transition-colors"
        style="color:var(--error);background:var(--surface-low)"
        title="Delete user">
        <i class="bi bi-trash3-fill text-sm"></i>
      </button>
    </div>`;
  }).join('');
}

async function saveUserCampus(empId, newCampus, currentRole) {
  if (CAMPUS_REQUIRED_ROLES.includes(currentRole) && !newCampus) {
    showToast(`${currentRole} 역할은 캠퍼스가 필수입니다.`, 'error');
    // 드롭다운을 기존 값으로 되돌림
    const u = allAdminUsers.find(x => x.emp_id === empId);
    if (u) filterAdminUsers();
    return;
  }
  try {
    const res = await fetch('/api/v2/admin/users/update', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ empId, campus: newCampus, currentRole }),
    });
    const data = await res.json();
    if (data.status === 'SUCCESS') {
      const u = allAdminUsers.find(x => x.emp_id === empId);
      if (u) u.campus = newCampus;
      showToast(`Campus updated to ${newCampus || '(없음)'}`, 'success');
      filterAdminUsers();
    } else {
      showToast(data.message || 'Failed to update campus', 'error');
      filterAdminUsers();
    }
  } catch (e) {
    showToast('Network error: ' + e.message, 'error');
  }
}

async function saveUserRole(empId, newRole, currentCampus) {
  const u = allAdminUsers.find(x => x.emp_id === empId);
  const userName = u ? u.name : empId;
  const isRetired = newRole === 'retired';
  const msg = isRetired
    ? `Retire "${userName}"?\n\nThis user's portal access will be restricted.`
    : `Change "${userName}"'s role to ${newRole}?`;
  const confirmed = await showCustomPrompt('Change Role', msg, false, isRetired, isRetired ? 'Retire' : 'Change');
  if (!confirmed) { filterAdminUsers(); return; }

  const fixedCampus = CAMPUS_FIXED_ROLES[newRole];
  const effectiveCampus = fixedCampus || currentCampus;

  if (CAMPUS_REQUIRED_ROLES.includes(newRole) && !effectiveCampus) {
    showToast(`${newRole} 역할은 캠퍼스를 먼저 지정해야 합니다.`, 'error');
    filterAdminUsers();
    return;
  }
  try {
    const payload = { empId, role: newRole, currentCampus: effectiveCampus };
    // campus-required 역할 변경 시 effectiveCampus 를 항상 campus 로 포함
    // (Firestore portal_users.campus 필드가 실제로 저장되도록)
    if (effectiveCampus) payload.campus = effectiveCampus;
    const res = await fetch('/api/v2/admin/users/update', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (data.status === 'SUCCESS') {
      const u = allAdminUsers.find(x => x.emp_id === empId);
      if (u) {
        u.role = newRole;
        if (fixedCampus) u.campus = fixedCampus;
      }
      showToast(`Role updated to ${newRole}${fixedCampus ? ` (Campus: ${fixedCampus})` : ''}`, 'success');
      filterAdminUsers();
    } else {
      showToast(data.message || 'Failed to update role', 'error');
      filterAdminUsers();
    }
  } catch (e) {
    showToast('Network error: ' + e.message, 'error');
  }
}

async function saveUserNotes(empId, notes) {
  try {
    const res = await fetch('/api/v2/admin/users/update', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ empId, notes }),
    });
    const data = await res.json();
    if (data.status === 'SUCCESS') {
      const u = allAdminUsers.find(x => x.emp_id === empId);
      if (u) u.notes = notes;
    } else {
      showToast(data.message || 'Failed to save notes', 'error');
    }
  } catch (e) {
    showToast('Network error: ' + e.message, 'error');
  }
}

async function deleteAdminUser(empId, name) {
  const confirmed = await showCustomPrompt(
    'Delete User',
    `Are you sure you want to delete user "${name}" (${empId})? This cannot be undone.`,
    false
  );
  if (confirmed !== 'confirm') return;
  try {
    const res = await fetch('/api/v2/admin/users/delete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ empId }),
    });
    const data = await res.json();
    if (data.status === 'SUCCESS') {
      allAdminUsers = allAdminUsers.filter(u => u.emp_id !== empId);
      filterAdminUsers();
      showToast(`User ${name} deleted.`, 'success');
    } else {
      showToast(data.message || 'Failed to delete user', 'error');
    }
  } catch (e) {
    showToast('Network error: ' + e.message, 'error');
  }
}
