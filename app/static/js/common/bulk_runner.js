/**
 * app/static/js/common/bulk_runner.js
 * ProgressModal 과 결합하여 items 배열을 chunk 단위로 POST 하는 유틸.
 *
 * 사용 예:
 *   const result = await BulkRunner.run({
 *     items: ['A1', 'A2', ...],
 *     chunkSize: 10,
 *     title: 'Creating folders',
 *     subtitle: 'N명의 교사 평가 폴더 생성 중...',
 *     url: '/api/v2/annual-eval/create-folders',
 *     bodyKey: 'emp_ids',          // chunk 를 body[bodyKey] 로 전달
 *     extraBody: { sessionId },    // 매 요청에 공통으로 포함
 *     tallyFn: (res, chunk) => ({ success, skip, error, currentLabel }),
 *   });
 *
 * tallyFn: 해당 chunk 응답(또는 네트워크 에러 시 null) 을 받아 이번 chunk 의
 *   { success, skip, error, currentLabel } 증분을 리턴. null 리턴 시 전체 chunk 를 error 로 계산.
 *
 * 반환: { cancelled, success, skip, error, total, processed, responses }
 */
(function (global) {
  'use strict';

  async function _post(url, body, signal) {
    const res = await fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Requested-With': 'XMLHttpRequest',
      },
      body: JSON.stringify(body),
      signal,
    });
    const data = await res.json().catch(() => ({}));
    return { ok: res.ok, data, httpStatus: res.status };
  }

  async function run(opts) {
    const items = Array.isArray(opts.items) ? opts.items.slice() : [];
    const chunkSize = Math.max(1, Number(opts.chunkSize) || 5);
    const chunks = [];
    for (let i = 0; i < items.length; i += chunkSize) {
      chunks.push(items.slice(i, i + chunkSize));
    }

    const pm = global.ProgressModal.open({
      title: opts.title || 'Processing',
      subtitle: opts.subtitle || `${items.length} item${items.length > 1 ? 's' : ''}`,
      total: items.length,
    });

    let success = 0, skip = 0, error = 0, processed = 0;
    const responses = [];
    let lastError = '';

    for (let i = 0; i < chunks.length; i++) {
      if (pm.cancelled) break;
      const chunk = chunks[i];
      const body = { ...(opts.extraBody || {}), [opts.bodyKey]: chunk };

      let res = null, netErr = false, lastErrMsg = '';
      try {
        const r = await _post(opts.url, body, pm.signal);
        res = r.data;
        if (!r.ok || (res && res.status && res.status !== 'SUCCESS' && res.status !== 'OK')) {
          // HTTP 에러 또는 서버 반환 status 가 SUCCESS/OK 아님 — 서버 메시지 추출
          if (window.ApiError) {
            lastErrMsg = ApiError.parseApiErrorFromResponse(
              { status: r.httpStatus, ok: r.ok }, res, opts.title || 'Bulk operation'
            );
          } else if (res && res.message) {
            lastErrMsg = res.message;
          }
          if (!res || !res.status) netErr = true;
        }
      } catch (e) {
        if (e && e.name === 'AbortError') break;   // 사용자 취소 — error 로 안 침
        netErr = true;
        lastErrMsg = window.ApiError
          ? ApiError.parseApiErrorFromException(e, opts.title || 'Bulk operation')
          : String(e && e.message || e);
        console.error('bulk chunk failed', e);
      }
      responses.push(res);
      if (lastErrMsg) { lastError = lastErrMsg; }

      if (netErr) {
        error += chunk.length;
      } else {
        try {
          const delta = opts.tallyFn ? opts.tallyFn(res, chunk) : null;
          if (delta) {
            if (typeof delta.success === 'number') success += delta.success;
            if (typeof delta.skip    === 'number') skip    += delta.skip;
            if (typeof delta.error   === 'number') error   += delta.error;
          } else {
            // tallyFn 없으면 chunk 전체를 success 처리
            success += chunk.length;
          }
        } catch (e) {
          console.error('tallyFn threw', e);
          error += chunk.length;
        }
      }

      processed += chunk.length;
      const lastItem = chunk[chunk.length - 1];
      const label = typeof lastItem === 'string' ? lastItem : '';
      pm.update(processed, { success, skip, error, currentLabel: label });
    }

    const summary = _buildSummary({ success, skip, error, cancelled: pm.cancelled, total: items.length, lastError });
    pm.done({
      success: error === 0 && !pm.cancelled,
      summary,
      autoCloseMs: opts.autoCloseMs ?? 1500,
    });

    return {
      cancelled: pm.cancelled,
      success, skip, error, processed,
      total: items.length,
      responses,
      lastError,
    };
  }

  function _buildSummary({ success, skip, error, cancelled, total, lastError }) {
    const parts = [];
    if (success > 0) parts.push(`${success} 성공`);
    if (skip    > 0) parts.push(`${skip} 건너뜀`);
    if (error   > 0) parts.push(`${error} 실패`);
    const body = parts.length ? parts.join(' · ') : `${total} items processed`;
    const base = cancelled ? `Cancelled — ${body}` : body;
    if (error > 0 && lastError) {
      // 마지막 에러 메시지를 요약 아래에 부가 (진단 힌트)
      return `${base}\n${lastError}`;
    }
    return base;
  }

  global.BulkRunner = { run };
})(window);
