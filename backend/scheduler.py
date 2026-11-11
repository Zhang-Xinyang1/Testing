#!/usr/bin/env python3
"""Confirmed scheduling rules, ported from the browser engine to Python.

The engine is a deterministic discrete-event simulation. It keeps the input
read-only, schedules every Lot along its original route order, and returns the
same camelCase structure consumed by the existing presentation layer.
"""
import heapq
import math
import re
import time
from datetime import datetime, timedelta, timezone
from functools import cmp_to_key, lru_cache
from platforms import normalize_platforms, split_platforms
from input_rules import (base_ppn, flow_product, ordered_plans, parse_datetime,
                         pm_events, resolve_wip, row_key)

MIN = 60000
HOUR = 3600000
DAY = 86400000
CST = timezone(timedelta(hours=8))
# Give products with more total CP work a small priority boost so long routes
# do not become the tail of the month.  Fair share remains the primary key.
PRODUCT_WORK_WEIGHT = 0.20


@lru_cache(maxsize=8192)
def parse_date(value):
    """Parse an ISO/date value to epoch milliseconds, matching the JS helper."""
    if value is None or value == '':
        return None
    stamp = parse_datetime(value).timestamp() * 1000
    return int(stamp) if stamp.is_integer() else stamp


def pad(value):
    return '%02d' % int(value)


def fmt(value, with_date=False):
    if value is None or not math.isfinite(value):
        return '—'
    stamp = datetime.fromtimestamp((value + 8 * HOUR) / 1000, timezone.utc)
    prefix = '%d/%d ' % (stamp.month, stamp.day) if with_date else ''
    return prefix + '%02d:%02d' % (stamp.hour, stamp.minute)


def day_key(value):
    stamp = datetime.fromtimestamp((value + 8 * HOUR) / 1000, timezone.utc)
    return '%d-%s-%s' % (stamp.year, pad(stamp.month), pad(stamp.day))


def wafers(rows):
    return sum((row.get('wafer') or 0) for row in rows)


def clamp(value, low, high):
    return max(low, min(high, value))


def groups(value):
    return split_platforms(value)


def merge_mps(rows):
    """Merge MPS rows by PPN prefix; multiple orders/vendors are summed."""
    merged, order = {}, []
    for row in rows or []:
        ppn = base_ppn(row.get('ppn', ''))
        if not ppn:
            continue
        if ppn not in merged:
            merged[ppn] = {'platform': '', 'ppn': ppn, 'mps': 0,
                           '_platforms': []}
            order.append(ppn)
        entry = merged[ppn]
        quantity = row.get('mps') or 0
        if isinstance(quantity, str):
            try:
                quantity = float(quantity)
            except ValueError:
                quantity = 0
        entry['mps'] += quantity
        for group in groups(row.get('platform')):
            if group not in entry['_platforms']:
                entry['_platforms'].append(group)
    result = []
    for ppn in order:
        entry = merged[ppn]
        entry['platform'] = ';'.join(entry.pop('_platforms'))
        result.append(entry)
    return result


def gap(blocks, start, duration, presorted=False):
    current = start
    for begin, end in blocks if presorted else sorted(blocks, key=lambda span: span[0]):
        if current + duration <= begin:
            break
        if current < end and current + duration > begin:
            current = end
    return current


def earliest_slot(resource, start, duration):
    blocks = resource.get('_blocks')
    if blocks is None:
        blocks = sorted(list(resource.get('pm') or []) + list(resource.get('reserved') or []))
    return gap(blocks, max(start, resource['free']), duration, presorted=True)


class Heap:
    """Stable min-heap ordered by event time, then insertion sequence."""

    def __init__(self):
        self.a = []
        self.seq = 0

    def push(self, when, lot):
        heapq.heappush(self.a, (when, self.seq, lot))
        self.seq += 1

    def pop(self):
        return heapq.heappop(self.a)

    def __len__(self):
        return len(self.a)


def _fair(a, b):
    # Lower fair rank first; large total-work products get a small head start.
    a_score = a['fairRank'] - PRODUCT_WORK_WEIGHT * a.get('prodWorkShare', 0)
    b_score = b['fairRank'] - PRODUCT_WORK_WEIGHT * b.get('prodWorkShare', 0)
    if abs(a_score - b_score) > 1e-12:
        return -1 if a_score < b_score else 1
    if a['fairRank'] != b['fairRank']:
        return -1 if a['fairRank'] < b['fairRank'] else 1
    if a['prod'] != b['prod']:
        return -1 if a['prod'] < b['prod'] else 1
    if a['id'] != b['id']:
        return -1 if a['id'] < b['id'] else 1
    a_key, b_key = a.get('key') or '', b.get('key') or ''
    if a_key != b_key:
        return -1 if a_key < b_key else 1
    return 0


def _sort_fair(rows):
    rows.sort(key=cmp_to_key(_fair))


def _busy_ms(intervals, start, end):
    total = 0
    for begin, finish in intervals:
        total += max(0, min(end, finish) - max(start, begin))
    return total


def _assign_preferred_types(ctx):
    """Pre-allocate lots across CP groups by remaining monthly machine time.

    Products that can only run in one group are placed first. Flexible lots are
    then placed largest-load first into the group with the most remaining time,
    which keeps scarce groups available for single-group products.
    """
    month_ms = ctx['monthEnd'] - ctx['start']
    remaining = {}
    for machine in ctx['machines']:
        if not machine['available']:
            continue
        blocked = list(machine.get('pm') or []) + list(machine.get('reserved') or [])
        busy = _busy_ms(blocked, ctx['start'], ctx['monthEnd'])
        remaining[machine['type']] = remaining.get(machine['type'], 0) + max(
            0, month_ms - busy)

    max_prod_work = max([lot.get('prodWork') or 0 for lot in ctx['lots']] + [1])
    for lot in ctx['lots']:
        lot['prodWorkShare'] = (lot.get('prodWork') or 0) / max_prod_work
        lot['cpLoadMs'] = sum(
            op[2] * MIN * lot['wafer'] for op in lot['ops']
            if str(op[0]).startswith('CP')) + lot.get('cpCount', 0) * 5 * MIN

    def choose(lot):
        choices = [group for group in lot.get('usableTypes') or []
                   if group in remaining]
        if not choices:
            return None
        return max(choices, key=lambda group: remaining[group])

    # Fixed single-group products reserve their group first.
    for lot in ctx['lots']:
        if len(lot.get('usableTypes') or []) == 1:
            group = lot['usableTypes'][0]
            lot['preferredType'] = group
            remaining[group] = remaining.get(group, 0) - lot['cpLoadMs']
    flexible = [lot for lot in ctx['lots']
                if len(lot.get('usableTypes') or []) > 1]
    for lot in sorted(flexible, key=lambda item: (-item['cpLoadMs'], item['id'], item.get('key') or '')):
        group = choose(lot)
        if group is None:
            continue
        lot['preferredType'] = group
        remaining[group] = remaining.get(group, 0) - lot['cpLoadMs']


