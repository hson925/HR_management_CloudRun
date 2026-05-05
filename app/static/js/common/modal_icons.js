/**
 * 공통 모달 헬퍼 — showModal / closeModal.
 *
 * 사용 (권장 → 호환):
 *   showModal('warning',      'Title', 'Text')                         // 시맨틱 토큰 (권장)
 *   showModal('locked',       'Title', 'Text')
 *   showModal('error',        'Title', 'Text')
 *   showModal('success',      'Title', 'Text')
 *   showModal('info',         'Title', 'Text')
 *   showModal('notification', 'Title', 'Text')
 *   showModal('bi-exclamation-triangle-fill text-amber-500', ...)     // Bootstrap Icon 클래스 (직접)
 *   showModal('🔒', 'Title', 'Text')                                   // 이모지 (legacy — 지원 유지)
 *
 * 렌더 우선순위:
 *   1) MODAL_ICONS 토큰 매칭 → 등록된 class string 으로 <i> 렌더
 *   2) 'bi-' 또는 'bi ' 로 시작 → <i class="..."> 로 렌더
 *   3) 그 외 → textContent (이모지·평문)
 *
 * 팔레트 변경 시 MODAL_ICONS 레지스트리만 수정 → 호출부 일괄 반영.
 *
 * ⚠ SECURITY: `icon` 인자는 **개발자가 작성한 리터럴** 또는 MODAL_ICONS 토큰만
 * 허용. 'bi-' 경로는 caller 문자열을 그대로 element.className 에 주입하므로
 * 사용자 입력을 여기 넘기면 class-injection 공격면이 됨. 사용자-유래 값은
 * 절대 icon 인자로 전달하지 말 것.
 * title / text 인자는 textContent 로 안전하게 렌더됨 — 사용자 입력 허용.
 *
 * 전제: 페이지에 #modalIcon, #modalTitle, #modalText, #modalOverlay 요소 존재.
 */
