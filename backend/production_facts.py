"""Fact reconciliation and canonical business snapshot identity.

This contract accepts complete monthly snapshots only. Delta streams must first
be assembled by an upstream adapter; an absent running row is never a release.
"""
import copy
import hashlib
import json
from input_rules import parse_datetime, identifier, CST

DATE_FIELDS = {'asOf','availableAt','shipDue','actualComplete','externalComplete',
               'start','end','early','due','over','expectedEnd','completeTime','updatedAt'}


def canonical(value, key=''):
    if isinstance(value,dict):
        return {k:canonical(v,k) for k,v in sorted(value.items())
                if k not in ('factIssues','inputNotes','receivedAt')}
    if isinstance(value,list):
        rows = [canonical(v,key) for v in value]
        # Route order and operation arrays are meaningful; record tables are sets.
        if rows and all(isinstance(v,dict) for v in rows):
            return sorted(rows,key=lambda v:json.dumps(v,sort_keys=True,ensure_ascii=False))
        if key=='cards':
            return sorted(set(rows))
        return rows
    if key in DATE_FIELDS and value:
        return parse_datetime(value).astimezone(CST).isoformat()
    if isinstance(value,float) and value.is_integer():return int(value)
    return value


def digest(snapshot):
    body = json.dumps(canonical(snapshot),sort_keys=True,ensure_ascii=False,allow_nan=False)
    return hashlib.sha256(body.encode()).hexdigest()


def complete_import(snapshot):
    meta = snapshot.get('ingestion',{})
    if not isinstance(meta,dict):
        raise ValueError('ingestion 必须为导入完整性对象')
    if meta.get('mode','full')!='full':
        raise ValueError('当前只接收完整全量快照；增量须先合成为全量，不能据缺行释放资源')
    if meta.get('complete',True) is not True:
        raise ValueError('分页或来源数据不完整，拒绝发布生产快照')
    if meta.get('scope','production-month')!='production-month':
        raise ValueError('快照覆盖范围必须为 production-month，不能用仅在制列表替代完整生产数据')
    if meta.get('sourceVersion') is not None and not isinstance(meta['sourceVersion'],str):
        raise ValueError('来源版本必须按字符串传输，避免编号精度损失')


def reconcile(previous, incoming):
    issues = list(incoming.get('factIssues',[]))
    if not previous or previous.get('source')!=incoming.get('source'):
        return issues
    protected = [l for l in previous['lots'] if l['state'] in ('RUNNING','DONE')]
    if protected and not (previous.get('identityStable') and incoming.get('identityStable')):
        issues.append('已有在制或完工事实，但跨快照稳定标识尚未确认，无法核实资源释放与产出')
        return issues
    new = {l['uid']:l for l in incoming['lots']}
    old_eqp={e['id']:e for e in previous['equipment']}
    new_eqp={e['id']:e for e in incoming['equipment']}
    for old in previous['lots']:
        row = new.get(old['uid'])
        if row is None:
            # Old-month completions need not reappear in a new monthly scope.
            if old['state']=='RUNNING' or (old['state']=='DONE' and previous['month']==incoming['month']):
                issues.append('原%s Lot %s 在完整快照中缺失，需明确完成或释放事实' %
                              ('在制' if old['state']=='RUNNING' else '已完工',old['lotId']))
            continue
        if old['ppn']!=row['ppn'] or old['wafer']!=row['wafer']:
            issues.append('Lot %s 身份或数量变化；拆合批、返工和数量修订需明确业务事件'%old['lotId'])
        if old['state']=='DONE' and (row['state']!='DONE' or parse_datetime(row['actualComplete'])!=parse_datetime(old['actualComplete'])):
            issues.append('已完工 Lot %s 不允许由普通快照回退或改写；需返工/撤销完工事件'%old['lotId'])
        if old['state']=='RUNNING':
            if row['state']=='DONE':
                if parse_datetime(row['actualComplete'])<parse_datetime(old['current']['start']):
                    issues.append('Lot %s 的完工时间早于原在制开始'%old['lotId'])
            elif row['state']!='RUNNING':
                issues.append('在制 Lot %s 缺少明确释放事实，不允许直接变为 %s'%(old['lotId'],row['state']))
            else:
                keys=('eqp','chamber','step','recipe','card','start')
                if any(canonical(old['current'].get(k),k)!=canonical(row['current'].get(k),k) for k in keys):
                    issues.append('在制 Lot %s 的工序或装配已变化，需明确工序结束/转移事件'%old['lotId'])
                eqp=old['current']['eqp']
                if old_eqp.get(eqp,{}).get('proberId')!=new_eqp.get(eqp,{}).get('proberId'):
                    issues.append('在制 Lot %s 的 Prober 配对变化，需核实实际释放/装配事件'%old['lotId'])
    new_pm={p.get('id'):p for p in incoming.get('pm',[]) if p.get('id')}
    def pm_state(p):
        state=identifier(p.get('state') or 'PLANNED')
        return {'COMPLETED':'DONE','CANCELED':'CANCELLED','IN_PROGRESS':'RUNNING'}.get(state,state)
    for old in previous.get('pm',[]):
        key=old.get('id')
        if not key:continue
        row=new_pm.get(key);before=pm_state(old)
        if not row:
            if before in ('RUNNING','PLANNED'):
                issues.append('未结束 PM %s 在新快照中缺失，需明确完成或取消；执行中维护须确认释放'%key)
            continue
        after=pm_state(row)
        if old.get('version') is not None and row.get('version') is not None:
            if row['version']<old['version']:
                issues.append('PM %s 的数据版本倒退，需核对来源'%key)
            elif row['version']==old['version'] and canonical(row)!=canonical(old):
                issues.append('PM %s 同一数据版本出现不同内容'%key)
        if before=='RUNNING':
            if after=='DONE':
                if parse_datetime(row['completeTime'])<parse_datetime(old['start']):
                    issues.append('PM %s 完工时间早于实际开始'%key)
            elif after!='RUNNING':
                issues.append('执行中 PM %s 缺少实际完成/释放事实，取消不能自动解除停机'%key)
            elif identifier(row.get('eqp'))!=identifier(old.get('eqp')) or canonical(row.get('start'),'start')!=canonical(old.get('start'),'start'):
                issues.append('执行中 PM %s 的对象或实际开始被改写'%key)
        if before=='DONE' and after!='DONE':
            issues.append('已完成 PM %s 不允许按同一任务 ID 重新执行'%key)
    return list(dict.fromkeys(issues))


def pm_lifecycle(snapshot):
    """Keep finished PM for traceability without reserving it again."""
    active=[]
    for raw in snapshot.get('pm',[]):
        row=copy.deepcopy(raw)
        state=identifier(row.get('state') or 'PLANNED')
        aliases={'COMPLETED':'DONE','CANCELED':'CANCELLED','IN_PROGRESS':'RUNNING'}
        state=aliases.get(state,state)
        if state not in ('PLANNED','RUNNING','DONE','CANCELLED'):
            raise ValueError('PM 状态未知：'+state)
        row['state']=state
        for key in ('id','sourceId','taskId'):
            if row.get(key) is not None and not isinstance(row[key],str):
                raise ValueError('PM '+key+' 必须按字符串传输')
        if state in ('DONE','CANCELLED'):
            if state=='DONE' and not row.get('completeTime'):
                raise ValueError('已完成 PM 需要 completeTime')
            if row.get('completeTime') and parse_datetime(row['completeTime'])>parse_datetime(snapshot['asOf']):
                raise ValueError('PM 实际完成不能晚于快照时点')
            continue
        active.append(row)
    return active
