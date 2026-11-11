/* Runtime data loader: server API first, embedded demo data as fallback. */
(function () {
  'use strict';

  function apiBase() {
    var base = window.APP_BASE || '/';
    if (base.charAt(base.length - 1) !== '/') base += '/';
    return base;
  }

  async function fetchJson(url) {
    var res = await fetch(url, { cache: 'no-store' });
    if (!res.ok) {
      var body = await res.json().catch(function () { return {}; });
      var error = new Error(body.error || url + ' -> HTTP ' + res.status);
      error.status = res.status; error.issues = body.issues || [];
      throw error;
    }
    return res.json();
  }

  async function load() {
    if (location.protocol === 'http:' || location.protocol === 'https:') {
      try {
        var base = apiBase();
        var config = await fetchJson(base + 'api/config');
        try {
          var data = await fetchJson(base + 'api/data');
          if (!data || !data.mps || !data.lots) throw new Error('API returned empty data');
          return { data: data, config: config, mode: 'server', error: null };
        } catch (dataError) {
          // Keep import and configuration available when the saved workbook is invalid.
          return { data: null, config: config, mode: 'server', error: dataError.message, issues: dataError.issues || [] };
        }
      } catch (e) {
        if (e.status === 404 && window.DEMO_DATA) {
          return { data: window.DEMO_DATA, config: null, mode: 'embedded', error: e.message };
        }
        throw e;
      }
    }
    if (window.DEMO_DATA) return { data: window.DEMO_DATA, config: null, mode: 'embedded', error: null };
    throw new Error('No data source: start server.py or include assets/data.js');
  }

  window.DataLoader = { load: load };
})();
