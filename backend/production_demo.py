"""Reproducible synthetic production scenarios; never sourced from factory data."""
import copy
from datetime import datetime, timedelta, timezone


def demo_snapshot(now=None):
    now = now or datetime.now(timezone(timedelta(hours=8)))
    # Use the current calendar month, but a reproducible review time in it.
    anchor = now.replace(day=min(now.day, 20), hour=8, minute=0, second=0, microsecond=0)
    iso = lambda hours: (anchor + timedelta(hours=hours)).isoformat()
    products = ['DEMO01', 'DEMO02', 'DEMO03']
    lots = []
    for i in range(12):
        ppn = products[i % 3]
        lots.append({'uid': 'DEMO-LOT-%03d' % i, 'lotId': ppn + '-L%03d' % i,
                     'ppn': ppn, 'wafer': 25, 'platform': 'CP-A;CP-B',
                     'state': 'READY', 'availableAt': iso(0), 'shipDue': iso(36 + i * 8),
                     'location': '测试待排区', 'source': '仓库'})
    lots[0].update(state='DONE', actualComplete=iso(-2), location='成品区')
    lots[1].update(state='RUNNING', location='CP-A-01', current={
        'eqp':'CP-A-01', 'step':'CP1', 'recipe':'R1', 'card':'CARD1',
        'start':iso(-1), 'end':iso(3)})
    lots[2].update(state='FUTURE', availableAt=iso(30), location='Fab', source='Fab')
    lots[3].update(state='FUTURE', availableAt=None, location='Fab', source='Fab')
    lots[4].update(state='HOLD', holdReason='模拟品质待确认', location='HOLD 区')
    return {'schemaVersion':1, 'snapshotId':'DEMO-' + anchor.strftime('%Y%m%d'),
            'source':'demo', 'identityStable':True, 'asOf':iso(0), 'month':anchor.strftime('%Y-%m'),
            'demands':[{'ppn':p, 'wafer':125} for p in products], 'lots':lots,
            'equipment':[{'id':t + '-%02d' % i, 'type':t, 'status':'RUN', 'recipe':'R1',
                          'card':'CARD1', 'proberId':'PROBE-' + t + '-%02d' % i}
                         for t in ('CP-A', 'CP-B') for i in (1,2)] +
                        [{'id':'OVEN-01', 'type':'OVEN', 'status':'RUN'}],
            'bakeChambers':2,
            'flows':{p:[['CP1','R1',2 + j], ['BAKE','210C24H',24], ['CP2','R2',3 + j]]
                     for j,p in enumerate(products)},
            'recipes':[{'recipe':'R1','cards':['CARD1']}, {'recipe':'R2','cards':['CARD2']}],
            'pm':[{'eqp':'CP-B-02','targetKind':'EQP','early':iso(0)[:10],
                   'due':iso(0)[:10], 'over':iso(24)[:10], 'hours':4, 'start':iso(4)}],
            'borrow':[{'eqp':'CP-B-01','recipe':'R1','date':iso(0)[:10], 'start':iso(0), 'hours':2}],
            'constraints':[], 'externalBlocks':[]}


SCENARIOS = [
    ('full', '综合生产 · 72 Lot', 72, '六种产品、三类 CP、两台烤箱，覆盖库存、Fab 来料、在制、HOLD、实际完工与重复批次号。',
     ['未知到货与 HOLD 不排产', '在制仅保留当前占用', '25/20/15 片尾批、禁加工与停机均生效', '可一键注入来料/交期/停机变化']),
    ('basic', '快速入门 · 12 Lot', 12, '小数据快速走完试排、比较、选用与查询流程。',
     ['首次打开不计算', '三个产品都有缺口', '完成后人工选用，刷新可恢复']),
    ('recipe', '借机与 35 分钟切换', 3, '初始 R1，借机前加工切换为 R2；借机要求 R2。',
     ['以借机开始时 R2 校验，而非初始 R1', '同时换 Recipe 和针卡占用 35 分钟', '修改换 Recipe 参数后必须重新试排']),
    ('paired_pm', 'EQP / Prober 组合维护', 12, '同一组合有两条重叠 PM 和两条分时 PM。',
     ['4 条原始 PM 对应 3 段组合停机', '重叠区间两项维护都保留', '分时 PM 分别整组停机']),
    ('month_end', '月末边界与静置', 4, '月末 22 点起排，覆盖月内完成、次月零点完成及次月来料。',
     ['当月实际 25、预测 50 wafer', '恰好次月零点完成不算当月', '336 小时静置不延迟 MPS 统计']),
    ('capacity', '产能缺口与扩产 · 120 Lot', 120, '距月底两天、两台实际 CP，供给充足但产能不足。',
     ['实际资源显示缺口和延期', '切换扩产场景已预填新增 2 台', '扩产可保存比较，不能选成实际计划']),
    ('freeze', '冻结与滚动比较', 12, '演示冻结窗口预置 1 小时；先选用一版，再试排比较。',
     ['第二次试排保留整 Lot 及同批 BAKE', '已开工当前占用一直保留', '输入变化后的旧候选不能选用']),
    ('identity', '重复 LotID / 标识待确认', 12, '故意重复显示 LotID，并声明跨快照稳定标识未确认。',
     ['重复显示号仍为两条独立批次', '逐 Lot 跨快照对比受限', '首次可选用，后续替换会明确阻止']),
    ('scale500', '规模验证 · 500 Lot', 500, '六产品、多机台、烤箱与库存状态；计算上限预置 60 秒。',
     ['验证分页与异步进度', '60 秒内结束或停止并说明结果', '不作为真实产线性能承诺']),
    ('timeout2000', '超时与取消 · 2000 Lot', 2000, '故意将计算上限设为 2 秒，便于快速验证停止保护。',
     ['到时停止，已有可行候选才会保留', '也可主动取消', '停止计算不自动替换已选方案']),
]


