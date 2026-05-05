/* ── Drafts Tab (Email Draft Generation) ── */

let draftsTabInitialized = false;
let _campusEmailsData = [];

async function initDraftsTab() {
  if (draftsTabInitialized) return;
  draftsTabInitialized = true;
  await Promise.all([loadDraftTemplates(), loadDraftCampusEmailsWithStore(), loadDraftSessions()]);
}

// ── Session selector for drafts ──────────────────────────────────────────
async function loadDraftSessions() {
  const sel  = document.getElementById('d2_sessionSelect');
  const menu = document.getElementById('d2_sessionMenu');
  if (!sel || !menu) return;
  try {
    const res = await fetch('/api/v2/sub-ctl/sessions');
    const data = await res.json();
    if (data.status !== 'SUCCESS') return;
    (data.sessions || []).forEach(s => {
      const isAct = s.status === 'active';
      const icon  = isAct ? '●' : '■';
      const label = `${s.label} ${icon}`;
      const opt = document.createElement('option');
      opt.value = s.id; opt.textContent = label;
      sel.appendChild(opt);
      const div = document.createElement('div');
      div.className = 'dyb-dd-option';
      div.dataset.value = s.id;
      div.innerHTML = `<span class="font-bold" style="color:var(--on-surface)">${s.label}</span><span class="ml-auto text-xs font-bold px-2 py-0.5 rounded-full ${isAct?'bg-emerald-100 text-emerald-700':'bg-slate-100 text-slate-500'}">${icon}</span>`;
      div.onclick = () => dybPick('d2_session', s.id, s.label);
      menu.appendChild(div);
    });
  } catch(e) { console.error('loadDraftSessions error', e); }
}

function d2_getSelectedSession() {
  const sel = document.getElementById('d2_sessionSelect'); // hidden select
  return sel ? sel.value : '';
}

// ── Templates ────────────────────────────────────────────────

async function loadDraftTemplates() {
  try {
    const res = await fetch('/api/v2/draft-templates');
    const data = await res.json();
    if (data.status !== 'SUCCESS') return;
    document.getElementById('d2_gsTitleInput').value  = data.gsTitle  || '';
    document.getElementById('d2_gsBodyInput').value   = data.gsBody   || '';
    document.getElementById('d2_ctlTitleInput').value = data.ctlTitle || '';
    document.getElementById('d2_ctlBodyInput').value  = data.ctlBody  || '';
    document.getElementById('d2_stlTitleInput').value = data.stlTitle || '';
    document.getElementById('d2_stlBodyInput').value  = data.stlBody  || '';
  } catch (e) {
    showToast('Failed to load email templates.', 'error');
  }
}

async function saveDraftTemplates() {
  const btn = document.getElementById('saveTmplBtn');
  btn.disabled = true;
  try {
    const payload = {
      gsTitle:  document.getElementById('d2_gsTitleInput').value.trim(),
      gsBody:   document.getElementById('d2_gsBodyInput').value.trim(),
      ctlTitle: document.getElementById('d2_ctlTitleInput').value.trim(),
      ctlBody:  document.getElementById('d2_ctlBodyInput').value.trim(),
      stlTitle: document.getElementById('d2_stlTitleInput').value.trim(),
      stlBody:  document.getElementById('d2_stlBodyInput').value.trim(),
    };
    const res = await fetch('/api/v2/draft-templates', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (data.status === 'SUCCESS') showToast('Templates saved successfully.');
    else showToast(data.message || 'Failed to save templates.', 'error');
  } catch (e) {
    showToast('Failed to save templates.', 'error');
  } finally {
    btn.disabled = false;
  }
}

// ── Campus Emails ─────────────────────────────────────────────

function renderCampusEmailsTable(campuses) {
  const tbody = document.getElementById('d2_campusEmailsBody');
  if (!campuses.length) {
    tbody.innerHTML = '<tr><td colspan="4" class="px-4 py-6 text-center text-sm text-zinc-400">No campus emails configured.</td></tr>';
    return;
  }
  tbody.innerHTML = campuses.map((c, i) => `
    <tr style="background:${i%2===0?'var(--surface-lowest)':'var(--surface-low)'}">
      <td class="px-4 py-2.5">
        <input type="text" value="${c.campus_ko||''}"
          class="w-full px-2 py-1 text-sm border-2 rounded-lg" style="background:var(--surface-lowest);color:var(--on-surface);border-color:var(--outline-variant)">
      </td>
      <td class="px-4 py-2.5">
        <input type="text" value="${c.campus_en||''}"
          class="w-full px-2 py-1 text-sm border-2 rounded-lg" style="background:var(--surface-lowest);color:var(--on-surface);border-color:var(--outline-variant)">
      </td>
      <td class="px-4 py-2.5">
        <input type="email" value="${c.gs_email||''}"
          class="w-full px-2 py-1 text-sm border-2 rounded-lg" style="background:var(--surface-lowest);color:var(--on-surface);border-color:var(--outline-variant)">
      </td>
      <td class="px-4 py-2.5">
        <input type="email" value="${c.ctl_email||''}"
          class="w-full px-2 py-1 text-sm border-2 rounded-lg" style="background:var(--surface-lowest);color:var(--on-surface);border-color:var(--outline-variant)">
      </td>
    </tr>
  `).join('');
}

async function loadDraftCampusEmailsWithStore() {
  try {
    const res = await fetch('/api/v2/draft-campus-emails');
    const data = await res.json();
    _campusEmailsData = JSON.parse(JSON.stringify(data.campuses || []));
    renderCampusEmailsTable(_campusEmailsData);
    renderCampusCheckboxes(_campusEmailsData);
  } catch (e) {
    showToast('Failed to load campus emails.', 'error');
  }
}

async function refreshCampusEmails() {
  const btn = document.getElementById('refreshCampusEmailsBtn');
  btn.disabled = true;
  const icon = btn.querySelector('i');
  icon.classList.add('spin');
  try {
    const res = await fetch('/api/v2/sync-campus-emails', {  // notifications.py
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
    });
    const data = await res.json();
    if (data.status === 'SUCCESS') {
      _campusEmailsData = data.campuses || [];
      renderCampusEmailsTable(_campusEmailsData);
      renderCampusCheckboxes(_campusEmailsData);
      showToast(`${data.count} campus${data.count !== 1 ? 'es' : ''} synced from user accounts.`);
    } else {
      showToast(data.message || 'Sync failed.', 'error');
    }
  } catch (e) {
    showToast('Sync failed.', 'error');
  } finally {
    btn.disabled = false;
    icon.classList.remove('spin');
  }
}

async function saveDraftCampusEmails() {
  const btn = document.getElementById('saveCampusEmailsBtn');
  btn.disabled = true;
  try {
    // Collect current values from table inputs
    const rows = document.querySelectorAll('#d2_campusEmailsBody tr');
    const campuses = [];
    rows.forEach(row => {
      const inputs = row.querySelectorAll('input');
      if (inputs.length === 4) {
        campuses.push({
          campus_ko: inputs[0].value.trim(),
          campus_en: inputs[1].value.trim(),
          gs_email:  inputs[2].value.trim(),
          ctl_email: inputs[3].value.trim(),
        });
      }
    });
    const res = await fetch('/api/v2/draft-campus-emails', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ campuses }),
    });
    const data = await res.json();
    if (data.status === 'SUCCESS') {
      showToast('Campus emails saved successfully.');
      renderCampusCheckboxes(campuses);
    } else {
      showToast(data.message || 'Failed to save campus emails.', 'error');
    }
  } catch (e) {
    showToast('Failed to save campus emails.', 'error');
  } finally {
    btn.disabled = false;
  }
}

