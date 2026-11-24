"""Receive versioned MES WIP events against a known, complete monthly snapshot."""
from copy import deepcopy

from input_rules import identifier, parse_datetime, resolve_pm_target, wip_timestamp as _timestamp
from production_facts import pm_lifecycle
from production_store import StateConflict, wip_history


def string(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(label + ' 须为非空字符串')
    return value.strip()


def integer(value, label, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f'{label} 须为 {low} 至 {high} 的整数')
    return value


def read_pages(feed):
    if not isinstance(feed, dict) or type(feed.get('schemaVersion')) is not int or feed['schemaVersion'] != 1:
        raise ValueError('WIP 批次 schemaVersion 须为整数 1')
    batch_id = string(feed.get('batchId'), 'batchId')
    if batch_id != feed['batchId']:
        raise ValueError('batchId 首尾不能有空白')
    count = integer(feed.get('pageCount'), 'pageCount', 1, 2000)
    total = integer(feed.get('total'), 'total', 1, 20000)
    pages = feed.get('pages')
    if not isinstance(pages, list) or len(pages) != count:
        raise ValueError('WIP 缺页或页数不符，未接收')
    by_number = {}
    for page in pages:
        if not isinstance(page, dict) or page.get('batchId') != batch_id:
            raise ValueError('分页数据不属于同一读取批次')
        number = integer(page.get('page'), '页码', 1, count)
        if number in by_number or not isinstance(page.get('events'), list):
            raise ValueError('WIP 页码重复或缺少 events')
        by_number[number] = page['events']
    rows = [(p, i, e) for p in sorted(by_number)
            for i, e in enumerate(by_number[p], 1)]
    if len(rows) != total:
        raise ValueError('WIP 实收条数与声明 total 不符')
    for p, i, event in rows:
        if not isinstance(event, dict):
            raise ValueError(f'第 {p} 页第 {i} 行须为事件对象')
    return rows


FIELDS = {
    'REGISTER': {'lotId','ppn','wafer','platform','shipDue','location'},
    'ETA': {'step','at'}, 'AVAILABLE': {'step','at'},
    'HOLD': {'reason'}, 'UNHOLD': {'at'},
    'START': {'current'}, 'EXTEND': {'operationId','expectedEnd','processEnd'},
    'RELEASE': {'operationId','eqp','step','chamber','processEnd','releasedAt','location'},
    'FORECAST': {'at'}, 'COMPLETE': {'at'},
}
COMMON = {'eventId','version','kind','uid','recordedAt'}
REVISABLE = {'ETA','AVAILABLE','FORECAST','EXTEND'}


def blocked_windows(snapshot):
    windows = {}
    equipment = snapshot['equipment']
    chambers = snapshot.get('bakeChambers',4)
    ovens = {eq['id'] for eq in equipment if 'OVEN' in eq['type']}
    for block in snapshot['externalBlocks']:
        key = block['resource']
        targets = [f'{key}-C{i+1}' for i in range(chambers)] if key in ovens else [key]
        for target in targets:
            windows.setdefault(target,[]).append((parse_datetime(block['start']),parse_datetime(block['end'])))
    for pm in pm_lifecycle(snapshot):
        if pm['state']=='RUNNING':
            target = resolve_pm_target({'eqp':equipment,'bakeResources':{'chambers':chambers}},pm)
            for resource in target['resources']:
                windows.setdefault(resource,[]).append((parse_datetime(pm.get('start')),None))
    return windows


def apply_event(lots, machines, snapshot, occupants, released, blocks, event, now, asof):
    flows = snapshot['flows']
    kind = event['kind']
    uid = event['uid']
    lot = lots.get(uid)
    if 'location' in event and not isinstance(event['location'],str):
        raise ValueError('location 须为字符串')
    if kind == 'REGISTER':
        if lot:
            raise ValueError('UID 已存在，不能重复登记物料')
        lot = {key:event[key] for key in FIELDS[kind] if key in event}
        lot.update(uid=uid, state='FUTURE', availableAt=None)
        lot['lotId']=string(lot.get('lotId'),'lotId')
        lot['platform']=identifier(string(lot.get('platform'),'platform'))
        integer(lot.get('wafer'),'wafer',1,25)
        # Subsequent events may reference the new product in this same page.
        lot['ppn'] = identifier(string(lot.get('ppn'),'ppn'))
        _timestamp(lot.get('shipDue'),'shipDue')
        if lot['ppn'] not in flows:
            raise ValueError('新 Lot 缺少已登记路线')
        lots[uid] = lot
        return
    if not lot:
        raise ValueError('未知 UID，请先 REGISTER 或补全 MES 基线')
    state = lot['state']
    def allowed(*states):
        if state not in states:
            raise ValueError(f'{state} 状态不能执行 {kind}')
    def instant(field):
        return _timestamp(event.get(field), field)
    if kind in ('AVAILABLE','ETA'):
        step = identifier(event.get('step'))
        steps = [op[0] for op in flows[lot['ppn']]]
        if step not in steps:
            raise ValueError('工序不属于该产品路线')
        if kind == 'ETA':
            lot.setdefault('wipEta', {})[step] = instant('at').isoformat()
            return
        allowed('FUTURE','READY')
        if step != steps[0] or steps.count(step) != 1:
            raise ValueError('确认物料须能唯一匹配本系统接收的首工序')
        at = instant('at')
        lot.update(availableAt=at.isoformat(), state='READY' if at <= asof else 'FUTURE')
    elif kind == 'HOLD':
        allowed('FUTURE','READY')
        lot.update(state='HOLD', holdReason=string(event.get('reason'),'reason'))
    elif kind == 'UNHOLD':
        allowed('HOLD')
        at = instant('at')
        lot.update(state='READY' if at <= asof else 'FUTURE', availableAt=at.isoformat(), holdReason='')
    elif kind == 'START':
        allowed('FUTURE','READY')
        work = event.get('current')
        if not isinstance(work, dict):
            raise ValueError('START 缺少 current')
        if set(work) - {'operationId','eqp','step','recipe','card','chamber','batchId','start','end','processEnd'}:
            raise ValueError('current 含有未约定的字段')
        work = dict(work)
        work['operationId'] = string(work.get('operationId'),'operationId')
        actual_start = _timestamp(work.get('start'),'current.start')
        if not _timestamp(snapshot['asOf'],'基线时点') <= actual_start <= now:
            raise ValueError('实际开工早于基线或晚于上报时间')
        work['start'] = actual_start.isoformat()
        if 'end' not in work or _timestamp(work['end'],'current.end') <= actual_start:
            raise ValueError('预计设备释放须晚于开工')
        route = flows[lot['ppn']]
        step, recipe, eqp = [identifier(string(work.get(key),key)) for key in ('step','recipe','eqp')]
        if eqp not in machines or not any(op[:2] == [step,recipe] for op in route):
            raise ValueError('上机事实不匹配产品路线或实际机台')
        work.update(step=step,recipe=recipe,eqp=eqp, chamber=identifier(work.get('chamber')),
                    card=identifier(work.get('card')))
        if work.get('processEnd') and not actual_start <= _timestamp(work['processEnd'],'processEnd') <= _timestamp(work['end'],'end'):
            raise ValueError('预计加工结束须在占用时间内')
        machine = machines[eqp]
        chamber = work['chamber']
        if machine['status'] != 'RUN' or any(begin <= now and (end is None or actual_start < end)
                for begin,end in blocks.get(chamber or eqp,[])):
            raise ValueError('上机时设备停用、执行 PM 或有外部占用，请核对设备基线')
        prior_release=released.get(chamber or eqp)
        if prior_release is not None and actual_start < prior_release:
            raise ValueError('实际开工早于前一占用的实际释放')
        members = occupants.setdefault(chamber or eqp,set())
        if 'OVEN' in machine['type']:
            valid_chambers = {f'{eqp}-C{i+1}' for i in range(snapshot.get('bakeChambers',4))}
            if step != 'BAKE' or chamber not in valid_chambers:
                raise ValueError('烤箱上机须提供实际 BAKE 腔体')
            work['batchId'] = string(work.get('batchId'),'烘烤 batchId')
            if len(members) >= 4 or any(not same_bake(work,lots[member]['current']) for member in members):
                raise ValueError('BAKE 腔体占用冲突或同批超过四个 Lot')
        else:
            cards = next((r['cards'] for r in snapshot['recipes'] if r['recipe']==recipe), [])
            platforms = {identifier(t) for t in lot['platform'].split(';')}
            if chamber or not step.startswith('CP') or work['card'] not in cards or machine['type'] not in platforms:
                raise ValueError('CP 上机的工序、平台或针卡不匹配')
            if members:
                raise ValueError('实际机台已有在制，须先收到释放事实')
        members.add(uid)
        lot.update(state='RUNNING', current=work, location=work['chamber'] or eqp)
    elif kind in ('EXTEND','RELEASE'):
        allowed('RUNNING')
        work = lot['current']
        op = string(event.get('operationId'),'operationId')
        if work.get('operationId') != op:
            raise ValueError('operationId 与在机工序实例不符，请核对完整基线')
        if kind == 'EXTEND':
            end = instant('expectedEnd')
            if end <= now:
                raise ValueError('预计释放须晚于更新时刻')
            work['end'] = end.isoformat()
            if 'processEnd' in event:
                work['processEnd'] = instant('processEnd').isoformat()
            if work.get('processEnd') and not parse_datetime(work['start']) <= parse_datetime(work['processEnd']) <= end:
                raise ValueError('预计加工结束须在占用时间内')
            return
        for key in ('eqp','step','chamber'):
            if identifier(event.get(key)) != identifier(work.get(key)):
                raise ValueError('释放对象不匹配 '+key)
        finished = instant('processEnd')
        actual_release = instant('releasedAt')
        if not _timestamp(work['start'],'current.start') <= finished <= actual_release <= now:
            raise ValueError('加工结束时间不在上机到实际释放的区间内')
        handoff = {key:work[key] for key in ('operationId','eqp','step','start','chamber') if key in work}
        handoff.update(eventId=event['eventId'], processEnd=finished.isoformat(), releasedAt=actual_release.isoformat())
        if not work.get('chamber'):
            machines[work['eqp']].update(recipe=work['recipe'],card=work['card'])
        resource=work.get('chamber') or work['eqp']
        occupants[resource].remove(uid)
        released[resource]=max(actual_release,released.get(resource,actual_release))
        del lot['current']
        lot.update(state='EXTERNAL', handoff=handoff, location=event.get('location',''))
    elif kind == 'FORECAST':
        allowed('RUNNING','EXTERNAL')
        if 'at' not in event:
            raise ValueError('FORECAST 缺少 at，撤销预测请显式传 null')
        lot['externalComplete'] = instant('at').isoformat() if event['at'] is not None else None
    elif kind == 'COMPLETE':
        allowed('EXTERNAL')
        at = instant('at')
        if not _timestamp(lot['handoff']['processEnd'],'processEnd') <= at <= now:
            raise ValueError('实际终工时间不在已加工结束至上报时点范围内')
        lot.update(state='DONE', actualComplete=at.isoformat())
        lot.pop('externalComplete', None)


def same_bake(left, right):
    return (left['recipe'] == right['recipe'] and bool(left.get('batchId')) and left.get('batchId') == right.get('batchId')
            and all(_timestamp(left[k],k) == _timestamp(right[k],k) for k in ('start','end')))


def prepare_batch(config, state, feed):
    from production import identity, validate_snapshot
    from production_facts import reconcile
    base = state.get('snapshot')
    if not base or base['source'] != 'mes' or not base.get('identityStable'):
        raise ValueError('请先选用 MES 数据源并接收具有稳定 UID 的完整月度快照')
    rows = read_pages(feed)
    ids = [string(e.get('eventId'),'eventId') for _, _, e in rows]
    previous, history = wip_history(config, feed['batchId'], ids)
    digest = identity(dict(feed,pages=sorted(feed['pages'],key=lambda p:p['page'])))
    if previous:
        receipt, original_hash = previous
        if digest != original_hash:
            raise StateConflict('同一 WIP 批次 ID 对应不同报文')
        return None, dict(receipt, replay=True), []
    if string(feed.get('baseSnapshotId'),'baseSnapshotId') != base['snapshotId']:
        raise StateConflict('WIP 批次引用的基线已变化，请重新对账')
    start = _timestamp(base['asOf'],'基线时点')
    asof = _timestamp(feed.get('asOf'),'asOf')
    if asof < start or asof.strftime('%Y-%m') != base['month']:
        raise ValueError('WIP 时点倒退或跨月，请先接收对应月份的完整快照')
    candidate = deepcopy(base)
    lots = {lot['uid']:lot for lot in candidate['lots']}
    machines = {eq['id']:eq for eq in candidate['equipment']}
    blocks = blocked_windows(candidate)
    occupants, released = {}, {}
    for lot in lots.values():
        if lot['state']=='RUNNING':
            w = lot['current']
            occupants.setdefault(w.get('chamber') or w['eqp'],set()).add(lot['uid'])
    writes = []
    receipts = []
    last = start
    for page, row, original in rows:
        try:
            eid = string(original.get('eventId'),'eventId')
            version = integer(original.get('version'),'version',1,2**31-1)
            uid = identifier(string(original.get('uid'),'uid'))
            kind = identifier(string(original.get('kind'),'kind'))
            if kind not in FIELDS or set(original) - COMMON - FIELDS[kind]:
                raise ValueError('不支持的事件类型或字段 '+kind)
            event = dict(original, uid=uid, kind=kind, eventId=eid)
            event_hash = identity(event)
            versions = history.setdefault(eid,{})
            if version in versions:
                if versions[version][0] != event_hash:
                    raise ValueError('同一事件版本的内容冲突')
                status = 'replay'
            else:
                if versions:
                    latest = max(versions)
                    if version <= latest or versions[latest][1:] != (uid,kind) or kind not in REVISABLE:
                        raise ValueError('事件版本倒退、身份冲突或不允许修订此事实')
                now = _timestamp(event.get('recordedAt'),'recordedAt')
                if not last <= now <= asof:
                    raise ValueError('事件超出批次时间范围或乱序')
                apply_event(lots,machines,candidate,occupants,released,blocks,event,now,asof)
                versions[version] = (event_hash,uid,kind)
                writes.append((eid,version,event_hash,uid,kind))
                status = 'observed' if kind == 'ETA' else 'applied'
                last = now
            receipts.append(dict(page=page,row=row,eventId=eid,version=version,uid=uid,kind=kind,status=status))
        except ValueError as exc:
            raise ValueError(f'WIP 第 {page} 页第 {row} 行：{exc}') from exc
    candidate.update(lots=list(lots.values()),asOf=asof.isoformat(), snapshotId='WIP-'+digest[:32],wipBatchId=feed['batchId'],
                     masterAsOf=base.get('masterAsOf',base['asOf']))
    candidate = validate_snapshot(candidate)
    for members in occupants.values():
        if len(members) <= 1:
            continue
        works = [lots[uid]['current'] for uid in members]
        if not works[0].get('chamber') or len(works)>4 or not all(same_bake(works[0],w) for w in works[1:]):
            raise ValueError('同一资源存在不相容的在制占用')
    issues = reconcile(state.get('factBaseline') or base,candidate)
    if issues:
        raise ValueError('WIP 事实待核对：'+'；'.join(issues[:8]))
    receipt = dict(batchId=feed['batchId'],contentHash=digest,baseSnapshotId=base['snapshotId'],
                   snapshotId=candidate['snapshotId'],asOf=candidate['asOf'],rows=receipts,
                   newEvents=len(writes),replayedEvents=len(rows)-len(writes),replay=False)
    return candidate, receipt, writes
