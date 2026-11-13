"""Read-only chart projection of a selected unified plan, including frozen work."""
import copy
from scheduler import parse_date


def chart_projection(plan):
    d=plan['input']; month=d['month']; y,m=map(int,month.split('-'))
    end='%04d-%02d-01'%(y+1 if m==12 else y,1 if m==12 else m+1)
    resources={}; machines=[]; chambers=[]; tasks=[]; batches={}; lots=[]
    for e in plan['equipment']:
        is_oven='OVEN' in e['type']
        for number in range(1,d.get('bakeChambers',4)+1) if is_oven else [0]:
            key=e['id']+'-C%d'%number if is_oven else e['id']
            resource=dict(id=key,eqp=e['id'],type=e['type'],kind='bake' if is_oven else 'cp',
                          pm=[],tasks=[],batches=[],borrows=[],wip=[],locked=[])
            if e['status']!='RUN':
                resource['wip'].append(dict(lotId=e['status'],start=d['asOf'],end=end,reason='设备不可用'))
            resources[key]=resource
            (chambers if is_oven else machines).append(resource)
    for lot in plan['lots']:
        cp=[]
        for raw in lot['tasks']:
            t=dict(copy.deepcopy(raw),lotId=lot['lotId'],lotKey=lot['uid'],wafer=lot['wafer'],prod=lot['ppn'])
            if t.get('machine'):
                r=resources[t['machine']];t['platform']=r['type'];cp.append(t);tasks.append(t);r['tasks'].append(t)
            elif t.get('chamber'):
                key=(t['chamber'],t['start'],t.get('resourceEnd',t['end']))
                if key not in batches:
                    b=dict(id='BATCH-%04d'%(len(batches)+1),chamber=t['chamber'],recipe=t['recipe'],
                           start=t['start'],end=key[2],lotEnd=t['end'],duration=key[2]-t['start'],lots=[],lotKeys=[],wafers=0)
                    batches[key]=b;resources[t['chamber']]['batches'].append(b)
                b=batches[key];b['lots'].append(lot['lotId']);b['lotKeys'].append(lot['uid']);b['wafers']+=lot['wafer']
        lots.append(dict(id=lot['lotId'],key=lot['uid'],cpTasks=cp))
    for lot in d['lots']:
        if lot['state']=='RUNNING':
            w=dict(lot['current'],lotId=lot['lotId'],lotKey=lot['uid'])
            resources[w.get('chamber') or w['eqp']]['wip'].append(w)
    for block in d.get('externalBlocks',[]):
        targets=[r for r in resources.values() if r['id']==block['resource'] or r['eqp']==block['resource']]
        for r in targets:r['wip'].append(dict(block,lotId=block.get('reason') or '外部占用'))
    for pm in plan['pm']:
        for resource in pm['resources']:resources[resource]['pm'].append([pm['start'],pm['end']])
    for b in plan['borrows']:
        if b.get('reserved'):resources[b['eqp']]['borrows'].append(copy.deepcopy(b))
    return dict(planId=plan['id'],start=parse_date(month+'-01'),monthEnd=parse_date(end),
                machines=machines,chambers=chambers,cpTasks=tasks,batches=list(batches.values()),lots=lots,pmRecords=plan['pm'])
