/* ── Campus Status Page (GS/TL) — submission-only view, no scores/comments ── */

(function () {
  const sessionSel  = document.getElementById('cs_sessionSelect');
  const typeSel     = document.getElementById('cs_typeSelect');
  const container   = document.getElementById('cs_container');
  const campusLabel = document.getElementById('cs_campusLabel');

  if (!sessionSel || !container) return;

  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  // Lucide CDN 로드 실패 시 silent 처리
  function tryLucide() {
    if (typeof lucide !== 'undefined') lucide.createIcons();
  }

  function lucideIcon(name, size, sw, style) {
    return `<i data-lucide="${esc(name)}" style="width:${size || 14}px;height:${size || 14}px;stroke-width:${sw || 2};display:inline-block;vertical-align:middle;${style || ''}"></i>`;
  }

  const TYPE_INFO = {
    position: { cls: 'badge-type-position', icon: 'star'        },
    regular:  { cls: 'badge-type-regular',  icon: 'user'        },
    tl:       { cls: 'badge-type-tl',       icon: 'users'       },
    sub:      { cls: 'badge-type-sub',       icon: 'replace'    },
    stl:      { cls: 'badge-type-stl',       icon: 'shield-half'},
  };

  // 초기 로드 시 캠퍼스 레이블을 영어 코드로 변환
  function initCampusLabel() {
    if (!campusLabel) return;
    const koName = campusLabel.textContent.trim();
    const en = (typeof CAMPUS_EN !== 'undefined' ? CAMPUS_EN[koName] : null) || koName;
    campusLabel.textContent = en;
  }

  async function loadSessions() {
    try {
      // portal_roles 캐시 워밍 — role badge label 즉시 표시.
      if (typeof window.dybLoadRoleLabels === 'function') window.dybLoadRoleLabels();
      const res = await fetch('/api/v2/get-sessions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: '{}',
      });
      const data = await res.json();
      if (data.status !== 'SUCCESS') return [];
      return data.sessions || [];
    } catch (_) {
      return [];
    }
  }

  function populateSessionDropdown(sessions) {
    sessionSel.innerHTML = '';
    if (!sessions.length) {
      const opt = document.createElement('option');
      opt.value = ''; opt.textContent = 'No sessions available';
      sessionSel.appendChild(opt);
      return '';
    }
    sessions.forEach(s => {
      const opt = document.createElement('option');
      opt.value = s.id;
      const mark = s.status === 'active' ? ' ●' : '';
      opt.textContent = (s.label || s.id) + mark;
      sessionSel.appendChild(opt);
    });
    const firstActive = sessions.find(s => s.status === 'active');
    const chosen = firstActive ? firstActive.id : sessions[0].id;
    sessionSel.value = chosen;
    return chosen;
  }

  async function fetchCampusStatus(sessionId, evalType) {
    const body = {};
    if (sessionId) body.sessionId = sessionId;
    if (evalType)  body.evalType  = evalType;
    const res = await fetch('/api/v2/get-campus-status', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    return res.json();
  }

  function renderEmpty(msg, iconName) {
    container.innerHTML = `
      <div class="cs-card">
        <div class="cs-empty">
          <div class="cs-empty-icon">
            ${lucideIcon(iconName || 'inbox', 28, 1.5)}
          </div>
          <p class="text-sm" style="color:var(--text-muted)">${esc(msg)}</p>
        </div>
      </div>`;
    tryLucide();
  }

  function renderTeachers(data) {
    const teachers = data.teachers || [];

    const campusEn = (typeof CAMPUS_EN !== 'undefined' ? CAMPUS_EN[data.campus] : null) || data.campus;
    if (campusLabel) campusLabel.textContent = campusEn;

    if (!teachers.length) {
      renderEmpty('No teachers found for this campus / session.', 'inbox');
      return;
    }

    const completed = teachers.filter(t => t.allDone).length;
    const pct = teachers.length ? Math.round(completed / teachers.length * 100) : 0;

    const _rl = (typeof dybRoleLabel === 'function') ? dybRoleLabel : (n => n);
    const _resolveLbl = s => s.label || _rl(s.role) || s.role;
    const rowsHtml = teachers.map(t => {
      const roleBadges = (t.status || []).map(s => {
        const cls  = s.done ? 'badge-done' : 'badge-pending';
        const mark = s.done ? '✓' : '·';
        return `<span class="badge-base ${cls}">${mark} ${esc(_resolveLbl(s))} ${s.current}/${s.required}</span>`;
      }).join('');

      const info = TYPE_INFO[t.type] || TYPE_INFO.regular;

      const overallCls = t.allDone ? 'done' : 'pending';
      const overallIco = t.allDone
        ? lucideIcon('circle-check-big', 15, 2.5, 'flex-shrink:0')
        : lucideIcon('clock-3', 14, 2, 'flex-shrink:0');
      const overallTxt = t.allDone ? 'Complete' : 'Pending';

      // 역할별 평가자 드롭다운 패널
      const ratersRows = (t.status || []).map(s => {
        const nameList = s.raters && s.raters.length
          ? s.raters.map(r => `<span style="display:inline-block;background:var(--surface);border:1px solid var(--border);border-radius:4px;padding:1px 8px;margin:2px 3px 2px 0;font-size:.76rem;color:var(--text)">${esc(r)}</span>`).join('')
          : `<span style="font-size:.75rem;color:var(--text-dim);font-style:italic">No submissions</span>`;
        const statusDot = s.done
          ? '<span style="display:inline-block;width:7px;height:7px;border-radius:50%;background:#22c55e;flex-shrink:0;margin-top:2px"></span>'
          : '<span style="display:inline-block;width:7px;height:7px;border-radius:50%;background:#fbbf24;flex-shrink:0;margin-top:2px"></span>';
        return `<div class="cs-rater-row">
          ${statusDot}
          <span class="cs-rater-label">${esc(_resolveLbl(s))}</span>
          <span class="cs-rater-names">${nameList}</span>
        </div>`;
      }).join('');

      return `
        <div class="teacher-row${t.allDone ? ' done' : ''}" data-cs-toggle>
          <div class="teacher-row-grid">
            <div style="min-width:0">
              <p style="font-weight:600;font-size:.875rem;color:var(--text-strong);white-space:nowrap;overflow:hidden;text-overflow:ellipsis">
                ${esc(t.name)}
              </p>
              <p class="teacher-empid">${esc(String(t.id).toUpperCase())}</p>
            </div>
            <div>
              <span class="badge-type ${info.cls}">${esc(t.typeLabel || t.type)}</span>
            </div>
            <div class="role-badges">
              ${roleBadges || '<span style="color:var(--text-dim);font-size:.75rem">—</span>'}
            </div>
            <div class="overall-status ${overallCls}">
              ${overallIco}
              <span>${overallTxt}</span>
              <i class="bi bi-chevron-down cs-chevron" style="font-size:.65rem;color:var(--text-dim);margin-left:4px;transition:transform .2s;flex-shrink:0"></i>
            </div>
          </div>
          <div class="cs-detail-panel">
            ${ratersRows || '<span style="font-size:.75rem;color:var(--text-dim)">No submission data available.</span>'}
          </div>
        </div>`;
    }).join('');

    container.innerHTML = `
      <div class="cs-card">
        <div class="cs-card-header">
          <div class="flex items-center gap-3">
            <i class="bi bi-geo-alt-fill" style="color:var(--primary);font-size:.9rem;flex-shrink:0"></i>
            <span style="font-size:1rem;font-weight:700;color:var(--text-strong)">${esc(campusEn)}</span>
            <span style="font-size:.8rem;font-weight:500;color:var(--text-muted)">${completed} / ${teachers.length} complete</span>
          </div>
          <div style="display:flex;align-items:center;gap:8px;min-width:120px">
            <div class="cs-progress-track">
              <div class="cs-progress-fill" style="width:${pct}%"></div>
            </div>
            <span style="font-size:.75rem;font-weight:700;color:var(--success);min-width:32px;text-align:right">${pct}%</span>
          </div>
        </div>
        <div style="padding:12px 16px;display:flex;flex-direction:column;gap:6px">
          ${rowsHtml}
        </div>
      </div>`;

    tryLucide();
  }

  async function refresh() {
    const sid   = sessionSel.value;
    const etype = typeSel ? typeSel.value : '';
    container.innerHTML = `
      <div class="cs-card">
        <div class="cs-empty">
          <div class="cs-empty-icon">
            <span class="cs-spin" style="display:flex">
              <i class="bi bi-arrow-clockwise" style="width:28px;height:28px;stroke-width:1.5;color:var(--text-dim)"></i>
            </span>
          </div>
          <p class="text-sm" style="color:var(--text-muted)">Loading...</p>
        </div>
      </div>`;
    tryLucide();

    try {
      const data = await fetchCampusStatus(sid, etype);
      if (data.status !== 'SUCCESS') {
        if (data.code === 'NO_CAMPUS') {
          renderEmpty('Campus not assigned. Please contact admin.', 'building-2');
        } else {
          renderEmpty(data.message || 'Failed to load.', 'triangle-alert');
        }
        return;
      }
      renderTeachers(data);
    } catch (_) {
      renderEmpty('Request failed. Please try again.', 'wifi-off');
    }
  }

  sessionSel.addEventListener('change', refresh);
  if (typeSel) typeSel.addEventListener('change', refresh);

  container.addEventListener('click', (ev) => {
    const row = ev.target.closest('[data-cs-toggle]');
    if (!row || !container.contains(row)) return;
    const panel   = row.querySelector('.cs-detail-panel');
    const chevron = row.querySelector('.cs-chevron');
    if (!panel) return;
    const opening = !panel.classList.contains('open');
    panel.classList.toggle('open', opening);
    if (chevron) chevron.style.transform = opening ? 'rotate(180deg)' : '';
  });

  (async () => {
    initCampusLabel();
    const sessions = await loadSessions();
    populateSessionDropdown(sessions);
    await refresh();
  })();
})();
