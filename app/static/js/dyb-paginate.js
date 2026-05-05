/* DYB Paginate — reusable client-side pagination helper
 *
 * Usage:
 *   const p = DYBPaginate.create({
 *     wrap: '#paginationWrap',        // container for page buttons
 *     pageSize: 50,
 *     onRender: (pageItems, state) => { ... render rows ... },
 *     scrollTo: '#tableWrap',         // optional: element to scroll into view on page change
 *   });
 *   p.setData(allItems);              // resets to page 1 and renders
 *   p.setPageSize(100);
 *   p.goTo(3);
 *   p.refresh();                      // re-render current page
 */
(function (global) {
  'use strict';

  function create(opts) {
    const wrapEl = typeof opts.wrap === 'string' ? document.querySelector(opts.wrap) : opts.wrap;
    const scrollEl = opts.scrollTo
      ? (typeof opts.scrollTo === 'string' ? document.querySelector(opts.scrollTo) : opts.scrollTo)
      : null;

    const state = {
      data: [],
      pageSize: opts.pageSize || 50,
      currentPage: 1,
    };

    function totalPages() {
      return Math.max(1, Math.ceil(state.data.length / state.pageSize));
    }

    function render() {
      const tp = totalPages();
      if (state.currentPage > tp) state.currentPage = tp;
      if (state.currentPage < 1) state.currentPage = 1;

      const start = (state.currentPage - 1) * state.pageSize;
      const end = Math.min(start + state.pageSize, state.data.length);
      const slice = state.data.slice(start, end);

      if (typeof opts.onRender === 'function') {
        opts.onRender(slice, { start, end, total: state.data.length, page: state.currentPage, totalPages: tp });
      }
      renderButtons(tp);
    }

    function renderButtons(tp) {
      if (!wrapEl) return;
      if (tp <= 1) { wrapEl.innerHTML = ''; return; }

      const MAX = 7;
      let pages = [];
      if (tp <= MAX) {
        for (let i = 1; i <= tp; i++) pages.push(i);
      } else {
        const left = Math.max(2, state.currentPage - 2);
        const right = Math.min(tp - 1, state.currentPage + 2);
        pages = [1];
        if (left > 2) pages.push('…');
        for (let p = left; p <= right; p++) pages.push(p);
        if (right < tp - 1) pages.push('…');
        pages.push(tp);
      }

      const id = '_dybp_' + Math.random().toString(36).slice(2, 8);
      wrapEl.innerHTML = `
        <button class="dyb-page-btn" data-act="prev" ${state.currentPage === 1 ? 'disabled' : ''}>
          <i class="bi bi-chevron-left" style="font-size:.7rem"></i>
        </button>
        ${pages.map(p => p === '…'
          ? `<span class="dyb-page-btn" style="cursor:default;border-color:transparent">…</span>`
          : `<button class="dyb-page-btn ${p === state.currentPage ? 'active' : ''}" data-page="${p}">${p}</button>`
        ).join('')}
        <button class="dyb-page-btn" data-act="next" ${state.currentPage === tp ? 'disabled' : ''}>
          <i class="bi bi-chevron-right" style="font-size:.7rem"></i>
        </button>`;

      wrapEl.querySelectorAll('button[data-page]').forEach(b => {
        b.addEventListener('click', () => goTo(parseInt(b.dataset.page)));
      });
      const prev = wrapEl.querySelector('button[data-act="prev"]');
      const next = wrapEl.querySelector('button[data-act="next"]');
      if (prev) prev.addEventListener('click', () => goTo(state.currentPage - 1));
      if (next) next.addEventListener('click', () => goTo(state.currentPage + 1));
    }

    function goTo(p) {
      const tp = totalPages();
      if (p < 1 || p > tp || p === state.currentPage) return;
      state.currentPage = p;
      render();
      if (scrollEl) scrollEl.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }

    function setData(arr) {
      state.data = Array.isArray(arr) ? arr : [];
      state.currentPage = 1;
      render();
    }

    function setPageSize(n) {
      state.pageSize = parseInt(n) || state.pageSize;
      state.currentPage = 1;
      render();
    }

    return {
      setData, setPageSize, goTo,
      refresh: render,
      getState: () => ({ ...state, totalPages: totalPages() }),
    };
  }

  global.DYBPaginate = { create };
})(window);
