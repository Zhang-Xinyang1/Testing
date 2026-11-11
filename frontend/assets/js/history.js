/* Saved plans are immutable viewing snapshots, never live configuration writes. */
(function () {
  'use strict';
  var A = window.TSApp, esc = A.esc, rows = [], failure = '', loaded = false, loading = false;
  var selection = [], comparison = '', live = null, requestSequence = 0;
  async function request(path, body) {
    var base = (window.APP_BASE || '/').replace(/\/?$/, '/');
    var response = await fetch(base + 'api/schedules' + path, body ? {
      method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)
    } : {cache:'no-store'});
    var value = await response.json();
    if (!response.ok) throw new Error(value.error || '方案读取失败');
    return value;
  }
  A.saveScheduleHistory = async function (result, snapshot) {
    try {
      var saved = await request('', {input:snapshot.input, options:snapshot.options, result:result});
      rows.unshift(saved.summary); rows = rows.slice(0, 50); failure = '';
    } catch (error) {
      failure = '本次结果可查看，但历史方案未保存：' + error.message;
      if (A.showWorkflowError) A.showWorkflowError(new Error(failure));
    }
  };
  async function refresh() {
    if (loading) return;
    loading = true;
    try { rows = (await request('')).schedules; loaded = true; failure = ''; }
    catch (error) { failure = error.message; }
    finally { loading = false; if (A.state.view === 'history') A.renderHistory(false); }
  }
  A.updateHistoryBanner = function () {
    var node = A.$('#snapshot-banner');
    if (!node) return;
    node.hidden = !A.state.historyId;
    node.className = 'note-card snapshot-banner';
    node.innerHTML = A.state.historyId ? '<b>正在查看历史方案</b> · ' + esc(A.state.historyTime || '') +
      '。输入及结果属于该次快照；当前服务器配置未改变。<button class="btn ghost small" data-export="mps">导出 MPS</button><button class="btn ghost small" data-export="lots">导出排产</button><button class="btn ghost small" data-export="pm">导出 PM 明细</button><button class="btn ghost small" id="history-return">返回当前工作</button>' : '';
  };
  A.leaveHistoricalView = function () {
    if (!live) return;
    A.state.data = live.data; A.state.result = live.result; A.state.options = live.options;
    live = null; A.state.historyId = null; A.state.gantt.anchor = 0;
    A.updateSourceCard(); A.updateRunChip(); A.updateHistoryBanner();
  };
  A.openHistoricalSchedule = async function (id) {
    if (A.hasConfigurationChanges && A.hasConfigurationChanges()) {
      A.toast('请先保存或放弃配置草稿，再打开历史方案'); return;
    }
    if (A.configurationBusy && A.configurationBusy()) return;
    var sequence = ++requestSequence;
    A.showLoading('正在打开历史方案', '只读取已保存的结果，不重新排产');
    try {
      var snapshot = await request('/' + encodeURIComponent(id));
      if (sequence !== requestSequence) return;
      if (!live) live = {data:A.state.data, result:A.state.result, options:A.state.options};
      A.state.data = snapshot.input; A.state.result = snapshot.result; A.state.options = snapshot.options;
      A.state.historyId = id; A.state.historyTime = snapshot.summary.time.slice(0,19).replace('T',' ');
      A.state.gantt.anchor = snapshot.result.start;
      A.updateSourceCard(); A.updateRunChip(); A.navigate('mps');
    } catch (error) { A.toast(error.message); }
    finally { if (sequence === requestSequence) A.hideLoading(); }
  };
  A.compareScheduleSnapshots = function (first, second) {
    var left = {}, right = {};
    first.result.productStats.forEach(function (p) { left[p.ppn] = p; });
    second.result.productStats.forEach(function (p) { right[p.ppn] = p; });
    return Array.from(new Set(Object.keys(left).concat(Object.keys(right)))).sort().map(function (key) {
      return {ppn:key, left:left[key] || null, right:right[key] || null};
    });
  };
  async function compare() {
    if (selection.length !== 2) { A.toast('请勾选两份方案'); return; }
    A.showLoading('正在比较方案', '比较输入、达成量与产品缺口');
    try {
      var snapshots = await Promise.all(selection.map(function (id) { return request('/' + encodeURIComponent(id)); }));
      snapshots.sort(function (a,b) { return a.summary.time.localeCompare(b.summary.time); });
      var first = snapshots[0], second = snapshots[1];
      comparison = '<div class="card"><div class="card-head"><h3>方案对比 · 较早 → 较晚</h3></div><div class="card-body"><p>' +
        esc(first.summary.time.slice(0,19)) + ' → ' + esc(second.summary.time.slice(0,19)) + '</p><p>' +
        (first.summary.inputHash === second.summary.inputHash ? '输入相同。' : '输入不同：差异同时受到数据或约束变化影响。') +
        '完成 wafer：' + A.num(first.summary.completedWafers) + ' → ' + A.num(second.summary.completedWafers) +
        '；最低产品达成率：' + A.pct(first.summary.minProductRate) + ' → ' + A.pct(second.summary.minProductRate) +
        '。</p><div class="table-scroll"><table class="data-table"><thead><tr><th>PPN</th><th>原目标</th><th>新目标</th><th>原完成</th><th>新完成</th><th>完成增减</th><th>原达成率</th><th>新达成率</th></tr></thead><tbody>' +
        A.compareScheduleSnapshots(first, second).map(function (row) {
          var a = row.left, b = row.right;
          return '<tr><td>' + esc(row.ppn) + '</td><td>' + (a ? A.num(a.mps) : '未包含') + '</td><td>' + (b ? A.num(b.mps) : '未包含') +
            '</td><td>' + (a ? A.num(a.completed) : '—') + '</td><td>' + (b ? A.num(b.completed) : '—') + '</td><td>' +
            (a && b ? A.num(b.completed - a.completed) : '—') + '</td><td>' + (a ? A.pct(a.rate) : '—') + '</td><td>' + (b ? A.pct(b.rate) : '—') + '</td></tr>';
        }).join('') + '</tbody></table></div></div></div>';
      A.renderHistory(false);
    } catch (error) { A.toast(error.message); }
    finally { A.hideLoading(); }
  }
  A.renderHistory = function (auto) {
    var html = '<div class="source-hero"><div><div class="source-title">方案历史</div><div class="source-sub">每次正式排产自动保存 · 最近 50 份 · 可打开 MPS、甘特并导出</div></div><div class="source-actions"><button class="btn ghost" id="history-refresh">刷新列表</button><button class="btn primary" id="history-compare">比较所选两份</button></div></div>';
    if (failure) html += '<div class="error-box">' + esc(failure) + '</div>';
    html += '<p class="config-hint">勾选两份方案进行比较。打开方案仅切换结果视图；编辑配置或重新排产会回到当前工作。旧版本产生的诊断记录不包含完整方案，仍在“排产诊断”查看。</p>';
    html += '<div class="table-card table-scroll"><table class="data-table"><thead><tr><th>比较</th><th>保存时间</th><th>月份 / 来源</th><th>完成 wafer</th><th>最低产品达成率</th><th>未排 Lot</th><th>操作</th></tr></thead><tbody>' +
      (rows.length ? rows.map(function (row) { return '<tr><td><input aria-label="选择方案 ' + esc(row.time) + '" type="checkbox" data-history-select="' + esc(row.id) + '"' + (selection.indexOf(row.id) >= 0 ? ' checked' : '') + '></td><td>' + esc(row.time.slice(0,19).replace('T',' ')) + '</td><td>' + esc(row.month) + '<br><small>' + esc(row.source.split(/[\\/]/).pop()) + '</small></td><td>' + A.num(row.completedWafers) + '</td><td>' + A.pct(row.minProductRate) + '</td><td>' + A.num(row.unscheduledLots) + '</td><td><button class="btn ghost small" data-history-open="' + esc(row.id) + '">打开方案</button></td></tr>'; }).join('') : '<tr><td colspan="7">' + (loading ? '正在读取…' : '暂无保存方案。完成一次正式排产后自动保存。') + '</td></tr>') + '</tbody></table></div>' + comparison;
    A.$('#view-history').innerHTML = html;
    if (auto !== false && !loaded && !loading) refresh();
  };
  document.addEventListener('change', function (event) {
    var id = event.target.dataset && event.target.dataset.historySelect;
    if (!id) return;
    if (event.target.checked && selection.length >= 2) { event.target.checked = false; A.toast('最多选择两份方案'); return; }
    selection = selection.filter(function (key) { return key !== id; });
    if (event.target.checked) selection.push(id);
  });
  document.addEventListener('click', function (event) {
    var target = event.target.closest('[data-history-open]');
    if (target) A.openHistoricalSchedule(target.dataset.historyOpen);
    if (event.target.id === 'history-refresh') refresh();
    if (event.target.id === 'history-compare') compare();
    if (event.target.id === 'history-return') { A.leaveHistoricalView(); A.renderView(); }
  });
})();
