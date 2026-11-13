/* Diagnostics stay on the deployment's own server; no external transmission. */
(function () {
  'use strict';
  var A = window.TSApp, esc = A.esc;
  A.TITLES.diagnostics = ['排产诊断', '问题编号 · 内网明细 · 前后对比'];
  var reports = [], failure = '', trigger = '首次加载';
  function endpoint() { var base = window.APP_BASE || '/'; return base.replace(/\/?$/, '/') + 'api/runs'; }
  function pick(object, keys) {
    var out = {}; keys.forEach(function (key) { out[key] = object[key]; }); return out;
  }
  A.recordDiagnostics = async function (result, error, snapshot) {
    if (A.state.mode !== 'server') { failure = '静态模式不保存诊断，请使用 Python 服务地址。'; return; }
    var input = JSON.parse(JSON.stringify(snapshot ? snapshot.input : A.state.data));
    var options = snapshot ? snapshot.options : A.state.options;
    // Operational timestamps do not alter the scheduling input identity.
    delete input.meta.loadedAt; delete input.meta.configPath;
    var output = result ? {
      cpTasks: result.cpTasks.map(function (t) { return pick(t, ['lotId','lotKey','prod','step','recipe','machine','card','start','end','blockStart','opIndex']); }),
      batches: result.batches.map(function (b) { return {id:b.id, chamber:b.chamber, lots:b.lots.map(function (l) {return {id:l.id, key:l.key};}), wafers:b.wafers, start:b.start,end:b.end,processStart:b.processStart,processEnd:b.processEnd,recipe:b.recipe}; }),
      lots: result.lots.map(function (l) {return pick(l,['id','key','prod','wafer','complete','routeTasks','unscheduledReason']);}),
      borrows: result.borrows, pmRecords: result.pmRecords, runMs: result.runMs,
      optimization: result.optimization, trials: result.trials
    } : null;
    try {
      var response = await fetch(endpoint(), {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({input:input, options:options,result:output,error:error ? error.message : null,trigger:trigger})});
      var body = await response.json(); if (!response.ok) throw new Error(body.error || '记录失败');
      reports.unshift(body.report); reports = reports.slice(0,20); failure = '';
    } catch (err) { failure = '诊断未保存：' + err.message; }
    trigger = '手动重排';
  };
  A.renderDiagnostics = function () {
    var latest = reports[0], html = '<div class="source-hero"><div><div class="source-title">排产诊断</div><div class="source-sub">明细仅在内网查看，不提供导出。</div></div><button class="btn ghost" id="diag-refresh">刷新记录</button></div>';
    if (failure) html += '<div class="note-card">' + esc(failure) + '</div>';
    if (!latest) html += '<div class="note-card">暂无记录。完成一次排产后自动检查。</div>';
    else {
      html += '<div class="card production-config"><div class="card-head"><h3>' + esc(latest.version) + ' · ' + esc(latest.time.slice(0,19).replace('T',' ')) + '</h3></div><div class="card-body"><p>运行编号：' + esc(latest.id) + '</p><div class="note-card">简短反馈：' + esc(latest.feedback) + '</div>';
      if (latest.comparison) { var c=latest.comparison; html += '<p>与上次比较：' + (c.sameInput ? '输入及策略相同' : '输入或策略已变化') + (c.completedDelta === null ? '' : ' · 完成 wafer 量 ' + (c.completedDelta>=0?'+':'') + c.completedDelta + ' wafer') + (c.newCodes.length ? ' · 新增 '+esc(c.newCodes.join('、')) : '') + '</p>'; }
      html += '<p class="config-hint">仅检查列出的规则，不代表全部业务约束通过。“待核查”不是已确定的根因。</p>';
      if (!latest.findings.length) html += '<div class="note-card">未发现已检查项异常。</div>';
      latest.findings.forEach(function (f) {
        html += '<details class="config-details"><summary>' + esc(f.code) + ' · ' + esc(f.title) + ' · ' + f.count + ' 项 · ' + esc(f.certainty) + '</summary><p>' + esc(f.action) + '</p><ul>' + f.details.map(function (detail) {return '<li>'+esc(detail)+'</li>';}).join('')+'</ul>' + (f.count>20?'<p>仅展示前 20 条。</p>':'') + '</details>';
      });
      html += '</div></div>';
    }
    html += '<details class="config-details"><summary>最近运行 · ' + reports.length + '</summary>' + reports.map(function (r) {return '<p>'+esc(r.time.slice(0,19).replace('T',' '))+' · '+esc(r.trigger)+' · '+(r.completed===null?'失败':r.completed+' '+(r.unit||'lot'))+' · '+esc(r.findings.map(function(f){return f.code;}).join('、')||'无异常')+'</p>';}).join('')+'</details>';
    A.$('#view-diagnostics').innerHTML = html;
  };
  document.addEventListener('click', async function (e) {
    var id=e.target.id;
    if (id==='diag-refresh') {
      try {var response=await fetch(endpoint(),{cache:'no-store'}); var body=await response.json();if(!response.ok)throw new Error(body.error || '读取失败');reports=body.runs;failure='';} catch(err){failure='记录读取失败：'+err.message;}
      A.renderDiagnostics();
    }
    if (['btn-run','cfg-save','cfg-add','cfg-excel','modal-apply'].indexOf(id)>=0) trigger={ 'btn-run':'手动重排','cfg-save':'保存配置','cfg-add':'新增计划','cfg-excel':'恢复参考配置','modal-apply':'切换策略'}[id];
  }, true);
})();
