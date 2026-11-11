/* Core: state, helpers, data loading, scheduling run, navigation. */
(function () {
  'use strict';

  var A = window.TSApp = window.TSApp || {};
  var SCHED = window.TestingScheduler;
  var DAY = 86400000, HOUR = 3600000;

  A.DAY = DAY; A.HOUR = HOUR;
  A.$ = function (s, root) { return (root || document).querySelector(s); };
  A.$$ = function (s, root) { return Array.prototype.slice.call((root || document).querySelectorAll(s)); };
  A.SCHED = SCHED;
  A.PLATFORM_COLORS = { 'CP-A': '#3b74ff', 'CP-B': '#0fa8a0', 'CP-C': '#e89b1c' };
  A.RECIPE_COLORS = { '210C24H': '#d28b35', '250C24H': '#c35b58' };
  A.ICONS = {
    target: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1.4" fill="currentColor" stroke="none"/></svg>',
    gantt: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M4 5h10M4 12h16M4 19h7"/><rect x="3" y="3" width="4" height="4" rx="1"/><rect x="15" y="10" width="5" height="4" rx="1"/><rect x="11" y="17" width="6" height="4" rx="1"/></svg>',
    database: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v7c0 1.7 3.6 3 8 3s8-1.3 8-3V5"/><path d="M4 12v7c0 1.7 3.6 3 8 3s8-1.3 8-3v-7"/></svg>'
  };
  A.TITLES = {
    mps: ['MPS 达成', 'Excel MPS 与算法完成量'],
    gantt: ['排产甘特', '机台与腔体时间轴'],
    inputs: ['输入与配置', '数据、机台与计划']
  };
  A.SHEET_ORDER = ['MPS', 'EQP', '针卡', '测试flow&工时', '借机计划', 'PM计划', '约束条件'];

  A.state = {
    data: null, config: null, mode: 'embedded', loadError: null, result: null,
    options: { strategy: 'attainment', borrowMode: 'reserve', bakeWindowHours: null, setupMinutes: 5, maxDelayHours: 0 },
    view: 'mps',
    mps: { q: '', platform: 'all' },
    gantt: { mode: 'all', range: '1d', anchor: 0, platform: 'all', q: '' },
    inputs: { sheet: 'MPS', q: '' },
    ganttRegions: [], ganttRows: [], ganttGeom: null,
    draft: null
  };
  A.stateRef = A.state;

  A.esc = function (s) { return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) { return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]; }); };
  A.pct = function (v, d) { return (v * 100).toFixed(d == null ? 1 : d) + '%'; };
  A.num = function (v) { return Math.round(v).toLocaleString('en-US'); };
  A.fmtDate = function (ms) { if (ms == null || !isFinite(ms)) return '—'; var d = new Date(ms + 8 * HOUR); return (d.getUTCMonth() + 1) + '/' + d.getUTCDate(); };
  A.fmtDateTime = function (ms) { return ms != null && isFinite(ms) ? A.fmtDate(ms) + ' ' + SCHED.fmt(ms, false) : '—'; };
  A.pad2 = function (n) { return n < 10 ? '0' + n : '' + n; };
  A.bar = function (v, cls) { return '<div class="bar ' + (cls || '') + '"><span style="width:' + (Math.max(0, Math.min(1, v)) * 100).toFixed(1) + '%"></span></div>'; };
  A.pfBadge = function (p) { return '<span class="platform-badge pf-' + A.esc(p) + '">' + A.esc(p) + '</span>'; };
  A.strategyName = function (s) {
    if (s === 'attainment') return '产品 MPS 达成率均衡';
    if (s === 'weekly') return (A.state.result && A.state.result.weeklyHasTargets) ? '周目标优先' : '周次优先';
    return { due: '交期优先', changeover: '减少换卡', spt: '短工序优先' }[s] || s;
  };
  A.borrowName = function (b) { return '指定日期内安排借机'; };

  A.boot = function () {
    A.$$('[data-icon]').forEach(function (el) {
      el.outerHTML = '<span class="nav-ico">' + (A.ICONS[el.getAttribute('data-icon')] || '') + '</span>';
    });
    A.bindShell();
    var h = (location.hash || '').replace('#', '');
    if (A.TITLES[h]) A.state.view = h;
    return window.DataLoader.load().then(function (res) {
      A.state.data = res.data;
      A.state.config = res.config;
      A.state.mode = res.mode;
      A.state.loadError = res.error;
      A.updateSourceCard();
      A.syncNav();
      if (res.mode !== 'server' && window.DEMO_RESULT) {
        A.state.result = window.DEMO_RESULT;
        if (!A.state.gantt.anchor) A.state.gantt.anchor = A.state.result.start;
        A.hideLoading();
        A.updateRunChip();
        A.renderView();
      } else if (res.mode !== 'server') {
        A.hideLoading();
        A.$('#run-chip').textContent = '静态模式';
        A.renderView();
        A.toast('静态模式不执行排产，请使用 Python 服务地址');
      } else {
        A.hideLoading();
        A.updateRunChip();
        if (res.error) A.navigate('inputs');
        else A.renderView();
      }
      if (res.error) A.toast((res.mode === 'server' ? '数据读取失败，可在输入与配置页修正：' : '已使用内置数据：') + res.error);
    }).catch(function (err) {
      console.error(err);
      A.state.loadError = err.message;
      A.hideLoading();
      A.syncNav();
      A.updateRunChip();
      A.$('#view-' + A.state.view).innerHTML = '<div class="error-box"><b>服务连接失败</b>\n' + A.esc(err.message) + '\n请检查服务后刷新页面重试。</div>';
    });
  };

  A.updateSourceCard = function () {
    var d = A.state.data || {mps:[], lots:[]}, meta = d.meta || {};
    var name = A.$('#source-name'), sub = A.$('#source-meta'), status = A.$('#source-status'), note = A.$('#source-note');
    if (name) name.textContent = (meta.source || ((A.state.config || {}).dataSource || {}).displayName || ((A.state.config || {}).dataSource || {}).path || '未读取数据').split(/[\\/]/).pop();
    if (sub) sub.textContent = (meta.month || '') + ' · ' + A.num(d.mps.reduce(function(s,m){return s+m.mps;},0)) + ' wafer / ' + A.num(d.lots.length) + ' lots · ' + (A.state.mode === 'server' ? '服务端数据' : '内置数据');
    if (status) status.textContent = A.state.loadError ? '数据读取失败，请修正配置' : A.state.mode === 'server' ? 'Excel 数据源已连接' : '本地内置数据';
    if (note) note.textContent = A.state.mode === 'server' ? '参考数据只读' : '内置算例';
  };

  A.bindShell = function () {
    A.$('#nav').addEventListener('click', function (e) {
      var b = e.target.closest('[data-view]');
      if (b) A.navigate(b.getAttribute('data-view'));
    });
    A.$('#btn-run').addEventListener('click', A.reloadAndRun);
    A.$('#btn-import').addEventListener('click', A.openExcelImport);
    A.$('#btn-settings').addEventListener('click', function () { A.openSettings(); });
    A.$('#drawer-close').addEventListener('click', function () { A.closeDrawer(); });
    A.$('#drawer-scrim').addEventListener('click', function () { A.closeDrawer(); });
    A.$('#modal-close').addEventListener('click', function () { A.closeModal(); });
    A.$('#modal-cancel').addEventListener('click', function () { A.closeModal(); });
    A.$('#modal-apply').addEventListener('click', function () { A.applySettings(); });
    A.$('#modal').addEventListener('click', function (e) { if (e.target.id === 'modal') A.closeModal(); });
    window.addEventListener('hashchange', function () {
      var v = (location.hash || '').replace('#', '');
      if (A.TITLES[v] && v !== A.state.view) { A.state.view = v; A.syncNav(); A.renderView(); }
    });
  };

  A.syncNav = function () {
    A.$$('.nav-item').forEach(function (b) { b.classList.toggle('active', b.getAttribute('data-view') === A.state.view); });
    A.$$('.view').forEach(function (v) { v.classList.toggle('active', v.id === 'view-' + A.state.view); });
    A.$('#page-title').textContent = A.TITLES[A.state.view][0];
    A.$('#page-sub').textContent = A.TITLES[A.state.view][1];
  };

  A.navigate = function (view) {
    if (!A.TITLES[view]) return;
    A.state.view = view;
    history.replaceState(null, '', '#' + view);
    A.syncNav();
    A.renderView();
    window.scrollTo({ top: 0, behavior: 'smooth' });
  };

  A.openExcelImport = function () {
    if (A.state.mode !== 'server') {
      A.toast('Excel 导入需要启动后端服务：运行 python3 run.py 后访问 http://127.0.0.1:8080');
      return;
    }
    A.navigate('inputs');
    var input = A.$('#cfg-file');
    if (input) input.click();
  };

  function inputSignature(data) {
    var snapshot = JSON.parse(JSON.stringify(data));
    if (snapshot.meta) delete snapshot.meta.loadedAt;
    return JSON.stringify(snapshot);
  }
  A.canReuseSchedule = function (cached, input, options) {
    return !!(cached && cached.result && cached.input &&
      inputSignature(cached.input) === inputSignature(input) &&
      JSON.stringify(cached.options) === JSON.stringify(options));
  };
  A.reloadAndRun = async function () {
    if (A.state.mode !== 'server') { A.toast('请连接 Python 服务后排产'); return; }
    var sequence = A.state.runSequence = (A.state.runSequence || 0) + 1;
    A.state.result = null;
    A.showLoading('正在读取已保存的数据', '读取完成后开始排产');
    try {
      var res = await window.DataLoader.load();
      if (sequence !== A.state.runSequence) return;
      if (res.mode !== 'server') throw new Error(res.error || '服务不可用');
      A.state.data = res.data; A.state.config = res.config; A.state.loadError = res.error;
      A.updateSourceCard();
      if (res.error) throw new Error(res.error);
      await A.runSchedule();
    } catch (err) {
      if (sequence !== A.state.runSequence) return;
      A.state.loadError = err.message;
      A.state.data = null;
      A.updateSourceCard();
      A.hideLoading(); A.updateRunChip();
      A.toast('未排产：' + err.message);
      if (A.state.config) A.navigate('inputs');
      else A.renderView();
    }
  };
  A.runSchedule = async function (cached) {
    if (!A.state.data) return;
    if (A.state.mode !== 'server') {
      A.toast('静态模式不执行排产，请启动 Python 服务');
      return;
    }
    var sequence = A.state.runSequence = (A.state.runSequence || 0) + 1;
    var snapshot = {input:JSON.parse(JSON.stringify(A.state.data)), options:JSON.parse(JSON.stringify(A.state.options))};
    A.state.result = null;
    A.showLoading('正在排产', A.num(snapshot.input.lots.length) + ' lot');
      try {
        var t0 = performance.now();
        var res = A.canReuseSchedule(cached, snapshot.input, snapshot.options) ? cached.result : await SCHED.run(snapshot.input, snapshot.options);
        if (sequence !== A.state.runSequence) return;
        A.state.result = res;
        if (A.recordDiagnostics) await A.recordDiagnostics(res, null, snapshot);
        if (sequence !== A.state.runSequence) return;
        if (!A.state.gantt.anchor) A.state.gantt.anchor = res.start;
        A.hideLoading();
        A.updateRunChip();
        A.renderView();
        A.toast('排产完成：' + A.num(res.metrics.completedWafers) + ' / ' + A.num(res.metrics.totalWafers) + ' wafer（' + A.num(res.metrics.completedLots) + ' lots）在月内完成 · ' + Math.round(performance.now() - t0) + ' ms');
      } catch (err) {
        if (sequence !== A.state.runSequence) return;
        console.error(err);
        A.hideLoading();
        A.updateRunChip();
        if (A.recordDiagnostics) await A.recordDiagnostics(null, err, snapshot);
        if (sequence !== A.state.runSequence) return;
        A.toast('排产失败：' + err.message);
        if (A.renderDiagnostics) A.navigate('diagnostics');
      }
  };

  A.showLoading = function (title, sub) {
    A.$('#loading-title').textContent = title;
    A.$('#loading-sub').textContent = sub;
    A.$('#loading').classList.add('show');
    A.$('#btn-run').disabled = true;
  };
  A.hideLoading = function () { A.$('#loading').classList.remove('show'); A.$('#btn-run').disabled = false; };
  A.toast = function (msg) {
    var el = A.$('#toast'); el.textContent = msg; el.classList.add('show');
    clearTimeout(el._t); el._t = setTimeout(function () { el.classList.remove('show'); }, 3200);
  };
  A.updateRunChip = function () {
    if (!A.state.result) { A.$('#run-chip').textContent = A.state.loadError ? '数据待修正' : '尚未排产'; return; }
    var d = new Date(A.state.result.runAt);
    A.$('#run-chip').textContent = '上次排产 ' + d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }) + ' · ' + Math.round(A.state.result.runMs) + ' ms';
  };
  A.renderView = function () {
    if (A.state.view === 'diagnostics' && A.renderDiagnostics) { A.renderDiagnostics(); return; }
    if (A.state.view === 'inputs' && (A.state.data || A.state.config) && A.state.mode === 'server') { A.renderInputs(); return; }
    if (!A.state.result) {
      var el = A.$('#view-' + A.state.view);
      if (el && A.state.mode !== 'server') {
        el.innerHTML = '<div class="error-box"><b>静态模式仅展示内置数据</b>' +
          '\n排产由 Python 后端执行。请运行 <code>python3 run.py</code>，' +
          '然后访问 <code>http://127.0.0.1:8080</code>。</div>';
      }
      if (el && A.state.mode === 'server') el.innerHTML = '<div class="empty-state"><b>尚未排产</b><p>打开或刷新页面只读取数据。请先检查“输入与配置”，再点击“重新排产”。该按钮读取已保存的数据；未保存的编辑请使用“保存并排产”。</p></div>';
      return;
    }
    if (A.state.view === 'mps') A.renderMps();
    else if (A.state.view === 'gantt') A.renderGantt();
    else if (A.state.view === 'inputs') A.renderInputs();
  };

  window.addEventListener('DOMContentLoaded', A.boot);
})();
