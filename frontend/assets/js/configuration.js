/* Configuration is persisted separately from the read-only reference workbook. */
(function () {
  'use strict';
  var A = window.TSApp, esc = A.esc, busy = false;
  var schemas = {
    eqp: [['id', 'EQPID'], ['type', 'TYPE'], ['card', 'Current ProbeCard'], ['recipe', 'Current Recipe'], ['status', 'status', 'status']],
    borrow: [['eqp', '机台', 'machine'], ['start', '借机开始时间（北京时间）', 'datetime-local'], ['hours', '连续小时', 'number']],
    pm: [['eqp', '固定机台', 'resource'], ['early', '最早日期', 'date'], ['due', '期望日期', 'date'], ['over', '最迟完成日期', 'date'], ['hours', '连续小时', 'number'], ['start', '指定开始（可留空）', 'datetime-local']],
    wip: [['lotId', '在制 LotID'], ['eqp', '占用机台（等待可空）', 'resource'], ['chamber', 'BAKE 腔体（CP 留空）'], ['card', '占用针卡（BAKE 留空）'], ['start', '占用开始', 'datetime-local'], ['end', '预计释放', 'datetime-local']]
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
    var d = A.state.data, values = d.eqp.map(function (m) { return m.id; });
    if (bake) for (var i = 1; i <= (d.bakeResources || {machines:7}).machines; i++) values.push('BAKE-' + String(i).padStart(2, '0'));
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
    if (A.state.mode !== 'server') return '<div class="note-card">启动服务端后可保存配置并自动重新排产。</div>';
    var c = A.state.config, d = A.state.data, bake = d.bakeResources || {machines:7, chambers:4};
    var borrow = d.borrow.map(function (b) { return Object.assign({}, b, {start:b.start || b.generatedAt || b.date + 'T08:00:00+08:00'}); });
    return '<div class="card production-config"><div class="card-head"><div><h3>生产配置</h3><p>保存后自动重排，不修改 Excel。</p></div></div><div class="card-body">' +
      '<div class="config-grid">' + '<label class="config-field">参考 Excel<input class="input" id="cfg-file" type="file" accept=".xlsx"><span id="cfg-file-status">' + esc(c.dataSource.displayName || c.dataSource.path.split(/[\\/]/).pop()) + '</span><input id="cfg-path" type="hidden" value="' + esc(c.dataSource.path) + '"></label>' + input('month', '计划月份', c.dataSource.month, 'month') + input('bake-machines', 'BAKE 台数', bake.machines, 'number') + input('bake-chambers', '每台 BAKE 腔体数', bake.chambers, 'number') + '</div>' +
      '<details class="config-details"><summary>Oracle 数据源 · 预留</summary><p>仅保存连接配置，当前不连接数据库。</p><div class="config-grid">' + input('oracle-host', '主机', (c.oracle || {}).host) + input('oracle-port', '端口', (c.oracle || {}).port || 1521, 'number') + input('oracle-service', 'Service Name', (c.oracle || {}).service) + input('oracle-user', '只读用户名', (c.oracle || {}).user) + input('oracle-env', '密码环境变量名', (c.oracle || {}).passwordEnv || 'ORACLE_PASSWORD') + '</div>' + jsonBox('oracle-views', '业务视图映射', (c.oracle || {}).views || {}) + '<button class="btn ghost" id="cfg-oracle-save">保存 Oracle 配置</button><div id="cfg-oracle-status" role="status"></div></details>' +
      jsonBox('source', 'Sheet 设置', {sheetMap:c.dataSource.sheetMap, headerRow:c.dataSource.headerRow}) + jsonBox('fields', '字段映射', c.fieldMap) +
      jsonBox('platformType', 'Platform / TYPE 映射', d.platformType) + jsonBox('cardType', '针卡 TYPE 映射', d.cardType) +
      table('eqp', 'CP 机台', d.eqp) + table('pm', 'PM 计划', d.pm) + table('borrow', '借机计划', borrow) +
      '<details class="config-details"><summary>在制填写说明</summary><p>在制不重排，保留当前占用。等待中只填 LotID；RUN 缺少快照时暂停分配；LOST 不可用。</p></details>' + table('wip', '在制 Lot', d.wip || []) +
      '<div class="source-actions config-actions"><button class="btn primary" id="cfg-save">保存并排产</button><button class="btn ghost" id="cfg-excel">恢复参考配置</button></div>' +
      ((d.planIssues || []).length ? '<div class="note-card">当前计划冲突：' + d.planIssues.map(esc).join('<br>') + '</div>' : '') +
      '<div class="section-title"><h2>添加计划与推荐</h2><p>借机推荐同类型机台；PM 推荐维护时间。</p></div>' +
      '<div class="config-grid"><label class="config-field">计划类别<select class="select" id="cfg-kind"><option value="borrow">借机</option><option value="pm">PM</option></select></label>' +
      '<label class="config-field">所选机台<select class="select" id="cfg-machine">' + resources(true).map(function (id) { return '<option>' + esc(id) + '</option>'; }).join('') + '</select></label>' +
      input('time', '借机开始时间', d.meta.month + '-01T08:00', 'datetime-local') + input('hours', '时长（小时）', 8, 'number') +
      input('early', 'PM 最早日期', d.meta.month + '-01', 'date') + input('due', 'PM 期望日期', d.meta.month + '-03', 'date') + input('over', 'PM 最迟完成日期', d.meta.month + '-05', 'date') + '</div>' +
      '<div class="source-actions config-actions"><button class="btn ghost" id="cfg-recommend">试排并推荐</button><button class="btn primary" id="cfg-add">添加计划并保存</button></div><div id="cfg-result" class="note-card" role="status">推荐后点击添加保存。</div></div></div>';
  };
  function draft() {
    var src = Object.assign({}, A.state.config.dataSource, JSON.parse(A.$('#cfg-source').value)), scheduling = {};
    src.path = A.$('#cfg-path').value.trim();
    src.displayName = A.$('#cfg-file-status').textContent;
    src.type = 'excel';
    src.month = A.$('#cfg-month').value;
    Object.keys(schemas).forEach(function (key) {
      scheduling[key] = A.$$('#cfg-table-' + key + ' tbody tr').map(function (tr) {
        var row = {};
        A.$$('[data-field]', tr).forEach(function (el) { var v = el.value.trim(); row[el.dataset.field] = el.type === 'number' ? Number(v) : el.type === 'datetime-local' ? iso(v) : v; });
        if (key === 'borrow') { row.date = row.start.slice(0, 10); row.generatedAt = row.start; }
        return row;
      });
    });
    scheduling.bakeResources = {machines:Number(A.$('#cfg-bake-machines').value), chambers:Number(A.$('#cfg-bake-chambers').value)};
    ['platformType', 'cardType'].forEach(function (key) { scheduling[key] = JSON.parse(A.$('#cfg-' + key).value); });
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
    A.SCHED.run(preview.data, A.state.options);
    var body = await request('api/config', update);
    A.state.data = body.data; A.state.config = body.config;
    A.updateSourceCard(); A.runSchedule();
  }
  function plan(eqp) {
    var kind = A.$('#cfg-kind').value, time = A.$('#cfg-time').value, hours = Number(A.$('#cfg-hours').value);
    if (!Number.isFinite(hours) || hours <= 0) throw new Error('请输入正数时长');
    if (kind === 'borrow') {
      if (!time || eqp.indexOf('BAKE') === 0) throw new Error('借机请选择 CP 机台并填写开始时间');
      return {eqp:eqp, date:time.slice(0,10), start:iso(time), generatedAt:iso(time), hours:hours};
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
    var status = A.$('#cfg-file-status');
    if (!file.name.toLowerCase().endsWith('.xlsx') || file.size > 20 * 1024 * 1024) { A.toast('请选择不超过 20 MB 的 .xlsx 文件'); event.target.value = ''; return; }
    setBusy(true);
    try {
      var base = window.APP_BASE || '/'; if (!base.endsWith('/')) base += '/';
      var res = await fetch(base + 'api/uploads', {method:'POST', headers:{'Content-Type':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', 'X-Filename':encodeURIComponent(file.name)}, body:file});
      var result = await res.json(); if (!res.ok) throw new Error(result.error || '上传失败');
      A.$('#cfg-path').value = result.path;
      status.textContent = result.name;
      A.$('#cfg-result').textContent = '已上传 ' + result.name + '。点击保存生效；如需沿用新文件中的机台和计划，请选择“恢复参考配置”。';
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
          var test = A.SCHED.run(data, A.state.options);
          if (kind === 'borrow' && addedBorrow(test, data.borrow.length).status === 'cancel') throw new Error('借机时间与 PM、在制占用或其他借机冲突，请先推荐其他机台');
          cfg.scheduling[kind].push(proposal);
          await save(cfg);
        } else if (kind === 'pm') {
          data.pm.push(proposal);
          var result = A.SCHED.run(data, A.state.options), pm = result.pmRecords[result.pmRecords.length - 1];
          output.textContent = '固定机台 ' + pm.eqp + '，推荐维护时间：' + A.fmtDateTime(pm.start) + ' → ' + A.fmtDateTime(pm.end) + '。点击“添加计划并保存”后生效。';
        } else {
          var selected = data.eqp.find(function (m) { return m.id === eqp; });
          if (!selected) throw new Error('请选择 CP 机台');
          output.textContent = '正在逐台试排…';
          var scores = [], candidates = data.eqp.filter(function (m) { return m.type === selected.type && m.status !== 'LOST'; });
          for (var machine of candidates) {
            await new Promise(function (resolve) { setTimeout(resolve, 20); });
            var candidate = JSON.parse(JSON.stringify(data));
            candidate.borrow.push(Object.assign({}, proposal, {eqp:machine.id}));
            try {
              var trial = A.SCHED.run(candidate, A.state.options);
              if (addedBorrow(trial, candidate.borrow.length).status === 'cancel') continue;
              scores.push({id:machine.id, done:trial.metrics.completedMonth, weekly:trial.metrics.onTimeRate, setups:trial.metrics.setups});
            } catch (err) { /* An infeasible resource calendar cannot be recommended. */ }
          }
          scores.sort(function (a,b) { return b.done-a.done || b.weekly-a.weekly || a.setups-b.setups || a.id.localeCompare(b.id); });
          output.textContent = scores.length ? scores.slice(0,5).map(function (s,i) { return (i ? '候选 ' : '推荐 ') + s.id + '：算法月完成 ' + s.done + ' lot，周按期率 ' + A.pct(s.weekly) + '，换卡 ' + s.setups + ' 次'; }).join('；') + '。借机按原时间执行，保存后生效。' : '同 TYPE 机台在该时段均不可借用，请调整时间。';
          if (scores.length) A.$('#cfg-machine').value = scores[0].id;
        }
      }
    } catch (err) {
      A.toast(err.message);
      var status = A.$('#cfg-result'); if (status) status.textContent = '未保存：' + err.message;
    } finally { setBusy(false); }
  });
})();
