/* Browser transport and date helpers. Scheduling runs in Python via /api/schedule. */
(function (global) {
  'use strict';
  var MIN = 60000, HOUR = 3600000, DAY = 86400000;

  function parseDate(value) {
    if (!value) return NaN;
    // Match backend.input_rules.parse_datetime; dates without a zone use China time.
    var text = String(value).trim().replace(/年|月|\//g, '-').replace(/日/g, '');
    var m = /^(\d{4})-(\d{1,2})-(\d{1,2})(?:[Tt ](\d{1,2}):(\d{2})(?::(\d{2})(?:\.(\d{1,6}))?)?)?([Zz]|[+-]\d{2}:?\d{2})?$/.exec(text);
    if (!m || (m[8] && !m[4])) return NaN;
    var y = +m[1], month = +m[2], day = +m[3], hour = +(m[4] || 0), minute = +(m[5] || 0), second = +(m[6] || 0);
    if (!y || month < 1 || month > 12 || day < 1 || day > 31 || hour > 23 || minute > 59 || second > 59) return NaN;
    var date = new Date(0);
    date.setUTCFullYear(y, month - 1, day);
    date.setUTCHours(hour, minute, second, Number(((m[7] || '') + '000').slice(0, 3)));
    if (date.getUTCMonth() !== month - 1 || date.getUTCDate() !== day) return NaN;
    var offset = 480;
    if (m[8]) {
      var zone = m[8].replace(':', '');
      if (/z/i.test(zone)) offset = 0;
      else {
        var zh = +zone.slice(1, 3), zm = +zone.slice(3);
        if (zh > 23 || zm > 59) return NaN;
        offset = (zh * 60 + zm) * (zone[0] === '-' ? -1 : 1);
      }
    }
    return date.getTime() - offset * MIN;
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
