/* Explicit CP constraints are saved only after the full schedule passes. */
(function () {
  'use strict';
  var A = window.TSApp, esc = A.esc, editing = null;
  A.taskOverridesHTML = function () {
    var items = ((A.state.config || {}).scheduling || {}).taskOverrides || [];
    return '<details class="config-details" data-config-part="plans"><summary>手工 CP 调整 · ' + items.length + ' 条</summary><p class="config-hint">在甘特图点击 CP 任务，再选择“调整此工序”。支持指定机台、最早占用开始、固定加工开始与针卡。固定任务仍须满足前序工艺、PM、借机和在制约束；重新导入改变 Lot 行或工艺时需重新选择。</p>' +
      (items.length ? '<div class="table-scroll"><table class="data-table"><thead><tr><th>Lot / 行键</th><th>工序位置</th><th>机台</th><th>时间要求</th><th>操作</th></tr></thead><tbody>' + items.map(function (p, i) {
        return '<tr><td>' + esc(p.lotId || p.lotKey) + '<br><small>' + esc(p.lotKey) + '</small></td><td>' + (p.opIndex + 1) + '</td><td>' + esc(p.eqp) + '</td><td>' + esc(p.fixedStart ? '固定加工开始 ' + p.fixedStart : p.earliestStart ? '最早占用开始 ' + p.earliestStart : '仅指定机台') + '</td><td><button class="btn ghost small" data-task-remove="' + i + '">解除并排产</button></td></tr>';
      }).join('') + '</tbody></table></div><button class="btn ghost small" id="task-remove-all">解除全部并排产</button><p class="config-hint">多条旧调整同时失效时，可一次解除全部，再选择新的任务。</p>' : '<p>当前没有手工调整。</p>') + '</details>';
  };
  A.openTaskAdjustment = function (lotKey, index) {
    if (A.state.historyId) { A.toast('历史方案只供查看，请返回当前工作后调整'); return; }
    if (A.hasConfigurationChanges && A.hasConfigurationChanges()) { A.toast('请先保存或放弃配置草稿'); return; }
    var task = A.state.result.cpTasks.find(function (t) { return t.lotKey === lotKey && t.opIndex === index; });
    if (!task) return;
    editing = task;
    var machines = A.state.data.eqp.filter(function (m) { return m.status === 'RUN' && task.platform.split(';').indexOf(m.type) >= 0 && !(A.state.data.constraints || []).some(function (c) { return c.eqp === m.id && c.recipe === task.recipe; }); });
    var value = A.SCHED.isoDate(task.start).replace('+08:00', '');
    A.openDrawer('手工 CP 调整', task.lotId + ' / ' + task.step,
      '<p>当前：' + esc(task.machine) + ' · 加工开始 ' + esc(A.SCHED.isoDate(task.start)) + '</p><p>保存前先完整试排。冲突时保留原配置与结果，并说明原因。</p>' +
      '<label class="config-field">指定机台<select class="select" id="task-machine">' + machines.map(function (m) { return '<option value="' + esc(m.id) + '"' + (m.id === task.machine ? ' selected' : '') + '>' + esc(m.id) + '</option>'; }).join('') + '</select></label>' +
      '<label class="config-field">时间要求<select class="select" id="task-mode"><option value="machine">仅指定机台，时间由算法安排</option><option value="earliest">不早于指定占用开始</option><option value="fixed">固定加工开始与当前针卡</option></select></label>' +
      '<label class="config-field">北京时间<input class="input" type="datetime-local" step="any" id="task-time" value="' + esc(value) + '"></label>' +
      '<p class="config-hint">固定加工开始：必要换卡在此前完成。保持原机台与原时间时保留原换卡窗口；变更时预留最多 5 分钟。BAKE 和 QTIME 跟随工艺重排，本页不直接修改其时间。</p>' +
      '<button class="btn primary" id="task-save">试排通过后保存</button><div id="task-feedback" role="status"></div>');
  };
  document.addEventListener('click', async function (event) {
    var edit = event.target.closest('[data-task-edit]');
    if (edit) { A.openTaskAdjustment(edit.dataset.taskEdit, Number(edit.dataset.taskIndex)); return; }
    var remove = event.target.closest('[data-task-remove]');
    var removeAll = event.target.id === 'task-remove-all';
    if (!remove && !removeAll && event.target.id !== 'task-save') return;
    if (A.configurationBusy && A.configurationBusy()) return;
    var items = JSON.parse(JSON.stringify(((A.state.config || {}).scheduling || {}).taskOverrides || []));
    try {
      if (removeAll) items = [];
      else if (remove) items.splice(Number(remove.dataset.taskRemove), 1);
      else {
        if (!editing) return;
        var mode = A.$('#task-mode').value, time = A.$('#task-time').value;
        var item = {lotKey:editing.lotKey, lotId:editing.lotId, opIndex:editing.opIndex,
          eqp:A.$('#task-machine').value, signature:(A.state.data.lotSignatures || {})[editing.lotKey]};
        if (mode !== 'machine') {
          if (!time || !Number.isFinite(A.SCHED.parseDate(time))) throw new Error('请填写有效的北京时间');
          item[mode === 'fixed' ? 'fixedStart' : 'earliestStart'] = A.SCHED.isoDate(A.SCHED.parseDate(time));
        }
        if (mode === 'fixed') {
          item.card = editing.card;
          if (item.eqp === editing.machine && A.SCHED.parseDate(time) === editing.start) item.reserveStart = A.SCHED.isoDate(editing.blockStart);
        }
        items = items.filter(function (p) { return p.lotKey !== item.lotKey || p.opIndex !== item.opIndex; });
        items.push(item);
      }
      await A.commitTaskOverrides(items);
      A.closeDrawer(); A.toast('手工调整已保存，排产结果已更新');
    } catch (error) {
      var feedback = A.$('#task-feedback'); if (feedback) feedback.textContent = '未保存：' + error.message;
      if (A.showWorkflowError) A.showWorkflowError(error);
    }
  });
})();
