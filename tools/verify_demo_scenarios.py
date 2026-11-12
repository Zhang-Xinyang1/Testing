#!/usr/bin/env python3
"""Exercise built-in demos with independent resource/quantity audits.

Uses temporary workspaces, never edits the user's production configuration.
Run: python tools/verify_demo_scenarios.py
"""
import copy
from datetime import datetime
import html
import json
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from production import (adopt, archive, context_hash, month_bounds, read_plan,
                        read_state, trial, update, validate_snapshot)
from production_demo import check_examples, demo_catalog, demo_event, scenario_pack
from production_jobs import TrialJobs
from scheduler import HOUR, MIN, parse_date


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def workspace(pack):
    return dict(snapshot=validate_snapshot(pack['snapshot']), rules=pack['rules'],
                active=None, events=[], history=[], revision=1, changePending=True)


def audit_plan(plan):
    """Reconstruct physical calendars and forecasts from output, not metrics."""
    d=plan['input']; asof=parse_date(d['asOf'])
    begin,finish=month_bounds(d['month'])
    begin,finish=begin.timestamp()*1000,finish.timestamp()*1000
    equipment={e['id']:e for e in plan['equipment']}
    resources={}
    committed={}
    batches={}
    cp_tasks={}
    def reserve(resource,start,end,label):
        require(end>start,'占用时长须大于零：'+label)
        resources.setdefault(resource,[]).append((start,end,label))
    def expand(resource):
        return [resource+'-C%d'%i for i in range(1,d.get('bakeChambers',4)+1)] if resource in equipment and 'OVEN' in equipment[resource]['type'] else [resource]
    wip_seen=set()
    for lot in d['lots']:
        if lot['state']!='RUNNING':continue
        w=lot['current'];resource=w.get('chamber') or w['eqp']
        start,end=parse_date(w['start']),parse_date(w['end'])
        key=(resource,start,end)
        if not w.get('chamber') or key not in wip_seen:
            reserve(resource,start,end,'在制 '+lot['uid'])
        wip_seen.add(key)
        committed.setdefault(w['eqp'],[]).append(dict(w,end=end))
    for block in d.get('externalBlocks',[]):
        start,end=parse_date(block['start']),parse_date(block['end'])
        for resource in expand(block['resource']):reserve(resource,start,end,'外部占用')
        committed.setdefault(block['resource'],[]).append(dict(block,end=end))
    for pm in plan['pm']:
        for resource in pm['resources']:reserve(resource,pm['start'],pm['end'],'PM '+pm['id'])
    for b in plan['borrows']:
        if b.get('reserved'):
            reserve(b['eqp'],b['start'],b['end'],'借机')
            require(b.get('recipeMatched'), '安排的借机 Recipe 必须匹配')
    require({l['uid'] for l in plan['lots']}=={l['uid'] for l in d['lots']},'不能丢失或合并批次')
    require(len(plan['lots'])==len(d['lots']),'Lot 数量不一致')
    tasks=0
    for lot in plan['lots']:
        route=lot['tasks'];tasks+=len(route)
        if lot['state'] in ('RUNNING','DONE','HOLD') or not lot.get('availableAt'):
            require(not route,'不可生成后续任务：'+lot['uid'])
        ready=max(asof,parse_date(lot.get('availableAt')) or asof)
        for task in route:
            start,end=task.get('blockStart',task['start']),task.get('resourceEnd',task['end'])
            require(start>=ready,'任务早于来料或前序放行：'+lot['uid'])
            ready=task['end']
            if task.get('machine'):
                resource=task['machine'];machine=equipment[resource]
                require(machine['status']=='RUN','非 RUN 机台被安排新加工')
                require(machine['type'] in lot['platform'].split(';'),'加工机台 TYPE 不兼容')
                require(not any(c['eqp']==resource and c['recipe']==task['recipe'] for c in d['constraints']),'违反禁加工')
                op=d['flows'][lot['ppn']][task['opIndex']]
                require(abs((task['end']-task['start'])-op[2]*MIN*lot['wafer'])<.01,'CP 工时未按 wafer 计算')
                reserve(resource,start,end,lot['uid']+' '+task['step'])
                cp_tasks.setdefault(resource,[]).append(dict(task,frozen=lot.get('frozen',False)))
            elif task.get('chamber'):
                key=(task['chamber'],start,end)
                members=batches.setdefault(key,[]);members.append((lot['uid'],task['recipe']))
                if len(members)==1:reserve(key[0],start,end,'BAKE '+task.get('batchId',''))
                require(len(members)<=4 and len({r for _,r in members})==1,'BAKE 批量或 Recipe 错误')
                require(task['end']-start==26*HOUR and end-start==28*HOUR,'BAKE 放行/占腔时间错误')
        if route and lot.get('complete') is not None:
            require(lot['complete']==route[-1]['end'],'最后加工结束与完工不一致')
        if lot.get('complete') is not None:
            require(lot['restEnd']==lot['complete']+336*HOUR,'静置期错误')
    for resource,slots in resources.items():
        ordered=sorted(slots)
        for a,b in zip(ordered,ordered[1:]):
            require(a[1]<=b[0],'%s 资源重叠：%s / %s'%(resource,a[2],b[2]))
    for resource,tasks_on_machine in cp_tasks.items():
        for task in tasks_on_machine:
            earlier=[t for t in tasks_on_machine+committed.get(resource,[]) if t['end']<=task['blockStart']]
            card,recipe=equipment[resource].get('card'),equipment[resource].get('recipe')
            for old in sorted(earlier,key=lambda t:t['end']):
                card=old.get('card') or card;recipe=old.get('recipe') or recipe
            needed=(5*MIN if card!=task['card'] else 0)+(plan['rules']['recipeChangeMinutes']*MIN if recipe!=task['recipe'] else 0)
            actual=task['start']-task['blockStart']
            require(actual>=needed if task['frozen'] else abs(actual-needed)<.01,'切换占用不符合此前真实状态')
    for borrow in plan['borrows']:
        if not borrow.get('reserved'):continue
        resource=borrow['eqp']
        recipe=equipment[resource].get('recipe')
        earlier=[t for t in cp_tasks.get(resource,[])+committed.get(resource,[]) if t['end']<=borrow['start']]
        for previous in sorted(earlier,key=lambda t:t['end']):recipe=previous.get('recipe') or recipe
        require(recipe==borrow['req']['recipe'],'借机时点 Recipe 独立复算不匹配')
    for product in plan['products']:
        selected=[l for l in plan['lots'] if l['ppn']==product['ppn']]
        actual=sum(l['wafer'] for l in selected if l['state']=='DONE' and begin<=parse_date(l['actualComplete'])<finish)
        predicted=actual
        for lot in selected:
            if lot['state']=='DONE':continue
            end=parse_date(lot.get('externalComplete')) if lot['state']=='RUNNING' else lot.get('complete')
            if end is not None and asof<=end<finish:predicted+=lot['wafer']
        require(actual==product['actual'] and predicted==product['forecast'],'实际/预测有遗漏或重复')
        require(product['targetDelta']==predicted-product['target'],'目标差额错误')
    return dict(tasks=tasks, resources=len(resources), checked='资源不重叠、工序顺序、切换时间、批次身份、来料、wafer 工时、PM/在制/外部占用、月度统计与静置')


