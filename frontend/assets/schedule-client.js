/* Browser transport and date helpers. Scheduling runs in Python via /api/schedule. */
(function (global) {
  'use strict';
  var MIN = 60000, HOUR = 3600000, DAY = 86400000;

  function parseDate(value) {
    if (!value) return NaN;
    value = String(value);
    return Date.parse(value.indexOf('T') >= 0 ? value : value + 'T00:00:00+08:00');
  }
  function pad(value) { return String(value).padStart(2, '0'); }
  function fmt(value, withDate) {
    if (!isFinite(value)) return '—';
    var date = new Date(value + 8 * HOUR);
    return (withDate ? (date.getUTCMonth() + 1) + '/' + date.getUTCDate() + ' ' : '') +
      pad(date.getUTCHours()) + ':' + pad(date.getUTCMinutes());
  }
  function dayKey(value) {
    var date = new Date(value + 8 * HOUR);
    return date.getUTCFullYear() + '-' + pad(date.getUTCMonth() + 1) + '-' +
      pad(date.getUTCDate());
  }
  function apiBase() {
    var base = window.APP_BASE || '/';
    if (base.charAt(base.length - 1) !== '/') base += '/';
    return base;
  }
  async function run(data, options) {
    var response = await fetch(apiBase() + 'api/schedule', {
      method: 'POST',
      cache: 'no-store',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ data: data, options: options || {} })
    });
    var body = await response.json().catch(function () { return {}; });
    if (!response.ok) throw new Error(body.error || ('排产接口 HTTP ' + response.status));
    if (!body.result) throw new Error('排产接口未返回结果');
    return body.result;
  }

  global.TestingScheduler = {
    run: run, parseDate: parseDate, fmt: fmt, dayKey: dayKey,
    MIN: MIN, HOUR: HOUR, DAY: DAY
  };
})(window);