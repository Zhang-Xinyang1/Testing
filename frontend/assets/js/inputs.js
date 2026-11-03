/* Inputs & configuration view. */
(function () {
  'use strict';
  var A = window.TSApp;
  var $ = A.$, esc = A.esc, num = A.num;

  function sheetList() {
    var d = A.state.data;
    var found = {};
    (d.sheetSummary || []).forEach(function (s) { found[s.name] = s; });
    return A.SHEET_ORDER.filter(function (n) { return found[n]; }).map(function (n) { return found[n]; })
      .concat((d.sheetSummary || []).filter(function (s) { return A.SHEET_ORDER.indexOf(s.name) < 0; }));
  }

  function renderInputs() {
    var d = A.state.data, meta = d.meta || {};
    var sheets = sheetList();
    if (!sheets.some(function (s) { return s.name === A.state.inputs.sheet; }) && sheets.length) A.state.inputs.sheet = sheets[0].name;
    var html = '<div class="source-hero"><div><div class="source-title">' + esc((meta.source || '内置算例').split(/[\\/]/).pop()) + '</div>' +
      '<div class="source-sub">' + esc(meta.month || '') + ' · ' + num(d.lots.length) + ' lot · ' + sheets.length + ' 张表</div></div>' +
      '<div class="source-actions"><span class="pill green">Excel 只读</span>' +
      '<button class="btn ghost small" data-export="mps">导出 MPS</button><button class="btn ghost small" data-export="lots">导出排产</button></div></div>';

    html += A.configurationHTML();
    html += '<details class="config-details"><summary>数据预览</summary>';
    html += '<div class="inputs-layout">' +
      '<div class="sheet-list">' + sheets.map(function (s) {
        return '<button class="sheet-btn ' + (s.name === A.state.inputs.sheet ? 'active' : '') + '" data-inputs-sheet="' + esc(s.name) + '">' +
          '<span class="sheet-name">' + esc(s.name) + '</span><span class="sheet-rows">' + num(s.rows) + ' 行</span></button>';
      }).join('') + '</div>' +
      '<div class="preview-card" id="inputs-preview-wrap">' + inputsPreviewHTML() + '</div>' +
    '</div></details>';

    html += '<details class="config-details"><summary>输入核验</summary><div class="check-list">' + validationHTML() + '</div></details>';
    $('#view-inputs').innerHTML = html;
  }

  function sheetData(name) {
    var d = A.state.data;
    var sheetMap = d.sheetMap || {};
    var role = Object.keys(sheetMap).find(function (key) { return sheetMap[key] === name; });
    var names = {mps:'MPS', eqp:'EQP', cards:'针卡', flow:'测试flow&工时', borrow:'借机计划', pm:'PM计划', constraints:'约束条件', params:'算例参数', lots:'Lot明细', weekly:'周目标'};
    name = names[role] || name;
    if (name === 'MPS') return { fields: ['Platform', 'PPN', 'MPS'], rows: d.mps.map(function (x) { return [x.platform, x.ppn, x.mps]; }) };
    if (name === 'EQP') return { fields: ['TYPE', 'EQPID', 'Current ProbeCard', 'Current Recipe', 'status'], rows: d.eqp.map(function (x) { return [x.type, x.id, x.card, x.recipe, x.status]; }) };
    if (name === '针卡') return { fields: ['Recipe', 'ProbeCard List'], rows: d.recipes.map(function (x) { return [x.recipe, x.cards.join(';')]; }) };
    if (name === '测试flow&工时') {
      var rows = [];
      Object.keys(d.flows).forEach(function (p) { d.flows[p].forEach(function (op) { rows.push([p, op[0], op[1], op[2]]); }); });
      return { fields: ['PROD', 'STEP', 'RECIPE', 'TIME'], rows: rows };
    }
    if (name === '借机计划') return { fields: ['DATE', 'EQPID', '借机时数', 'Generated At'], rows: d.borrow.map(function (x) { return [x.date, x.eqp, x.hours, x.generatedAt || '']; }) };
    if (name === 'PM计划') return { fields: ['EQPID', 'Early Due', 'Due Date', 'Over Due', 'Estimated Hours'], rows: d.pm.map(function (x) { return [x.eqp, x.early, x.due, x.over, x.hours]; }) };
    if (name === '约束条件') return { fields: ['TYPE', 'EQPID', 'Constraint Recipe'], rows: (d.constraints || []).map(function (x) { return [x.type, x.eqp, x.recipe]; }) };
    if (name === '算例参数') return { fields: ['类别', '参数', '对象', '值', '单位', '依据'], rows: (d.rules || []).map(function (x) { return [x.category, x.parameter, x.object, x.value, x.unit, x.basis]; }) };
    if (name === 'Lot明细') return { fields: d.lotFields || [], rows: d.lots };
    if (name === '周目标') return { fields: ['Platform', 'PPN', 'Week', 'Week Start', 'Week End', 'Target Lots'], rows: d.weekly.map(function (x) { return [x.platform, x.ppn, x.week, x.start, x.end, x.target]; }) };
    return { fields: [], rows: [] };
  }

  function inputsPreviewHTML() {
    var name = A.state.inputs.sheet;
    var sd = sheetData(name);
    var q = A.state.inputs.q.trim().toLowerCase();
    var rows = sd.rows.filter(function (r) {
      if (!q) return true;
      return r.some(function (v) { return String(v).toLowerCase().indexOf(q) >= 0; });
    });
    var shown = rows.slice(0, 50);
    var meta = (A.state.data.sheetSummary || []).filter(function (s) { return s.name === name; })[0] || { rows: sd.rows.length };
    var fm = (A.state.data.fieldMap || {})[name] || {};
    var fmKeys = Object.keys(fm).filter(function (k) { return k !== 'separator'; });
    var html = '<div class="preview-head"><div><h3>' + esc(name) + '</h3><p>参考表记录 ' + num(meta.rows) + ' 行 · 预览字段 ' + sd.fields.length + ' 个</p></div>' +
      '<span class="pill blue">' + (A.state.mode === 'server' ? '服务端读取' : '内置数据') + '</span></div>';
    html += '<details class="preview-mapping"><summary>字段映射</summary><div class="field-pills">' + sd.fields.map(function (f) { return '<span class="field-pill">' + esc(f) + '</span>'; }).join('') + '</div>';
    if (fmKeys.length) {
      html += '<div class="field-map">' +
        fmKeys.map(function (k) { return '<span class="map-item"><b>' + esc(k) + '</b> → ' + esc(fm[k]) + '</span>'; }).join('') + '</div>';
    } else if (A.state.mode === 'server') {
      html += '<div class="field-map muted small">该表未配置 fieldMap，按 Excel 表头原样读取。</div>';
    }
    html += '</details><div class="preview-toolbar"><input class="input search" id="inputs-q" placeholder="在 ' + esc(name) + ' 中搜索…" value="' + esc(A.state.inputs.q) + '">' +
      '<span class="filter-count" id="inputs-count">显示 ' + shown.length + ' / ' + num(rows.length) + ' 行</span></div>';
    html += '<div class="table-scroll" style="max-height:430px"><table class="data-table compact"><thead><tr>' +
      sd.fields.map(function (f) { return '<th>' + esc(f) + '</th>'; }).join('') + '</tr></thead><tbody>' +
      (shown.length ? shown.map(function (r) {
        return '<tr>' + sd.fields.map(function (_, i) { return '<td>' + esc(r[i] == null ? '' : r[i]) + '</td>'; }).join('') + '</tr>';
      }).join('') : '<tr><td colspan="' + Math.max(1, sd.fields.length) + '"><div class="empty-state">没有匹配记录</div></td></tr>') +
      '</tbody></table></div>';
    return html;
  }

  function renderInputsTable() {
    var wrap = $('#inputs-preview-wrap');
    if (wrap) wrap.innerHTML = inputsPreviewHTML();
  }
function validationHTML() {
    var d = A.state.data, r = A.state.result;
    var checks = [];
    var mpsSum = (d.mps || []).reduce(function (a, x) { return a + x.mps; }, 0);
    checks.push([mpsSum === d.lots.length, 'MPS 需求与 Lot 数一致：' + num(mpsSum) + ' / ' + num(d.lots.length) + ' lots']);
    checks.push([Object.keys(d.flows).length === (d.mps || []).length, '工艺覆盖：' + Object.keys(d.flows).length + ' 个 PROD / ' + (d.mps || []).length + ' 个 PPN']);
    var flowRows = Object.keys(d.flows).reduce(function (a, p) { return a + d.flows[p].length; }, 0);
    checks.push([flowRows > 0, '工艺记录：' + flowRows + ' 行，展开 ' + num(r.metrics.cpOps) + ' 道 CP 工序']);
    var noCards = (d.recipes || []).filter(function (x) { return !x.cards.length; }).length;
    checks.push([noCards === 0, 'Recipe 与针卡：' + (d.recipes || []).length + ' 个 Recipe，' + noCards + ' 个无候选针卡']);
    var cards = {};
    (d.recipes || []).forEach(function (x) { x.cards.forEach(function (c) { cards[c] = 1; }); });
    checks.push([true, '物理针卡：' + Object.keys(cards).length + ' 张，按 TYPE 互斥占用']);
    checks.push([r.metrics.pmInWindow === r.metrics.pmCount, 'PM 窗口：' + r.metrics.pmInWindow + ' / ' + r.metrics.pmCount + ' 次在允许窗口内']);
    var maxBatchLots = Math.max.apply(null, r.batches.map(function (b) { return b.lots.length; }).concat([0]));
    checks.push([maxBatchLots <= 4 && r.metrics.maxBatchWafers <= 50, 'BAKE 组批：最大 ' + maxBatchLots + ' lots / ' + r.metrics.maxBatchWafers + ' 片（上限 4 / 50）']);
    checks.push([r.metrics.borrowCancelRate <= 0.1, '借机取消率：' + A.pct(r.metrics.borrowCancelRate, 1) + '（阈值 10%）']);
    checks.push([r.metrics.completedMonth >= mpsSum, '算法完成 / Excel MPS：' + num(r.metrics.completedMonth) + ' / ' + num(mpsSum) + ' lots；在制另列 ' + num(r.metrics.wipLots || 0) + ' lots']);
    return checks.map(function (c) {
      return '<div class="check-item"><span class="check-icon ' + (c[0] ? '' : 'warn') + '">' + (c[0] ? '✓' : '!') + '</span><span>' + esc(c[1]) + '</span></div>';
    }).join('');
  }

  A.renderInputs = renderInputs;
  A.renderInputsTable = renderInputsTable;
})();
