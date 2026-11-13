/* Production workspace: explicit facts, isolated scenarios and manual adoption. */
(function () {
  'use strict';
  var A = window.TSApp, esc = A.esc, S = A.SCHED;
  var state = null, job = null, loading = false, busy = false, failure = '', poll = null;
  var tab = 'trial', query = '', status = 'all', product = 'all', page = 1;
  var scene = 'actual', extras = {}, reason = '', draftRules = null, snapshotDraft = '';
  var demoCatalog={scenarios:[],events:[]}, demoChoice='basic', presetChoice='arrival_known', validationReport=null;
  var excelDraft={}, mpsQuery='', chartPlanId=null;
  var sourceNames={demo:'内置演示',excel:'Excel',mes:'MES'};
  var stateNames = {READY:'已到位',FUTURE:'预计来料',HOLD:'HOLD',RUNNING:'在制 · 外部接续',DONE:'实际完工'};
  var views = {production:['生产看板','实际完成、预测产出与交期风险'],
    tracking:['Lot 查询','当前位置、跨机台路线与静置信息'],
    replanning:['滚动重排','数据变化 → 试排 → 比较 → 人工选用'],
    mps:['MPS 结果','当前选用方案的产品目标、实际与预测'],
    gantt:['排产甘特','当前选用方案的设备与腔体占用'],
    history:['方案历史','当前数据来源的选用方案与比较记录']};
  Object.keys(views).forEach(function (key) { A.TITLES[key] = views[key]; });
  A.unifiedEnabled=true;
  A.isProductionView = function () { return !!views[A.state.view] && !(['mps','gantt'].includes(A.state.view)&&A.state.historyId) && !(A.state.view==='history'&&A.state.legacyHistory); };
  A.ganttResult=function(){return A.isProductionView() ? state&&state.chart : A.state.result;};
  A.ganttData=function(){
    if(!A.isProductionView())return A.state.data;
    var d=currentPlan()&&currentPlan().input, types={};
    if(d)d.equipment.forEach(function(e){if(!e.type.includes('OVEN'))types[e.type]=e.type;});
    return {meta:{month:d&&d.month},platformType:types};
  };
  A.acceptProductionState=function(value){state=value;job=null;chartPlanId=null;draftRules=null;excelDraft={};reason='';};
  A.productionPlan=currentPlan;
  A.buildProductionCsv=function(kind){
    var p=currentPlan();if(!p)return null;
    var rows;
    function date(v){return v==null?'':S.isoDate(typeof v==='number'?v:S.parseDate(v));}
    if(kind==='mps'){
      rows=[['PlanID','PPN','Target_Wafers','Actual_Wafers','Forecast_Wafers','Actual_Plan_Delta','Forecast_Delta','Target_Delta','External_Unknown_Wafers']];
      p.products.forEach(function(x){rows.push([p.id,x.ppn,x.target,x.actual,x.forecast,x.actualPlanDelta,x.forecastDelta,x.targetDelta,x.externalUnknownWafers]);});
    }else if(kind==='pm'){
      rows=[['PlanID','EventID','SourcePMID','SourceTarget','TargetKind','Early','Due','Over','RequestedStart','RequestedHours','AffectedEQP','AffectedProber','Resources','DowntimeStart','DowntimeEnd','Merged']];
      p.pm.forEach(function(x){(x.sourcePlans||[]).forEach(function(s){rows.push([p.id,x.id,s.id,s.eqp,s.targetKind,s.early,s.due,s.over,s.start,s.hours,(x.affectedEqps||[]).join(';'),(x.affectedProbers||[]).join(';'),(x.resources||[]).join(';'),date(x.start),date(x.end),x.merged]);});});
    }else{
      rows=[['PlanID','UID','LotID','PPN','Wafer','State','Step','Recipe','Resource','BlockStart','Start','End','ResourceEnd','LotComplete','Reason']];
      p.lots.forEach(function(l){(l.tasks.length?l.tasks:[{}]).forEach(function(t){rows.push([p.id,l.uid,l.lotId,l.ppn,l.wafer,l.state,t.step,t.recipe,t.machine||t.chamber,date(t.blockStart),date(t.start),date(t.end),date(t.resourceEnd),date(l.complete),l.reason]);});});
    }
    function csv(v){var s=String(v==null?'':v);if(typeof v==='string'&&/^[=+@\-\t\r]/.test(s))s="'"+s;return '"'+s.replace(/"/g,'""')+'"';}
    return {filename:'production_'+kind+'_'+p.input.month+'_'+p.id.slice(0,8)+'.csv',text:'\ufeff'+rows.map(function(r){return r.map(csv).join(',');}).join('\r\n')};
  };
  A.productionGanttHeader=function(){return A.isProductionView()?header():'';};
  A.refreshProduction=refresh;
  A.receiveExcel=async function(){state=await request('/excel',excelDraft);job=null;draftRules=null;chartPlanId=null;render();return state;};
  function fmt(value) {
    if (value == null || value === '') return '待提供';
    var n = typeof value === 'number' ? value : S.parseDate(value);
    return Number.isFinite(n) ? S.isoDate(n).slice(0,16).replace('T',' ') : '无效时间';
  }
  function delta(value) { return value == null ? '无基线' : (value > 0 ? '+' : '') + A.num(value); }
  function btn(id, text, cls) { return '<button type="button" class="btn '+(cls||'ghost')+'" data-prod-action="'+id+'"'+(busy?' disabled':'')+'>'+text+'</button>'; }
  function table(headers, rows) { return '<div class="table-scroll"><table class="data-table"><thead><tr>'+headers.map(function(x){return '<th>'+x+'</th>';}).join('')+'</tr></thead><tbody>'+ (rows.length?rows.join(''):'<tr><td colspan="'+headers.length+'">暂无记录</td></tr>')+'</tbody></table></div>'; }
  function cell(value) { return '<td>'+esc(value == null ? '—' : value)+'</td>'; }
  function input(id,label,value,type,attrs) { return '<label class="config-field">'+label+'<input class="input" id="'+esc(id)+'" type="'+(type||'text')+'" value="'+esc(value==null?'':value)+'" '+(attrs||'')+'></label>'; }
  function dateValue(value) { return value ? S.isoDate(S.parseDate(value)).replace('+08:00','') : ''; }
  function currentPlan() { return state && state.active; }
  function lotLink(lot) { return '<button class="prod-link" data-prod-lot="'+esc(lot.uid)+'">'+esc(lot.lotId)+'</button>'; }
  async function request(path, value) {
    var base = (window.APP_BASE || '/').replace(/\/?$/, '/');
    var response = await fetch(base+'api/production'+path, value === undefined ? {cache:'no-store'} : {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(value)});
    var result = await response.json();
    if (!response.ok) throw new Error(result.error || '生产工作台请求失败');
    return result;
  }
  async function refresh() {
    if (loading) return;
    loading = true;
    try {
      var results = await Promise.all([request(''),request('/job'),request('/demo-catalog')]);
      state=results[0]; job=results[1].job; demoCatalog=results[2]; failure='';
      if(state.snapshot&&state.snapshot.demoCase){
        demoChoice=state.snapshot.demoCase.key;
        if(!Object.keys(extras).length)extras=Object.assign({},state.snapshot.demoCase.suggestedExpansion||{});
      }
      if (job && job.status === 'running') schedulePoll();
    } catch(error) { failure=error.message; }
    finally { loading=false; render(); }
  }
  function schedulePoll() {
    clearTimeout(poll);
    poll=setTimeout(async function () {
      try {
        job=(await request('/job')).job;
        if (job && job.status==='running') schedulePoll();
        else state=await request('');
        failure='';
      } catch(error) { failure=error.message; }
      render();
    },1000);
  }
  function header() {
    var d=state && state.snapshot;
    var plan=currentPlan();
    return '<div class="source-hero"><div><div class="source-title">'+(d?esc(sourceNames[d.source]||d.source)+' · 统一生产数据':'等待生产数据')+'</div><div class="source-sub">'+(d?esc(d.month)+' · 数据时点 '+fmt(d.asOf)+' · '+d.lots.length+' Lot':'接收 Excel、MES 或内置演示数据后开始试排。')+'</div></div><div class="source-actions">'+btn('refresh','刷新状态')+btn('source-excel','Excel 数据')+btn('source-mes','MES 数据')+btn('source-demo','演示数据')+'</div></div>'+
      '<div class="note-card" data-current-plan="'+esc(plan?plan.id:'')+'">'+(plan?'当前选用方案：'+esc(plan.id.slice(0,12))+' · 看板、MPS、甘特与 Lot 查询共用此方案。':'尚未选用方案；试排候选需人工选用后才会更新所有结果页面。')+(plan?' <button class="btn ghost small" data-export="pm">导出选用 PM</button>':'')+(state&&state.changePending&&plan?' 输入已有变化，预测与任务仍属于上次选用方案，请重排并确认。':'')+'</div>'+
      (d?'<div class="note-card '+(d.source==='demo'?'prod-demo':'')+'">'+(d.source==='demo'?'全部为合成数据；各页面同步使用本演示来源。':d.source==='excel'?'Excel 来源：'+esc(d.sourceName||'已保存配置'):'当前使用已接收的 MES 快照；尚未配置 MES 主动拉取。')+' '+(!d.identityStable?'批次稳定标识待确认，跨快照冻结与替换计划受限。':'批次使用本数据源提供的稳定标识。')+'</div>':'')+
      (d&&(d.inputIssues||[]).length?'<div class="error-box"><b>试排前需补充</b><ul>'+d.inputIssues.map(function(x){return '<li>'+esc(x)+'</li>';}).join('')+'</ul></div>':'')+
      (d&&(d.inputNotes||[]).length?'<p class="config-hint">'+d.inputNotes.map(esc).join('；')+'</p>':'')+
      (failure||state&&state.inputLoadError?'<div class="error-box" role="alert">'+esc(failure||state.inputLoadError)+' <button class="btn ghost" data-open-view="inputs">检查 Excel 配置</button></div>':'')+
      (((!d&&(!state||!state.selectedSource||state.selectedSource==='demo'))||(d&&d.source==='demo'))&&['production','replanning'].includes(A.state.view)?demoHTML():'')+
      (state&&((d&&d.source==='excel')||state.selectedSource==='excel')&&['production','replanning'].includes(A.state.view)?excelHTML():'');
  }
  function excelHTML(){
    var d=state.snapshot, options=Object.assign({},d&&d.excelOptions||{},excelDraft);
    var month=d&&d.month||((A.state.config||{}).dataSource||{}).month||'';
    return '<details class="config-details"'+(!d?' open':'')+'><summary>Excel 接收与缺失字段补充</summary><p>在“数据配置”上传并保存 Excel 后，统一工作区自动更新。以下批量补充仅用于表中缺失的值，需由计划员确认；不会改写原 Excel。</p><div class="config-grid">'+
      input('prod-excel-asOf','Excel 数据时点',dateValue(options.asOf||d&&d.asOf)||month+'-01T00:00','datetime-local')+
      input('prod-excel-defaultAvailableAt','缺失可用时间的批次统一可用时间',dateValue(options.defaultAvailableAt),'datetime-local')+
      input('prod-excel-defaultShipDue','缺失内部出货日的批次统一交期',dateValue(options.defaultShipDue),'datetime-local')+
      '<label><input id="prod-excel-confirmPlannedLots" type="checkbox"'+(options.confirmPlannedLots?' checked':'')+'>确认无明细时的 MPS 拆批可用于计划，并使用上方明确的可用时间</label>'+
      '<label><input id="prod-excel-identityStable" type="checkbox"'+((options.identityStable===undefined?d&&d.identityStable:options.identityStable)?' checked':'')+'>已确认 Excel 的 UID 列跨次导入稳定</label></div>'+btn('excel','接收已保存 Excel 与补充信息','primary')+'<p>缺少明确可用时间或内部交期的 Lot 保留展示，不安排新加工。月度 MPS 不代表实际库存。</p></details>';
  }
  function demoHTML(){
    var selected=demoCatalog.scenarios.find(function(c){return c.key===demoChoice;});
    var active=state&&state.snapshot&&state.snapshot.demoCase;
    return '<details class="config-details"'+(active?'':' open')+'><summary>演示场景与输入自检'+(active?' · '+esc(active.title):'')+'</summary><div class="card prod-demo-tools"><div class="card-head"><h3>内置演示场景</h3><span class="pill">全部为合成数据</span></div><div class="card-body"><div class="prod-toolbar"><label class="config-field">选择演示场景<select id="prod-demo-case" class="select">'+demoCatalog.scenarios.map(function(c){return '<option value="'+esc(c.key)+'"'+(demoChoice===c.key?' selected':'')+'>'+esc(c.title)+'</option>';}).join('')+'</select></label>'+btn('demo','载入所选场景','primary')+btn('demo-check','输入样例自检')+'</div>'+
      '<p>'+esc(selected?selected.description:'正在读取演示目录…')+'</p>'+(selected?'<ul class="prod-checklist">'+selected.checks.map(function(c){return '<li>'+esc(c)+'</li>';}).join('')+'</ul>':'')+
      '<p class="config-hint">载入后应用该场景的演示规则、清空当前选用状态，保留历史记录；不会自动排产。'+(active?'当前已载入：'+esc(active.title)+'。':'')+'</p>'+
      (validationReport?'<details open class="config-details"><summary>输入自检：'+validationReport.checks.filter(function(c){return c.passed;}).length+' / '+validationReport.checks.length+' 符合预期</summary><p>使用真实输入校验器检查内置样例；不保存样例，不启动排产。拒绝错误数据属于预期通过。</p>'+table(['样例','预期','实际','核对','说明'],validationReport.checks.map(function(c){return '<tr>'+[c.title,c.expected==='accept'?'允许':'拒绝',c.accepted?'允许':'拒绝',c.passed?'符合预期':'需要修复',c.message].map(cell).join('')+'</tr>';}))+'</details>':'')+'</div></div></details>';
  }
  function adoptReason(){return reason||(state&&state.snapshot&&state.snapshot.source==='demo'?'演示测试：'+((state.snapshot.demoCase||{}).title||'当前模拟场景')+'，人工选用':'');}
  function projectedRows() {
    var d=state.snapshot, start=S.parseDate(d.month+'-01'), end=S.parseDate(nextMonth(d.month));
    return d.demands.map(function (p) {
      var actual=d.lots.filter(function(l){return l.ppn===p.ppn && l.state==='DONE' && S.parseDate(l.actualComplete)>=start && S.parseDate(l.actualComplete)<end;}).reduce(function(n,l){return n+l.wafer;},0);
      var missing=d.lots.filter(function(l){return l.ppn===p.ppn&&l.state==='RUNNING'&&!l.externalComplete;}).reduce(function(n,l){return n+l.wafer;},0);
      return {ppn:p.ppn,target:p.wafer,actual:actual,forecast:null,targetDelta:null,actualPlanDelta:null,forecastDelta:null,externalUnknownWafers:missing};
    });
  }
  function nextMonth(month) { var p=month.split('-').map(Number); return (p[1]===12?p[0]+1:p[0])+'-'+String(p[1]===12?1:p[1]+1).padStart(2,'0')+'-01'; }
  function kpi(label,value,sub) { return '<div class="prod-kpi"><span>'+label+'</span><strong>'+value+'</strong><small>'+sub+'</small></div>'; }
  function overview() {
    var plan=currentPlan(), d=state.snapshot, facts=projectedRows(), products=plan?plan.products:facts, total=facts.reduce(function(n,p){return n+p.target;},0), actual=facts.reduce(function(n,p){return n+p.actual;},0);
    var forecast=plan?products.reduce(function(n,p){return n+p.forecast;},0):null;
    var pending=state.changePending && plan;
    var html='<div class="prod-kpis">'+kpi('月度 MPS',A.num(total),'wafer')+kpi('实际完成',A.num(actual),'最后一道加工结束')+kpi('方案预计完成',forecast==null?'尚未试排':A.num(forecast),'含实际完成，不重复累加')+kpi('在制待外部预测',A.num(d.lots.filter(function(l){return l.state==='RUNNING'&&!l.externalComplete;}).reduce(function(n,l){return n+l.wafer;},0)),'wafer · 未计入明确预测')+'</div>';
    if (pending) html+='<div class="workflow-error">已收到新数据或规则变化。上方实际完成来自最新快照；下表及预测仍是已选方案的统计，需重新试排并选用。'+btn('goto-trial','查看变化与试排','primary')+'</div>';
    html+='<div class="card"><div class="card-head"><h3>产品达成与三种差额</h3><div class="source-actions"><button class="btn ghost" data-open-view="mps">MPS 产品明细</button><button class="btn ghost" data-export="mps">导出 MPS</button>'+btn('goto-trial','进入滚动重排','primary')+'</div></div><div class="card-body"><p class="config-hint">差额 = 前者减后者；负数表示不足。无基线显示“无基线”。本月按最后一道加工结束统计，静置期不延后产出。</p>'+table(['PPN','MPS / wafer','实际完成','预计完成','实际 − 原计划截至当前','新预测 − 原预测','预测 − 月目标','外部未知 / wafer'],products.map(function(p){return '<tr><td><button class="prod-link" data-prod-product="'+esc(p.ppn)+'">'+esc(p.ppn)+'</button></td>'+[p.target,p.actual,p.forecast==null?'待试排':p.forecast,delta(p.actualPlanDelta),delta(p.forecastDelta),p.forecast==null?'待试排':delta(p.targetDelta),p.externalUnknownWafers||0].map(cell).join('')+'</tr>';}))+'</div></div>';
    var alerts=plan?plan.alerts:[];
    html+='<div class="card"><div class="card-head"><h3>业务预警 · '+alerts.length+' 条</h3></div><div class="card-body">'+table(['类型','产品','说明'],alerts.map(function(a){return '<tr>'+cell(a.kind)+cell(a.ppn)+'<td>'+esc(a.message)+(a.uid?' <button class="prod-link" data-prod-lot="'+esc(a.uid)+'">查看 Lot</button>':'')+'</td></tr>';}))+'</div></div>';
    return html;
  }
  function allLots() {
    var plan=currentPlan(), mapped={};
    if (plan) plan.lots.forEach(function(l){mapped[l.uid]=l;});
    return (plan?plan.input:state.snapshot).lots.map(function(l){return Object.assign({},mapped[l.uid]||{},l,{tasks:(mapped[l.uid]||{}).tasks||[],plannedComplete:(mapped[l.uid]||{}).complete});});
  }
  function tracking() {
    var d=currentPlan()?currentPlan().input:state.snapshot, rows=allLots().filter(function(l){return (status==='all'||l.state===status)&&(product==='all'||l.ppn===product)&&(!query||(l.lotId+' '+l.uid+' '+l.ppn).toLowerCase().includes(query.toLowerCase()));});
    var pages=Math.max(1,Math.ceil(rows.length/30)); page=Math.min(page,pages);
    return '<div class="card"><div class="card-body"><div class="prod-toolbar">'+input('prod-search','搜索 Lot / PPN / 编号',query)+
      '<label class="config-field">状态<select id="prod-state" class="select"><option value="all">全部状态</option>'+Object.keys(stateNames).map(function(k){return '<option value="'+k+'"'+(status===k?' selected':'')+'>'+stateNames[k]+'</option>';}).join('')+'</select></label><label class="config-field">产品<select id="prod-product" class="select"><option value="all">全部产品</option>'+d.demands.map(function(p){return '<option'+(product===p.ppn?' selected':'')+'>'+esc(p.ppn)+'</option>';}).join('')+'</select></label>'+btn('search','查询')+'<button class="btn ghost" data-export="lots">导出选用方案 Lot</button></div><p class="config-hint">位置与任务使用已选方案的数据时点；尚未选用时展示收到的原始数据。'+(state.changePending?'已收到新输入，选用新方案后此列表才会更新。':'')+'</p>'+table(['LotID','PPN / wafer','状态','实际位置','可用时间','内部出货日','加工完工（实际/预测）','操作'],rows.slice((page-1)*30,page*30).map(function(l){return '<tr><td>'+lotLink(l)+'<small class="prod-uid">'+esc(l.uid)+'</small></td>'+cell(l.ppn+' / '+l.wafer)+cell(stateNames[l.state])+cell(l.location||'待提供')+cell(fmt(l.availableAt))+cell(fmt(l.shipDue))+cell(fmt(l.plannedComplete))+'<td><button class="prod-link" data-prod-lot="'+esc(l.uid)+'">路线与静置</button></td></tr>';}))+'<div class="prod-pagination">'+btn('prev','上一页')+'<span>第 '+page+' / '+pages+' 页 · '+rows.length+' 条</span>'+btn('next','下一页')+'</div></div></div>';
  }
  function openLot(uid) {
    var lot=allLots().find(function(l){return l.uid===uid;}); if(!lot)return;
    var complete=lot.state==='DONE'?S.parseDate(lot.actualComplete):lot.plannedComplete;
    var restEnd=complete==null?null:complete+336*A.HOUR, remaining=restEnd==null?null:Math.max(0,(restEnd-S.parseDate(currentPlan()?currentPlan().asOf||currentPlan().input.asOf:state.snapshot.asOf))/A.HOUR);
    var restStatus=lot.state!=='DONE'?'尚未确认开始，待最后加工实际完成':remaining>0?'剩余 '+remaining.toFixed(1)+' 小时':'已达到 336 小时';
    var html='<div class="prod-detail"><span class="pill">'+esc(stateNames[lot.state])+'</span><p>'+esc(lot.ppn)+' · '+lot.wafer+' wafer · '+esc(lot.uid)+'</p><div class="kv"><dt>实际位置</dt><dd>'+esc(lot.location||'待提供')+'</dd><dt>内部计划出货</dt><dd>'+fmt(lot.shipDue)+'</dd><dt>'+(lot.state==='DONE'?'实际':'计划')+'加工完工</dt><dd>'+fmt(complete)+'</dd><dt>'+(lot.state==='DONE'?'静置结束':'预计静置结束')+'</dt><dd>'+fmt(restEnd)+'</dd><dt>静置状态</dt><dd>'+restStatus+'</dd></div><p class="note-card">静置 14×24 小时是独立记录，不阻塞后续工序，不延后 MPS 完成统计。</p>';
    if (lot.state==='RUNNING') html+='<div class="note-card">当前 '+esc(lot.current.step)+' · '+esc(lot.current.eqp)+'<br>'+fmt(lot.current.start)+' → '+fmt(lot.current.end)+'<br>本系统仅保留当前占用；后续由外部系统负责。最终完工预测：'+fmt(lot.externalComplete)+'</div>';
    html+='<h3>计划跨机台路线</h3><div class="prod-route">'+lot.tasks.map(function(t){return '<div><b>'+esc(t.step)+'</b><span>'+esc(t.machine||t.chamber||'等待')+'</span><small>'+fmt(t.start)+' → '+fmt(t.end)+'</small></div>';}).join('')+'</div><p class="config-hint">BAKE 任务开始包含升温，Lot 放行后仍有降温与换 Lot 占腔。</p>'+table(['工序','资源','Recipe','占用开始','任务开始','Lot 放行','资源释放','切换'],lot.tasks.map(function(t){return '<tr>'+[t.step,t.machine||t.chamber||'工艺等待',t.recipe,fmt(t.blockStart||t.start),fmt(t.start),fmt(t.end),fmt(t.resourceEnd||t.end),t.machine?'Recipe '+((t.recipeSetup||0)/60000)+' 分 + 针卡 '+((t.cardSetup||0)/60000)+' 分':'—'].map(cell).join('')+'</tr>';}));
    if (!lot.tasks.length) html+='<p>'+esc(lot.reason||'当前没有本系统安排的后续任务。')+'</p>';
    html+='</div>'; A.openDrawer('LOT TRACKING',lot.lotId,html);
  }
  A.openProductionLot=openLot;
  A.openProductionBatch=function(id){
    var b=state.chart&&state.chart.batches.find(function(x){return x.id===id;});if(!b)return;
    A.openDrawer('BAKE BATCH',b.id,'<p>'+esc(b.chamber)+' · '+esc(b.recipe)+' · '+b.wafers+' wafer</p><p>占腔：'+fmt(b.start)+' → '+fmt(b.end)+'；Lot 放行：'+fmt(b.lotEnd)+'</p>'+b.lotKeys.map(function(uid,i){return '<p><button class="prod-link" data-prod-lot="'+esc(uid)+'">'+esc(b.lots[i])+' · '+esc(uid)+'</button></p>';}).join(''));
  };
  function historyHTML(){
    return '<div class="card"><div class="card-head"><h3>统一方案与比较记录</h3>'+btn('legacy-history','查看旧版历史档案')+'</div><div class="card-body">'+table(['保存时间','来源','场景','说明','操作'],state.history.map(function(h){return '<tr>'+[fmt(h.time),sourceNames[h.source],h.scenario==='actual'?'实际资源':'扩产假设',h.reason].map(cell).join('')+'<td><button class="prod-link" data-prod-history="'+esc(h.id)+'">打开记录</button></td></tr>';}))+'</div></div>';
  }
  function mpsHTML(){
    var plan=currentPlan(), rows=plan?plan.products:projectedRows();
    rows=rows.filter(function(p){return !mpsQuery||p.ppn.toLowerCase().includes(mpsQuery.toLowerCase());});
    return '<div class="card"><div class="card-body"><div class="prod-toolbar">'+input('prod-mps-search','搜索产品 PPN',mpsQuery)+btn('mps-search','查询')+btn('goto-trial','试排与选用')+'<button class="btn ghost" data-export="mps">导出 MPS</button></div><p>预计完成包含当月实际完成；差额为预测减目标。结果与生产看板使用同一选用方案，点击产品查看对应 Lot。</p>'+table(['PPN','月度目标 / wafer','实际完成','预计完成','预计达成率','预测 − 月目标','实际 − 原计划截至当前','新预测 − 原预测','外部未知 / wafer'],rows.map(function(p){return '<tr><td><button class="prod-link" data-prod-product="'+esc(p.ppn)+'">'+esc(p.ppn)+'</button></td>'+[p.target,p.actual,p.forecast==null?'待选用':p.forecast,p.forecast==null?'—':(p.target?(100*p.forecast/p.target).toFixed(1)+'%':'—'),delta(p.targetDelta),delta(p.actualPlanDelta),delta(p.forecastDelta),p.externalUnknownWafers||0].map(cell).join('')+'</tr>';}))+'</div></div>';
  }
  function jobHTML() {
    if(!job)return '<div class="note-card">尚无候选方案。试排结果不会自动替换已选方案。</div>';
    var names={running:'正在试排',completed:'试排完成',cancelled:'已取消',timed_out:'已达时限',failed:'试排失败'};
    return '<div class="prod-job" role="status"><div><b>'+names[job.status]+'</b><p>'+esc(job.message)+'</p><small>已运行 '+job.elapsedSeconds+' 秒 / 上限 '+job.limitSeconds+' 秒 · '+job.evaluations+' 个候选</small></div>'+(job.status==='running'?btn('cancel','取消计算'):'')+'</div>';
  }
  function comparison(plan, historical) {
    if(!plan)return '';
    var adopted=currentPlan()&&currentPlan().id===plan.id;
    var stale=!historical&&!adopted&&(JSON.stringify(plan.input)!==JSON.stringify(state.snapshot)||JSON.stringify(plan.rules)!==JSON.stringify(state.rules)||(plan.baselineId||null)!==(currentPlan()&&currentPlan().id||null));
    return '<div class="card"><div class="card-head"><h3>'+(historical?'历史记录':'候选方案')+' · '+(plan.scenario==='expansion'?'扩产假设':'实际资源')+'</h3></div><div class="card-body"><p>数据时点：'+fmt(plan.asOf)+'。冻结 '+plan.frozenLots+' 个 Lot。交期优先，其次改善产品缺口；未证明全局最优。</p>'+table(['PPN','实际完成','方案预计','实际 − 原计划','新预测 − 原预测','预测 − 月目标'],plan.products.map(function(p){return '<tr>'+[p.ppn,p.actual,p.forecast,delta(p.actualPlanDelta),delta(p.forecastDelta),delta(p.targetDelta)].map(cell).join('')+'</tr>';}))+
      '<details class="config-details"><summary>任务影响 · '+plan.changes.length+' 个 Lot</summary>'+(!plan.identityStable?'<p class="workflow-error">批次稳定标识待确认，无法可靠逐 Lot 对比。</p>':'')+
      table(['Lot','变化','原完工','新完工'],plan.changes.map(function(c){return '<tr>'+[c.lotId,c.kind,fmt(c.before),fmt(c.after)].map(cell).join('')+'</tr>';}))+
      '</details><h3>借机开始时 Recipe 检查</h3>'+table(['机台','借机开始','要求 Recipe','当时 Recipe','检查'],plan.borrows.map(function(b){return '<tr>'+[b.eqp,fmt(b.start),b.requiredRecipe||b.req.recipe,b.recipeAtStart||'未取得',b.recipeMatched?'匹配':b.reason||'未匹配'].map(cell).join('')+'</tr>';}))+
      '<details class="config-details"><summary>PM 原始维护与组合停机 · '+(plan.pm||[]).length+' 段</summary>'+table(['原始维护对象','组合 EQP','组合 Prober','停机开始','停机结束','合并说明'],(plan.pm||[]).map(function(p){return '<tr>'+[(p.sourcePlans||[]).map(function(s){return s.eqp+' ('+s.targetKind+')';}).join('、'),(p.affectedEqps||[]).join('、'),(p.affectedProbers||[]).join('、'),fmt(p.start),fmt(p.end),p.merged?'重叠停机合并，各项维护保留':'独立停机'].map(cell).join('')+'</tr>';}))+'</details>'+
      '<details class="config-details"><summary>查看候选任务时间与资源</summary>'+table(['Lot','工序','资源','占用开始','任务开始','Lot 放行','资源释放'],plan.lots.flatMap(function(l){return l.tasks.map(function(t){return '<tr>'+[l.lotId,t.step,t.machine||t.chamber||'等待',fmt(t.blockStart||t.start),fmt(t.start),fmt(t.end),fmt(t.resourceEnd||t.end)].map(cell).join('')+'</tr>'; }); }))+'</details>'+
      (historical?'<p class="note-card">历史只读记录，不会覆盖当前方案。</p>':job&&job.status==='running'?'<p class="note-card">仍在计算，当前候选仅供查看；完成或取消后可保存和选用。</p>':adopted?'<p class="note-card">此方案已经选用，当前未接入 MES 下发。</p>':btn('archive','保存比较记录')+(stale?'<p class="workflow-error">数据、规则或已选方案已变化，此候选仅供查看，请重新试排。</p>':plan.scenario==='actual'?'<div class="prod-toolbar">'+input('prod-adopt-reason','选用说明',adoptReason())+btn('adopt','选用此方案','primary')+'</div><p class="config-hint">演示已预填说明，仍由你点击选用；现场方案需填写实际原因。选用保存至本程序，当前未接入 MES 下发。</p>':'<p class="note-card">这是扩产假设结果，不能选为实际资源计划。</p>'))+'</div></div>';
  }
  function trialHTML() {
    var d=state.snapshot, types=Array.from(new Set(d.equipment.filter(function(e){return !e.type.includes('OVEN');}).map(function(e){return e.type;})));
    return '<div class="card"><div class="card-body"><div class="prod-toolbar"><label class="config-field">资源场景<select id="prod-scenario" class="select"><option value="actual"'+(scene==='actual'?' selected':'')+'>实际资源</option><option value="expansion"'+(scene==='expansion'?' selected':'')+'>假设扩产</option></select></label>'+types.map(function(t){return input('prod-extra-'+t,'新增 '+esc(t)+' 台数（仅扩产）',extras[t]||0,'number','min="0" max="20" data-prod-extra="'+esc(t)+'"');}).join('')+btn('trial','开始试排','primary')+'</div><p>重排时点：'+fmt(d.asOf)+'；计划月份：'+esc(d.month)+'；冻结窗口：'+state.rules.freezeHours+' 小时。</p><p class="config-hint">当前工序的在制占用始终保留。冻结窗口覆盖的计划将按整 Lot 剩余路线保留，并连带保留同一 BAKE 批次成员，避免拆散已有批次。冻结含过去任务但缺少执行事实时，会要求更新快照。</p></div></div>'+jobHTML()+comparison(job&&job.plan);
  }
  function eventsHTML() {
    var d=state.snapshot;
    return (d.source==='demo'?'<div class="card"><div class="card-head"><h3>一键生产变化</h3></div><div class="card-body"><div class="prod-toolbar"><label class="config-field">选择变化样例<select id="prod-demo-event" class="select">'+demoCatalog.events.map(function(e){return '<option value="'+esc(e.key)+'"'+(presetChoice===e.key?' selected':'')+'>'+esc(e.title)+'</option>';}).join('')+'</select></label>'+btn('demo-event','应用此变化','primary')+'</div><p class="config-hint">自动选取合适 Lot / 设备，生成时间和原因。建议先选用基线方案，再应用变化并重新试排；每种变化每次载入只能应用一次。</p></div></div>':'')+
      '<div class="card"><div class="card-head"><h3>数据与规则变化记录</h3></div><div class="card-body">'+table(['时间','类型','已知原因'],state.events.map(function(e){return '<tr>'+[fmt(e.time),e.kind,e.reason].map(cell).join('')+'</tr>';}))+'<p class="config-hint">记录事件事实；多种变化同时发生时，不将全部产出差额直接归因于某一事件。</p></div></div>'+
      (d.source==='demo'?'<details class="config-details"><summary>自定义变化（可选手工填写）</summary><div class="card"><div class="card-head"><h3>模拟生产变化</h3></div><div class="card-body"><div class="config-grid"><label class="config-field">事件<select class="select" id="prod-event-kind"><option value="arrival">来料时间变化</option><option value="due">内部出货日变化</option><option value="down">设备停机</option></select></label><label class="config-field">Lot<select id="prod-event-lot" class="select">'+d.lots.filter(function(l){return ['READY','FUTURE'].includes(l.state);}).map(function(l){return '<option value="'+esc(l.uid)+'">'+esc(l.lotId+' · '+l.uid)+'</option>';}).join('')+'</select></label><label class="config-field">设备（停机事件）<select id="prod-event-eqp" class="select">'+d.equipment.filter(function(e){return !e.type.includes('OVEN');}).map(function(e){return '<option>'+esc(e.id)+'</option>';}).join('')+'</select></label>'+input('prod-event-time','新的可用/出货时间；停机填写结束时间',dateValue(d.asOf),'datetime-local')+input('prod-event-reason','变更原因','')+'</div>'+btn('event','保存模拟变化')+'</div></div></details>':'');
  }
  function settingsHTML() {
    var r=draftRules||state.rules;
    return '<div class="card"><div class="card-head"><h3>生产规则</h3></div><div class="card-body"><div class="config-grid">'+
      input('prod-rule-recipeChangeMinutes','换 Recipe（分钟）',r.recipeChangeMinutes,'number','min="0" max="1440"')+
      input('prod-rule-maxRuntimeSeconds','计算上限（秒，最多 1800）',r.maxRuntimeSeconds,'number','min="1" max="1800"')+
      input('prod-rule-freezeHours','冻结范围（小时，待产线确认）',r.freezeHours,'number','min="0" max="744"')+
      input('prod-rule-checkTime','每日检查已接收数据的时刻',r.checkTime,'time')+
      input('prod-rule-delayAlertHours','延期预警阈值（小时）',r.delayAlertHours,'number','min="0" max="744"')+
      input('prod-rule-shortageAlertWafers','产品缺口预警阈值（wafer）',r.shortageAlertWafers,'number','min="1"')+'</div><p>换针卡固定 5 分钟，切换同时发生时相加。静置固定 336 小时，不阻塞后续。月度完成按最后一道加工结束。</p>'+btn('rules','保存规则','primary')+'<p class="config-hint">保存规则后需要重新试排。定时检查由服务端执行，只提示变化，不自动运行排产。</p><p>最近检查：'+(state.lastCheck?fmt(state.lastCheck.time)+' · '+esc(state.lastCheck.message):'尚未到检查时刻')+'</p></div></div>'+
      '<div class="card"><div class="card-head"><h3>MES 数据接入</h3><span class="pill amber">字段约定 / 待联调</span></div><div class="card-body"><p>接收完整快照，实际调用方式可后续替换。需要需求、Lot 状态、资源、路线、Recipe、PM、借机及外部占用。稳定标识未确认时标记 identityStable=false。</p><details class="config-details"><summary>粘贴快照进行接收验证</summary><p>与演示数据使用同一校验规则。接收成功会更新工作台数据；不会改写 Excel 配置。请在 JSON 中明确 source=mes 或 demo。</p><textarea id="prod-snapshot" class="input prod-json" aria-label="MES 快照 JSON">'+esc(snapshotDraft)+'</textarea>'+btn('load-json','填入当前快照示例')+btn('snapshot','校验并接收快照','primary')+'</details></div></div>';
  }
  function replan() {
    var tabs={trial:'试排与比较',events:'变化记录',settings:'生产规则与 MES'};
    return '<div class="config-tabs" role="tablist">'+Object.keys(tabs).map(function(k){return '<button role="tab" aria-selected="'+(tab===k)+'" class="btn '+(tab===k?'primary':'ghost')+'" data-prod-tab="'+k+'">'+tabs[k]+'</button>';}).join('')+'</div>'+(tab==='trial'?trialHTML():tab==='events'?eventsHTML():settingsHTML())+
      '<details class="config-details"><summary>方案与比较记录 · '+state.history.length+' 份</summary>'+btn('legacy-history','旧版历史档案')+table(['保存时间','来源','场景','说明','操作'],state.history.map(function(h){return '<tr>'+[fmt(h.time),sourceNames[h.source]||h.source,h.scenario==='actual'?'实际资源':'扩产假设',h.reason].map(cell).join('')+'<td><button class="prod-link" data-prod-history="'+esc(h.id)+'">打开记录</button></td></tr>';}))+'</details>';
  }
  function render() {
    if(!A.isProductionView())return;
    A.updateProductionShell(true);
    var root=A.$('#view-'+A.state.view); if(!root)return;
    var html=header();
    if(!state)html+='<div class="empty-state">'+(loading?'正在读取生产工作台…':'暂时无法读取，请刷新重试。')+'</div>';
    else if(!state.snapshot) html+='<div class="empty-state"><b>等待生产数据</b><p>'+(state.selectedSource==='mes'?'请在下方接收 MES 完整快照。当前来源尚无选用方案。':state.selectedSource==='excel'?'请检查数据配置，再接收已保存的 Excel。':'选择上方场景并点击“载入所选场景”，再进入滚动重排；也可在下方接收 MES 完整快照。')+'</p></div>'+settingsHTML();
    else if(A.state.view==='gantt'){
      if(state.chart){
        if(chartPlanId!==state.chart.planId){A.state.gantt.anchor=S.parseDate(currentPlan().asOf);A.state.gantt.platform='all';chartPlanId=state.chart.planId;}
        A.renderGantt();return;
      }
      html+='<div class="empty-state"><b>尚未选用方案</b><p>先在滚动重排完成试排并选用，甘特图将与生产看板、MPS 和 Lot 查询同步。</p>'+btn('goto-trial','前往试排')+'</div>';
    }
    else html+=A.state.view==='production'?overview():A.state.view==='mps'?mpsHTML():A.state.view==='history'?historyHTML():A.state.view==='tracking'?tracking():replan();
    root.innerHTML=html;
  }
  A.updateProductionShell=function (active) {
    var d=state&&state.snapshot;
    function text(id,value){var el=A.$(id);if(el)el.textContent=value;}
    text('#btn-run','试排与选用');
    text('#btn-settings',active?'生产规则':'排产规则');
    var importButton=A.$('#btn-import');if(importButton)importButton.hidden=active;
    if(!active){A.updateSourceCard();A.updateRunChip();return;}
    text('#source-name',d?(sourceNames[d.source]+' 生产数据'):'等待生产数据');
    text('#source-meta',d?d.month+' · '+d.lots.length+' Lot':'统一生产工作区');
    text('#source-status',d?'数据时点 '+fmt(d.asOf):'尚未接收快照');
    text('#source-note',!d?'可载入演示或接收 MES 快照':d.source==='demo'?'合成数据，仅用于功能验证':d.source==='excel'?'已保存 Excel 接入统一工作区':'已接收快照，不主动拉取 MES');
    text('#run-chip',job&&job.status==='running'?'正在试排':state&&state.changePending?'等待试排与选用':currentPlan()?'已有选用方案':'尚未试排');
  };
  A.renderProduction=function () { render(); if(!state&&!loading)refresh(); };
  A.startProductionTrial=function () { A.navigate('replanning'); tab='trial'; render(); };
  A.openProductionSettings=function () { tab='settings'; A.navigate('replanning'); };
  document.addEventListener('input',function(event){
    var id=event.target.id||'';
    if(id==='prod-search')query=event.target.value;
    if(id==='prod-mps-search')mpsQuery=event.target.value;
    if(id.startsWith('prod-excel-'))excelDraft[id.slice(11)]=event.target.type==='checkbox'?event.target.checked:event.target.value;
    if(id==='prod-adopt-reason')reason=event.target.value;
    if(id==='prod-snapshot')snapshotDraft=event.target.value;
    if(id.startsWith('prod-rule-')) { draftRules=Object.assign({},draftRules||state.rules); draftRules[id.slice(10)]=event.target.type==='number'?Number(event.target.value):event.target.value; }
    if(event.target.dataset.prodExtra)extras[event.target.dataset.prodExtra]=Number(event.target.value);
  });
  document.addEventListener('change',function(event){
    if(event.target.id==='prod-demo-case'){demoChoice=event.target.value;render();}
    if(event.target.id==='prod-demo-event')presetChoice=event.target.value;
    if(event.target.id==='prod-state'){status=event.target.value;page=1;render();}
    if(event.target.id==='prod-product'){product=event.target.value;page=1;render();}
    if(event.target.id==='prod-scenario')scene=event.target.value;
  });
  document.addEventListener('click',async function(event){
    var lot=event.target.closest('[data-prod-lot]'), p=event.target.closest('[data-prod-product]'), t=event.target.closest('[data-prod-tab]');
    if(lot){openLot(lot.dataset.prodLot);return;}
    if(p){product=p.dataset.prodProduct;query='';page=1;A.navigate('tracking');return;}
    if(t){tab=t.dataset.prodTab;render();return;}
    var history=event.target.closest('[data-prod-history]');
    if(history){try{var saved=await request('/plans/'+history.dataset.prodHistory);A.openDrawer('PLAN RECORD','方案与比较记录',comparison(saved,true));}catch(error){failure=error.message;render();}return;}
    var target=event.target.closest('[data-prod-action]'); if(!target||busy)return;
    var action=target.dataset.prodAction;
    if(action==='goto-trial'){tab='trial';A.navigate('replanning');return;}
    if(action==='mps-search'){render();return;}
    if(action==='legacy-history'){A.state.legacyHistory=true;A.navigate('history');return;}
    if(action==='search'){page=1;render();return;}
    if(action==='prev'||action==='next'){page=Math.max(1,page+(action==='prev'?-1:1));render();return;}
    if(action==='refresh'){await refresh();return;}
    if(action==='load-json'){if(!state.snapshot){failure='请先载入演示数据以查看完整示例，或直接粘贴 MES 快照。';}else snapshotDraft=JSON.stringify(state.snapshot,null,2);render();return;}
    busy=true;failure='';
    try {
      if(action.startsWith('source-')){
        A.closeDrawer();
        state=await request('/source',{source:action.slice(7)});job=null;draftRules=null;reason='';excelDraft={};chartPlanId=null;
        if(action==='source-excel'&&!state.snapshot)state=await request('/excel',{});
        tab=action==='source-mes'?'settings':'trial';
        A.navigate(action==='source-mes'?'replanning':'production');
      }
      if(action==='excel'){
        ['asOf','defaultAvailableAt','defaultShipDue','confirmPlannedLots','identityStable'].forEach(function(k){var el=A.$('#prod-excel-'+k);excelDraft[k]=el.type==='checkbox'?el.checked:el.value;});
        state=await request('/excel',excelDraft);excelDraft={};job=null;chartPlanId=null;
      }
      if(action==='demo'){
        state=await request('/demo',{case:demoChoice});job=null;draftRules=null;reason='';scene='actual';tab='trial';snapshotDraft='';validationReport=null;
        extras=Object.assign({},state.snapshot.demoCase.suggestedExpansion||{});query='';product='all';status='all';page=1;
      }
      if(action==='demo-check')validationReport=await request('/demo-check',{});
      if(action==='demo-event'){state=await request('/demo-event',{event:presetChoice});A.toast('变化已保存，请重新试排比较');}
      if(action==='rules'){state=await request('/rules',draftRules||state.rules);draftRules=null;}
      if(action==='snapshot'){state=await request('/snapshot',JSON.parse(snapshotDraft));}
      if(action==='trial'){
        if(Object.keys(excelDraft).length)throw new Error('Excel 补充信息尚未接收，请先点击“接收已保存 Excel 与补充信息”');
        if(A.hasConfigurationChanges&&A.hasConfigurationChanges())throw new Error('Excel 配置有未保存修改，请先保存或放弃草稿');
        if(draftRules)throw new Error('请先保存生产规则，再开始试排');
        job=(await request('/trial',{scenario:scene,extraMachines:extras})).job;schedulePoll();
      }
      if(action==='cancel'){job=(await request('/cancel',{id:job.id})).job;schedulePoll();}
      if(action==='adopt'){
        if(Object.keys(excelDraft).length)throw new Error('Excel 补充信息尚未接收，请先处理后重新试排');
        if(A.hasConfigurationChanges&&A.hasConfigurationChanges())throw new Error('Excel 配置有未保存修改，请先处理后重新试排');
        if(draftRules)throw new Error('有尚未保存的生产规则，请先处理');
        state=await request('/adopt',{id:job&&job.id,reason:adoptReason()});A.toast('方案已选用，已保存在本程序');
      }
      if(action==='archive'){state=await request('/archive',{id:job&&job.id});A.toast('比较记录已保存');}
      if(action==='event'){
        var value=A.$('#prod-event-time').value;
        state=await request('/event',{kind:A.$('#prod-event-kind').value,uid:A.$('#prod-event-lot').value,eqp:A.$('#prod-event-eqp').value,time:value?S.isoDate(S.parseDate(value)):null,reason:A.$('#prod-event-reason').value});
      }
    } catch(error){failure=error.message;}
    finally{busy=false;render();}
  });
})();
