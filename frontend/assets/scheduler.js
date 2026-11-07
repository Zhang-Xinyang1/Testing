/* Confirmed business rules: arbitrary ordered routes, card types without stock limits,
 * event-based resource dispatch, four-lot BAKE batches and product attainment fairness. */
(function (global) {
  'use strict';
  var MIN=60000,HOUR=3600000,DAY=86400000;
  function parseDate(v){if(!v)return NaN;v=String(v);return Date.parse(v.indexOf('T')>=0?v:v+'T00:00:00+08:00');}
  function pad(n){return String(n).padStart(2,'0');}
  function fmt(v,withDate){if(!isFinite(v))return '—';var d=new Date(v+8*HOUR);return (withDate?(d.getUTCMonth()+1)+'/'+d.getUTCDate()+' ':'')+pad(d.getUTCHours())+':'+pad(d.getUTCMinutes());}
  function dayKey(v){var d=new Date(v+8*HOUR);return d.getUTCFullYear()+'-'+pad(d.getUTCMonth()+1)+'-'+pad(d.getUTCDate());}
  function wafers(a){return sum(a,function(l){return l.wafer;});}
  function sum(a,f){return a.reduce(function(s,v,i){return s+f(v,i);},0);}
  function clamp(v,a,b){return Math.max(a,Math.min(b,v));}
  function groups(v){return String(v||'').split(/[;,，；/|]+/).map(function(s){return s.trim();}).filter(Boolean);}
  function gap(blocks,from,duration){var t=from;blocks.slice().sort(function(a,b){return a[0]-b[0];}).forEach(function(iv){if(t<iv[1]&&t+duration>iv[0])t=iv[1];});return t;}
  function earliestSlot(r,from,duration){return gap((r.pm||[]).concat(r.reserved||[]),Math.max(from,r.free),duration);}
  function Heap(){this.a=[];this.seq=0;}
  Heap.prototype.push=function(t,lot){var v={t:t,lot:lot,seq:this.seq++},a=this.a,i=a.length;a.push(v);while(i){var p=(i-1)>>1;if(a[p].t<v.t||(a[p].t===v.t&&a[p].seq<v.seq))break;a[i]=a[p];i=p;}a[i]=v;};
  Heap.prototype.pop=function(){var a=this.a,v=a[0],z=a.pop();if(a.length){var i=0;while(i*2+1<a.length){var c=i*2+1;if(c+1<a.length&&(a[c+1].t<a[c].t||(a[c+1].t===a[c].t&&a[c+1].seq<a[c].seq)))c++;if(z.t<a[c].t||(z.t===a[c].t&&z.seq<a[c].seq))break;a[i]=a[c];i=c;}a[i]=z;}return v;};
  function dataRunWarnings(){return [];}
  function simulate(data,options){
    var start=parseDate(data.meta.start),monthEnd=start+data.meta.days*DAY;
    var ctx={data:data,options:options,start:start,monthEnd:monthEnd,horizonEnd:monthEnd,horizonDays:data.meta.days,weekMeta:{},weekEndByProd:{},prodMps:{},prodPlatform:{},platformMps:{},eqpByType:{},machineMap:{},machines:[],chambers:[],cards:[],cardMap:{},recipeCards:{},lots:[],batches:[],pmRecords:[],borrows:[],wip:data.wip||[]};
    data.mps.forEach(function(m){ctx.prodMps[m.ppn]=m.mps;ctx.prodPlatform[m.ppn]=m.platform;ctx.platformMps[m.platform]=(ctx.platformMps[m.platform]||0)+m.mps;});
    var cursor=start,wk=0;while(cursor<monthEnd){wk++;var dow=new Date(cursor+8*HOUR).getUTCDay(),stop=Math.min(monthEnd,cursor+((7-dow)%7+1)*DAY);ctx.weekMeta['W'+wk]={week:'W'+wk,start:cursor,end:stop};cursor=stop;}
    (data.weekly||[]).forEach(function(w){ctx.weekMeta[w.week]={week:w.week,start:parseDate(w.start),end:parseDate(w.end)+DAY};});
    var cpInput=data.eqp.filter(function(e){return !/OVEN/i.test(e.type);});
    cpInput.forEach(function(e,i){var r={kind:'cp',id:e.id,eqp:e.id,type:e.type,index:i,initialCard:e.card,card:e.card,initialRecipe:e.recipe,free:start,pm:[],reserved:[],tasks:[],borrows:[],intervals:[],wip:[],prodMs:0,setupMs:0,setupCount:0,borrowMs:0,pmMs:0,available:e.status==='RUN'};ctx.machines.push(r);ctx.machineMap[r.id]=r;(ctx.eqpByType[r.type]=ctx.eqpByType[r.type]||[]).push(r);});
    var ovens=data.eqp.filter(function(e){return /OVEN/i.test(e.type);});
    var ovenIds=ovens.length?ovens.map(function(e){return e.id;}):((data.bakeResources||{}).ids||Array.from({length:(data.bakeResources||{}).machines||7},function(_,i){return 'BAKE-'+pad(i+1);}));
    ovenIds.forEach(function(id){for(var i=1;i<=((data.bakeResources||{}).chambers||4);i++)ctx.chambers.push({kind:'bake',id:id+'-C'+i,eqp:id,free:start,pm:[],reserved:[],batches:[],wip:[],prodMs:0,pmMs:0,available:!ovens.length||ovens.some(function(e){return e.id===id&&e.status==='RUN';})});});
    ctx.wip.forEach(function(w){var rs=w.chamber?ctx.chambers.filter(function(c){return c.id===w.chamber;}):[ctx.machineMap[w.eqp]].filter(Boolean);rs.forEach(function(r){r.reserved.push([parseDate(w.start),parseDate(w.end)]);r.wip.push(w);});});
    data.recipes.forEach(function(r){ctx.recipeCards[r.recipe]=r.cards.slice();r.cards.forEach(function(id){if(!ctx.cardMap[id]){var c={id:id,type:'共享类型',free:start,tasks:[],busyMs:0,moves:0,recipes:{}};ctx.cards.push(c);ctx.cardMap[id]=c;}ctx.cardMap[id].recipes[r.recipe]=true;});});
    (data.pm||[]).forEach(function(p,i){var rs=ctx.machineMap[p.eqp]?[ctx.machineMap[p.eqp]]:ctx.chambers.filter(function(c){return c.eqp===p.eqp;});if(!rs.length)throw Error('PM 未知设备：'+p.eqp);var blocks=[];rs.forEach(function(r){blocks=blocks.concat(r.reserved,r.pm);});var duration=p.hours*HOUR,first=p.start?parseDate(p.start):parseDate(p.early),s=gap(blocks,first,duration);if(s+duration>parseDate(p.over)+DAY||(p.start&&s!==first))throw Error('PM 窗口内无连续空档：'+p.eqp);var rec={id:'PM-'+pad(i+1),eqp:p.eqp,early:p.early,due:p.due,over:p.over,hours:p.hours,start:s,end:s+duration,status:'window',resources:rs.map(function(r){return r.id;})};ctx.pmRecords.push(rec);rs.forEach(function(r){r.pm.push([s,rec.end]);r.pmMs+=duration;});});
    (data.borrow||[]).slice().sort(function(a,b){return parseDate(a.date||a.start)-parseDate(b.date||b.start);}).forEach(function(b,i){var m=ctx.machineMap[b.eqp],duration=b.hours*HOUR,day=parseDate(b.date||dayKey(parseDate(b.start))),first=b.start?parseDate(b.start):day,latest=b.start?first:day+DAY-1;var s=m&&m.available?gap(m.pm.concat(m.reserved),first,duration):Infinity;var rec={id:'BR-'+pad(i+1),eqp:b.eqp,hours:b.hours,req:b,duration:duration,delay:0};if(s<=latest){rec.start=s;rec.end=s+duration;rec.status='on-time';rec.reserved=true;m.reserved.push([s,rec.end]);m.borrows.push(rec);m.borrowMs+=duration;}else{rec.status='cancel';rec.reason='原机台当天无连续空档，保留原计划并建议最早可用时间';if(isFinite(s))rec.suggestion={eqp:b.eqp,start:s,end:s+duration};}ctx.borrows.push(rec);});
    var excluded=new Set(ctx.wip.map(function(w){return w.lotId;})),ord={},events=new Heap(),cpQueues={},bakeQueues={},done={},waitingMs=(options.bakeWindowHours==null?4:options.bakeWindowHours)*HOUR;
    function fair(a,b){return a.fairRank-b.fairRank||a.prod.localeCompare(b.prod)||a.id.localeCompare(b.id);}
    var forbidden={};(data.constraints||[]).forEach(function(c){forbidden[c.eqp+'|'+c.recipe]=true;});
    data.lots.forEach(function(row){if(excluded.has(row[0]))return;var p=row[1],ops=(data.flows[p]||[]).map(function(o){var v=o.slice();if(v[0]==='BAKE'){if(!/^(210|250)C24H$/.test(String(v[1])))throw Error('无效 BAKE Recipe：'+v[1]);v[2]=Number(String(v[1]).match(/C(\d+)H$/)[1]);}if(v[0]==='QTIME1'){if(!/^(7|14|28)$/.test(String(v[1])))throw Error('无效 QTIME Recipe：'+v[1]);v[2]=Number(v[1]);}return v;});var n=ord[p]||0;ord[p]=n+row[3];var l={id:row[0],prod:p,platform:row[2],type:row[2],wafer:row[3],release:row[4],week:row[5],weeklyDue:row[6],monthlyDue:row[7],priority:row[8],ops:ops,cpCount:ops.filter(function(o){return /^CP/.test(o[0]);}).length,cpMinutes:sum(ops.filter(function(o){return /^CP/.test(o[0]);}),function(o){return o[2];}),cpTasks:[],routeTasks:[],bakeTasks:[],cpEnd:null,bakeStart:null,bakeEnd:null,complete:Infinity,monthEnd:monthEnd,weekEnd:parseDate(row[6])+DAY,fairRank:n/Math.max(1,ctx.prodMps[p]),index:0,ready:parseDate(row[4]),status:'pending'};ctx.lots.push(l);var allowed=groups(l.platform);l.allowedTypes=allowed;var bad=ops.find(function(o){return /^CP/.test(o[0])&&(!ctx.recipeCards[o[1]]||!ctx.machines.some(function(m){return m.available&&allowed.indexOf(m.type)>=0&&!forbidden[m.id+'|'+o[1]];}));});if(!ops.length||bad){l.unscheduledReason='路线缺失或没有 RUN/兼容/非禁加工机台';return;}events.push(l.ready,l);});
    function enter(l,t){if(l.index>=l.ops.length){l.complete=t;l.status=t<=monthEnd?'on-time':'late-month';done[l.prod]=(done[l.prod]||0)+(t<=monthEnd?l.wafer:0);return;}var op=l.ops[l.index];l.ready=t;if(op[0]==='QTIME1'){var end=t+op[2]*MIN;l.qtOp=op;l.routeTasks.push({step:op[0],recipe:op[1],start:t,end:end,opIndex:l.index});l.index++;events.push(end,l);}else if(op[0]==='BAKE'){var q=bakeQueues[op[1]]||(bakeQueues[op[1]]=[]);q.push(l);q.sort(fair);}else{var key=l.prod+'|'+l.index;var q=cpQueues[key]||(cpQueues[key]=[]);q.push(l);q.sort(fair);}}
    var now=start,loops=0;
    while(events.a.length||Object.keys(cpQueues).some(function(k){return cpQueues[k].length;})||Object.keys(bakeQueues).some(function(k){return bakeQueues[k].length;})){
      if(++loops>500000)throw Error('调度事件超出保护上限');
      while(events.a.length&&events.a[0].t<=now){var ev=events.pop();enter(ev.lot,ev.t);}
      var next=events.a.length?events.a[0].t:Infinity;
      ctx.machines.forEach(function(m){if(!m.available)return;if(m.free>now){next=Math.min(next,m.free);return;}var best=null,bestCard=null,bestSetup=0;
        Object.keys(cpQueues).forEach(function(k){var q=cpQueues[k];if(!q.length)return;var l=q[0],op=l.ops[l.index];if(l.allowedTypes.indexOf(m.type)<0||forbidden[m.id+'|'+op[1]])return;var cards=ctx.recipeCards[op[1]]||[];if(!cards.length)return;var card=cards.indexOf(m.card)>=0?m.card:cards[0],setup=card===m.card?0:5*MIN,s=earliestSlot(m,now,op[2]*MIN+setup);if(s>now){next=Math.min(next,s);return;}if(!best||fair(l,best)<0||(fair(l,best)===0&&setup<bestSetup)){best=l;bestCard=card;bestSetup=setup;}});
        if(!best)return;var l=best,op=l.ops[l.index],q=cpQueues[l.prod+'|'+l.index];q.shift();var end=now+bestSetup+op[2]*MIN;var task={kind:'cp',lotId:l.id,prod:l.prod,platform:l.platform,wafer:l.wafer,step:op[0],recipe:op[1],duration:op[2]*MIN,setup:bestSetup,blockStart:now,start:now+bestSetup,end:end,machine:m.id,eqp:m.id,type:m.type,card:bestCard,week:l.week,setupChanged:bestSetup>0,opIndex:l.index};m.tasks.push(task);m.intervals.push([now,end]);m.free=end;m.prodMs+=task.duration;m.setupMs+=bestSetup;if(bestSetup)m.setupCount++;m.card=bestCard;l.cpTasks.push(task);l.routeTasks.push(task);l.cpEnd=end;l.index++;events.push(end,l);next=Math.min(next,end);
      });
      ctx.chambers.forEach(function(ch){if(!ch.available)return;if(ch.free>now){next=Math.min(next,ch.free);return;}var bestRecipe=null,best=null;
        Object.keys(bakeQueues).forEach(function(recipe){var q=bakeQueues[recipe];if(!q.length)return;var s=earliestSlot(ch,now,(q[0].ops[q[0].index][2]+4)*HOUR);if(s>now){next=Math.min(next,s);return;}var oldest=Math.min.apply(null,q.map(function(l){return l.ready;}));if(q.length<4&&now<oldest+waitingMs){next=Math.min(next,oldest+waitingMs);return;}if(!best||fair(q[0],best)<0){best=q[0];bestRecipe=recipe;}});
        if(!best)return;var lots=bakeQueues[bestRecipe].splice(0,4),processDuration=best.ops[best.index][2]*HOUR,processStart=now+2*HOUR,processEnd=processStart+processDuration,end=processEnd+2*HOUR;var batch={kind:'bake',id:'B-'+pad(ctx.batches.length+1),recipe:bestRecipe,temperature:Number(bestRecipe.split('C')[0]),lots:lots,wafers:sum(lots,function(l){return l.wafer||0;}),duration:processDuration+4*HOUR,processDuration:processDuration,warmupHours:2,cooldownHours:2,ready:Math.max.apply(null,lots.map(function(l){return l.ready;})),start:now,processStart:processStart,processEnd:processEnd,end:end,release:processEnd,chamber:ch.id,eqp:ch.eqp,week:lots[0].week,platform:lots[0].platform};ctx.batches.push(batch);ch.batches.push(batch);ch.prodMs+=batch.duration;ch.free=end;
        lots.forEach(function(l){l.bakeStart=now;l.bakeEnd=processEnd;l.bakeChamber=ch.id;l.batch=batch;l.bakeTasks.push(batch);l.bakeOp=l.ops[l.index];l.routeTasks.push({step:'BAKE',recipe:bestRecipe,start:now,processStart:processStart,end:processEnd,resourceEnd:end,chamber:ch.id,batchId:batch.id,opIndex:l.index});l.index++;events.push(processEnd,l);});next=Math.min(next,processEnd,end);
      });
      if(events.a.length)next=Math.min(next,events.a[0].t);if(!isFinite(next))break;now=next>now?next:now+1;
    }
    ctx.lots.forEach(function(l){if(!isFinite(l.complete)&&!l.unscheduledReason)l.unscheduledReason='无可用腔体或资源';if(!isFinite(l.complete))l.status='unscheduled';});
    ctx.cpOps=sum(ctx.machines,function(m){return m.tasks.length;});ctx.horizonEnd=Math.max(monthEnd,now);ctx.horizonDays=(ctx.horizonEnd-start)/DAY;
    var result=finalize(ctx);result.schemaVersion='confirmed-2026-11-06';result.productStats=data.mps.map(function(m){var completedLots=ctx.lots.filter(function(l){return l.prod===m.ppn&&l.complete<=monthEnd;}),done=sum(completedLots,function(l){return l.wafer;});return {ppn:m.ppn,platform:m.platform,mps:m.mps,unit:'wafer',completed:done,completedWafers:done,completedLots:completedLots.length,rate:m.mps?done/m.mps:1,gap:m.mps-done};});
    result.metrics.minProductRate=Math.min.apply(null,result.productStats.map(function(p){return p.rate;}));result.metrics.meanProductRate=sum(result.productStats,function(p){return p.rate;})/result.productStats.length;
    result.metrics.cpUtil=sum(ctx.machines,function(m){return sum(m.tasks,function(t){return Math.max(0,Math.min(monthEnd,t.end)-Math.max(start,t.blockStart));});})/(Math.max(1,ctx.machines.length)*(monthEnd-start));
    result.metrics.bakeUtil=sum(ctx.chambers,function(ch){return sum(ch.batches,function(b){return Math.max(0,Math.min(monthEnd,b.end)-Math.max(start,b.start));});})/(Math.max(1,ctx.chambers.length)*(monthEnd-start));return result;
  }
function finalize(ctx) {
    var monthMs = ctx.monthEnd - ctx.start;
    var totalLots = ctx.lots.length;
    var targetWafers = sum(ctx.data.mps,function(m){return m.mps;});
    var completedWafers = wafers(ctx.lots.filter(function(l){return l.complete<=ctx.monthEnd;}));
    var wipIds = new Set(ctx.wip.map(function(w){return w.lotId;}));
    var wipWafers = sum(ctx.data.lots.filter(function(l){return wipIds.has(l[0]);}),function(l){return l[3];});

    ctx.machines.forEach(function (m) {
      m.daily = new Array(ctx.data.meta.days).fill(0);
      m.tasks.forEach(function (t) { splitLoad(m.daily, ctx.start, t.blockStart, t.end); });
    });
    ctx.chambers.forEach(function (ch) {
      ch.daily = new Array(ctx.data.meta.days).fill(0);
      ch.batches.forEach(function (b) { splitLoad(ch.daily, ctx.start, b.start, b.end); });
    });

    var weeklyRows = [];
    var weeklyData = ctx.data.weekly || [];
    var hasWeeklyTargets = !!weeklyData.length;
    var byProdWeek = {};
    weeklyData.forEach(function (w) { byProdWeek[w.ppn + '|' + w.week] = w; });
    var products = Object.keys(ctx.prodMps).sort();
    var weekKeys = Object.keys(ctx.weekMeta).sort(function (a, b) { return ctx.weekMeta[a].start - ctx.weekMeta[b].start; });
    var lastWeekKey = weekKeys[weekKeys.length - 1];
    function completedInWeek(lot, wk) {
      var meta = ctx.weekMeta[wk];
      if (lot.complete < meta.start) return false;
      if (lot.complete < meta.end) return true;
      return wk === lastWeekKey && lot.complete <= ctx.monthEnd;
    }
    if (!hasWeeklyTargets) {
      // No weekly target rows: keep weekly completed quantities only.
      products.forEach(function (p) {
        var pLots = ctx.lots.filter(function (l) { return l.prod === p; });
        var plannedCum = 0;
        weekKeys.forEach(function (wk) {
          var meta = ctx.weekMeta[wk];
          var plannedWeek = wafers(pLots.filter(function (l) {
            return completedInWeek(l, wk);
          }));
          plannedCum += plannedWeek;
          weeklyRows.push({
            platform: ctx.prodPlatform[p], ppn: p, week: wk, start: meta.start, end: meta.end,
            target: 0, targetCum: 0, plannedWeek: plannedWeek, plannedCum: plannedCum,
            gap: plannedCum, attainment: 0, status: 'no-target'
          });
        });
      });
    } else {
      products.forEach(function (p) {
        var cumTarget = 0;
        var pLots = ctx.lots.filter(function (l) { return l.prod === p; });
        weekKeys.forEach(function (wk) {
          var w = byProdWeek[p + '|' + wk];
          if (!w) return;
          cumTarget += w.target;
          var wStart = parseDate(w.start), wEnd = parseDate(w.end) + DAY;
          var plannedWeek = wafers(pLots.filter(function (l) { return l.complete >= wStart && l.complete < wEnd; }));
          var plannedCum = wafers(pLots.filter(function (l) { return l.complete < wEnd; }));
          var gap = plannedCum - cumTarget;
          weeklyRows.push({
            platform: w.platform, ppn: p, week: wk, start: wStart, end: wEnd,
            target: w.target, targetCum: cumTarget, plannedWeek: plannedWeek,
            plannedCum: plannedCum, gap: gap,
            attainment: cumTarget ? plannedCum / cumTarget : 0,
            status: gap >= 0 ? 'ahead' : 'gap'
          });
        });
      });
    }

    var weeklySummary = weekKeys.map(function (wk) {
      var rows = weeklyRows.filter(function (r) { return r.week === wk; });
      var meta = ctx.weekMeta[wk] || {};
      var target = sum(rows, function (r) { return r.target; });
      var targetCum = sum(rows, function (r) { return r.targetCum; });
      var plannedWeek = sum(rows, function (r) { return r.plannedWeek; });
      var plannedCum = sum(rows, function (r) { return r.plannedCum; });
      var lotsWeek = hasWeeklyTargets
        ? ctx.lots.filter(function (l) { return l.week === wk; })
        : ctx.lots.filter(function (l) { return completedInWeek(l, wk); });
      var onTime = hasWeeklyTargets
        ? lotsWeek.filter(function (l) {
            return l.complete <= (ctx.weekMeta[wk] ? ctx.weekMeta[wk].end : l.weekEnd);
          }).length
        : lotsWeek.length;
      return {
        week: wk, start: meta.start, end: meta.end, target: target, targetCum: targetCum,
        plannedWeek: plannedWeek, plannedCum: plannedCum, gap: plannedCum - targetCum,
        attainment: targetCum ? plannedCum / targetCum : 0,
        lots: lotsWeek.length, onTime: onTime,
        onTimeRate: lotsWeek.length ? onTime / lotsWeek.length : 1,
        hasTargets: hasWeeklyTargets
      };
    });

    var platformStats = Object.keys(ctx.platformMps).sort().map(function (pf) {
      var type = ctx.data.platformType[pf];
      var ms = ctx.machines.filter(function(m){return groups(pf).indexOf(m.type)>=0;});
      var pLots = ctx.lots.filter(function (l) { return l.platform === pf; });
      var done = wafers(pLots.filter(function (l) { return l.complete <= ctx.monthEnd; }));
      var onTime = wafers(pLots.filter(function (l) { return l.status === 'on-time'; }));
      var util = ms.length ? sum(ms, function (m) { return m.prodMs + m.setupMs; }) / (ms.length * monthMs) : 0;
      return {
        platform: pf, type: type, mps: ctx.platformMps[pf] || pLots.length, lots: pLots.length,
        completed: done, onTime: onTime, onTimeRate: ctx.platformMps[pf] ? onTime / ctx.platformMps[pf] : 0,
        cpUtil: clamp(util, 0, 1), machines: ms.length,
        setups: sum(ms, function (m) { return m.setupCount; })
      };
    });

    var cpTasks = [];
    ctx.machines.forEach(function (m) { cpTasks = cpTasks.concat(m.tasks); });
    ctx.cpTasks = cpTasks;
    ctx.weeklyRows = weeklyRows;
    ctx.weeklySummary = weeklySummary;
    ctx.platformStats = platformStats;
    var completedMonth = ctx.lots.filter(function (l) { return l.complete <= ctx.monthEnd; }).length;
    var onTime = ctx.lots.filter(function (l) { return l.status === 'on-time'; }).length;
    var lateWeek = ctx.lots.filter(function (l) { return l.status === 'late-week'; }).length;
    var lateMonth = ctx.lots.filter(function (l) { return l.status === 'late-month'; }).length;
    var setups = sum(ctx.machines, function (m) { return m.setupCount; });
    var cpMakespan = Math.max.apply(null, [ctx.start].concat(ctx.machines.map(function (m) { return m.free; })));
    var bakeMakespan = Math.max.apply(null, [ctx.start].concat(ctx.chambers.map(function (c) { return c.free; })));
    var makespan = Math.max(cpMakespan, bakeMakespan,
      Math.max.apply(null, [ctx.start].concat(ctx.lots.filter(function(l){return isFinite(l.complete);}).map(function (l) { return l.complete; }))));
    var bakeLoad = sum(ctx.chambers, function (c) { return c.prodMs; }) / (ctx.chambers.length * monthMs);
    var cardUtil = sum(ctx.cards, function (c) { return c.busyMs; }) / (Math.max(1, ctx.cards.length) * monthMs);
    var pmInWindow = ctx.pmRecords.filter(function (p) { return p.status === 'window'; }).length;
    var borrowScheduled = ctx.borrows.filter(function (b) { return b.status !== 'cancel'; }).length;
    var borrowCanceled = ctx.borrows.length - borrowScheduled;
    var borrowDelayed = ctx.borrows.filter(function (b) { return b.status === 'delayed'; }).length;
    var borrowMaxDelay = Math.max.apply(null, ctx.borrows.map(function (b) { return b.delay || 0; }).concat([0]));
    var avgBatchLots = ctx.batches.length ? sum(ctx.batches, function (b) { return b.lots.length; }) / ctx.batches.length : 0;
    var maxBatchWafers = Math.max.apply(null, ctx.batches.map(function (b) { return b.wafers; }).concat([0]));
    var avgBatchWafers = ctx.batches.length ? sum(ctx.batches, function (b) { return b.wafers; }) / ctx.batches.length : 0;

    var metrics = {
      totalLots: totalLots, completedMonth: completedMonth, completedLots: completedMonth, wipLots: ctx.wip.length,
      mpsUnit: 'wafer', totalWafers:targetWafers, scheduledWafers:wafers(ctx.lots), completedWafers:completedWafers, wipWafers:wipWafers,
      unscheduledLots: ctx.lots.filter(function (l) { return !!l.unscheduledReason; }).length,
      completionRate: targetWafers ? completedWafers / targetWafers : 0,
      onTime: onTime, onTimeRate: targetWafers ? completedWafers / targetWafers : 0,
      lateWeek: lateWeek, lateMonth: lateMonth,
      cpOps: ctx.cpOps || cpTasks.length, cpTasks: cpTasks.length,
      batches: ctx.batches.length, avgBatchLots: avgBatchLots, maxBatchWafers: maxBatchWafers,
      avgBatchWafers: avgBatchWafers,
      cpUtil: clamp(sum(ctx.machines, function (m) { return m.prodMs + m.setupMs; }) / (Math.max(1, ctx.machines.length) * monthMs), 0, 1),
      bakeUtil: clamp(bakeLoad, 0, 1), bakeLoad: bakeLoad, cardUtil: clamp(cardUtil, 0, 1),
      setups: setups, setupMinutes: setups * (ctx.options.setupMinutes == null ? 5 : ctx.options.setupMinutes),
      pmCount: ctx.pmRecords.length, pmInWindow: pmInWindow,
      borrowTotal: ctx.borrows.length, borrowScheduled: borrowScheduled, borrowCanceled: borrowCanceled,
      borrowDelayed: borrowDelayed, borrowCancelRate: ctx.borrows.length ? borrowCanceled / ctx.borrows.length : 0,
      borrowMaxDelay: borrowMaxDelay,
      cpMakespan: cpMakespan, bakeMakespan: bakeMakespan, makespan: makespan,
      horizonDays: (makespan - ctx.start) / DAY
    };
    ctx.metrics = metrics;

    var alerts = [];
    if (hasWeeklyTargets) {
      weeklySummary.forEach(function (w) {
        if (w.gap < 0) alerts.push({ level: 'warn', title: w.week + ' 周目标缺口',
          text: '累计目标 ' + w.targetCum + ' wafer，累计完成 ' + w.plannedCum + ' wafer，缺口 ' + (-w.gap) + ' lot。' });
      });
    }
    if (lateMonth > 0) alerts.push({ level: 'danger', title: '月度未完成',
      text: '当前启发式方案有 ' + lateMonth + ' lot 未在月内完成；需诊断资源及时间缺口，不能据此判定问题无解。' });
    if (lateWeek > 0 && hasWeeklyTargets) alerts.push({ level: 'warn', title: '晚于周目标',
      text: lateWeek + ' lot 晚于目标周完成，可由后续周提前完成量抵扣。' });
    if (metrics.borrowCancelRate > 0.1) alerts.push({ level: 'danger', title: '借机计划需协调',
      text: '取消率 ' + (metrics.borrowCancelRate * 100).toFixed(1) + '%，请查看原机台建议时段。' });
    else if (borrowCanceled > 0) alerts.push({ level: 'info', title: '借机取消',
      text: '取消 ' + borrowCanceled + ' / ' + ctx.borrows.length + ' 条，取消率 ' + (metrics.borrowCancelRate * 100).toFixed(1) + '%。' });
    if (metrics.bakeLoad > 1) alerts.push({ level: 'danger', title: 'BAKE 产能不足',
      text: '当前方案 BAKE 总占腔负荷 ' + (metrics.bakeLoad * 100).toFixed(1) + '%，包含跨月批次；请结合产品路线、实际月内占用与达成率分析，不能据此认定已求得最优解。' });
    else if (metrics.bakeUtil > 0.85) alerts.push({ level: 'warn', title: 'BAKE 高负荷',
      text: 'BAKE 腔体利用率 ' + (metrics.bakeUtil * 100).toFixed(1) + '%，建议关注组批与 PM 窗口。' });
    ctx.machines.forEach(function (m) {
      var u = (m.prodMs + m.setupMs) / monthMs;
      if (u > 0.85) alerts.push({ level: 'warn', title: m.id + ' 高负载', text: '利用率 ' + (u * 100).toFixed(0) + '%。' });
    });
    if (metrics.pmInWindow < metrics.pmCount) alerts.push({ level: 'danger', title: 'PM 越窗',
      text: (metrics.pmCount - metrics.pmInWindow) + ' 次 PM 未落在允许窗口内。' });
    if (!alerts.length) alerts.push({ level: 'ok', title: '排产完成', text: '全部 lot 在月度截止前完成，未发现硬约束冲突。' });

    if (ctx.wip.length) alerts.push({ level: 'info', title: '在制 Lot 已排除', text: ctx.wip.length + ' lot 不参加本次排产及算法完成量统计，资源占用保持不变。' });
    dataRunWarnings(ctx).forEach(function (a) { alerts.push(a); });
    ctx.alerts = alerts;
    ctx.metrics = metrics;
return {
      start: ctx.start, monthEnd: ctx.monthEnd, horizonEnd: ctx.horizonEnd,
      options: ctx.options, metrics: metrics, weekly: weeklySummary, weeklyRows: weeklyRows,
      weeklyHasTargets: hasWeeklyTargets,
      platformStats: platformStats, alerts: alerts,
      machines: ctx.machines, chambers: ctx.chambers, cards: ctx.cards,
      pmRecords: ctx.pmRecords, borrows: ctx.borrows, batches: ctx.batches,
      cpTasks: cpTasks, lots: ctx.lots, wip: ctx.wip, data: ctx.data
    };
  }

  function splitLoad(arr, start, a, b) {
    a = Math.max(a, start); b = Math.min(b, start + arr.length * DAY);
    if (b <= a) return;
    var i = Math.floor((a - start) / DAY), end = Math.floor((b - start - 1) / DAY);
    for (; i <= end && i < arr.length; i++) {
      var s = Math.max(a, start + i * DAY), e = Math.min(b, start + (i + 1) * DAY);
      if (e > s) arr[i] += e - s;
    }
  }


  function objective(r){return r.productStats.map(function(p){return p.rate;}).sort(function(a,b){return a-b;});}
  function run(data,options){options=Object.assign({},options||{});options.strategy='attainment';options.setupMinutes=5;var t0=Date.now(),windows=options.bakeWindowHours==null?[0,4,8]:[options.bakeWindowHours],best=null,trials=[];
    windows.forEach(function(w){var opt=Object.assign({},options,{bakeWindowHours:w}),r=simulate(data,opt),score=objective(r),wins=!best;
      if(best){var old=objective(best);for(var i=0;i<score.length;i++){if(Math.abs(score[i]-old[i])>1e-10){wins=score[i]>old[i];break;}}if(score.every(function(v,i){return Math.abs(v-old[i])<1e-10;}))wins=r.metrics.completedWafers>best.metrics.completedWafers;}
      trials.push({bakeWindowHours:w,minProductRate:r.metrics.minProductRate,completedMonth:r.metrics.completedMonth,completedWafers:r.metrics.completedWafers});if(wins)best=r;});best.trials=trials;best.runMs=Date.now()-t0;best.runAt=new Date();return best;}
  global.TestingScheduler={run:run,parseDate:parseDate,fmt:fmt,dayKey:dayKey,MIN:MIN,HOUR:HOUR,DAY:DAY};
})(typeof window!=='undefined'?window:globalThis);
