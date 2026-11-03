/* Testing area scheduling engine — dependency-free, runs in the browser.
 * Demo heuristic: lot-level list scheduling + card exclusivity + bake batch grouping.
 * Resource calendars preserve WIP and fixed borrowing before scheduling new lots.
 */
(function (global) {
  'use strict';

  var MIN = 60000, HOUR = 3600000, DAY = 86400000;

  function parseDate(s) {
    if (!s) return NaN;
    s = String(s);
    if (s.indexOf('T') >= 0) return Date.parse(s);
    return Date.parse(s + 'T00:00:00+08:00');
  }
  function pad(n) { return n < 10 ? '0' + n : '' + n; }
  function fmt(ms, withDate) {
    if (!isFinite(ms)) return '—';
    var d = new Date(ms + 8 * HOUR);
    var t = pad(d.getUTCHours()) + ':' + pad(d.getUTCMinutes());
    if (!withDate) return t;
    return (d.getUTCMonth() + 1) + '/' + d.getUTCDate() + ' ' + t;
  }
  function dayKey(ms) {
    var d = new Date(ms + 8 * HOUR);
    return d.getUTCFullYear() + '-' + pad(d.getUTCMonth() + 1) + '-' + pad(d.getUTCDate());
  }
  function clamp(v, a, b) { return Math.max(a, Math.min(b, v)); }
  function sum(arr, fn) { var s = 0; for (var i = 0; i < arr.length; i++) s += fn(arr[i], i); return s; }

  function earliestSlot(resource, ready, duration) {
    var s = Math.max(ready, resource.free);
    var blocks = (resource.pm || []).concat(resource.reserved || []);
    for (var guard = 0; guard <= blocks.length; guard++) {
      var conflict = null;
      for (var i = 0; i < blocks.length; i++) {
        if (s < blocks[i][1] && s + duration > blocks[i][0]) { conflict = blocks[i][1]; break; }
      }
      if (conflict === null) return s;
      s = conflict;
    }
    return Infinity;
  }

  function prepare(data, options) {
    var start = parseDate(data.meta.start);
    var monthEnd = start + data.meta.days * DAY;
    var horizonDays = Math.max(32, options.horizonDays || 34);
    var horizonEnd = start + horizonDays * DAY;

    var weekMeta = {};
    data.weekly.forEach(function (w) {
      if (!weekMeta[w.week]) {
        weekMeta[w.week] = { week: w.week, start: parseDate(w.start), end: parseDate(w.end) + DAY };
      }
    });
    var weekEndByProd = {};
    var weekTargetByProd = {};
    data.weekly.forEach(function (w) {
      weekEndByProd[w.ppn + '|' + w.week] = parseDate(w.end) + DAY;
      weekTargetByProd[w.ppn + '|' + w.week] = w.target;
    });

    var prodPlatform = {}, prodMps = {}, platformMps = {};
    data.mps.forEach(function (m) {
      prodPlatform[m.ppn] = m.platform;
      prodMps[m.ppn] = m.mps;
      platformMps[m.platform] = (platformMps[m.platform] || 0) + m.mps;
    });

    var recipeCards = {};
    data.recipes.forEach(function (r) { recipeCards[r.recipe] = r.cards.slice(); });

    var eqpByType = {};
    var machineMap = {};
    var machines = data.eqp.map(function (e, idx) {
      var m = {
        kind: 'cp', id: e.id, type: e.type, index: idx,
        initialCard: e.card, initialRecipe: e.recipe, card: e.card,
        free: start, pm: [], tasks: [], borrows: [], intervals: [], reserved: [], locked: [],
        prodMs: 0, setupMs: 0, pmMs: 0, borrowMs: 0, setupCount: 0
      };
      var active = (data.wip || []).filter(function (w) { return w.eqp === e.id; });
      m.wip = active;
      active.forEach(function (w) { m.locked.push([parseDate(w.start), parseDate(w.end)]); });
      m.pmBlocked = m.locked.slice();
      if (e.status === 'RUN' && !active.length) m.pmBlocked.push([start, Infinity]);
      if (e.status === 'LOST' || (e.status === 'RUN' && !active.length)) m.locked.push([start, Infinity]);
      m.reserved = m.locked.slice();
      m.intervals = m.locked.slice();
      (eqpByType[e.type] = eqpByType[e.type] || []).push(m);
      machineMap[e.id] = m;
      return m;
    });

    var chambers = [];
    var bakeResources = data.bakeResources || { machines: 7, chambers: 4 };
    for (var b = 1; b <= bakeResources.machines; b++) {
      for (var c = 1; c <= bakeResources.chambers; c++) {
        chambers.push({
          kind: 'bake', id: 'BAKE-' + pad(b) + '-C' + c, eqp: 'BAKE-' + pad(b),
          free: start, pm: [], batches: [], reserved: [], wip: [], prodMs: 0, pmMs: 0
        });
      }
    }
    var chamberMap = {};
    chambers.forEach(function (c) {
      chamberMap[c.id] = c;
      c.wip = (data.wip || []).filter(function (w) { return w.chamber === c.id; });
      c.reserved = c.wip.map(function (w) { return [parseDate(w.start), parseDate(w.end)]; });
    });

    var pmRecords = data.pm.map(function (p, idx) {
      var s = p.start ? parseDate(p.start) : parseDate(p.early);
      var resources = p.eqp.indexOf('BAKE') === 0 ? chambers.filter(function (ch) { return ch.eqp === p.eqp; }) : [machineMap[p.eqp]].filter(Boolean);
      var occupied = [];
      resources.forEach(function (m) { occupied = occupied.concat(m.pmBlocked || m.reserved || [], m.pm); });
      var latest = parseDate(p.over) + DAY - p.hours * HOUR;
      var slot = findGap(mergeIntervals(occupied), Math.max(s, parseDate(p.early)), p.hours * HOUR, latest);
      if (slot === null) slot = findGap(mergeIntervals(occupied), parseDate(p.early), p.hours * HOUR, latest);
      if (slot === null || (p.start && slot !== s)) throw new Error('PM 与在制任务或其他 PM 冲突：' + p.eqp);
      s = slot;
      var e = s + p.hours * HOUR;
      var rec = {
        id: 'PM-' + pad(idx + 1), eqp: p.eqp, early: p.early, due: p.due, over: p.over,
        hours: p.hours, start: s, end: e, resources: [],
        status: s >= parseDate(p.early) && e <= parseDate(p.over) + DAY ? 'window' : 'outside'
      };
      if (p.eqp.indexOf('BAKE') === 0) {
        chambers.forEach(function (ch) {
          if (ch.eqp === p.eqp) { ch.pm.push([s, e]); ch.pmMs += p.hours * HOUR; rec.resources.push(ch.id); }
        });
      } else if (machineMap[p.eqp]) {
        var m = machineMap[p.eqp];
        m.pm.push([s, e]); m.pmMs += p.hours * HOUR; rec.resources.push(m.id);
      }
      return rec;
    });

    var cardMap = {};
    data.eqp.forEach(function (e) {
      if (e.card && !cardMap[e.card]) {
        cardMap[e.card] = { id: e.card, type: data.cardType[e.card], free: start, tasks: [],
          busyMs: 0, moves: 0, lastEqp: e.id, recipes: {} };
      }
    });
    data.recipes.forEach(function (r) {
      r.cards.forEach(function (id) {
        if (!cardMap[id]) cardMap[id] = { id: id, type: data.cardType[id], free: start, tasks: [],
          busyMs: 0, moves: 0, lastEqp: null, recipes: {} };
        cardMap[id].recipes[r.recipe] = true;
      });
    });
    var cards = Object.keys(cardMap).map(function (k) { return cardMap[k]; });
    (data.wip || []).forEach(function (w) {
      var machine = machineMap[w.eqp];
      var card = cardMap[w.card || (machine && machine.initialCard)];
      if (card) card.free = Math.max(card.free, parseDate(w.end));
    });
    data.eqp.forEach(function (e) {
      if (e.status === 'RUN' && !(data.wip || []).some(function (w) { return w.eqp === e.id; }) && cardMap[e.card]) cardMap[e.card].free = Infinity;
    });

    var wipIds = new Set((data.wip || []).map(function (w) { return w.lotId; }));
    var lots = data.lots.filter(function (r) { return !wipIds.has(r[0]); }).map(function (r) {
      var ops = data.flows[r[1]] || [];
      var cpCount = ops.filter(function (o) { return o[0].indexOf('CP') === 0; }).length;
      var cpMinutes = ops.filter(function (o) { return o[0].indexOf('CP') === 0; })
        .reduce(function (a, o) { return a + o[2]; }, 0);
      return {
        id: r[0], prod: r[1], platform: r[2], wafer: r[3], release: r[4], week: r[5],
        weeklyDue: r[6], monthlyDue: r[7], priority: r[8], type: data.platformType[r[2]],
        ops: ops, cpCount: cpCount, cpMinutes: cpMinutes,
        weekTarget: weekTargetByProd[r[1] + '|' + r[5]] || 0,
        cpTasks: [], cpEnd: null, bakeStart: null,
        bakeEnd: null, complete: null, batch: null,
        weekEnd: weekEndByProd[r[1] + '|' + r[5]] || parseDate(r[6]) + DAY,
        monthEnd: monthEnd, status: 'pending'
      };
    });

    return {
      start: start, monthEnd: monthEnd, horizonEnd: horizonEnd, horizonDays: horizonDays,
      weekMeta: weekMeta, weekEndByProd: weekEndByProd,
      prodPlatform: prodPlatform, prodMps: prodMps, platformMps: platformMps,
      recipeCards: recipeCards, eqpByType: eqpByType, machines: machines, machineMap: machineMap,
      chambers: chambers, chamberMap: chamberMap, pmRecords: pmRecords,
      cards: cards, cardMap: cardMap, lots: lots, wip: data.wip || [], data: data, options: options
    };
  }
function orderLots(ctx) {
    var strategy = ctx.options.strategy || 'weekly';
    var lots = ctx.lots.slice();
    var weekIdx = function (l) { return l.week ? (l.week.charCodeAt(1) - 48) : 9; };
    if (strategy === 'due') {
      lots.sort(function (a, b) {
        return (a.weekEnd - b.weekEnd) || (a.priority - b.priority) ||
          a.prod.localeCompare(b.prod) || a.id.localeCompare(b.id);
      });
    } else if (strategy === 'changeover') {
      lots.sort(function (a, b) {
        return (weekIdx(a) - weekIdx(b)) || a.platform.localeCompare(b.platform) ||
          a.prod.localeCompare(b.prod) || a.id.localeCompare(b.id);
      });
    } else if (strategy === 'spt') {
      lots.sort(function (a, b) {
        return (weekIdx(a) - weekIdx(b)) || (a.priority - b.priority) ||
          (a.cpMinutes - b.cpMinutes) || a.prod.localeCompare(b.prod) || a.id.localeCompare(b.id);
      });
    } else if (strategy === 'balanced') {
      var groups = {};
      lots.forEach(function (l) {
        var k = weekIdx(l) + '|' + l.platform;
        (groups[k] = groups[k] || []).push(l);
      });
      Object.keys(groups).forEach(function (k) {
        groups[k].sort(function (a, b) { return (a.priority - b.priority) || a.prod.localeCompare(b.prod) || a.id.localeCompare(b.id); });
      });
      var weekGroups = {};
      Object.keys(groups).forEach(function (k) {
        var wk = k.split('|')[0];
        (weekGroups[wk] = weekGroups[wk] || []).push(groups[k]);
      });
      lots = [];
      Object.keys(weekGroups).sort().forEach(function (wk) {
        var arr = weekGroups[wk], max = Math.max.apply(null, arr.map(function (g) { return g.length; }));
        for (var i = 0; i < max; i++) {
          arr.forEach(function (g) { if (g[i]) lots.push(g[i]); });
        }
      });
    } else {
      lots.sort(function (a, b) {
        return (weekIdx(a) - weekIdx(b)) || (a.priority - b.priority) ||
          (b.weekTarget - a.weekTarget) || a.platform.localeCompare(b.platform) ||
          a.prod.localeCompare(b.prod) || a.id.localeCompare(b.id);
      });
    }
    return lots;
  }

  function runCP(ctx) {
    var setupMs = (ctx.options.setupMinutes == null ? 5 : ctx.options.setupMinutes) * MIN;
    var lots = orderLots(ctx);
    var totalOps = 0;
    lots.forEach(function (lot) {
      var ready = parseDate(lot.release);
      var machines = ctx.eqpByType[lot.type] || [];
      var cpIndex = 0;
      for (var oi = 0; oi < lot.ops.length; oi++) {
        var op = lot.ops[oi];
        if (op[0].indexOf('CP') !== 0) continue;
        cpIndex++;
        var dur = op[2] * MIN;
        var cardsForRecipe = ctx.recipeCards[op[1]] || [];
        var best = null;
        for (var mi = 0; mi < machines.length; mi++) {
          var m = machines[mi];
          for (var ci = 0; ci < cardsForRecipe.length; ci++) {
            var card = ctx.cardMap[cardsForRecipe[ci]];
            if (!card || card.type !== m.type) continue;
            var same = m.card === card.id;
            var setup = same ? 0 : setupMs;
            var base = Math.max(ready, card.free);
            var blockStart = earliestSlot(m, base, dur + setup);
            var processStart = blockStart + setup;
            var end = blockStart + dur + setup;
            if (!isFinite(end)) continue;
            if (!best || end < best.end || (end === best.end && setup < best.setup) ||
                (end === best.end && setup === best.setup && mi < best.mi)) {
              best = { end: end, start: processStart, blockStart: blockStart, machine: m, card: card,
                setup: setup, mi: mi };
            }
          }
        }
        if (!best) {
          lot.unscheduledReason = '没有可用的同 TYPE 机台或针卡：' + op[1];
          ready = Infinity; lot.cpEnd = Infinity;
          break;
        }
        var m = best.machine, card = best.card;
        m.intervals.push([best.blockStart, best.end]);
        m.free = Math.max(m.free, best.end);
        if (m.card !== card.id) { m.setupCount++; m.setupMs += best.setup; card.moves++; }
        if (card.lastEqp && card.lastEqp !== m.id && ctx.machineMap[card.lastEqp]) ctx.machineMap[card.lastEqp].card = '';
        m.card = card.id;
        card.free = Math.max(card.free, best.end);
        card.busyMs += best.end - best.blockStart;
        card.lastEqp = m.id;
        m.prodMs += dur;
        card.tasks.push({ lot: lot.id, recipe: op[1], start: best.start, end: best.end, machine: m.id });
        var task = {
          kind: 'cp', lotId: lot.id, prod: lot.prod, platform: lot.platform, wafer: lot.wafer,
          step: op[0], recipe: op[1], duration: dur, setup: best.setup,
          start: best.start, blockStart: best.blockStart, end: best.end,
          machine: m.id, eqp: m.id, type: m.type, card: card.id, week: lot.week,
          setupChanged: !!(best.setup > 0)
        };
        m.tasks.push(task);
        lot.cpTasks.push(task);
        lot.cpEnd = best.end;
        ready = best.end;
        totalOps++;
      }
      lot.cpOps = cpIndex;
      if (!lot.cpEnd) lot.cpEnd = ready;
    });
    ctx.cpOps = totalOps;
    ctx.lotsOrdered = lots;
  }
function runBake(ctx) {
    var windowMs = (ctx.options.bakeWindowHours == null ? 8 : ctx.options.bakeWindowHours) * HOUR;
    var groups = {};
    ctx.lots.forEach(function (lot) {
      var bakeOp = null, qtOp = null;
      for (var i = 0; i < lot.ops.length; i++) {
        if (lot.ops[i][0] === 'BAKE') bakeOp = lot.ops[i];
        if (lot.ops[i][0] === 'QTIME1') qtOp = lot.ops[i];
      }
      lot.bakeOp = bakeOp;
      lot.qtOp = qtOp;
      if (bakeOp && isFinite(lot.cpEnd)) (groups[bakeOp[1]] = groups[bakeOp[1]] || []).push(lot);
    });

    var rawBatches = [];
    Object.keys(groups).forEach(function (recipe) {
      var arr = groups[recipe].sort(function (a, b) { return (a.cpEnd - b.cpEnd) || a.id.localeCompare(b.id); });
      var cur = null;
      arr.forEach(function (lot) {
        if (cur && cur.lots.length < 4 && cur.wafers + lot.wafer <= 50 &&
            (lot.cpEnd - cur.firstReady) <= windowMs) {
          cur.lots.push(lot); cur.wafers += lot.wafer;
          cur.ready = Math.max(cur.ready, lot.cpEnd);
        } else {
          cur = { recipe: recipe, lots: [lot], wafers: lot.wafer, firstReady: lot.cpEnd,
            ready: lot.cpEnd, duration: lot.bakeOp[2] * HOUR };
          rawBatches.push(cur);
        }
      });
    });
    rawBatches.sort(function (a, b) { return (a.ready - b.ready) || (b.duration - a.duration); });

    var batchSeq = 0;
    var batches = rawBatches.map(function (raw) {
      batchSeq++;
      var best = null;
      for (var i = 0; i < ctx.chambers.length; i++) {
        var ch = ctx.chambers[i];
        var start = earliestSlot(ch, raw.ready, raw.duration);
        var end = start + raw.duration;
        if (!best || end < best.end || (end === best.end && ch.prodMs < best.chamber.prodMs)) {
          best = { start: start, end: end, chamber: ch };
        }
      }
      var b = {
        kind: 'bake', id: 'B-' + pad(batchSeq), recipe: raw.recipe, lots: raw.lots,
        wafers: raw.wafers, duration: raw.duration, ready: raw.ready,
        start: best.start, end: best.end, chamber: best.chamber.id, eqp: best.chamber.eqp,
        week: raw.lots[0].week, platform: raw.lots[0].platform
      };
      best.chamber.batches.push(b);
      best.chamber.free = Math.max(best.chamber.free, b.end);
      best.chamber.prodMs += raw.duration;
      raw.lots.forEach(function (lot) {
        lot.batch = b;
        lot.bakeStart = b.start;
        lot.bakeEnd = b.end;
        lot.complete = b.end + (lot.qtOp ? lot.qtOp[2] * MIN : 0);
        lot.bakeChamber = b.chamber;
      });
      return b;
    });

    ctx.batches = batches;
    ctx.lots.forEach(function (lot) {
      if (!lot.complete) {
        lot.complete = lot.cpEnd;
        if (!lot.bakeStart) { lot.bakeStart = lot.cpEnd; lot.bakeEnd = lot.cpEnd; }
      }
      if (lot.complete <= lot.weekEnd) lot.status = 'on-time';
      else if (lot.complete <= lot.monthEnd) lot.status = 'late-week';
      else lot.status = 'late-month';
    });
  }

  function mergeIntervals(list) {
    var arr = list.slice().sort(function (a, b) { return a[0] - b[0]; });
    var out = [];
    arr.forEach(function (iv) {
      if (!out.length || iv[0] > out[out.length - 1][1]) out.push([iv[0], iv[1]]);
      else out[out.length - 1][1] = Math.max(out[out.length - 1][1], iv[1]);
    });
    return out;
  }

  function findGap(intervals, from, duration, latestStart) {
    var start = from;
    for (var i = 0; i < intervals.length; i++) {
      var a = intervals[i][0], b = intervals[i][1];
      if (start + duration <= a) return start <= latestStart ? start : null;
      if (start < b) start = b;
      if (start > latestStart) return null;
    }
    return start <= latestStart ? start : null;
  }

  function planBorrowReserve(ctx) {
    ctx.borrows = ctx.data.borrow.map(function (r, idx) {
      var m = ctx.machineMap[r.eqp];
      var start = parseDate(r.start || r.generatedAt || (r.date + 'T08:00:00+08:00'));
      var end = start + r.hours * HOUR;
      var record = { id: 'BR-' + pad(idx + 1), eqp: r.eqp, hours: r.hours,
        req: r, generatedAt: r.generatedAt, delay: 0, duration: r.hours * HOUR };
      var conflict = !m || m.pm.concat(m.reserved).some(function (iv) { return start < iv[1] && end > iv[0]; });
      if (conflict) {
        record.status = 'cancel';
        record.reason = '与 PM、在制占用、不可用状态或其他借机冲突；请换同 TYPE 机台';
      } else {
        record.start = start; record.end = end; record.status = 'on-time'; record.reserved = true;
        m.reserved.push([start, end]); m.borrows.push(record); m.borrowMs += record.duration;
      }
      return record;
    });
  }

  function dataRunWarnings(ctx) {
    return ctx.data.eqp.filter(function (e) {
      return e.status === 'RUN' && !ctx.wip.some(function (w) { return w.eqp === e.id; });
    }).map(function (e) {
      return { level: 'warn', title: e.id + ' 缺少在制快照', text: 'RUN 机台及其安装针卡暂不分配新任务，请补充预计结束时间。' };
    });
  }

function finalize(ctx) {
    var monthMs = ctx.monthEnd - ctx.start;
    var totalLots = ctx.lots.length;

    ctx.machines.forEach(function (m) {
      m.daily = new Array(ctx.data.meta.days).fill(0);
      m.tasks.forEach(function (t) { splitLoad(m.daily, ctx.start, t.blockStart, t.end); });
    });
    ctx.chambers.forEach(function (ch) {
      ch.daily = new Array(ctx.data.meta.days).fill(0);
      ch.batches.forEach(function (b) { splitLoad(ch.daily, ctx.start, b.start, b.end); });
    });

    var weeklyRows = [];
    var byProdWeek = {};
    ctx.data.weekly.forEach(function (w) { byProdWeek[w.ppn + '|' + w.week] = w; });
    var products = Object.keys(ctx.prodMps).sort();
    var weekKeys = Object.keys(ctx.weekMeta).sort(function (a, b) { return ctx.weekMeta[a].start - ctx.weekMeta[b].start; });
    products.forEach(function (p) {
      var cumTarget = 0;
      var pLots = ctx.lots.filter(function (l) { return l.prod === p; });
      weekKeys.forEach(function (wk) {
        var w = byProdWeek[p + '|' + wk];
        if (!w) return;
        cumTarget += w.target;
        var wStart = parseDate(w.start), wEnd = parseDate(w.end) + DAY;
        var plannedWeek = pLots.filter(function (l) { return l.complete >= wStart && l.complete < wEnd; }).length;
        var plannedCum = pLots.filter(function (l) { return l.complete < wEnd; }).length;
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

    var weeklySummary = weekKeys.map(function (wk) {
      var rows = weeklyRows.filter(function (r) { return r.week === wk; });
      var meta = ctx.weekMeta[wk] || {};
      var target = sum(rows, function (r) { return r.target; });
      var targetCum = sum(rows, function (r) { return r.targetCum; });
      var plannedWeek = sum(rows, function (r) { return r.plannedWeek; });
      var plannedCum = sum(rows, function (r) { return r.plannedCum; });
      var lotsWeek = ctx.lots.filter(function (l) { return l.week === wk; });
      var onTime = lotsWeek.filter(function (l) {
        return l.complete <= (ctx.weekMeta[wk] ? ctx.weekMeta[wk].end : l.weekEnd);
      }).length;
      return {
        week: wk, start: meta.start, end: meta.end, target: target, targetCum: targetCum,
        plannedWeek: plannedWeek, plannedCum: plannedCum, gap: plannedCum - targetCum,
        attainment: targetCum ? plannedCum / targetCum : 0,
        lots: lotsWeek.length, onTime: onTime,
        onTimeRate: lotsWeek.length ? onTime / lotsWeek.length : 1
      };
    });

    var platformStats = ['K1', 'K2', 'K3', 'K4', 'K5'].map(function (pf) {
      var type = ctx.data.platformType[pf];
      var ms = ctx.eqpByType[type] || [];
      var pLots = ctx.lots.filter(function (l) { return l.platform === pf; });
      var done = pLots.filter(function (l) { return l.complete <= ctx.monthEnd; }).length;
      var onTime = pLots.filter(function (l) { return l.status === 'on-time'; }).length;
      var util = ms.length ? sum(ms, function (m) { return m.prodMs + m.setupMs; }) / (ms.length * monthMs) : 0;
      return {
        platform: pf, type: type, mps: ctx.platformMps[pf] || pLots.length, lots: pLots.length,
        completed: done, onTime: onTime, onTimeRate: pLots.length ? onTime / pLots.length : 0,
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
    var cpMakespan = Math.max.apply(null, ctx.machines.map(function (m) { return m.free; }));
    var bakeMakespan = Math.max.apply(null, ctx.chambers.map(function (c) { return c.free; }));
    var makespan = Math.max(cpMakespan, bakeMakespan,
      Math.max.apply(null, ctx.lots.map(function (l) { return l.complete; })));
    var bakeUtil = sum(ctx.chambers, function (c) { return c.prodMs; }) / (ctx.chambers.length * monthMs);
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
      totalLots: totalLots, completedMonth: completedMonth, wipLots: ctx.wip.length,
      unscheduledLots: ctx.lots.filter(function (l) { return !!l.unscheduledReason; }).length,
      completionRate: totalLots ? completedMonth / totalLots : 0,
      onTime: onTime, onTimeRate: totalLots ? onTime / totalLots : 0,
      lateWeek: lateWeek, lateMonth: lateMonth,
      cpOps: ctx.cpOps || cpTasks.length, cpTasks: cpTasks.length,
      batches: ctx.batches.length, avgBatchLots: avgBatchLots, maxBatchWafers: maxBatchWafers,
      avgBatchWafers: avgBatchWafers,
      cpUtil: clamp(sum(ctx.machines, function (m) { return m.prodMs + m.setupMs; }) / (Math.max(1, ctx.machines.length) * monthMs), 0, 1),
      bakeUtil: clamp(bakeUtil, 0, 1), cardUtil: clamp(cardUtil, 0, 1),
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
    weeklySummary.forEach(function (w) {
      if (w.gap < 0) alerts.push({ level: 'warn', title: w.week + ' 周目标缺口',
        text: '累计目标 ' + w.targetCum + ' lot，累计完成 ' + w.plannedCum + ' lot，缺口 ' + (-w.gap) + ' lot。' });
    });
    if (lateMonth > 0) alerts.push({ level: 'danger', title: '月度未完成',
      text: '当前启发式方案有 ' + lateMonth + ' lot 未在月内完成；需诊断资源及时间缺口，不能据此判定问题无解。' });
    if (lateWeek > 0) alerts.push({ level: 'warn', title: '晚于周目标',
      text: lateWeek + ' lot 晚于目标周完成，可由后续周提前完成量抵扣。' });
    if (metrics.borrowCancelRate > 0.1) alerts.push({ level: 'danger', title: '借机取消超限',
      text: '取消率 ' + (metrics.borrowCancelRate * 100).toFixed(1) + '%，超过 10% 阈值。' });
    else if (borrowCanceled > 0) alerts.push({ level: 'info', title: '借机取消',
      text: '取消 ' + borrowCanceled + ' / ' + ctx.borrows.length + ' 条，取消率 ' + (metrics.borrowCancelRate * 100).toFixed(1) + '%。' });
    if (metrics.bakeUtil > 0.85) alerts.push({ level: 'warn', title: 'BAKE 为瓶颈资源',
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

  function run(data, options) {
    options = options || {};
    var t0 = (typeof performance !== 'undefined' ? performance.now() : Date.now());
    var t0Date = Date.now();
    var ctx = prepare(data, options);
    planBorrowReserve(ctx);
    runCP(ctx);
    runBake(ctx);
    var result = finalize(ctx);
    result.runMs = (typeof performance !== 'undefined' ? performance.now() : Date.now()) - t0;
    if (!result.runMs) result.runMs = Date.now() - t0Date;
    result.runAt = new Date();
    return result;
  }

  global.TestingScheduler = { run: run, parseDate: parseDate, fmt: fmt, dayKey: dayKey,
    MIN: MIN, HOUR: HOUR, DAY: DAY };
})(typeof window !== 'undefined' ? window : globalThis);
