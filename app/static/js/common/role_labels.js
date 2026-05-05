/**
 * 공통 role label 헬퍼 — window.dybRoleLabel / window.dybLoadRoleLabels.
 *
 * 사용:
 *   await dybLoadRoleLabels();              // 페이지 init 시 1회 (캐시 워밍)
 *   const label = dybRoleLabel('TL');       // 'NET Team Lead'
 *   const label = dybRoleLabel('ui_test');  // 'UI Test' (custom role label)
 *   const label = dybRoleLabel('unknown');  // 'unknown' (graceful fallback)
 *
 * 백엔드: /api/v2/admin/roles?include_deprecated=1 (admin 인증 필요).
 * 캐시: 모듈 변수 _cache (페이지 lifetime). 60초 TTL 은 서버 측에서 처리하므로
 *       클라 캐시는 새로고침 전까지 유지. 즉시 반영이 필요하면 dybLoadRoleLabels(true).
 *
 * 동시 요청 dedup — _fetchPromise 로 in-flight 보호.
 */
(function (global) {
  let _cache = null;
  let _fetchPromise = null;

  // System role 의 한글/이중 표기 매핑 — fetch 결과가 도착하기 전에도 일관된 표시.
  // portal_roles 의 label 이 우선이며, 이는 fallback 만.
  const _SYSTEM_FALLBACK = {
    admin:    'Admin',
    MASTER:   'Admin',
    retired:  'Retired',
    '퇴사':    'Retired',
  };

  function dybRoleLabel(name) {
    if (!name || typeof name !== 'string') return name || '';
    if (_cache) {
      const found = _cache.find(r => r.name === name);
      if (found) return found.label || found.name;
    }
    if (_SYSTEM_FALLBACK[name]) return _SYSTEM_FALLBACK[name];
    return name;
  }

  function dybLoadRoleLabels(force) {
    if (_cache && !force) return Promise.resolve(_cache);
    if (_fetchPromise) return _fetchPromise;
    _fetchPromise = fetch('/api/v2/admin/roles?include_deprecated=1', {
      credentials: 'same-origin',
    })
      .then(r => r.ok ? r.json() : null)
      .then(data => {
        if (data && data.status === 'SUCCESS' && data.data && Array.isArray(data.data.roles)) {
          _cache = data.data.roles;
        }
        return _cache || [];
      })
      .catch(() => _cache || [])
      .finally(() => { _fetchPromise = null; });
    return _fetchPromise;
  }

  // 외부에서 캐시 직접 주입 가능 (예: SSR 로 미리 로드한 경우)
  function dybSetRoleLabelsCache(roles) {
    if (Array.isArray(roles)) _cache = roles;
  }

  global.dybRoleLabel = dybRoleLabel;
  global.dybLoadRoleLabels = dybLoadRoleLabels;
  global.dybSetRoleLabelsCache = dybSetRoleLabelsCache;
})(window);
