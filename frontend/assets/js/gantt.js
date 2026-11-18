/* Gantt view: canvas rendering and interactions. */
(function () {
  'use strict';
  var A = window.TSApp;
  var $ = A.$, num = A.num, esc = A.esc, fmtDate = A.fmtDate, fmtDateTime = A.fmtDateTime, pad2 = A.pad2;
  var DAY = A.DAY, HOUR = A.HOUR;

  /* 左侧资源表：机台/腔体 + 四列统计（换线、末单完成、利用率、宕机时间）。 */
  var RES_W = 84, LABEL_PAD = 12, LABEL_PAD_R = 10;
  var STAT_COLS = [
    { key: 'changes', label: '换线', hint: '次', w: 34 },
    { key: 'last', label: '末单完成', hint: '时间', w: 64 },
    { key: 'util', label: '利用率', hint: '占比', w: 46 },
    { key: 'down', label: '宕机', hint: '小时', w: 46 }
  ];
  /* 宕机口径：PM 维护 + 故障停机（资源上的 down 区间）。
     若现场只把故障算宕机、计划保养不计入，把这里改成 false 即可。 */
  var PM_IS_DOWN = true;
  /* 换线口径：相邻两单 Recipe 或针卡任一变化即计一次；两者同时变化仍只计一次。
     时间轴上用红色竖线 + 顶部小三角标出每次换线的起点。 */
  var LINE_CHANGE_COLOR = '#d92d20';

  function labelWidth() {
    return LABEL_PAD + RES_W + LABEL_PAD_R +
      STAT_COLS.reduce(function (sum, col) { return sum + col.w; }, 0);
  }

  function mergeIntervals(list) {
    var out = [];
    list.filter(function (iv) { return iv && iv[1] > iv[0]; })
      .sort(function (a, b) { return a[0] - b[0]; })
      .forEach(function (iv) {
        var last = out[out.length - 1];
        if (last && iv[0] <= last[1]) last[1] = Math.max(last[1], iv[1]);
        else out.push([iv[0], iv[1]]);
      });
    return out;
  }

  function coveredMs(intervals, from, to) {
    if (!isFinite(from) || !isFinite(to) || to <= from) return 0;
    return mergeIntervals(intervals).reduce(function (sum, iv) {
      return sum + Math.max(0, Math.min(iv[1], to) - Math.max(iv[0], from));
    }, 0);
  }

  /* 单台资源（CP 机台或 BAKE 腔体）的四项统计。
     利用率窗口 = [排产开始, 该资源最后一单完工]；
     窗口内加工（含换线准备）、借机、在制占用算工作，PM/故障停机单独计宕机。 */
  function resourceStats(res, result) {
    result = result || A.state.result || {};
    var from = result.start, monthEnd = result.monthEnd == null ? from : result.monthEnd;
    var items = res.kind === 'cp' ? (res.tasks || []) : (res.batches || []);
    var work = [], down = [], changes = 0, last = null, lineFlags = [];
    var prevRecipe = res.initialRecipe;
    var prevCard = res.kind === 'cp' ? res.initialCard : null;
    items.forEach(function (item) {
      var recipeChanged = prevRecipe != null && item.recipe != null && item.recipe !== prevRecipe;
      var cardChanged = res.kind === 'cp' && prevCard != null && item.card != null && item.card !== prevCard;
      if (recipeChanged || cardChanged) {
        changes += 1;
        lineFlags.push({
          recipe: recipeChanged, card: cardChanged,
          fromRecipe: prevRecipe, toRecipe: item.recipe,
          fromCard: prevCard, toCard: item.card
        });
      } else {
        lineFlags.push(null);
      }
      prevRecipe = item.recipe;
      if (res.kind === 'cp') prevCard = item.card;
      var begin = res.kind === 'cp' && item.blockStart != null ? item.blockStart : item.start;
      work.push([begin, item.end]);
      if (last == null || item.end > last) last = item.end;
    });
    (res.borrows || []).forEach(function (b) { work.push([b.start, b.end]); });
    (res.wip || []).forEach(function (w) {
      work.push([A.SCHED.parseDate(w.start), A.SCHED.parseDate(w.end)]);
    });
    if (PM_IS_DOWN) (res.pm || []).forEach(function (iv) { down.push([iv[0], iv[1]]); });
    (res.down || []).forEach(function (iv) {
      down.push(Array.isArray(iv) ? [iv[0], iv[1]] : [iv.start, iv.end]);
    });
    var stats = { changes: changes, lineFlags: lineFlags, last: last, windowMs: 0, workMs: 0,
      downMs: 0, idleMs: 0, util: null };
    if (res.available === false) {
      var end = last == null ? monthEnd : last;
      stats.windowMs = Math.max(0, end - from);
      stats.downMs = stats.windowMs;
      stats.util = stats.windowMs > 0 ? 0 : null;
      return stats;
    }
    var limit = last == null ? monthEnd : last;
    if (last != null) stats.windowMs = Math.max(0, last - from);
    stats.workMs = coveredMs(work, from, limit);
    stats.downMs = coveredMs(down, from, limit);
    var occupied = coveredMs(work.concat(down), from, limit);
    if (last != null) {
      stats.idleMs = Math.max(0, stats.windowMs - occupied);
      stats.util = stats.windowMs > 0 ? stats.workMs / stats.windowMs : null;
    }
    return stats;
  }

  function aggregateStats(rows) {
    var out = null;
    rows.forEach(function (row) {
      var stat = row.stats;
      if (!stat) return;
      out = out || { changes: 0, last: null, windowMs: 0, workMs: 0,
        downMs: 0, idleMs: 0, util: null };
      out.changes += stat.changes || 0;
      if (stat.last != null && (out.last == null || stat.last > out.last)) out.last = stat.last;
      out.windowMs += stat.windowMs || 0;
      out.workMs += stat.workMs || 0;
      out.downMs += stat.downMs || 0;
      out.idleMs += stat.idleMs || 0;
    });
    if (out) out.util = out.windowMs > 0 ? out.workMs / out.windowMs : null;
    return out;
  }

  function statText(key, stats) {
    if (!stats) return '—';
    if (key === 'changes') return String(stats.changes || 0);
    if (key === 'last') return stats.last == null ? '—' : fmtDateTime(stats.last);
    if (key === 'util') return stats.util == null ? '—' : (stats.util * 100).toFixed(1) + '%';
    if (key === 'down') return (stats.downMs || 0) > 0 ? (stats.downMs / HOUR).toFixed(1) + 'h' : '0h';
    return '—';
  }

  function statColor(key, stats, muted) {
    var down = stats ? (stats.downMs || 0) : 0;
    if (muted) return '#7f8da4';
    if (key === 'util' && stats && stats.util != null) {
      if (stats.util >= 0.95) return '#b4453f';
      if (stats.util >= 0.85) return '#b07b1e';
    }
    if (key === 'down' && down > 0) return '#6f52c9';
    if (key === 'changes' && !(stats && stats.changes)) return '#b9c3d1';
    if (key === 'down' && !down) return '#b9c3d1';
    return '#3d4d66';
  }

  function hoursText(ms) {
    if (ms == null || !isFinite(ms)) return '—';
    return (ms / HOUR).toFixed(1) + ' h';
  }

  function drawStatCells(ctx, stats, baseline, muted) {
    var x = LABEL_PAD + RES_W;
    STAT_COLS.forEach(function (col) {
      ctx.textAlign = 'right';
      ctx.font = (muted ? '500 8px ' : '600 8.5px ') + '"PingFang SC", sans-serif';
      ctx.fillStyle = statColor(col.key, stats, muted);
      ctx.fillText(statText(col.key, stats), x + col.w - 8, baseline);
      x += col.w;
    });
    ctx.textAlign = 'left';
  }

  function renderGantt() {
    var r = A.state.result, g = A.state.gantt;
    var rangeDays = { '1d': 1, '3d': 3, '7d': 7, '30d': 30 }[g.range] || 1;
    var start = ganttStart(r, g, rangeDays);
    var platformOpts = '<option value="all">全部 Platform</option>' +
      Object.keys(A.state.data.platformType).sort().map(function (p) {
        return '<option value="' + p + '"' + (g.platform === p ? ' selected' : '') + '>' + p + ' · ' + A.state.data.platformType[p] + '</option>';
      }).join('');
    var html = '<div class="toolbar">' +
      '<span class="label">资源</span><div class="seg" data-gantt-mode>' +
        '<button data-mode="cp" class="' + (g.mode === 'cp' ? 'active' : '') + '">CP 机台</button>' +
        '<button data-mode="bake" class="' + (g.mode === 'bake' ? 'active' : '') + '">BAKE 腔体</button>' +
        '<button data-mode="all" class="' + (g.mode === 'all' ? 'active' : '') + '">全部</button></div>' +
      '<span class="label" style="margin-left:8px">范围</span><div class="seg" data-gantt-range>' +
        ['1d', '3d', '7d', '30d'].map(function (x) {
          return '<button data-range="' + x + '" class="' + (g.range === x ? 'active' : '') + '">' + ({ '1d': '1天', '3d': '3天', '7d': '7天', '30d': '全月' })[x] + '</button>';
        }).join('') + '</div>' +
      '<div class="date-nav"><button data-gantt-nav="-1">‹</button><span class="date-label">' + ganttRangeLabel(start, rangeDays) + '</span><button data-gantt-nav="1">›</button></div>' +
      (g.mode !== 'bake' ? '<select class="select" id="gantt-platform">' + platformOpts + '</select>' : '') +
      '<input class="input search" id="gantt-lot-q" placeholder="定位 Lot，如 PPN01-L0001" value="' + esc(g.q) + '">' +
      '<button class="btn ghost small" data-gantt-month>回到月初</button>' +
      '<span class="filter-count">' + num(r.machines.length) + ' 台 CP · ' + r.chambers.length + ' 个 BAKE 腔体</span>' +
    '</div>' +
    '<div class="gantt-card">' +
      '<div class="gantt-legend">' +
        '<span>CP 任务按机台组着色</span>' +
        '<span><i style="background:#f0b45a"></i>BAKE 批</span><span><i style="background:#c9b8f5"></i>PM 维护</span><span><i style="background:#f3a58a"></i>借机</span><span><i style="background:#65758b"></i>在制占用 / 不可用</span>' +
        '<span><i style="background:#d92d20;width:2px;height:12px;border-radius:1px"></i>红色标记 = 换线（全月为顶部刻度）</span>' +
        '<span style="flex-basis:100%;color:#94a1b4">左侧四列 · 换线 = 相邻两单 Recipe 或针卡变化次数（两者同时变只计 1 次，含首单；图中红色标记标出）· 末单完成 = 最后一单完工时间 · 利用率 = 完工窗口内非空闲非宕机占比 · 宕机 = PM 维护与故障停机</span>' +
        '<span style="margin-left:auto;color:#94a1b4">悬停查看 · 点击详情</span>' +
      '</div>' +
      '<div class="gantt-scroll" id="gantt-scroll"><canvas id="gantt-canvas"></canvas></div>' +
      '<div class="gantt-footer"><span>北京时间 · ' + ganttRangeLabel(start, rangeDays) + '</span><span id="gantt-footer-stat">—</span></div>' +
    '</div>' +
    '<div class="gantt-tip" id="gantt-tip"></div>';
    $('#view-gantt').innerHTML = html;
    drawGantt();
  }

  function ganttStart(r, g, rangeDays) {
    if (rangeDays >= 30) return r.start;
    var anchor = g.anchor || r.start;
    anchor = A.SCHED.parseDate(A.SCHED.dayKey(anchor));
    return Math.max(r.start, Math.min(r.monthEnd - DAY, anchor));
  }

  function ganttRangeLabel(start, rangeDays) {
    var wd = ['日', '一', '二', '三', '四', '五', '六'][new Date(start + 8 * HOUR).getUTCDay()];
    if (rangeDays === 1) return (new Date(start + 8 * HOUR).getUTCMonth() + 1) + '月' + new Date(start + 8 * HOUR).getUTCDate() + '日 周' + wd;
    if (rangeDays === 30) {
      var mm = (A.state.data.meta && A.state.data.meta.month) || '';
      return mm ? mm.split('-')[0] + ' 年 ' + Number(mm.split('-')[1]) + ' 月' : '全月';
    }
    return fmtDate(start) + ' - ' + fmtDate(start + rangeDays * DAY - 1);
  }

  function buildGanttRows() {
    var r = A.state.result, g = A.state.gantt;
    var rows = [];
    if (g.mode !== 'bake') {
      var types = g.platform === 'all' ? Array.from(new Set(r.machines.map(function(m){return m.type;}))).sort() : [g.platform];
      types.forEach(function (type) {
        var ms = r.machines.filter(function (m) { return m.type === type; });
        var group = { kind: 'group', label: platformOfType(type) + ' · ' + type, sub: ms.length + ' 台', children: [] };
        rows.push(group);
        ms.forEach(function (m) {
          var row = { kind: 'machine', res: m, label: m.id, sub: 'CP', stats: resourceStats(m, r) };
          group.children.push(row);
          rows.push(row);
        });
        group.stats = aggregateStats(group.children);
      });
    }
    if (g.mode !== 'cp') {
      Array.from(new Set(r.chambers.map(function(c){return c.eqp;}))).forEach(function(eqp){
        var chambers=r.chambers.filter(function(c){return c.eqp===eqp;});
        var group = { kind: 'group', label: eqp, sub: chambers.length + ' 腔', children: [] };
        rows.push(group);
        chambers.forEach(function(c){
          var row = {kind:'machine',res:c,label:c.id,sub:'BAKE',stats:resourceStats(c, r)};
          group.children.push(row);
          rows.push(row);
        });
        group.stats = aggregateStats(group.children);
      });
    }
    var y = 46;
    rows.forEach(function (row) {
      row.h = row.kind === 'group' ? 24 : 26;
      row.y = y; y += row.h;
    });
    A.state.ganttRows = rows;
    return rows;
  }

  function platformOfType(type) {
    var found = type;
    Object.keys(A.state.data.platformType).forEach(function (k) { if (A.state.data.platformType[k] === type) found = k; });
    return found;
  }

  function drawGantt() {
    var canvas = $('#gantt-canvas'), scroll = $('#gantt-scroll');
    if (!canvas || !scroll || !A.state.result) return;
    var r = A.state.result, g = A.state.gantt;
    var rows = buildGanttRows();
    var rangeDays = { '1d': 1, '3d': 3, '7d': 7, '30d': 30 }[g.range] || 1;
    var start = ganttStart(r, g, rangeDays), end = start + rangeDays * DAY;
    var width = Math.max(scroll.clientWidth || 1000, 920);
    var labelW = labelWidth(), axisH = 46, padR = 12;
    var height = axisH + rows.reduce(function (a, x) { return a + x.h; }, 0) + 10;
    var dpr = window.devicePixelRatio || 1;
    canvas.width = Math.round(width * dpr); canvas.height = Math.round(height * dpr);
    canvas.style.width = width + 'px'; canvas.style.height = height + 'px';
    var ctx = canvas.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, height);
    ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, width, height);
    var xOf = function (ms) { return labelW + (ms - start) / (end - start) * (width - labelW - padR); };
    var plotW = width - labelW - padR;

    for (var d = 0; d < rangeDays; d++) {
      var day = start + d * DAY;
      var wd = new Date(day + 8 * HOUR).getUTCDay();
      if ((wd === 0 || wd === 6) && rangeDays >= 7) {
        ctx.fillStyle = '#f8fafd';
        ctx.fillRect(xOf(day), axisH, xOf(day + DAY) - xOf(day), height - axisH);
      }
    }
    var tick = rangeDays === 1 ? 2 * HOUR : rangeDays === 3 ? 6 * HOUR : rangeDays === 7 ? 12 * HOUR : 2 * DAY;
    ctx.strokeStyle = '#eef2f7'; ctx.lineWidth = 1;
    for (var t = start; t <= end + 1; t += tick) {
      var x = Math.round(xOf(t)) + .5;
      ctx.beginPath(); ctx.moveTo(x, axisH); ctx.lineTo(x, height - 8); ctx.stroke();
    }

    var alt = 0;
    rows.forEach(function (row) {
      if (row.kind === 'group') {
        ctx.fillStyle = '#f6f9fd'; ctx.fillRect(0, row.y, width, row.h);
        ctx.fillStyle = '#2c3d58'; ctx.font = '700 10px "PingFang SC", sans-serif';
        ctx.fillText(row.label, 14, row.y + 16);
        ctx.strokeStyle = '#e4e9f1'; ctx.beginPath(); ctx.moveTo(0, row.y + row.h - .5); ctx.lineTo(width, row.y + row.h - .5); ctx.stroke();
      } else {
        alt++;
        ctx.fillStyle = alt % 2 ? '#fcfdff' : '#ffffff'; ctx.fillRect(0, row.y, width, row.h);
        ctx.strokeStyle = '#f1f4f9'; ctx.beginPath(); ctx.moveTo(0, row.y + row.h - .5); ctx.lineTo(width, row.y + row.h - .5); ctx.stroke();
      }
    });

    ctx.save();
    ctx.beginPath(); ctx.rect(labelW, axisH, plotW, height - axisH - 8); ctx.clip();
    var regions = rows.map(function () { return []; });
    var visible = 0;
    rows.forEach(function (row, idx) {
      if (row.kind !== 'machine') return;
      var res = row.res, y0 = row.y + 4, y1 = row.y + row.h - 4;
      (res.wip || []).forEach(function (w) {
        var ws = A.SCHED.parseDate(w.start), we = A.SCHED.parseDate(w.end);
        if (we < start || ws > end) return;
        ctx.fillStyle = '#65758b';
        ctx.fillRect(xOf(ws), y0, Math.max(2, xOf(we) - xOf(ws)), y1 - y0);
        addRegion(regions[idx], xOf(ws), xOf(we), y0, y1, 'wip', w, res);
      });
      if (res.locked && res.locked.some(function (iv) { return iv[1] === Infinity; })) {
        ctx.fillStyle = '#65758b'; ctx.globalAlpha = .35;
        ctx.fillRect(labelW, y0, plotW, y1 - y0); ctx.globalAlpha = 1;
      }
      if (res.kind === 'cp') {
        res.pm.forEach(function (iv) { drawPM(ctx, xOf, iv[0], iv[1], y0, y1); addRegion(regions[idx], xOf(iv[0]), xOf(iv[1]), y0, y1, 'pm', iv, res); });
        res.tasks.forEach(function (task, i) {
          if (task.end < start || task.start > end) return;
          visible++;
          var x1 = xOf(task.start), x2 = xOf(task.end);
          if (task.setup && task.blockStart < task.start) {
            ctx.fillStyle = '#c9d4e3';
            ctx.fillRect(xOf(task.blockStart), y0 + 8, Math.max(1, x1 - xOf(task.blockStart)), 2);
          }
          var w = Math.max(1.6, x2 - x1);
          ctx.fillStyle = A.PLATFORM_COLORS[task.platform] || '#2f6bff';
          ctx.globalAlpha = .9; ctx.fillRect(x1, y0, w, y1 - y0); ctx.globalAlpha = 1;
          addRegion(regions[idx], x1, x1 + w, y0, y1, 'cp', task, res);
          var lc = row.stats && row.stats.lineFlags && row.stats.lineFlags[i];
          var lcMs = task.blockStart != null ? task.blockStart : task.start;
          if (lc && lcMs >= start && lcMs <= end) {
            var lx = xOf(lcMs);
            drawLineChange(ctx, lx, y0, y1, rangeDays > 7);
            addRegion(regions[idx], lx - 4, lx + 4, rangeDays > 7 ? y0 - 1 : y0, rangeDays > 7 ? y0 + 5 : y1, 'linechange', {
              time: lcMs, recipe: lc.recipe, card: lc.card,
              fromRecipe: lc.fromRecipe, toRecipe: lc.toRecipe,
              fromCard: lc.fromCard, toCard: lc.toCard,
              label: task.lotId + ' · ' + task.step
            }, res);
          }
        });
        res.borrows.forEach(function (b) {
          if (b.end < start || b.start > end) return;
          var bx1 = xOf(b.start), bx2 = xOf(b.end);
          ctx.fillStyle = '#f3a58a';
          ctx.fillRect(bx1, y1 - 3, Math.max(1.5, bx2 - bx1), 3);
          addRegion(regions[idx], bx1, bx2, y1 - 5, y1, 'borrow', b, res);
        });
      } else {
        res.pm.forEach(function (iv) { drawPM(ctx, xOf, iv[0], iv[1], y0, y1); addRegion(regions[idx], xOf(iv[0]), xOf(iv[1]), y0, y1, 'pm', iv, res); });
        res.batches.forEach(function (batch, i) {
          if (batch.end < start || batch.start > end) return;
          visible++;
          var bx1 = xOf(batch.start), bx2 = xOf(batch.end), bw = Math.max(1.6, bx2 - bx1);
          ctx.fillStyle = A.RECIPE_COLORS[batch.recipe] || '#e89b1c';
          ctx.globalAlpha = .92;
          roundRect(ctx, bx1, y0 + 1, bw, y1 - y0 - 2, 3); ctx.fill(); ctx.globalAlpha = 1;
          if (bw > 36) {
            ctx.fillStyle = 'rgba(255,255,255,.92)'; ctx.font = '700 8px "PingFang SC", sans-serif';
            ctx.fillText(batch.lots.length + ' lots', bx1 + 4, y0 + 11);
          }
          addRegion(regions[idx], bx1, bx1 + bw, y0, y1, 'bake', batch, res);
          var lc = row.stats && row.stats.lineFlags && row.stats.lineFlags[i];
          if (lc && batch.start >= start && batch.start <= end) {
            var lx = xOf(batch.start);
            drawLineChange(ctx, lx, y0, y1, rangeDays > 7);
            addRegion(regions[idx], lx - 4, lx + 4, rangeDays > 7 ? y0 - 1 : y0, rangeDays > 7 ? y0 + 5 : y1, 'linechange', {
              time: batch.start, recipe: lc.recipe, card: lc.card,
              fromRecipe: lc.fromRecipe, toRecipe: lc.toRecipe,
              fromCard: lc.fromCard, toCard: lc.toCard,
              label: batch.id
            }, res);
          }
        });
      }
    });
    ctx.restore();
    drawGanttChrome(ctx, rows, regions, visible, width, height, labelW, axisH, plotW, start, end, rangeDays, tick);
  }