// ── Campus Checkboxes ─────────────────────────────────────────

function renderCampusCheckboxes(campuses) {
  const grid = document.getElementById('d2_campusCheckboxGrid');
  if (!campuses.length) {
    grid.innerHTML = '<span class="text-xs text-zinc-400">No campuses available. Add campus emails above first.</span>';
    return;
  }
  grid.innerHTML = campuses
    .filter(c => c.campus_ko)
    .map(c => `
      <label class="flex items-center gap-1.5 cursor-pointer select-none px-3 py-1.5 rounded-lg border-2 text-sm font-semibold transition-all"
        style="border-color:var(--outline-variant);color:var(--on-surface-variant)"
        onmouseenter="this.style.borderColor='var(--primary-dark)';this.style.color='var(--primary-dark)'"
        onmouseleave="if(!this.querySelector('input').checked){this.style.borderColor='var(--outline-variant)';this.style.color='var(--on-surface-variant)'}"
        id="cb-label-${c.campus_ko}">
        <input type="checkbox" class="d2-campus-cb" value="${c.campus_ko}"
          onchange="d2_onCampusCbChange(this,'${c.campus_ko}')">
        ${c.campus_ko}
      </label>
    `).join('');
}

function d2_onCampusCbChange(cb, campus) {
  const label = document.getElementById('cb-label-' + campus);
  if (cb.checked) {
    label.style.borderColor = 'var(--primary-dark)';
    label.style.color = 'var(--primary-dark)';
    label.style.background = 'var(--primary-soft)';
  } else {
    label.style.borderColor = 'var(--outline-variant)';
    label.style.color = 'var(--on-surface-variant)';
    label.style.background = '';
  }
}

function d2_selectAllCampuses() {
  document.querySelectorAll('.d2-campus-cb').forEach(cb => {
    cb.checked = true;
    d2_onCampusCbChange(cb, cb.value);
  });
}

function d2_getSelectedCampuses() {
  return [...document.querySelectorAll('.d2-campus-cb:checked')].map(cb => cb.value);
}

// ── Generate Drafts ───────────────────────────────────────────

async function d2_generateGSTLDrafts() {
  const campuses = d2_getSelectedCampuses();
  if (!campuses.length) {
    showToast('Please select at least one campus.', 'error');
    return;
  }
  const sessionId = d2_getSelectedSession();
  await d2_runDraftGeneration({ targetGroup: 'CAMPUS', campuses, sessionId });
}

async function d2_generateSTLDraft() {
  const sessionId = d2_getSelectedSession();
  await d2_runDraftGeneration({ targetGroup: 'SUB', campuses: ['SUB'], sessionId });
}

async function d2_runDraftGeneration(payload) {
  const gsBtn  = document.getElementById('d2_gsBtn');
  const stlBtn = document.getElementById('d2_stlBtn');
  const msg    = document.getElementById('d2_draftMsg');
  gsBtn.disabled = stlBtn.disabled = true;
  msg.className = 'mt-3 text-sm font-semibold';
  msg.style.color = 'var(--outline)';
  msg.textContent = 'Generating drafts...';
  msg.classList.remove('hidden');
  try {
    const res = await fetch('/api/v2/create-drafts', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (data.status === 'SUCCESS') {
      msg.style.color = 'var(--success)';
      msg.textContent = data.message || 'Drafts created successfully.';
      showToast(data.message || 'Drafts created successfully.');
    } else {
      msg.style.color = 'var(--error)';
      msg.textContent = data.message || 'Failed to create drafts.';
      showToast(data.message || 'Failed to create drafts.', 'error');
    }
  } catch (e) {
    msg.style.color = 'var(--error)';
    msg.textContent = 'Request failed. Please try again.';
    showToast('Request failed.', 'error');
  } finally {
    gsBtn.disabled = stlBtn.disabled = false;
  }
}