def _parse_route(data, product):
    ops = []
    for source in data.get('flows', {}).get(product) or []:
        op = list(source)
        step = op[0]
        if step == 'BAKE':
            if not re.fullmatch(r'(210|250)C24H', str(op[1])):
                raise ValueError('无效 BAKE Recipe：' + str(op[1]))
            op[2] = int(re.search(r'C(\d+)H$', str(op[1])).group(1))
        if step == 'QTIME1':
            if str(op[1]) not in ('7', '14', '28'):
                raise ValueError('无效 QTIME Recipe：' + str(op[1]))
            op[2] = int(op[1])
        ops.append(op)
    return ops


def _remaining_times(ops, wafer):
    """Optimistic route suffix durations; BAKE releases the lot before cooldown."""
    suffix = [0] * (len(ops) + 1)
    for index in range(len(ops) - 1, -1, -1):
        step, _, duration = ops[index]
        if str(step).startswith('CP'):
            duration *= MIN * wafer
        elif step == 'BAKE':
            duration = (duration + 2) * HOUR
        else:
            duration *= MIN
        suffix[index] = suffix[index + 1] + duration
    return suffix


def _dispatch_key(lot, now, policy, month_ms):
    remaining = lot['remainingMs'][lot['index']]
    score = (lot['fairRank']
             - policy['workWeight'] * lot.get('prodWorkShare', 0)
             - policy['tailWeight'] * remaining / max(1, month_ms)
             + policy.get('productBias', {}).get(lot['prod'], 0)
             + policy.get('lotBias', {}).get(lot['key'], 0))
    # Defer work that cannot possibly finish this month, but still schedule it
    # later so the returned horizon contains every feasible lot.
    return (now + remaining > lot['monthEnd'], score,
            lot['fairRank'], lot['prod'], lot['id'], lot['key'])


