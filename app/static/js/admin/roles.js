// /admin/roles 페이지 — system role 7개 + custom role 카드 리스트 + Add/Edit modal
// system role 은 잠금 (이름·삭제·수정 불가), custom role 은 label 만 수정 가능.

function showToast(msg, type = 'info') {
  const c = document.getElementById('toastContainer');
  if (!c) { console.log('[toast]', msg); return; }
  const t = document.createElement('div');
  t.className = `toast toast-${type}`;
  t.textContent = msg;
  c.appendChild(t);
  setTimeout(() => t.remove(), 3000);
}

// ── State ──
let _roles = [];
let _showLegacy = false;

async function loadRoles() {
  const list = document.getElementById('rolesList');
  list.innerHTML = `<div class="flex items-center gap-3 py-8 justify-center" style="color:var(--text-muted)">
    <div class="w-5 h-5 rounded-full animate-spin" style="border:2px solid var(--outline-variant);border-top-color:var(--primary);"></div>
    <span class="text-sm">Loading...</span></div>`;
  try {
    const url = _showLegacy ? '/api/v2/admin/roles?include_deprecated=1' : '/api/v2/admin/roles';
    const res = await fetch(url);
    const data = await res.json();
    if (data.status !== 'SUCCESS') throw new Error(data.message || 'Failed');
    _roles = (data.data && data.data.roles) || [];
    renderRoles();
  } catch (e) {
    list.innerHTML = `<div class="py-8 text-center text-sm" style="color:var(--error)">Failed to load: ${e.message}</div>`;
  }
}

function toggleShowLegacy() {
  _showLegacy = !!document.getElementById('showLegacyToggle').checked;
  loadRoles();
}

function renderRoles() {
  const list = document.getElementById('rolesList');
  if (!_roles.length) {
    list.innerHTML = `<div class="py-8 text-center text-sm" style="color:var(--text-muted)">No roles found.</div>`;
    return;
  }
  list.innerHTML = _roles.map(r => {
    const isDeprecated = !!r.deprecated;
    const lockBadge = r.is_system
      ? `<span class="text-xs font-bold inline-flex items-center gap-1 px-2 py-1 rounded-full" style="background:var(--surface-low);color:var(--text-muted)">
           <i class="bi bi-lock-fill" style="font-size:10px"></i>System
         </span>`
      : `<span class="text-xs font-bold inline-flex items-center gap-1 px-2 py-1 rounded-full" style="background:rgba(59,130,246,.1);color:#2563eb">
           <i class="bi bi-tag-fill" style="font-size:10px"></i>Custom
         </span>`;
    const deprecatedBadge = isDeprecated
      ? `<span class="text-xs font-bold inline-flex items-center gap-1 px-2 py-1 rounded-full" style="background:rgba(217,119,6,.12);color:#d97706">
           <i class="bi bi-archive-fill" style="font-size:10px"></i>Deprecated
         </span>`
      : '';
    // 보안: inline onclick 제거. event delegation 으로 위임 (renderRoles 하단에서
    // rolesList container 에 click 리스너 1회 바인딩, data-action + data-role-name 으로 dispatch).
    // 과거 escapeAttr-based onclick 패턴이 HTML attribute decoding 으로 깨지는 사고 (admin label
    // 의 `'` 가 JS string break) 재발 방지.
    const _attrName = escapeAttr(r.name);
    // Edit Label 은 system + custom 모두 노출
    const editBtn = `<button data-action="edit" data-role-name="${_attrName}"
           class="btn-secondary px-3 py-1.5 text-xs">
           <i class="bi bi-pencil-fill"></i> Edit Label
         </button>`;
    // Deprecate / Restore 토글 — 양방향 모두 가능
    const deprecateBtn = isDeprecated
      ? `<button data-action="restore" data-role-name="${_attrName}"
           class="btn-secondary px-3 py-1.5 text-xs">
           <i class="bi bi-arrow-counterclockwise"></i> Restore
         </button>`
      : `<button data-action="deprecate" data-role-name="${_attrName}"
           class="btn-secondary px-3 py-1.5 text-xs">
           <i class="bi bi-archive"></i> Deprecate
         </button>`;
    // Delete 는 custom only — system role 잠금
    const deleteBtn = r.is_system
      ? ''
      : `<button data-action="delete" data-role-name="${_attrName}"
           class="btn-danger px-3 py-1.5 text-xs">
           <i class="bi bi-trash3-fill"></i> Delete
         </button>`;
    // deprecated 카드: 회색 톤 + opacity
    const cardStyle = isDeprecated
      ? 'background:var(--surface-low);border:1px dashed var(--outline-variant);opacity:0.75'
      : 'background:var(--surface-lowest);border:1px solid var(--outline-variant)';
    return `
      <div class="flex items-center gap-4 px-5 py-4 rounded-lg" style="${cardStyle}">
        <div class="flex-1 min-w-0">
          <div class="flex items-center gap-2 flex-wrap">
            <span class="text-base font-extrabold font-mono" style="color:var(--on-surface)">${escapeHTML(r.name)}</span>
            ${lockBadge}
            ${deprecatedBadge}
          </div>
          <p class="text-sm mt-0.5" style="color:var(--on-surface-variant)">${escapeHTML(r.label || r.name)}</p>
        </div>
        <div class="flex-shrink-0 flex gap-2 flex-wrap">${editBtn}${deprecateBtn}${deleteBtn}</div>
      </div>`;
  }).join('');
}

