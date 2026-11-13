"""Convert saved Excel facts into the same snapshot used by MES and demos.

Legacy release/month-end defaults are deliberately not treated as factory facts.
Missing lot facts remain visible; unresolved resource facts prevent a trial.
"""
import copy
from production import identity, month_bounds, validate_snapshot
from input_rules import identifier, parse_datetime


def excel_snapshot(data, options=None):
    if options is not None and not isinstance(options, dict):
        raise ValueError('Excel 补充信息必须为对象')
    options = dict(options or {})
    metadata = data.get('productionMeta') or {}
    for flag in ('identityStable', 'confirmPlannedLots'):
        if flag in options and type(options[flag]) is not bool:
            raise ValueError(flag + ' 必须为布尔值')
    month = data['meta']['month']
    first, _ = month_bounds(month)
    asof = options.get('asOf') or metadata.get('asOf') or first.isoformat()
    parse_datetime(asof)
    facts = data.get('productionFacts', [])
    actual = data['meta'].get('lotSizing') == 'actual'
    issues, notes, lots = [], [], []
    if not options.get('asOf') and not metadata.get('asOf'):
        notes.append('未提供数据时点，暂按月初展示；试排前请在 Excel 接收补充中确认实际时点。')
    if not actual and not options.get('confirmPlannedLots'):
        notes.append('Excel 没有实际 Lot 明细；MPS 拆批仅作为计划批次展示，尚未确认物料。')
    wips = {w.get('lotKey'): w for w in data.get('wip', []) if w.get('lotKey')}
    for index, row in enumerate(data['lots']):
        fact = facts[index] if index < len(facts) else {}
        available = fact.get('availableAt') or options.get('defaultAvailableAt') or None
        due = fact.get('shipDue') or options.get('defaultShipDue') or None
        state = identifier(fact.get('state'))
        if not state:
            state = ('READY' if parse_datetime(available) <= parse_datetime(asof) else 'FUTURE') if available else 'FUTURE'
        if not actual and not options.get('confirmPlannedLots'):
            available, state = None, 'FUTURE'
        missing = []
        if not due: missing.append('内部出货日待补充')
        if state in ('READY', 'FUTURE') and not available: missing.append('明确可用时间待补充')
        lot = dict(uid=fact.get('uid') or 'EXCEL-ROW-%06d' % (index+1), lotId=row[0],
                   ppn=row[1], wafer=int(row[3]), platform=row[2], state=state,
                   availableAt=available, shipDue=due, location=fact.get('location') or '',
                   inputMissing=missing, plannedLot=not actual)
        for key in ('actualComplete','externalComplete','holdReason'):
            if fact.get(key): lot[key]=fact[key]
        wip = wips.get('LOT-%06d' % (index+1))
        if not wip:
            matches=[w for w in data.get('wip',[]) if w.get('lotId')==row[0] and not w.get('lotKey')]
            if matches and sum(r[0]==row[0] for r in data['lots']) != 1:
                issues.append('重复 LotID 的在制占用需要 LotKey：'+row[0])
            elif len(matches)==1: wip=matches[0]
        if wip:
            current={k:wip.get(k) or fact.get('current'+k[0].upper()+k[1:]) for k in ('eqp','chamber','step','recipe','card','start','end')}
            if all(current.get(k) for k in ('eqp','step','recipe','start','end')):
                lot.update(state='RUNNING',current=current)
            else:
                issues.append('在制 '+row[0]+' 需补齐当前工序、Recipe 和占用起止时间')
        elif state=='RUNNING':
            lot['current']={k:fact.get('current'+k[0].upper()+k[1:]) for k in ('eqp','chamber','step','recipe','card','start','end')}
        lots.append(lot)
    equipment=copy.deepcopy(data['eqp'])
    if not any('OVEN' in e['type'] for e in equipment):
        for index in range(int(data.get('bakeResources',{}).get('machines',0))):
            equipment.append(dict(id='CONFIG-OVEN-%02d'%(index+1),type='OVEN',status='RUN'))
        notes.append('烤箱取自已保存的 BAKE 容量配置，请与实际设备核对。')
    borrows=[]
    for index, b in enumerate(data.get('borrow', []), 1):
        if not b.get('recipe') or not b.get('start'):
            issues.append('借机第 %d 条需补齐 Recipe 和明确开始时间'%index)
        else: borrows.append(copy.deepcopy(b))
    if data.get('taskOverrides'):
        issues.append('存在旧版手工 CP 调整；请先核对并解除，统一试排不会静默忽略这些调整。')
    for ppn, route in data['flows'].items():
        if any(not op[0].startswith('CP') and op[0]!='BAKE' for op in route):
            issues.append('产品 '+ppn+' 含旧版等待工序；请按已确认规则更新为 CP/BAKE 路线，336 小时静置单独记录。')
    snapshot=dict(schemaVersion=1,source='excel',month=month,asOf=asof,
        identityStable=bool(options.get('identityStable', metadata.get('identityStable', False)) and actual and all(f.get('uid') for f in facts) and len(facts)==len(lots)),
        demands=[dict(ppn=m['ppn'],wafer=m['mps']) for m in data['mps']], lots=lots,
        equipment=equipment, flows=copy.deepcopy(data['flows']),recipes=copy.deepcopy(data['recipes']),
        pm=copy.deepcopy(data.get('pm',[])),borrow=borrows,constraints=copy.deepcopy(data.get('constraints',[])),
        externalBlocks=[],bakeChambers=data.get('bakeResources',{}).get('chambers',4),
        inputIssues=issues,inputNotes=notes,excelOptions=options,
        sourceName=data['meta'].get('source','Excel'))
    snapshot['snapshotId']='EXCEL-'+identity(snapshot)[:24]
    # Structural failures stay explicit and prevent selecting a misleading plan.
    if not issues:
        try: snapshot=validate_snapshot(snapshot)
        except (ValueError,TypeError,KeyError) as error:
            snapshot['inputIssues'].append(str(error))
    return snapshot
