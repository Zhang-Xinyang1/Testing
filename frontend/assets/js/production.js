/* Production workspace: explicit facts, isolated scenarios and manual adoption. */
(function () {
  'use strict';
  var A = window.TSApp, esc = A.esc, S = A.SCHED;
  var state = null, job = null, loading = false, busy = false, failure = '', poll = null;
  var tab = 'trial', query = '', status = 'all', product = 'all', page = 1;
  var scene = 'actual', extras = {}, reason = '', draftRules = null, snapshotDraft = '';
  var stateNames = {READY:'已到位',FUTURE:'预计来料',HOLD:'HOLD',RUNNING:'在制 · 外部接续',DONE:'实际完工'};
  var views = {production:['生产看板','实际完成、预测产出与交期风险'],
    tracking:['Lot 查询','当前位置、跨机台路线与静置信息'],
    replanning:['滚动重排','数据变化 → 试排 → 比较 → 人工选用']};
  Object.keys(views).forEach(function (key) { A.TITLES[key] = views[key]; });
  A.isProductionView = function () { return !!views[A.state.view]; };
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
      var results = await Promise.all([request(''),request('/job')]);
      state=results[0]; job=results[1].job; failure='';
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
    return '<div class="source-hero"><div><div class="source-title">'+(d?(d.source==='demo'?'模拟生产数据':'MES 接收快照'):'等待生产数据')+'</div><div class="source-sub">'+(d?esc(d.month)+' · 数据时点 '+fmt(d.asOf)+' · '+d.lots.length+' Lot':'可先载入模拟数据，验证完整操作流程。')+'</div></div><div class="source-actions">'+btn('refresh','刷新状态')+(!d||d.source==='demo'?btn('demo',d?'重置演示数据':'载入演示数据','primary'):'')+'</div></div>'+
      (d?'<div class="note-card '+(d.source==='demo'?'prod-demo':'')+'">'+(d.source==='demo'?'演示模式：全部为合成数据，选用仅保存在独立演示工作区。':'当前使用已接收的 MES 快照；尚未配置 MES 主动拉取。')+' '+(!d.identityStable?'批次稳定标识待确认，跨快照冻结与替换计划受限。':'批次使用本数据源提供的稳定标识。')+'</div>':'')+
      (failure?'<div class="error-box" role="alert">'+esc(failure)+'</div>':'');
  }
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
    html+='<div class="card"><div class="card-head"><h3>产品达成与三种差额</h3>'+btn('goto-trial','进入滚动重排','primary')+'</div><div class="card-body"><p class="config-hint">差额 = 前者减后者；负数表示不足。无基线显示“无基线”。本月按最后一道加工结束统计，静置期不延后产出。</p>'+table(['PPN','MPS / wafer','实际完成','预计完成','实际 − 原计划截至当前','新预测 − 原预测','预测 − 月目标','外部未知 / wafer'],products.map(function(p){return '<tr><td><button class="prod-link" data-prod-product="'+esc(p.ppn)+'">'+esc(p.ppn)+'</button></td>'+[p.target,p.actual,p.forecast==null?'待试排':p.forecast,delta(p.actualPlanDelta),delta(p.forecastDelta),p.forecast==null?'待试排':delta(p.targetDelta),p.externalUnknownWafers||0].map(cell).join('')+'</tr>';}))+'</div></div>';
    var alerts=plan?plan.alerts:[];
    html+='<div class="card"><div class="card-head"><h3>业务预警 · '+alerts.length+' 条</h3></div><div class="card-body">'+table(['类型','产品','说明'],alerts.map(function(a){return '<tr>'+cell(a.kind)+cell(a.ppn)+'<td>'+esc(a.message)+(a.uid?' <button class="prod-link" data-prod-lot="'+esc(a.uid)+'">查看 Lot</button>':'')+'</td></tr>';}))+'</div></div>';
    return html;
  }
  function allLots() {
    var plan=currentPlan(), mapped={};
    if (plan) plan.lots.forEach(function(l){mapped[l.uid]=l;});
    return state.snapshot.lots.map(function(l){return Object.assign({},mapped[l.uid]||{},l,{tasks:(mapped[l.uid]||{}).tasks||[],plannedComplete:(mapped[l.uid]||{}).complete});});
  }
  function tracking() {
    var d=state.snapshot, rows=allLots().filter(function(l){return (status==='all'||l.state===status)&&(product==='all'||l.ppn===product)&&(!query||(l.lotId+' '+l.uid+' '+l.ppn).toLowerCase().includes(query.toLowerCase()));});
    var pages=Math.max(1,Math.ceil(rows.length/30)); page=Math.min(page,pages);
    return '<div class="card"><div class="card-body"><div class="prod-toolbar">'+input('prod-search','搜索 Lot / PPN / 编号',query)+
      '<label class="config-field">状态<select id="prod-state" class="select"><option value="all">全部状态</option>'+Object.keys(stateNames).map(function(k){return '<option value="'+k+'"'+(status===k?' selected':'')+'>'+stateNames[k]+'</option>';}).join('')+'</select></label><label class="config-field">产品<select id="prod-product" class="select"><option value="all">全部产品</option>'+d.demands.map(function(p){return '<option'+(product===p.ppn?' selected':'')+'>'+esc(p.ppn)+'</option>';}).join('')+'</select></label>'+btn('search','查询')+'</div><p class="config-hint">实际位置来自快照；任务路线来自已选方案。静置倒计时以快照时点为准。'+(state.changePending?'输入已变化，已有计划可能需要更新。':'')+'</p>'+table(['LotID','PPN / wafer','状态','实际位置','可用时间','内部出货日','计划完工','操作'],rows.slice((page-1)*30,page*30).map(function(l){return '<tr><td>'+lotLink(l)+'</td>'+cell(l.ppn+' / '+l.wafer)+cell(stateNames[l.state])+cell(l.location||'待提供')+cell(fmt(l.availableAt))+cell(fmt(l.shipDue))+cell(fmt(l.plannedComplete))+'<td><button class="prod-link" data-prod-lot="'+esc(l.uid)+'">路线与静置</button></td></tr>';}))+'<div class="prod-pagination">'+btn('prev','上一页')+'<span>第 '+page+' / '+pages+' 页 · '+rows.length+' 条</span>'+btn('next','下一页')+'</div></div></div>';
  }
  function openLot(uid) {
    var lot=allLots().find(function(l){return l.uid===uid;}); if(!lot)return;
    var complete=lot.state==='DONE'?S.parseDate(lot.actualComplete):lot.plannedComplete;
    var restEnd=complete==null?null:complete+336*A.HOUR, remaining=restEnd==null?null:Math.max(0,(restEnd-S.parseDate(state.snapshot.asOf))/A.HOUR);
    var restStatus=lot.state!=='DONE'?'尚未确认开始，待最后加工实际完成':remaining>0?'剩余 '+remaining.toFixed(1)+' 小时':'已达到 336 小时';
    var html='<div class="prod-detail"><span class="pill">'+esc(stateNames[lot.state])+'</span><p>'+esc(lot.ppn)+' · '+lot.wafer+' wafer · '+esc(lot.uid)+'</p><div class="kv"><dt>实际位置</dt><dd>'+esc(lot.location||'待提供')+'</dd><dt>内部计划出货</dt><dd>'+fmt(lot.shipDue)+'</dd><dt>'+(lot.state==='DONE'?'实际':'计划')+'加工完工</dt><dd>'+fmt(complete)+'</dd><dt>'+(lot.state==='DONE'?'静置结束':'预计静置结束')+'</dt><dd>'+fmt(restEnd)+'</dd><dt>静置状态</dt><dd>'+restStatus+'</dd></div><p class="note-card">静置 14×24 小时是独立记录，不阻塞后续工序，不延后 MPS 完成统计。</p>';
    if (lot.state==='RUNNING') html+='<div class="note-card">当前 '+esc(lot.current.step)+' · '+esc(lot.current.eqp)+'<br>'+fmt(lot.current.start)+' → '+fmt(lot.current.end)+'<br>本系统仅保留当前占用；后续由外部系统负责。最终完工预测：'+fmt(lot.externalComplete)+'</div>';
    html+='<h3>计划跨机台路线</h3><div class="prod-route">'+lot.tasks.map(function(t){return '<div><b>'+esc(t.step)+'</b><span>'+esc(t.machine||t.chamber||'等待')+'</span><small>'+fmt(t.start)+' → '+fmt(t.end)+'</small></div>';}).join('')+'</div><p class="config-hint">BAKE 任务开始包含升温，Lot 放行后仍有降温与换 Lot 占腔。</p>'+table(['工序','资源','Recipe','占用开始','任务开始','Lot 放行','资源释放','切换'],lot.tasks.map(function(t){return '<tr>'+[t.step,t.machine||t.chamber||'工艺等待',t.recipe,fmt(t.blockStart||t.start),fmt(t.start),fmt(t.end),fmt(t.resourceEnd||t.end),t.machine?'Recipe '+((t.recipeSetup||0)/60000)+' 分 + 针卡 '+((t.cardSetup||0)/60000)+' 分':'—'].map(cell).join('')+'</tr>';}));
    if (!lot.tasks.length) html+='<p>'+esc(lot.reason||'当前没有本系统安排的后续任务。')+'</p>';
    html+='</div>'; A.openDrawer('LOT TRACKING',lot.lotId,html);
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
      '<h3>任务影响 · '+plan.changes.length+' 个 Lot</h3>'+(!plan.identityStable?'<p class="workflow-error">批次稳定标识待确认，无法可靠逐 Lot 对比。</p>':'')+
      table(['Lot','变化','原完工','新完工'],plan.changes.map(function(c){return '<tr>'+[c.lotId,c.kind,fmt(c.before),fmt(c.after)].map(cell).join('')+'</tr>';}))+
      '<h3>借机开始时 Recipe 检查</h3>'+table(['机台','借机开始','要求 Recipe','当时 Recipe','检查'],plan.borrows.map(function(b){return '<tr>'+[b.eqp,fmt(b.start),b.requiredRecipe||b.req.recipe,b.recipeAtStart||'未取得',b.recipeMatched?'匹配':b.reason||'未匹配'].map(cell).join('')+'</tr>';}))+
      '<details class="config-details"><summary>查看候选任务时间与资源</summary>'+table(['Lot','工序','资源','占用开始','任务开始','Lot 放行','资源释放'],plan.lots.flatMap(function(l){return l.tasks.map(function(t){return '<tr>'+[l.lotId,t.step,t.machine||t.chamber||'等待',fmt(t.blockStart||t.start),fmt(t.start),fmt(t.end),fmt(t.resourceEnd||t.end)].map(cell).join('')+'</tr>'; }); }))+'</details>'+
      (historical?'<p class="note-card">历史只读记录，不会覆盖当前方案。</p>':adopted?'<p class="note-card">此方案已经选用，当前未接入 MES 下发。</p>':btn('archive','保存比较记录')+(stale?'<p class="workflow-error">数据、规则或已选方案已变化，此候选仅供查看，请重新试排。</p>':plan.scenario==='actual'?'<div class="prod-toolbar">'+input('prod-adopt-reason','选用说明',reason)+btn('adopt','选用此方案','primary')+'</div><p class="config-hint">选用保存至本程序；当前未接入 MES 下发。</p>':'<p class="note-card">这是扩产假设结果，不能选为实际资源计划。</p>'))+'</div></div>';
  }
  function trialHTML() {
    var d=state.snapshot, types=Array.from(new Set(d.equipment.filter(function(e){return !e.type.includes('OVEN');}).map(function(e){return e.type;})));
    return '<div class="card"><div class="card-body"><div class="prod-toolbar"><label class="config-field">资源场景<select id="prod-scenario" class="select"><option value="actual"'+(scene==='actual'?' selected':'')+'>实际资源</option><option value="expansion"'+(scene==='expansion'?' selected':'')+'>假设扩产</option></select></label>'+types.map(function(t){return input('prod-extra-'+t,'新增 '+esc(t)+' 台数（仅扩产）',extras[t]||0,'number','min="0" max="20" data-prod-extra="'+esc(t)+'"');}).join('')+btn('trial','开始试排','primary')+'</div><p>重排时点：'+fmt(d.asOf)+'；计划月份：'+esc(d.month)+'；冻结窗口：'+state.rules.freezeHours+' 小时。</p><p class="config-hint">当前工序的在制占用始终保留。冻结窗口覆盖的计划将按整 Lot 剩余路线保留，并连带保留同一 BAKE 批次成员，避免拆散已有批次。冻结含过去任务但缺少执行事实时，会要求更新快照。</p></div></div>'+jobHTML()+comparison(job&&job.plan);
  }
  function eventsHTML() {
    var d=state.snapshot;
    return '<div class="card"><div class="card-head"><h3>数据与规则变化记录</h3></div><div class="card-body">'+table(['时间','类型','已知原因'],state.events.map(function(e){return '<tr>'+[fmt(e.time),e.kind,e.reason].map(cell).join('')+'</tr>';}))+'<p class="config-hint">记录事件事实；多种变化同时发生时，不将全部产出差额直接归因于某一事件。</p></div></div>'+
      (d.source==='demo'?'<div class="card"><div class="card-head"><h3>模拟生产变化</h3></div><div class="card-body"><div class="config-grid"><label class="config-field">事件<select class="select" id="prod-event-kind"><option value="arrival">来料时间变化</option><option value="due">内部出货日变化</option><option value="down">设备停机</option></select></label><label class="config-field">Lot<select id="prod-event-lot" class="select">'+d.lots.filter(function(l){return ['READY','FUTURE'].includes(l.state);}).map(function(l){return '<option value="'+esc(l.uid)+'">'+esc(l.lotId)+'</option>';}).join('')+'</select></label><label class="config-field">设备（停机事件）<select id="prod-event-eqp" class="select">'+d.equipment.filter(function(e){return !e.type.includes('OVEN');}).map(function(e){return '<option>'+esc(e.id)+'</option>';}).join('')+'</select></label>'+input('prod-event-time','新的可用/出货时间；停机填写结束时间',dateValue(d.asOf),'datetime-local')+input('prod-event-reason','变更原因','')+'</div>'+btn('event','保存模拟变化')+'</div></div>':'');
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
      '<details class="config-details"><summary>方案与比较记录 · '+state.history.length+' 份</summary>'+table(['保存时间','来源','场景','说明','操作'],state.history.map(function(h){return '<tr>'+[fmt(h.time),h.source==='demo'?'模拟':'MES',h.scenario==='actual'?'实际资源':'扩产假设',h.reason].map(cell).join('')+'<td><button class="prod-link" data-prod-history="'+esc(h.id)+'">打开记录</button></td></tr>';}))+'</details>';
  }
  function render() {
    if(!A.isProductionView())return;
    A.updateProductionShell(true);
    var root=A.$('#view-'+A.state.view); if(!root)return;
    var html=header();
    if(!state)html+='<div class="empty-state">'+(loading?'正在读取生产工作台…':'暂时无法读取，请刷新重试。')+'</div>';
    else if(!state.snapshot) html+='<div class="empty-state"><b>先验证生产流程</b><p>点击“载入演示数据”，再进入滚动重排；也可在下方接收 MES 完整快照。</p></div>'+settingsHTML();
    else html+=A.state.view==='production'?overview():A.state.view==='tracking'?tracking():replan();
    root.innerHTML=html;
  }
  A.updateProductionShell=function (active) {
    var d=state&&state.snapshot;
    function text(id,value){var el=A.$(id);if(el)el.textContent=value;}
    text('#btn-run',active?'试排与选用':'重新排产');
    text('#btn-settings',active?'生产规则':'排产规则');
    var importButton=A.$('#btn-import');if(importButton)importButton.hidden=active;
    if(!active){A.updateSourceCard();A.updateRunChip();return;}
    text('#source-name',d?(d.source==='demo'?'模拟生产快照':'MES 生产快照'):'等待生产数据');
    text('#source-meta',d?d.month+' · '+d.lots.length+' Lot':'使用独立生产工作区');
    text('#source-status',d?'数据时点 '+fmt(d.asOf):'尚未接收快照');
    text('#source-note',d&&d.source==='demo'?'合成数据，仅用于功能验证':'已接收快照，不主动拉取 MES');
    text('#run-chip',job&&job.status==='running'?'正在试排':state&&state.changePending?'等待试排与选用':currentPlan()?'已有选用方案':'尚未试排');
  };
  A.renderProduction=function () { render(); if(!state&&!loading)refresh(); };
  A.startProductionTrial=function () { A.navigate('replanning'); tab='trial'; render(); };
  A.openProductionSettings=function () { tab='settings'; A.navigate('replanning'); };
  document.addEventListener('input',function(event){
    var id=event.target.id||'';
    if(id==='prod-search')query=event.target.value;
    if(id==='prod-adopt-reason')reason=event.target.value;
    if(id==='prod-snapshot')snapshotDraft=event.target.value;
    if(id.startsWith('prod-rule-')) { draftRules=Object.assign({},draftRules||state.rules); draftRules[id.slice(10)]=event.target.type==='number'?Number(event.target.value):event.target.value; }
    if(event.target.dataset.prodExtra)extras[event.target.dataset.prodExtra]=Number(event.target.value);
  });
  document.addEventListener('change',function(event){
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
    if(action==='search'){page=1;render();return;}
    if(action==='prev'||action==='next'){page=Math.max(1,page+(action==='prev'?-1:1));render();return;}
    if(action==='refresh'){await refresh();return;}
    if(action==='load-json'){if(!state.snapshot){failure='请先载入演示数据以查看完整示例，或直接粘贴 MES 快照。';}else snapshotDraft=JSON.stringify(state.snapshot,null,2);render();return;}
    busy=true;failure='';
    try {
      if(action==='demo'){state=await request('/demo',{});job=null;draftRules=null;}
      if(action==='rules'){state=await request('/rules',draftRules||state.rules);draftRules=null;}
      if(action==='snapshot'){state=await request('/snapshot',JSON.parse(snapshotDraft));}
      if(action==='trial'){
        if(draftRules)throw new Error('请先保存生产规则，再开始试排');
        job=(await request('/trial',{scenario:scene,extraMachines:extras})).job;schedulePoll();
      }
      if(action==='cancel'){job=(await request('/cancel',{id:job.id})).job;schedulePoll();}
      if(action==='adopt'){
        if(draftRules)throw new Error('有尚未保存的生产规则，请先处理');
        state=await request('/adopt',{id:job&&job.id,reason:reason});A.toast('方案已选用，已保存在本程序');
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