// ── Add Role 모달 ──
function openAddRoleModal() {
  document.getElementById('newRoleNameInput').value = '';
  document.getElementById('newRoleLabelInput').value = '';
  const err = document.getElementById('addRoleError');
  err.style.display = 'none';
  err.textContent = '';
  document.getElementById('addRoleModal').style.display = 'flex';
  setTimeout(() => document.getElementById('newRoleNameInput').focus(), 50);
}
function closeAddRoleModal() {
  document.getElementById('addRoleModal').style.display = 'none';
}
async function submitAddRole() {
  const name = (document.getElementById('newRoleNameInput').value || '').trim();
  const label = (document.getElementById('newRoleLabelInput').value || '').trim();
  const err = document.getElementById('addRoleError');
  err.style.display = 'none';
  if (!name || !label) {
    err.textContent = 'Name and label are required.';
    err.style.display = 'block';
    return;
  }
  const btn = document.getElementById('addRoleSubmitBtn');
  btn.disabled = true;
  try {
    const res = await fetch('/api/v2/admin/roles', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, label }),
    });
    const data = await res.json();
    if (data.status !== 'SUCCESS') {
      err.textContent = data.message || 'Failed to add role.';
      err.style.display = 'block';
      return;
    }
    closeAddRoleModal();
    showToast(`Role "${name}" added.`, 'success');
    loadRoles();
  } catch (e) {
    err.textContent = 'Network error.';
    err.style.display = 'block';
  } finally {
    btn.disabled = false;
  }
}

// ── Edit Label 모달 ──
function openEditRoleModal(name, label) {
  document.getElementById('editRoleNameDisplay').value = name;
  document.getElementById('editRoleLabelInput').value = label;
  const err = document.getElementById('editRoleError');
  err.style.display = 'none';
  err.textContent = '';
  document.getElementById('editRoleModal').style.display = 'flex';
  setTimeout(() => document.getElementById('editRoleLabelInput').focus(), 50);
}
function closeEditRoleModal() {
  document.getElementById('editRoleModal').style.display = 'none';
}
async function submitEditRole() {
  const name = document.getElementById('editRoleNameDisplay').value;
  const label = (document.getElementById('editRoleLabelInput').value || '').trim();
  const err = document.getElementById('editRoleError');
  err.style.display = 'none';
  if (!label) {
    err.textContent = 'Label is required.';
    err.style.display = 'block';
    return;
  }
  const btn = document.getElementById('editRoleSubmitBtn');
  btn.disabled = true;
  try {
    const res = await fetch(`/api/v2/admin/roles/${encodeURIComponent(name)}/update-label`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ label }),
    });
    const data = await res.json();
    if (data.status !== 'SUCCESS') {
      err.textContent = data.message || 'Failed to update label.';
      err.style.display = 'block';
      return;
    }
    closeEditRoleModal();
    showToast(`Label updated for "${name}".`, 'success');
    loadRoles();
  } catch (e) {
    err.textContent = 'Network error.';
    err.style.display = 'block';
  } finally {
    btn.disabled = false;
  }
}

