/**
 * DYB NHR Portal — Theme toggle helper.
 *
 * Exposes `window.dybToggleTheme()` to flip between light and dark themes.
 * Persists the choice in localStorage under key `dyb-theme`.
 *
 * FOUC prevention: the initial theme is applied by the inline <script> in
 * layout.html <head> BEFORE this file loads (defer). This module only
 * handles user-initiated toggle and icon swap.
 *
 * Icon swap convention: any element with id `themeToggleIcon` (typically
 * an <i class="bi ..."> inside the header toggle button) will have its
 * class swapped between `bi-sun` (shown in dark mode) and `bi-moon-stars`
 * (shown in light mode).
 *
 * Accessibility: the toggle button's aria-label is updated to reflect
 * the next action (e.g. "Switch to dark mode" vs "Switch to light mode").
 */
(function (global) {
  'use strict';

  var STORAGE_KEY = 'dyb-theme';
  var BUTTON_ID   = 'themeToggleBtn';
  var ICON_ID     = 'themeToggleIcon';

  function _currentTheme() {
    return document.documentElement.getAttribute('data-theme') === 'dark' ? 'dark' : 'light';
  }

  function _applyIcon(theme) {
    var icon = document.getElementById(ICON_ID);
    if (icon) {
      // Dark 상태에서는 sun 아이콘(= 라이트로 전환), Light 상태에서는 moon-stars.
      icon.classList.remove('bi-sun', 'bi-moon-stars');
      icon.classList.add(theme === 'dark' ? 'bi-sun' : 'bi-moon-stars');
    }
    var btn = document.getElementById(BUTTON_ID);
    if (btn) {
      btn.setAttribute('aria-label',
        theme === 'dark' ? 'Switch to light mode · 라이트 모드로 전환' : 'Switch to dark mode · 다크 모드로 전환');
    }
  }

  function setTheme(theme) {
    if (theme !== 'dark' && theme !== 'light') return;
    document.documentElement.setAttribute('data-theme', theme);
    try { localStorage.setItem(STORAGE_KEY, theme); } catch (_) {}
    _applyIcon(theme);
  }

  function toggleTheme() {
    setTheme(_currentTheme() === 'dark' ? 'light' : 'dark');
  }

  // 페이지 로드 시 현재 테마에 맞는 아이콘으로 초기화.
  document.addEventListener('DOMContentLoaded', function () {
    _applyIcon(_currentTheme());
  });

  global.dybToggleTheme = toggleTheme;
  global.dybSetTheme    = setTheme;

  // ── Language toggle (placeholder) ────────────────────────────────────────
  // 실제 i18n 미연동 상태 — 사용자 혼란 방지 차 클릭 시 "Coming Soon" 안내.
  // 향후 Flask-Babel 혹은 서버 세션 연동 시 이 함수를 실제 토글 로직으로 교체.
  function toggleLang(_btn) {
    if (typeof showModal === 'function') {
      showModal('info', 'Coming Soon',
        'KO/EN switch is under preparation.\n한/영 전환은 준비 중입니다.');
    }
  }
  // DOMContentLoaded 시 검색 kbd 라벨을 플랫폼 기반으로 조정 (Mac → ⌘K, 기타 → Ctrl K).
  document.addEventListener('DOMContentLoaded', function () {
    var kbd = document.getElementById('hdrSearchKbd');
    if (kbd) {
      var isMac = /Mac|iPhone|iPad|iPod/i.test(navigator.platform || navigator.userAgent || '');
      kbd.textContent = isMac ? '⌘ K' : 'Ctrl K';
    }
  });
  global.dybToggleLang = toggleLang;
})(window);