def demo_catalog():
    return {'scenarios':[dict(key=k, title=t, lots=n, description=d, checks=c)
                         for k,t,n,d,c in SCENARIOS],
            'events':[
                {'key':'arrival_known','title':'未知来料 → 明确 24 小时后到货'},
                {'key':'arrival_delay','title':'已知来料 → 延迟 48 小时'},
                {'key':'due_urgent','title':'一个待排 Lot → 2 小时后交期'},
                {'key':'machine_down','title':'空闲 CP → 停机 12 小时'},
                {'key':'external_forecast','title':'在制 Lot → 补充外部完工预测'},
                {'key':'release_hold','title':'HOLD → 放行并立即可用'},
            ]}


def paired_pm(snapshot, machine_id):
    anchor = datetime.fromisoformat(snapshot['asOf'])
    machine = next(e for e in snapshot['equipment'] if e['id']==machine_id)
    def row(target, kind, hour, duration):
        begin = anchor + timedelta(hours=hour)
        return dict(eqp=target, targetKind=kind, early=anchor.date().isoformat(),
                    due=begin.date().isoformat(), over=(anchor+timedelta(days=2)).date().isoformat(),
                    start=begin.isoformat(), hours=duration)
    return [row(machine_id,'EQP',4,4), row(machine['proberId'],'PROBER',6,4),
            row(machine_id,'EQP',12,2), row(machine['proberId'],'PROBER',18,2)]