def simulate(data, options, policy=None):
    start = parse_date(data['meta']['start'])
    month_end = start + data['meta']['days'] * DAY
    ctx = {
        'data': data, 'options': options, 'start': start, 'monthEnd': month_end,
        'horizonEnd': month_end, 'horizonDays': data['meta']['days'],
        'weekMeta': {}, 'weekEndByProd': {}, 'prodMps': {}, 'prodPlatform': {},
        'platformMps': {}, 'eqpByType': {}, 'machineMap': {}, 'machines': [],
        'chambers': [], 'cards': [], 'cardMap': {}, 'recipeCards': {}, 'lots': [],
        'batches': [], 'pmRecords': [], 'borrows': [], 'wip': data.get('wip') or [],
    }

    mps_rows = merge_mps(data.get('mps') or [])
    for m in mps_rows:
        ctx['prodMps'][m['ppn']] = m['mps']
        ctx['prodPlatform'][m['ppn']] = m['platform']
        ctx['platformMps'][m['platform']] = ctx['platformMps'].get(m['platform'], 0) + m['mps']

    cursor, week = start, 0
    while cursor < month_end:
        week += 1
        dow = datetime.fromtimestamp((cursor + 8 * HOUR) / 1000, timezone.utc).weekday()
        # JS getUTCDay(): Sunday = 0, Monday = 1.
        dow = (dow + 1) % 7
        stop = min(month_end, cursor + (((7 - dow) % 7) + 1) * DAY)
        ctx['weekMeta']['W%d' % week] = {'week': 'W%d' % week, 'start': cursor, 'end': stop}
        cursor = stop
    if data.get('weekly'):
        ctx['weekMeta'] = {}
    for w in data.get('weekly') or []:
        ctx['weekMeta'][w['week']] = {
            'week': w['week'], 'start': parse_date(w['start']),
            'end': parse_date(w['end']) + DAY,
        }

    cp_input = [e for e in data.get('eqp') or []
                if not re.search('OVEN', e.get('type', ''), re.I)]
    for index, e in enumerate(cp_input):
        resource = {
            'kind': 'cp', 'id': e['id'], 'eqp': e['id'], 'type': e['type'],
            'index': index, 'initialCard': e.get('card'), 'card': e.get('card'),
            'cardUpdatedAt': start,
            'initialRecipe': e.get('recipe'), 'free': start, 'pm': [], 'reserved': [],
            'tasks': [], 'borrows': [], 'intervals': [], 'wip': [], 'prodMs': 0,
            'setupMs': 0, 'setupCount': 0, 'borrowMs': 0, 'pmMs': 0,
            'available': e.get('status') == 'RUN',
        }
        ctx['machines'].append(resource)
        ctx['machineMap'][resource['id']] = resource
        ctx['eqpByType'].setdefault(resource['type'], []).append(resource)

    ovens = [e for e in data.get('eqp') or []
             if re.search('OVEN', e.get('type', ''), re.I)]
    bake = data.get('bakeResources') or {}
    if ovens:
        oven_ids = [e['id'] for e in ovens]
    elif bake.get('ids') is not None:
        oven_ids = list(bake['ids'])
    else:
        oven_ids = ['BAKE-' + pad(i + 1) for i in range(int(bake.get('machines') or 7))]
    chamber_count = int(bake.get('chambers') or 4)
    for oven_id in oven_ids:
        for index in range(1, chamber_count + 1):
            available = (not ovens) or any(
                e['id'] == oven_id and e.get('status') == 'RUN' for e in ovens)
            ctx['chambers'].append({
                'kind': 'bake', 'id': oven_id + '-C' + str(index), 'eqp': oven_id,
                'free': start, 'pm': [], 'reserved': [], 'batches': [], 'wip': [],
                'prodMs': 0, 'pmMs': 0, 'available': available,
            })

    for w in ctx['wip']:
        if w.get('chamber'):
            resources = [c for c in ctx['chambers'] if c['id'] == w['chamber']]
        else:
            machine = ctx['machineMap'].get(w.get('eqp'))
            resources = [machine] if machine else []
        for resource in resources:
            resource['reserved'].append([parse_date(w['start']), parse_date(w['end'])])
            resource['wip'].append(w)

    for recipe in data.get('recipes') or []:
        ctx['recipeCards'][recipe['recipe']] = list(recipe['cards'])
        for card_id in recipe['cards']:
            if card_id not in ctx['cardMap']:
                card = {'id': card_id, 'type': '共享类型', 'free': start, 'tasks': [],
                        'busyMs': 0, 'moves': 0, 'recipes': {}}
                ctx['cards'].append(card)
                ctx['cardMap'][card_id] = card
            ctx['cardMap'][card_id]['recipes'][recipe['recipe']] = True

    pm_resources = {resource['id']: resource for resource in ctx['machines'] + ctx['chambers']}
    for plan in pm_events(data):
        resources = [pm_resources[key] for key in plan['resources']]
        blocks = []
        for resource in resources:
            blocks.extend(resource['reserved'])
            blocks.extend(resource['pm'])
        duration = plan['hours'] * HOUR
        first = parse_date(plan['start']) if plan.get('start') else parse_date(plan['early'])
        slot = gap(blocks, first, duration)
        if slot + duration > parse_date(plan['over']) + DAY or (
                plan.get('start') and slot != first):
            raise ValueError('PM 窗口内无连续空档：' + plan['eqp'])
        record = {
            'id': plan['id'], 'eqp': plan['eqp'], 'early': plan['early'],
            'due': plan['due'], 'over': plan['over'], 'hours': plan['hours'],
            'start': slot, 'end': slot + duration, 'status': 'window',
            **{key: plan[key] for key in ('targetKind', 'affectedEqps', 'affectedProbers', 'resources',
                                          'sourceIds', 'sourcePlans', 'merged')},
        }
        ctx['pmRecords'].append(record)
        for resource in resources:
            resource['pm'].append([slot, record['end']])
            resource['pmMs'] += duration

    for index, plan in ordered_plans(data.get('borrow') or [], 'borrow'):
        machine = ctx['machineMap'].get(plan['eqp'])
        duration = plan['hours'] * HOUR
        day = parse_date(plan.get('date') or day_key(parse_date(plan['start'])))
        first = parse_date(plan['start']) if plan.get('start') else day
        latest = first if plan.get('start') else day + DAY - 1
        slot = gap(machine['pm'] + machine['reserved'], first, duration) \
            if machine and machine['available'] else float('inf')
        record = {
            'id': 'BR-' + pad(index + 1), 'eqp': plan['eqp'], 'hours': plan['hours'],
            'req': plan, 'duration': duration, 'delay': 0,
        }
        if slot <= latest:
            record.update({'start': slot, 'end': slot + duration, 'status': 'on-time',
                           'reserved': True})
            machine['reserved'].append([slot, record['end']])
            machine['borrows'].append(record)
            machine['borrowMs'] += duration
        else:
            record['status'] = 'cancel'
            record['reason'] = '原机台当天无连续空档，保留原计划并建议最早可用时间'
            if math.isfinite(slot):
                record['suggestion'] = {'eqp': plan['eqp'], 'start': slot,
                                        'end': slot + duration}
        ctx['borrows'].append(record)

    for resource in ctx['machines'] + ctx['chambers']:
        resource['_blocks'] = sorted(resource['pm'] + resource['reserved'])
    excluded = {w['lotKey'] for w in ctx['wip']}
    order = {}
    events = Heap()
    cp_queues, bake_queues, done = {}, {}, {}
    waiting_ms = ((4 if options.get('bakeWindowHours') is None
                   else options['bakeWindowHours']) * HOUR)
    forbidden = {}
    for constraint in data.get('constraints') or []:
        forbidden[str(constraint['eqp']) + '|' + str(constraint['recipe'])] = True

    flows = data.get('flows') or {}
    for row_number, row in enumerate(data.get('lots') or []):
        if row_key(row_number) in excluded:
            continue
        product = row[1]
        # Prefer the explicit Lot.PPN; fall back to the full lot id or the
        # prefix before the hyphen (PPN1-1 -> PPN1) when the source only
        # provides the lot ID.
        product = flow_product(row[0], product, flows)
        ops = _parse_route(data, product)
        rank = order.get(product, 0)
        order[product] = rank + row[3]
        cp_per_wafer = sum(
            op[2] for op in ops if str(op[0]).startswith('CP'))
        lot = {
            'key': row_key(row_number),
            'id': row[0], 'prod': product, 'platform': row[2], 'type': row[2],
            'wafer': row[3], 'release': row[4], 'week': row[5],
            'weeklyDue': row[6], 'monthlyDue': row[7], 'priority': row[8],
            'ops': ops,
            'cpCount': sum(1 for op in ops if str(op[0]).startswith('CP')),
            'cpMinutesPerWafer': cp_per_wafer,
            'cpMinutes': cp_per_wafer * row[3],
            'prodWork': cp_per_wafer * ctx['prodMps'].get(product, 0),
            'cpTasks': [], 'routeTasks': [], 'bakeTasks': [],
            'cpEnd': None, 'bakeStart': None, 'bakeEnd': None,
            'complete': float('inf'), 'monthEnd': month_end,
            'weekEnd': parse_date(row[6]) + DAY,
            'fairRank': rank / max(1, ctx['prodMps'].get(product, 0)),
            'index': 0, 'ready': parse_date(row[4]), 'status': 'pending',
        }
        ctx['lots'].append(lot)
        allowed = groups(lot['platform'])
        lot['allowedTypes'] = allowed
        cp_ops = [op for op in ops if str(op[0]).startswith('CP')]
        usable = []
        if not cp_ops:
            usable = list(allowed)
        else:
            for group in allowed:
                group_machines = ctx['eqpByType'].get(group) or []
                if not group_machines:
                    continue
                ok = True
                for op in cp_ops:
                    cards = ctx['recipeCards'].get(op[1])
                    if not cards or not any(
                            m['available'] and m['type'] == group
                            and (m['id'] + '|' + str(op[1])) not in forbidden
                            for m in group_machines):
                        ok = False
                        break
                if ok:
                    usable.append(group)
        lot['usableTypes'] = usable
        if policy is not None:
            lot['remainingMs'] = _remaining_times(ops, lot['wafer'])
            # Each operation can use a different allowed group. Requiring one
            # group capable of the *whole* route rejects valid cross-group lots.
            feasible = all(ctx['recipeCards'].get(op[1]) and any(
                m['available'] and m['type'] in allowed
                and (m['id'] + '|' + str(op[1])) not in forbidden
                for m in ctx['machines']) for op in cp_ops)
        else:
            feasible = not cp_ops or bool(usable)
        if not ops or not feasible:
            lot['unscheduledReason'] = '路线缺失或没有 RUN/兼容/非禁加工机台'
            continue
        events.push(lot['ready'], lot)

    _assign_preferred_types(ctx)

    def enter(lot, when):
        if lot['index'] >= len(lot['ops']):
            lot['complete'] = when
            lot['status'] = 'on-time' if when <= month_end else 'late-month'
            done[lot['prod']] = done.get(lot['prod'], 0) + (
                lot['wafer'] if when <= month_end else 0)
            return
        op = lot['ops'][lot['index']]
        lot['ready'] = when
        if op[0] == 'QTIME1':
            end = when + op[2] * MIN
            lot['qtOp'] = op
            lot['routeTasks'].append({'step': op[0], 'recipe': op[1], 'start': when,
                                      'end': end, 'opIndex': lot['index']})
            lot['index'] += 1
            events.push(end, lot)
        elif op[0] == 'BAKE':
            queue = bake_queues.setdefault(op[1], [])
            queue.append(lot)
            _sort_fair(queue)
        else:
            key = lot['prod'] + '|' + str(lot['index'])
            queue = cp_queues.setdefault(key, [])
            queue.append(lot)
            _sort_fair(queue)
    now, loops = start, 0
    while (len(events) or any(cp_queues[k] for k in cp_queues)
           or any(bake_queues[k] for k in bake_queues)):
        loops += 1
        if loops > 500000:
            raise ValueError('调度事件超出保护上限')
        while len(events) and events.a[0][0] <= now:
            when, _, lot = events.pop()
            enter(lot, when)
        next_time = events.a[0][0] if len(events) else float('inf')

        dispatch = {}
        if policy is not None:
            for queue in list(cp_queues.values()) + list(bake_queues.values()):
                for lot in queue:
                    dispatch[lot['key']] = _dispatch_key(
                        lot, now, policy, month_end - start)
                queue.sort(key=lambda lot: dispatch[lot['key']])
            candidates = sorted(
                (lot for queue in cp_queues.values() for lot in queue),
                key=lambda lot: dispatch[lot['key']])
        else:
            candidates = None

        for machine in ctx['machines']:
            if not machine['available']:
                continue
            if machine['free'] > now:
                next_time = min(next_time, machine['free'])
                continue
            # A fixed WIP operation can finish between dispatched tasks and
            # leave a different card installed on this machine.
            released = [w for w in machine['wip']
                        if machine['cardUpdatedAt'] < parse_date(w['end']) <= now]
            if released:
                last_wip = max(released, key=lambda w: parse_date(w['end']))
                machine['card'] = last_wip.get('card') or machine['initialCard']
                machine['cardUpdatedAt'] = parse_date(last_wip['end'])
            if policy is not None:
                available_at = earliest_slot(machine, now, 1)
                if available_at > now:
                    next_time = min(next_time, available_at)
                    continue
            slot_cache = {}
            best, best_card, best_setup = None, None, 0
            pending = candidates if candidates is not None else [
                queue[0] for queue in cp_queues.values() if queue]
            for lot in pending:
                op = lot['ops'][lot['index']]
                if policy is None and lot.get('preferredType') and machine['type'] != lot['preferredType']:
                    continue
                if machine['type'] not in lot['allowedTypes'] or (
                        machine['id'] + '|' + str(op[1])) in forbidden:
                    continue
                cards = ctx['recipeCards'].get(op[1]) or []
                if not cards:
                    continue
                card = machine['card'] if machine['card'] in cards else cards[0]
                setup = 0 if card == machine['card'] else 5 * MIN
                # CP.TIME is minutes per wafer; machine time scales with lot wafers.
                process_ms = op[2] * MIN * lot['wafer']
                duration = process_ms + setup
                if duration not in slot_cache:
                    slot_cache[duration] = earliest_slot(machine, now, duration)
                slot = slot_cache[duration]
                if slot > now:
                    next_time = min(next_time, slot)
                    continue
                if policy is not None:
                    best, best_card, best_setup = lot, card, setup
                    break
                if best is None or _fair(lot, best) < 0 or (
                        _fair(lot, best) == 0 and setup < best_setup):
                    best, best_card, best_setup = lot, card, setup
            if best is None:
                continue
            lot, op = best, best['ops'][best['index']]
            queue = cp_queues[lot['prod'] + '|' + str(lot['index'])]
            if candidates is not None:
                queue.remove(lot)
                candidates.remove(lot)
            else:
                queue.pop(0)
            process_ms = op[2] * MIN * lot['wafer']
            end = now + best_setup + process_ms
            task = {
                'kind': 'cp', 'lotId': lot['id'], 'lotKey': lot['key'], 'prod': lot['prod'],
                'platform': lot['platform'], 'wafer': lot['wafer'], 'step': op[0],
                'recipe': op[1], 'timePerWafer': op[2],
                'duration': process_ms, 'setup': best_setup,
                'blockStart': now, 'start': now + best_setup, 'end': end,
                'machine': machine['id'], 'eqp': machine['id'],
                'type': machine['type'], 'card': best_card, 'week': lot['week'],
                'setupChanged': best_setup > 0, 'opIndex': lot['index'],
            }
            machine['tasks'].append(task)
            machine['intervals'].append([now, end])
            machine['free'] = end
            machine['prodMs'] += task['duration']
            machine['setupMs'] += best_setup
            if best_setup:
                machine['setupCount'] += 1
            machine['card'] = best_card
            machine['cardUpdatedAt'] = end
            lot['cpTasks'].append(task)
            lot['routeTasks'].append(task)
            lot['cpEnd'] = end
            lot['index'] += 1
            events.push(end, lot)
            next_time = min(next_time, end)

        for chamber in ctx['chambers']:
            if not chamber['available']:
                continue
            if chamber['free'] > now:
                next_time = min(next_time, chamber['free'])
                continue
            best_recipe, best = None, None
            for recipe in list(bake_queues.keys()):
                queue = bake_queues[recipe]
                if not queue:
                    continue
                slot = earliest_slot(
                    chamber, now, (queue[0]['ops'][queue[0]['index']][2] + 4) * HOUR)
                if slot > now:
                    next_time = min(next_time, slot)
                    continue
                oldest = min(lot['ready'] for lot in queue)
                if len(queue) < 4 and now < oldest + waiting_ms:
                    next_time = min(next_time, oldest + waiting_ms)
                    continue
                better = best is None or (
                    dispatch[queue[0]['key']] < dispatch[best['key']]
                    if policy is not None else _fair(queue[0], best) < 0)
                if better:
                    best, best_recipe = queue[0], recipe
            if best is None:
                continue
            lots = bake_queues[best_recipe][:4]
            del bake_queues[best_recipe][:4]
            process_duration = best['ops'][best['index']][2] * HOUR
            process_start = now + 2 * HOUR
            process_end = process_start + process_duration
            end = process_end + 2 * HOUR
            batch = {
                'kind': 'bake', 'id': 'B-' + pad(len(ctx['batches']) + 1),
                'recipe': best_recipe,
                'temperature': int(str(best_recipe).split('C')[0]),
                'lots': lots, 'wafers': sum((lot.get('wafer') or 0) for lot in lots),
                'duration': process_duration + 4 * HOUR,
                'processDuration': process_duration, 'warmupHours': 2,
                'cooldownHours': 2, 'ready': max(lot['ready'] for lot in lots),
                'start': now, 'processStart': process_start, 'processEnd': process_end,
                'end': end, 'release': process_end, 'chamber': chamber['id'],
                'eqp': chamber['eqp'], 'week': lots[0]['week'],
                'platform': lots[0]['platform'],
            }
            ctx['batches'].append(batch)
            chamber['batches'].append(batch)
            chamber['prodMs'] += batch['duration']
            chamber['free'] = end
            for lot in lots:
                lot['bakeStart'] = now
                lot['bakeEnd'] = process_end
                lot['bakeChamber'] = chamber['id']
                lot['batchId'] = batch['id']
                lot['bakeTasks'].append(batch['id'])
                lot['bakeOp'] = lot['ops'][lot['index']]
                lot['routeTasks'].append({
                    'step': 'BAKE', 'recipe': best_recipe, 'start': now,
                    'processStart': process_start, 'end': process_end,
                    'resourceEnd': end, 'chamber': chamber['id'],
                    'batchId': batch['id'], 'opIndex': lot['index'],
                })
                lot['index'] += 1
                events.push(process_end, lot)
            next_time = min(next_time, process_end, end)

        if len(events):
            next_time = min(next_time, events.a[0][0])
        if not math.isfinite(next_time):
            break
        now = next_time if next_time > now else now + 1

    for lot in ctx['lots']:
        if not math.isfinite(lot['complete']) and not lot.get('unscheduledReason'):
            lot['unscheduledReason'] = '无可用腔体或资源'
        if not math.isfinite(lot['complete']):
            lot['status'] = 'unscheduled'

    ctx['cpOps'] = sum(len(machine['tasks']) for machine in ctx['machines'])
    ctx['horizonEnd'] = max(month_end, now)
    ctx['horizonDays'] = (ctx['horizonEnd'] - start) / DAY

    result = finalize(ctx)
    result['schemaVersion'] = 'confirmed-2026-11-06'
    result['productStats'] = []
    for m in mps_rows:
        completed_lots = [lot for lot in ctx['lots']
                          if lot['prod'] == m['ppn'] and lot['complete'] <= month_end]
        completed = sum(lot['wafer'] for lot in completed_lots)
        result['productStats'].append({
            'ppn': m['ppn'], 'platform': m['platform'], 'mps': m['mps'],
            'unit': 'wafer', 'completed': completed, 'completedWafers': completed,
            'completedLots': len(completed_lots),
            'rate': completed / m['mps'] if m['mps'] else 1,
            'gap': m['mps'] - completed,
        })
    rates = [p['rate'] for p in result['productStats']]
    result['metrics']['minProductRate'] = min(rates) if rates else 0
    result['metrics']['meanProductRate'] = sum(rates) / len(rates) if rates else 0
    result['metrics']['cpUtil'] = sum(
        sum(max(0, min(month_end, task['end']) - max(start, task['blockStart']))
            for task in machine['tasks'])
        for machine in ctx['machines']
    ) / (max(1, len(ctx['machines'])) * (month_end - start))
    result['metrics']['bakeUtil'] = sum(
        sum(max(0, min(month_end, batch['end']) - max(start, batch['start']))
            for batch in chamber['batches'])
        for chamber in ctx['chambers']
    ) / (max(1, len(ctx['chambers'])) * (month_end - start))
    return result


