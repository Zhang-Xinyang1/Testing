/* Configuration sections, ordinary mapping controls and persistent error guidance. */
(function () {
  'use strict';
  var A = window.TSApp, esc = A.esc;
  var section = 'source', lastError = null;
  var labels = {source:'1 数据来源', resources:'2 设备与容量', plans:'3 停机与在制', mapping:'4 表与字段', advanced:'其他接口'};
  var roles = {mps:'MPS', eqp:'设备', cards:'针卡', flow:'工艺路线', borrow:'借机', pm:'PM', constraints:'禁加工', lots:'Lot 明细', weekly:'周目标', wip:'在制', production:'生产信息', params:'参数参考'};
  var defaults = {mps:{platform:'Platform',ppn:'PPN',mps:'MPS'}, eqp:{id:'EQPID',type:'TYPE',proberId:'proberID',currentCard:'Current ProbeCard',currentRecipe:'Current Recipe',status:'Status'}, cards:{recipe:'Recipe',cards:'ProbeCard List',separator:';'}, flow:{prod:'PROD',step:'STEP',recipe:'RECIPE',time:'Time'}, borrow:{eqp:'EQPID',date:'DATE',hours:'借机时数',recipe:'Recipe',start:'Start'}, pm:{eqp:'EQP ID',targetKind:'TargetKind',early:'Early Due',due:'Due Date',over:'Over Due',hours:'Estimated Hours',start:'Start'}, constraints:{eqp:'EQPID',type:'TYPE',recipe:'Constraint Recipe'}, lots:{lotId:'LotID',ppn:'PROD',uid:'UID',state:'State',shipDue:'Ship Due',location:'Location',holdReason:'Hold Reason',actualComplete:'Actual Complete',externalComplete:'External Complete',currentEqp:'Current EQP',currentChamber:'Current Chamber',currentStep:'Current Step',currentRecipe:'Current Recipe',currentCard:'Current Card',currentStart:'Current Start',currentEnd:'Current End',platform:'Platform',wafer:'Wafer Qty',release:'Release Time',week:'Target Week',weeklyDue:'Weekly Due',monthlyDue:'Monthly Due',priority:'Priority'}, weekly:{ppn:'PPN',platform:'Platform',week:'Week',start:'Week Start',end:'Week End',target:'Target Wafers'}, wip:{lotId:'LotID',lotKey:'LotKey',eqp:'EQPID',chamber:'Chamber',card:'Card',step:'Step',recipe:'Recipe',start:'Start',end:'End'}, production:{asOf:'AsOf',identityStable:'Identity Stable'},params:{}};
  var fields = {uid:'稳定批次标识',state:'Lot 状态',shipDue:'内部出货日',location:'实际位置',holdReason:'HOLD 原因',actualComplete:'实际最后加工结束',externalComplete:'外部完工预测',currentEqp:'当前 EQP',currentChamber:'当前腔体',currentStep:'当前工序',currentRecipe:'当前 Recipe',currentCard:'当前针卡',currentStart:'当前工序开始',currentEnd:'当前资源释放',asOf:'数据时点',identityStable:'UID 已确认稳定',platform:'机台组', ppn:'产品 PPN', prod:'产品', mps:'目标 wafer', type:'设备类型', id:'EQPID', proberId:'ProberID', card:'当前针卡', recipe:'Recipe', status:'状态', cards:'针卡列表', step:'工序', time:'工时', date:'借机日期', eqp:'设备／维护对象', hours:'时长（小时）', early:'最早维护日期', due:'期望日期', over:'最迟完成日期', start:'开始时间', end:'结束时间', targetKind:'维护对象类型', lotId:'LotID', lotKey:'Lot 行键', wafer:'wafer 数', release:'释放时间', week:'周次', weeklyDue:'周交期', monthlyDue:'月交期', priority:'优先级', target:'周目标', chamber:'腔体', separator:'列表分隔符'};
  A.clearWorkflowErrors = function () {
    lastError = null;
    var node = A.$('#workflow-errors'); if (node) { node.hidden = true; node.innerHTML = ''; }
  };
  A.showWorkflowError = function (error) {
    lastError = error;
    var node = A.$('#workflow-errors'); if (!node) return;
    var issues = error.issues || [], message = error.message || String(error);
    node.hidden = false; node.className = 'workflow-error';
    node.innerHTML = '<div class="row"><b>需要处理的问题</b><button class="btn ghost small" id="workflow-error-close">收起</button></div><p>' + esc(message) + '</p>' +
      (issues.length ? '<div class="table-scroll"><table class="data-table"><thead><tr><th>输入来源</th><th>记录 / 字段</th><th>问题</th><th>处理</th></tr></thead><tbody>' + issues.slice(0,20).map(function (issue, i) {
        return '<tr><td>' + esc(issue.source === 'override' ? '手工配置' : issue.sheet || roles[issue.table] || '输入校验') + '</td><td>' +
          esc((issue.record ? '第 ' + issue.record + ' 条输入 · ' : '') + (issue.field || '')) + '</td><td>' + esc(issue.message) + '</td><td><button class="btn ghost small" data-error-location="' + i + '">前往检查</button></td></tr>';
      }).join('') + '</tbody></table></div>' : '<button class="btn ghost small" data-open-view="inputs">前往数据配置</button>') +
      '<p class="config-hint">“第 N 条输入”指读取后的记录序号，不一定是 Excel 物理行号。保存失败时，草稿仍保留。</p>';
  };
  A.selectConfigSection = function (value) {
    if (!labels[value]) value = 'source';
    section = value;
    A.$$('[data-config-panel]').forEach(function (node) { node.hidden = node.dataset.configPanel !== section; });
    A.$$('[data-config-tab]').forEach(function (node) { node.classList.toggle('active', node.dataset.configTab === section); node.setAttribute('aria-selected', String(node.dataset.configTab === section)); });
  };
  function mappingControls() {
    var source = A.$('#cfg-source'), field = A.$('#cfg-fields');
    if (!source || !field) return;
    var src, mapping;
    try { src = JSON.parse(source.value); mapping = JSON.parse(field.value); }
    catch (_) { return; } // Incomplete advanced JSON stays visible and editable.
    var node = A.$('#mapping-form');
    if (!node) { node = document.createElement('div'); node.id = 'mapping-form'; source.closest('details').before(node); }
    var opened = Array.from(node.querySelectorAll('details[open]')).map(function (item) { return item.dataset.mappingRole; });
    var sheetMap = src.sheetMap || {};
    node.innerHTML = '<p class="config-hint">直接填写实际 Sheet 名和表头。多个候选表头请每行填写一个；留空表示不映射。高级 JSON 仍可展开编辑。</p>' +
      '<label class="config-field">表头所在行<input class="input" id="map-header-row" type="number" min="1" value="' + esc(src.headerRow || 1) + '"></label>' +
      Object.keys(roles).map(function (role) {
        var sheet = sheetMap[role] || '';
        var key = Object.keys(mapping).find(function (name) { return A.sheetKey(name) === A.sheetKey(sheet); });
        var map = mapping[key] || {};
        return '<details class="config-details" data-mapping-role="' + role + '"><summary>' + esc(roles[role]) + ' · ' + esc(sheet || '未配置') + '</summary><label class="config-field">Sheet 名称<input class="input" data-map-role="' + role + '" data-map-original="' + esc(sheet) + '" value="' + esc(sheet) + '"></label><div class="mapping-grid">' +
          (sheet ? Array.from(new Set(Object.keys(defaults[role]).concat(Object.keys(map)))).map(function (logical) { var value = map[logical] || ''; return '<label class="config-field">' + esc(fields[logical] || logical) + ' <small>' + esc(logical) + '</small><textarea class="input mapping-value" data-map-field="' + esc(logical) + '" data-map-field-role="' + role + '">' + esc(Array.isArray(value) ? value.join('\n') : value) + '</textarea></label>'; }).join('') : '') + '</div></details>';
      }).join('');
    node.querySelectorAll('details').forEach(function (item) { item.open = opened.indexOf(item.dataset.mappingRole) >= 0; });
  }
  A.refreshMappingControls = mappingControls;
  A.setupConfigurationWorkflow = function () {
    var root = A.$('.production-config > .card-body');
    if (!root || !A.$('#cfg-source') || root.querySelector('.config-tabs')) return;
    var panels = {}, nav = document.createElement('div');
    nav.className = 'config-tabs'; nav.setAttribute('role', 'tablist');
    nav.innerHTML = Object.keys(labels).map(function (key) { return '<button class="btn ghost" role="tab" data-config-tab="' + key + '">' + labels[key] + '</button>'; }).join('');
    Object.keys(labels).forEach(function (key) { var panel = document.createElement('section'); panel.dataset.configPanel = key; panel.className = 'config-panel'; panels[key] = panel; });
    var current = 'source', sticky = [];
    Array.from(root.children).forEach(function (child) {
      if (child.querySelector('#cfg-save') || child.id === 'cfg-draft-status' || child.id === 'cfg-result') { sticky.push(child); return; }
      if (child.dataset.configPart) current = child.dataset.configPart;
      panels[current].appendChild(child);
    });
    root.prepend(nav);
    Object.keys(panels).forEach(function (key) { root.appendChild(panels[key]); });
    var capacity = document.createElement('div'); capacity.className = 'config-grid';
    ['#cfg-bake-machines', '#cfg-bake-chambers'].forEach(function (selector) {
      var field = A.$(selector); if (field) capacity.appendChild(field.closest('label'));
    });
    panels.resources.prepend(capacity);
    var actions = document.createElement('div'); actions.className = 'config-savebar';
    sticky.forEach(function (child) { actions.appendChild(child); }); root.appendChild(actions);
    mappingControls(); A.selectConfigSection(section);
  };
  document.addEventListener('input', function (event) {
    var target = event.target, data = target.dataset || {};
    if (!data.mapRole && !data.mapField && target.id !== 'map-header-row') return;
    try {
      var source = A.$('#cfg-source'), field = A.$('#cfg-fields');
      var src = JSON.parse(source.value), mapping = JSON.parse(field.value);
      if (target.id === 'map-header-row') src.headerRow = Number(target.value);
      if (data.mapRole) {
        var old = (src.sheetMap || {})[data.mapRole] || data.mapOriginal, name = target.value.trim();
        var oldKey = Object.keys(mapping).find(function (key) { return A.sheetKey(key) === A.sheetKey(old); });
        src.sheetMap = src.sheetMap || {};
        if (name) src.sheetMap[data.mapRole] = name; else delete src.sheetMap[data.mapRole];
        if (name && !mapping[name]) mapping[name] = Object.assign({}, mapping[oldKey] || defaults[data.mapRole]);
        if (name && oldKey && oldKey !== name && !Object.values(src.sheetMap).some(function (sheet) { return A.sheetKey(sheet) === A.sheetKey(oldKey); })) delete mapping[oldKey];
      }
      if (data.mapField) {
        var values = target.value.split(/\r?\n/).map(function (v) { return v.trim(); }).filter(Boolean);
        var sheet = (src.sheetMap || {})[data.mapFieldRole];
        if (!sheet) throw new Error('请先填写 Sheet 名');
        var mapped = Object.keys(mapping).find(function (key) { return A.sheetKey(key) === A.sheetKey(sheet); }) || sheet;
        mapping[mapped] = mapping[mapped] || {};
        mapping[mapped][data.mapField] = values.length > 1 ? values : (values[0] || '');
      }
      source.value = JSON.stringify(src, null, 2); field.value = JSON.stringify(mapping, null, 2);
      if (A.markConfigurationDirty) A.markConfigurationDirty();
    } catch (error) { A.showWorkflowError(new Error('请先修正高级映射 JSON：' + error.message)); }
  });
  document.addEventListener('change', function (event) {
    if (event.target.id === 'cfg-source' || event.target.id === 'cfg-fields' || (event.target.dataset || {}).mapRole) mappingControls();
  });
  document.addEventListener('click', function (event) {
    var tab = event.target.closest('[data-config-tab]');
    if (tab) A.selectConfigSection(tab.dataset.configTab);
    var link = event.target.closest('[data-open-view]');
    if (link) A.navigate(link.dataset.openView);
    if (event.target.id === 'workflow-error-close') A.clearWorkflowErrors();
    var location = event.target.closest('[data-error-location]');
    if (location && lastError) {
      var issue = (lastError.issues || [])[Number(location.dataset.errorLocation)] || {};
      A.navigate('inputs');
      var part = issue.section || (issue.table === 'eqp' ? 'resources' : ['pm','borrow','wip','taskOverrides'].indexOf(issue.table) >= 0 ? 'plans' : 'mapping');
      A.selectConfigSection(part);
      if (part === 'mapping') {
        var mapped = A.$$('[data-mapping-role]').find(function (node) { return node.dataset.mappingRole === issue.table; });
        if (mapped) {
          mapped.open = true; mapped.scrollIntoView({block:'center'});
          var field = Array.from(mapped.querySelectorAll('[data-map-field]')).find(function (node) { return node.dataset.mapField === issue.field; });
          if (field) { field.focus(); field.classList.add('field-attention'); }
        }
      }
      var table = A.$('#cfg-table-' + issue.table);
      if (table) {
        table.closest('details').open = true;
        var row = table.querySelectorAll('tbody tr')[Math.max(0, (issue.record || 1) - 1)];
        if (row) { row.scrollIntoView({block:'center'}); row.classList.add('field-attention'); var input = row.querySelector('input,select'); if (input) input.focus(); }
      }
    }
  });
})();
