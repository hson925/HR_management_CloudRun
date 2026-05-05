/* eval_v2 평가 폼 공통 렌더링 — form.html (admin) + public_form.html (public) 양쪽에서 사용
 *
 * 호스트 페이지가 제공해야 할 globals (let/var 어느 쪽이든 OK — 동일 script realm 의 lexical scope 참조):
 *   - selectedRole : { items, open_questions, label, role }
 *   - scores       : { [qId]: number }
 *   - openAnswers  : { [oqId]: string }
 *   - updateProgress() : 진행도 업데이트
 *   - escapeHtml(s)    : HTML 텍스트 노드 escape
 *
 * 공개 함수 (window 에 attach — 인라인 onclick 호환):
 *   - renderQuestions()
 *   - selectScore(el, qId, val)
 *   - reopenCard(qi)
 *   - collapseCard(qi)              : qId 없이 동작 (card.dataset.qid 사용 — I3)
 *
 * 보안 (C1): 인라인 onclick 미사용 → addEventListener 만 사용. item.id 가 onclick 속성에 보간되지 않음 → XSS 차단.
 * 동작 정확성 (C3): selectScore 의 setTimeout 누적 방지 — clearTimeout 으로 이전 타이머 취소.
 */
(function() {
  const _AUTO_COLLAPSE_MS = 800;
  const _EXPAND_TRANSITION_MS = 300;  // .qc-body grid-template-rows 트랜지션(.28s) 보다 약간 길게

  // 카드 확장 후 overflow:visible 로 전환 (hover scale·box-shadow·tooltip 잘림 방지)
  function _allowOverflow(card) {
    if (card.dataset.overflowTimerId) clearTimeout(parseInt(card.dataset.overflowTimerId, 10));
    const tid = setTimeout(() => {
      card.classList.add('overflow-allowed');
      card.removeAttribute('data-overflow-timer-id');
    }, _EXPAND_TRANSITION_MS);
    card.dataset.overflowTimerId = String(tid);
  }
  function _restrictOverflow(card) {
    if (card.dataset.overflowTimerId) {
      clearTimeout(parseInt(card.dataset.overflowTimerId, 10));
      card.removeAttribute('data-overflow-timer-id');
    }
    card.classList.remove('overflow-allowed');
  }

  function _hasAnyDesc(scoreVals, descs) {
    return scoreVals.some(v => {
      const d = descs[String(v)];
      return d && ((d.en && String(d.en).trim()) || (d.ko && String(d.ko).trim()));
    });
  }

  // 현재 언어 (body data-eval-lang) — host page 가 init 시 설정
  function _currentLang() {
    return document.body.getAttribute('data-eval-lang') === 'en' ? 'en' : 'ko';
  }
  // 평균 점수 설명 산출 — 선택 언어 우선, 폴백 다른 언어
  function _pickDescText(d) {
    if (!d) return '';
    return _currentLang() === 'en' ? (d.en || d.ko || '') : (d.ko || d.en || '');
  }
  // textarea placeholder 다국어
  function _phRequired() { return _currentLang() === 'en' ? 'Required · Enter your response...' : '필수 · 답변을 입력하세요...'; }
  function _phOptional() { return _currentLang() === 'en' ? 'Enter your response...' : '답변을 입력하세요...'; }

  function _buildVerticalRow(qi, qId, v, en, ko) {
    const row = document.createElement('div');
    row.className = 'score-row-v';
    row.dataset.qi = String(qi);
    row.dataset.val = String(v);
    let textHtml = '';
    if (en || ko) {
      // 점수 행은 KO/EN 양쪽 동시 노출 (토글과 무관) — main 문항만 토글로 전환.
      const enHtml = en ? `<span class="srv-en">${escapeHtml(en)}</span>` : '';
      const koHtml = ko ? `<span class="srv-ko">${escapeHtml(ko)}</span>` : '';
      textHtml = `<span class="srv-text">${enHtml}${koHtml}</span>`;
    }
    row.innerHTML = `<span class="srv-pill">${v}</span>${textHtml}`;
    row.addEventListener('click', () => selectScore(row, qId, v));
    return row;
  }

  function _buildHorizontalBtn(qi, qId, v) {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'score-btn';
    btn.dataset.qi = String(qi);
    btn.dataset.val = String(v);
    btn.textContent = String(v);
    btn.addEventListener('click', () => selectScore(btn, qId, v));
    return btn;
  }

  function _buildScoreContainer(qi, item) {
    const max = Math.max(2, Math.min(10, parseInt(item.max_score, 10) || 5));
    const descs = item.descriptions || {};
    const scoreVals = Array.from({ length: max }, (_, i) => i + 1);
    if (_hasAnyDesc(scoreVals, descs)) {
      const wrap = document.createElement('div');
      wrap.className = 'score-rows-vert';
      scoreVals.forEach(v => {
        const d = descs[String(v)] || {};
        const en = d.en ? String(d.en).trim() : '';
        const ko = d.ko ? String(d.ko).trim() : '';
        wrap.appendChild(_buildVerticalRow(qi, item.id, v, en, ko));
      });
      return wrap;
    }
    const wrap = document.createElement('div');
    wrap.className = 'flex flex-wrap gap-2 justify-center mt-3';
    wrap.id = `scores-q${qi}`;
    scoreVals.forEach(v => wrap.appendChild(_buildHorizontalBtn(qi, item.id, v)));
    return wrap;
  }

  function _buildQuestionCard(qi, item) {
    const card = document.createElement('div');
    card.className = 'question-card overflow-allowed';  // 초기 렌더는 펼친 상태 → overflow:visible 즉시
    card.id = `qcard-${qi}`;
    card.dataset.qid = item.id;

    const summary = document.createElement('div');
    summary.className = 'qc-summary';
    summary.innerHTML = `<span class="qc-q">Q${qi + 1} <span class="qc-check">✓</span></span><span class="qc-pill" id="qc-pill-${qi}"></span><span class="qc-desc" id="qc-desc-${qi}"></span>`;
    summary.addEventListener('click', () => reopenCard(qi));
    card.appendChild(summary);

    const body = document.createElement('div');
    body.className = 'qc-body';
    const inner = document.createElement('div');
    inner.className = 'qc-inner';

    const header = document.createElement('div');
    header.className = 'mb-3 qc-q-header';
    header.title = 'Click to collapse';
    // 두 <p> 모두 메인 스타일. data-lang 으로 CSS 토글. 폴백: 빈 값이면 다른 언어 사용.
    const enText = escapeHtml(item.en || item.ko || '');
    const koText = escapeHtml(item.ko || item.en || '');
    const qBadge = `<span class="text-xs font-extrabold px-1.5 py-0.5 rounded mr-1.5" style="background:var(--strong-bg);color:#fff;">Q${qi + 1}</span>`;
    header.innerHTML = `<p data-lang="en" class="text-sm font-bold leading-relaxed mb-0.5" style="color:var(--on-surface)">${qBadge}${enText}<span class="qc-collapse-hint">▲ collapse</span></p><p data-lang="ko" class="text-sm font-bold leading-relaxed mb-0.5" style="color:var(--on-surface)">${qBadge}${koText}<span class="qc-collapse-hint">▲ collapse</span></p>`;
    header.addEventListener('click', () => collapseCard(qi));
    inner.appendChild(header);
    inner.appendChild(_buildScoreContainer(qi, item));

    body.appendChild(inner);
    card.appendChild(body);
    return card;
  }

  function _buildOpenQuestion(oqi, oq) {
    const div = document.createElement('div');
    div.dataset.oqid = oq.id;
    if (oq.required) div.dataset.oqRequired = '1';
    const ta = document.createElement('textarea');
    ta.rows = 3;
    ta.placeholder = oq.required ? _phRequired() : _phOptional();
    ta.className = 'w-full px-4 py-3 border-2 rounded-xl text-sm resize-none focus:outline-none transition-all oq-textarea';
    ta.style.cssText = 'border-color:var(--outline-variant);background:var(--surface-lowest)';
    if (oq.required) ta.dataset.oqRequired = '1';
    ta.addEventListener('input', () => { openAnswers[oq.id] = ta.value; updateProgress(); });
    const reqBadge = oq.required ? '<span class="ml-1 text-[10px] font-extrabold text-red-600">*Required</span>' : '';
    const enText = escapeHtml(oq.text_en || oq.text_ko || '');
    const koText = escapeHtml(oq.text_ko || oq.text_en || '');
    const oqBadge = `<span class="oq-badge">OQ${oqi + 1}</span>`;
    div.innerHTML = `<p data-lang="en" class="text-sm font-bold leading-relaxed mb-1" style="color:var(--on-surface)">${oqBadge}${enText}${reqBadge}</p><p data-lang="ko" class="text-sm font-bold leading-relaxed mb-1" style="color:var(--on-surface)">${oqBadge}${koText}${reqBadge}</p>`;
    div.appendChild(ta);
    return div;
  }

  // 호스트 페이지가 사용할 helper — 진행도/제출 검증에 활용
  // role 의 required open_question 목록 + 채워진 개수 반환
  function getRequiredOpenQuestions() {
    const role = selectedRole;
    return (role?.open_questions || []).filter(oq => oq && oq.required && oq.id);
  }
  function getRequiredOpenAnsweredCount() {
    return getRequiredOpenQuestions().filter(oq => String(openAnswers[oq.id] || '').trim()).length;
  }
  window.getRequiredOpenQuestions = getRequiredOpenQuestions;
  window.getRequiredOpenAnsweredCount = getRequiredOpenAnsweredCount;

  // ── 공개 함수 ─────────────────────────────────────────────────────────────
  function renderQuestions() {
    const role = selectedRole;
    const items = role?.items || [];
    const listEl = document.getElementById('questionList');
    listEl.innerHTML = '';
    document.getElementById('evalRoleTitle').textContent = role?.label || role?.role || '';

    const bar = document.getElementById('progressBar');
    if (bar && bar.parentElement !== document.body) document.body.appendChild(bar);
    if (bar) bar.classList.add('visible');
    updateProgress();

    items.forEach((item, qi) => listEl.appendChild(_buildQuestionCard(qi, item)));

    const openItems = role?.open_questions || [];
    const oqSection = document.getElementById('openQuestionSection');
    const fallback = document.getElementById('commentFallback');
    oqSection.innerHTML = '';
    if (openItems.length) {
      oqSection.style.display = '';
      if (fallback) fallback.style.display = 'none';
      openItems.forEach((oq, oqi) => oqSection.appendChild(_buildOpenQuestion(oqi, oq)));
    } else {
      oqSection.style.display = 'none';
      if (fallback) fallback.style.display = 'none';
    }
  }

  function selectScore(el, qId, val) {
    const card = el.closest('.question-card');
    if (!card) return;
    const qi = parseInt(card.id.replace('qcard-', ''), 10);
    if (Number.isNaN(qi)) return;

    card.querySelectorAll('.score-btn.selected, .score-row-v.selected')
      .forEach(b => b.classList.remove('selected'));
    el.classList.add('selected');
    scores[qId] = parseInt(val, 10);
    updateProgress();

    const pillEl = document.getElementById(`qc-pill-${qi}`);
    const descEl = document.getElementById(`qc-desc-${qi}`);
    if (pillEl) pillEl.textContent = String(val);
    if (descEl) {
      const item = (selectedRole?.items || [])[qi];
      const d = (item && item.descriptions) ? item.descriptions[String(val)] : null;
      descEl.textContent = _pickDescText(d);
    }

    if (card.dataset.collapseDisabled) return;
    if (card.dataset.collapseTimerId) clearTimeout(parseInt(card.dataset.collapseTimerId, 10));
    const tid = setTimeout(() => {
      card.removeAttribute('data-collapse-timer-id');
      if (card.dataset.collapseDisabled) return;
      _restrictOverflow(card);  // 접힘 직전 overflow:hidden 복구 → 트랜지션 클리핑
      card.classList.add('collapsed');
    }, _AUTO_COLLAPSE_MS);
    card.dataset.collapseTimerId = String(tid);
  }

  function reopenCard(qi) {
    const card = document.getElementById(`qcard-${qi}`);
    if (!card) return;
    if (card.dataset.collapseTimerId) {
      clearTimeout(parseInt(card.dataset.collapseTimerId, 10));
      card.removeAttribute('data-collapse-timer-id');
    }
    card.classList.remove('collapsed');
    card.dataset.collapseDisabled = '1';
    _allowOverflow(card);  // 펼침 트랜지션 끝나면 overflow:visible
  }

  function collapseCard(qi) {
    const card = document.getElementById(`qcard-${qi}`);
    if (!card) return;
    const qId = card.dataset.qid;
    if (typeof scores[qId] !== 'number' || scores[qId] < 1) return;
    if (card.dataset.collapseTimerId) {
      clearTimeout(parseInt(card.dataset.collapseTimerId, 10));
      card.removeAttribute('data-collapse-timer-id');
    }
    _restrictOverflow(card);
    card.classList.add('collapsed');
    delete card.dataset.collapseDisabled;
  }

  // 언어 토글 시 호출 — 답한 카드의 접힘 요약 (qc-desc) 재렌더 + textarea placeholder 갱신
  function refreshCollapsedSummaries() {
    document.querySelectorAll('.question-card').forEach(card => {
      const qi = parseInt(card.id.replace('qcard-', ''), 10);
      if (Number.isNaN(qi)) return;
      const qid = card.dataset.qid;
      const val = scores[qid];
      if (typeof val !== 'number' || val < 1) return;
      const descEl = document.getElementById(`qc-desc-${qi}`);
      if (!descEl) return;
      const item = (selectedRole?.items || [])[qi];
      const d = (item && item.descriptions) ? item.descriptions[String(val)] : null;
      descEl.textContent = _pickDescText(d);
    });
  }
  function refreshPlaceholders() {
    document.querySelectorAll('.oq-textarea').forEach(ta => {
      ta.placeholder = ta.dataset.oqRequired ? _phRequired() : _phOptional();
    });
  }

  // KST (Asia/Seoul) 오늘 날짜를 YYYY-MM-DD 로 반환 (sv-SE 로케일 = ISO 포맷)
  function kstTodayISO() {
    return new Date().toLocaleDateString('sv-SE', { timeZone: 'Asia/Seoul' });
  }

  // 인라인 onclick 호환을 위해 window 노출 (있을지 모를 외부 호출도 대비)
  window.renderQuestions = renderQuestions;
  window.selectScore     = selectScore;
  window.reopenCard      = reopenCard;
  window.collapseCard    = collapseCard;
  window.refreshCollapsedSummaries = refreshCollapsedSummaries;
  window.refreshPlaceholders       = refreshPlaceholders;
  window.kstTodayISO               = kstTodayISO;
})();
