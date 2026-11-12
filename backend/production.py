"""Production snapshot contract, isolated workspace and rolling-plan projection.

MES transport is intentionally external: callers submit a complete snapshot.
Synthetic data uses the same validator and planner, with an explicit source tag.
"""
import copy
import hashlib
import json
import math
import re
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from input_rules import identifier, parse_datetime
from production_rules import rules
from schedule_history import _write
from scheduler import run, compact_result, make_json_safe, parse_date, iso_date, HOUR, DAY

LOCK = threading.RLock()
CST = timezone(timedelta(hours=8))
STATES = ('READY', 'FUTURE', 'HOLD', 'RUNNING', 'DONE')


def stamp():
    return datetime.now(CST).isoformat(timespec='seconds')


def identity(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def folder(config):
    return Path(config).resolve().parent / 'production'


def read_state(config):
    path = folder(config) / 'state.json'
    if path.exists():
        return json.loads(path.read_text(encoding='utf-8'))
    return {'revision':0, 'snapshot':None, 'rules':rules(), 'active':None,
            'history':[], 'events':[], 'lastCheck':None, 'changePending':False}


def write_state(config, state):
    folder(config).mkdir(parents=True, exist_ok=True)
    _write(folder(config) / 'state.json', state)


def month_bounds(month):
    first = datetime.strptime(month, '%Y-%m').replace(tzinfo=CST)
    last = (first.replace(day=28) + timedelta(days=4)).replace(day=1)
    return first, last


def validate_snapshot(raw):
    if not isinstance(raw, dict):
        raise ValueError('MES 快照必须为对象')
    d = copy.deepcopy(raw)
    for key in ('snapshotId','asOf','month','demands','lots','equipment','flows','recipes'):
        if key not in d:
            raise ValueError('MES 快照缺少字段：' + key)
    if d.get('schemaVersion') != 1 or d.get('source') not in ('mes','demo'):
        raise ValueError('schemaVersion 必须为 1，source 必须为 mes 或 demo')
    if type(d.get('identityStable', False)) is not bool:
        raise ValueError('identityStable 必须为布尔值')
    if not isinstance(d['snapshotId'],str) or not d['snapshotId'].strip():
        raise ValueError('snapshotId 必须为非空字符串')
    if not isinstance(d['month'],str) or not re.fullmatch(r'\d{4}-\d{2}',d['month']):
        raise ValueError('计划月份须为 YYYY-MM')
    first, last = month_bounds(d['month'])
    asof = parse_datetime(d['asOf'])
    if not first <= asof < last:
        raise ValueError('快照时点必须在计划月份内')
    def quantity(value, label, low=0, high=100000000):
        if type(value) is not int or not low <= value <= high:
            raise ValueError(label + ' 必须为规定范围内的整数')
    def date(value, label):
        try:
            return parse_datetime(value)
        except (ValueError, TypeError, OverflowError) as error:
            raise ValueError(label + ' 日期无效') from error
    for table in ('demands','lots','equipment','recipes','pm','borrow','externalBlocks','constraints'):
        d.setdefault(table, [])
        if not isinstance(d[table], list) or any(not isinstance(row, dict) for row in d[table]):
            raise ValueError(table + ' 必须为记录列表')
    if len(d['lots']) > 20000:
        raise ValueError('首版单次快照最多支持 20000 个 Lot')
    products, eqps, ids = set(), {}, set()
    for row in d['demands']:
        row['ppn'] = identifier(row.get('ppn'))
        if not row['ppn'] or '-' in row['ppn'] or row['ppn'] in products:
            raise ValueError('需求 PPN 须为已汇总的唯一产品前缀，不包含厂家后缀')
        quantity(row.get('wafer'), '需求 wafer')
        products.add(row['ppn'])
    for row in d['equipment']:
        for key in ('id','type','status','recipe','card','proberId'):
            row[key] = identifier(row.get(key))
        if not row['id'] or not row['type'] or row['id'] in eqps:
            raise ValueError('设备编号和 TYPE 必填，设备编号须唯一')
        if row['status'] not in ('RUN','DOWN','PM','OFFLINE'):
            raise ValueError('设备状态须为 RUN/DOWN/PM/OFFLINE')
        eqps[row['id']] = row
    if not eqps or not products:
        raise ValueError('快照至少需要一项需求和一台设备')
    if not isinstance(d['flows'], dict) or any(
            not isinstance(route, list) or any(not isinstance(op, list) or len(op) != 3 for op in route)
            for route in d['flows'].values()):
        raise ValueError('flows 须为 PPN 到 [工序, Recipe, 工时] 列表的映射')
    if len({identifier(k) for k in d['flows']}) != len(d['flows']):
        raise ValueError('路线 PPN 忽略大小写后重复')
    d['flows'] = {identifier(k):[[identifier(op[0]), identifier(op[1]), op[2]] for op in route]
                  for k,route in d['flows'].items()}
    recipes = {}
    for row in d['recipes']:
        row['recipe'] = identifier(row.get('recipe'))
        if not isinstance(row.get('cards'), list) or not row['cards']:
            raise ValueError('Recipe 必须有针卡列表')
        row['cards'] = [identifier(card) for card in row['cards']]
        if row['recipe'] in recipes or not row['recipe'] or not all(row['cards']):
            raise ValueError('Recipe 或针卡编号无效、重复')
        recipes[row['recipe']] = row['cards']
    for ppn in products:
        route = d['flows'].get(ppn)
        if not route:
            raise ValueError('产品缺少路线：' + ppn)
        for step, recipe, duration in route:
            if step.startswith('CP'):
                if recipe not in recipes or isinstance(duration, bool) or not isinstance(duration, (int,float)) or not math.isfinite(duration) or duration <= 0:
                    raise ValueError('CP 工时或 Recipe 无效：' + ppn)
            elif step == 'BAKE':
                if recipe not in ('210C24H','250C24H') or duration != 24:
                    raise ValueError('BAKE 只支持已确认的 24 小时工艺')
            else:
                raise ValueError('生产快照只接收 CP/BAKE 加工工序；静置由完工时间单独计算：' + step)
    if any(op[0] == 'BAKE' for route in d['flows'].values() for op in route) and not any('OVEN' in e['type'] for e in eqps.values()):
        raise ValueError('路线包含 BAKE，必须提供实际 OVEN 资源')
    quantity(d.get('bakeChambers', 4), 'BAKE 腔体', 1, 100)
    types = {row['type'] for row in eqps.values() if 'OVEN' not in row['type']}
    for index,row in enumerate(d['lots']):
        for key in ('ppn','state','platform'):
            row[key] = identifier(row.get(key))
        row['platform'] = ';'.join(dict.fromkeys(identifier(part) for part in row['platform'].split(';') if identifier(part)))
        row['uid'] = identifier(row.get('uid'))
        if not row['uid']:
            if d.get('identityStable'):
                raise ValueError('稳定标识模式下每个 Lot 都必须提供 uid')
            row['uid'] = 'SNAP-%s-%06d' % (identity(d['snapshotId'])[:8], index)
        if row['uid'] in ids:
            raise ValueError('Lot uid 重复：' + row['uid'])
        ids.add(row['uid'])
        if not str(row.get('lotId','')).strip() or row['ppn'] not in products or row['state'] not in STATES:
            raise ValueError('LotID、PPN 或状态无效')
        quantity(row.get('wafer'), 'Lot wafer', 1, 25)
        if not row['platform'] or set(row['platform'].split(';')) - types:
            raise ValueError('Lot Platform 未匹配设备 TYPE：' + row['lotId'])
        date(row.get('shipDue'), '内部计划出货日')
        if row.get('availableAt'):
            date(row['availableAt'], '预计可用时间')
        if row['state'] == 'READY' and (not row.get('availableAt') or date(row['availableAt'], '可用时间') > asof):
            raise ValueError('已到位 Lot 的可用时间必须不晚于快照时点')
        if row['state'] == 'DONE':
            completed = date(row.get('actualComplete'), '实际完工')
            if completed > asof:
                raise ValueError('实际完工不能晚于快照时点')
        if row['state'] == 'RUNNING':
            current = row.get('current') or {}
            for key in ('eqp','recipe','card','chamber','step'):
                current[key] = identifier(current.get(key))
            if current['eqp'] not in eqps or not date(current.get('start'),'当前工序开始') <= asof < date(current.get('end'),'预计释放'):
                raise ValueError('在制当前工序须覆盖快照时点，且设备存在')
            if 'OVEN' in eqps[current['eqp']]['type']:
                if not re.fullmatch(re.escape(current['eqp']) + r'-C[1-9]\d*', current['chamber']) or int(current['chamber'].rsplit('-C',1)[1]) > d.get('bakeChambers',4):
                    raise ValueError('BAKE 在制需要有效腔体编号')
                if current['recipe'] not in ('210C24H','250C24H'):
                    raise ValueError('BAKE 在制需要有效烘烤 Recipe')
                if current['step'] != 'BAKE':
                    raise ValueError('烤箱在制工序须为 BAKE')
            elif current['recipe'] not in recipes or current['card'] not in recipes[current['recipe']]:
                raise ValueError('CP 在制需要有效 Recipe 与针卡')
            elif not current['step'].startswith('CP'):
                raise ValueError('CP 在制必须提供当前 CP 工序')
            row['current'] = current
        if row.get('externalComplete'):
            if row['state'] != 'RUNNING' or date(row['externalComplete'],'外部完工预测') < date(row['current']['end'],'预计释放'):
                raise ValueError('外部完工预测仅适用于在制，且不得早于当前工序释放')
    for row in d['borrow']:
        row['eqp'], row['recipe'] = identifier(row.get('eqp')), identifier(row.get('recipe'))
        if row['eqp'] not in eqps or 'OVEN' in eqps[row['eqp']]['type'] or row['recipe'] not in recipes:
            raise ValueError('借机需要有效 CP 机台和要求 Recipe')
        begin = date(row.get('start'), '借机开始')
        if begin < asof:
            raise ValueError('借机开始不能早于快照；正在借机请提供 externalBlocks')
        row['date'] = begin.astimezone(CST).date().isoformat()
        if isinstance(row.get('hours'), bool) or not isinstance(row.get('hours'), (int,float)) or not math.isfinite(row['hours']) or not 0.6 <= row['hours'] <= 24:
            raise ValueError('借机时长须为 0.6 至 24 小时')
    chambers = {e['id'] + '-C%d' % c for e in eqps.values() if 'OVEN' in e['type'] for c in range(1,d.get('bakeChambers',4)+1)}
    for block in d['externalBlocks']:
        block['resource'] = identifier(block.get('resource'))
        if block['resource'] not in set(eqps) | chambers or date(block.get('end'),'外部占用结束') <= date(block.get('start'),'外部占用开始'):
            raise ValueError('外部资源占用无效')
        for key in ('card','recipe'):
            block[key] = identifier(block.get(key))
        if block['recipe'] and (block['recipe'] not in recipes or block['card'] not in recipes[block['recipe']]):
            raise ValueError('外部占用结束状态需要有效 Recipe 与针卡')
    # Reuse the established pairing/window validator for PM and constraints.
    from input_rules import pm_events
    for p in d['pm']:
        p['eqp'] = identifier(p.get('eqp'))
        p['targetKind'] = identifier(p.get('targetKind'))
        early,due,over = (date(p.get(k),'PM '+k) for k in ('early','due','over'))
        hours = p.get('hours')
        if not early.date() <= due.date() <= over.date() or isinstance(hours,bool) or not isinstance(hours,(int,float)) or not math.isfinite(hours) or not 0 < hours <= 744:
            raise ValueError('PM 窗口或时长无效')
        if p.get('start'):
            begin = date(p['start'],'PM 开始')
            if begin < early or begin + timedelta(hours=hours) > over.replace(hour=0,minute=0,second=0,microsecond=0)+timedelta(days=1):
                raise ValueError('PM 指定时间超出维护窗口')
    pm_events({'eqp':d['equipment'], 'pm':d['pm'], 'bakeResources':{'chambers':d.get('bakeChambers',4)}})
    for row in d['constraints']:
        row['eqp'], row['recipe'] = identifier(row.get('eqp')), identifier(row.get('recipe'))
        if row['eqp'] not in eqps or row['recipe'] not in recipes:
            raise ValueError('禁加工引用未知机台或 Recipe')
    return d


def context_hash(state):
    return identity({'snapshot':state['snapshot'], 'rules':state['rules'],
                     'active':(state.get('active') or {}).get('id')})


def record_change(state, kind, reason):
    state['revision'] += 1
    state['events'] = [{'time':stamp(), 'kind':kind, 'reason':reason}] + state.get('events', [])[:99]
    state['changePending'] = True


def update(config, action, body):
    with LOCK:
        state = read_state(config)
        if action == 'demo':
            from production_demo import demo_snapshot
            if state['snapshot'] and state['snapshot']['source'] != 'demo':
                raise ValueError('当前已接入 MES 快照，演示入口不会覆盖现场数据')
            state['snapshot'] = validate_snapshot(demo_snapshot())
            state['active'] = None
            record_change(state, '模拟数据', '载入演示快照，等待人工试排')
        elif action == 'snapshot':
            snapshot = validate_snapshot(body)
            old = state.get('snapshot')
            if old == snapshot:
                return state
            if old and snapshot['source'] == old['source'] and parse_date(snapshot['asOf']) < parse_date(old['asOf']):
                raise ValueError('拒绝早于当前时点的过期快照')
            state['snapshot'] = snapshot
            if old and (old['source'] != snapshot['source'] or old['month'] != snapshot['month']):
                state['active'] = None
            record_change(state, 'MES 快照', '接收快照 ' + str(snapshot['snapshotId']))
        elif action == 'rules':
            state['rules'] = rules(body)
            record_change(state, '规则修改', '生产规则已保存，候选方案需重新试排')
        elif action == 'event':
            if not state['snapshot'] or state['snapshot']['source'] != 'demo':
                raise ValueError('场景事件只用于模拟数据；现场变化由 MES 快照提供')
            d = state['snapshot']
            reason = str(body.get('reason','')).strip()
            if not reason:
                raise ValueError('请填写变更原因')
            lot = next((x for x in d['lots'] if x['uid'] == body.get('uid')), None)
            if body.get('kind') in ('arrival','due') and lot:
                field = 'availableAt' if body['kind'] == 'arrival' else 'shipDue'
                lot[field] = body.get('time')
                if field == 'availableAt':
                    lot['state'] = 'READY' if parse_date(lot[field]) <= parse_date(d['asOf']) else 'FUTURE'
            elif body.get('kind') == 'down':
                machine = next((x for x in d['equipment'] if x['id'] == body.get('eqp')), None)
                if not machine:
                    raise ValueError('设备不存在')
                d['externalBlocks'].append({'resource':machine['id'], 'start':d['asOf'], 'end':body.get('time'), 'reason':reason})
            else:
                raise ValueError('变更类型或 Lot 无效')
            state['snapshot'] = validate_snapshot(d)
            record_change(state, body['kind'], reason)
        else:
            raise ValueError('未知生产工作台操作')
        write_state(config, state)
        return state


def daily_check(config):
    with LOCK:
        state = read_state(config)
        now = datetime.now(CST)
        today = now.date().isoformat()
        if not state['snapshot'] or now.strftime('%H:%M') < state['rules']['checkTime'] or (state.get('lastCheck') or {}).get('day') == today:
            return
        active = state.get('active')
        changed = not active or active.get('snapshotHash') != identity(state['snapshot']) or active.get('rules') != state['rules']
        state['lastCheck'] = {'day':today, 'time':stamp(), 'changed':changed,
                              'message':'已检查服务器收到的快照；尚未配置 MES 主动拉取'}
        state['changePending'] = changed
        write_state(config, state)


def prepare(state, request):
    d = validate_snapshot(state['snapshot'])
    r = rules(state['rules'])
    asof = parse_date(d['asOf'])
    first, last = month_bounds(d['month'])
    end = last.timestamp() * 1000
    baseline = state.get('active')
    scenario = request.get('scenario','actual')
    if scenario not in ('actual','expansion'):
        raise ValueError('资源场景无效')
    frozen, frozen_ids, blocks = [], set(), []
    current = {lot['uid']:lot for lot in d['lots']}
    if baseline and r['freezeHours']:
        if not d.get('identityStable') or not baseline.get('identityStable'):
            raise ValueError('跨快照冻结需要稳定 Lot 标识，当前标识待确认')
        cutoff = asof + r['freezeHours'] * HOUR
        frozen_ids = {lot['uid'] for lot in baseline['lots'] if any(asof <= t.get('blockStart',t['start']) < cutoff for t in lot.get('tasks',[]))}
        # Preserve whole shared BAKE batches when one member is frozen.
        while True:
            batches = {t.get('batchId') for lot in baseline['lots'] if lot['uid'] in frozen_ids for t in lot.get('tasks',[]) if t.get('batchId')}
            expanded = frozen_ids | {lot['uid'] for lot in baseline['lots'] if any(t.get('batchId') in batches for t in lot.get('tasks',[]) if t.get('batchId'))}
            if expanded == frozen_ids:
                break
            frozen_ids = expanded
        previous = {lot['uid']:lot for lot in baseline['input']['lots']}
        for old in baseline['lots']:
            if old['uid'] not in frozen_ids:
                continue
            row = current.get(old['uid'])
            if not row or row != previous.get(old['uid']) or d['flows'].get(row['ppn']) != baseline['input']['flows'].get(row['ppn']):
                raise ValueError('冻结 Lot 的输入已变化，请核对后调整冻结范围：' + old['lotId'])
            if any(t['start'] < asof for t in old.get('tasks',[])):
                raise ValueError('冻结路线含过去任务但缺少对应执行事实，请更新 MES 状态：' + old['lotId'])
            frozen.append(copy.deepcopy(old))
        unique = set()
        for lot in frozen:
            lot['frozen'] = True
            for t in lot['tasks']:
                resource = t.get('machine') or t.get('chamber')
                if not resource:
                    continue
                key = (resource,t.get('blockStart',t['start']),t.get('resourceEnd',t['end']))
                if key in unique:
                    continue
                unique.add(key)
                blocks.append({'resource':key[0], 'start':key[1], 'end':key[2], 'card':t.get('card'), 'recipe':t.get('recipe'), 'kind':'frozen', 'setup':t.get('setup',0)})
    eqp = copy.deepcopy(d['equipment'])
    if scenario == 'expansion':
        extras = request.get('extraMachines', {})
        if not isinstance(extras, dict) or not extras:
            raise ValueError('扩产模拟需要填写新增机台数')
        for kind, count in extras.items():
            if type(count) is not int or not 0 <= count <= 20:
                raise ValueError('各类型新增台数须为 0 至 20 的整数')
            template = next((e for e in eqp if e['type'] == kind and 'OVEN' not in kind), None)
            if not template:
                raise ValueError('扩产类型未匹配现有 CP 机台组')
            for index in range(count):
                eqp.append(dict(template, id='SIM-' + kind + '-%02d' % index, proberId='', status='RUN'))
    for b in d['externalBlocks']:
        oven = next((e for e in eqp if e['id']==b['resource'] and 'OVEN' in e['type']), None)
        resources = [oven['id']+'-C%d' % c for c in range(1,d.get('bakeChambers',4)+1)] if oven else [b['resource']]
        blocks.extend(dict(b, resource=resource, start=parse_date(b['start']), end=parse_date(b['end'])) for resource in resources)
    rows, source, wip, held = [], [], [], []
    for lot in d['lots']:
        if lot['state'] == 'DONE' or lot['uid'] in frozen_ids:
            continue
        if lot['state'] == 'HOLD' or (lot['state'] != 'RUNNING' and not lot.get('availableAt')):
            held.append(dict(lot, reason=lot.get('holdReason') or '缺少明确可用时间'))
            continue
        rows.append([lot['lotId'],lot['ppn'],lot['platform'],lot['wafer'],
                     iso_date(max(asof,parse_date(lot.get('availableAt')) or asof)),'W1',last.date().isoformat(),lot['shipDue'],1])
        source.append(lot)
        if lot['state'] == 'RUNNING':
            wip.append(dict(lot['current'],lotId=lot['lotId'],lotKey='LOT-%06d' % len(rows)))
    targets = []
    for demand in d['demands']:
        done = sum(l['wafer'] for l in d['lots'] if l['ppn']==demand['ppn'] and l['state']=='DONE' and first.timestamp()*1000 <= parse_date(l['actualComplete']) < end)
        kept = sum(l['wafer'] for l in frozen if l['ppn']==demand['ppn'] and l.get('complete') is not None and l['complete'] < end)
        types = sorted({t for l in d['lots'] if l['ppn']==demand['ppn'] for t in l['platform'].split(';')})
        targets.append({'ppn':demand['ppn'],'platform':';'.join(types),'mps':max(0,demand['wafer']-done-kept)})
    data = {'meta':{'start':d['asOf'],'monthEnd':last.isoformat(),'days':math.ceil((end-asof)/DAY),'month':d['month']},
            'mps':targets, 'eqp':eqp, 'flows':d['flows'], 'recipes':d['recipes'], 'lots':rows,
            'pm':d['pm'], 'borrow':d['borrow'], 'wip':wip, 'weekly':[], 'constraints':d['constraints'],
            'fixedBlocks':blocks,'productionRules':r,'bakeResources':{'chambers':d.get('bakeChambers',4)},
            'platformType':{}, 'cardType':{}}
    # Only an explicitly identical BAKE batch can share a WIP interval.
    calendars = {}
    for b in blocks:
        calendars.setdefault(b['resource'], []).append((b['start'],b['end']))
    bake_wip = {}
    for w in wip:
        interval = (parse_date(w['start']),parse_date(w['end']))
        if w.get('chamber'):
            key = (w['chamber'],interval)
            members = bake_wip.setdefault(key, [])
            members.append(w['recipe'])
            if len(members)>4 or len(set(members))>1:
                raise ValueError('同腔体在制批次最多 4 Lot，且 Recipe 必须相同')
            if len(members)>1:
                continue
        calendars.setdefault(w.get('chamber') or w['eqp'], []).append(interval)
    for resource, intervals in calendars.items():
        ordered = sorted(intervals)
        if any(a[1] > b[0] for a,b in zip(ordered,ordered[1:])):
            raise ValueError('冻结、外部占用或在制时间冲突：' + resource)
    resources = {e['id']:e for e in eqp}
    for block in blocks:
        if block.get('kind') == 'frozen':
            machine = resources.get(block['resource'].rsplit('-C',1)[0]) if block['resource'] not in resources else resources[block['resource']]
            if not machine or machine['status'] != 'RUN':
                raise ValueError('冻结计划资源不可用：' + block['resource'])
            if any(c['eqp']==machine['id'] and c['recipe']==block.get('recipe') for c in d['constraints']):
                raise ValueError('冻结计划违反新的禁加工条件：' + machine['id'])
    return d, r, data, source, held, frozen, scenario


def project(state, request, prepared, result):
    d,r,data,source,held,frozen,scenario = prepared
    first,last = month_bounds(d['month'])
    start,end,asof = first.timestamp()*1000,last.timestamp()*1000,parse_date(d['asOf'])
    mapped = {('LOT-%06d' % (i+1)):lot for i,lot in enumerate(source)}
    lots = copy.deepcopy(frozen)
    for item in result['lots']:
        row = mapped[item['key']]
        lots.append(dict(row, tasks=item['routeTasks'], complete=item.get('complete'),
                         restEnd=item.get('restEnd'), reason=item.get('unscheduledReason'), frozen=False))
    ids = {l['uid'] for l in lots}
    for row in d['lots']:
        if row['uid'] not in ids:
            completed = parse_date(row['actualComplete']) if row['state']=='DONE' else parse_date(row.get('externalComplete'))
            reason = next((h['reason'] for h in held if h['uid']==row['uid']), None)
            lots.append(dict(row,tasks=[],complete=completed,restEnd=completed+336*HOUR if completed else None,reason=reason))
    baseline = state.get('active')
    old_products = {p['ppn']:p for p in (baseline or {}).get('products',[])}
    products, alerts = [], []
    for demand in d['demands']:
        ppn = demand['ppn']; selected = [l for l in lots if l['ppn']==ppn]
        actual = sum(l['wafer'] for l in selected if l['state']=='DONE' and start <= l['complete'] < end)
        forecast = actual + sum(l['wafer'] for l in selected if l['state']!='DONE' and l.get('complete') is not None and asof <= l['complete'] < end)
        old = old_products.get(ppn)
        old_by_now = sum(l['wafer'] for l in (baseline or {}).get('lots',[]) if l['ppn']==ppn and l.get('complete') is not None and start <= l['complete'] <= asof)
        missing = sum(l['wafer'] for l in selected if l['state']=='RUNNING' and not l.get('externalComplete'))
        p = dict(ppn=ppn,target=demand['wafer'],actual=actual,forecast=forecast,
                 actualPlanDelta=actual-old_by_now if baseline else None,
                 forecastDelta=forecast-old['forecast'] if old else None,
                 targetDelta=forecast-demand['wafer'],externalUnknownWafers=missing)
        products.append(p)
        if demand['wafer']-forecast >= r['shortageAlertWafers']:
            alerts.append({'kind':'产品缺口','ppn':ppn,'message':'预计缺口 %s wafer；原因需结合来料、设备和计划共同核对' % (demand['wafer']-forecast)})
    for lot in lots:
        if lot.get('reason'):
            alerts.append({'kind':'待排条件不足','uid':lot['uid'],'ppn':lot['ppn'],'message':lot['lotId']+'：'+lot['reason']})
        if lot.get('complete') and lot['complete'] - parse_date(lot['shipDue']) > r['delayAlertHours']*HOUR:
            alerts.append({'kind':'交期风险','uid':lot['uid'],'ppn':lot['ppn'],'message':lot['lotId']+'：预计/实际完工晚于内部出货日'})
        if lot['state']=='RUNNING' and not lot.get('externalComplete'):
            alerts.append({'kind':'外部预测待补','uid':lot['uid'],'ppn':lot['ppn'],'message':lot['lotId']+'：仅保留当前占用，最终完工待外部提供'})
    old_lots = {l['uid']:l for l in (baseline or {}).get('lots',[])}
    changes = []
    reliable = bool(d.get('identityStable') and (not baseline or baseline.get('identityStable')))
    if reliable:
        for lot in lots:
            old = old_lots.get(lot['uid'])
            if not old:
                changes.append({'uid':lot['uid'],'lotId':lot['lotId'],'kind':'新增','before':None,'after':lot.get('complete')})
            elif old.get('tasks') != lot.get('tasks') or old.get('complete') != lot.get('complete'):
                changes.append({'uid':lot['uid'],'lotId':lot['lotId'],'kind':'时间或资源变化','before':old.get('complete'),'after':lot.get('complete')})
        changes += [{'uid':l['uid'],'lotId':l['lotId'],'kind':'不在新快照中，待核对','before':l.get('complete'),'after':None} for l in old_lots.values() if l['uid'] not in {x['uid'] for x in lots}]
    return {'id':uuid.uuid4().hex, 'createdAt':stamp(),'contextHash':context_hash(state),
            'baselineId':(baseline or {}).get('id'),
            'snapshotHash':identity(d),'source':d['source'],'identityStable':reliable,
            'month':d['month'],'asOf':d['asOf'],'scenario':scenario,'input':d,'rules':r,
            'lots':lots,'products':products,'alerts':alerts,'changes':changes,
            'borrows':result['borrows'],'pm':result['pmRecords'],'equipment':data['eqp'],
            'events':state['events'][:20], 'optimization':result.get('optimization',{}),
            'frozenLots':len(frozen), 'runMs':result.get('runMs',0)}


def trial(state, request, progress=None):
    prepared = prepare(state, request)
    def report(best, count, accepted):
        if progress:
            progress(project(state,request,prepared,make_json_safe(compact_result(best))) if accepted else None,count)
    result = run(prepared[2], {'algorithm':'search','searchIterations':8}, report)
    return project(state,request,prepared,compact_result(result))


def adopt(config, plan, reason):
    with LOCK:
        state = read_state(config)
        if not str(reason).strip():
            raise ValueError('选用方案需要填写说明')
        if plan['contextHash'] != context_hash(state):
            raise ValueError('输入、规则或当前方案已变化，请重新试排后选用')
        if plan['scenario'] != 'actual':
            raise ValueError('扩产模拟不能选为实际资源计划；可保存为比较记录')
        if state['active'] and not plan['identityStable']:
            raise ValueError('跨快照关联标识待确认，不能自动继承或替换当前计划')
        plan = dict(plan, adoptedAt=stamp(),adoptReason=str(reason)[:1000])
        state['active'] = plan
        state['changePending'] = False
        state['revision'] += 1
        state['history'] = [dict(id=plan['id'],time=plan['adoptedAt'],source=plan['source'],scenario=plan['scenario'],reason=plan['adoptReason'])] + [item for item in state['history'] if item['id'] != plan['id']][:49]
        _write(folder(config) / (plan['id'] + '.json'), plan)
        write_state(config,state)
        return state


def archive(config, plan):
    """Persist a candidate for review without changing the active production plan."""
    with LOCK:
        state = read_state(config)
        _write(folder(config) / (plan['id'] + '.json'), plan)
        if not any(item['id']==plan['id'] for item in state['history']):
            state['history'] = [dict(id=plan['id'],time=stamp(),source=plan['source'],scenario=plan['scenario'],reason='候选比较记录，未选用')] + state['history'][:49]
        write_state(config,state)
        return state


def read_plan(config, plan_id):
    if not re.fullmatch(r'[a-f0-9]{32}', plan_id):
        raise ValueError('方案编号无效')
    with LOCK:
        return json.loads((folder(config) / (plan_id + '.json')).read_text(encoding='utf-8'))