def finalize(ctx):
    month_ms = ctx['monthEnd'] - ctx['start']
    total_lots = len(ctx['lots'])
    target_wafers = sum(m['mps'] for m in ctx['data'].get('mps') or [])
    completed_wafers = wafers(
        [lot for lot in ctx['lots'] if lot['complete'] <= ctx['monthEnd']])
    wip_keys = {w['lotKey'] for w in ctx['wip']}
    wip_wafers = sum(row[3] for index, row in enumerate(ctx['data'].get('lots') or [])
                     if row_key(index) in wip_keys)

    for machine in ctx['machines']:
        machine['daily'] = [0] * ctx['data']['meta']['days']
        for task in machine['tasks']:
            split_load(machine['daily'], ctx['start'], task['blockStart'], task['end'])
    for chamber in ctx['chambers']:
        chamber['daily'] = [0] * ctx['data']['meta']['days']
        for batch in chamber['batches']:
            split_load(chamber['daily'], ctx['start'], batch['start'], batch['end'])

    weekly_rows = []
    weekly_data = ctx['data'].get('weekly') or []
    has_weekly_targets = bool(weekly_data)
    by_prod_week = {(w['ppn'], w['week']): w for w in weekly_data}
    products = sorted(ctx['prodMps'].keys())
    week_keys = sorted(ctx['weekMeta'].keys(),
                       key=lambda key: ctx['weekMeta'][key]['start'])
    last_week_key = week_keys[-1] if week_keys else None

    def completed_in_week(lot, week_key):
        meta = ctx['weekMeta'][week_key]
        if lot['complete'] < meta['start']:
            return False
        if lot['complete'] < meta['end']:
            return True
        return week_key == last_week_key and lot['complete'] <= ctx['monthEnd']

    if not has_weekly_targets:
        for product in products:
            product_lots = [lot for lot in ctx['lots'] if lot['prod'] == product]
            planned_cum = 0
            for week_key in week_keys:
                meta = ctx['weekMeta'][week_key]
                planned_week = wafers(
                    [lot for lot in product_lots if completed_in_week(lot, week_key)])
                planned_cum += planned_week
                weekly_rows.append({
                    'platform': ctx['prodPlatform'][product], 'ppn': product,
                    'week': week_key, 'start': meta['start'], 'end': meta['end'],
                    'target': 0, 'targetCum': 0, 'plannedWeek': planned_week,
                    'plannedCum': planned_cum, 'gap': planned_cum,
                    'attainment': 0, 'status': 'no-target',
                })
    else:
        for product in products:
            cum_target = 0
            product_lots = [lot for lot in ctx['lots'] if lot['prod'] == product]
            for week_key in week_keys:
                weekly = by_prod_week.get((product, week_key))
                if not weekly:
                    continue
                cum_target += weekly['target']
                week_start = parse_date(weekly['start'])
                week_end = parse_date(weekly['end']) + DAY
                planned_week = wafers([
                    lot for lot in product_lots
                    if week_start <= lot['complete'] < week_end
                    or (lot['complete'] == week_end == ctx['monthEnd'])])
                planned_cum = wafers(
                    [lot for lot in product_lots if lot['complete'] < week_end
                     or (lot['complete'] == week_end == ctx['monthEnd'])])
                gap_value = planned_cum - cum_target
                weekly_rows.append({
                    'platform': weekly['platform'], 'ppn': product,
                    'week': week_key, 'start': week_start, 'end': week_end,
                    'target': weekly['target'], 'targetCum': cum_target,
                    'plannedWeek': planned_week, 'plannedCum': planned_cum,
                    'gap': gap_value,
                    'attainment': planned_cum / cum_target if cum_target else 0,
                    'status': 'ahead' if gap_value >= 0 else 'gap',
                })
    weekly_summary = []
    for week_key in week_keys:
        rows = [row for row in weekly_rows if row['week'] == week_key]
        meta = ctx['weekMeta'].get(week_key, {})
        target = sum(row['target'] for row in rows)
        target_cum = sum(row['targetCum'] for row in rows)
        planned_week = sum(row['plannedWeek'] for row in rows)
        planned_cum = sum(row['plannedCum'] for row in rows)
        if has_weekly_targets:
            lots_week = [lot for lot in ctx['lots'] if lot['week'] == week_key]
            on_time = sum(
                1 for lot in lots_week
                if lot['complete'] <= (
                    ctx['weekMeta'][week_key]['end']
                    if week_key in ctx['weekMeta'] else lot['weekEnd']))
        else:
            lots_week = [lot for lot in ctx['lots'] if completed_in_week(lot, week_key)]
            on_time = len(lots_week)
        weekly_summary.append({
            'week': week_key, 'start': meta.get('start'), 'end': meta.get('end'),
            'target': target, 'targetCum': target_cum, 'plannedWeek': planned_week,
            'plannedCum': planned_cum, 'gap': planned_cum - target_cum,
            'attainment': planned_cum / target_cum if target_cum else 0,
            'lots': len(lots_week), 'onTime': on_time,
            'onTimeRate': on_time / len(lots_week) if lots_week else 1,
            'hasTargets': has_weekly_targets,
        })

    platform_stats = []
    for platform in sorted(ctx['platformMps'].keys()):
        platform_type = (ctx['data'].get('platformType') or {}).get(platform)
        platform_machines = [m for m in ctx['machines'] if m['type'] in groups(platform)]
        product_lots = [lot for lot in ctx['lots']
                        if ctx['prodPlatform'].get(lot['prod']) == platform]
        done = wafers([lot for lot in product_lots
                       if lot['complete'] <= ctx['monthEnd']])
        on_time = wafers([lot for lot in product_lots if lot['status'] == 'on-time'])
        util = (sum(m['prodMs'] + m['setupMs'] for m in platform_machines)
                / (len(platform_machines) * month_ms)) if platform_machines else 0
        raw_mps = ctx['platformMps'].get(platform, 0)
        mps_value = raw_mps or len(product_lots)
        entry = {
            'platform': platform, 'mps': mps_value,
            'lots': len(product_lots), 'completed': done, 'onTime': on_time,
            'onTimeRate': on_time / raw_mps if raw_mps else 0,
            'cpUtil': clamp(util, 0, 1), 'machines': len(platform_machines),
            'setups': sum(m['setupCount'] for m in platform_machines),
        }
        if platform_type is not None:
            entry['type'] = platform_type
        platform_stats.append(entry)

    cp_tasks = []
    for machine in ctx['machines']:
        cp_tasks.extend(machine['tasks'])
    ctx['cpTasks'] = cp_tasks
    ctx['weeklyRows'] = weekly_rows
    ctx['weeklySummary'] = weekly_summary
    ctx['platformStats'] = platform_stats
    completed_month = sum(1 for lot in ctx['lots']
                          if lot['complete'] <= ctx['monthEnd'])
    on_time = sum(1 for lot in ctx['lots'] if lot['status'] == 'on-time')
    late_week = sum(1 for lot in ctx['lots'] if lot['status'] == 'late-week')
    late_month = sum(1 for lot in ctx['lots'] if lot['status'] == 'late-month')
    setups = sum(machine['setupCount'] for machine in ctx['machines'])
    cp_makespan = max([ctx['start']] + [m['free'] for m in ctx['machines']])
    bake_makespan = max([ctx['start']] + [c['free'] for c in ctx['chambers']])
    makespan = max(
        cp_makespan, bake_makespan,
        max([ctx['start']] + [lot['complete'] for lot in ctx['lots']
                              if math.isfinite(lot['complete'])]),
    )
    bake_load = (sum(c['prodMs'] for c in ctx['chambers'])
                 / (len(ctx['chambers']) * month_ms)) if ctx['chambers'] else 0
    card_util = (sum(c['busyMs'] for c in ctx['cards'])
                 / (max(1, len(ctx['cards'])) * month_ms))
    pm_in_window = sum(1 for record in ctx['pmRecords']
                       if record['status'] == 'window')
    borrow_scheduled = sum(1 for record in ctx['borrows']
                           if record['status'] != 'cancel')
    borrow_canceled = len(ctx['borrows']) - borrow_scheduled
    borrow_delayed = sum(1 for record in ctx['borrows']
                         if record['status'] == 'delayed')
    borrow_max_delay = max([record.get('delay') or 0 for record in ctx['borrows']]
                           + [0])
    avg_batch_lots = (sum(len(batch['lots']) for batch in ctx['batches'])
                      / len(ctx['batches'])) if ctx['batches'] else 0
    max_batch_wafers = max([batch['wafers'] for batch in ctx['batches']] + [0])
    avg_batch_wafers = (sum(batch['wafers'] for batch in ctx['batches'])
                        / len(ctx['batches'])) if ctx['batches'] else 0

    metrics = {
        'totalLots': total_lots, 'completedMonth': completed_month,
        'completedLots': completed_month, 'wipLots': len(ctx['wip']),
        'mpsUnit': 'wafer', 'totalWafers': target_wafers,
        'scheduledWafers': wafers(ctx['lots']), 'completedWafers': completed_wafers,
        'wipWafers': wip_wafers,
        'unscheduledLots': sum(1 for lot in ctx['lots']
                               if lot.get('unscheduledReason')),
        'completionRate': completed_wafers / target_wafers if target_wafers else 0,
        'onTime': on_time,
        'onTimeRate': completed_wafers / target_wafers if target_wafers else 0,
        'lateWeek': late_week, 'lateMonth': late_month,
        'cpOps': ctx.get('cpOps') or len(cp_tasks), 'cpTasks': len(cp_tasks),
        'batches': len(ctx['batches']), 'avgBatchLots': avg_batch_lots,
        'maxBatchWafers': max_batch_wafers, 'avgBatchWafers': avg_batch_wafers,
        'cpUtil': clamp(
            sum(m['prodMs'] + m['setupMs'] for m in ctx['machines'])
            / (max(1, len(ctx['machines'])) * month_ms), 0, 1),
        'bakeUtil': clamp(bake_load, 0, 1), 'bakeLoad': bake_load,
        'cardUtil': clamp(card_util, 0, 1), 'setups': setups,
        'setupMinutes': setups * (ctx['options'].get('setupMinutes')
                                  if ctx['options'].get('setupMinutes') is not None else 5),
        'pmCount': len(ctx['pmRecords']), 'pmInWindow': pm_in_window,
        'pmInputCount': len(ctx['data'].get('pm') or []),
        'pmMergedRows': len(ctx['data'].get('pm') or []) - len(ctx['pmRecords']),
        'borrowTotal': len(ctx['borrows']), 'borrowScheduled': borrow_scheduled,
        'borrowCanceled': borrow_canceled, 'borrowDelayed': borrow_delayed,
        'borrowCancelRate': borrow_canceled / len(ctx['borrows'])
        if ctx['borrows'] else 0,
        'borrowMaxDelay': borrow_max_delay,
        'cpMakespan': cp_makespan, 'bakeMakespan': bake_makespan,
        'makespan': makespan, 'horizonDays': (makespan - ctx['start']) / DAY,
    }
    alerts = []
    if has_weekly_targets:
        for week in weekly_summary:
            if week['gap'] < 0:
                alerts.append({
                    'level': 'warn', 'title': week['week'] + ' 周目标缺口',
                    'text': '累计目标 %s wafer，累计完成 %s wafer，缺口 %s lot。'
                            % (week['targetCum'], week['plannedCum'], -week['gap']),
                })
    if late_month > 0:
        alerts.append({
            'level': 'danger', 'title': '月度未完成',
            'text': '当前启发式方案有 %d lot 未在月内完成；需诊断资源及时间缺口，'
                    '不能据此判定问题无解。' % late_month,
        })
    if late_week > 0 and has_weekly_targets:
        alerts.append({
            'level': 'warn', 'title': '晚于周目标',
            'text': '%d lot 晚于目标周完成，可由后续周提前完成量抵扣。' % late_week,
        })
    if metrics['borrowCancelRate'] > 0.1:
        alerts.append({
            'level': 'danger', 'title': '借机计划需协调',
            'text': '取消率 %.1f%%，请查看原机台建议时段。'
                    % (metrics['borrowCancelRate'] * 100),
        })
    elif borrow_canceled > 0:
        alerts.append({
            'level': 'info', 'title': '借机取消',
            'text': '取消 %d / %d 条，取消率 %.1f%%。'
                    % (borrow_canceled, len(ctx['borrows']),
                       metrics['borrowCancelRate'] * 100),
        })
    if metrics['bakeLoad'] > 1:
        alerts.append({
            'level': 'danger', 'title': 'BAKE 产能不足',
            'text': '当前方案 BAKE 总占腔负荷 %.1f%%，包含跨月批次；请结合产品路线、'
                    '实际月内占用与达成率分析，不能据此认定已求得最优解。'
                    % (metrics['bakeLoad'] * 100),
        })
    elif metrics['bakeUtil'] > 0.85:
        alerts.append({
            'level': 'warn', 'title': 'BAKE 高负荷',
            'text': 'BAKE 腔体利用率 %.1f%%，建议关注组批与 PM 窗口。'
                    % (metrics['bakeUtil'] * 100),
        })
    for machine in ctx['machines']:
        utilization = (machine['prodMs'] + machine['setupMs']) / month_ms
        if utilization > 0.85:
            alerts.append({
                'level': 'warn', 'title': machine['id'] + ' 高负载',
                'text': '利用率 %.0f%%。' % (utilization * 100),
            })
    if metrics['pmInWindow'] < metrics['pmCount']:
        alerts.append({
            'level': 'danger', 'title': 'PM 越窗',
            'text': '%d 次 PM 未落在允许窗口内。'
                    % (metrics['pmCount'] - metrics['pmInWindow']),
        })
    if not alerts:
        alerts.append({
            'level': 'ok', 'title': '排产完成',
            'text': '全部 lot 在月度截止前完成，未发现硬约束冲突。',
        })
    if ctx['wip']:
        alerts.append({
            'level': 'info', 'title': '在制 Lot 已排除',
            'text': '%d lot 不参加本次排产及算法完成量统计，资源占用保持不变。'
                    % len(ctx['wip']),
        })
    ctx['alerts'] = alerts
    ctx['metrics'] = metrics
    return {
        'start': ctx['start'], 'monthEnd': ctx['monthEnd'],
        'horizonEnd': ctx['horizonEnd'], 'options': ctx['options'],
        'metrics': metrics, 'weekly': weekly_summary, 'weeklyRows': weekly_rows,
        'weeklyHasTargets': has_weekly_targets, 'platformStats': platform_stats,
        'alerts': alerts, 'machines': ctx['machines'],
        'chambers': ctx['chambers'], 'cards': ctx['cards'],
        'pmRecords': ctx['pmRecords'], 'borrows': ctx['borrows'],
        'batches': ctx['batches'], 'cpTasks': cp_tasks, 'lots': ctx['lots'],
        'wip': ctx['wip'], 'data': ctx['data'],
    }