def bounded_trial(state, request=None, cancel=False):
    jobs=TrialJobs()
    try:
        job=jobs.start(state,request or {})
        if cancel:jobs.cancel(job['id'])
        deadline=time.monotonic()+state['rules']['maxRuntimeSeconds']+8
        while time.monotonic()<deadline:
            job=jobs.read()
            if job['status']!='running':return job
            time.sleep(.1)
        raise AssertionError('试排未在预算和退出宽限内停止')
    finally:
        jobs.close()


def run_suite():
    report=dict(time=datetime.now().astimezone().isoformat(),synthetic=True,scenarios=[],workflows=[],validation=check_examples())
    plans={};states={}
    for case in demo_catalog()['scenarios']:
        state=workspace(scenario_pack(case['key']));states[case['key']]=state
        item=dict(key=case['key'],title=case['title'],lots=len(state['snapshot']['lots']),
                  wafers=sum(l['wafer'] for l in state['snapshot']['lots']),passed=False)
        start=time.monotonic()
        try:
            job=bounded_trial(state)
            item.update(status=job['status'],elapsed=job['elapsedSeconds'],evaluations=job['evaluations'])
            if case['key']=='timeout2000':
                require(job['status'] in ('completed','timed_out'),'大批量任务非预期失败')
                require(time.monotonic()-start<7,'2 秒计算停止保护超出退出宽限')
            else:require(job['status']=='completed',job['message'])
            if job['plan']:
                p=job['plan'];plans[case['key']]=p
                item.update(audit=audit_plan(p),forecast=sum(x['forecast'] for x in p['products']),alerts=len(p['alerts']))
                if case['key']=='month_end':require(p['products'][0]['actual']==25 and p['products'][0]['forecast']==50,'月末预期必须为实际 25、预测 50')
                if case['key']=='paired_pm':require(len(p['pm'])==3 and sum(len(x['sourcePlans']) for x in p['pm'])==4,'PM 应为 4 条维护、3 段停机')
                if case['key']=='recipe':
                    first=next(l for l in p['lots'] if l['uid']=='CASE-0000')['tasks'][0]
                    require(first['setup']==35*MIN and p['borrows'][0]['recipeAtStart']=='R2','借机/35 分切换不符合预期')
            else:item['audit']=dict(checked='计算到时停止；尚无可行候选，因此未进行排程资源审计')
            item['passed']=True
        except Exception as error:item['error']=str(error)
        report['scenarios'].append(item)
        print(case['key'],item.get('status'),item.get('elapsed'),item['passed'],item.get('error',''),flush=True)
    def workflow(name, fn):
        item=dict(name=name,passed=False)
        try:item['detail']=fn();item['passed']=True
        except Exception as error:item['error']=str(error)
        report['workflows'].append(item)
        print(name,item['passed'],item.get('error',''),flush=True)
    def expand():
        s=copy.deepcopy(states['capacity']);s['active']=plans['capacity']
        request=dict(scenario='expansion',extraMachines=s['snapshot']['demoCase']['suggestedExpansion'])
        p=trial(s,request);audit_plan(p)
        before=sum(x['forecast'] for x in plans['capacity']['products']);after=sum(x['forecast'] for x in p['products'])
        require(after>before,'增加 CP 后月内产出应改善')
        return dict(before=before,after=after,delta=after-before)
    workflow('实际资源与扩产独立比较',expand)
    def freeze():
        s=copy.deepcopy(states['freeze']);s['active']=plans['freeze'];p=trial(s,{})
        audit_plan(p);require(p['frozenLots']>0,'第二次试排应有冻结任务')
        original={l['uid']:l for l in s['active']['lots']}
        for lot in p['lots']:
            if lot.get('frozen'):require(lot['tasks']==original[lot['uid']]['tasks'],'冻结路线被修改')
        return dict(frozenLots=p['frozenLots'])
    workflow('已选基线与整 Lot / BAKE 批次冻结',freeze)
    for event in demo_catalog()['events']:
        def changed(key=event['key']):
            s=copy.deepcopy(states['full']);s['active']=plans['full']
            d,reason=demo_event(s['snapshot'],key);s['snapshot']=validate_snapshot(d)
            require(s['active']['contextHash']!=context_hash(s),'变化后旧候选必须失效')
            p=trial(s,{});audit_plan(p)
            try:demo_event(s['snapshot'],key)
            except ValueError:pass
            else:raise AssertionError('重复变化未被阻止')
            return dict(reason=reason,changedLots=len(p['changes']),forecastDelta=sum(x['forecastDelta'] or 0 for x in p['products']))
        workflow(event['title'],changed)
    def recipe_parameter():
        s=copy.deepcopy(states['recipe']);s['rules']['recipeChangeMinutes']=15
        p=trial(s,{});audit_plan(p)
        first=next(l for l in p['lots'] if l['uid']=='CASE-0000')['tasks'][0]
        require(first['setup']==20*MIN,'15 分 Recipe + 5 分针卡应为 20 分')
        return '30 改为 15 后，首工序切换从 35 分钟变为 20 分钟'
    workflow('可配置 Recipe 切换耗时',recipe_parameter)
    def cancel():
        job=bounded_trial(states['timeout2000'],cancel=True)
        require(job['status']=='cancelled','取消任务没有停止')
        return dict(status=job['status'],elapsed=job['elapsedSeconds'])
    workflow('主动取消后台试排',cancel)
    def persist():
        with tempfile.TemporaryDirectory() as tmp:
            config=Path(tmp)/'config.json';config.write_text('{}')
            s=update(config,'demo',{'case':'basic'});p=trial(s,{})
            archive(config,p);require(read_state(config)['active'] is None,'保存比较不应选用')
            adopt(config,p,'自动功能测试：模拟人工确认')
            archive(config,p)
            require(read_plan(config,p['id'])['adoptReason'],'方案未持久化')
            require(read_state(config)['active']['id']==p['id'],'重新读取丢失已选方案')
            fresh=trial(read_state(config),{})
            update(config,'demo-event',{'event':'arrival_known'})
            try:adopt(config,fresh,'过期候选')
            except ValueError:pass
            else:raise AssertionError('过期方案竟可选用')
            require(config.read_text()=='{}','误改 Excel 配置')
        return '比较记录、选用说明与快照可恢复；过期候选拒绝选用'
    workflow('保存、恢复、选用及旧候选保护',persist)
    report['passed']=report['validation']['passed'] and all(x['passed'] for x in report['scenarios']+report['workflows'])
    return report


