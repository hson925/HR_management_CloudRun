/**
 * DYBDatepicker — Single-date custom picker (DYB red theme)
 *
 * Usage:
 *   DYBDatepicker.replace('inputId', { placeholder: 'Select', onChange: fn });
 *   DYBDatepicker.setValue('inputId', '2026-04-14');
 *   DYBDatepicker.getValue('inputId');  // → 'YYYY-MM-DD' or ''
 *
 * The original input element is converted to type="hidden" and its value
 * stays in sync, so existing code reading `getElementById('id').value` works.
 */
(function (global) {
  'use strict';

  const MONTHS_LONG  = ['January','February','March','April','May','June',
                         'July','August','September','October','November','December'];
  const MONTHS_SHORT = ['Jan','Feb','Mar','Apr','May','Jun',
                         'Jul','Aug','Sep','Oct','Nov','Dec'];
  const WEEKDAYS     = ['Su','Mo','Tu','We','Th','Fr','Sa'];

  /* ── Registry: inputId → instance ── */
  const _registry = {};

  class DYBDatepicker {

    /**
     * @param {HTMLInputElement} inputEl  The original input element to replace.
     * @param {object}           options  { placeholder, onChange }
     */
    constructor(inputEl, options = {}) {
      this.input       = inputEl;
      this.placeholder = options.placeholder || 'Select date';
      this.onChange    = options.onChange    || null;
      this.uid         = 'dyb-dp-' + Math.random().toString(36).slice(2, 8);

      // State
      const now = new Date();
      this.selected    = this.input.value || '';
      this.year        = now.getFullYear();
      this.month       = now.getMonth();
      this.selYearBase = this.year - 4;

      if (this.selected) {
        const d = new Date(this.selected + 'T00:00:00');
        if (!isNaN(d)) {
          this.year        = d.getFullYear();
          this.month       = d.getMonth();
          this.selYearBase = this.year - 4;
        }
      }

      this._build();
      if (this.input.id) _registry[this.input.id] = this;
    }

    /* ── Build DOM ── */
    _build() {
      // Convert original input to hidden
      this.input.type = 'hidden';

      // Wrapper
      const wrap = document.createElement('div');
      wrap.className = 'dp-wrap';
      wrap.id = this.uid + '-wrap';
      this.input.parentNode.insertBefore(wrap, this.input);
      wrap.appendChild(this.input);
      this._wrap = wrap;

      // Input box (visible trigger)
      wrap.insertAdjacentHTML('afterbegin', `
        <div class="dp-input-box" id="${this.uid}-box">
          <i class="bi bi-calendar3 dp-icon"></i>
          <span class="dp-label placeholder" id="${this.uid}-label">${this._esc(this.placeholder)}</span>
          <i class="bi bi-chevron-down dp-caret"></i>
        </div>
        <div class="dp-popup" id="${this.uid}-popup" style="display:none">
          <!-- Calendar view -->
          <div id="${this.uid}-cal">
            <div class="dp-header">
              <button class="dp-nav-btn" id="${this.uid}-prev"><i class="bi bi-chevron-left"></i></button>
              <span class="dp-month-label" id="${this.uid}-monthlabel">
                <span id="${this.uid}-monthtext"></span>
                <i class="bi bi-chevron-down dp-sel-caret" id="${this.uid}-selcaret"></i>
              </span>
              <button class="dp-nav-btn" id="${this.uid}-next"><i class="bi bi-chevron-right"></i></button>
            </div>
            <div class="dp-grid" id="${this.uid}-grid"></div>
            <div class="dp-footer">
              <button class="dp-today-btn" id="${this.uid}-today">
                <i class="bi bi-dot" style="font-size:1.1rem;margin:-2px -2px -2px -4px"></i> Today
              </button>
              <button class="dp-clear-btn" id="${this.uid}-clear">Clear</button>
            </div>
          </div>
          <!-- Selector view -->
          <div class="dp-sel-view" id="${this.uid}-sel">
            <div class="dp-sel-header">
              <button class="dp-nav-btn" id="${this.uid}-selprev"><i class="bi bi-chevron-left"></i></button>
              <span class="dp-sel-title">Year &amp; Month</span>
              <button class="dp-nav-btn" id="${this.uid}-selnext"><i class="bi bi-chevron-right"></i></button>
            </div>
            <div class="dp-year-grid" id="${this.uid}-yeargrid"></div>
            <div class="dp-sel-divider"></div>
            <div class="dp-month-grid" id="${this.uid}-monthgrid"></div>
            <div class="dp-sel-footer">
              <button class="dp-back-btn" id="${this.uid}-back">
                <i class="bi bi-arrow-left"></i> Back
              </button>
            </div>
          </div>
        </div>
      `);

      this._bindEvents();
      this._updateDisplay();
    }

    _bindEvents() {
      const $ = id => document.getElementById(id);

      // Toggle popup
      $(`${this.uid}-box`).addEventListener('click', () => this._togglePopup());

      // Month navigation
      $(`${this.uid}-prev`).addEventListener('click', e => { e.stopPropagation(); this._prevMonth(); });
      $(`${this.uid}-next`).addEventListener('click', e => { e.stopPropagation(); this._nextMonth(); });

      // Month label → selector
      $(`${this.uid}-monthlabel`).addEventListener('click', e => { e.stopPropagation(); this._toggleSel(); });

      // Today / Clear
      $(`${this.uid}-today`).addEventListener('click', e => { e.stopPropagation(); this._goToday(); });
      $(`${this.uid}-clear`).addEventListener('click', e => { e.stopPropagation(); this._clear(); });

      // Selector prev/next year range
      $(`${this.uid}-selprev`).addEventListener('click', e => { e.stopPropagation(); this.selYearBase -= 9; this._renderSel(); });
      $(`${this.uid}-selnext`).addEventListener('click', e => { e.stopPropagation(); this.selYearBase += 9; this._renderSel(); });

      // Back button
      $(`${this.uid}-back`).addEventListener('click', e => { e.stopPropagation(); this._toggleSel(); });

      // Close on outside click
      document.addEventListener('click', e => {
        const wrap = document.getElementById(`${this.uid}-wrap`);
        if (wrap && !wrap.contains(e.target)) this._closePopup();
      });
    }

    /* ── Popup open/close ── */
    _togglePopup() {
      const popup = document.getElementById(`${this.uid}-popup`);
      const box   = document.getElementById(`${this.uid}-box`);
      if (popup.style.display === 'none') {
        // Reset to cal view before opening
        this._showCalView();
        this._renderCal();
        popup.style.display = '';
        box.classList.add('open');
        this._positionPopup();
        // Reposition on scroll/resize while open
        this._scrollHandler = () => this._positionPopup();
        this._resizeHandler = () => this._positionPopup();
        window.addEventListener('scroll', this._scrollHandler, true);
        window.addEventListener('resize', this._resizeHandler);
      } else {
        this._closePopup();
      }
    }

    _positionPopup() {
      const popup   = document.getElementById(`${this.uid}-popup`);
      const box     = document.getElementById(`${this.uid}-box`);
      if (!popup || !box) return;
      const POPUP_H = 340; // approximate max height
      const POPUP_W = 264;
      const GAP     = 6;
      const MARGIN  = 8;

      const b = box.getBoundingClientRect();

      // Horizontal: align left edge with trigger, but clamp to viewport
      let left = b.left;
      if (left + POPUP_W > window.innerWidth - MARGIN) {
        left = window.innerWidth - POPUP_W - MARGIN;
      }
      if (left < MARGIN) left = MARGIN;

      // Vertical: prefer below, flip above if not enough room
      let top, bottom;
      if (b.bottom + GAP + POPUP_H <= window.innerHeight - MARGIN) {
        top = b.bottom + GAP;
        popup.style.top    = top + 'px';
        popup.style.bottom = '';
      } else {
        // Flip above the trigger
        top = b.top - GAP - POPUP_H;
        if (top < MARGIN) top = MARGIN;
        popup.style.top    = top + 'px';
        popup.style.bottom = '';
      }
      popup.style.left = left + 'px';
    }

    _closePopup() {
      const popup = document.getElementById(`${this.uid}-popup`);
      const box   = document.getElementById(`${this.uid}-box`);
      if (popup) { popup.style.display = 'none'; box.classList.remove('open'); }
      if (this._scrollHandler) {
        window.removeEventListener('scroll', this._scrollHandler, true);
        this._scrollHandler = null;
      }
      if (this._resizeHandler) {
        window.removeEventListener('resize', this._resizeHandler);
        this._resizeHandler = null;
      }
    }

    /* ── Calendar rendering ── */
    _renderCal() {
      document.getElementById(`${this.uid}-monthtext`).textContent =
        `${MONTHS_LONG[this.month]} ${this.year}`;

      const grid = document.getElementById(`${this.uid}-grid`);
      grid.innerHTML = '';

      WEEKDAYS.forEach(d => {
        const el = document.createElement('div');
        el.className = 'dp-weekday'; el.textContent = d;
        grid.appendChild(el);
      });

      const firstDay    = new Date(this.year, this.month, 1).getDay();
      const daysInMonth = new Date(this.year, this.month + 1, 0).getDate();
      const today       = new Date();
      const todayStr    = this._toStr(today.getFullYear(), today.getMonth(), today.getDate());

      for (let i = 0; i < firstDay; i++) {
        const el = document.createElement('div');
        el.className = 'dp-day dp-empty'; grid.appendChild(el);
      }

      for (let d = 1; d <= daysInMonth; d++) {
        const dateStr = this._toStr(this.year, this.month, d);
        const el = document.createElement('div');
        el.className = 'dp-day'; el.textContent = d;
        if (dateStr === todayStr)    el.classList.add('dp-today');
        if (dateStr === this.selected) el.classList.add('dp-selected');
        el.addEventListener('click', () => this._select(dateStr));
        grid.appendChild(el);
      }
    }

    /* ── Selector (year/month) ── */
    _toggleSel() {
      const cal  = document.getElementById(`${this.uid}-cal`);
      const sel  = document.getElementById(`${this.uid}-sel`);
      const lbl  = document.getElementById(`${this.uid}-monthlabel`);
      if (sel.classList.contains('active')) {
        sel.classList.remove('active');
        cal.style.display = '';
        lbl.classList.remove('sel-open');
      } else {
        this._renderSel();
        sel.classList.add('active');
        cal.style.display = 'none';
        lbl.classList.add('sel-open');
      }
    }

    _showCalView() {
      const cal = document.getElementById(`${this.uid}-cal`);
      const sel = document.getElementById(`${this.uid}-sel`);
      const lbl = document.getElementById(`${this.uid}-monthlabel`);
      if (cal) cal.style.display = '';
      if (sel) sel.classList.remove('active');
      if (lbl) lbl.classList.remove('sel-open');
    }

    _renderSel() {
      const thisYear  = new Date().getFullYear();
      const yearGrid  = document.getElementById(`${this.uid}-yeargrid`);
      const monthGrid = document.getElementById(`${this.uid}-monthgrid`);
      yearGrid.innerHTML = '';
      monthGrid.innerHTML = '';

      for (let y = this.selYearBase; y < this.selYearBase + 9; y++) {
        const el = document.createElement('div');
        el.className = 'dp-year-cell'; el.textContent = y;
        if (y === thisYear)  el.classList.add('dp-this-year');
        if (y === this.year) el.classList.add('dp-sel-active');
        el.addEventListener('click', e => { e.stopPropagation(); this.year = y; this._renderSel(); });
        yearGrid.appendChild(el);
      }

      MONTHS_SHORT.forEach((m, i) => {
        const el = document.createElement('div');
        el.className = 'dp-month-cell'; el.textContent = m;
        if (i === this.month) el.classList.add('dp-sel-active');
        el.addEventListener('click', e => {
          e.stopPropagation();
          this.month = i;
          this._toggleSel();
          this._renderCal();
        });
        monthGrid.appendChild(el);
      });
    }

    /* ── Navigation ── */
    _prevMonth() {
      if (this.month === 0) { this.month = 11; this.year--; }
      else this.month--;
      this._renderCal();
    }
    _nextMonth() {
      if (this.month === 11) { this.month = 0; this.year++; }
      else this.month++;
      this._renderCal();
    }

    /* ── Select / Clear / Today ── */
    _select(dateStr) {
      this.selected = dateStr;
      this._updateDisplay();
      this.input.value = dateStr;
      this._closePopup();
      if (this.onChange) this.onChange(dateStr);
      // Also fire native 'change' event so existing listeners work
      this.input.dispatchEvent(new Event('change', { bubbles: true }));
    }

    _clear() {
      this.selected = '';
      this._updateDisplay();
      this.input.value = '';
      this._closePopup();
      if (this.onChange) this.onChange('');
      this.input.dispatchEvent(new Event('change', { bubbles: true }));
    }

    _goToday() {
      const now = new Date();
      this.year  = now.getFullYear();
      this.month = now.getMonth();
      this.selYearBase = this.year - 4;
      this._select(this._toStr(this.year, this.month, now.getDate()));
    }

    /* ── Public API ── */
    getValue() { return this.selected; }

    setValue(dateStr) {
      this.selected = dateStr || '';
      this.input.value = this.selected;
      if (dateStr) {
        const d = new Date(dateStr + 'T00:00:00');
        if (!isNaN(d)) {
          this.year  = d.getFullYear();
          this.month = d.getMonth();
          this.selYearBase = this.year - 4;
        }
      }
      this._updateDisplay();
    }

    /* ── Helpers ── */
    _updateDisplay() {
      const label = document.getElementById(`${this.uid}-label`);
      if (!label) return;
      if (this.selected) {
        label.textContent = this.selected;
        label.classList.remove('placeholder');
      } else {
        label.textContent = this.placeholder;
        label.classList.add('placeholder');
      }
    }

    _toStr(y, m, d) {
      return `${y}-${String(m + 1).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
    }

    _esc(str) {
      return String(str).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
    }

    /* ── Static helpers ── */

    /**
     * Replace a native input with the custom datepicker.
     * @param {string|HTMLElement} inputIdOrEl
     * @param {object} options  { placeholder, onChange }
     */
    static replace(inputIdOrEl, options = {}) {
      const el = typeof inputIdOrEl === 'string'
        ? document.getElementById(inputIdOrEl)
        : inputIdOrEl;
      if (!el) { console.warn('DYBDatepicker.replace: element not found', inputIdOrEl); return null; }
      return new DYBDatepicker(el, options);
    }

    /** Set value by original input ID. */
    static setValue(inputId, dateStr) {
      const inst = _registry[inputId];
      if (inst) inst.setValue(dateStr);
      else {
        // Fallback: set hidden input directly
        const el = document.getElementById(inputId);
        if (el) el.value = dateStr || '';
      }
    }

    /** Get value by original input ID. */
    static getValue(inputId) {
      const inst = _registry[inputId];
      return inst ? inst.getValue() : (document.getElementById(inputId)?.value || '');
    }
  }

  global.DYBDatepicker = DYBDatepicker;

})(window);