def split_load(values, start, begin, end):
    begin = max(begin, start)
    end = min(end, start + len(values) * DAY)
    if end <= begin:
        return
    index = int(math.floor((begin - start) / DAY))
    last = int(math.floor((end - start - 1) / DAY))
    while index <= last and index < len(values):
        slice_start = max(begin, start + index * DAY)
        slice_end = min(end, start + (index + 1) * DAY)
        if slice_end > slice_start:
            values[index] += slice_end - slice_start
        index += 1


def make_json_safe(value):
    """Convert non-finite floats to null and datetime values to ISO strings."""
    if isinstance(value, dict):
        return {str(key): make_json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [make_json_safe(item) for item in value]
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def run(data, options=None):
    from optimizer import search_schedules

    data = normalize_platforms(data)
    data['wip'] = resolve_wip(data)
    options = dict(options or {})
    options['strategy'] = 'attainment'
    options['setupMinutes'] = 5
    raw_window = options.get('bakeWindowHours')
    if raw_window is None:
        windows = [0, 4, 8]
    elif isinstance(raw_window, (list, tuple)):
        windows = list(raw_window)
    else:
        windows = [raw_window]
    started = time.monotonic()
    if not windows:
        windows = [0]
    if len(windows) > 8 or any(
            isinstance(w, bool) or not isinstance(w, (int, float))
            or not math.isfinite(w) or not 0 <= w <= 24 for w in windows):
        raise ValueError('BAKE 等待窗口须为 0 至 24 小时，最多 8 个候选值')
    windows = list(dict.fromkeys(windows))
    best = search_schedules(data, options, windows, simulate)
    best['runMs'] = int((time.monotonic() - started) * 1000)
    best['runAt'] = datetime.now(timezone.utc).isoformat(
        timespec='milliseconds').replace('+00:00', 'Z')
    return make_json_safe(best)


run_schedule = run


def compact_result(result):
    """Trim duplicate references before sending the result to the browser.

    The full result stays available to Python tools and tests. The browser only
    needs one copy of each lot, task and batch; batch.lots keeps the small
    summary used by the batch drawer and diagnostics.
    """
    compact = dict(result)
    compact.pop('data', None)
    lots = []
    for source in compact.get('lots') or []:
        lot = dict(source)
        for key in ('allowedTypes', 'bakeOp', 'qtOp', 'remainingMs'):
            lot.pop(key, None)
        lots.append(lot)
    compact['lots'] = lots
    batches = []
    for source in compact.get('batches') or []:
        batch = dict(source)
        batch['lots'] = [{
            'id': lot['id'], 'key': lot.get('key'), 'prod': lot['prod'],
            'platform': lot['platform'], 'wafer': lot['wafer'],
        } for lot in source.get('lots') or []]
        batches.append(batch)
    compact['batches'] = batches
    batch_map = {batch['id']: batch for batch in batches}
    chambers = []
    for source in compact.get('chambers') or []:
        chamber = dict(source)
        chamber.pop('_blocks', None)
        chamber.pop('daily', None)
        chamber.pop('reserved', None)
        chamber['batches'] = [batch_map[batch['id']]
                              for batch in source.get('batches') or []
                              if batch['id'] in batch_map]
        chambers.append(chamber)
    compact['chambers'] = chambers
    machines = []
    for source in compact.get('machines') or []:
        machine = dict(source)
        machine.pop('_blocks', None)
        machine.pop('cardUpdatedAt', None)
        machine.pop('daily', None)
        machine.pop('intervals', None)
        machines.append(machine)
    compact['machines'] = machines
    return compact
