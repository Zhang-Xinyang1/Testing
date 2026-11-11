/* Inputs & configuration view. */
(function () {
  'use strict';
  var A = window.TSApp;
  var $ = A.$, esc = A.esc, num = A.num;
  var SHEETS = {mps:'MPS', eqp:'EQP', cards:'针卡', flow:'测试flow&工时', borrow:'借机计划', pm:'PM计划', constraints:'约束条件', params:'算例参数', lots:'Lot明细', weekly:'周目标', wip:'在制'};
  function sheetRole(name) {
    var map = A.state.data.sheetMap || SHEETS;
    return Object.keys(map).find(function (key) { return A.sheetKey(map[key]) === A.sheetKey(name); });
  }
  function fieldMapping(name) {
    var map = A.state.data.fieldMap || {};
    var key = Object.keys(map).find(function (key) { return A.sheetKey(key) === A.sheetKey(name); });
    return map[key] || {};
  }

  function sheetList() {
    var d = A.state.data;
    var found = {};
    (d.sheetSummary || []).forEach(function (s) { found[s.name] = s; });
    return A.SHEET_ORDER.filter(function (n) { return found[n]; }).map(function (n) { return found[n]; })
      .concat((d.sheetSummary || []).filter(function (s) { return A.SHEET_ORDER.indexOf(s.name) < 0; }));
  }

  function renderInputs() {
    if (A.captureConfigurationDraft) A.captureConfigurationDraft();
    if (!A.state.data) {
      $('#view-inputs').innerHTML = '<div class="error-box"><b>数据读取失败</b>\n' + esc(A.state.loadError || '暂无有效数据') + '\n可重新导入 Excel、修正字段映射或手工配置，然后保存并排产。</div>' + A.configurationHTML();
      if (A.restoreConfigurationDraft) A.restoreConfigurationDraft();
      return;
    }
    var d = A.state.data, meta = d.meta || {};
    var sheets = sheetList();
    if (!sheets.some(function (s) { return s.name === A.state.inputs.sheet; }) && sheets.length) A.state.inputs.sheet = sheets[0].name;
    var html = '<div class="source-hero"><div><div class="source-title">' + esc((meta.source || '内置算例').split(/[\\/]/).pop()) + '</div>' +
      '<div class="source-sub">' + esc(meta.month || '') + ' · ' + num(d.lots.length) + ' lot · ' + sheets.length + ' 张表</div></div>' +
      '<div class="source-actions"><button class="btn small source-import" id="inputs-import" type="button">Excel 导入</button>' +
      '<span class="pill green">Excel 只读</span>' +
      '<button class="btn ghost small" data-export="mps">导出 MPS</button><button class="btn ghost small" data-export="lots">导出排产</button><button class="btn ghost small" data-export="pm">导出 PM 对应明细</button></div></div>';

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
    if (A.pmTraceHTML) html += A.pmTraceHTML();
    $('#view-inputs').innerHTML = html;
    if (A.restoreConfigurationDraft) A.restoreConfigurationDraft();
  }

  function mappedFields(sheetName, defaultFields, logicalFields) {
    var map = fieldMapping(sheetName);
    return logicalFields.map(function (key, index) {
      var value = map[key];
      if (Array.isArray(value)) value = value[0];
      return value || defaultFields[index];
    });
  }

  function displayMapping(value) {
    return Array.isArray(value) ? value.join(' / ') : value;
  }

  function sheetData(name) {
    var d = A.state.data;
    var sheetMap = d.sheetMap || {};
    var role = sheetRole(name);
    name = SHEETS[role];
    if (name === 'MPS') return { fields: mappedFields(sheetMap.mps || 'MPS', ['Platform', 'PPN', 'MPS'], ['platform', 'ppn', 'mps']), rows: d.mps.map(function (x) { return [x.platform, x.ppn, x.mps]; }) };
    if (name === 'EQP') return { fields: ['TYPE', 'EQPID', 'proberID', 'Current ProbeCard', 'Current Recipe', 'status'], rows: d.eqp.map(function (x) { return [x.type, x.id, x.proberId || '', x.card, x.recipe, x.status]; }) };
    if (name === '针卡') return { fields: ['Recipe', 'ProbeCard List'], rows: d.recipes.map(function (x) { return [x.recipe, x.cards.join(';')]; }) };
    if (name === '测试flow&工时') {
      var rows = [];
      Object.keys(d.flows).forEach(function (p) { d.flows[p].forEach(function (op) { rows.push([p, op[0], op[1], op[2]]); }); });
      return { fields: mappedFields(sheetMap.flow || '测试flow&工时', ['PROD', 'STEP', 'RECIPE', 'TIME'], ['prod', 'step', 'recipe', 'time']), rows: rows };
    }
    if (name === '借机计划') return { fields: ['DATE', 'EQPID', '借机时数', 'Generated At'], rows: d.borrow.map(function (x) { return [x.date, x.eqp, x.hours, x.generatedAt || '']; }) };
    if (name === 'PM计划') return { fields: ['维护对象（EQPID / proberID）', 'TargetKind', 'Early Due', 'Due Date', 'Over Due', 'Estimated Hours', 'Start'], rows: d.pm.map(function (x) { return [x.eqp, x.targetKind || '自动识别', x.early, x.due, x.over, x.hours, x.start || '']; }) };
    if (name === '约束条件') return { fields: ['TYPE', 'EQPID', 'Constraint Recipe'], rows: (d.constraints || []).map(function (x) { return [x.type, x.eqp, x.recipe]; }) };
    if (name === '算例参数') return { fields: ['类别', '参数', '对象', '值', '单位', '依据'], rows: (d.rules || []).map(function (x) { return [x.category, x.parameter, x.object, x.value, x.unit, x.basis]; }) };
    if (name === 'Lot明细') return { fields: d.lotFields || [], rows: d.lots };
    if (name === '周目标') return { fields: ['Platform', 'PPN', 'Week', 'Week Start', 'Week End', 'Target Wafers'], rows: d.weekly.map(function (x) { return [x.platform, x.ppn, x.week, x.start, x.end, x.target]; }) };
    if (name === '在制') return { fields: ['LotID', 'LotKey', 'EQPID', 'Chamber', 'Card', 'Start', 'End'], rows: (d.wip || []).map(function (x) { return [x.lotId, x.lotKey, x.eqp, x.chamber, x.card, x.start, x.end]; }) };
    return { fields: [], rows: [], unmapped:true };
  }

  function inputsPreviewHTML() {
    var name = A.state.inputs.sheet;
    var sd = sheetData(name);
    var q = A.state.inputs.q.trim().toLowerCase();
    var rows = sd.rows.filter(function (r) {
      if (!q) return true;
      return r.some(function (v) { return String(v).toLowerCase().indexOf(q) >= 0; });
    });
    var pages = Math.max(1, Math.ceil(rows.length / 50));
    var page = A.state.inputs.page = Math.max(1, Math.min(pages, Math.floor(Number(A.state.inputs.page)) || 1));
    var shown = rows.slice((page - 1) * 50, page * 50);
    var meta = (A.state.data.sheetSummary || []).filter(function (s) { return s.name === name; })[0] || { rows: sd.rows.length };
    var fm = fieldMapping(name);
    var fmKeys = Object.keys(fm).filter(function (k) { return k !== 'separator'; });
    var role = sheetRole(name), dataMeta = A.state.data.meta || {};
    var source = (role === 'eqp' ? dataMeta.eqpSource : (dataMeta.planSources || {})[role]) === 'override' ? '手工覆盖' : 'Excel';
    var html = '<div class="preview-head"><div><h3>' + esc(name) + '</h3><p>参考表记录 ' + num(meta.rows) + ' 行 · 预览字段 ' + sd.fields.length + ' 个</p></div>' +
      '<span class="pill blue">' + (sd.unmapped ? '未映射，不参与排产' : '有效输入 · ' + (A.state.mode === 'server' ? source : '内置数据')) + '</span></div>';
    html += '<details class="preview-mapping"><summary>字段映射</summary><div class="field-pills">' + sd.fields.map(function (f) { return '<span class="field-pill">' + esc(f) + '</span>'; }).join('') + '</div>';
    if (fmKeys.length) {
      html += '<div class="field-map">' +
        fmKeys.map(function (k) { return '<span class="map-item"><b>' + esc(k) + '</b> → ' + esc(displayMapping(fm[k])) + '</span>'; }).join('') + '</div>';
    } else if (A.state.mode === 'server') {
      html += '<div class="field-map muted small">该表未配置 fieldMap，按 Excel 表头原样读取。</div>';
    }
    html += '</details><div class="preview-toolbar"><input class="input search" id="inputs-q" placeholder="在 ' + esc(name) + ' 中搜索…" value="' + esc(A.state.inputs.q) + '">' +
      '<span class="filter-count" id="inputs-count">第 ' + (rows.length ? (page - 1) * 50 + 1 : 0) + '–' + Math.min(page * 50, rows.length) + ' 条 / 共 ' + num(rows.length) + ' 条</span></div>' +
      '<div class="preview-toolbar"><button class="btn ghost small" data-inputs-page="' + (page - 1) + '"' + (page === 1 ? ' disabled' : '') + '>上一页</button>' +
      '<label>第 <input class="input" id="inputs-page" type="number" min="1" max="' + pages + '" value="' + page + '" style="width:80px"> / ' + pages + ' 页</label>' +
      '<button class="btn ghost small" data-inputs-page="' + (page + 1) + '"' + (page === pages ? ' disabled' : '') + '>下一页</button></div>';
    html += '<div class="table-scroll" style="max-height:430px"><table class="data-table compact"><thead><tr>' +
      sd.fields.map(function (f) { return '<th>' + esc(f) + '</th>'; }).join('') + '</tr></thead><tbody>' +
      (shown.length ? shown.map(function (r) {
        return '<tr>' + sd.fields.map(function (_, i) { return '<td>' + esc(r[i] == null ? '' : r[i]) + '</td>'; }).join('') + '</tr>';
      }).join('') : '<tr><td colspan="' + Math.max(1, sd.fields.length) + '"><div class="empty-state">' + (sd.unmapped ? '此表未配置业务映射，不作为有效排产输入。' : '没有匹配记录') + '</div></td></tr>') +
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
    var waferSum=d.lots.reduce(function(s,l){return s+l[3];},0);
    checks.push([mpsSum === waferSum && d.lots.every(function(l){return Number.isInteger(l[3])&&l[3]>=1&&l[3]<=25;}), 'MPS 与 Lot wafer 合计一致：' + num(mpsSum) + ' / ' + num(waferSum) + ' wafer，拆为 ' + num(d.lots.length) + ' Lot（每 Lot 最多25片）']);
    checks.push([Object.keys(d.flows).length === (d.mps || []).length, '工艺覆盖：' + Object.keys(d.flows).length + ' 个 PROD / ' + (d.mps || []).length + ' 个 PPN']);
    var flowRows = Object.keys(d.flows).reduce(function (a, p) { return a + d.flows[p].length; }, 0);
    checks.push([flowRows > 0, '工艺记录：' + flowRows + ' 行' + (r ? '，展开 ' + num(r.metrics.cpOps) + ' 道 CP 工序' : '，尚无排产结果')]);
    var noCards = (d.recipes || []).filter(function (x) { return !x.cards.length; }).length;
    checks.push([noCards === 0, 'Recipe 与针卡：' + (d.recipes || []).length + ' 个 Recipe，' + noCards + ' 个无候选针卡']);
    var cards = {};
    (d.recipes || []).forEach(function (x) { x.cards.forEach(function (c) { cards[c] = 1; }); });
    checks.push([true, '针卡类型：' + Object.keys(cards).length + ' 种，实物数量充足，可跨机并行']);
    if (r) {
      checks.push([r.metrics.pmInWindow === r.metrics.pmCount, 'PM 停机窗口：' + r.metrics.pmInWindow + ' / ' + r.metrics.pmCount + ' 次在允许窗口内' + (r.metrics.pmMergedRows ? '（原始维护任务 ' + r.metrics.pmInputCount + ' 条，重叠停机已合并，任务均保留）' : '')]);
      var maxBatchLots = Math.max.apply(null, r.batches.map(function (b) { return b.lots.length; }).concat([0]));
      checks.push([maxBatchLots <= 4, 'BAKE 组批：最大 ' + maxBatchLots + ' lots（每腔上限 4 Lot，无 wafer 限制）']);
      checks.push([r.metrics.borrowCancelRate <= 0.1, '借机取消率：' + A.pct(r.metrics.borrowCancelRate, 1) + '（阈值 10%）']);
      checks.push([r.metrics.completedWafers >= mpsSum, '算法完成 / Excel MPS：' + num(r.metrics.completedWafers) + ' / ' + num(mpsSum) + ' wafer；在制另列 ' + num(r.metrics.wipLots || 0) + ' lots']);
    }
    return (!r ? '<p class="config-hint">尚无排产结果，以下仅核验输入数据。</p>' : '') + checks.map(function (c) {
      return '<div class="check-item"><span class="check-icon ' + (c[0] ? '' : 'warn') + '">' + (c[0] ? '✓' : '!') + '</span><span>' + esc(c[1]) + '</span></div>';
    }).join('');
  }

  document.addEventListener('click', function (event) {
    if (event.target.closest('#inputs-import')) A.openExcelImport();
    var page = event.target.closest('[data-inputs-page]');
    if (page) { A.state.inputs.page = Number(page.getAttribute('data-inputs-page')); renderInputsTable(); }
  });
  document.addEventListener('change', function (event) {
    if (event.target.id === 'inputs-page') { A.state.inputs.page = Number(event.target.value); renderInputsTable(); }
  });

  A.renderInputs = renderInputs;
  A.renderInputsTable = renderInputsTable;
  A.selectInputSheet = function (name) {
    A.state.inputs.sheet = name; A.state.inputs.q = ''; A.state.inputs.page = 1;
    A.$$('.sheet-btn').forEach(function (button) { button.classList.toggle('active', button.getAttribute('data-inputs-sheet') === name); });
    renderInputsTable();
  };
})();
