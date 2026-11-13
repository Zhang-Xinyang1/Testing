/* PM provenance: original requests, merged events and actual stopped resources. */
(function () {
  'use strict';
  var A = window.TSApp, esc = A.esc;
  function sources(record) { return record.sourcePlans || [record]; }
  function time(value) { return A.SCHED.isoDate(typeof value === 'number' ? value : A.SCHED.parseDate(value)) || '未指定'; }
  function records() { return ((A.ganttResult?A.ganttResult():A.state.result) || {}).pmRecords || []; }

  A.pmSources = sources;
  A.pmRecordsFor = function (resource, start, end) {
    return records().filter(function (p) {
      return (p.resources || p.affectedEqps || [p.eqp]).indexOf(resource) >= 0 && p.start < end && start < p.end;
    });
  };
  A.pmTraceHTML = function () {
    if (!(A.ganttResult?A.ganttResult():A.state.result)) return '';
    var plans = records(), originalCount = plans.reduce(function (n, p) { return n + sources(p).length; }, 0);
    return '<details class="config-details"><summary>PM 执行对应关系 · 维护任务 ' + originalCount + ' 条 → 组合停机 ' + plans.length + ' 次</summary>' +
      '<p class="config-hint">Probe 与 EQP 为组合设备，任一部件维护都会整组停机。重叠时仅合并停机区间，各部件的维护任务均保留；连带停机不表示该部件也新增一项维护。PM 编号按本次输入顺序生成。</p>' +
      '<div class="table-scroll"><table class="data-table compact"><thead><tr><th>停机事件</th><th>原始任务 / 维护对象</th><th>停机 EQPID</th><th>连带停机 Prober</th><th>实际开始（北京时间）</th><th>实际结束</th><th>处理方式</th></tr></thead><tbody>' +
      (plans.length ? plans.map(function (p) {
        return '<tr><td><button class="btn ghost small" data-pm-detail="' + esc(p.id) + '">' + esc(p.id) + '</button></td>' +
          '<td>' + sources(p).map(function (s) { return esc(s.id + ' / ' + s.eqp + ' (' + (s.targetKind || 'EQP') + ')'); }).join('<br>') + '</td>' +
          '<td>' + esc((p.affectedEqps || [p.eqp]).join('、')) + '</td><td>' + esc((p.affectedProbers || []).join('、') || '—') + '</td><td>' + esc(time(p.start)) + '</td><td>' + esc(time(p.end)) + '</td><td>' + (p.merged ? '停机合并，维护任务均保留' : '单项维护，整组停机') + '</td></tr>';
      }).join('') : '<tr><td colspan="7">本次没有 PM 计划</td></tr>') + '</tbody></table></div></details>';
  };
  A.openPMDrawer = function (plans) {
    if (!plans.length) { A.toast('未找到该停机区间的 PM 原始记录'); return; }
    var html = plans.map(function (p) {
      return '<div class="detail-hero"><h3>' + esc(p.id) + ' · ' + (p.merged ? '合并停机，各项维护均保留' : '单项维护，整组停机') + '</h3>' +
        '<p>实际停机：' + esc(time(p.start)) + ' → ' + esc(time(p.end)) + '</p>' +
        '<p>受影响 EQPID：' + esc((p.affectedEqps || [p.eqp]).join('、')) + '</p><p>连带停机 Prober：' + esc((p.affectedProbers || []).join('、') || '—') + '</p><p>资源：' + esc((p.resources || [p.eqp]).join('、')) + '</p></div>' +
        sources(p).map(function (s) {
          return '<div class="note-card"><b>' + esc(s.id) + ' · ' + esc(s.eqp) + ' (' + esc(s.targetKind || 'EQP') + ')</b>' +
            '<p>最早日期：' + esc(s.early) + '；期望日期：' + esc(s.due) + '；最迟完成日期：' + esc(s.over) + '</p>' +
            '<p>原始指定开始：' + esc(s.start || '未指定，由算法在窗口内安排') + '；原始时长：' + esc(s.hours) + ' 小时</p></div>';
        }).join('');
    }).join('');
    A.openDrawer('PM 执行详情', plans.map(function (p) { return p.id; }).join('、'), html);
  };
  document.addEventListener('click', function (event) {
    var button = event.target.closest('[data-pm-detail]');
    if (button) A.openPMDrawer(records().filter(function (p) { return p.id === button.getAttribute('data-pm-detail'); }));
  });
})();