function drawGanttChrome(ctx, rows, regions, visible, width, height, labelW, axisH, plotW, start, end, rangeDays, tick) {
    ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, labelW, height);
    ctx.strokeStyle = '#e4e9f1'; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(labelW - .5, 0); ctx.lineTo(labelW - .5, height); ctx.stroke();
    ctx.fillStyle = '#6b7a91'; ctx.font = '600 9px "PingFang SC", sans-serif';
    ctx.fillText('资源 / 时间', 14, 20);
    ctx.fillStyle = '#aab5c5'; ctx.font = '8px "PingFang SC", sans-serif';
    ctx.fillText('机台 · 腔体', 14, 34);
    var colX = LABEL_PAD + RES_W;
    STAT_COLS.forEach(function (col) {
      ctx.textAlign = 'right';
      ctx.fillStyle = '#6b7a91'; ctx.font = '600 9px "PingFang SC", sans-serif';
      ctx.fillText(col.label, colX + col.w - 8, 20);
      ctx.fillStyle = '#aab5c5'; ctx.font = '8px "PingFang SC", sans-serif';
      ctx.fillText(col.hint, colX + col.w - 8, 34);
      ctx.textAlign = 'left';
      colX += col.w;
    });
    ctx.strokeStyle = '#eef2f7'; ctx.lineWidth = 1;
    colX = LABEL_PAD + RES_W;
    STAT_COLS.slice(0, -1).forEach(function (col) {
      colX += col.w;
      var sx = Math.round(colX) + .5;
      ctx.beginPath(); ctx.moveTo(sx, 0); ctx.lineTo(sx, height); ctx.stroke();
    });
    rows.forEach(function (row) {
      if (row.kind === 'group') {
        ctx.fillStyle = '#f6f9fd'; ctx.fillRect(0, row.y, labelW - 1, row.h);
        ctx.fillStyle = '#2c3d58'; ctx.font = '700 9.5px "PingFang SC", sans-serif';
        ctx.fillText(row.label, LABEL_PAD + 2, row.y + 16);
        var nameW = ctx.measureText(row.label).width;
        ctx.fillStyle = '#94a1b4'; ctx.font = '8px "PingFang SC", sans-serif';
        ctx.fillText(row.sub, LABEL_PAD + 8 + nameW, row.y + 16);
        drawStatCells(ctx, row.stats, row.y + 16, true);
      } else {
        ctx.fillStyle = '#16233a'; ctx.font = '500 9.5px "PingFang SC", sans-serif';
        ctx.fillText(row.label, LABEL_PAD + 2, row.y + 16);
        if (row.res.kind === 'cp') {
          ctx.fillStyle = '#a6b2c4'; ctx.font = '8px "PingFang SC", sans-serif';
          ctx.textAlign = 'right'; ctx.fillText(row.sub, LABEL_PAD + RES_W - 8, row.y + 16); ctx.textAlign = 'left';
        }
        var dotColor = row.res.kind === 'cp' ? A.PLATFORM_COLORS[platformOfType(row.res.type)] : '#e89b1c';
        ctx.fillStyle = dotColor; ctx.beginPath(); ctx.arc(9, row.y + 13, 2, 0, Math.PI * 2); ctx.fill();
        drawStatCells(ctx, row.stats, row.y + 16);
      }
    });

    ctx.fillStyle = '#fff'; ctx.fillRect(labelW, 0, plotW, axisH);
    ctx.strokeStyle = '#e4e9f1'; ctx.beginPath(); ctx.moveTo(0, axisH - .5); ctx.lineTo(width, axisH - .5); ctx.stroke();
    var xOf = function (ms) { return labelW + (ms - start) / (end - start) * plotW; };
    var lastLabel = -999;
    for (var tt = start; tt <= end; tt += tick) {
      var xx = xOf(tt);
      if (xx < labelW - 1) continue;
      var dt = new Date(tt + 8 * HOUR);
      var txt = '', show = true;
      if (rangeDays === 1) txt = pad2(dt.getUTCHours()) + ':00';
      else if (rangeDays === 3) txt = dt.getUTCHours() === 0 ? (dt.getUTCMonth() + 1) + '/' + dt.getUTCDate() : pad2(dt.getUTCHours()) + ':00';
      else if (rangeDays === 7) txt = dt.getUTCHours() === 0 ? (dt.getUTCMonth() + 1) + '/' + dt.getUTCDate() : '12:00';
      else { txt = (dt.getUTCMonth() + 1) + '/' + dt.getUTCDate(); show = dt.getUTCDate() % 2 === 1; }
      if (show && xx - lastLabel > 30) {
        ctx.fillStyle = '#6b7a91'; ctx.font = '8.5px "PingFang SC", sans-serif';
        ctx.textAlign = 'center'; ctx.fillText(txt, xx, 33); ctx.textAlign = 'left';
        lastLabel = xx;
      }
    }

    A.state.ganttRegions = regions;
    A.state.ganttGeom = { labelW: labelW, axisH: axisH, width: width, height: height, start: start, end: end, xOf: xOf, rows: rows };
    var stat = $('#gantt-footer-stat');
    if (stat) stat.textContent = rows.filter(function (x) { return x.kind === 'machine'; }).length + ' 个资源 · ' + visible + ' 个任务/批次';
  }

  function roundRect(ctx, x, y, w, h, r) {
    r = Math.min(r, w / 2, h / 2);
    ctx.beginPath(); ctx.moveTo(x + r, y); ctx.arcTo(x + w, y, x + w, y + h, r);
    ctx.arcTo(x + w, y + h, x, y + h, r); ctx.arcTo(x, y + h, x, y, r);
    ctx.arcTo(x, y, x + w, y, r); ctx.closePath();
  }

  function drawPM(ctx, xOf, a, b, y0, y1) {
    var x1 = xOf(a), x2 = xOf(b);
    if (x2 <= x1) return;
    ctx.save();
    ctx.fillStyle = 'rgba(116,87,232,.13)'; ctx.fillRect(x1, y0, x2 - x1, y1 - y0);
    ctx.beginPath(); ctx.rect(x1, y0, x2 - x1, y1 - y0); ctx.clip();
    ctx.strokeStyle = 'rgba(116,87,232,.32)'; ctx.lineWidth = 1;
    for (var x = x1 - (y1 - y0); x < x2; x += 5) {
      ctx.beginPath(); ctx.moveTo(x, y1); ctx.lineTo(x + (y1 - y0), y0); ctx.stroke();
    }
    ctx.restore();
  }

  /* 换线标记：短周期用红色竖线贯穿资源行 + 顶部小三角；全月任务过密时
     只在行顶部画红色刻度，避免红色盖住任务条。 */
  function drawLineChange(ctx, x, y0, y1, compact) {
    var px = Math.round(x) + .5;
    ctx.save();
    ctx.strokeStyle = LINE_CHANGE_COLOR;
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(px, y0 - 1);
    ctx.lineTo(px, compact ? y0 + 5 : y1 + 1);
    ctx.stroke();
    if (!compact) {
      ctx.fillStyle = LINE_CHANGE_COLOR;
      ctx.beginPath();
      ctx.moveTo(px - 3.5, y0 - 1);
      ctx.lineTo(px + 3.5, y0 - 1);
      ctx.lineTo(px, y0 + 3.5);
      ctx.closePath();
      ctx.fill();
    }
    ctx.restore();
  }

  function addRegion(arr, x1, x2, y1, y2, type, data, res) {
    arr.push({ x1: x1, x2: x2, y1: y1, y2: y2, type: type, data: data, res: res });
  }
