"""Reconstruct feasibility from input facts and candidate operations.

This checker does not call the schedule decoder or trust its feasibility flags.
Only schema meanings, identity resolution and the output ledger are shared.
"""
import math
from collections import defaultdict
from input_rules import pm_events, resolve_pm_target
from production_facts import pm_lifecycle
from production_ledger import output_ledger, time_ms
from task_overrides import TaskConstraintError

MINUTE=60000
HOUR=60*MINUTE
DAY=24*HOUR
EPS=0.01


def require(ok,message):
    if not ok:
        raise TaskConstraintError('独立排程校验失败：'+message,{'table':'production','section':'validation'})


def finite(value,label):
    require(type(value) in (int,float) and math.isfinite(value),label+' 必须为有限时间')
    return value


def validate_plan(snapshot, plan, settings):
    require(not snapshot.get('factIssues'),'生产事实尚未确认')
    require(plan.get('input')==snapshot and plan.get('rules')==settings,'候选事实或规则与当前输入不一致')
    require(plan.get('month')==snapshot['month'],'月份不一致')
    original={e['id']:e for e in snapshot['equipment']}
    equipment={e['id']:e for e in plan['equipment']}
    require(len(equipment)==len(plan['equipment']),'设备编号重复')
    require(all(equipment.get(k)==v for k,v in original.items()),'候选改写了实际资源')
    if plan['scenario']=='actual':require(set(equipment)==set(original),'实际方案包含假设设备')
    prober={}
    for e in equipment.values():
        if e.get('proberId'):
            require(e['proberId'] not in prober,'Prober '+e['proberId']+' 同时属于多台 EQP')
            prober[e['proberId']]=e['id']
    resource_eqp={e['id']:e['id'] for e in equipment.values() if 'OVEN' not in e['type']}
    for e in equipment.values():
        if 'OVEN' in e['type']:
            for n in range(1,snapshot.get('bakeChambers',4)+1):resource_eqp[e['id']+'-C%d'%n]=e['id']
    calendars=defaultdict(list);events=defaultdict(list);task_events=defaultdict(list)
    batches=defaultdict(list);batch_ids={}
    recipes={r['recipe']:r['cards'] for r in snapshot['recipes']}
    forbidden={(r['eqp'],r['recipe']) for r in snapshot.get('constraints',[])}
    start=finite(plan['execution']['start'],'计划起点')
    asof=time_ms(snapshot['asOf'])

    def reserve(resource,begin,end,label,recipe=None,card=None,transition=False):
        require(resource in resource_eqp,'未知资源 '+str(resource))
        finite(begin,label+' 开始');finite(end,label+' 结束')
        require(begin<end,label+' 的占用区间无效')
        calendars[resource].append((begin,end,label))
        eqp=equipment[resource_eqp[resource]]
        if eqp.get('proberId'):
            calendars['PROBER:'+eqp['proberId']].append((begin,end,label))
        if transition:events[resource].append((end,recipe,card))

    # Preserve actual occupation even though no subsequent WIP work is planned.
    wip_batches=defaultdict(list)
    for lot in snapshot['lots']:
        if lot['state']!='RUNNING':continue
        w=lot['current'];resource=w.get('chamber') or w['eqp']
        key=(resource,time_ms(w['start']),time_ms(w['end']),w['recipe'],w.get('batchId'))
        if w.get('chamber'):
            wip_batches[key].append(lot)
            require(len(wip_batches[key])<=4,'同腔在制批次超过 4 Lot')
            if len(wip_batches[key])>1:continue
        reserve(resource,key[1],key[2],'在制 '+lot['uid'],w['recipe'],w.get('card'),True)
    for block in snapshot.get('externalBlocks',[]):
        resources=[r for r,e in resource_eqp.items() if r==block['resource'] or e==block['resource']]
        require(bool(resources),'外部占用资源不存在')
        for resource in resources:
            reserve(resource,time_ms(block['start']),time_ms(block['end']),'外部占用',block.get('recipe'),block.get('card'),True)
    pending=[]
    pm_context={'eqp':list(equipment.values()),'bakeResources':{'chambers':snapshot.get('bakeChambers',4)}}
    for pm in pm_lifecycle(snapshot):
        if pm['state']=='RUNNING':
            target=resolve_pm_target(pm_context,pm)
            for resource in target['resources']:
                reserve(resource,time_ms(pm['start']),time_ms(pm['expectedEnd']),'执行中 PM')
        else:pending.append(pm)
    expected_pm=pm_events(dict(pm_context,pm=pending))
    expected={p['id']:p for p in expected_pm}
    require(len(plan['pm'])==len(expected),'PM 事项数量不一致')
    seen=set()
    for pm in plan['pm']:
        require(pm['id'] in expected and pm['id'] not in seen,'PM 事项重复或未知')
        seen.add(pm['id']);source=expected[pm['id']]
        begin,end=finite(pm['start'],'PM 开始'),finite(pm['end'],'PM 结束')
        require(abs(end-begin-source['hours']*HOUR)<EPS,'PM 时长被改写')
        require(begin>=time_ms(source['early']) and end<=time_ms(source['over'])+DAY,'PM 超出窗口')
        if source.get('start'):
            require(abs(begin-time_ms(source['start']))<EPS,'指定 PM 被移动')
            require(begin>=asof,'未开始 PM 被排到过去')
        else:require(begin>=start,'柔性 PM 被排到可执行起点之前')
        require(set(pm['resources'])==set(source['resources']),'PM 漏停组合资源')
        require(pm['sourcePlans']==source['sourcePlans'],'PM 原始事项或来源标识丢失')
        for resource in source['resources']:reserve(resource,begin,end,'PM '+pm['id'])

    source_lots={l['uid']:l for l in snapshot['lots']}
    require(len(plan['lots'])==len(source_lots),'Lot 数量不守恒')
    seen=set();derived=[]
    for lot in plan['lots']:
        uid=lot['uid'];require(uid in source_lots and uid not in seen,'Lot UID 重复或未知')
        seen.add(uid);source=source_lots[uid]
        require(all(lot.get(k)==source.get(k) for k in ('ppn','wafer','state','platform','lotId')),'Lot 身份、数量或状态被改写：'+uid)
        tasks=lot.get('tasks',[]);route=snapshot['flows'][source['ppn']]
        if source['state'] in ('DONE','RUNNING','EXTERNAL','HOLD') or not source.get('availableAt') or not source.get('shipDue'):
            require(not tasks,'不可新排的 Lot 生成了加工任务：'+uid)
            derived.append(dict(uid=uid,complete=None));continue
        require(len(tasks)<=len(route),'工序数量超过路线：'+uid)
        floor=max(asof,plan['execution']['requestedAt']) if lot.get('frozen') and plan['execution']['mode']=='live' else start
        previous=max(floor,time_ms(source['availableAt']))
        for index,t in enumerate(tasks):
            op=route[index];label=uid+' / '+op[0]
            require(t.get('opIndex')==index and t.get('step')==op[0] and t.get('recipe')==op[1],'工序顺序或 Recipe 不符合路线：'+label)
            begin,end=finite(t['start'],label),finite(t['end'],label)
            if op[0].startswith('CP'):
                resource=t.get('machine');require(resource in equipment and 'OVEN' not in equipment[resource]['type'],'CP 机台不存在：'+label)
                machine=equipment[resource]
                require(machine['status']=='RUN' and machine['type'] in source['platform'].split(';'),'机台资格或 Platform 已失效：'+label)
                require((resource,op[1]) not in forbidden,'违反禁加工条件：'+label)
                require(t.get('card') in recipes.get(op[1],[]),'针卡不兼容：'+label)
                require(abs(end-begin-op[2]*MINUTE*source['wafer'])<EPS,'CP 工时不符：'+label)
                block=finite(t.get('blockStart'),label+' 占用开始')
                require(block>=previous-EPS and begin>=block,'工序提前加工或前后工序冲突：'+label)
                require(abs(begin-block-t.get('setup',0))<EPS,'切换时间与资源占用不一致：'+label)
                reserve(resource,block,end,label)
                task_events[resource].append((block,begin,end,t,label))
                events[resource].append((end,op[1],t['card']))
            else:
                resource=t.get('chamber');require(resource in resource_eqp and 'OVEN' in equipment[resource_eqp[resource]]['type'],'BAKE 腔体不存在：'+label)
                require(equipment[resource_eqp[resource]]['status']=='RUN','BAKE 设备不可用：'+label)
                release=finite(t.get('resourceEnd'),label+' 资源释放')
                require(begin>=previous-EPS and abs(end-begin-26*HOUR)<EPS and abs(release-begin-28*HOUR)<EPS,'BAKE 升温、加工或占腔时间不符：'+label)
                key=(resource,begin,release,op[1]);batch=t.get('batchId')
                require(bool(batch),'BAKE 缺少批次标识：'+label)
                require(batch not in batch_ids or batch_ids[batch]==key,'BAKE 批次标识对应不同区间')
                batch_ids[batch]=key;batches[key].append(source)
                require(len(batches[key])<=4,'BAKE 一个批次超过 4 Lot')
                if len(batches[key])==1:reserve(resource,begin,release,'BAKE '+batch)
            previous=end
        completion=previous if len(tasks)==len(route) else None
        require(lot.get('complete')==completion,'最终完工不是最后一道加工结束：'+uid)
        derived.append(dict(uid=uid,complete=completion,frozen=lot.get('frozen',False)))

    # Setup state is rebuilt from earlier task/fact endings, not setup fields.
    for resource,tasks in task_events.items():
        machine=equipment[resource]
        for block,begin,end,t,label in tasks:
            recipe,card=machine.get('recipe'),machine.get('card')
            for when,r,c in sorted(events[resource],key=lambda x:x[0]):
                if asof<when<=block+EPS:
                    recipe=r or recipe;card=c or card
            recipe_ms=0 if recipe==t['recipe'] else settings['recipeChangeMinutes']*MINUTE
            card_ms=0 if card==t['card'] else settings['cardChangeMinutes']*MINUTE
            require(abs(begin-block-recipe_ms-card_ms)<EPS,'切换时间不足或错误：'+label)

    require(len(plan['borrows'])==len(snapshot.get('borrow',[])),'借机记录缺失')
    unmatched=list(snapshot.get('borrow',[]))
    for b in plan['borrows']:
        request=b['req']
        match=next((r for r in unmatched if r['eqp']==request['eqp'] and time_ms(r['start'])==time_ms(request['start']) and r['hours']==request['hours'] and r['recipe']==request['recipe']),None)
        require(match is not None,'借机重复或被改写');unmatched.remove(match)
        if not b.get('reserved'):
            require(b['status']=='cancel' and bool(b.get('reason')),'未安排借机缺少说明');continue
        begin,end=finite(b['start'],'借机开始'),finite(b['end'],'借机结束')
        require(begin==time_ms(match['start']) and abs(end-begin-match['hours']*HOUR)<EPS,'借机固定区间被改写')
        resource=match['eqp'];require(equipment[resource]['status']=='RUN','借机设备不可用')
        recipe=equipment[resource].get('recipe')
        for when,r,c in sorted(events[resource],key=lambda x:x[0]):
            if asof<when<=begin+EPS:recipe=r or recipe
        require(recipe==match['recipe'],'借机开始时 Recipe 不匹配：'+resource)
        reserve(resource,begin,end,'借机')

    for resource,intervals in calendars.items():
        ordered=sorted(intervals)
        for a,b in zip(ordered,ordered[1:]):
            require(a[1]<=b[0]+EPS,'资源 %s 冲突：%s 与 %s，区间 %s～%s'%(resource,a[2],b[2],b[0],min(a[1],b[1])))
    ledger=output_ledger(snapshot,derived)
    actual={p['ppn']:p for p in plan['products']}
    require(len(actual)==len(plan['products'])==len(snapshot['demands']),'产品汇总数量不符或重复')
    for p in ledger['products']:
        require(p['ppn'] in actual and all(actual[p['ppn']][k]==p[k] for k in ('target','actual','forecast','externalUnknownWafers')),'产出账本与产品汇总不一致：'+p['ppn'])
    require(plan.get('outputLedger')==ledger['rows'],'产出账本来源或去重结果不一致')
    return dict(passed=True,version=1,resources=len(calendars),lots=len(seen),
                checks=['事实与身份','工序与资格','组合资源占用','切换与借机','PM 窗口','BAKE 批次','产出去重'])
