/* Configuration is persisted separately from the read-only reference workbook. */
(function () {
  'use strict';
  var A = window.TSApp, esc = A.esc, busy = false, pendingUpload = null;
  var dirty = false, draftState = null, skipCapture = false;
  var auxiliary = {oracle:false, plan:false};
  function fieldGroup(id) {
    if ((id || '').indexOf('cfg-oracle-') === 0) return 'oracle';
    return ['cfg-kind','cfg-machine','cfg-target-kind','cfg-time','cfg-hours','cfg-early','cfg-due','cfg-over'].indexOf(id) >= 0 ? 'plan' : 'main';
  }
  var schemas = {
    eqp: [['id', 'EQPID'], ['type', 'TYPE'], ['proberId', 'proberID'], ['card', 'Current ProbeCard'], ['recipe', 'Current Recipe'], ['status', 'status', 'status']],
    borrow: [['eqp', '机台', 'machine'], ['date', '借机日期', 'date'], ['hours', '连续小时', 'number']],
    pm: [['eqp', '维护对象（EQPID / proberID）', 'maintenance'], ['targetKind', '对象类型', 'target-kind'], ['early', '最早日期', 'date'], ['due', '期望日期', 'date'], ['over', '最迟完成日期', 'date'], ['hours', '连续小时', 'number'], ['start', '指定开始（可留空）', 'datetime-local']],
    wip: [['lotId', '在制 LotID'], ['lotKey', '行键（同名 Lot 必填）'], ['eqp', '占用机台（等待可空）', 'resource'], ['chamber', 'BAKE 腔体（CP 留空）'], ['card', '占用针卡（BAKE 留空）'], ['start', '占用开始', 'datetime-local'], ['end', '预计释放', 'datetime-local']]
  };
  A.configurationBusy = function () { return busy; };
  A.hasConfigurationChanges = function () { return dirty || auxiliary.oracle || auxiliary.plan || !!pendingUpload; };
  function updateDraftStatus() {
    var el = A.$('#cfg-draft-status');
    if (el) el.textContent = A.hasConfigurationChanges() ? '有未保存修改：切换页面会保留草稿。' + (dirty || pendingUpload ? '生产配置可“仅保存”或“保存并排产”。' : '') + (auxiliary.oracle ? 'Oracle 项请单独保存。' : '') + (auxiliary.plan ? '新增计划请点击“添加计划并保存”。' : '') : '配置已保存。';
  }
  function markDirty(event) {
    if (busy) return;
    var target = event && event.target;
    if (target && (!target.closest || !target.closest('.production-config') || target.type === 'file')) return;
    var table = target && target.closest('[id^="cfg-table-"]');
    if (table) markManual(table.id.slice('cfg-table-'.length));
    var group = fieldGroup(target && target.id);
    if (group === 'main') dirty = true;
    else auxiliary[group] = true;
    updateDraftStatus();
  }
  // Keep raw form values, including incomplete JSON, separate from validated input.
  // Capture only before rebuilding the view, rather than copying large tables on each keypress.
  A.captureConfigurationDraft = function () {
    if (skipCapture) { skipCapture = false; return; }
    var root = A.$('.production-config');
    if (!A.hasConfigurationChanges() || !root) return;
    draftState = {fields:[], tables:{}, opened:[]};
    A.$$('input[id], select[id], textarea[id]', root).forEach(function (el) {
      if (el.type !== 'file') draftState.fields.push({id:el.id, value:el.value, checked:el.checked});
    });
    Object.keys(schemas).forEach(function (key) {
      draftState.tables[key] = A.$$('#cfg-table-' + key + ' tbody tr').map(function (tr) {
        return {original:tr.dataset.original, fields:A.$$('[data-field]', tr).map(function (el) {
          return {field:el.dataset.field, value:el.value};
        })};
      });
    });
    draftState.opened = A.$$('details', root).map(function (el) { return el.open; });
  };
  A.markConfigurationDirty = markDirty;
  A.restoreConfigurationDraft = function () {
    if (draftState) {
      Object.keys(schemas).forEach(function (key) {
        var tbody = A.$('#cfg-table-' + key + ' tbody');
        if (!tbody) return;
        var rows = draftState.tables[key];
        if (!rows) return;
        tbody.innerHTML = rows.map(function (row) { return rowHTML(key, JSON.parse(row.original || '{}')); }).join('');
        A.$$('#cfg-table-' + key + ' tbody tr').forEach(function (tr, i) {
          A.$$('[data-field]', tr).forEach(function (el) {
            var field = rows[i].fields.find(function (f) { return f.field === el.dataset.field; });
            if (field) el.value = field.value;
          });
        });
      });
      draftState.fields.forEach(function (field) {
        var el = A.$('#' + field.id);
        if (el) { el.value = field.value; if (el.type === 'checkbox') el.checked = field.checked; }
      });
      A.$$('details', A.$('.production-config')).forEach(function (el, i) { el.open = !!draftState.opened[i]; });
    }
    updateDraftStatus();
    if (busy) setBusy(true);
  };
  document.addEventListener('input', markDirty);
  window.addEventListener('beforeunload', function (event) {
    if (A.hasConfigurationChanges() || busy) { event.preventDefault(); event.returnValue = ''; }
  });
  function localTime(value) {
    if (!value) return '';
    var ms = A.SCHED.parseDate(value);
    return isFinite(ms) ? new Date(ms + 8 * A.HOUR).toISOString().slice(0, -1) : '';
  }
  function iso(value) { return value ? value + (value.length === 16 ? ':00' : '') + '+08:00' : ''; }
  function configuredData() {
    if (A.state.data) return A.state.data;
    var c = A.state.config, s = c.scheduling || {};
    return {meta:{month:c.dataSource.month}, eqp:s.eqp || [], pm:s.pm || [], borrow:s.borrow || [], wip:s.wip || [], bakeResources:s.bakeResources};
  }
  function manual(key) { return Object.prototype.hasOwnProperty.call(A.state.config.scheduling || {}, key); }
  function markManual(key) { var el = A.$('#cfg-override-' + key); if (el) el.checked = true; }
  function input(id, label, value, type) {
    return '<label class="config-field">' + label + '<input class="input" id="cfg-' + id + '" type="' + (type || 'text') + '" value="' + esc(value == null ? '' : value) + '"></label>';
  }
  function jsonBox(key, label, value) {
    return '<details class="config-details" data-config-part="' + (key === 'source' || key === 'fields' ? 'mapping' : 'advanced') + '"><summary>高级 JSON · ' + label + '</summary><textarea class="input config-json" id="cfg-' + key + '">' + esc(JSON.stringify(value, null, 2)) + '</textarea></details>';
  }
  function resources(bake, probers) {
    var d = configuredData(), values = d.eqp.filter(function(m){return bake || !/OVEN/i.test(m.type);}).map(function(m){return m.id;});
    if (probers) d.eqp.forEach(function (m) { if (m.proberId && !/OVEN/i.test(m.type)) values.push(m.proberId); });
    return Array.from(new Set(values));
  }
  function rowHTML(key, row) {
    return '<tr data-original="' + esc(JSON.stringify(row)) + '">' + schemas[key].map(function (col) {
      var field = col[0], type = col[2] || 'text', value = row[field] == null ? '' : row[field];
      if (type === 'datetime-local' || type === 'date') {
        var ms = A.SCHED.parseDate(value);
        if (value && !isFinite(ms)) type = 'text'; // Keep invalid input visible for repair.
        else value = value ? (type === 'date' ? A.SCHED.dayKey(ms) : localTime(value)) : '';
      }
      var attrs = ' data-field="' + field + '" aria-label="' + esc(col[1]) + '"';
      if (['status', 'machine', 'resource', 'maintenance', 'target-kind'].indexOf(type) >= 0) {
        var values = type === 'status' ? ['', 'RUN', 'LOST'] : type === 'target-kind' ? ['', 'EQP', 'PROBER'] : [''].concat(resources(type !== 'machine', type === 'maintenance'));
        if (values.indexOf(value) < 0) values.push(value);
        return '<td><select class="select"' + attrs + '>' + values.map(function (v) { return '<option value="' + esc(v) + '"' + (v === value ? ' selected' : '') + '>' + esc(v || (type === 'target-kind' ? '自动识别' : '—')) + '</option>'; }).join('') + '</select></td>';
      }
      return '<td><input class="input"' + attrs + ' type="' + type + '"' + (type === 'number' ? ' min="0.01" step="any"' : type === 'datetime-local' ? ' step="any"' : '') + ' value="' + esc(value) + '"></td>';
    }).join('') + '<td><button class="btn ghost small" data-cfg-delete="' + key + '">删除</button></td></tr>';
  }
  function table(key, label, rows) {
    return '<details class="config-details" data-config-part="' + (key === 'eqp' ? 'resources' : 'plans') + '"><summary>' + label + ' · ' + rows.length + ' 条 · ' + (manual(key) ? '手工配置' : 'Excel') + '</summary>' +
      '<label><input type="checkbox" id="cfg-override-' + key + '"' + (manual(key) ? ' checked' : '') + '> 手工配置覆盖 Excel</label><p class="config-hint">编辑下表会自动勾选。取消勾选后，保存时重新读取 Excel；当前表格仍为保存前预览。</p>' +
      '<div class="table-scroll config-editor"><table class="data-table" id="cfg-table-' + key + '"><thead><tr>' + schemas[key].map(function (col) { return '<th>' + col[1] + '</th>'; }).join('') + '<th>操作</th></tr></thead><tbody>' + rows.map(function (row) { return rowHTML(key, row); }).join('') + '</tbody></table></div><button class="btn ghost small" data-cfg-new="' + key + '">增加一行</button></details>';
  }
  A.configurationHTML = function () {
    if (A.state.mode !== 'server') return '<div class="card production-config"><div class="card-head"><div><h3>Excel 导入</h3><p>上传只读副本后保存并自动重排，不修改原文件。</p></div></div><div class="card-body">' +
      '<label class="config-field">Excel 导入（.xlsx）<input class="input" id="cfg-file" type="file" accept=".xlsx" disabled><span class="config-hint">当前为内置算例静态模式，无法上传。请启动后端服务（<code>python3 run.py</code>）后访问 <code>http://127.0.0.1:8080</code>。</span></label>' +
      '<div class="note-card">当前页面未连接后端，仅展示内置算例；导入、保存和重新排产不可用。</div></div></div>';
    var c = A.state.config, d = configuredData(), bake = d.bakeResources || {machines:'', chambers:''};
    var selectedPath = pendingUpload ? pendingUpload.path : c.dataSource.path;
    var selectedName = pendingUpload ? pendingUpload.name : c.dataSource.displayName || selectedPath.split(/[\\/]/).pop();
    var borrow = d.borrow;
    return '<div class="card production-config"><div class="card-head"><div><h3>生产配置</h3><p>先核对数据，可仅保存，也可保存并排产；原 Excel 保持只读。</p></div></div><div class="card-body">' +
      '<div class="config-grid" data-config-part="source">' + '<label class="config-field">Excel 导入（.xlsx）<input class="input" id="cfg-file" type="file" accept=".xlsx"><span id="cfg-file-status">' + esc(selectedName) + '</span><span class="config-hint">' + (pendingUpload ? '新文件已上传，等待保存。' : '') + '选择新文件后点击下方“仅保存”或“保存并排产”；如需沿用新文件内的机台与计划，再点击“恢复 Excel 设备与计划”。</span><input id="cfg-path" type="hidden" value="' + esc(selectedPath) + '"></label>' + input('month', '计划月份', c.dataSource.month, 'month') + input('bake-machines', 'BAKE 台数', bake.machines, 'number') + input('bake-chambers', '每台 BAKE 腔体数', bake.chambers, 'number') + '</div>' +
      '<details class="config-details" data-config-part="advanced"><summary>Oracle 数据源 · 预留</summary><p>仅保存连接配置，当前不连接数据库。</p><div class="config-grid">' + input('oracle-host', '主机', (c.oracle || {}).host) + input('oracle-port', '端口', (c.oracle || {}).port || 1521, 'number') + input('oracle-service', 'Service Name', (c.oracle || {}).service) + input('oracle-user', '只读用户名', (c.oracle || {}).user) + input('oracle-env', '密码环境变量名', (c.oracle || {}).passwordEnv || 'ORACLE_PASSWORD') + '</div>' + jsonBox('oracle-views', '业务视图映射', (c.oracle || {}).views || {}) + '<button class="btn ghost" id="cfg-oracle-save">保存 Oracle 配置</button><div id="cfg-oracle-status" role="status"></div></details>' +
      jsonBox('source', 'Sheet 设置', {sheetMap:c.dataSource.sheetMap, headerRow:c.dataSource.headerRow}) + jsonBox('fields', '字段映射', c.fieldMap) +
      '<p class="config-hint" data-config-part="resources">BAKE 台数：EQP 中有 OVEN 时以设备行数为准；没有 OVEN 时使用上方配置台数。每台腔体数使用上方配置，请核对实际容量。</p>' +
      '<p class="config-hint">同一 TYPE 可有多台设备，EQPID 必须唯一。Platform 可用分号填写多个 TYPE，匹配时忽略大小写、空白和全半角格式差异；产品、工序、Recipe、针卡及机台编号的关联也忽略大小写；针卡列表支持逗号、分号和换行。</p>' +
      (d.meta.eqpSource === 'override' ? '<div class="note-card">当前使用手工机台配置，会覆盖 Excel 的 EQP 表。上传新文件后，请核对下方 TYPE；“恢复 Excel 设备与计划”会清空机台、PM、借机和在制的全部手工覆盖。</div>' : '') +
      table('eqp', '设备与 prober 对应关系', d.eqp) + '<p class="config-hint">PM 可填写 EQPID 或 proberID；两者组装为同一设备，任一部件维护都会整组停机。配对部件的指定时间重叠时，仅合并停机区间，两项维护任务均保留；不同时间分别停机。未指定时间仍按两项任务分别安排。编号同时匹配两类对象时请选择对象类型。</p>' + table('pm', 'PM 计划', d.pm) + table('borrow', '借机计划', borrow) +
      '<details class="config-details"><summary>在制填写说明</summary><p>在制不重排，保留当前占用。等待中 LotID 唯一时可只填 LotID，同名时还须填写行键（Lot 详情可查看）；重新导入后请核对行键；RUN 表示可用资格，资源日历保证任务互斥；非 RUN 不安排生产。</p></details>' + table('wip', '在制 Lot', d.wip || []) + (A.taskOverridesHTML ? A.taskOverridesHTML() : '') +
      '<div class="source-actions config-actions"><button class="btn ghost" id="cfg-validate">检查输入</button><button class="btn ghost" id="cfg-save-only">仅保存</button><button class="btn primary" id="cfg-save">保存并排产</button><button class="btn ghost" id="cfg-excel">恢复 Excel 设备与计划</button><button class="btn ghost" id="cfg-discard">放弃未保存修改</button></div><p id="cfg-draft-status" class="config-hint" role="status">' + (A.hasConfigurationChanges() ? '有未保存修改：切换页面会保留草稿，保存后才生效。' : '配置已保存。') + '</p>' +
      ((d.planIssues || []).length ? '<div class="note-card">当前计划冲突：' + d.planIssues.map(esc).join('<br>') + '</div>' : '') +
      '<div class="section-title" data-config-part="plans"><h2>添加计划与推荐</h2><p>借机固定原机台推荐时间；PM 在维护窗口内推荐时间。</p></div>' +
      '<div class="config-grid"><label class="config-field">计划类别<select class="select" id="cfg-kind"><option value="borrow">借机</option><option value="pm">PM</option></select></label>' +
      '<label class="config-field">设备／维护对象<select class="select" id="cfg-machine">' + resources(true, true).map(function (id) { return '<option>' + esc(id) + '</option>'; }).join('') + '</select></label>' +
      '<label class="config-field">PM 对象类型<select class="select" id="cfg-target-kind"><option value="">自动识别</option><option value="EQP">EQP</option><option value="PROBER">PROBER</option></select></label>' +
      input('time', '借机日期', d.meta.month + '-01', 'date') + input('hours', '时长（小时）', 8, 'number') +
      input('early', 'PM 最早日期', d.meta.month + '-01', 'date') + input('due', 'PM 期望日期', d.meta.month + '-03', 'date') + input('over', 'PM 最迟完成日期', d.meta.month + '-05', 'date') + '</div>' +
      '<div class="source-actions config-actions"><button class="btn ghost" id="cfg-recommend">试排并推荐</button><button class="btn primary" id="cfg-add">添加计划并保存</button></div><div id="cfg-result" class="note-card" role="status">推荐后点击添加保存。</div></div></div>';
  };
  function draft() {
    var src = Object.assign({}, A.state.config.dataSource, JSON.parse(A.$('#cfg-source').value));
    var scheduling = Object.assign({}, A.state.config.scheduling || {});
    src.path = pendingUpload ? pendingUpload.path : A.$('#cfg-path').value.trim();
    src.displayName = pendingUpload ? pendingUpload.name : A.$('#cfg-file-status').textContent;
    src.type = 'excel';
    src.month = A.$('#cfg-month').value;
    Object.keys(schemas).forEach(function (key) {
      var source = A.$('#cfg-override-' + key);
      if (!(source ? source.checked : manual(key))) { delete scheduling[key]; return; }
      scheduling[key] = A.$$('#cfg-table-' + key + ' tbody tr').map(function (tr) {
        var row = JSON.parse((tr.dataset || {}).original || '{}');
        A.$$('[data-field]', tr).forEach(function (el) {
          var field = el.dataset.field, v = el.value.trim(), previous = row[field];
          // Browsers normalize datetime-local strings; compare instants before replacing
          // an unchanged source value (including seconds and sub-millisecond precision).
          if (previous && v && el.type === 'datetime-local' && A.SCHED.parseDate(previous) === A.SCHED.parseDate(v)) return;
          if (previous && v && el.type === 'date' && A.SCHED.dayKey(A.SCHED.parseDate(previous)) === v) return;
          row[field] = el.type === 'number' ? Number(v) : el.type === 'datetime-local' ? iso(v) : v;
        });
        return row;
      });
    });
    scheduling.bakeResources = {machines:Number(A.$('#cfg-bake-machines').value), chambers:Number(A.$('#cfg-bake-chambers').value)};

    return { dataSource: src, fieldMap: JSON.parse(A.$('#cfg-fields').value), scheduling: scheduling };
  }
  async function request(path, update) {
    var base = window.APP_BASE || '/';
    if (!base.endsWith('/')) base += '/';
    var res = await fetch(base + path, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(update)});
    var body = await res.json();
    if (!res.ok) { var error = new Error(body.error || '配置请求失败'); error.issues = body.issues || []; throw error; }
    return body;
  }
  async function save(update, includesPlan, runAfterSave) {
    if (runAfterSave === undefined) runAfterSave = true;
    // Validate the candidate workbook and scheduling constraints before committing it.
    var preview = await request('api/preview', update);
    if (preview.data.planIssues && preview.data.planIssues.length) throw new Error(preview.data.planIssues.slice(0,5).join('；'));
    var options = JSON.parse(JSON.stringify(A.state.options));
    var result = runAfterSave ? await A.SCHED.run(preview.data, options) : null;
    var body = await request('api/config', update);
    A.state.data = body.data; A.state.config = body.config;
    A.state.loadError = null;
    A.captureConfigurationDraft();
    if (pendingUpload && body.config.dataSource.path === pendingUpload.path) pendingUpload = null;
    dirty = false;
    if (includesPlan) auxiliary.plan = false;
    // Main saves do not submit the separate Oracle form or an unadded proposal.
    if (draftState && (auxiliary.oracle || auxiliary.plan)) {
      draftState.tables = {};
      draftState.fields = draftState.fields.filter(function (f) { return auxiliary[fieldGroup(f.id)]; });
    } else draftState = null;
    skipCapture = true;
    A.updateSourceCard();
    if (runAfterSave) await A.runSchedule({input:preview.data, options:options, result:result});
    else {
      A.state.result = null;
      A.updateRunChip(); A.renderInputs();
      A.toast('配置已保存，尚未排产。需要结果时点击“重新排产”。');
    }
  }
  function plan(eqp) {
    var kind = A.$('#cfg-kind').value, time = A.$('#cfg-time').value, hours = Number(A.$('#cfg-hours').value);
    if (!Number.isFinite(hours) || hours <= 0) throw new Error('请输入正数时长');
    if (kind === 'borrow') {
      if (!time || !configuredData().eqp.some(function(m){return m.id===eqp && !/OVEN/i.test(m.type);})) throw new Error('借机请选择 CP 机台并填写开始时间');
      if (hours<0.6 || hours>24) throw new Error('借机时数需为 0.6–24 小时');
      return {eqp:eqp, date:time.slice(0,10), hours:hours};
    }
    return {eqp:eqp, targetKind:A.$('#cfg-target-kind').value, early:A.$('#cfg-early').value, due:A.$('#cfg-due').value, over:A.$('#cfg-over').value, hours:hours};
  }
  A.commitTaskOverrides = async function (items) {
    if (busy || A.hasConfigurationChanges()) throw new Error('请先保存或放弃配置草稿，再调整工序');
    if (A.state.historyId) throw new Error('请先返回当前工作');
    if (A.clearWorkflowErrors) A.clearWorkflowErrors();
    var cfg = JSON.parse(JSON.stringify(A.state.config));
    var update = {dataSource:cfg.dataSource, fieldMap:cfg.fieldMap, scheduling:cfg.scheduling || {}};
    update.scheduling.taskOverrides = items;
    setBusy(true);
    A.showLoading('正在校验手工调整', '试排通过后保存，冲突时保留原配置');
    try { await save(update); }
    finally { setBusy(false); A.hideLoading(); }
  };
  function setBusy(value) {
    busy = value;
    A.$$('.production-config button, .production-config input, .production-config select, .production-config textarea').forEach(function (el) { el.disabled = value; });
  }
  document.addEventListener('change', async function (event) {
    markDirty(event);
    if (event.target.id !== 'cfg-file' || busy) return;
    var file = event.target.files[0]; if (!file) return;
    if (!file.name.toLowerCase().endsWith('.xlsx') || file.size > 20 * 1024 * 1024) { A.toast('请选择不超过 20 MB 的 .xlsx 文件'); event.target.value = ''; return; }
    setBusy(true);
    try {
      var base = window.APP_BASE || '/'; if (!base.endsWith('/')) base += '/';
      var res = await fetch(base + 'api/uploads', {method:'POST', headers:{'Content-Type':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', 'X-Filename':encodeURIComponent(file.name)}, body:file});
      var result = await res.json(); if (!res.ok) throw new Error(result.error || '上传失败');
      pendingUpload = {path:result.path, name:result.name};
      dirty = true;
      updateDraftStatus();
      var pathInput = A.$('#cfg-path'), status = A.$('#cfg-file-status'), output = A.$('#cfg-result');
      if (pathInput) pathInput.value = result.path;
      if (status) status.textContent = result.name;
      if (output) output.textContent = '已上传 ' + result.name + '。点击保存生效；如需沿用新文件中的机台和计划，请选择“恢复 Excel 设备与计划”。';
    } catch (err) { A.toast(err.message); event.target.value = ''; }
    finally { setBusy(false); }
  });
  document.addEventListener('click', async function (event) {
    var add = event.target.closest('[data-cfg-new]'), del = event.target.closest('[data-cfg-delete]');
    if (busy) return;
    if (add) { var key = add.dataset.cfgNew; markManual(key); A.$('#cfg-table-' + key + ' tbody').insertAdjacentHTML('beforeend', rowHTML(key, {})); markDirty(); return; }
    if (del) { markManual(del.dataset.cfgDelete); del.closest('tr').remove(); markDirty(); return; }
    var id = event.target.id;
    if (id === 'cfg-discard') {
      dirty = false; draftState = null; pendingUpload = null; skipCapture = false;
      auxiliary = {oracle:false, plan:false};
      A.renderInputs(); A.toast('已放弃未保存修改，恢复当前已保存配置'); return;
    }
    if (['cfg-save', 'cfg-save-only', 'cfg-validate', 'cfg-add', 'cfg-excel', 'cfg-recommend', 'cfg-oracle-save'].indexOf(id) < 0) return;
    try {
      if (A.clearWorkflowErrors) A.clearWorkflowErrors();
      if (id === 'cfg-oracle-save') {
        setBusy(true);
        var oracle = {host:A.$('#cfg-oracle-host').value.trim(), port:Number(A.$('#cfg-oracle-port').value), service:A.$('#cfg-oracle-service').value.trim(), user:A.$('#cfg-oracle-user').value.trim(), passwordEnv:A.$('#cfg-oracle-env').value.trim(), views:JSON.parse(A.$('#cfg-oracle-views').value)};
        var saved = await request('api/oracle/config', oracle);
        A.state.config.oracle = saved.oracle;
        auxiliary.oracle = false;
        updateDraftStatus();
        A.$('#cfg-oracle-status').textContent = '配置已保存；接口预留，尚未连接数据库。';
        return;
      }
      var cfg = draft(), kind = A.$('#cfg-kind').value, eqp = A.$('#cfg-machine').value;
      var proposal = (id === 'cfg-add' || id === 'cfg-recommend') ? plan(eqp) : null;
      setBusy(true);
      if (id === 'cfg-excel') { Object.keys(schemas).forEach(function (key) { delete cfg.scheduling[key]; }); await save(cfg, false, false); }
      else if (id === 'cfg-save') await save(cfg);
      else if (id === 'cfg-save-only') await save(cfg, false, false);
      else if (id === 'cfg-validate') {
        var checked = await request('api/preview', cfg);
        if ((checked.data.planIssues || []).length) throw new Error(checked.data.planIssues.join('；'));
        A.$('#cfg-result').textContent = '输入校验通过，尚未保存或排产。实际可排性仍需运行排产确认。';
      }
      else {
        var preview = await request('api/preview', cfg), data = preview.data;
        var output = A.$('#cfg-result');
        if (id === 'cfg-add') {
          data[kind].push(proposal);
          cfg.scheduling[kind] = data[kind];
          await save(cfg, true);
        } else if (kind === 'pm') {
          data.pm.push(proposal);
          var result = await A.SCHED.run(data, A.state.options), pmId = 'PM-' + String(data.pm.length).padStart(2, '0');
          var pm = result.pmRecords.find(function (p) { return (p.sourceIds || [p.id]).indexOf(pmId) >= 0; });
          output.textContent = '维护对象 ' + (pm.sourcePlans || [{eqp:pm.eqp}]).map(function(p){return p.eqp;}).join('、') + '，停机 EQPID：' + (pm.affectedEqps || [pm.eqp]).join('、') + '，连带停机 Prober：' + ((pm.affectedProbers || []).join('、') || '—') + (pm.merged ? '，停机区间已合并，各项维护均保留' : '，单项维护、整组停机') + '，推荐维护时间：' + A.fmtDateTime(pm.start) + ' → ' + A.fmtDateTime(pm.end) + '。点击“添加计划并保存”后生效。';
        } else {
          data.borrow.push(proposal);
          var trial=await A.SCHED.run(data,A.state.options);
          var borrowRequest=trial.borrows.find(function(b){return b.req.eqp===proposal.eqp && b.req.date===proposal.date && b.req.hours===proposal.hours;});
          output.textContent=borrowRequest && borrowRequest.status!=='cancel' ? '固定机台 '+borrowRequest.eqp+'，安排时间：'+A.fmtDateTime(borrowRequest.start)+' → '+A.fmtDateTime(borrowRequest.end)+'。保存后生效。' : borrowRequest && borrowRequest.suggestion ? '当天无法执行，原机台建议：'+A.fmtDateTime(borrowRequest.suggestion.start)+' → '+A.fmtDateTime(borrowRequest.suggestion.end)+'。建议尚未执行。' : '原机台不可用，保留未执行计划，不自动换机。';

        }
      }
    } catch (err) {
      A.toast(err.message);
      if (A.showWorkflowError) A.showWorkflowError(err);
      var status = A.$('#cfg-result'); if (status) status.textContent = '未保存：' + err.message;
    } finally { setBusy(false); }
  });
})();