(function (global) {
  // 시맨틱 토큰 레지스트리 — 앱 전역 모달 아이콘 팔레트의 단일 진실.
  // warning:      사용자 입력/충돌/rate limit — 행동 필요
  // locked:       시간 게이트·접근 제한 — 에러 아닌 중립 상태
  // error:        시스템 실패 — 네트워크/제출 실패
  // success:      작업 성공 — 저장/제출 완료
  // info:         안내 — 행동 필요 없음, 정보성
  // notification: 알림 발송 결과 (bell) — info 와 동일 톤, 다른 의미
  // text-sky-* 는 Tailwind purged CSS 에 미포함 → text-blue-500 사용.
  const MODAL_ICONS = {
    warning:      'bi bi-exclamation-triangle-fill text-amber-500',
    locked:       'bi bi-lock-fill text-slate-500',
    error:        'bi bi-x-circle-fill text-red-500',
    success:      'bi bi-check-circle-fill text-emerald-500',
    info:         'bi bi-info-circle-fill text-blue-500',
    notification: 'bi bi-bell-fill text-blue-500',
  };

  function _renderIcon(iconEl, icon) {
    const raw = String(icon == null ? '' : icon).trim();
    if (Object.prototype.hasOwnProperty.call(MODAL_ICONS, raw)) {
      iconEl.textContent = '';
      const i = document.createElement('i');
      i.className = MODAL_ICONS[raw];
      iconEl.appendChild(i);
      return;
    }
    if (/^bi[-\s]/.test(raw)) {
      iconEl.textContent = '';
      const i = document.createElement('i');
      i.className = /^bi\s/.test(raw) ? raw : ('bi ' + raw);
      iconEl.appendChild(i);
      return;
    }
    iconEl.textContent = raw;
  }

  // 외부에서 토큰 추가·수정 가능 (예: showModal.icons.info = 'bi bi-info-circle-fill text-blue-500')
  global.MODAL_ICONS = MODAL_ICONS;

  function showModal(icon, title, text) {
    const iconEl = document.getElementById('modalIcon');
    if (iconEl) _renderIcon(iconEl, icon);
    const titleEl = document.getElementById('modalTitle');
    const textEl  = document.getElementById('modalText');
    if (titleEl) titleEl.textContent = title;
    if (textEl)  textEl.textContent  = text;
    const overlay = document.getElementById('modalOverlay');
    if (overlay) overlay.style.display = 'flex';
  }

  function closeModal(id) {
    // 인자 있으면 해당 id 모달 닫기 (여러 페이지가 `closeModal('quickAssignModal')`
    // 같이 id 를 넘기는 패턴 사용). 없으면 기본 #modalOverlay (showModal 의 짝).
    // defer 로드 때문에 페이지별 inline 정의를 덮어쓰는 상황을 공용으로 흡수.
    const targetId = (typeof id === 'string' && id) ? id : 'modalOverlay';
    const el = document.getElementById(targetId);
    if (el) el.style.display = 'none';
  }

  global.showModal = showModal;
  global.closeModal = closeModal;

  // ── L-8: 2-옵션 confirm 모달 헬퍼 ─────────────────────────────────────
  // showConfirmModal({icon, title, text, confirmLabel, cancelLabel, onConfirm, onCancel})
  // .modal-backdrop 패턴 (layout.html 글로벌 클래스) 재사용 — backdrop-filter / --sb-w 자동.
  // 호출 시점에 DOM 동적 생성, 닫힐 때 제거 (singleton 충돌 회피).
  function showConfirmModal(opts) {
    opts = opts || {};
    const icon = opts.icon || 'warning';
    const title = String(opts.title || '');
    const text = String(opts.text || '');
    const confirmLabel = String(opts.confirmLabel || 'OK');
    const cancelLabel = String(opts.cancelLabel || 'Cancel');
    const onConfirm = typeof opts.onConfirm === 'function' ? opts.onConfirm : null;
    const onCancel = typeof opts.onCancel === 'function' ? opts.onCancel : null;

    // 기존 confirm 모달이 떠 있으면 제거
    const existing = document.getElementById('_dybConfirmModal');
    if (existing) existing.remove();

    const overlay = document.createElement('div');
    overlay.id = '_dybConfirmModal';
    overlay.className = 'modal-backdrop';
    overlay.style.cssText = 'display:flex;z-index:10003;';

    const box = document.createElement('div');
    box.className = 'modal-box';
    box.style.cssText = 'background:var(--surface);border-radius:8px;padding:24px;max-width:420px;width:90%;box-shadow:0 8px 32px rgba(0,0,0,.18);';

    const iconWrap = document.createElement('div');
    iconWrap.style.cssText = 'text-align:center;font-size:2.4rem;margin-bottom:12px;';
    _renderIcon(iconWrap, icon);
    box.appendChild(iconWrap);

    const titleEl = document.createElement('h3');
    titleEl.style.cssText = 'font-size:1.05rem;font-weight:800;text-align:center;margin:0 0 8px;color:var(--text-strong);white-space:pre-line;';
    titleEl.textContent = title;
    box.appendChild(titleEl);

    const textEl = document.createElement('p');
    textEl.style.cssText = 'font-size:.875rem;text-align:center;margin:0 0 20px;color:var(--text);white-space:pre-line;line-height:1.5;';
    textEl.textContent = text;
    box.appendChild(textEl);

    const btnRow = document.createElement('div');
    btnRow.style.cssText = 'display:flex;gap:8px;';

    const cancelBtn = document.createElement('button');
    cancelBtn.type = 'button';
    cancelBtn.className = 'btn-secondary';
    cancelBtn.style.cssText = 'flex:1;padding:10px;border-radius:6px;font-weight:700;font-size:.875rem;';
    cancelBtn.textContent = cancelLabel;
    cancelBtn.onclick = () => {
      overlay.remove();
      if (onCancel) onCancel();
    };
    btnRow.appendChild(cancelBtn);

    const confirmBtn = document.createElement('button');
    confirmBtn.type = 'button';
    confirmBtn.className = 'btn-primary';
    confirmBtn.style.cssText = 'flex:1;padding:10px;border-radius:6px;font-weight:700;font-size:.875rem;';
    confirmBtn.textContent = confirmLabel;
    confirmBtn.onclick = () => {
      overlay.remove();
      if (onConfirm) onConfirm();
    };
    btnRow.appendChild(confirmBtn);

    box.appendChild(btnRow);
    overlay.appendChild(box);
    document.body.appendChild(overlay);
  }

  global.showConfirmModal = showConfirmModal;
})(window);
