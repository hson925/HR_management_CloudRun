/**
 * app/static/js/common/api_error.js
 * 공용 API 호출 + 에러 파서.
 *
 * 사용 1 — fetch wrapper:
 *   const { ok, data, errorMessage } = await apiFetch('/api/...', {
 *     method: 'POST', body: {...}, operation: 'Refresh NT Cache'
 *   });
 *   if (!ok) showToast(errorMessage, 'error');
 *
 * 사용 2 — 기존 fetch catch 블록에서:
 *   try {
 *     const res = await fetch('/api/...', { method: 'POST' });
 *     const data = await res.json();
 *     if (data.status !== 'SUCCESS') {
 *       showToast(parseApiErrorFromResponse(res, data, 'Operation name'), 'error');
 *     }
 *   } catch (e) {
 *     showToast(parseApiErrorFromException(e, 'Operation name'), 'error');
 *   }
 *
 * 메시지는 모두 영어 (사용자 정책).
 */
(function (global) {
  'use strict';

  function _truncate(s, n) {
    s = String(s || '');
    return s.length > n ? s.slice(0, n) + '...' : s;
  }

  /**
   * Response + parsed body → user-friendly English message.
   * res: Response object. body: parsed JSON or null. operation: short verb phrase.
   */
  function parseApiErrorFromResponse(res, body, operation) {
    const op = operation ? ` during '${operation}'` : '';
    const status = res ? res.status : 0;
    const serverMsg = body && typeof body === 'object' ? (body.message || body.error || '') : '';

    // HTTP status 우선 분기
    if (status === 401 || status === 403) {
      const code = body && body.code;
      if (code === 'FORCE_LOGOUT') {
        return 'Session invalidated by admin (role change or password reset). Please sign in again.';
      }
      return 'Session expired. Please reload the page and sign in again.';
    }
    if (status === 404) {
      return `Endpoint not found${op}. This may indicate a deploy mismatch — please report to admin.`;
    }
    if (status === 409) {
      return serverMsg
        ? `Conflict${op}: ${serverMsg}`
        : `Conflict${op}. The data was modified by another user — please reload and retry.`;
    }
    if (status === 413) {
      return `Payload too large${op}. Try processing fewer items at once.`;
    }
    if (status === 429) {
      if (body && body.remaining_minutes) {
        return `Rate limited${op}. Try again in ${body.remaining_minutes} minute(s).`;
      }
      return `Rate limited${op}. Too many requests — wait a moment and retry.`;
    }
    if (status >= 500 && status < 600) {
      if (status === 502 || status === 503 || status === 504) {
        return `Service temporarily unavailable (HTTP ${status})${op}. The server may be restarting or overloaded — retry in 30 seconds.`;
      }
      if (serverMsg) {
        return `Server error (${status})${op}: ${_truncate(serverMsg, 200)}`;
      }
      return `Server error (${status})${op}. The server encountered an unexpected condition — please retry, and if it persists, check Cloud Run logs.`;
    }

    // SUCCESS 가 아닌 body.status (예: 'ERROR', 'COOLDOWN')
    if (body && body.status && body.status !== 'SUCCESS' && body.status !== 'OK') {
      if (body.status === 'COOLDOWN') {
        return serverMsg || `Operation on cooldown${op}.`;
      }
      return serverMsg
        ? `Request failed${op}: ${_truncate(serverMsg, 200)}`
        : `Request failed${op} (status: ${body.status}).`;
    }

    // 일반 (HTTP 4xx, body message 있음)
    if (serverMsg) {
      return `Request failed${op} (HTTP ${status}): ${_truncate(serverMsg, 200)}`;
    }
    return `Request failed${op} (HTTP ${status}).`;
  }

  /**
   * Exception (네트워크 · JSON parse · timeout) → user-friendly English message.
   */
  function parseApiErrorFromException(err, operation) {
    const op = operation ? ` during '${operation}'` : '';
    const msg = String((err && err.message) || err || '');

    if (/Failed to fetch|NetworkError|network error/i.test(msg)) {
      return `Network error${op}. Check your connection and retry.`;
    }
    if (/timeout|timed out/i.test(msg)) {
      return `Request timed out${op}. The server may be slow — retry or check Cloud Run logs.`;
    }
    if (/JSON|Unexpected token|Unexpected end/i.test(msg)) {
      return `Invalid server response${op}. The server returned non-JSON (possibly an HTML error page or container restart). Reload and retry.`;
    }
    if (/AbortError/i.test(msg)) {
      return `Request aborted${op}.`;
    }
    return `Unexpected error${op}: ${_truncate(msg, 200)}`;
  }

  /**
   * fetch wrapper — JSON body 자동 직렬화 + Content-Type 검증 + 파싱 + 에러 메시지 생성.
   * 반환: { ok, data, errorMessage, httpStatus }
   *   ok=true: 서버가 SUCCESS/OK 반환. data 는 파싱된 body.
   *   ok=false: errorMessage 에 사용자에게 보여줄 영어 메시지.
   */
  async function apiFetch(url, opts) {
    opts = opts || {};
    const method = opts.method || 'POST';
    const operation = opts.operation || '';
    const extraHeaders = opts.headers || {};

    const init = {
      method,
      headers: Object.assign({
        'Content-Type': 'application/json',
        'X-Requested-With': 'XMLHttpRequest',
      }, extraHeaders),
    };
    if (opts.body !== undefined) {
      init.body = typeof opts.body === 'string' ? opts.body : JSON.stringify(opts.body);
    }

    let res;
    try {
      res = await fetch(url, init);
    } catch (e) {
      return {
        ok: false,
        data: null,
        httpStatus: 0,
        errorMessage: parseApiErrorFromException(e, operation),
      };
    }

    const ctype = res.headers.get('content-type') || '';
    if (!ctype.includes('application/json')) {
      // HTML / 빈 응답 / 502 gateway page 등
      return {
        ok: false,
        data: null,
        httpStatus: res.status,
        errorMessage: parseApiErrorFromResponse(res, null, operation) +
                      (res.status >= 500 ? '' : ' [non-JSON response]'),
      };
    }

    let data = null;
    try {
      data = await res.json();
    } catch (e) {
      return {
        ok: false,
        data: null,
        httpStatus: res.status,
        errorMessage: parseApiErrorFromException(e, operation),
      };
    }

    const success = res.ok && data && (data.status === 'SUCCESS' || data.status === 'OK');
    if (success) {
      return { ok: true, data, httpStatus: res.status, errorMessage: '' };
    }
    return {
      ok: false,
      data,
      httpStatus: res.status,
      errorMessage: parseApiErrorFromResponse(res, data, operation),
    };
  }

  global.ApiError = {
    parseApiErrorFromResponse,
    parseApiErrorFromException,
    apiFetch,
  };
  // Short aliases for brevity in callsites
  global.apiFetch = apiFetch;
})(window);
