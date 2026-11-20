/* TEMPORARY screenshot harness for demo documentation. Deleted after capture. */
(function () {
  'use strict';
  var A = window.TSApp;
  var params = new URLSearchParams(location.search);
  var shotName = params.get('shot') || 'mps_top';
  if (!params.get('record')) A.recordDiagnostics = null;

  /* Headless + virtual time can freeze the entrance animations (reveal-panel /
     fadeUp) mid-flight, which bakes a washed-out veil into the screenshot.
     Disable motion before the first paint so captures are deterministic. */
  (function killMotion() {
    var style = document.createElement('style');
    style.textContent = '*,*::before,*::after{animation:none !important;transition:none !important}';
    (document.head || document.documentElement).appendChild(style);
  })();

  var SHOTS = {
    mps_top: {
      setup: function () { A.navigate('mps'); },
      marks: [
        { sel: '.nav', n: 1 },
        { sel: '.topbar-right', n: 2 },
        { sel: '.hero', n: 3 },
        { sel: '.mps-kpis', n: 4 },
        { sel: '.note-card', idx: 0, n: 5 },
        { sel: '.source-top', n: 6 }
      ]
    },
    mps_charts: {
      setup: function () { A.navigate('mps'); hide(['.hero', '.mps-kpis', '.note-card']); },
      marks: [
        { sel: '.dash-grid .card', idx: 0, n: 1 },
        { sel: '.dash-grid .card', idx: 1, n: 2 }
      ]
    },
    mps_table: {
      setup: function () { A.navigate('mps'); hide(['.hero', '.mps-kpis', '.note-card', '.dash-grid']); },
      marks: [
        { sel: '.table-card .toolbar', n: 1 },
        { sel: '.table-card .data-table thead', n: 2 },
        { sel: '.table-card .data-table tbody tr', idx: 2, n: 3 }
      ]
    },
    gantt_cp: {
      setup: function () { gantt('cp', '1d'); },
      marks: [
        { sel: '#view-gantt .toolbar', n: 1 },
        { sel: '.gantt-legend', n: 2 },
        { rect: statColsRect, n: 3 },
        { rect: axisRect, n: 4 },
        { rect: firstTaskRect, n: 5 }
      ]
    },
    gantt_stat_hover: {
      setup: async function () {
        gantt('cp', '1d');
        await delay(350);
        hoverMachine(0);
      },
      marks: [
        { sel: '#gantt-tip', n: 1 },
        { rect: machineLabelRect, n: 2 }
      ]
    },
    gantt_task_hover: {
      setup: async function () {
        gantt('cp', '1d');
        await delay(350);
        hoverTask();
      },
      marks: [
        { sel: '#gantt-tip', n: 1 },
        { rect: firstTaskRect, n: 2 }
      ]
    },
    gantt_bake: {
      setup: function () { gantt('bake', '1d'); },
      marks: [
        { sel: '#view-gantt .toolbar', n: 1 },
        { sel: '.gantt-legend', n: 2 },
        { rect: statColsRect, n: 3 },
        { rect: firstBatchRect, n: 4 }
      ]
    },
    gantt_month: {
      setup: function () { gantt('cp', '30d'); },
      marks: [
        { sel: '[data-gantt-range]', n: 1 },
        { rect: axisRect, n: 2 }
      ]
    },
    ppn_search: {
      setup: async function () {
        A.navigate('gantt');
        await delay(300);
        A.searchPpn('PPN01');
        await delay(500);
      },
      marks: [
        { sel: '#gantt-ppn-q', n: 1 },
        { sel: '.ppn-card', n: 2 },
        { sel: '.ppn-table thead', n: 3 },
        { rect: firstTaskRect, n: 4 }
      ]
    },
    ppn_ui_test: {
      setup: async function () {
        A.navigate('gantt');
        await delay(250);
        var input = document.querySelector('#gantt-ppn-q');
        input.value = 'PPN02';
        document.querySelector('[data-gantt-ppn-search]').click();
        await delay(350);
        var matched = document.querySelector('.ppn-title').textContent;
        var rows = document.querySelectorAll('.ppn-table tbody tr').length;
        var platform = A.state.gantt.platform;
        document.querySelector('.ppn-table tbody tr').click();
        await delay(250);
        var drawer = document.querySelector('#drawer').classList.contains('open');
        document.querySelector('.ppn-card [data-gantt-ppn-clear]').click();
        await delay(250);
        var cleared = !A.state.gantt.ppn && !document.querySelector('.ppn-card');
        input = document.querySelector('#gantt-ppn-q');
        input.value = 'PPN05-L0001';
        input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
        await delay(350);
        document.title = 'TEST matched=' + matched + ' rows=' + rows + ' platform=' + platform +
          ' drawer=' + drawer + ' cleared=' + cleared + ' lotEnter=' + A.state.gantt.ppn;
      }
    },
    ppn_range_test: {
      setup: async function () {
        A.navigate('gantt');
        await delay(250);
        A.searchPpn('PPN01');
        await delay(350);
        document.querySelector('[data-gantt-range] button[data-range="7d"]').click();
        await delay(350);
        var range = A.state.gantt.range;
        var tableRows = document.querySelectorAll('.ppn-table tbody tr').length;
        document.querySelector('[data-gantt-mode] button[data-mode="cp"]').click();
        await delay(350);
        var mode = A.state.gantt.mode;
        var machines = A.state.ganttGeom ? A.state.ganttGeom.rows.filter(function (r) { return r.kind === 'machine'; }).length : 0;
        var canvas = !!document.querySelector('#gantt-canvas');
        document.title = 'TEST range=' + range + ' tableRows=' + tableRows + ' mode=' + mode +
          ' machines=' + machines + ' canvas=' + canvas + ' ppn=' + A.state.gantt.ppn;
      }
    },
    gantt_drawer: {
      setup: async function () {
        gantt('cp', '1d');
        await delay(350);
        var task = firstVisibleTask();
        if (task) A.openLotDrawer(task.lotKey || task.lotId);
      },
      marks: [
        { sel: '#drawer', n: 1 },
        { sel: '#drawer .timeline', n: 2 }
      ]
    },
    mps_drawer: {
      setup: async function () {
        A.navigate('mps');
        await delay(250);
        if (A.state.data.mps.length) A.openProductDrawer(A.state.data.mps[0].ppn);
      },
      marks: [
        { sel: '#drawer', n: 1 }
      ]
    },
    settings: {
      setup: async function () {
        A.navigate('mps');
        await delay(200);
        A.openSettings();
      },
      marks: [
        { sel: '#modal .modal-card', n: 1 },
        { sel: '.modal-foot', n: 2 }
      ]
    },
    inputs_top: {
      setup: function () { A.navigate('inputs'); },
      marks: [
        { sel: '#view-inputs .source-hero', n: 1 },
        { sel: '.production-config .config-grid', idx: 0, n: 2 },
        { sel: '.production-config .config-actions', idx: 0, n: 3 },
        { sel: '.production-config', n: 4 }
      ]
    },
    inputs_preview: {
      setup: function () {
        A.navigate('inputs');
        A.$$('#view-inputs details').forEach(function (d) { if (d.querySelector('.inputs-layout')) d.open = true; });
        hide(['#view-inputs .source-hero', '#view-inputs .production-config']);
      },
      marks: [
        { sel: '.inputs-layout', n: 1 },
        { sel: '.sheet-list', n: 2 },
        { sel: '.preview-mapping', n: 3 },
        { sel: '.preview-toolbar', n: 4 }
      ]
    },
    diag: {
      setup: function () {
        A.navigate('diagnostics');
        A.renderDiagnostics();
        var d = A.$('#view-diagnostics details.config-details');
        if (d) d.open = true;
      },
      marks: [
        { sel: '#view-diagnostics .source-hero', n: 1 },
        { sel: '#view-diagnostics .production-config', n: 2 },
        { sel: '#view-diagnostics details.config-details', idx: 1, n: 3 }
      ]
    }
  };

  function delay(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }

  function hide(selectors) {
    var style = document.createElement('style');
    style.textContent = selectors.join(',') + '{display:none !important}';
    document.head.appendChild(style);
  }

  function scrollToSel(sel) {
    var el = document.querySelector(sel);
    if (!el) return;
    var top = el.getBoundingClientRect().top + window.pageYOffset - 96;
    window.scrollTo(0, Math.max(0, top));
  }

  function gantt(mode, range) {
    A.state.gantt.mode = mode;
    A.state.gantt.range = range;
    A.state.gantt.anchor = A.state.result.start;
    A.state.gantt.platform = 'all';
    A.navigate('gantt');
  }

  function canvasBox() {
    var canvas = document.querySelector('#gantt-canvas');
    return canvas ? canvas.getBoundingClientRect() : null;
  }

  function machineRows() {
    var geom = A.state.ganttGeom;
    return geom ? geom.rows.filter(function (r) { return r.kind === 'machine'; }) : [];
  }

  function statColsRect() {
    var box = canvasBox(), geom = A.state.ganttGeom;
    if (!box || !geom) return null;
    var labelW = A.ganttLabelWidth();
    return { x: box.left + labelW - 200, y: box.top, w: 198, h: geom.axisH + 3 * 26 + 26 };
  }

  function axisRect() {
    var box = canvasBox(), geom = A.state.ganttGeom;
    if (!box || !geom) return null;
    return { x: box.left + geom.labelW, y: box.top, w: 600, h: geom.axisH };
  }

  function firstMachineGeom() { return machineRows()[0]; }

  function machineLabelRect() {
    var box = canvasBox(), row = firstMachineGeom();
    if (!box || !row) return null;
    return { x: box.left, y: box.top + row.y, w: A.ganttLabelWidth(), h: row.h };
  }

  function firstTaskRect() {
    var box = canvasBox(), row = firstMachineGeom();
    if (!box || !row) return null;
    var idx = A.state.ganttGeom.rows.indexOf(row);
    var regs = (A.state.ganttRegions[idx] || []).filter(function (r) { return r.type === 'cp'; });
    if (!regs.length) return null;
    var r = regs[0];
    return { x: box.left + r.x1, y: box.top + r.y1, w: Math.max(3, r.x2 - r.x1), h: r.y2 - r.y1 };
  }

  function firstBatchRect() {
    var box = canvasBox(), rows = machineRows();
    if (!box || !rows.length) return null;
    var idx = A.state.ganttGeom.rows.indexOf(rows[0]);
    var regs = (A.state.ganttRegions[idx] || []).filter(function (r) { return r.type === 'bake'; });
    if (!regs.length) return null;
    var r = regs[0];
    return { x: box.left + r.x1, y: box.top + r.y1, w: Math.max(3, r.x2 - r.x1), h: r.y2 - r.y1 };
  }

  function firstVisibleTask() {
    var rows = machineRows();
    for (var i = 0; i < rows.length; i++) {
      var idx = A.state.ganttGeom.rows.indexOf(rows[i]);
      var regs = (A.state.ganttRegions[idx] || []).filter(function (r) { return r.type === 'cp'; });
      if (regs.length) return regs[0].data;
    }
    return null;
  }

  function dispatchMove(x, y) {
    var canvas = document.querySelector('#gantt-canvas');
    if (!canvas) return;
    var box = canvas.getBoundingClientRect();
    canvas.dispatchEvent(new MouseEvent('mousemove', {
      bubbles: true, clientX: box.left + x, clientY: box.top + y
    }));
  }

  function hoverMachine(i) {
    var rows = machineRows();
    var row = rows[i] || rows[0];
    if (!row) return;
    dispatchMove(90, row.y + row.h / 2);
  }

  function hoverTask() {
    var r = firstTaskRect();
    if (!r) return;
    dispatchMove(r.x + Math.min(20, r.w / 2), r.y + r.h / 2);
  }
function annotate(marks) {
    var old = document.getElementById('shot-marks');
    if (old) old.parentNode.removeChild(old);
    var ns = 'http://www.w3.org/2000/svg';
    var svg = document.createElementNS(ns, 'svg');
    svg.setAttribute('id', 'shot-marks');
    svg.setAttribute('width', window.innerWidth);
    svg.setAttribute('height', window.innerHeight);
    svg.setAttribute('style', 'position:fixed;left:0;top:0;z-index:99999;pointer-events:none');
    document.body.appendChild(svg);
    marks.forEach(function (mark) {
      var box = mark.rect ? mark.rect() : null;
      if (!box) {
        var nodes = document.querySelectorAll(mark.sel);
        var el = nodes[mark.idx || 0];
        if (!el) { console.log('MARK MISS ' + mark.n + ' ' + mark.sel); return; }
        var r = el.getBoundingClientRect();
        box = { x: r.left, y: r.top, w: r.width, h: r.height };
      }
      if (!box.w || !box.h) return;
      var pad = 4;
      var x = Math.max(2, box.x - pad), y = Math.max(2, box.y - pad);
      var w = Math.min(window.innerWidth - x - 2, box.w + pad * 2);
      var h = Math.min(window.innerHeight - y - 2, box.h + pad * 2);
      var rect = document.createElementNS(ns, 'rect');
      rect.setAttribute('x', x); rect.setAttribute('y', y);
      rect.setAttribute('width', w); rect.setAttribute('height', h);
      rect.setAttribute('rx', 8);
      rect.setAttribute('fill', 'rgba(224,69,63,.045)');
      rect.setAttribute('stroke', '#e0453f');
      rect.setAttribute('stroke-width', 1.8);
      rect.setAttribute('stroke-dasharray', '7 4');
      svg.appendChild(rect);

      var ring = document.createElementNS(ns, 'circle');
      ring.setAttribute('cx', x); ring.setAttribute('cy', y); ring.setAttribute('r', 12);
      ring.setAttribute('fill', '#e0453f');
      ring.setAttribute('stroke', '#fff');
      ring.setAttribute('stroke-width', 2.5);
      svg.appendChild(ring);
      var text = document.createElementNS(ns, 'text');
      text.setAttribute('x', x); text.setAttribute('y', y + 4);
      text.setAttribute('text-anchor', 'middle');
      text.setAttribute('font-size', 11);
      text.setAttribute('font-weight', 700);
      text.setAttribute('font-family', '"PingFang SC", sans-serif');
      text.setAttribute('fill', '#fff');
      text.textContent = String(mark.n);
      svg.appendChild(text);
    });
  }

  async function ready() {
    var tries = 0;
    while (tries < 600) {
      tries += 1;
      if (A && A.state && A.state.result) return true;
      await delay(50);
    }
    return false;
  }

  ready().then(async function (ok) {
    if (!ok) { console.log('READY TIMEOUT'); return; }
    await delay(300);
    var shot = SHOTS[shotName] || SHOTS.mps_top;
    if (A.closeDrawer) A.closeDrawer();
    if (shot.setup) await shot.setup();
    await delay(650);
    annotate(shot.marks || []);
    /* Re-measure periodically: late layout shifts (modal body, fonts, drawer
       content) would otherwise leave the callout boxes misaligned by the time
       Chrome finally grabs the frame. */
    var ticks = 0;
    var timer = setInterval(function () {
      ticks += 1;
      if (ticks > 120) { clearInterval(timer); return; }
      annotate(shot.marks || []);
    }, 250);
    await delay(250);
    console.log('SHOT READY ' + shotName);
  });
})();