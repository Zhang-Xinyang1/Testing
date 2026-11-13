/* Configuration is persisted separately from the read-only reference workbook. */
(function () {
  'use strict';
  var A = window.TSApp, esc = A.esc, busy = false, pendingUpload = null;
  var schemas = {
    eqp: [['id', 'EQPID'], ['type', 'TYPE'], ['card', 'Current ProbeCard'], ['recipe', 'Current Recipe'], ['status', 'status', 'status']],
    borrow: [['eqp', '机台', 'machine'], ['date', '借机日期', 'date'], ['hours', '连续小时', 'number']],
    pm: [['eqp', '固定机台', 'resource'], ['early', '最早日期', 'date'], ['due', '期望日期', 'date'], ['over', '最迟完成日期', 'date'], ['hours', '连续小时', 'number'], ['start', '指定开始（可留空）', 'datetime-local']],
    wip: [['lotId', '在制 LotID'], ['lotKey', '行键（同名 Lot 必填）'], ['eqp', '占用机台（等待可空）', 'resource'], ['chamber', 'BAKE 腔体（CP 留空）'], ['card', '占用针卡（BAKE 留空）'], ['start', '占用开始', 'datetime-local'], ['end', '预计释放', 'datetime-local']]
  };
  function localTime(value) {
    if (!value) return '';
    var ms = A.SCHED.parseDate(value);
    return isFinite(ms) ? new Date(ms + 8 * A.HOUR).toISOString().slice(0, 16) : '';
  }
  function iso(value) { return value ? value + ':00+08:00' : ''; }
  function input(id, label, value, type) {
    return '<label class="config-field">' + label + '<input class="input" id="cfg-' + id + '" type="' + (type || 'text') + '" value="' + esc(value == null ? '' : value) + '"></label>';
  }
  function jsonBox(key, label, value) {
    return '<details class="config-details"><summary>' + label + '</summary><textarea class="input config-json" id="cfg-' + key + '">' + esc(JSON.stringify(value, null, 2)) + '</textarea></details>';
  }
  function resources(bake) {
    var d = A.state.data, values = d.eqp.filter(function(m){return bake || !/OVEN/i.test(m.type);}).map(function(m){return m.id;});
    return values;
  }
  function rowHTML(key, row) {
    return '<tr>' + schemas[key].map(function (col) {
      var field = col[0], type = col[2] || 'text', value = row[field] == null ? '' : row[field];
      if (type === 'datetime-local') value = localTime(value);
      var attrs = ' data-field="' + field + '" aria-label="' + esc(col[1]) + '"';
      if (['status', 'machine', 'resource'].indexOf(type) >= 0) {
        var values = type === 'status' ? ['', 'RUN', 'LOST'] : [''].concat(resources(type === 'resource'));
        if (values.indexOf(value) < 0) values.push(value);
        return '<td><select class="select"' + attrs + '>' + values.map(function (v) { return '<option value="' + esc(v) + '"' + (v === value ? ' selected' : '') + '>' + esc(v || '—') + '</option>'; }).join('') + '</select></td>';
      }
      return '<td><input class="input"' + attrs + ' type="' + type + '"' + (type === 'number' ? ' min="0.01" step="any"' : '') + ' value="' + esc(value) + '"></td>';
    }).join('') + '<td><button class="btn ghost small" data-cfg-delete="' + key + '">删除</button></td></tr>';
  }
  function table(key, label, rows) {
    return '<details class="config-details"><summary>' + label + ' · ' + rows.length + ' 条</summary><div class="table-scroll config-editor"><table class="data-table" id="cfg-table-' + key + '"><thead><tr>' + schemas[key].map(function (col) { return '<th>' + col[1] + '</th>'; }).join('') + '<th>操作</th></tr></thead><tbody>' + rows.map(function (row) { return rowHTML(key, row); }).join('') + '</tbody></table></div><button class="btn ghost small" data-cfg-new="' + key + '">增加一行</button></details>';
  }
  A.configurationHTML = function () {
    if (A.state.mode !== 'server') return '<div class="card production-config"><div class="card-head"><div><h3>Excel 导入</h3><p>上传只读副本后保存并自动重排，不修改原文件。</p></div></div><div class="card-body">' +
      '<label class="config-field">Excel 导入（.xlsx）<input class="input" id="cfg-file" type="file" accept=".xlsx" disabled><span class="config-hint">当前为内置算例静态模式，无法上传。请启动后端服务（<code>python3 run.py</code>）后访问 <code>http://127.0.0.1:8080</code>。</span></label>' +
      '<div class="note-card">当前页面未连接后端，仅展示内置算例；导入、保存和重新排产不可用。</div></div></div>';
    var c = A.state.config, d = A.state.data, bake = d.bakeResources || {machines:7, chambers:4};
    var selectedPath = pendingUpload ? pendingUpload.path : c.dataSource.path;
    var selectedName = pendingUpload ? pendingUpload.name : c.dataSource.displayName || selectedPath.split(/[\\/]/).pop();
    var borrow = d.borrow.map(function (b) { return Object.assign({}, b, {start:b.start || b.generatedAt || b.date + 'T08:00:00+08:00'}); });
    return '<div class="card production-config"><div class="card-head"><div><h3>生产配置</h3><p>保存后自动重排，不修改 Excel。</p></div></div><div class="card-body">' +
      '<div class="config-grid">' + '<label class="config-field">Excel 导入（.xlsx）<input class="input" id="cfg-file" type="file" accept=".xlsx"><span id="cfg-file-status">' + esc(selectedName) + '</span><span class="config-hint">' + (pendingUpload ? '新文件已上传，等待保存。' : '') + '选择新文件后点击下方“保存并排产”；如需沿用新文件内的机台与计划，再点击“恢复参考配置”。</span><input id="cfg-path" type="hidden" value="' + esc(selectedPath) + '"></label>' + input('month', '计划月份', c.dataSource.month, 'month') + input('bake-machines', 'BAKE 台数', bake.machines, 'number') + input('bake-chambers', '每台 BAKE 腔体数', bake.chambers, 'number') + '</div>' +
      '<details class="config-details"><summary>Oracle 数据源 · 预留</summary><p>仅保存连接配置，当前不连接数据库。</p><div class="config-grid">' + input('oracle-host', '主机', (c.oracle || {}).host) + input('oracle-port', '端口', (c.oracle || {}).port || 1521, 'number') + input('oracle-service', 'Service Name', (c.oracle || {}).service) + input('oracle-user', '只读用户名', (c.oracle || {}).user) + input('oracle-env', '密码环境变量名', (c.oracle || {}).passwordEnv || 'ORACLE_PASSWORD') + '</div>' + jsonBox('oracle-views', '业务视图映射', (c.oracle || {}).views || {}) + '<button class="btn ghost" id="cfg-oracle-save">保存 Oracle 配置</button><div id="cfg-oracle-status" role="status"></div></details>' +
      jsonBox('source', 'Sheet 设置', {sheetMap:c.dataSource.sheetMap, headerRow:c.dataSource.headerRow}) + jsonBox('fields', '字段映射', c.fieldMap) +
      '<p class="config-hint">同一 TYPE 可有多台设备，EQPID 必须唯一。Platform 可用分号填写多个 TYPE，匹配时忽略大小写、空白和全半角格式差异；产品、工序、Recipe、针卡及机台编号的关联也忽略大小写；针卡列表支持逗号、分号和换行。</p>' +
      (d.meta.eqpSource === 'override' ? '<div class="note-card">当前使用手工机台配置，会覆盖 Excel 的 EQP 表。上传新文件后，请核对下方 TYPE；“恢复参考配置”会清空机台、PM、借机和在制的全部手工覆盖。</div>' : '') +
      table('eqp', 'CP 机台', d.eqp) + table('pm', 'PM 计划', d.pm) + table('borrow', '借机计划', borrow) +
      '<details class="config-details"><summary>在制填写说明</summary><p>在制不重排，保留当前占用。等待中 LotID 唯一时可只填 LotID，同名时还须填写行键（Lot 详情可查看）；重新导入后请核对行键；RUN 表示可用资格，资源日历保证任务互斥；非 RUN 不安排生产。</p></details>' + table('wip', '在制 Lot', d.wip || []) +
      '<div class="source-actions config-actions"><button class="btn primary" id="cfg-save">保存并排产</button><button class="btn ghost" id="cfg-excel">恢复参考配置</button></div>' +
      ((d.planIssues || []).length ? '<div class="note-card">当前计划冲突：' + d.planIssues.map(esc).join('<br>') + '</div>' : '') +
      '<div class="section-title"><h2>添加计划与推荐</h2><p>借机固定原机台推荐时间；PM 在维护窗口内推荐时间。</p></div>' +
      '<div class="config-grid"><label class="config-field">计划类别<select class="select" id="cfg-kind"><option value="borrow">借机</option><option value="pm">PM</option></select></label>' +
      '<label class="config-field">所选机台<select class="select" id="cfg-machine">' + resources(true).map(function (id) { return '<option>' + esc(id) + '</option>'; }).join('') + '</select></label>' +
      input('time', '借机日期', d.meta.month + '-01', 'date') + input('hours', '时长（小时）', 8, 'number') +
      input('early', 'PM 最早日期', d.meta.month + '-01', 'date') + input('due', 'PM 期望日期', d.meta.month + '-03', 'date') + input('over', 'PM 最迟完成日期', d.meta.month + '-05', 'date') + '</div>' +
      '<div class="source-actions config-actions"><button class="btn ghost" id="cfg-recommend">试排并推荐</button><button class="btn primary" id="cfg-add">添加计划并保存</button></div><div id="cfg-result" class="note-card" role="status">推荐后点击添加保存。</div></div></div>';
  };
  function draft() {
    var src = Object.assign({}, A.state.config.dataSource, JSON.parse(A.$('#cfg-source').value)), scheduling = {};
    src.path = pendingUpload ? pendingUpload.path : A.$('#cfg-path').value.trim();
    src.displayName = pendingUpload ? pendingUpload.name : A.$('#cfg-file-status').textContent;
    src.type = 'excel';
    src.month = A.$('#cfg-month').value;
    Object.keys(schemas).forEach(function (key) {
      scheduling[key] = A.$$('#cfg-table-' + key + ' tbody tr').map(function (tr) {
        var row = {};
        A.$$('[data-field]', tr).forEach(function (el) { var v = el.value.trim(); row[el.dataset.field] = el.type === 'number' ? Number(v) : el.type === 'datetime-local' ? iso(v) : v; });
        if (key === 'borrow') { row.date = row.date || (row.start || '').slice(0, 10); delete row.generatedAt; }
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
    if (!res.ok) throw new Error(body.error || '配置请求失败');
    return body;
  }
  async function save(update) {
    // Validate the candidate workbook and scheduling constraints before committing it.
    var preview = await request('api/preview', update);
    if (preview.data.planIssues && preview.data.planIssues.length) throw new Error(preview.data.planIssues.slice(0,5).join('；'));
    var options = JSON.parse(JSON.stringify(A.state.options));
    var result = await A.SCHED.run(preview.data, options);
    var body = await request('api/config', update);
    A.state.data = body.data; A.state.config = body.config;
    if (pendingUpload && body.config.dataSource.path === pendingUpload.path) pendingUpload = null;
    A.updateSourceCard();
    await A.runSchedule({input:preview.data, options:options, result:result});
  }
  function plan(eqp) {
    var kind = A.$('#cfg-kind').value, time = A.$('#cfg-time').value, hours = Number(A.$('#cfg-hours').value);
    if (!Number.isFinite(hours) || hours <= 0) throw new Error('请输入正数时长');
    if (kind === 'borrow') {
      if (!time || !A.state.data.eqp.some(function(m){return m.id===eqp && !/OVEN/i.test(m.type);})) throw new Error('借机请选择 CP 机台并填写开始时间');
      if (hours<0.6 || hours>24) throw new Error('借机时数需为 0.6–24 小时');
      return {eqp:eqp, date:time.slice(0,10), hours:hours};
    }
    return {eqp:eqp, early:A.$('#cfg-early').value, due:A.$('#cfg-due').value, over:A.$('#cfg-over').value, hours:hours};
  }
  function addedBorrow(result, count) { return result.borrows.find(function (b) { return b.id === 'BR-' + String(count).padStart(2, '0'); }); }
  function setBusy(value) {
    busy = value;
    A.$$('.production-config button, .production-config input, .production-config select, .production-config textarea').forEach(function (el) { el.disabled = value; });
  }
  document.addEventListener('change', async function (event) {
    if (event.target.id !== 'cfg-file' || busy) return;
    var file = event.target.files[0]; if (!file) return;
    if (!file.name.toLowerCase().endsWith('.xlsx') || file.size > 20 * 1024 * 1024) { A.toast('请选择不超过 20 MB 的 .xlsx 文件'); event.target.value = ''; return; }
    setBusy(true);
    try {
      var base = window.APP_BASE || '/'; if (!base.endsWith('/')) base += '/';
      var res = await fetch(base + 'api/uploads', {method:'POST', headers:{'Content-Type':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', 'X-Filename':encodeURIComponent(file.name)}, body:file});
      var result = await res.json(); if (!res.ok) throw new Error(result.error || '上传失败');
      pendingUpload = {path:result.path, name:result.name};
      var pathInput = A.$('#cfg-path'), status = A.$('#cfg-file-status'), output = A.$('#cfg-result');
      if (pathInput) pathInput.value = result.path;
      if (status) status.textContent = result.name;
      if (output) output.textContent = '已上传 ' + result.name + '。点击保存生效；如需沿用新文件中的机台和计划，请选择“恢复参考配置”。';
    } catch (err) { A.toast(err.message); event.target.value = ''; }
    finally { setBusy(false); }
  });
  document.addEventListener('click', async function (event) {
    var add = event.target.closest('[data-cfg-new]'), del = event.target.closest('[data-cfg-delete]');
    if (busy) return;
    if (add) { var key = add.dataset.cfgNew; A.$('#cfg-table-' + key + ' tbody').insertAdjacentHTML('beforeend', rowHTML(key, {})); return; }
    if (del) { del.closest('tr').remove(); return; }
    var id = event.target.id;
    if (['cfg-save', 'cfg-add', 'cfg-excel', 'cfg-recommend', 'cfg-oracle-save'].indexOf(id) < 0) return;
    try {
      var cfg = draft(), kind = A.$('#cfg-kind').value, eqp = A.$('#cfg-machine').value;
      var proposal = (id === 'cfg-add' || id === 'cfg-recommend') ? plan(eqp) : null;
      setBusy(true);
      if (id === 'cfg-oracle-save') {
        var oracle = {host:A.$('#cfg-oracle-host').value.trim(), port:Number(A.$('#cfg-oracle-port').value), service:A.$('#cfg-oracle-service').value.trim(), user:A.$('#cfg-oracle-user').value.trim(), passwordEnv:A.$('#cfg-oracle-env').value.trim(), views:JSON.parse(A.$('#cfg-oracle-views').value)};
        var saved = await request('api/oracle/config', oracle);
        A.state.config.oracle = saved.oracle;
        A.$('#cfg-oracle-status').textContent = '配置已保存；接口预留，尚未连接数据库。';
      }
      else if (id === 'cfg-excel') { cfg.scheduling = {}; await save(cfg); }
      else if (id === 'cfg-save') await save(cfg);
      else {
        var preview = await request('api/preview', cfg), data = preview.data;
        var output = A.$('#cfg-result');
        if (id === 'cfg-add') {
          data[kind].push(proposal);
          var test = await A.SCHED.run(data, A.state.options);

          cfg.scheduling[kind].push(proposal);
          await save(cfg);
        } else if (kind === 'pm') {
          data.pm.push(proposal);
          var result = await A.SCHED.run(data, A.state.options), pm = result.pmRecords[result.pmRecords.length - 1];
          output.textContent = '固定机台 ' + pm.eqp + '，推荐维护时间：' + A.fmtDateTime(pm.start) + ' → ' + A.fmtDateTime(pm.end) + '。点击“添加计划并保存”后生效。';
        } else {
          data.borrow.push(proposal);
          var trial=await A.SCHED.run(data,A.state.options);
          var request=trial.borrows.find(function(b){return b.req.eqp===proposal.eqp && b.req.date===proposal.date && b.req.hours===proposal.hours;});
          output.textContent=request && request.status!=='cancel' ? '固定机台 '+request.eqp+'，安排时间：'+A.fmtDateTime(request.start)+' → '+A.fmtDateTime(request.end)+'。保存后生效。' : request && request.suggestion ? '当天无法执行，原机台建议：'+A.fmtDateTime(request.suggestion.start)+' → '+A.fmtDateTime(request.suggestion.end)+'。建议尚未执行。' : '原机台不可用，保留未执行计划，不自动换机。';

        }
      }
    } catch (err) {
      A.toast(err.message);
      var status = A.$('#cfg-result'); if (status) status.textContent = '未保存：' + err.message;
    } finally { setBusy(false); }
  });
})();