def _comprehensive(now):
    d = demo_snapshot(now)
    anchor = datetime.fromisoformat(d['asOf'])
    iso = lambda hours:(anchor+timedelta(hours=hours)).isoformat()
    products = ['DEMO%02d'%i for i in range(1,7)]
    d['equipment'] = [dict(id='DEMO-%s-%02d'%(short,i), type=kind, status='RUN',
                           recipe='R1', card='CARD1', proberId='DEMO-P-%s-%02d'%(short,i))
                      for kind,short in [('K8000L','K80'),('T5830','T58'),('JH6800','JH')]
                      for i in range(1,4)]
    d['equipment'][-1]['status']='DOWN'
    d['equipment'] += [dict(id='DEMO-OVEN-%02d'%i,type='OVEN',status='RUN') for i in (1,2)]
    d['bakeChambers']=4
    d['recipes']=[dict(recipe='R%d'%i,cards=['CARD%d'%i]) for i in range(1,5)]
    routes = [
        [['CP1','R1',2],['BAKE','210C24H',24],['CP2','R2',3]],
        [['CP1','R1',3],['BAKE','210C24H',24],['CP2','R2',2]],
        [['CP1','R2',2.5],['BAKE','250C24H',24],['CP2','R3',4]],
        [['CP1','R3',5]],
        [['CP1','R1',2],['BAKE','210C24H',24],['CP2','R3',3],['CP3','R4',2]],
        [['CP1','R4',6],['CP2','R2',3]],
    ]
    d['flows']=dict(zip(products,routes))
    d['demands']=[dict(ppn=p,wafer=350) for p in products]
    d['lots']=[dict(uid='FULL-%04d'%i,lotId=products[i%6]+'-L%04d'%i,ppn=products[i%6],
                    wafer=[25,20,15][(i//6)%3],platform='K8000L;T5830;JH6800',
                    state='READY',availableAt=iso(0),shipDue=iso(48+(i%12)*8),location='测试待排区')
               for i in range(72)]
    for i,h in [(0,-2),(12,-336),(24,-400)]:
        d['lots'][i].update(state='DONE',actualComplete=iso(h),location='成品区')
    for i,m in [(1,'DEMO-K80-01'),(13,'DEMO-T58-01')]:
        d['lots'][i].update(state='RUNNING',availableAt=None,location=m,
                           current=dict(eqp=m,step='CP1',recipe='R1',card='CARD1',start=iso(-1),end=iso(3)))
    d['lots'][13]['externalComplete']=iso(42)
    for i in (7,19):
        d['lots'][i].update(state='RUNNING',availableAt=None,location='DEMO-OVEN-01-C1',
                           current=dict(eqp='DEMO-OVEN-01',chamber='DEMO-OVEN-01-C1',step='BAKE',
                                        recipe='210C24H',start=iso(-8),end=iso(20)),externalComplete=iso(32))
    for i,h in [(2,24),(14,72),(26,120)]:
        d['lots'][i].update(state='FUTURE',availableAt=iso(h),location='Fab')
    for i in (3,15):d['lots'][i].update(state='FUTURE',availableAt=None,location='Fab')
    for i in (4,16):d['lots'][i].update(state='HOLD',holdReason='演示品质待放行',location='HOLD 区')
    d['lots'][5]['shipDue']=iso(2)
    d['lots'][11]['lotId']=d['lots'][5]['lotId']
    d['lots'][20]['platform']=' k8000l ; t5830; '
    d['pm']=paired_pm(d,'DEMO-T58-02')
    d['borrow']=[dict(eqp='DEMO-JH-01',recipe='r1',start=iso(0),hours=2)]
    d['constraints']=[dict(eqp='DEMO-K80-03',recipe='R4')]
    d['externalBlocks']=[dict(resource='DEMO-JH-02',start=iso(0),end=iso(12),reason='演示故障维修'),
                         dict(resource='DEMO-OVEN-02',start=iso(4),end=iso(8),reason='整台烤箱检修')]
    return d


def scenario_pack(key='full', now=None):
    from production_rules import rules
    definition = next((c for c in demo_catalog()['scenarios'] if c['key']==key), None)
    if not definition:
        raise ValueError('未知演示场景，请从内置列表选择')
    d = _comprehensive(now) if key in ('full','scale500','timeout2000') else demo_snapshot(now)
    preset = rules()
    anchor = datetime.fromisoformat(d['asOf'])
    month_end = (anchor.replace(day=28)+timedelta(days=4)).replace(day=1,hour=0,minute=0,second=0,microsecond=0)
    if key=='paired_pm':
        d['pm']=paired_pm(d,'CP-B-02')
        d['borrow']=[]
    if key=='freeze':preset['freezeHours']=1
    if key=='identity':
        d['identityStable']=False
        d['lots'][8]['lotId']=d['lots'][5]['lotId']
        for lot in d['lots']:lot.pop('uid')
    if key in ('recipe','month_end','capacity'):
        d.update(pm=[],borrow=[],externalBlocks=[],constraints=[],bakeChambers=1)
        d['equipment']=[dict(id='DEMO-CP-01',type='K8000L',status='RUN',recipe='R1',card='CARD1',proberId='DEMO-P-01')]
        if key=='month_end':anchor=month_end-timedelta(hours=2)
        if key=='capacity':
            anchor=month_end-timedelta(hours=48)
            d['equipment'].append(dict(d['equipment'][0],id='DEMO-CP-02',proberId='DEMO-P-02'))
        iso=lambda hours:(anchor+timedelta(hours=hours)).isoformat()
        d['asOf']=iso(0)
        products=['DEMO01','DEMO02','DEMO03'] if key=='capacity' else ['DEMO01','DEMO02'] if key=='recipe' else ['DEMO01']
        d['flows']={p:[['CP1','R1',9.6 if key=='capacity' else 2.4 if key=='month_end' else 1]] for p in products}
        d['demands']=[dict(ppn=p,wafer=1000 if key=='capacity' else 100) for p in products]
        d['lots']=[dict(uid='CASE-%04d'%i,lotId=products[i%len(products)]+'-L%04d'%i,
                        ppn=products[i%len(products)],wafer=25,platform='K8000L',state='READY',
                        availableAt=iso(0),shipDue=iso(12 if key=='capacity' else 8),location='演示待排区')
                   for i in range(definition['lots'])]
        if key=='recipe':
            d['flows']['DEMO01']=[['CP1','R2',1]]
            d['lots'][1].update(state='FUTURE',availableAt=iso(5))
            d['lots'][2].update(state='FUTURE',availableAt=iso(7))
            d['borrow']=[dict(eqp='DEMO-CP-01',recipe='R2',start=iso(2),hours=1)]
        if key=='month_end':
            d['lots'][0].update(state='DONE',actualComplete=iso(-1),location='成品区')
            d['lots'][2].update(state='FUTURE',availableAt=iso(1))
            d['lots'][3].update(state='FUTURE',availableAt=iso(3))
    if key in ('scale500','timeout2000'):
        for i in range(72,definition['lots']):
            template=copy.deepcopy(d['lots'][30+i%42])
            template.update(uid='SCALE-%05d'%i,lotId=template['ppn']+'-S%05d'%i)
            d['lots'].append(template)
        for demand in d['demands']:
            demand['wafer']=sum(l['wafer'] for l in d['lots'] if l['ppn']==demand['ppn'])+125
        preset['maxRuntimeSeconds']=60 if key=='scale500' else 2
    d['snapshotId']='DEMO-%s-%s'%(key,anchor.strftime('%Y%m%d'))
    definition['suggestedExpansion']={next(e['type'] for e in d['equipment'] if 'OVEN' not in e['type']):2}
    d['demoCase']=definition
    return {'snapshot':d,'rules':preset}


def demo_event(snapshot, key):
    """Inject a fully specified, deterministic example change into demo facts."""
    if snapshot.get('source')!='demo':
        raise ValueError('一键变化仅用于模拟数据')
    d=copy.deepcopy(snapshot)
    applied=d.setdefault('demoAppliedEvents',[])
    if key in applied:
        raise ValueError('该变化已应用；需要重测时请重新载入演示场景')
    anchor=datetime.fromisoformat(d['asOf'])
    iso=lambda hours:(anchor+timedelta(hours=hours)).isoformat()
    lot=None
    if key=='arrival_known':
        lot=next((l for l in d['lots'] if l['state']=='FUTURE' and not l.get('availableAt')),None)
        if lot:lot['availableAt']=iso(24)
        reason='演示：Fab 确认可用时间为快照后 24 小时'
    elif key=='arrival_delay':
        lot=next((l for l in d['lots'] if l['state']=='FUTURE' and l.get('availableAt')),None)
        if lot:lot['availableAt']=(datetime.fromisoformat(lot['availableAt'])+timedelta(hours=48)).isoformat()
        reason='演示：已知来料时间延迟 48 小时'
    elif key=='due_urgent':
        lot=next((l for l in d['lots'] if l['state']=='READY' and datetime.fromisoformat(l['shipDue'])>anchor+timedelta(hours=2)),None)
        if lot:lot['shipDue']=iso(2)
        reason='演示：内部出货日提前至快照后 2 小时'
    elif key=='release_hold':
        lot=next((l for l in d['lots'] if l['state']=='HOLD'),None)
        if lot:lot.update(state='READY',availableAt=iso(0),holdReason='',location='测试待排区')
        reason='演示：品质确认放行，物料立即可用'
    elif key=='external_forecast':
        lot=next((l for l in d['lots'] if l['state']=='RUNNING' and not l.get('externalComplete')),None)
        if lot:lot['externalComplete']=(datetime.fromisoformat(lot['current']['end'])+timedelta(hours=48)).isoformat()
        reason='演示：外部系统补充最终加工完工预测，不生成后续任务'
    elif key=='machine_down':
        # Pick a RUN CP with no existing current/PM/borrowing commitment in the interval.
        occupied={l['current']['eqp'] for l in d['lots'] if l['state']=='RUNNING'}
        occupied.update(b['resource'] for b in d.get('externalBlocks',[]))
        occupied.update(b['eqp'] for b in d.get('borrow',[]))
        occupied.update(p['eqp'] for p in d.get('pm',[]))
        machine=next((e for e in d['equipment'] if e['status']=='RUN' and 'OVEN' not in e['type'] and e['id'] not in occupied and e.get('proberId') not in occupied),None)
        if not machine:raise ValueError('当前场景没有可用于停机演示的空闲 CP，请载入综合生产场景')
        reason='演示：%s 故障停机 12 小时'%machine['id']
        d.setdefault('externalBlocks',[]).append(dict(resource=machine['id'],start=iso(0),end=iso(12),reason=reason))
    else:
        raise ValueError('未知的一键变化')
    if key!='machine_down' and not lot:
        raise ValueError('当前场景没有符合条件的 Lot，请载入综合生产场景')
    if lot:reason+=' · '+lot['lotId']+' / '+lot['uid']
    applied.append(key)
    d['snapshotId'] += '-'+key
    return d, reason


def validation_examples():
    """Valid and intentionally invalid examples for the real input validator."""
    cases=[]
    def add(key,title,change,expected='accept',message=''):
        d=demo_snapshot()
        change(d)
        cases.append(dict(key=key,title=title,input=d,expected=expected,message=message))
    add('repeated_type','同一 TYPE 多台设备应通过',lambda d:None)
    add('duplicate_display','显示 LotID 重复但 uid 不同应通过',lambda d:d['lots'][8].update(lotId=d['lots'][5]['lotId']))
    add('mixed_case','Platform 大小写、分隔符空白应通过',lambda d:d['lots'][5].update(platform=' cp-a ; cp-b; '))
    add('unknown_eta','未来来料日期未知允许展示但不排产',lambda d:d['lots'][3].update(availableAt=None))
    add('duplicate_uid','稳定批次 uid 重复应拒绝',lambda d:d['lots'][8].update(uid=d['lots'][5]['uid']),'reject','uid 重复')
    add('missing_uid','稳定标识模式缺少 uid 应拒绝',lambda d:d['lots'][0].pop('uid'),'reject','提供 uid')
    add('bad_platform','Platform 无匹配 TYPE 应拒绝',lambda d:d['lots'][0].update(platform='K8000-MISSING'),'reject','Platform 未匹配')
    add('bad_date','无效出货日期应拒绝',lambda d:d['lots'][0].update(shipDue='2026-02-30'),'reject','日期无效')
    add('bad_month','无效月份应拒绝',lambda d:d.update(month='2026-13'),'reject','计划月份')
    add('missing_route','产品缺少路线应拒绝',lambda d:d['flows'].pop('DEMO01'),'reject','产品缺少路线')
    add('zero_wafer','0 片 Lot 应拒绝',lambda d:d['lots'][0].update(wafer=0),'reject','Lot wafer')
    add('large_lot','单 Lot 超过 25 片应拒绝',lambda d:d['lots'][0].update(wafer=26),'reject','Lot wafer')
    add('bad_pm','PM 引用未知对象应拒绝',lambda d:d['pm'][0].update(eqp='UNKNOWN-EQP'),'reject','未知')
    add('bad_borrow','借机缺少要求 Recipe 应拒绝',lambda d:d['borrow'][0].update(recipe=''),'reject','借机需要')
    add('duplicate_eqp','设备编号重复应拒绝',lambda d:d['equipment'].append(copy.deepcopy(d['equipment'][0])),'reject','编号须唯一')
    add('ready_future','已到位但可用时间在未来应拒绝',lambda d:d['lots'][5].update(availableAt=(datetime.fromisoformat(d['asOf'])+timedelta(days=1)).isoformat()),'reject','可用时间')
    add('rest_route','不能把静置填成加工等待应拒绝',lambda d:d['flows']['DEMO01'].append(['QTIME1','14',14]),'reject','静置')
    add('bad_wip_object','在制当前占用格式错误应拒绝',lambda d:d['lots'][1].update(current=['invalid']),'reject','current 必须')
    def overlap(d):
        other=copy.deepcopy(d['lots'][1]);other.update(uid='OTHER',lotId='OTHER');d['lots'].append(other)
    add('wip_overlap','同 CP 重复在制占用应拒绝',overlap,'reject','时间冲突')
    add('bad_chamber','BAKE 在制腔体越界应拒绝',lambda d:d['lots'][1]['current'].update(eqp='OVEN-01',step='BAKE',recipe='210C24H',chamber='OVEN-01-C99'),'reject','腔体')
    return cases


def check_examples():
    from production import validate_snapshot, prepare
    from production_rules import rules
    results=[]
    for case in validation_examples():
        message='输入检查通过；未保存、未执行排产'
        accepted=True
        try:
            d=validate_snapshot(case['input'])
            prepare(dict(snapshot=d,rules=rules(),active=None),{})
        except Exception as error:
            accepted=False
            message=str(error)
        passed=accepted if case['expected']=='accept' else not accepted and case['message'] in message
        results.append(dict(key=case['key'],title=case['title'],expected=case['expected'],
                            accepted=accepted,passed=passed,message=message))
    return dict(passed=all(r['passed'] for r in results),checks=results)
