/* Run after scheduler.js, with Node or a JavaScript VM. */
(function () {
  'use strict';
  var S = globalThis.TestingScheduler, H = S.HOUR;
  function assert(value, message) { if (!value) throw new Error(message); }
  function clone(value) { return JSON.parse(JSON.stringify(value)); }
  function fixture() {
    return {
      meta: {start:'2026-09-01T00:00:00+08:00', days:30},
      mps:[{platform:'K1', ppn:'P', mps:3}],
      eqp:[{id:'M1',type:'E1',card:'C1',recipe:'R',status:''}, {id:'M2',type:'E1',card:'C2',recipe:'R',status:''}],
      recipes:[{recipe:'R',cards:['C1','C2']}], cardType:{C1:'E1',C2:'E1'}, platformType:{K1:'E1'},
      flows:{P:[['CP1','R',60],['CP2','R',60],['BAKE','150C2H',2],['QTIME1','',60]]},
      weekly:[], pm:[], borrow:[], wip:[], bakeResources:{machines:1,chambers:1},
      lots:['L1','L2','L3'].map(function (id) {return [id,'P','K1',20,'2026-09-01','W1','2026-09-07','2026-10-01',1];})
    };
  }
  var passed=[];
  function test(name, fn) { fn(); passed.push(name); }
  function overlap(a,b) { return a[0] < b[1] && a[1] > b[0]; }
  test('WIP lot is entirely excluded and its machine/card occupancy remains', function () {
    var d=fixture(); d.eqp[0].status='RUN';
    d.wip=[{lotId:'L1',eqp:'M1',card:'C1',start:'2026-09-01T00:00:00+08:00',end:'2026-09-01T12:00:00+08:00'}];
    var r=S.run(d,{}), stop=S.parseDate(d.wip[0].end);
    assert(!r.lots.some(function(l){return l.id==='L1';}), 'WIP must not get downstream tasks');
    assert(r.metrics.wipLots===1 && r.metrics.totalLots===2, 'WIP count must be separate');
    assert(r.cpTasks.every(function(t){return (t.machine!=='M1' && t.card!=='C1') || t.blockStart>=stop;}), 'WIP resource occupied');
    assert(r.batches.every(function(b){return b.lots.every(function(l){return l.id!=='L1';});}), 'WIP must not enter BAKE');
  });
  test('BAKE WIP blocks its chamber without being rescheduled', function () {
    var d=fixture(); d.wip=[{lotId:'L1',eqp:'BAKE-01',chamber:'BAKE-01-C1',start:'2026-09-01T00:00:00+08:00',end:'2026-09-02T00:00:00+08:00'}];
    var r=S.run(d,{}); assert(r.batches.every(function(b){return b.start>=S.parseDate(d.wip[0].end);}), 'Chamber overlap');
  });
  test('LOST cannot produce or lend, but can have fixed-machine PM', function () {
    var d=fixture(); d.eqp[0].status='LOST';
    d.pm=[{eqp:'M1',early:'2026-09-01',due:'2026-09-02',over:'2026-09-03',hours:4}];
    d.borrow=[{eqp:'M1',start:'2026-09-02T10:00:00+08:00',hours:2}];
    var r=S.run(d,{}); assert(r.cpTasks.every(function(t){return t.machine!=='M1';}), 'LOST machine used');
    assert(r.borrows[0].status==='cancel', 'LOST was lent'); assert(r.pmRecords[0].eqp==='M1','PM changed machine');
  });
  test('PM chooses earliest free time within its window, keeping the machine', function () {
    var d=fixture(); d.wip=[{lotId:'L1',eqp:'M1',card:'C1',start:'2026-09-01T00:00:00+08:00',end:'2026-09-01T04:00:00+08:00'}];
    d.pm=[{eqp:'M1',early:'2026-09-01',due:'2026-09-02',over:'2026-09-03',hours:2}];
    var r=S.run(d,{}); assert(r.pmRecords[0].eqp==='M1' && r.pmRecords[0].start===S.parseDate(d.wip[0].end),'PM did not select earliest');
  });
  test('Borrow conflicts with PM without delay; another machine is usable', function () {
    var d=fixture(); d.pm=[{eqp:'M1',early:'2026-09-01',due:'2026-09-01',over:'2026-09-01',hours:8}];
    d.borrow=[{eqp:'M1',start:'2026-09-01T02:00:00+08:00',hours:2}];
    var r=S.run(d,{maxDelayHours:48}); assert(r.borrows[0].status==='cancel','Borrow was postponed');
    d.borrow[0].eqp='M2'; r=S.run(d,{});
    assert(r.borrows[0].status==='on-time' && r.borrows[0].start===S.parseDate(d.borrow[0].start),'Borrow time changed');
    assert(r.cpTasks.filter(function(t){return t.machine==='M2';}).every(function(t){return !overlap([t.blockStart,t.end],[r.borrows[0].start,r.borrows[0].end]);}), 'Production overlaps borrowing');
  });
  test('Impossible PM is reported instead of moving outside the window', function () {
    var d=fixture(); d.pm=[{eqp:'M1',early:'2026-09-01',due:'2026-09-01',over:'2026-09-01',hours:25}];
    var failed=false; try{S.run(d,{});}catch(e){failed=true;} assert(failed,'Out-of-window PM accepted');
  });
  test('Unavailable capacity yields a gap without scheduling infinite tasks', function () {
    var d=fixture();d.eqp.forEach(function(m){m.status='LOST';});
    var r=S.run(d,{}); assert(r.metrics.completedMonth===0 && r.metrics.unscheduledLots===3,'Missing capacity is hidden');
    assert(r.cpTasks.length===0 && r.batches.length===0,'Infinite task emitted');
  });
  test('Missing RUN snapshot reserves both machine and installed card', function () {
    var d=fixture();d.eqp[0].status='RUN';var r=S.run(d,{});
    assert(r.cpTasks.every(function(t){return t.machine!=='M1' && t.card!=='C1';}),'Unknown RUN resource used');
    assert(r.alerts.some(function(a){return a.title.indexOf('缺少在制快照')>=0;}),'Missing snapshot not reported');
  });
  test('Independent resource calendars never overlap and batches respect capacity', function () {
    var r=S.run(fixture(),{}), calendars={};
    r.cpTasks.forEach(function(t){['machine:'+t.machine,'card:'+t.card].forEach(function(k){
      (calendars[k]||[]).forEach(function(iv){assert(!overlap(iv,[t.blockStart,t.end]),'Resource overlap '+k);});
      (calendars[k]=calendars[k]||[]).push([t.blockStart,t.end]);
    });});
    assert(r.batches.every(function(b){return b.lots.length<=4 && b.wafers<=50;}),'Batch capacity exceeded');
    r.lots.forEach(function(l){assert(l.complete===l.bakeEnd+H,'QTIME ignored');});
  });
  test('Scheduling does not mutate input data', function () {
    var d=fixture(), before=JSON.stringify(d); S.run(d,{});assert(before===JSON.stringify(d),'Reference data mutated');
  });
  globalThis.schedulerTestResults=passed;
})();
