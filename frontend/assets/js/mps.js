/* MPS result & comparison view. */
(function () {
  'use strict';
  var A = window.TSApp;
  var $ = A.$, esc = A.esc, pct = A.pct, num = A.num, fmtDate = A.fmtDate, bar = A.bar, pfBadge = A.pfBadge;

  function mpsRows() {
    var d = A.state.data, r = A.state.result;
    var byProd = {};
    var lotProducts = {}, wipByProd = {};
    var productsByKey = {};
    d.lots.forEach(function (l, i) { lotProducts[l[0]] = l[1]; productsByKey['LOT-' + String(i + 1).padStart(6, '0')] = l[1]; });
    (r.wip || []).forEach(function (w) { var p = w.lotKey ? productsByKey[w.lotKey] : lotProducts[w.lotId]; wipByProd[p] = (wipByProd[p] || 0) + 1; });
    r.lots.forEach(function (l) { (byProd[l.prod] = byProd[l.prod] || []).push(l); });
    return (d.mps || []).map(function (m) {
      var arr = byProd[m.ppn] || [];
      var done = arr.filter(function (l) { return l.complete != null && l.complete <= r.monthEnd; });
      var completedWafers=done.reduce(function(s,l){return s+l.wafer;},0);
      var completes = arr.map(function (l) { return l.complete; }).filter(function (v) { return Number.isFinite(v); });
      var first = completes.length ? Math.min.apply(null, completes) : NaN;
      var last = completes.length ? Math.max.apply(null, completes) : NaN;
      var inProcess = wipByProd[m.ppn] || 0;
      return {
        platform: m.platform, ppn: m.ppn, mps: m.mps, lots: arr.length,
        completed: completedWafers, completedLots:done.length, delta: completedWafers - m.mps,
        rate: m.mps ? completedWafers / m.mps : 1, first: first, last: last,
        wip: inProcess,
        status: completedWafers >= m.mps ? '达成' : '缺口'
      };
    });
  }

  function renderMps() {
    var d = A.state.data, r = A.state.result, rows = mpsRows();
    var target = rows.reduce(function (a, x) { return a + x.mps; }, 0);
    var completed = rows.reduce(function (a, x) { return a + x.completed; }, 0);
    var gap = completed - target;
    var html = '<div class="hero">' +
      '<div class="hero-main"><div class="hero-title">MPS 对比</div>' +
        '<div class="hero-sub">Excel MPS 与算法月内完成量，单位 wafer。</div>' +
        '<div class="hero-tags"><span class="hero-tag">' + esc(d.meta.month || '') + '</span>' +
          '<span class="hero-tag">' + esc(A.strategyName(r.options.strategy)) + '</span></div></div>' +
      '<div class="hero-side"><div class="donut-wrap"><div class="donut" style="--deg:' + (target ? Math.min(1, completed / target) * 360 : 0).toFixed(1) + 'deg">' +
        '<div class="donut-center"><div><b>' + (target ? pct(completed / target, 1) : '—') + '</b><span>MPS 达成率</span></div></div></div></div></div></div>';

    html += '<div class="kpi-grid mps-kpis">' +
      mpsKpi('blue', 'Excel MPS', num(target), 'wafer') +
      mpsKpi('teal', '算法完成', num(completed), 'wafer') +
      mpsKpi(gap < 0 ? 'red' : 'green', '差额', (gap >= 0 ? '+' : '') + num(gap), 'wafer') +
      mpsKpi('amber', '在制另列', num(r.metrics.wipLots || 0), 'lot') + '</div>';
    if (d.meta.lotSizing === 'default-25-tail') html += '<div class="note-card">未提供实际 Lot 明细，当前按每产品 25 片满批加余数尾批生成 '+num(d.lots.length)+' 个 Lot。每 Lot 最多25片；真实拆分可不同。</div>';
    html += '<div class="note-card">MPS 的 PPN 以第一个 - 前为准，- 后厂家后缀忽略；同一 PPN 多行按 PPN 汇总 wafer，Platform 合并去重。</div>';
    if (r.metrics.unscheduledLots) html += '<div class="note-card">' + num(r.metrics.unscheduledLots) + ' lot 缺少可用资源，请检查机台和针卡。</div>';
    html += '<div class="note-card">优化目标：产品 MPS 达成率均衡。最低产品达成率 '+pct(r.metrics.minProductRate,1)+'，产品达成率平均 '+pct(r.metrics.meanProductRate,1)+'。全厂总量达成率与产品均衡指标分别展示。</div>';
    if (r.optimization && r.optimization.algorithm === 'search') {
      var opt = r.optimization;
      html += '<div class="note-card">已比较 '+num(opt.evaluations)+' 个候选排程。原方案 → 选中方案：最低产品达成率 '+pct(opt.baseline.minProductRate,1)+' → '+pct(opt.selected.minProductRate,1)+'；月内完成 '+num(opt.baseline.completedWafers)+' → '+num(opt.selected.completedWafers)+' wafer。优先改善低达成率产品，未证明全局最优。</div>';
    }
    html += '<div class="dash-grid">' +
      '<div class="card"><div class="card-head"><div><h3>平台完成量</h3></div><span class="pill ' + (gap < 0 ? 'red' : 'green') + '">' + (gap < 0 ? '存在缺口' : 'MPS 达成') + '</span></div>' +
        '<div class="card-body">' + platformMpsChart(rows) + '</div></div>' +
      '<div class="card"><div class="card-head"><div><h3>' + (r.weeklyHasTargets ? '周累计完成' : '每周完成量') + '</h3></div></div>' +
        '<div class="card-body">' + weeklyChartSVG(r, 560, 248) + chartLegend(r.weeklyHasTargets) + '</div></div>' +
    '</div>';

    html += mpsTableCard(rows);
    var weeklyNote = r.weeklyHasTargets
      ? '周目标按累计量比较。'
      : '未提供周目标，周图只展示每周完成量，不做目标对比。';
    html += '<details class="config-details"><summary>统计口径</summary><p class="note-card">差额 = 算法完成 − Excel MPS。在制单列，不计入算法完成量。完成时间包含 QTIME；' + weeklyNote + '缺口表示当前方案未达成。</p></details>';
    $('#view-mps').innerHTML = html;
  }

  function mpsKpi(color, label, value, unit) {
    return '<div class="kpi ' + color + '"><div class="kpi-label">' + label + '</div>' +
      '<div class="kpi-value">' + value + (unit ? '<small>' + unit + '</small>' : '') + '</div>' +
      '</div>';
  }

  function mpsTableCard(rows) {
    return '<div class="table-card" style="margin-top:18px">' +
      '<div class="toolbar" style="margin:0;border:0;border-bottom:1px solid var(--line);border-radius:0;box-shadow:none">' +
        '<span class="label">筛选</span>' +
        '<input class="input search" id="mps-q" placeholder="搜索 PPN，如 PPN01" value="' + esc(A.state.mps.q) + '">' +
        '<select class="select" id="mps-platform"><option value="all">全部 Platform</option>' +
          Array.from(new Set(A.state.data.mps.map(function (m) { return m.platform; }))).map(function (p) { return '<option value="' + p + '"' + (A.state.mps.platform === p ? ' selected' : '') + '>' + p + '</option>'; }).join('') +
        '</select>' +
        '<span class="filter-count" id="mps-count">' + filteredMpsRows(rows).length + ' / ' + rows.length + ' 个 PPN</span>' +
      '</div>' +
      '<div id="mps-table-wrap">' + mpsTableHTML(rows) + '</div>' +
    '</div>';
  }

  function filteredMpsRows(rows) {
    var q = A.state.mps.q.trim().toUpperCase();
    return rows.filter(function (x) {
      if (A.state.mps.platform !== 'all' && x.platform !== A.state.mps.platform) return false;
      if (q && x.ppn.toUpperCase().indexOf(q) < 0) return false;
      return true;
    });
  }

  function mpsTableHTML(rows) {
    var f = filteredMpsRows(rows);
    var body = f.map(function (x) {
      var status = x.status === '达成' ? '<span class="pill green">达成</span>' : '<span class="pill red">缺口 ' + (-x.delta) + '</span>';
      return '<tr class="clickable" data-product="' + esc(x.ppn) + '">' +
        '<td>' + pfBadge(x.platform) + '</td>' +
        '<td><span class="id">' + esc(x.ppn) + '</span></td>' +
        '<td class="num">' + num(x.mps) + '</td>' +
        '<td class="num">' + num(x.lots) + '</td><td class="num">' + num(x.wip) + '</td>' +
        '<td class="num">' + num(x.completed) + '</td>' +
        '<td class="num ' + (x.delta < 0 ? 'neg' : 'pos') + '">' + (x.delta >= 0 ? '+' : '') + num(x.delta) + '</td>' +
        '<td><div class="row" style="gap:7px"><span class="tiny">' + pct(x.rate, 0) + '</span><div style="width:54px">' + bar(x.rate, x.rate >= 1 ? 'teal' : 'red') + '</div></div></td>' +
        '<td class="tiny">' + fmtDate(x.first) + '</td>' +
        '<td class="tiny">' + fmtDate(x.last) + '</td>' +
        '<td>' + status + '</td></tr>';
    }).join('');
    if (!body) body = '<tr><td colspan="' + 11 + '"><div class="empty-state">没有匹配的 PPN</div></td></tr>';
    return '<div class="table-scroll"><table class="data-table"><thead><tr>' +
      '<th>Platform</th><th>PPN</th><th class="right">Excel MPS (wafer)</th><th class="right">待排 Lot</th><th class="right">在制 Lot</th><th class="right">算法完成 (wafer)</th><th class="right">差异 (wafer)</th><th>达成率</th><th>首件完成</th><th>末件完成</th><th>状态</th>' +
      '</tr></thead><tbody>' + body + '</tbody></table></div>';
  }

  function renderMpsTable() {
    var rows = mpsRows();
    var wrap = $('#mps-table-wrap');
    if (wrap) wrap.innerHTML = mpsTableHTML(rows);
    var c = $('#mps-count'); if (c) c.textContent = filteredMpsRows(rows).length + ' / ' + rows.length + ' 个 PPN';
  }

  function platformMpsChart(rows) {
    var groups = Array.from(new Set(rows.map(function (r) { return r.platform; }))).map(function (p) {
      var arr = rows.filter(function (x) { return x.platform === p; });
      return { p: p,
        target: arr.reduce(function (a, x) { return a + x.mps; }, 0),
        done: arr.reduce(function (a, x) { return a + x.completed; }, 0) };
    });
    var w = 620, h = 238, pl = 38, pr = 14, pt = 18, pb = 34;
    var iw = w - pl - pr, ih = h - pt - pb;
    var max = Math.max.apply(null, groups.map(function (g) { return Math.max(g.target, g.done); }).concat([1]));
    var step = iw / groups.length, bw = Math.min(20, step * 0.18);
    var svg = '<svg class="weekly-chart" viewBox="0 0 ' + w + ' ' + h + '" preserveAspectRatio="none">';
    for (var i = 0; i <= 4; i++) {
      var y = pt + ih - ih * i / 4;
      svg += '<line x1="' + pl + '" y1="' + y + '" x2="' + (w - pr) + '" y2="' + y + '" stroke="#edf1f7"/>';
      svg += '<text x="' + (pl - 7) + '" y="' + (y + 3) + '" text-anchor="end" font-size="8" fill="#94a1b4">' + (max * i / 4 / 1000).toFixed(1) + 'k</text>';
    }
    groups.forEach(function (g, i) {
      var cx = pl + step * i + step / 2;
      var vals = [{ v: g.target, c: '#dfe7f5', o: -bw * 1.55 }, { v: g.done, c: '#108c72', o: -bw / 2 }];
      vals.forEach(function (v) {
        var hh = ih * v.v / max;
        svg += '<rect x="' + (cx + v.o) + '" y="' + (pt + ih - hh) + '" width="' + bw + '" height="' + hh + '" rx="2.5" fill="' + v.c + '"/>';
      });
      svg += '<text x="' + cx + '" y="' + (h - 16) + '" text-anchor="middle" font-size="9" font-weight="700" fill="#2c3d58">' + g.p + '</text>';
      svg += '<text x="' + cx + '" y="' + (h - 4) + '" text-anchor="middle" font-size="7.5" fill="#94a1b4">' + num(g.done) + ' / ' + num(g.target) + '</text>';
    });
    svg += '</svg>';
    return svg + '<div class="chart-legend"><span><i style="background:#dfe7f5"></i>Excel MPS</span><span><i style="background:#108c72"></i>算法完成</span>' +
      '</div>';
  }

  function weeklyChartSVG(r, w, h) {
    var weeks = r.weekly || [];
    if (!weeks.length) return '<div class="empty-state">暂无可统计的周完成数据。</div>';
    var hasTargets = !!r.weeklyHasTargets;
    var pl = 38, pr = 14, pt = 16, pb = 30;
    var iw = w - pl - pr, ih = h - pt - pb;
    var max = Math.max.apply(null, weeks.map(function (x) {
      return hasTargets ? Math.max(x.targetCum, x.plannedCum) : x.plannedWeek;
    }).concat([1]));
    var step = iw / weeks.length, bw = Math.min(hasTargets ? 28 : 36, step * (hasTargets ? 0.25 : 0.32));
    var svg = '<svg class="weekly-chart" viewBox="0 0 ' + w + ' ' + h + '" preserveAspectRatio="none">';
    for (var g = 0; g <= 4; g++) {
      var y = pt + ih - ih * g / 4;
      svg += '<line x1="' + pl + '" y1="' + y + '" x2="' + (w - pr) + '" y2="' + y + '" stroke="#edf1f7"/>';
      svg += '<text x="' + (pl - 7) + '" y="' + (y + 3) + '" text-anchor="end" font-size="8" fill="#94a1b4">' + (max * g / 4 / 1000).toFixed(1) + 'k</text>';
    }
    weeks.forEach(function (x, i) {
      var cx = pl + step * i + step / 2;
      if (hasTargets) {
        var h1 = ih * x.targetCum / max, h2 = ih * x.plannedCum / max;
        svg += '<rect x="' + (cx - bw - 2) + '" y="' + (pt + ih - h1) + '" width="' + bw + '" height="' + h1 + '" rx="3" fill="#dfe7f5"/>';
        svg += '<rect x="' + (cx + 2) + '" y="' + (pt + ih - h2) + '" width="' + bw + '" height="' + h2 + '" rx="3" fill="#108c72"/>';
        svg += '<text x="' + (cx + 2 + bw / 2) + '" y="' + (pt + ih - h2 - 5) + '" text-anchor="middle" font-size="8" fill="#108c72" font-weight="700">' + x.plannedCum + '</text>';
      } else {
        var hh = ih * x.plannedWeek / max;
        var yTop = pt + ih - hh;
        svg += '<rect x="' + (cx - bw / 2) + '" y="' + yTop + '" width="' + bw + '" height="' + hh + '" rx="3" fill="#108c72"/>';
        svg += '<text x="' + cx + '" y="' + (yTop - 5) + '" text-anchor="middle" font-size="8" fill="#108c72" font-weight="700">' + x.plannedWeek + '</text>';
      }
      svg += '<text x="' + cx + '" y="' + (h - 9) + '" text-anchor="middle" font-size="8.5" fill="#6b7a91" font-weight="600">' + x.week + '</text>';
      svg += '<text x="' + cx + '" y="' + (h - 1) + '" text-anchor="middle" font-size="7.5" fill="#a6b2c4">' + fmtDate(x.end - 1) + '</text>';
    });
    svg += '</svg>';
    return svg;
  }

  function chartLegend(hasTargets) {
    if (!hasTargets) return '<div class="chart-legend"><span><i style="background:#108c72"></i>每周完成</span></div>';
    return '<div class="chart-legend"><span><i style="background:#dfe7f5"></i>累计目标</span><span><i style="background:#108c72"></i>累计完成</span></div>';
  }

  A.mpsRows = mpsRows;
  A.renderMps = renderMps;
  A.renderMpsTable = renderMpsTable;
  A.openProductDrawer = A.openProductDrawer || function () {};
})();
