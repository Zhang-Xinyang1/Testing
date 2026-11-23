"""Separate historical shadow trials from executable, time-bounded candidates."""
from datetime import datetime, timedelta, timezone
from input_rules import parse_datetime

CST=timezone(timedelta(hours=8))
MINUTE=60000


def now_ms():
    return datetime.now(CST).timestamp()*1000


def milliseconds(value):
    return parse_datetime(value).timestamp()*1000


def live_facts(snapshot, settings, now):
    asof=milliseconds(snapshot['asOf'])
    if asof>now+MINUTE or now-asof>settings['snapshotMaxAgeMinutes']*MINUTE:
        raise ValueError('现场数据时点已过期或超前，请接收当前完整快照；历史数据请使用影子试排')
    for lot in snapshot['lots']:
        if lot['state']=='RUNNING' and milliseconds(lot['current']['end'])<=now:
            raise ValueError('在制 Lot %s 预计释放已到，实际释放待确认，请更新快照'%lot['lotId'])
    for pm in snapshot.get('pm',[]):
        if str(pm.get('state','')).upper() in ('RUNNING','IN_PROGRESS') and milliseconds(pm['expectedEnd'])<=now:
            raise ValueError('执行中 PM 预计释放已到，实际释放待确认')


def execution_context(snapshot, settings, request):
    mode=request.get('mode','shadow' if snapshot['source']=='demo' else 'live')
    if mode not in ('live','shadow'):
        raise ValueError('试排模式须为 live 或 shadow')
    asof=milliseconds(snapshot['asOf']);now=now_ms()
    if mode=='shadow':
        return dict(mode=mode,start=asof,requestedAt=now,validUntil=None)
    live_facts(snapshot,settings,now)
    start=max(asof,now+settings['planningLeadMinutes']*MINUTE)
    if request.get('executableFrom'):
        start=milliseconds(request['executableFrom'])
        if start<max(asof,now):raise ValueError('计划可执行起点不能早于当前时间或数据时点')
    return dict(mode=mode,start=start,requestedAt=now,
                validUntil=min(start,asof+settings['snapshotMaxAgeMinutes']*MINUTE))


def check_adoption(plan, snapshot, settings):
    execution=plan.get('execution')
    if not execution:
        raise ValueError('旧版候选缺少执行时效信息，请重新试排')
    if execution['mode']=='shadow':return
    now=now_ms()
    live_facts(snapshot,settings,now)
    if now>=execution['validUntil']:
        raise ValueError('候选执行起点或数据有效期已过，请更新事实并重新试排，不能整体平移旧方案')
    if any(t.get('blockStart',t['start'])<now for lot in plan['lots'] for t in lot.get('tasks',[])):
        raise ValueError('候选包含已过时的待开工任务，请重新试排')
    if any(pm['start']<now for pm in plan['pm']):
        raise ValueError('候选 PM 开始时间已过，请更新维护事实并重新试排')
