/* UI: drawer, settings modal, exports and delegated events. */
(function () {
  'use strict';
  var A = window.TSApp;
  var $ = A.$, esc = A.esc, pct = A.pct, num = A.num, fmtDate = A.fmtDate, fmtDateTime = A.fmtDateTime, pfBadge = A.pfBadge;
  var HOUR = A.HOUR, DAY = A.DAY;

  function openDrawer(kicker, title, html) {
    $('#drawer-kicker').textContent = kicker;
    $('#drawer-title').textContent = title;
    $('#drawer-body').innerHTML = html;
    $('#drawer').classList.add('open');
    $('#drawer').setAttribute('aria-hidden', 'false');
    $('#drawer-scrim').classList.add('show');
  }
  function closeDrawer() {
    $('#drawer').classList.remove('open');
    $('#drawer').setAttribute('aria-hidden', 'true');
    $('#drawer-scrim').classList.remove('show');
  }

  function lotStatusPill(s) {
    if (s === 'on-time') return '<span class="pill green">按期</span>';
    if (s === 'late-week') return '<span class="pill amber">晚于周目标</span>';
    if (s === 'late-month') return '<span class="pill red">超月</span>';
    return '<span class="pill">待排</span>';
  }

  function openLotDrawer(lotKey) {
    var lot = A.state.result.lots.filter(function (l) { return l.key === lotKey; })[0] ||
      A.state.result.lots.filter(function (l) { return l.id === lotKey; })[0];
    if (!lot) return;
    var html = '<div class="detail-hero"><div class="detail-hero-top">' + pfBadge(lot.platform) + '<h3>' + esc(lot.id) + '</h3>' + lotStatusPill(lot.status) + '</div>' +
      '<p>' + esc(lot.prod) + ' · ' + lot.wafer + ' wafer · MPS 月结 ' + esc(lot.monthlyDue) + '</p>' +
      '<p>在制行键：' + esc(lot.key || '') + '（重新导入明细后请核对）</p>' +
      '<div class="detail-stats"><div class="detail-stat"><span>路线工序数</span><b>' + lot.ops.length + '</b></div>' +
        '<div class="detail-stat"><span>CP 完成</span><b>' + fmtDateTime(lot.cpEnd) + '</b></div>' +
        '<div class="detail-stat"><span>完工</span><b>' + fmtDateTime(lot.complete) + '</b></div></div></div>';
    html += '<div class="timeline"><div class="timeline-title">实际工序顺序</div>';
    (lot.routeTasks||[]).forEach(function(t){
      html += '<div class="timeline-item"><div class="timeline-dot">'+esc(t.step)+'</div><div class="timeline-main"><b>'+esc(t.recipe)+'</b><span>'+esc(t.machine||t.chamber||'不占资源等待')+(t.card?' · 针卡类型 '+esc(t.card):'')+(t.setup?' · 换卡 5 min':'')+'</span><div class="timeline-time">'+fmtDateTime(t.start)+' → '+fmtDateTime(t.end)+(t.resourceEnd?' · 腔体释放 '+fmtDateTime(t.resourceEnd):'')+'</div></div></div>';
    });
    html += '</div>';
    openDrawer('LOT DETAIL', lot.id, html);
  }

  function openProductDrawer(ppn) {
    var row = A.mpsRows().filter(function (x) { return x.ppn === ppn; })[0];
    if (!row) return;
    var route = A.state.data.flows[ppn] || [];
    var html = '<div class="detail-hero"><div class="detail-hero-top">' + pfBadge(row.platform) + '<h3>' + esc(ppn) + '</h3>' +
      (row.status === '达成' ? '<span class="pill green">达成</span>' : '<span class="pill red">缺口 ' + (-row.delta) + '</span>') + '</div>' +
      '<p>Excel MPS 目标 ' + num(row.mps) + ' wafer · 算法完成 ' + num(row.completed) + ' wafer</p>' +
      '<div class="detail-stats"><div class="detail-stat"><span>MPS</span><b>' + num(row.mps) + '</b></div>' +
        '<div class="detail-stat"><span>算法完成</span><b>' + num(row.completed) + '</b></div>' +
        '<div class="detail-stat"><span>达成率</span><b>' + pct(row.rate, 1) + '</b></div></div></div>';
    html += '<div class="timeline"><div class="timeline-title"><span>工艺路线</span><span class="muted tiny">' + route.length + ' 道工序</span></div>';
    route.forEach(function (op) {
      html += '<div class="route-item"><div class="route-index">' + esc(op[0]) + '</div><div class="route-info"><b>' + esc(op[1]) + '</b>' +
        '<span>' + (op[0] === 'BAKE' ? op[2] + ' 小时/批' : op[0] === 'QTIME1' ? op[2] + ' 分钟等待' : op[2] + ' 分钟/片') + '</span></div></div>';
    });
    html += '</div><div class="divider"></div><div class="kv"><dt>首件完成</dt><dd>' + fmtDateTime(row.first) + '</dd>' +
      '<dt>末件完成</dt><dd>' + fmtDateTime(row.last) + '</dd>' +
      '<dt>Lot 数</dt><dd>' + num(row.lots) + '</dd>' +
      '<dt>在制另列</dt><dd>' + num(row.wip || 0) + ' lot</dd></div>';
    openDrawer('MPS DETAIL', ppn, html);
  }

  function openBatchDrawer(batchId) {
    var b = A.state.result.batches.filter(function (x) { return x.id === batchId; })[0];
    if (!b) return;
    var html = '<div class="detail-hero"><div class="detail-hero-top"><span class="pill amber">' + esc(b.recipe) + '</span><h3>' + esc(b.id) + '</h3></div>' +
      '<p>' + esc(b.chamber) + ' · ' + b.lots.length + ' lots / ' + b.wafers + ' 片</p>' +
      '<div class="detail-stats"><div class="detail-stat"><span>开始</span><b>' + fmtDateTime(b.start) + '</b></div>' +
        '<div class="detail-stat"><span>结束</span><b>' + fmtDateTime(b.end) + '</b></div><div class="detail-stat"><span>Lot 放行</span><b>' + fmtDateTime(b.processEnd) + '</b></div>' +
        '<div class="detail-stat"><span>时长</span><b>' + (b.duration / HOUR).toFixed(1) + ' h</b></div></div></div>';
    html += '<table class="task-table"><thead><tr><th>Lot</th><th>PPN</th><th>Platform</th></tr></thead><tbody>' +
      b.lots.map(function (l) { return '<tr><td>' + esc(l.id) + '</td><td>' + esc(l.prod) + '</td><td>' + esc(l.platform) + '</td></tr>'; }).join('') +
      '</tbody></table>';
    openDrawer('BAKE BATCH', b.id, html);
  }

  function optionHTML(key, value, title, desc) {
    var active = A.state.draft && A.state.draft[key] === value ? ' active' : '';
    return '<button class="option' + active + '" data-key="' + key + '" data-value="' + value + '"><b>' + title + '</b><span>' + desc + '</span></button>';
  }

  function openSettings() {
    A.state.draft = JSON.parse(JSON.stringify(A.state.options));
    var html = '<p>目标：各 PPN 月末 MPS 达成率均衡。换卡固定 5 分钟。</p><p>系统比较多种派工顺序，并针对低达成率产品和延期 Lot 重新排程，保留综合目标最好的方案。可跨组产品按每道 CP 的资源可用情况选机。</p><p>BAKE 每腔最多 4 Lot，升温 2h、Recipe 加工 24h、降温/换 Lot 2h，共占腔 28h，Lot 在第 26h 放行。算法比较立即开批与等待 4h、8h 的方案。</p><p>借机固定机台，指定日期内开始，允许跨日。</p>';
    $('#modal-body').innerHTML = html;
    $('#modal').classList.add('open');
    $('#modal').setAttribute('aria-hidden', 'false');
  }
  function closeModal() {
    $('#modal').classList.remove('open');
    $('#modal').setAttribute('aria-hidden', 'true');
  }
  function applySettings() {
    A.state.options = {strategy:'attainment',borrowMode:'reserve',bakeWindowHours:null,setupMinutes:5};
    closeModal();
    A.runSchedule();
  }

  function onGlobalClick(e) {
    if (e.target.id === 'gantt-canvas') {
      var hit = A.ganttHit(e);
      if (hit && hit.type === 'cp') openLotDrawer(hit.data.lotKey || hit.data.lotId);
      else if (hit && hit.type === 'bake') openBatchDrawer(hit.data.id);
      else if (hit && hit.type === 'pm') A.toast('PM 维护：' + hit.res.id + ' · ' + fmtDateTime(hit.data[0]) + ' → ' + fmtDateTime(hit.data[1]));
      else if (hit && hit.type === 'borrow') A.toast('借机 ' + hit.data.id + ' · ' + hit.data.eqp + ' · 按指定时间执行');
      else if (hit && hit.type === 'wip') A.toast('在制 Lot ' + hit.data.lotId + ' · 预计释放 ' + fmtDateTime(A.SCHED.parseDate(hit.data.end)));
      return;
    }
    var product = e.target.closest('[data-product]');
    if (product) { openProductDrawer(product.getAttribute('data-product')); return; }
    var gmode = e.target.closest('[data-gantt-mode] button');
    if (gmode) { A.state.gantt.mode = gmode.getAttribute('data-mode'); A.renderGantt(); return; }
    var grange = e.target.closest('[data-gantt-range] button');
    if (grange) {
      A.state.gantt.range = grange.getAttribute('data-range');
      if (A.state.gantt.range === '30d') A.state.gantt.anchor = A.state.result.start;
      A.renderGantt(); return;
    }
    var gnav = e.target.closest('[data-gantt-nav]');
    if (gnav) {
      var days = { '1d': 1, '3d': 3, '7d': 7, '30d': 30 }[A.state.gantt.range] || 1;
      A.state.gantt.anchor = Math.max(A.state.result.start,
        Math.min(A.state.result.monthEnd - DAY, A.state.gantt.anchor + Number(gnav.getAttribute('data-gantt-nav')) * days * DAY));
      A.renderGantt(); return;
    }
    if (e.target.closest('[data-gantt-month]')) { A.state.gantt.anchor = A.state.result.start; A.renderGantt(); return; }
    var sheet = e.target.closest('[data-inputs-sheet]');
    if (sheet) { A.state.inputs.sheet = sheet.getAttribute('data-inputs-sheet'); A.state.inputs.q = ''; A.renderInputs(); return; }
    var exp = e.target.closest('[data-export]');
    if (exp) { exportCsv(exp.getAttribute('data-export')); return; }
    var opt = e.target.closest('.option');
    if (opt) {
      var key = opt.getAttribute('data-key'), val = opt.getAttribute('data-value');
      A.state.draft[key] = val;
      A.$$('.option[data-key="' + key + '"]').forEach(function (o) { o.classList.toggle('active', o === opt); });
    }
  }

  function onGlobalChange(e) {
    if (e.target.id === 'mps-platform') { A.state.mps.platform = e.target.value; A.renderMpsTable(); }
    if (e.target.id === 'gantt-platform') { A.state.gantt.platform = e.target.value; A.renderGantt(); }
  }

  function onGlobalInput(e) {
    if (e.target.id === 'mps-q') { A.state.mps.q = e.target.value; A.renderMpsTable(); }
    if (e.target.id === 'inputs-q') {
      var v = e.target.value;
      A.state.inputs.q = v;
      A.renderInputsTable();
      var n = $('#inputs-q');
      if (n) { n.focus(); n.setSelectionRange(v.length, v.length); }
    }
    if (e.target.id === 'gantt-lot-q') A.state.gantt.q = e.target.value;
  }

  function csvCell(v) {
    var s = String(v == null ? '' : v);
    return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
  }

  function exportCsv(kind) {
    if (!A.state.result) { A.toast('尚无排产结果，请先排产再导出'); return; }
    var lines = [], filename = '';
    if (kind === 'mps') {
      var rows = A.mpsRows();
      lines.push(['Platform', 'PPN', 'MPS_Target_Wafers', 'Lot_Count', 'WIP_Excluded_Lots', 'Completed_Wafers', 'Delta_Wafers', 'Achievement',
        'First_Completion', 'Last_Completion', 'Status'].map(csvCell).join(','));
      rows.forEach(function (x) {
        lines.push([x.platform, x.ppn, x.mps, x.lots, x.wip, x.completed, x.delta, pct(x.rate, 2),
          fmtDateTime(x.first), fmtDateTime(x.last), x.status].map(csvCell).join(','));
      });
      filename = 'MPS_comparison_' + (A.state.data.meta.month || '') + '.csv';
    } else {
      lines.push(['LotID','PROD','Platform','Wafer_Qty','StepIndex','STEP','RECIPE','Resource','CardType','Start','End','LotComplete','Status'].map(csvCell).join(','));
      A.state.result.lots.forEach(function(l){
        (l.routeTasks||[]).forEach(function(t,i){
          lines.push([l.id,l.prod,l.platform,l.wafer,i+1,t.step,t.recipe,t.machine||t.chamber||'',t.card||'',fmtDateTime(t.start),fmtDateTime(t.end),fmtDateTime(l.complete),l.status].map(csvCell).join(','));
        });
      });
      filename = 'schedule_lots_' + (A.state.data.meta.month || '') + '.csv';
    }
    var blob = new Blob(['\ufeff' + lines.join('\n')], { type: 'text/csv;charset=utf-8' });
    var a = document.createElement('a');
    a.href = URL.createObjectURL(blob); a.download = filename;
    document.body.appendChild(a); a.click();
    setTimeout(function () { URL.revokeObjectURL(a.href); document.body.removeChild(a); }, 300);
    A.toast('已导出 ' + filename);
  }

  A.openDrawer = openDrawer;
  A.closeDrawer = closeDrawer;
  A.openLotDrawer = openLotDrawer;
  A.openProductDrawer = openProductDrawer;
  A.openBatchDrawer = openBatchDrawer;
  A.openSettings = openSettings;
  A.closeModal = closeModal;
  A.applySettings = applySettings;

  document.addEventListener('click', onGlobalClick);
  document.addEventListener('change', onGlobalChange);
  document.addEventListener('input', onGlobalInput);
  document.addEventListener('mousemove', function (e) { A.onGanttMove(e); });
  document.addEventListener('mouseleave', function () { A.hideTip(); });
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') { closeModal(); closeDrawer(); }
    if (e.key === 'Enter' && e.target.id === 'gantt-lot-q') A.locateLot(e.target.value);
  });
  var resizeTimer = null;
  window.addEventListener('resize', function () {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(function () { if (A.state.view === 'gantt' && A.state.result) A.drawGantt(); }, 120);
  });
})();