def write_report(report):
    destination=ROOT/'output';destination.mkdir(exist_ok=True)
    (destination/'demo_test_results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    esc=lambda value:html.escape(str(value))
    rows=''.join('<tr>'+''.join('<td>'+esc(v)+'</td>' for v in [r['title'],r['lots'],r['wafers'],r.get('status','失败'),r.get('elapsed','—'),r.get('forecast','未取得候选'), '通过' if r['passed'] else r.get('error','失败')])+'</tr>' for r in report['scenarios'])
    workflows=''.join('<tr><td>'+esc(w['name'])+'</td><td>'+('通过' if w['passed'] else '失败')+'</td><td>'+esc(json.dumps(w.get('detail',w.get('error')),ensure_ascii=False))+'</td></tr>' for w in report['workflows'])
    checks=''.join('<tr><td>'+esc(c['title'])+'</td><td>'+('符合预期' if c['passed'] else '失败')+'</td><td>'+esc(c['message'])+'</td></tr>' for c in report['validation']['checks'])
    content='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>内置演示数据功能测试报告</title>
<style>body{font:15px/1.7 system-ui,sans-serif;color:#183c47;background:#f3f7f7;max-width:1200px;margin:36px auto;padding:0 24px}h1{font-size:30px}h2{margin-top:32px}table{width:100%;border-collapse:collapse;background:white}th,td{padding:10px 14px;border:1px solid #d9e5e4;text-align:left;vertical-align:top}th{background:#dfeeea}.note{background:#fff5d9;padding:18px;border-left:4px solid #bd941d}code{background:#e6ecee;padding:2px 5px}</style>'''
    content+='<h1>内置演示数据功能测试报告</h1><p>生成时间：'+esc(report['time'])+' · 结果：'+('全部符合预期' if report['passed'] else '存在失败，详见下表')+'</p>'
    content+='<p class="note">全部数据为合成演示数据。资源占用、切换与统计由独立审计重新计算。2000 Lot 场景故意只给 2 秒预算，用于验证停止功能；没有候选时不能声称已完成排程校验。耗时仅代表本机本次运行，不代表真实产线性能保证。</p>'
    content+='<h2>10 组内置场景</h2><table><tr><th>场景</th><th>Lot</th><th>wafer</th><th>计算状态</th><th>秒</th><th>月内预测 wafer</th><th>核对</th></tr>'+rows+'</table>'
    content+='<h2>连续业务流程</h2><table><tr><th>功能</th><th>结果</th><th>证据</th></tr>'+workflows+'</table>'
    content+='<h2>20 项输入样例</h2><p>使用与页面相同的校验器，检查有效输入与错误输入；不写入当前工作区。</p><table><tr><th>样例</th><th>结果</th><th>实际响应</th></tr>'+checks+'</table>'
    content+='<h2>使用与手动输入</h2><p>生产看板 → 选择演示场景 → 载入所选场景 → 滚动重排 → 开始试排。来源、Lot、设备、MPS、路线、Recipe、PM、借机、日期和样例原因均内置。</p><p>演示选用说明已预填，仍需要人工点击选用。变化记录里用“一键生产变化”，无需输入时间和原因。扩产预填新增 2 台；冻结场景预填 1 小时。</p><p>可选手动验证：选择“借机与 35 分钟切换”，将“换 Recipe（分钟）”填 <code>15</code> 后保存并试排，首工序切换应为 <code>20 分钟</code>。选用说明可复制：<code>演示验证：Recipe 切换改为 15 分钟，已核对总切换 20 分钟后选用。</code></p><p>真实运行仍需产线确认：稳定 Lot 标识、冻结范围、实际换 Recipe 耗时、预警阈值、每日检查时刻、真实 MES 数据与最终选用理由。演示默认值不代替现场确认。</p>'
    (destination/'演示功能测试报告.html').write_text(content,encoding='utf-8')


if __name__=='__main__':
    result=run_suite()
    write_report(result)
    print('Report:',ROOT/'output'/'演示功能测试报告.html',flush=True)
    raise SystemExit(0 if result['passed'] else 1)
