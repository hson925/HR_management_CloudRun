/* ── Narrative Analysis Page — Admin only ── */
(function () {

  // portal_roles 캐시 워밍 — comments 의 rater_role label 표시 즉시 사용 가능.
  if (typeof window.dybLoadRoleLabels === 'function') window.dybLoadRoleLabels();

  // ── state ──────────────────────────────────────────────────────────────────
  let allTeachers = [];
  let currentEmpId = null;
  let currentModalData = null;
  let currentTab = 'summary';
  let campuses = [];
  let lastCsMap = {};  // cached campus_summaries from server

  const sessionSel = document.getElementById('an_sessionSelect');
  const container  = document.getElementById('an_container');
  const campusSel  = document.getElementById('an_campusSelect');

  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function _parseFilename(disp, fallback) {
    if (!disp) return fallback;
    const utf8 = disp.match(/filename\*=UTF-8''([^;]+)/i);
    if (utf8) {
      try { return decodeURIComponent(utf8[1].trim()); } catch (_) {}
    }
    const ascii = disp.match(/filename="([^"]+)"/);
    return ascii ? ascii[1] : fallback;
  }

  function sid() { return sessionSel ? sessionSel.value : ''; }

  // ── session dropdown ───────────────────────────────────────────────────────
  async function loadSessions() {
    try {
      const res = await fetch('/api/v2/get-sessions', {
        method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}',
      });
      const data = await res.json();
      if (data.status !== 'SUCCESS') return;
      const sessions = data.sessions || [];
      sessionSel.innerHTML = '';
      sessions.forEach(s => {
        const opt = document.createElement('option');
        opt.value = s.id;
        opt.textContent = (s.label || s.id) + (s.status === 'active' ? ' ●' : '');
        sessionSel.appendChild(opt);
      });
      const first = sessions.find(s => s.status === 'active') || sessions[0];
      if (first) sessionSel.value = first.id;
    } catch (_) {}
  }

  // ── list ───────────────────────────────────────────────────────────────────
  async function loadList() {
    if (!sid()) return;
    renderLoading();
    try {
      const res = await fetch('/api/v2/analysis/list', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({sessionId: sid()}),
      });
      const data = await res.json();
      if (data.status !== 'SUCCESS') { renderError(data.message || 'Failed to load.'); return; }
      allTeachers = data.teachers || [];
      renderList();
    } catch (e) {
      renderError('Request failed: ' + e.message);
    }
  }

  function renderLoading() {
    container.innerHTML = `<div class="campus-card"><div style="padding:48px 24px;text-align:center;color:var(--text-muted)">
      <i class="bi bi-hourglass-split" style="font-size:2rem;color:var(--outline-variant)"></i>
      <p class="mt-3 text-sm">Loading...</p></div></div>`;
  }

  function renderError(msg) {
    container.innerHTML = `<div class="campus-card"><div style="padding:48px 24px;text-align:center;color:var(--error)">
      <i class="bi bi-exclamation-triangle" style="font-size:2rem"></i>
      <p class="mt-3 text-sm">${esc(msg)}</p></div></div>`;
  }

  function renderList() {
    const generated = allTeachers.filter(t => t.summary_status === 'generated').length;
    const withOpen  = allTeachers.filter(t => t.open_count > 0).length;

    const countEl = document.getElementById('an_count');
    if (countEl) countEl.textContent = `${generated} / ${withOpen} summaries generated`;

    const selBtn = document.getElementById('an_downloadSelBtn');
    if (selBtn) selBtn.disabled = true;

    if (!allTeachers.length) {
      container.innerHTML = `<div class="campus-card"><div style="padding:48px 24px;text-align:center;color:var(--text-muted)">
        <i class="bi bi-inbox" style="font-size:2rem;color:var(--outline-variant)"></i>
        <p class="mt-3 text-sm">No evaluatees found for this session.</p></div></div>`;
      return;
    }

    const BTN_STYLE = 'padding:4px 10px;font-size:.72rem;justify-content:center';
    const rows = allTeachers.map(t => {
      const noOpen = t.open_count === 0;
      const generated = t.summary_status === 'generated';
      const rowStyle = noOpen ? 'opacity:.45;' : '';
      const statusBadge = generated
        ? `<span class="badge-role badge-done" style="font-size:.7rem">✓ Generated</span>`
        : `<span style="color:var(--text-dim);font-size:.75rem">—</span>`;

      let actions;
      if (noOpen) {
        actions = `<span style="font-size:.72rem;color:var(--text-dim)">No open answers</span>`;
      } else {
        const viewBtn = `<button onclick="openModal('${esc(t.emp_id)}')" class="btn-secondary" style="${BTN_STYLE};min-width:64px"><i class="bi bi-eye"></i> View</button>`;
        const genBtn  = `<button onclick="generateOne('${esc(t.emp_id)}')" class="btn-secondary" style="${BTN_STYLE};min-width:92px" id="gen_${esc(t.emp_id)}">${generated ? '<i class="bi bi-arrow-clockwise"></i> Regen' : '<i class="bi bi-stars"></i> Generate'}</button>`;
        const pdfBtn  = generated
          ? `<button onclick="downloadPdf('${esc(t.emp_id)}')" class="btn-secondary" style="${BTN_STYLE};min-width:40px"><i class="bi bi-file-earmark-text"></i></button>`
          : `<button class="btn-secondary" style="${BTN_STYLE};min-width:40px;opacity:.35;cursor:not-allowed" disabled title="Generate summary first"><i class="bi bi-file-earmark-text"></i></button>`;
        actions = viewBtn + genBtn + pdfBtn;
      }

      return `
        <div class="flex flex-wrap items-center gap-3 px-4 py-3" style="${rowStyle}border-bottom:1px solid var(--border-soft)">
          <input type="checkbox" class="an-checkbox flex-shrink-0" data-id="${esc(t.emp_id)}"
            ${noOpen || !generated ? 'disabled' : ''}
            onchange="updateSelCount()" style="width:15px;height:15px;cursor:pointer;accent-color:var(--primary)">
          <div style="flex:1;min-width:140px">
            <p style="font-weight:700;font-size:.875rem;color:var(--text-strong)">${esc(t.name)}</p>
            <p style="font-size:.72rem;color:var(--text-muted);font-family:monospace">${esc(t.emp_id.toUpperCase())}</p>
          </div>
          <div style="flex-shrink:0;min-width:80px">
            <span style="font-size:.72rem;color:var(--on-surface-variant)">${esc(t.campus || '—')}</span>
          </div>
          <div style="flex-shrink:0;min-width:60px">
            <span class="badge-role" style="background:var(--surface-low);color:var(--on-surface-variant);border-color:var(--outline-variant);font-size:.7rem">${esc(t.type_label || t.eval_type)}</span>
          </div>
          <div style="flex-shrink:0;min-width:60px;text-align:center">
            <span style="font-size:.75rem;color:var(--on-surface-variant)">${t.open_count} resp.</span>
          </div>
          <div style="flex-shrink:0;min-width:110px">${statusBadge}</div>
          <div style="flex-shrink:0;min-width:230px;display:flex;justify-content:flex-end;align-items:center;gap:6px">${actions}</div>
        </div>`;
    }).join('');

    container.innerHTML = `
      <div class="campus-card">
        <div class="campus-header">
          <span style="font-size:.8rem;font-weight:700;color:var(--text-strong)">Evaluatees</span>
          <span style="font-size:.75rem;color:var(--on-surface-variant)">${generated} / ${withOpen} summaries generated (${allTeachers.length} total)</span>
        </div>
        <div>${rows}</div>
      </div>`;
  }

  // ── checkbox selection ─────────────────────────────────────────────────────
  window.updateSelCount = function () {
    const checked = document.querySelectorAll('.an-checkbox:checked').length;
    const selBtn = document.getElementById('an_downloadSelBtn');
    if (selBtn) selBtn.disabled = checked === 0;
  };

  // ── generate single ────────────────────────────────────────────────────────
  window.generateOne = async function (empId) {
    const btn = document.getElementById(`gen_${empId}`);
    if (btn) { btn.disabled = true; btn.innerHTML = '<i class="bi bi-hourglass-split"></i> Generating...'; }
    try {
      const res = await fetch('/api/v2/analysis/generate', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({sessionId: sid(), empId}),
      });
      const data = await res.json();
      if (data.status === 'SUCCESS') {
        const t = allTeachers.find(x => x.emp_id === empId);
        if (t) { t.summary_status = 'generated'; t.generated_at = data.generated_at; }
        renderList();
        refreshCampusFromTeachers();
        if (typeof showToast === 'function') showToast('Summary generated.', 'success');
      } else {
        if (typeof showToast === 'function') showToast(data.message || 'Generation failed.', 'error');
        if (btn) { btn.disabled = false; btn.innerHTML = '<i class="bi bi-stars"></i> Generate'; }
      }
    } catch (e) {
      if (typeof showToast === 'function') showToast('Network error: ' + e.message, 'error');
      if (btn) { btn.disabled = false; btn.innerHTML = '<i class="bi bi-stars"></i> Generate'; }
    }
  };

  // ── generate all ───────────────────────────────────────────────────────────
  window.generateAll = async function () {
    const targets = allTeachers.filter(t => t.open_count > 0 && t.summary_status !== 'generated');
    if (!targets.length) {
      if (typeof showToast === 'function') showToast('All summaries already generated.', 'success');
      return;
    }
    const wrap  = document.getElementById('an_progressWrap');
    const fill  = document.getElementById('an_progressFill');
    const label = document.getElementById('an_progressLabel');
    const pct   = document.getElementById('an_progressPct');
    if (wrap) wrap.style.display = '';

    const btn = document.getElementById('an_generateAllBtn');
    if (btn) btn.disabled = true;

    let done = 0;
    for (const t of targets) {
      if (label) label.textContent = `Generating: ${t.name} (${done + 1}/${targets.length})`;
      try {
        const res = await fetch('/api/v2/analysis/generate', {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({sessionId: sid(), empId: t.emp_id}),
        });
        const data = await res.json();
        if (data.status === 'SUCCESS') {
          t.summary_status = 'generated'; t.generated_at = data.generated_at;
        }
      } catch (_) {}
      done++;
      const p = Math.round(done / targets.length * 100);
      if (fill) fill.style.width = p + '%';
      if (pct) pct.textContent = p + '%';
    }

    if (wrap) wrap.style.display = 'none';
    if (btn) btn.disabled = false;
    renderList();
    refreshCampusFromTeachers();
    if (typeof showToast === 'function') showToast(`Generated ${done} summaries.`, 'success');
  };

  // ── modal ──────────────────────────────────────────────────────────────────
  window.openModal = async function (empId) {
    currentEmpId = empId;
    currentModalData = null;
    currentTab = 'summary';
    document.getElementById('an_modal').style.display = 'flex';
    document.getElementById('an_modalContent').innerHTML =
      '<div style="padding:32px;text-align:center;color:var(--text-muted)"><i class="bi bi-hourglass-split"></i> Loading...</div>';

    try {
      const res = await fetch('/api/v2/analysis/get', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({sessionId: sid(), empId}),
      });
      const data = await res.json();
      if (data.status !== 'SUCCESS') {
        document.getElementById('an_modalContent').innerHTML =
          `<div style="padding:32px;text-align:center;color:var(--error)">${esc(data.message || 'Load failed.')}</div>`;
        return;
      }
      currentModalData = data;
      const t = data.teacher;
      document.getElementById('an_modalName').textContent = t.name;
      document.getElementById('an_modalMeta').textContent =
        `${t.emp_id.toUpperCase()} · ${t.campus || '—'} · ${t.type_label} · ${t.session_label}`;

      const hasSummary = !!data.summary_ko;
      document.getElementById('an_downloadPdfBtn').disabled = !hasSummary;
      renderModalTab();
    } catch (e) {
      document.getElementById('an_modalContent').innerHTML =
        `<div style="padding:32px;text-align:center;color:var(--error)">Request failed: ${esc(e.message)}</div>`;
    }
  };

  // 페이지 로컬 wrapper — defer 로드된 modal_icons.js 의 글로벌 closeModal 이
  // 페이지 로컬 재정의를 덮어쓰는 race 회피 + cleanup 보존.
  window.closeAnalysisModal = function () {
    closeModal('an_modal');
    currentEmpId = null; currentModalData = null;
  };

  window.switchTab = function (tab) {
    currentTab = tab;
    document.getElementById('tab_summary').classList.toggle('active', tab === 'summary');
    document.getElementById('tab_original').classList.toggle('active', tab === 'original');
    renderModalTab();
  };

  function _parseSummarySections(text) {
    if (!text || !text.trim()) return [];
    const lines = text.trim().split('\n');
    const sections = [];
    let cur = null;
    for (const ln of lines) {
      const s = ln.trim();
      if (s.startsWith('## ')) {
        if (cur) sections.push(cur);
        cur = { title: s.slice(3).trim(), body: '' };
      } else {
        if (cur == null) cur = { title: '', body: '' };
        cur.body += ln + '\n';
      }
    }
    if (cur) sections.push(cur);
    return sections
      .map(s => ({ title: s.title, body: s.body.trim() }))
      .filter(s => s.body || s.title);
  }

  function renderModalTab() {
    if (!currentModalData) return;
    const el = document.getElementById('an_modalContent');
    if (currentTab === 'summary') {
      const sections = _parseSummarySections(currentModalData.summary_ko || '');
      if (!sections.length) {
        el.innerHTML = '<p style="color:var(--text-dim);font-size:.85rem;padding:8px 0">No summary yet. Click Generate to create one.</p>';
        return;
      }
      const html = sections.map(sec => {
        const titleHtml = sec.title
          ? `<div style="font-size:.72rem;font-weight:700;color:var(--primary);letter-spacing:.04em;padding-bottom:5px;margin-bottom:7px;border-bottom:1px solid rgba(176,17,22,.20)">${esc(sec.title)}</div>`
          : '';
        return `<div style="margin-bottom:14px">
          ${titleHtml}
          <div style="font-size:.875rem;line-height:1.75;color:var(--text-strong);white-space:pre-wrap">${esc(sec.body)}</div>
        </div>`;
      }).join('');
      el.innerHTML = `<div style="background:rgba(176,17,22,.06);border:1px solid rgba(176,17,22,.20);border-radius:4px;padding:16px 18px">${html}</div>`;
    } else {
      const comments = currentModalData.comments || [];
      if (!comments.length) {
        el.innerHTML = '<p style="color:var(--text-dim);font-size:.85rem;padding:8px 0">No open answers found.</p>';
        return;
      }
      const html = comments.map(c => {
        const answers = c.answers.map(a => `
          <div style="margin-bottom:12px;padding-left:12px;border-left:2px solid var(--outline-variant)">
            <p style="font-size:.72rem;font-weight:600;color:var(--text-muted);margin-bottom:4px">${esc(a.question_ko)}</p>
            <p style="font-size:.85rem;color:var(--text-strong);line-height:1.7">${esc(a.answer)}</p>
            ${a.translation_pending ? `<p style="font-size:.7rem;color:var(--warning);margin-top:3px">⚠ Translation pending — showing original</p>` : ''}
          </div>`).join('');
        return `<div style="margin-bottom:18px">
          <div style="display:inline-block;background:var(--strong-bg);color:var(--strong-bg-fg);font-size:.72rem;font-weight:700;padding:3px 12px;border-radius:3px;margin-bottom:10px">
            ${esc(c.rater_name)} &nbsp;·&nbsp; ${esc((typeof dybRoleLabel==='function')?dybRoleLabel(c.rater_role):c.rater_role)}
          </div>
          ${answers}
        </div>`;
      }).join('');
      el.innerHTML = html;
    }
  }

  window.regenerateCurrent = async function () {
    if (!currentEmpId) return;
    const btn = document.getElementById('an_regenerateBtn');
    if (btn) { btn.disabled = true; btn.innerHTML = '<i class="bi bi-hourglass-split"></i>'; }
    try {
      const res = await fetch('/api/v2/analysis/generate', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({sessionId: sid(), empId: currentEmpId}),
      });
      const data = await res.json();
      if (data.status === 'SUCCESS') {
        if (currentModalData) currentModalData.summary_ko = data.summary_ko;
        const t = allTeachers.find(x => x.emp_id === currentEmpId);
        if (t) { t.summary_status = 'generated'; t.generated_at = data.generated_at; }
        renderModalTab();
        renderList();
        refreshCampusFromTeachers();
        document.getElementById('an_downloadPdfBtn').disabled = false;
        if (typeof showToast === 'function') showToast('Summary regenerated.', 'success');
      } else {
        if (typeof showToast === 'function') showToast(data.message || 'Generation failed.', 'error');
      }
    } catch (e) {
      if (typeof showToast === 'function') showToast('Network error: ' + e.message, 'error');
    }
    if (btn) { btn.disabled = false; btn.innerHTML = '<i class="bi bi-arrow-clockwise"></i> Regenerate'; }
  };

  // ── PDF options popup ──────────────────────────────────────────────────────
  let _pdfOptResolver = null;

  function askPdfOptions(subtitle) {
    // Already open → treat second call as cancelled to avoid resolver overwrite.
    if (_pdfOptResolver) return Promise.resolve(null);
    return new Promise(resolve => {
      _pdfOptResolver = resolve;
      const sub = document.getElementById('an_pdfOptSubtitle');
      if (sub && subtitle) sub.textContent = subtitle;
      document.getElementById('an_pdfOptModal').style.display = 'flex';
    });
  }

  window.confirmPdfOpt = function (includeOriginal) {
    document.getElementById('an_pdfOptModal').style.display = 'none';
    if (_pdfOptResolver) { _pdfOptResolver(includeOriginal); _pdfOptResolver = null; }
  };

  window.closePdfOpt = function () {
    document.getElementById('an_pdfOptModal').style.display = 'none';
    if (_pdfOptResolver) { _pdfOptResolver(null); _pdfOptResolver = null; }
  };

  async function _fetchPdf(empId, includeOriginal) {
    return fetch('/api/v2/analysis/pdf', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({sessionId: sid(), empId, includeOriginal}),
    });
  }

  // ── PDF download (single) ──────────────────────────────────────────────────
  window.downloadPdf = async function (empId) {
    const id = empId || currentEmpId;
    if (!id) return;
    const includeOriginal = await askPdfOptions('Choose what to include in the PDF.');
    if (includeOriginal === null) return;
    try {
      const res = await _fetchPdf(id, includeOriginal);
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        if (typeof showToast === 'function') showToast(err.message || 'PDF generation failed.', 'error');
        return;
      }
      const blob = await res.blob();
      const disp = res.headers.get('Content-Disposition') || '';
      const filename = _parseFilename(disp, `${id}_analysis.pdf`);
      _triggerDownload(blob, filename);
    } catch (e) {
      if (typeof showToast === 'function') showToast('Download error: ' + e.message, 'error');
    }
  };

  // ── bulk ZIP download ──────────────────────────────────────────────────────
  window.downloadSelected = async function (all) {
    const targets = all
      ? allTeachers.filter(t => t.summary_status === 'generated')
      : Array.from(document.querySelectorAll('.an-checkbox:checked'))
          .map(cb => allTeachers.find(t => t.emp_id === cb.dataset.id))
          .filter(Boolean);

    if (!targets.length) {
      if (typeof showToast === 'function') showToast('No summaries to download.', 'error');
      return;
    }

    const includeOriginal = await askPdfOptions(
      `Applies to all ${targets.length} report(s) in this ZIP.`
    );
    if (includeOriginal === null) return;

    const dlBtn = all
      ? document.getElementById('an_downloadAllBtn')
      : document.getElementById('an_downloadSelBtn');
    if (dlBtn) { dlBtn.disabled = true; dlBtn.innerHTML = '<i class="bi bi-hourglass-split"></i> Preparing...'; }

    try {
      const zip = new JSZip();
      let done = 0;
      for (const t of targets) {
        try {
          const res = await _fetchPdf(t.emp_id, includeOriginal);
          if (res.ok) {
            const buf = await res.arrayBuffer();
            const disp = res.headers.get('Content-Disposition') || '';
            const fname = _parseFilename(disp, `${t.emp_id}_analysis.pdf`);
            zip.file(fname, buf);
          }
        } catch (_) {}
        done++;
        if (dlBtn) dlBtn.innerHTML = `<i class="bi bi-hourglass-split"></i> ${done}/${targets.length}`;
      }
      const blob = await zip.generateAsync({type: 'blob'});
      _triggerDownload(blob, `analysis_${sid()}.zip`);
    } catch (e) {
      if (typeof showToast === 'function') showToast('ZIP error: ' + e.message, 'error');
    }

    if (dlBtn) {
      dlBtn.disabled = false;
      dlBtn.innerHTML = all
        ? '<i class="bi bi-download"></i> Download All PDFs'
        : '<i class="bi bi-file-zip"></i> Download Selected';
    }
  };

  function _triggerDownload(blob, filename) {
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = filename;
    document.body.appendChild(a); a.click();
    document.body.removeChild(a);
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  // ── Campus Report ──────────────────────────────────────────────────────────
  function _selectedCampus() {
    return campusSel && campusSel.value
      ? campuses.find(c => c.campus === campusSel.value)
      : null;
  }

  function _renderCampusStatus() {
    const statusEl = document.getElementById('an_campusStatus');
    const genBtn = document.getElementById('an_campusGenBtn');
    const dlBtn  = document.getElementById('an_campusDlBtn');
    const c = _selectedCampus();
    if (!c) {
      if (statusEl) statusEl.innerHTML = '';
      if (genBtn) genBtn.disabled = true;
      if (dlBtn)  dlBtn.disabled  = true;
      return;
    }
    const readyFrag = `<strong>${c.summaries_ready}/${c.total_teachers}</strong> teachers summarized`;
    const campusFrag = c.campus_summary_status === 'generated'
      ? `<span style="color:#15803d;font-weight:700">✓ Campus summary generated</span> <span style="color:var(--text-dim)">(${c.generated_teacher_count} teachers, ${esc(c.generated_at || '')})</span>`
      : `<span style="color:var(--text-dim)">— Campus summary not generated</span>`;
    if (statusEl) statusEl.innerHTML = `${readyFrag} &nbsp;·&nbsp; ${campusFrag}`;
    if (genBtn) {
      genBtn.disabled = c.summaries_ready === 0;
      genBtn.innerHTML = c.campus_summary_status === 'generated'
        ? '<i class="bi bi-arrow-clockwise"></i> Regenerate'
        : '<i class="bi bi-stars"></i> Generate Campus Summary';
    }
    if (dlBtn) dlBtn.disabled = c.campus_summary_status !== 'generated';
  }

  function _aggregateCampusesFromTeachers(csMap) {
    // allTeachers 에서 {campus: {total, ready}} 집계 후 csMap 메타와 병합
    const agg = {};
    for (const t of allTeachers) {
      const name = (t.campus || '').trim();
      if (!name) continue;
      if (!agg[name]) agg[name] = { campus: name, total: 0, ready: 0 };
      agg[name].total += 1;
      if (t.summary_status === 'generated') agg[name].ready += 1;
    }
    return Object.values(agg).map(a => {
      const meta = csMap[a.campus] || {};
      return {
        campus: a.campus,
        total_teachers: a.total,
        summaries_ready: a.ready,
        campus_summary_status: meta.campus_summary_status || 'none',
        generated_at: meta.generated_at || null,
        generated_teacher_count: meta.generated_teacher_count || 0,
      };
    }).sort((a, b) => a.campus.localeCompare(b.campus));
  }

  function _renderCampusOptions() {
    if (!campusSel) return;
    if (!campuses.length) {
      campusSel.disabled = true;
      campusSel.innerHTML = '<option value="">No campuses in this session</option>';
      _renderCampusStatus();
      return;
    }
    const prev = campusSel.value;
    campusSel.disabled = false;
    campusSel.innerHTML = '<option value="">— Select campus —</option>' +
      campuses.map(c =>
        `<option value="${esc(c.campus)}">${esc(c.campus)} (${c.summaries_ready}/${c.total_teachers})${c.campus_summary_status === 'generated' ? ' ✓' : ''}</option>`
      ).join('');
    if (prev && campuses.some(c => c.campus === prev)) campusSel.value = prev;
    _renderCampusStatus();
  }

  async function loadCampusList() {
    if (!sid() || !campusSel) return;
    campusSel.disabled = true;
    campusSel.innerHTML = '<option value="">Loading…</option>';
    try {
      const res = await fetch('/api/v2/analysis/campus/list', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({sessionId: sid()}),
      });
      const data = await res.json();
      lastCsMap = (data && data.status === 'SUCCESS') ? (data.campus_summaries || {}) : {};
    } catch (e) {
      lastCsMap = {};
    }
    campuses = _aggregateCampusesFromTeachers(lastCsMap);
    _renderCampusOptions();
  }

  function refreshCampusFromTeachers() {
    if (!campusSel) return;
    campuses = _aggregateCampusesFromTeachers(lastCsMap);
    _renderCampusOptions();
  }

  window.generateCampusSummary = async function () {
    const c = _selectedCampus();
    if (!c) return;
    const btn = document.getElementById('an_campusGenBtn');
    if (btn) { btn.disabled = true; btn.innerHTML = '<i class="bi bi-hourglass-split"></i> Generating...'; }
    try {
      const res = await fetch('/api/v2/analysis/campus/generate', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({sessionId: sid(), campus: c.campus}),
      });
      const data = await res.json();
      if (data.status === 'SUCCESS') {
        // lastCsMap 을 갱신해야 이후 refreshCampusFromTeachers 가 호출돼도
        // 방금 생성된 캠퍼스 summary 의 ✓ 표시가 유지됨.
        lastCsMap[c.campus] = {
          campus_summary_status: 'generated',
          generated_at: data.generated_at,
          generated_teacher_count: data.teacher_count,
        };
        refreshCampusFromTeachers();
        if (typeof showToast === 'function') showToast(`Campus summary generated (${data.teacher_count} teachers).`, 'success');
      } else {
        if (typeof showToast === 'function') showToast(data.message || 'Campus generation failed.', 'error');
        _renderCampusStatus();
      }
    } catch (e) {
      if (typeof showToast === 'function') showToast('Network error: ' + e.message, 'error');
      _renderCampusStatus();
    }
  };

  window.downloadCampusPdf = async function () {
    const c = _selectedCampus();
    if (!c || c.campus_summary_status !== 'generated') return;
    const includeOriginal = await askPdfOptions(
      `Campus report for ${c.campus} (${c.generated_teacher_count} teachers).`
    );
    if (includeOriginal === null) return;

    const btn = document.getElementById('an_campusDlBtn');
    const origHtml = btn ? btn.innerHTML : '';
    if (btn) { btn.disabled = true; btn.innerHTML = '<i class="bi bi-hourglass-split"></i> Preparing...'; }
    try {
      const res = await fetch('/api/v2/analysis/campus/pdf', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({sessionId: sid(), campus: c.campus, includeOriginal}),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        if (typeof showToast === 'function') showToast(err.message || 'PDF generation failed.', 'error');
      } else {
        const blob = await res.blob();
        const disp = res.headers.get('Content-Disposition') || '';
        const filename = _parseFilename(disp, `${c.campus}_campus_report.pdf`);
        _triggerDownload(blob, filename);
      }
    } catch (e) {
      if (typeof showToast === 'function') showToast('Download error: ' + e.message, 'error');
    }
    if (btn) { btn.disabled = false; btn.innerHTML = origHtml || '<i class="bi bi-file-earmark-text"></i> Download Campus PDF'; }
    _renderCampusStatus();
  };

  if (campusSel) {
    campusSel.addEventListener('change', _renderCampusStatus);
  }

  // ── keyboard ───────────────────────────────────────────────────────────────
  document.addEventListener('keydown', e => {
    if (e.key !== 'Escape') return;
    const optModal = document.getElementById('an_pdfOptModal');
    if (optModal && optModal.style.display === 'flex') { closePdfOpt(); return; }
    closeAnalysisModal();
  });

  // ── init ───────────────────────────────────────────────────────────────────
  async function onSessionChange() {
    await loadList();       // populates allTeachers
    await loadCampusList(); // aggregates from allTeachers + fetches campus_summaries
  }

  if (sessionSel) {
    sessionSel.addEventListener('change', onSessionChange);
    loadSessions().then(onSessionChange);
  }

})();