function ganttHit(e) {
    var canvas = $('#gantt-canvas');
    if (!canvas || e.target !== canvas) return null;
    var geom = A.state.ganttGeom;
    if (!geom) return null;
    var rect = canvas.getBoundingClientRect();
    var x = e.clientX - rect.left, y = e.clientY - rect.top;
    if (y < geom.axisH) return null;
    var rowIdx = -1;
    for (var i = 0; i < geom.rows.length; i++) {
      var row = geom.rows[i];
      if (row.kind === 'machine' && y >= row.y && y < row.y + row.h) { rowIdx = i; break; }
    }
    if (rowIdx < 0) return null;
    if (x < geom.labelW) {
      var labelRow = geom.rows[rowIdx];
      return {
        type: 'resource', res: labelRow.res, stats: labelRow.stats,
        x1: 0, x2: geom.labelW, y1: labelRow.y, y2: labelRow.y + labelRow.h
      };
    }
    var arr = A.state.ganttRegions[rowIdx] || [];
    for (var j = arr.length - 1; j >= 0; j--) {
      var h = arr[j];
      if (x >= h.x1 - 1 && x <= h.x2 + 1 && y >= h.y1 && y <= h.y2) return h;
    }
    return null;
  }

  function onGanttMove(e) {
    var hit = ganttHit(e);
    if (!hit) { hideTip(); return; }
    var html = '';
    if (hit.type === 'resource') {
      var s = hit.stats || {};
      html = '<div class="tip-title">' + esc(hit.res.id) + ' · 资源统计</div>' +
        '<div class="tip-row"><span class="tip-muted">换线（Recipe / 换卡）</span><b>' + (s.changes || 0) + ' 次</b></div>' +
        (hit.res.kind === 'cp' ? '<div class="tip-row"><span class="tip-muted">其中换卡（Setup）</span><span>' + (hit.res.setupCount || 0) + ' 次</span></div>' : '') +
        '<div class="tip-row"><span class="tip-muted">末单完成</span><span>' + (s.last == null ? '—' : fmtDateTime(s.last)) + '</span></div>' +
        '<div class="tip-row"><span class="tip-muted">完工窗口</span><span>' + hoursText(s.windowMs) + '</span></div>' +
        '<div class="tip-row"><span class="tip-muted">工作</span><span>' + hoursText(s.workMs) + '</span></div>' +
        '<div class="tip-row"><span class="tip-muted">空闲</span><span>' + hoursText(s.idleMs) + '</span></div>' +
        '<div class="tip-row"><span class="tip-muted">宕机</span><span>' + hoursText(s.downMs) + '</span></div>' +
        '<div class="tip-row"><span class="tip-muted">利用率</span><b>' + (s.util == null ? '—' : (s.util * 100).toFixed(1) + '%') + '</b></div>';
      showTip(html, e); return;
    }
    if (hit.type === 'linechange') {
      var lc = hit.data;
      var reason = lc.recipe && lc.card ? 'Recipe + 针卡' : (lc.recipe ? 'Recipe' : '针卡');
      html = '<div class="tip-title">换线 · ' + esc(hit.res.id) + '</div>' +
        '<div class="tip-row"><span class="tip-muted">触发</span><b>' + reason + '</b></div>' +
        (lc.recipe ? '<div class="tip-row"><span class="tip-muted">Recipe</span><span>' + esc(lc.fromRecipe) + ' → ' + esc(lc.toRecipe) + '</span></div>' : '') +
        (lc.card ? '<div class="tip-row"><span class="tip-muted">针卡</span><span class="mono">' + esc(lc.fromCard) + ' → ' + esc(lc.toCard) + '</span></div>' : '') +
        (lc.label ? '<div class="tip-row"><span class="tip-muted">下一单</span><span>' + esc(lc.label) + '</span></div>' : '') +
        '<div class="tip-row"><span class="tip-muted">时间</span><span>' + fmtDateTime(lc.time) + '</span></div>';
      showTip(html, e); return;
    }
    if (hit.type === 'cp') {
      var t = hit.data;
      html = '<div class="tip-title">' + esc(t.lotId) + ' · ' + esc(t.step) + '</div>' +
        '<div class="tip-row"><span class="tip-muted">机台</span><b>' + esc(t.machine) + '</b></div>' +
        '<div class="tip-row"><span class="tip-muted">Recipe</span><span>' + esc(t.recipe) + '</span></div>' +
        '<div class="tip-row"><span class="tip-muted">针卡</span><span class="mono">' + esc(t.card) + '</span></div>' +
        '<div class="tip-row"><span class="tip-muted">时间</span><span>' + fmtDateTime(t.start) + ' → ' + A.SCHED.fmt(t.end, false) + '</span></div>' +
        '<div class="tip-row"><span class="tip-muted">加工 / 换卡</span><span>' + (t.duration / 60000).toFixed(0) + ' min + ' + (t.setup / 60000).toFixed(0) + ' min</span></div>' +
        '<div class="tip-row"><span class="tip-muted">晶圆</span><span>' + t.wafer + ' pcs</span></div>';
    } else if (hit.type === 'bake') {
      var b = hit.data;
      html = '<div class="tip-title">' + esc(b.id) + ' · ' + esc(b.recipe) + '</div>' +
        '<div class="tip-row"><span class="tip-muted">腔体</span><b>' + esc(b.chamber) + '</b></div>' +
        '<div class="tip-row"><span class="tip-muted">Lots / 片数</span><span>' + b.lots.length + ' / ' + b.wafers + '</span></div>' +
        '<div class="tip-row"><span class="tip-muted">时间</span><span>' + fmtDateTime(b.start) + ' → ' + A.SCHED.fmt(b.end, false) + '</span></div>' +
        '<div class="tip-row"><span class="tip-muted">时长</span><span>' + (b.duration / HOUR).toFixed(1) + ' h</span></div>';
    } else if (hit.type === 'wip') {
      html = '<div class="tip-title">在制占用 · ' + esc(hit.data.lotId) + '</div>' +
        '<div class="tip-row"><span>资源</span><b>' + esc(hit.res.id) + '</b></div>' +
        '<div class="tip-row"><span>预计释放</span><span>' + fmtDateTime(A.SCHED.parseDate(hit.data.end)) + '</span></div>';
    } else if (hit.type === 'pm') {
      html = '<div class="tip-title">PM 维护</div>' +
        '<div class="tip-row"><span class="tip-muted">资源</span><b>' + esc(hit.res.id) + '</b></div>' +
        '<div class="tip-row"><span class="tip-muted">时间</span><span>' + fmtDateTime(hit.data[0]) + ' → ' + fmtDateTime(hit.data[1]) + '</span></div>';
    } else if (hit.type === 'borrow') {
      var br = hit.data;
      html = '<div class="tip-title">借机 ' + esc(br.id) + '</div>' +
        '<div class="tip-row"><span class="tip-muted">机台</span><b>' + esc(br.eqp) + '</b></div>' +
        '<div class="tip-row"><span class="tip-muted">时间</span><span>' + fmtDateTime(br.start) + ' → ' + fmtDateTime(br.end) + '</span></div>' +
        '<div class="tip-row"><span class="tip-muted">执行方式</span><span>固定开始时间，不延期</span></div>';
    }
    showTip(html, e);
  }

  function showTip(html, e) {
    var tip = $('#gantt-tip');
    if (!tip) return;
    tip.innerHTML = html;
    tip.style.display = 'block';
    var left = e.clientX + 14, top = e.clientY + 14;
    var tw = tip.offsetWidth, th = tip.offsetHeight;
    if (left + tw > window.innerWidth - 12) left = e.clientX - tw - 14;
    if (top + th > window.innerHeight - 12) top = e.clientY - th - 14;
    tip.style.left = Math.max(8, left) + 'px';
    tip.style.top = Math.max(8, top) + 'px';
  }
  function hideTip() { var t = $('#gantt-tip'); if (t) t.style.display = 'none'; }

  function locateLot(q) {
    q = (q || '').trim().toUpperCase();
    if (!q || !A.state.result) return;
    var task = A.state.result.cpTasks.filter(function (t) { return t.lotKey === q || t.lotId.toUpperCase() === q; })[0];
    if (!task) {
      var lot = A.state.result.lots.filter(function (l) { return l.key === q || l.id.toUpperCase() === q; })[0];
      if (lot && lot.cpTasks.length) task = lot.cpTasks[0];
    }
    if (!task) { A.toast('未找到 Lot：' + q); return; }
    A.state.gantt.anchor = A.SCHED.parseDate(A.SCHED.dayKey(task.start));
    A.state.gantt.range = '1d';
    A.state.gantt.mode = 'cp';
    A.state.gantt.platform = task.platform;
    A.navigate('gantt');
    A.toast('已定位 ' + task.lotId + ' @ ' + task.machine + ' · ' + fmtDateTime(task.start));
  }

  A.renderGantt = renderGantt;
  A.drawGantt = drawGantt;
  A.ganttHit = ganttHit;
  A.onGanttMove = onGanttMove;
  A.hideTip = hideTip;
  A.locateLot = locateLot;
  A.resourceStats = resourceStats;
  A.ganttLabelWidth = labelWidth;
})();