// ── Deprecate / Restore 흐름 ──
async function _setRoleDeprecated(name, deprecated) {
  try {
    const res = await fetch(`/api/v2/admin/roles/${encodeURIComponent(name)}/deprecate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ deprecated }),
    });
    const data = await res.json();
    if (data.status !== 'SUCCESS') {
      showToast(data.message || 'Failed to update role.', 'error');
      return;
    }
    showToast(deprecated ? `Role "${name}" deprecated.` : `Role "${name}" restored.`, 'success');
    loadRoles();
  } catch (e) {
    showToast('Network error.', 'error');
  }
}

function deprecateRole(name) {
  // confirm 안내 — portal_role_mappings 자동 drop 위험을 명확히 (audit B1)
  const ok = confirm(
    `Deprecate role "${name}"?\n\n` +
    `· It will be hidden from new-user assignment dropdowns and eval_v2 chip UI.\n` +
    `· Existing users keeping this role are unaffected.\n` +
    `· Any portal_role_mappings chips with this role will be dropped on next config save.\n\n` +
    `평가 매핑 chip 은 다음 config 저장 시 자동 제거됩니다.`
  );
  if (!ok) return;
  _setRoleDeprecated(name, true);
}

function restoreRole(name) {
  _setRoleDeprecated(name, false);
}

// ── Delete 흐름 ──
let _deletingRoleName = null;

async function confirmDeleteRole(name) {
  // 사용자 카운트 미리보기 → 0 이면 확인 모달, 1+ 면 차단 모달
  try {
    const res = await fetch(`/api/v2/admin/roles/${encodeURIComponent(name)}/user-count`);
    const data = await res.json();
    if (data.status !== 'SUCCESS') {
      showToast(data.message || 'Failed to check users.', 'error');
      return;
    }
    const count = (data.data && data.data.count) || 0;
    if (count > 0) {
      openDeleteBlockedModal(name, count);
    } else {
      openDeleteRoleModal(name);
    }
  } catch (e) {
    showToast('Network error.', 'error');
  }
}

function openDeleteRoleModal(name) {
  _deletingRoleName = name;
  document.getElementById('deleteRoleNameDisplay').textContent = name;
  const err = document.getElementById('deleteRoleError');
  err.style.display = 'none';
  err.textContent = '';
  document.getElementById('deleteRoleModal').style.display = 'flex';
}
function closeDeleteRoleModal() {
  document.getElementById('deleteRoleModal').style.display = 'none';
  _deletingRoleName = null;
}

function openDeleteBlockedModal(name, count) {
  document.getElementById('deleteBlockedNameDisplay').textContent = name;
  document.getElementById('deleteBlockedCountDisplay').textContent = String(count);
  document.getElementById('deleteBlockedModal').style.display = 'flex';
}
function closeDeleteBlockedModal() {
  document.getElementById('deleteBlockedModal').style.display = 'none';
}

async function submitDeleteRole() {
  const name = _deletingRoleName;
  if (!name) return;
  const err = document.getElementById('deleteRoleError');
  err.style.display = 'none';
  const btn = document.getElementById('deleteRoleSubmitBtn');
  btn.disabled = true;
  try {
    const res = await fetch(`/api/v2/admin/roles/${encodeURIComponent(name)}`, { method: 'DELETE' });
    const data = await res.json();
    if (data.status !== 'SUCCESS') {
      // 409 race fallback — 다른 admin 이 그 사이 사용자를 할당했을 수 있음
      if (res.status === 409) {
        closeDeleteRoleModal();
        // 정확한 카운트 다시 조회 후 차단 모달 표시
        try {
          const cRes = await fetch(`/api/v2/admin/roles/${encodeURIComponent(name)}/user-count`);
          const cData = await cRes.json();
          const count = (cData.data && cData.data.count) || 1;
          openDeleteBlockedModal(name, count);
        } catch (_) {
          openDeleteBlockedModal(name, 1);
        }
        return;
      }
      err.textContent = data.message || 'Failed to delete role.';
      err.style.display = 'block';
      return;
    }
    closeDeleteRoleModal();
    showToast(`Role "${name}" deleted.`, 'success');
    loadRoles();
  } catch (e) {
    err.textContent = 'Network error.';
    err.style.display = 'block';
  } finally {
    btn.disabled = false;
  }
}

// ── helpers ──
function escapeHTML(s) {
  return String(s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}
function escapeAttr(s) {
  return escapeHTML(s).replace(/`/g, '&#96;');
}

// ── Init ──
// rolesList 컨테이너에 click 리스너 1회 바인딩 (event delegation).
// renderRoles() 가 innerHTML 로 카드 재생성해도 listener 는 컨테이너 자체에 붙어있어 영향 없음.
function _initRolesListDelegation() {
  const list = document.getElementById('rolesList');
  if (!list || list._delegationBound) return;
  list._delegationBound = true;
  list.addEventListener('click', (e) => {
    const btn = e.target.closest('button[data-action]');
    if (!btn || !list.contains(btn)) return;
    const action = btn.dataset.action;
    const name = btn.dataset.roleName;
    if (!name) return;
    // _roles 에서 r 객체 찾아 label 도 가져옴 (Edit 시 필요).
    const r = _roles.find(x => x.name === name);
    switch (action) {
      case 'edit':       openEditRoleModal(name, (r && (r.label || r.name)) || name); break;
      case 'restore':    restoreRole(name); break;
      case 'deprecate':  deprecateRole(name); break;
      case 'delete':     confirmDeleteRole(name); break;
    }
  });
}
_initRolesListDelegation();
loadRoles();
