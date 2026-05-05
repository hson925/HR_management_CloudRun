/**
 * app/static/js/common/progress_modal.js
 * 공용 Bulk 작업 진행률 모달.
 *
 * 사용:
 *   const pm = ProgressModal.open({ title: 'Creating folders', total: 50 });
 *   pm.update(processed, { success, skip, error, currentLabel });
 *   if (pm.cancelled) break;
 *   pm.done({ success: true, summary: 'Done' });
 *
 * 설계 주의:
 * - layout.html 의 `.modal-backdrop` 전역 규칙을 재사용 (재정의 금지)
 * - innerHTML 재렌더 없음 — 숫자/bar width 만 갱신
 * - Tailwind 임의값 클래스 미사용 (인라인 style 사용)
 */
(function (global) {
  'use strict';

  let _instance = null;
  let _stylesInjected = false;

  function _injectStyles() {
    if (_stylesInjected) return;
    _stylesInjected = true;
    const styleEl = document.createElement('style');
    styleEl.id = 'pm-styles';
    styleEl.textContent = `
@keyframes pm-spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
@keyframes pm-stripes { from { background-position: 0 0; } to { background-position: 28px 0; } }
.pm-spin { animation: pm-spin 0.8s linear infinite; display: inline-block; }
.pm-stripes {
  background-image: linear-gradient(45deg, rgba(255,255,255,.18) 25%, transparent 25%, transparent 50%, rgba(255,255,255,.18) 50%, rgba(255,255,255,.18) 75%, transparent 75%, transparent);
  background-size: 28px 28px;
  animation: pm-stripes 1s linear infinite;
}
.pm-chip { display: none; padding: 3px 10px; border-radius: 999px; font-weight: 600; font-size: 11px; }
.pm-chip-success { color: #15803d; background: #f0fdf4; }
.pm-chip-skip    { color: #a16207; background: #fefce8; }
.pm-chip-error   { color: #b91c1c; background: #fef2f2; }
html[data-theme="dark"] .pm-chip-success { color: #86efac; background: rgba(34,197,94,.16); }
html[data-theme="dark"] .pm-chip-skip    { color: #fde68a; background: rgba(217,119,6,.16); }
html[data-theme="dark"] .pm-chip-error   { color: #fca5a5; background: rgba(239,68,68,.16); }
.pm-subtitle-success { color: #15803d; }
.pm-subtitle-error   { color: #b91c1c; }
html[data-theme="dark"] .pm-subtitle-success { color: #86efac; }
html[data-theme="dark"] .pm-subtitle-error   { color: #fca5a5; }
`;
    document.head.appendChild(styleEl);
  }

  function _el(tag, attrs, children) {
    const el = document.createElement(tag);
    if (attrs) {
      for (const [k, v] of Object.entries(attrs)) {
        if (k === 'style' && typeof v === 'object') Object.assign(el.style, v);
        else if (k === 'class') el.className = v;
        else if (k === 'text') el.textContent = v;
        else el.setAttribute(k, v);
      }
    }
    if (children) for (const c of children) el.appendChild(c);
    return el;
  }

  function _buildDom() {
    // backdrop
    const backdrop = _el('div', { class: 'modal-backdrop', style: { display: 'flex' } });
    backdrop.dataset.role = 'progress-modal';

    const box = _el('div', {
      class: 'modal-box',
      style: {
        background: 'var(--surface-lowest)',
        border: '1px solid var(--outline-variant)',
        borderRadius: '8px',
        padding: '28px 32px',
        minWidth: '420px',
        maxWidth: '560px',
        width: '100%',
        boxShadow: '0 10px 40px rgba(0,0,0,0.15)',
      },
    });

    // title row — spinner + text
    const titleRow = _el('div', {
      style: {
        display: 'flex',
        alignItems: 'center',
        gap: '10px',
        marginBottom: '4px',
      },
    });
    const spinner = _el('i', {
      class: 'bi bi-arrow-repeat pm-spin',
      style: {
        fontSize: '20px',
        color: 'var(--primary)',
        flexShrink: '0',
      },
    });
    const title = _el('div', {
      style: {
        fontSize: '16px',
        fontWeight: '700',
        color: 'var(--on-surface)',
      },
    });
    titleRow.appendChild(spinner);
    titleRow.appendChild(title);

    const subtitle = _el('div', {
      style: {
        fontSize: '12px',
        color: 'var(--on-surface-variant)',
        marginBottom: '18px',
        whiteSpace: 'pre-line',
      },
    });

    // Progress bar wrapper
    const barWrap = _el('div', {
      style: {
        position: 'relative',
        width: '100%',
        height: '10px',
        background: 'var(--surface-low)',
        border: '1px solid var(--outline-variant)',
        borderRadius: '999px',
        overflow: 'hidden',
        marginBottom: '10px',
      },
    });
    const barFill = _el('div', {
      style: {
        position: 'absolute',
        top: '0',
        left: '0',
        bottom: '0',
        width: '0%',
        background: 'linear-gradient(90deg,#B01116,#e53935)',
        transition: 'width .25s ease',
        overflow: 'hidden',
      },
    });
    // stripe overlay — fill 안에서 흐르며 진행 중 시각 시그널
    const barStripe = _el('div', {
      class: 'pm-stripes',
      style: {
        position: 'absolute',
        top: '0', left: '0', right: '0', bottom: '0',
      },
    });
    barFill.appendChild(barStripe);
    barWrap.appendChild(barFill);

    // Count line
    const countRow = _el('div', {
      style: {
        display: 'flex',
        justifyContent: 'space-between',
        fontSize: '12px',
        color: 'var(--on-surface-variant)',
        marginBottom: '14px',
      },
    });
    const countLeft  = _el('span', { style: { fontWeight: '600' } });
    const countRight = _el('span', { style: { fontFamily: 'monospace' } });
    countRow.appendChild(countLeft);
    countRow.appendChild(countRight);

    // Stats
    const statsRow = _el('div', {
      style: {
        display: 'flex',
        gap: '10px',
        flexWrap: 'wrap',
        fontSize: '11px',
        marginBottom: '14px',
      },
    });

    function _chip(variant, label) {
      const chip = _el('span', { class: 'pm-chip pm-chip-' + variant });
      chip.dataset.label = label;
      return chip;
    }
    const chipSuccess = _chip('success', 'success');
    const chipSkip    = _chip('skip',    'skip');
    const chipError   = _chip('error',   'error');
    statsRow.appendChild(chipSuccess);
    statsRow.appendChild(chipSkip);
    statsRow.appendChild(chipError);

    // Current item line
    const currentLine = _el('div', {
      style: {
        fontSize: '11px',
        color: 'var(--outline)',
        fontFamily: 'monospace',
        minHeight: '16px',
        marginBottom: '20px',
        whiteSpace: 'nowrap',
        overflow: 'hidden',
        textOverflow: 'ellipsis',
      },
    });

    // Buttons
    const btnRow = _el('div', {
      style: {
        display: 'flex',
        justifyContent: 'flex-end',
        gap: '8px',
      },
    });

    const btnCancel = _el('button', {
      type: 'button',
      style: {
        padding: '8px 18px',
        borderRadius: '6px',
        border: '1px solid var(--outline-variant)',
        background: 'var(--surface-lowest)',
        color: 'var(--on-surface-variant)',
        fontSize: '13px',
        fontWeight: '600',
        cursor: 'pointer',
      },
      text: 'Cancel',
    });

    const btnClose = _el('button', {
      type: 'button',
      style: {
        display: 'none',
        padding: '8px 18px',
        borderRadius: '6px',
        border: 'none',
        background: 'var(--on-surface)',
        color: 'var(--surface-lowest)',
        fontSize: '13px',
        fontWeight: '600',
        cursor: 'pointer',
      },
      text: 'Close',
    });

    btnRow.appendChild(btnCancel);
    btnRow.appendChild(btnClose);

    box.appendChild(titleRow);
    box.appendChild(subtitle);
    box.appendChild(barWrap);
    box.appendChild(countRow);
    box.appendChild(statsRow);
    box.appendChild(currentLine);
    box.appendChild(btnRow);
    backdrop.appendChild(box);

    return {
      backdrop, box, spinner, title, subtitle,
      barFill, barStripe, countLeft, countRight,
      chipSuccess, chipSkip, chipError,
      currentLine, btnCancel, btnClose,
    };
  }

  function open(opts) {
    if (_instance) _instance.destroy();
    _injectStyles();

    const dom = _buildDom();
    const _abort = new AbortController();
    const state = {
      total: Math.max(0, Number(opts.total) || 0),
      processed: 0,
      success: 0, skip: 0, error: 0,
      cancelled: false,
      finished: false,
    };

    dom.title.textContent = opts.title || 'Processing...';
    if (opts.subtitle) dom.subtitle.textContent = opts.subtitle;
    else dom.subtitle.style.display = 'none';

    dom.countLeft.textContent  = '0%';
    dom.countRight.textContent = `0 / ${state.total}`;

    document.body.appendChild(dom.backdrop);

    const inst = {
      get cancelled() { return state.cancelled; },
      get total()     { return state.total; },
      get signal()    { return _abort.signal; },

      update(processed, stats) {
        if (state.finished) return;
        state.processed = Math.min(state.total, Math.max(0, Number(processed) || 0));
        if (stats) {
          if (typeof stats.success === 'number') state.success = stats.success;
          if (typeof stats.skip    === 'number') state.skip    = stats.skip;
          if (typeof stats.error   === 'number') state.error   = stats.error;
        }
        const pct = state.total > 0 ? Math.round((state.processed / state.total) * 100) : 0;
        dom.barFill.style.width = pct + '%';
        dom.countLeft.textContent  = pct + '%';
        dom.countRight.textContent = `${state.processed} / ${state.total}`;

        function _setChip(chip, label, n) {
          if (n > 0) {
            chip.style.display = 'inline-block';
            chip.textContent = `${label} ${n}`;
          } else {
            chip.style.display = 'none';
          }
        }
        _setChip(dom.chipSuccess, '성공', state.success);
        _setChip(dom.chipSkip,    '건너뜀', state.skip);
        _setChip(dom.chipError,   '실패', state.error);

        if (stats && typeof stats.currentLabel === 'string') {
          dom.currentLine.textContent = stats.currentLabel ? `→ ${stats.currentLabel}` : '';
        }
      },

      done(opts2) {
        if (state.finished) return;
        state.finished = true;
        opts2 = opts2 || {};
        const success = opts2.success !== false;

        // 진행 시그널 정지 — spinner 숨김 + bar stripe 애니메이션 제거
        dom.spinner.style.display = 'none';
        dom.barStripe.classList.remove('pm-stripes');

        const grad  = success
          ? 'linear-gradient(90deg,#15803d,#22c55e)'
          : 'linear-gradient(90deg,#7a0b0f,#B01116)';
        dom.barFill.style.background = grad;
        if (state.total > 0 && state.processed < state.total && success) {
          // 완료 지만 total 에 도달 안함 — bar 를 100% 로 강제하지 않음 (cancelled 등)
        } else if (success) {
          dom.barFill.style.width = '100%';
          dom.countLeft.textContent = '100%';
        }
        // subtitle — 클래스 기반으로 다크 모드 호환
        dom.subtitle.classList.remove('pm-subtitle-success', 'pm-subtitle-error');
        dom.subtitle.classList.add(success ? 'pm-subtitle-success' : 'pm-subtitle-error');
        dom.subtitle.style.color = '';  // 인라인 색상 제거 (CSS 클래스에 위임)
        dom.subtitle.style.display = 'block';
        if (opts2.summary) {
          dom.subtitle.textContent = opts2.summary;
        } else {
          dom.subtitle.textContent = success ? 'Completed.' : 'Finished with errors.';
        }
        dom.btnCancel.style.display = 'none';
        dom.btnClose.style.display  = 'inline-block';

        // autoCloseMs 옵션 — caller 명시 시 자동 닫기. destroy() 의 parentNode
        // 가드로 사용자가 그 사이 close 버튼 눌러도 안전 (no-op).
        if (typeof opts2.autoCloseMs === 'number' && opts2.autoCloseMs > 0) {
          setTimeout(() => inst.destroy(), opts2.autoCloseMs);
        }
      },

      destroy() {
        if (dom.backdrop && dom.backdrop.parentNode) {
          dom.backdrop.parentNode.removeChild(dom.backdrop);
        }
        if (_instance === inst) _instance = null;
      },

      cancel() {
        state.cancelled = true;
        try { _abort.abort(); } catch (e) { /* ignore double-abort */ }
      },
    };

    dom.btnCancel.addEventListener('click', () => {
      if (state.finished) {
        inst.destroy();
        return;
      }
      if (confirm('작업을 중단하시겠습니까?\nAlready-processed items will not be rolled back.')) {
        inst.cancel();   // state.cancelled + abort() 동시 — AbortController 즉시 abort
        dom.btnCancel.disabled = true;
        dom.btnCancel.textContent = 'Cancelled';
        dom.btnCancel.style.opacity = '0.5';
      }
    });
    dom.btnClose.addEventListener('click', () => inst.destroy());

    _instance = inst;
    return inst;
  }

  global.ProgressModal = { open };
})(window);
