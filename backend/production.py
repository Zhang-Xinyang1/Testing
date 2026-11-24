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
import production_store as store
from production_facts import complete_import, digest as snapshot_digest, reconcile, pm_lifecycle
from scheduler import run, compact_result, make_json_safe, parse_date, iso_date, HOUR, DAY

LOCK = threading.RLock()
CST = timezone(timedelta(hours=8))
STATES = ('READY', 'FUTURE', 'HOLD', 'RUNNING', 'EXTERNAL', 'DONE')


def stamp():
    return datetime.now(CST).isoformat(timespec='seconds')


def identity(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def folder(config):
    return Path(config).resolve().parent / 'production'


def read_state(config):
    state=store.read(config, lambda: {'revision':0, 'snapshot':None, 'rules':rules(), 'active':None,
            'history':[], 'events':[], 'lastCheck':None, 'changePending':False})
    state['rules']=rules(state['rules'])
    return state


def write_state(config, state):
    return store.save(config,state)


def source_state(config, state, source):
    """Switch an explicit source without erasing another source's selected plan."""
    if source not in ('excel','mes','demo'):
        raise ValueError('数据来源须为 Excel、MES 或演示')
    current=(state.get('snapshot') or {}).get('source') or state.get('selectedSource')
    if current==source:return state
    restored=store.source(config,source) or dict(revision=0,snapshot=None,rules=rules(),active=None,history=[],events=[],
                lastCheck=None,changePending=False,selectedSource=source)
    restored['_storeVersion']=state.get('_storeVersion',-1)
    return restored


def month_bounds(month):
    first = datetime.strptime(month, '%Y-%m').replace(tzinfo=CST)
    last = (first.replace(day=28) + timedelta(days=4)).replace(day=1)
    return first, last


def validate_snapshot(raw):
    if not isinstance(raw, dict):
        raise ValueError('MES 快照必须为对象')
    d = copy.deepcopy(raw)
    complete_import(d)
    d['factIssues']=[]
    for key in ('snapshotId','asOf','month','demands','lots','equipment','flows','recipes'):
        if key not in d:
            raise ValueError('MES 快照缺少字段：' + key)
    if type(d.get('schemaVersion')) is not int or d.get('schemaVersion') != 1 or d.get('source') not in ('mes','demo','excel'):
        raise ValueError('schemaVersion 必须为 1，source 必须为 mes、demo 或 excel')
    if type(d.get('identityStable', False)) is not bool:
        raise ValueError('identityStable 必须为布尔值')
    if not isinstance(d['snapshotId'],str) or not d['snapshotId'].strip():
        raise ValueError('snapshotId 必须为非空字符串')
    if not isinstance(d['month'],str) or not re.fullmatch(r'\d{4}-\d{2}',d['month']):
        raise ValueError('计划月份须为 YYYY-MM')
    try:
        first, last = month_bounds(d['month'])
    except (ValueError,OverflowError) as error:
        raise ValueError('计划月份无效，须为有效的 YYYY-MM') from error
    asof = parse_datetime(d['asOf'])
    if 'masterAsOf' in d and parse_datetime(d['masterAsOf']) > asof:
        raise ValueError('主数据时点不能晚于生产快照')
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
    products, eqps, ids, probers = set(), {}, set(), {}
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
        if row['proberId']:
            if row['proberId'] in probers:
                d['factIssues'].append('Prober %s 同时配对多台 EQP，需核实当前有效装配关系'%row['proberId'])
            probers[row['proberId']]=row['id']
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
        if row.get('uid') is not None and not isinstance(row['uid'],str):
            raise ValueError('Lot uid 必须按字符串传输，不能先转浮点数')
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
        if not isinstance(row.get('lotId'),str) or not row['lotId'].strip() or row['ppn'] not in products or row['state'] not in STATES:
            raise ValueError('LotID、PPN 或状态无效')
        quantity(row.get('wafer'), 'Lot wafer', 1, 25)
        if not row['platform'] or set(row['platform'].split(';')) - types:
            raise ValueError('Lot Platform 未匹配设备 TYPE：' + row['lotId'])
        if row.get('shipDue') or d['source']!='excel':
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
            if not isinstance(current,dict):
                raise ValueError('在制 current 必须为当前工序占用对象')
            if 'operationId' in current and (not isinstance(current['operationId'],str) or not current['operationId'].strip()):
                raise ValueError('在制 operationId 须为非空字符串')
            if current.get('batchId') is not None and (not isinstance(current['batchId'],str) or not current['batchId'].strip()):
                raise ValueError('在制 batchId 须为非空字符串')
            for key in ('eqp','recipe','card','chamber','step'):
                current[key] = identifier(current.get(key))
            if current['eqp'] not in eqps or not date(current.get('start'),'当前工序开始') <= asof or date(current.get('end'),'预计释放')<=date(current.get('start'),'当前工序开始'):
                raise ValueError('在制当前工序须覆盖快照时点，且设备存在')
            if date(current['end'],'预计释放')<=asof:
                d['factIssues'].append('在制 Lot %s 预计释放已过期，实际释放待确认'%row['lotId'])
            if 'OVEN' in eqps[current['eqp']]['type']:
                if not re.fullmatch(re.escape(current['eqp']) + r'-C[1-9]\d*', current['chamber']) or int(current['chamber'].rsplit('-C',1)[1]) > d.get('bakeChambers',4):
                    raise ValueError('BAKE 在制需要有效腔体编号')
                if current['recipe'] not in ('210C24H','250C24H'):
                    raise ValueError('BAKE 在制需要有效烘烤 Recipe')
                if current['step'] != 'BAKE':
                    raise ValueError('烤箱在制工序须为 BAKE')
            elif current['recipe'] not in recipes or current['card'] not in recipes[current['recipe']]:
                d['factIssues'].append('在制 Lot %s 的 Recipe/针卡无法关联，已保留实际占用，需核对'%row['lotId'])
            elif not current['step'].startswith('CP'):
                raise ValueError('CP 在制必须提供当前 CP 工序')
            row['current'] = current
            if not any(op[0]==current['step'] and op[1]==current['recipe'] for op in d['flows'][row['ppn']]):
                d['factIssues'].append('在制 Lot %s 当前工序/Recipe 不属于已提供路线，已保留占用，需补齐路线版本'%row['lotId'])
            if 'OVEN' not in eqps[current['eqp']]['type'] and eqps[current['eqp']]['type'] not in row['platform'].split(';'):
                d['factIssues'].append('在制 Lot %s 的当前机台不符合 Platform，需核对实际装配'%row['lotId'])
            if current.get('processEnd') and not (date(current['start'],'工序开始') <=
                    date(current['processEnd'],'预计加工结束') <= date(current['end'],'预计释放')):
                raise ValueError('预计加工结束须在资源占用区间内')
        if row['state'] == 'EXTERNAL' or row.get('handoff'):
            handoff = row.get('handoff')
            if row['state'] not in ('EXTERNAL','DONE') or not isinstance(handoff, dict) or row.get('current'):
                raise ValueError('外部接续需要独立移交记录，不能同时占用机台')
            for field in ('eqp','step','eventId','operationId'):
                if not isinstance(handoff.get(field), str) or not handoff[field].strip():
                    raise ValueError('移交缺少 '+field)
            if not (date(handoff.get('start'),'上机') <= date(handoff.get('processEnd'),'加工结束')
                    <= date(handoff.get('releasedAt'),'设备释放') <= asof):
                raise ValueError('移交的开始、加工结束、释放时序无效')
            if row['state']=='DONE' and completed < date(handoff['processEnd'],'加工结束'):
                raise ValueError('最终完工不能早于已移交工序的加工结束')
        if row.get('externalComplete'):
            if row['state'] not in ('RUNNING','EXTERNAL'):
                raise ValueError('外部完工预测仅适用于在制或已移交批次')
            work = row.get('current') or row.get('handoff')
            if date(row['externalComplete'],'外部完工预测') < date(work.get('processEnd') or work['end'],'加工结束'):
                raise ValueError('外部完工预测不得早于当前工序结束')
            if date(row['externalComplete'],'外部完工预测') < asof:
                row['expiredExternalEstimate']=row.pop('externalComplete')
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
    all_pm_ids=set()
    for p in d['pm']:
        p['eqp']=identifier(p.get('eqp'));p['targetKind']=identifier(p.get('targetKind'))
        # PM windows are local calendar dates, whereas start/end are instants.
        for key in ('early','due','over'):
            if p.get(key):p[key]=date(p[key],'PM '+key).astimezone(CST).date().isoformat()
        status=identifier(p.get('state') or 'PLANNED')
        p['state']={'COMPLETED':'DONE','IN_PROGRESS':'RUNNING','CANCELED':'CANCELLED'}.get(status,status)
        if p.get('id') is not None:
            if not isinstance(p['id'],str) or not p['id'].strip():raise ValueError('PM id 必须为非空字符串')
            p['id']=identifier(p['id'])
            if p['id'] in all_pm_ids:raise ValueError('PM id 重复，需先按任务版本去重')
            all_pm_ids.add(p['id'])
        if p.get('version') is not None and (type(p['version']) is not int or p['version']<0):
            raise ValueError('PM version 必须为非负整数')
    active_pm=pm_lifecycle(d)
    pm_ids=set()
    for p in active_pm:
        if not p.get('id') and (d['source']=='mes' or p['state']=='RUNNING'):
            d['factIssues'].append('MES/执行中 PM 缺少稳定字符串 id，无法跨快照核对维护与释放')
        if p.get('id') is not None and (not isinstance(p['id'],str) or not p['id'].strip()):
            raise ValueError('PM id 必须为非空字符串')
        if p.get('id') in pm_ids:
            raise ValueError('PM id 重复，需按任务版本去重')
        if p.get('id'):pm_ids.add(p['id'])
        p['eqp'] = identifier(p.get('eqp'))
        p['targetKind'] = identifier(p.get('targetKind'))
        early,due,over = (date(p.get(k),'PM '+k) for k in ('early','due','over'))
        hours = p.get('hours')
        if not early.date() <= due.date() <= over.date() or isinstance(hours,bool) or not isinstance(hours,(int,float)) or not math.isfinite(hours) or not 0 < hours <= 744:
            raise ValueError('PM 窗口或时长无效')
        if p.get('start') and p['state']=='PLANNED':
            begin = date(p['start'],'PM 开始')
            if begin < early or begin + timedelta(hours=hours) > over.replace(hour=0,minute=0,second=0,microsecond=0)+timedelta(days=1):
                raise ValueError('PM 指定时间超出维护窗口')
        if p['state']=='RUNNING':
            if not p.get('start') or not p.get('expectedEnd'):
                d['factIssues'].append('执行中 PM 需要实际 start 和 expectedEnd；不以预计总工时猜测释放')
            elif date(p['start'],'PM 实际开始')>asof or date(p['expectedEnd'],'PM 预计释放')<=asof:
                d['factIssues'].append('执行中 PM 的时间不覆盖当前时点，释放待确认')
            if p.get('start') and p.get('expectedEnd') and date(p['expectedEnd'],'PM 预计释放')<=date(p['start'],'PM 开始'):
                raise ValueError('PM 预计释放必须晚于实际开始')
    pm_events({'eqp':d['equipment'], 'pm':[p for p in active_pm if p['state']=='PLANNED'], 'bakeResources':{'chambers':d.get('bakeChambers',4)}})
    # Preserve original lifecycle records. Active events are derived in prepare.
    for row in d['constraints']:
        row['eqp'], row['recipe'] = identifier(row.get('eqp')), identifier(row.get('recipe'))
        if row['eqp'] not in eqps or row['recipe'] not in recipes:
            raise ValueError('禁加工引用未知机台或 Recipe')
    return d


def context_hash(state):
    return identity({'snapshot':state['snapshot'], 'rules':state['rules'],
                     'active':(state.get('active') or {}).get('id'),
                     'factIssues':state.get('factIssues',[]),
                     'receiptFailure':state.get('receiptFailure')})


def record_change(state, kind, reason):
    state['revision'] += 1
    state['events'] = [{'time':stamp(), 'kind':kind, 'reason':reason}] + state.get('events', [])[:99]
    state['changePending'] = True


def review_facts(state, old, snapshot):
    baseline=state.get('factBaseline') or old
    issues=reconcile(baseline,snapshot)
    state['factIssues']=issues
    if not issues or not baseline:
        state['factBaseline']=copy.deepcopy(snapshot)
    else:
        # A rejected reconciliation must not erase obligations seen in an
        # earlier or partially inconsistent snapshot, even after many retries.
        baseline=copy.deepcopy(baseline);known={l['uid'] for l in baseline['lots']}
        baseline['lots'].extend(copy.deepcopy(l) for l in snapshot['lots']
                                if l['state'] in ('RUNNING','EXTERNAL','DONE') and l['uid'] not in known)
        known_pm={p.get('id') for p in baseline.get('pm',[])}
        baseline.setdefault('pm',[]).extend(copy.deepcopy(p) for p in snapshot.get('pm',[])
                                           if p.get('id') and p['id'] not in known_pm)
        state['factBaseline']=baseline


def preview_wip_receipt(state, feed):
    """Bind the reviewed feed to the exact demo state, including the selected plan."""
    from production_wip_preview import preview_wip
    snapshot = state.get('snapshot')
    if not snapshot or snapshot.get('source') != 'demo':
        raise ValueError('请在演示数据中预演 WIP 来料')
    candidate, report = preview_wip(snapshot, feed)
    token = None
    if report['accepted']:
        token = identity([state.get('_storeVersion'), context_hash(state), feed])
    return candidate, {'contextHash': token, 'report': report}


def receive_wip_batch(config, feed, publish=False):
    from wip_intake import prepare_batch
    with LOCK:
        state = read_state(config)
        try:
            candidate, receipt, events = prepare_batch(config,state,feed)
        except ValueError as error:
            # A failed attempted receipt on the current MES baseline signals missing facts.
            # Stale writers, read-only previews and other sources do not alter the workspace.
            base = state.get('snapshot') or {}
            if (publish and isinstance(feed,dict) and not isinstance(error,store.StateConflict) and base.get('source')=='mes'
                    and feed.get('baseSnapshotId')==base['snapshotId']):
                pending=state.get('receiptFailure') or {}
                if not pending or (pending.get('source')=='wip' and pending.get('batchId') in (None,feed.get('batchId'))):
                    state['receiptFailure'] = {'time':stamp(),'message':str(error),'source':'wip',
                                               'batchId':feed.get('batchId')}
                state['changePending'] = True
                write_state(config,state)
            raise
        if publish and candidate is not None:
            failure = state.get('receiptFailure')
            if failure and failure.get('source')!='wip':
                raise ValueError('上一次完整快照接收失败，请先重新接收完整快照')
            if failure and failure.get('batchId') and failure['batchId']!=feed['batchId']:
                raise ValueError('请先重发上一次失败的 WIP 批次或接收完整快照')
            state.pop('receiptFailure',None)
            state['snapshot']=candidate
            state['lastWipReceipt']={k:v for k,v in receipt.items() if k!='rows'}
            state['factBaseline']=copy.deepcopy(candidate)
            state['factIssues']=[]
            record_change(state,'MES WIP 接收', f"{receipt['batchId']}：{receipt['newEvents']} 条新事件，{receipt['replayedEvents']} 条重复")
            store.save(config,state,snapshot_identity=('mes',candidate['snapshotId'],snapshot_digest(candidate)),
                       wip=(feed,receipt,events,receipt['contentHash']))
        return dict(receipt,published=publish or receipt['replay'])


def update(config, action, body):
    with LOCK:
        state = read_state(config)
        starting_source=(state.get('snapshot') or {}).get('source') or state.get('selectedSource')
        receipt_identity=None
        if action == 'source':
            state=source_state(config,state,body.get('source'))
        elif action == 'excel':
            state=source_state(config,state,'excel')
            if state.get('snapshot')==body:
                cleared=state.pop('receiptFailure',None)
                if cleared or starting_source!='excel':write_state(config,state)
                return state
            old=state.get('snapshot')
            state['snapshot']=copy.deepcopy(body)
            review_facts(state,old,body)
            state.pop('receiptFailure',None)
            receipt_identity=('excel',body['snapshotId'],snapshot_digest(body))
            if old and old['month']!=body['month']:state['active']=None
            record_change(state,'Excel 接收','已保存的 Excel 数据已接入统一排产；请检查待补充项后试排')
        elif action == 'demo':
            from production_demo import scenario_pack
            if state['snapshot'] and state['snapshot']['source'] != 'demo':
                raise ValueError('当前已接入 MES 快照，演示入口不会覆盖现场数据')
            pack=scenario_pack(body.get('case','basic'))
            state['snapshot'] = validate_snapshot(pack['snapshot'])
            state['rules'] = pack['rules']
            state['active'] = None
            state['lastCheck'] = None
            state['factIssues']=[]
            state['factBaseline']=copy.deepcopy(state['snapshot'])
            state.pop('receiptFailure',None)
            record_change(state, '模拟数据', '载入 '+state['snapshot']['demoCase']['title']+'；应用演示规则，等待人工试排')
        elif action == 'demo-event':
            from production_demo import demo_event
            if not state.get('snapshot'):
                raise ValueError('请先载入演示数据')
            snapshot,reason=demo_event(state['snapshot'],body.get('event'))
            state['snapshot']=validate_snapshot(snapshot)
            record_change(state,'一键模拟变化',reason)
        elif action == 'wip-apply':
            feed = body.get('feed')
            # A client may have lost the response after a successful commit.
            snapshot = state.get('snapshot') or {}
            previous = snapshot.get('wipPilot') or {}
            if (snapshot.get('source') == 'demo' and previous.get('token')
                    and previous['token'] == body.get('contextHash')
                    and previous.get('feedHash') == identity(feed)):
                return state
            candidate, receipt = preview_wip_receipt(state, feed)
            report = receipt['report']
            if not report['accepted']:
                raise ValueError('WIP 来料未通过核对：' + '；'.join(report['errors'][:5]))
            if receipt['contextHash'] != body.get('contextHash'):
                raise store.StateConflict('来料记录或演示基线已变化，请重新预演')
            if not report['applied']:
                raise ValueError('这份 WIP 来料没有可应用的记录')
            old = state['snapshot']
            candidate['snapshotId'] = 'DEMO-WIP-' + receipt['contextHash'][:24]
            candidate['wipPilot'] = {
                'baseSnapshotId': old['snapshotId'], 'asOf': feed['asOf'],
                'token': receipt['contextHash'], 'feedHash': identity(feed),
                'records': copy.deepcopy(feed['records']), 'report': report,
            }
            state['snapshot'] = validate_snapshot(candidate)
            review_facts(state, old, state['snapshot'])
            record_change(state, 'WIP 来料预演', '确认 %d 条可用时间，%d 条仅观察。' %
                          (report['applied'], report['observed']))
        elif action == 'snapshot':
            try:
                snapshot = validate_snapshot(body)
            except (ValueError,TypeError,KeyError) as error:
                state['receiptFailure']={'time':stamp(),'message':str(error)}
                state['changePending']=True
                write_state(config,state)
                raise
            state=source_state(config,state,snapshot['source'])
            old = state.get('snapshot')
            if old and snapshot_digest(old)==snapshot_digest(snapshot):
                cleared=state.pop('receiptFailure',None)
                if cleared or starting_source!=snapshot['source']:write_state(config,state)
                return state
            if old and old['snapshotId']==snapshot['snapshotId'] and snapshot['source']!='demo':
                raise ValueError('同一快照 ID 对应不同业务内容，请提供新的 snapshotId')
            if old and snapshot['source'] == old['source'] and parse_date(snapshot['asOf']) < parse_date(old['asOf']):
                raise ValueError('拒绝早于当前时点的过期快照')
            state['snapshot'] = snapshot
            review_facts(state,old,snapshot)
            state.pop('receiptFailure',None)
            if snapshot['source']!='demo':receipt_identity=(snapshot['source'],snapshot['snapshotId'],snapshot_digest(snapshot))
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
                if lot['state'] not in ('READY','FUTURE'):
                    raise ValueError('来料或交期变化仅适用于待排或未来来料，不能改写已完工、在制或 HOLD 状态')
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
        store.save(config,state,snapshot_identity=receipt_identity)
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
    if not state.get('snapshot'):
        raise ValueError('请先接收 Excel、MES 或演示数据')
    if state['snapshot'].get('inputIssues'):
        raise ValueError('Excel 待补充：'+'；'.join(state['snapshot']['inputIssues'][:8]))
    if state.get('receiptFailure'):
        raise ValueError('最近一次数据接收失败，请重新接收完整数据后试排：'+state['receiptFailure']['message'])
    if state.get('factIssues'):
        raise ValueError('生产事实待核对：'+'；'.join(state['factIssues'][:8]))
    d = validate_snapshot(state['snapshot'])
    if d.get('factIssues'):
        raise ValueError('生产事实待核对：'+'；'.join(d['factIssues'][:8]))
    r = rules(state['rules'])
    from production_time import execution_context
    execution=execution_context(d,r,request)
    asof = parse_date(d['asOf'])
    planning_start=execution['start']
    first, last = month_bounds(d['month'])
    end = last.timestamp() * 1000
    if planning_start>=end:
        raise ValueError('计划可执行起点已超出当前月份，请接收下一月需求和生产快照')
    baseline = state.get('active')
    if execution['mode']=='live' and baseline and baseline.get('execution',{}).get('mode')!='live':
        baseline=None
    scenario = request.get('scenario','actual')
    if scenario not in ('actual','expansion'):
        raise ValueError('资源场景无效')
    frozen, frozen_ids, blocks = [], set(), []
    current = {lot['uid']:lot for lot in d['lots']}
    if baseline and r['freezeHours']:
        if not d.get('identityStable') or not baseline.get('identityStable'):
            raise ValueError('跨快照冻结需要稳定 Lot 标识，当前标识待确认')
        cutoff = planning_start + r['freezeHours'] * HOUR
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
    from input_rules import resolve_pm_target
    pending_pm=[]
    for pm in pm_lifecycle(d):
        if pm['state']=='RUNNING':
            target=resolve_pm_target({'eqp':eqp,'bakeResources':{'chambers':d.get('bakeChambers',4)}},pm)
            blocks.extend(dict(resource=resource,start=parse_date(pm['start']),end=parse_date(pm['expectedEnd']),
                               reason='执行中 PM',kind='running-pm') for resource in target['resources'])
        else:pending_pm.append(pm)
    rows, source, wip, held = [], [], [], []
    for lot in d['lots']:
        if lot['state'] in ('DONE','EXTERNAL') or lot['uid'] in frozen_ids:
            continue
        if lot['state'] == 'HOLD' or (lot['state'] != 'RUNNING' and (not lot.get('availableAt') or not lot.get('shipDue'))):
            held.append(dict(lot, reason=lot.get('holdReason') or ('缺少内部出货日' if not lot.get('shipDue') else '缺少明确可用时间')))
            continue
        rows.append([lot['lotId'],lot['ppn'],lot['platform'],lot['wafer'],
                     iso_date(max(planning_start,parse_date(lot.get('availableAt')) or asof)),'W1',last.date().isoformat(),lot.get('shipDue') or last.isoformat(),1])
        source.append(lot)
        if lot['state'] == 'RUNNING':
            wip.append(dict(lot['current'],lotId=lot['lotId'],lotKey='LOT-%06d' % len(rows)))
    targets = []
    for demand in d['demands']:
        done = sum(l['wafer'] for l in d['lots'] if l['ppn']==demand['ppn'] and l['state']=='DONE' and first.timestamp()*1000 <= parse_date(l['actualComplete']) < end)
        kept = sum(l['wafer'] for l in frozen if l['ppn']==demand['ppn'] and l.get('complete') is not None and l['complete'] < end)
        external=sum(l['wafer'] for l in d['lots'] if l['ppn']==demand['ppn'] and l['state'] in ('RUNNING','EXTERNAL') and l.get('externalComplete') and asof<=parse_date(l['externalComplete'])<end)
        types = sorted({t for l in d['lots'] if l['ppn']==demand['ppn'] for t in l['platform'].split(';')})
        targets.append({'ppn':demand['ppn'],'platform':';'.join(types),'mps':max(0,demand['wafer']-done-kept-external)})
    data = {'meta':{'start':iso_date(planning_start),'monthEnd':last.isoformat(),'days':math.ceil((end-planning_start)/DAY),'month':d['month']},
            'mps':targets, 'eqp':eqp, 'flows':d['flows'], 'recipes':d['recipes'], 'lots':rows,
            'pm':pending_pm, 'borrow':d['borrow'], 'wip':wip, 'weekly':[], 'constraints':d['constraints'],
            'fixedBlocks':blocks,'productionRules':r,'bakeResources':{'chambers':d.get('bakeChambers',4)},
            'platformType':{}, 'cardType':{},'execution':execution,
            'productionSnapshot':d,'productionLotUids':[l['uid'] for l in source],
            'productionFrozen':frozen}
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
            members.append((w['recipe'],w.get('batchId')))
            if len(members)>4 or len(set(members))>1:
                raise ValueError('同腔体在制批次最多 4 Lot，且 Recipe 与批次标识必须相同')
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
    candidate_context=context_hash(state)
    lots = copy.deepcopy(frozen)
    for item in result['lots']:
        row = mapped[item['key']]
        tasks=copy.deepcopy(item['routeTasks'])
        for task in tasks:
            if task.get('batchId'):task['batchId']='R-'+candidate_context[:12]+'-'+task['batchId']
        lots.append(dict(row, tasks=tasks, complete=item.get('complete'),
                         restEnd=item.get('restEnd'), reason=item.get('unscheduledReason'), frozen=False))
    ids = {l['uid'] for l in lots}
    for row in d['lots']:
        if row['uid'] not in ids:
            completed = parse_date(row['actualComplete']) if row['state']=='DONE' else parse_date(row.get('externalComplete'))
            reason = next((h['reason'] for h in held if h['uid']==row['uid']), None)
            lots.append(dict(row,tasks=[],complete=completed,restEnd=completed+336*HOUR if completed else None,reason=reason))
    baseline = state.get('active')
    old_products = {p['ppn']:p for p in (baseline or {}).get('products',[])}
    from production_ledger import output_ledger
    ledger=output_ledger(d,lots)
    totals={p['ppn']:p for p in ledger['products']}
    by_uid={entry['uid']:entry for entry in ledger['rows']}
    for lot in lots:
        lot['complete']=by_uid[lot['uid']]['complete']
        lot['restEnd']=lot['complete']+336*HOUR if lot['complete'] is not None else None
    products, alerts = [], []
    for demand in d['demands']:
        ppn = demand['ppn']; selected = [l for l in lots if l['ppn']==ppn]
        actual = totals[ppn]['actual']
        forecast = totals[ppn]['forecast']
        old = old_products.get(ppn)
        old_by_now = sum(l['wafer'] for l in (baseline or {}).get('lots',[]) if l['ppn']==ppn and l.get('complete') is not None and start <= l['complete'] <= asof)
        missing = sum(l['wafer'] for l in selected if l['state'] in ('RUNNING','EXTERNAL') and not l.get('externalComplete'))
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
        if lot.get('complete') and lot.get('shipDue') and lot['complete'] - parse_date(lot['shipDue']) > r['delayAlertHours']*HOUR:
            alerts.append({'kind':'交期风险','uid':lot['uid'],'ppn':lot['ppn'],'message':lot['lotId']+'：预计/实际完工晚于内部出货日'})
        if lot['state'] in ('RUNNING','EXTERNAL') and not lot.get('externalComplete'):
            status = '设备已释放' if lot['state']=='EXTERNAL' else '保留当前在制占用'
            alerts.append({'kind':'外部预测待补','uid':lot['uid'],'ppn':lot['ppn'],'message':f"{lot['lotId']}：{status}，最终完工待外部提供"})
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
    execution=copy.deepcopy(data['execution'])
    if execution['mode']=='live':
        boundaries=[t.get('blockStart',t['start']) for lot in lots for t in lot['tasks']]
        boundaries.extend(pm['start'] for pm in result['pmRecords'])
        execution['validUntil']=min([execution['validUntil']]+boundaries)
    plan={'id':uuid.uuid4().hex, 'createdAt':stamp(),'contextHash':candidate_context,
            'baselineId':(baseline or {}).get('id'),
            'snapshotHash':identity(d),'source':d['source'],'identityStable':reliable,
            'month':d['month'],'asOf':d['asOf'],'scenario':scenario,'input':d,'rules':r,
            'lots':lots,'products':products,'alerts':alerts,'changes':changes,
            'borrows':result['borrows'],'pm':result['pmRecords'],'equipment':data['eqp'],
            'events':state['events'][:20], 'optimization':result.get('optimization',{}),
            'frozenLots':len(frozen), 'runMs':result.get('runMs',0),
            'execution':execution,'outputLedger':ledger['rows']}
    from production_validation import validate_plan
    plan['validation']=validate_plan(d,plan,r)
    return plan


def trial(state, request, progress=None):
    prepared = prepare(state, request)
    def report(best, count, accepted):
        if progress:
            progress(project(state,request,prepared,make_json_safe(compact_result(best))) if accepted else None,count)
    def verify(candidate):
        project(state,request,prepared,make_json_safe(compact_result(candidate)))
    result = run(prepared[2], {'algorithm':'search','searchIterations':32}, report,verify)
    return project(state,request,prepared,compact_result(result))


def adopt(config, plan, reason):
    with LOCK:
        state = read_state(config)
        if not str(reason).strip():
            raise ValueError('选用方案需要填写说明')
        if state.get('active') and state['active']['id']==plan['id']:
            return state
        if plan['contextHash'] != context_hash(state):
            raise ValueError('输入、规则或当前方案已变化，请重新试排后选用')
        if plan['scenario'] != 'actual':
            raise ValueError('扩产模拟不能选为实际资源计划；可保存为比较记录')
        if state['active'] and not plan['identityStable']:
            raise ValueError('跨快照关联标识待确认，不能自动继承或替换当前计划')
        if state.get('factIssues') or state.get('receiptFailure'):
            raise ValueError('生产事实尚未确认，不能选用')
        if state.get('active') and state['active'].get('execution',{}).get('mode')=='live' and plan['execution']['mode']=='shadow':
            raise ValueError('影子方案不能替换现场已选计划；请保存为比较记录')
        from production_validation import validate_plan
        from production_time import check_adoption
        validate_plan(validate_snapshot(state['snapshot']),plan,rules(state['rules']))
        check_adoption(plan,state['snapshot'],rules(state['rules']))
        plan = dict(plan, adoptedAt=stamp(),adoptReason=str(reason)[:1000])
        state['active'] = plan
        state['changePending'] = False
        state['revision'] += 1
        state['history'] = [dict(id=plan['id'],time=plan['adoptedAt'],source=plan['source'],scenario=plan['scenario'],reason=plan['adoptReason'])] + [item for item in state['history'] if item['id'] != plan['id']][:49]
        store.save(config,state,plan=plan,adopted=True)
        return state


def archive(config, plan):
    """Persist a candidate for review without changing the active production plan."""
    with LOCK:
        state = read_state(config)
        if not any(item['id']==plan['id'] for item in state['history']):
            state['history'] = [dict(id=plan['id'],time=stamp(),source=plan['source'],scenario=plan['scenario'],reason='候选比较记录，未选用')] + state['history'][:49]
        store.save(config,state,plan=plan)
        return state


def read_plan(config, plan_id):
    if not re.fullmatch(r'[a-f0-9]{32}', plan_id):
        raise ValueError('方案编号无效')
    with LOCK:
        return store.read_plan(config,plan_id)
