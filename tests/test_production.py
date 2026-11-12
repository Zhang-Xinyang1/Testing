import copy
import json
import tempfile
import time
import unittest
import threading
import urllib.request
import urllib.error
from http.server import ThreadingHTTPServer
from pathlib import Path
from production import (validate_snapshot, read_state, write_state, update, trial, adopt,
                        prepare, daily_check, context_hash, archive, read_plan)
from production_demo import demo_snapshot
from production_rules import rules
from production_jobs import TrialJobs
from scheduler import run, parse_date, MIN, HOUR
from task_overrides import TaskConstraintError
from test_scheduler import fixture


def state():
    return dict(revision=1, snapshot=validate_snapshot(demo_snapshot()), rules=rules(),
                active=None, history=[], events=[], lastCheck=None, changePending=True)


class ProductionRuleTests(unittest.TestCase):
    def test_recipe_and_card_switches_are_additive_and_configurable(self):
        d=fixture(1)
        d['productionRules']=rules()
        d['eqp'][0].update(recipe='OLD',card='OTHER')
        d['lots'][0][2]='A'; d['mps'][0]['platform']='A'
        r=run(d,{'bakeWindowHours':0})
        first=r['cpTasks'][0]
        self.assertEqual(first['recipeSetup'],30*MIN)
        self.assertEqual(first['cardSetup'],5*MIN)
        self.assertEqual(first['start']-first['blockStart'],35*MIN)
        d['productionRules']['recipeChangeMinutes']=12
        first=run(d,{'bakeWindowHours':0})['cpTasks'][0]
        self.assertEqual(first['setup'],17*MIN)

    def test_borrow_uses_recipe_at_start_after_preceding_work(self):
        d=fixture(1)
        d['productionRules']=rules()
        d['eqp'][0].update(recipe='OLD',card='C1')
        d['lots'][0][2]='A'; d['mps'][0]['platform']='A'
        d['flows']['P'][0][2]=1
        d['borrow']=[{'eqp':'M1','recipe':'R','date':'2026-09-01','start':'2026-09-01T02:00:00+08:00','hours':1}]
        result=run(d,{'bakeWindowHours':0})
        self.assertEqual(result['borrows'][0]['recipeAtStart'],'R')
        d['borrow'][0]['recipe']='OLD'
        with self.assertRaisesRegex(TaskConstraintError,'Recipe 不匹配'):
            run(d,{'bakeWindowHours':0})

    def test_last_processing_end_defines_completion_rest_is_information_only(self):
        d=fixture(1); d['productionRules']=rules()
        d['flows']['P']=[['CP1','R',1],['QTIME1','14',14]]
        result=run(d,{'bakeWindowHours':0})
        lot=result['lots'][0]
        self.assertEqual(lot['complete'],lot['cpTasks'][-1]['end'])
        self.assertGreater(lot['routeEnd'],lot['complete'])
        self.assertEqual(lot['restEnd']-lot['complete'],336*HOUR)

    def test_shipping_deadline_is_primary_dispatch_priority(self):
        d=fixture(2);d['productionRules']=rules()
        d['flows']['P']=[['CP1','R',1]]
        for row in d['lots']: row[2]='A'
        d['mps'][0]['platform']='A'
        d['lots'][1][7]='2026-09-01T02:00:00+08:00'
        result=run(d,{'bakeWindowHours':0})
        self.assertEqual(result['cpTasks'][0]['lotId'],d['lots'][1][0])

    def test_next_month_midnight_is_not_counted_in_current_month(self):
        d=fixture(1);d['productionRules']=rules()
        d['flows']['P']=[['CP1','R',1]]
        d['eqp'][0].update(recipe='R',card='C1')
        d['lots'][0][2]='A';d['lots'][0][4]='2026-09-30T23:35:00+08:00'
        d['mps'][0]['platform']='A'
        result=run(d,{'bakeWindowHours':0})
        self.assertEqual(result['lots'][0]['complete'],parse_date('2026-10-01'))
        self.assertEqual(result['metrics']['completedWafers'],0)
        self.assertEqual(result['productStats'][0]['completedWafers'],0)


class ProductionWorkspaceTests(unittest.TestCase):
    def test_known_supply_only_and_external_wip_are_not_rescheduled(self):
        s=state();s['snapshot']['lots'][1]['availableAt']=None
        p=trial(s,{})
        by_id={lot['uid']:lot for lot in p['lots']}
        self.assertEqual(by_id['DEMO-LOT-001']['tasks'],[])
        self.assertIsNone(by_id['DEMO-LOT-001']['complete'])
        self.assertEqual(by_id['DEMO-LOT-003']['tasks'],[])
        self.assertIn('可用时间',by_id['DEMO-LOT-003']['reason'])
        self.assertEqual(by_id['DEMO-LOT-004']['tasks'],[])
        self.assertTrue(all(t.get('blockStart',t['start'])>=parse_date(s['snapshot']['lots'][2]['availableAt']) for t in by_id['DEMO-LOT-002']['tasks']))
        occupied_end=parse_date(s['snapshot']['lots'][1]['current']['end'])
        self.assertTrue(all(t['blockStart']>=occupied_end for l in p['lots'] for t in l['tasks'] if t.get('machine')=='CP-A-01'))

    def test_three_deltas_and_actual_forecast_do_not_double_count(self):
        s=state(); first=trial(s,{})
        self.assertEqual(first['products'][0]['actual'],25)
        self.assertEqual(first['products'][0]['forecast'],75)
        self.assertIsNone(first['products'][0]['forecastDelta'])
        s['active']=first
        second=trial(s,{})
        self.assertEqual(second['products'][0]['forecastDelta'],0)
        self.assertEqual(second['products'][0]['targetDelta'],-50)
        self.assertEqual(second['products'][0]['actualPlanDelta'],0)

    def test_external_completion_forecast_counts_once(self):
        s=state(); row=s['snapshot']['lots'][1]
        row['externalComplete']=row['current']['end']
        plan=trial(s,{})
        product=next(p for p in plan['products'] if p['ppn']==row['ppn'])
        self.assertEqual(product['forecast'],75)
        self.assertEqual(product['externalUnknownWafers'],0)

    def test_freezing_preserves_whole_routes_and_shared_bake_batches(self):
        s=state();first=trial(s,{});s['active']=first;s['rules']['freezeHours']=1
        second=trial(s,{})
        kept=[l for l in second['lots'] if l.get('frozen')]
        self.assertTrue(kept)
        original={l['uid']:l for l in first['lots']}
        for lot in kept:
            self.assertEqual(lot['tasks'],original[lot['uid']]['tasks'])
        frozen_tasks=[t for l in kept for t in l['tasks'] if t.get('machine')]
        for lot in second['lots']:
            if lot.get('frozen'): continue
            for task in lot['tasks']:
                for fixed in frozen_tasks:
                    if task.get('machine') and task['machine']==fixed['machine']:
                        self.assertFalse(task['blockStart']<fixed['end'] and fixed['blockStart']<task['end'])

    def test_frozen_input_change_and_unknown_identity_fail_explicitly(self):
        s=state();s['active']=trial(s,{});s['rules']['freezeHours']=1
        s['snapshot']['identityStable']=False
        with self.assertRaisesRegex(ValueError,'稳定 Lot'):
            trial(s,{})
        s['snapshot']['identityStable']=True
        d,r,data,source,held,frozen,scene=prepare(s,{})
        uid=frozen[0]['uid']
        next(l for l in s['snapshot']['lots'] if l['uid']==uid)['shipDue']='2026-12-31'
        with self.assertRaisesRegex(ValueError,'输入已变化'):
            trial(s,{})

    def test_adoption_is_explicit_persistent_and_rejects_stale_or_expansion(self):
        with tempfile.TemporaryDirectory() as tmp:
            config=Path(tmp)/'config.json';config.write_text('{"untouched":true}')
            s=update(config,'demo',{});p=trial(s,{})
            self.assertIsNone(read_state(config)['active'])
            adopted=adopt(config,p,'第一版人工选用')
            self.assertEqual(read_state(config)['active']['id'],p['id'])
            self.assertEqual(json.loads(config.read_text()),{'untouched':True})
            new=trial(adopted,{})
            update(config,'rules',dict(s['rules'],recipeChangeMinutes=20))
            with self.assertRaisesRegex(ValueError,'已变化'):
                adopt(config,new,'旧候选')
            fresh=read_state(config)
            expansion=trial(fresh,{'scenario':'expansion','extraMachines':{'CP-A':1}})
            self.assertTrue(any(e['id'].startswith('SIM-') for e in expansion['equipment']))
            with self.assertRaisesRegex(ValueError,'扩产模拟'):
                adopt(config,expansion,'不能选用')

    def test_unknown_identity_never_silently_merges_duplicate_lot_ids(self):
        d=demo_snapshot();d['identityStable']=False
        for row in d['lots']: row.pop('uid')
        d['lots'][1]['lotId']=d['lots'][0]['lotId']
        valid=validate_snapshot(d)
        self.assertEqual(len({l['uid'] for l in valid['lots']}),len(valid['lots']))
        valid['lots'][1]['uid']=valid['lots'][0]['uid']
        with self.assertRaisesRegex(ValueError,'uid 重复'):
            validate_snapshot(valid)

    def test_invalid_dates_quantities_resources_and_borrow_recipe_are_rejected(self):
        mutations=[lambda d:d['lots'][0].update(wafer=-1),
                   lambda d:d['lots'][1]['current'].update(end='invalid'),
                   lambda d:d['borrow'][0].update(recipe=''),
                   lambda d:d['externalBlocks'].append({'resource':'unknown','start':d['asOf'],'end':'2026-12-01'}),
                   lambda d:d['pm'][0].update(hours=float('nan'))]
        for mutate in mutations:
            d=demo_snapshot();mutate(d)
            with self.assertRaises((ValueError,TypeError)):
                validate_snapshot(d)

    def test_demo_cannot_overwrite_mes_and_daily_check_does_not_schedule(self):
        with tempfile.TemporaryDirectory() as tmp:
            config=Path(tmp)/'config.json'
            s=update(config,'demo',{})
            s['snapshot']['source']='mes';update(config,'snapshot',s['snapshot'])
            with self.assertRaisesRegex(ValueError,'不会覆盖'):
                update(config,'demo',{})
            s=read_state(config);s['rules']['checkTime']='00:00';write_state(config,s)
            daily_check(config)
            after=read_state(config)
            self.assertIsNone(after['active'])
            self.assertTrue(after['lastCheck']['changed'])

    def test_cancel_stops_worker_without_selecting_a_plan(self):
        jobs=TrialJobs()
        try:
            job=jobs.start(state(),{})
            jobs.cancel(job['id'])
            deadline=time.monotonic()+8
            while jobs.read()['status']=='running' and time.monotonic()<deadline:
                time.sleep(0.05)
            self.assertEqual(jobs.read()['status'],'cancelled')
        finally:
            jobs.close()

    def test_http_trial_archive_and_stale_adoption(self):
        from server import Handler
        with tempfile.TemporaryDirectory() as tmp:
            config=Path(tmp)/'config.json';config.write_text('{}')
            server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
            server.config_path=str(config)
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            base='http://127.0.0.1:%d/api/production' % server.server_port
            def request(path='',body=None):
                req=urllib.request.Request(base+path,data=json.dumps(body).encode() if body is not None else None,headers={'Content-Type':'application/json'})
                with urllib.request.urlopen(req,timeout=10) as response:return json.load(response)
            try:
                self.assertIsNone(request()['snapshot'])
                self.assertIsNone(request('/job')['job'])
                request('/demo',{})
                job=request('/trial',{})['job']
                deadline=time.monotonic()+8
                while job['status']=='running' and time.monotonic()<deadline:
                    time.sleep(.05);job=request('/job')['job']
                self.assertEqual(job['status'],'completed')
                self.assertIsNone(request()['active'])
                saved=request('/archive',{'id':job['id']})
                self.assertEqual(len(saved['history']),1)
                plan=request('/plans/'+job['plan']['id'])
                self.assertEqual(plan['id'],job['plan']['id'])
                request('/adopt',{'id':job['id'],'reason':'HTTP 验证选用'})
                self.assertEqual(len(request()['history']),1)
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    request('/adopt',{'id':job['id'],'reason':'重复选用'})
                self.assertEqual(caught.exception.code,400)
                self.assertIn('已变化',json.load(caught.exception)['error'])
                self.assertEqual(config.read_text(),'{}')
            finally:
                if hasattr(server,'trial_jobs'):server.trial_jobs.close()
                server.shutdown();server.server_close();thread.join()

    def test_oven_external_block_reserves_every_actual_chamber(self):
        s=state();d=s['snapshot'];oven=next(e for e in d['equipment'] if 'OVEN' in e['type'])
        end=parse_date(d['asOf'])+48*HOUR
        from scheduler import iso_date
        d['externalBlocks']=[dict(resource=oven['id'],start=d['asOf'],end=iso_date(end))]
        plan=trial(s,{})
        tasks=[t for lot in plan['lots'] for t in lot['tasks'] if t.get('chamber')]
        self.assertTrue(tasks)
        self.assertTrue(all(t.get('blockStart',t['start'])>=end for t in tasks))

    def test_invalid_chamber_missing_oven_and_rest_route_are_rejected(self):
        d=demo_snapshot();oven=next(e for e in d['equipment'] if 'OVEN' in e['type'])
        d['lots'][1]['current'].update(eqp=oven['id'],chamber=oven['id']+'-C99')
        with self.assertRaisesRegex(ValueError,'腔体'):
            validate_snapshot(d)
        d=demo_snapshot();d['equipment']=[e for e in d['equipment'] if 'OVEN' not in e['type']]
        with self.assertRaisesRegex(ValueError,'实际 OVEN'):
            validate_snapshot(d)
        d=demo_snapshot();next(iter(d['flows'].values())).append(['QTIME1','14',14])
        with self.assertRaisesRegex(ValueError,'静置'):
            validate_snapshot(d)

    def test_frozen_setup_is_revalidated_against_machine_state(self):
        s=state();s['active']=trial(s,{});s['rules']['freezeHours']=1
        _,_,_,_,_,frozen,_=prepare(s,{})
        task=next(t for lot in frozen for t in lot['tasks'] if t.get('machine') and not t['setup'])
        machine=next(e for e in s['snapshot']['equipment'] if e['id']==task['machine'])
        machine.update(recipe='R2',card='CARD2')
        with self.assertRaisesRegex(TaskConstraintError,'切换时间不足'):
            trial(s,{})

    def test_duplicate_cp_wip_intervals_do_not_silently_merge(self):
        s=state();other=copy.deepcopy(s['snapshot']['lots'][1]);other['uid']='OTHER';other['lotId']='OTHER'
        s['snapshot']['lots'].append(other)
        with self.assertRaisesRegex(ValueError,'在制时间冲突'):
            trial(s,{})

    def test_identifiers_ignore_case_and_platform_separator_spaces(self):
        d=demo_snapshot()
        for e in d['equipment']:
            for k in ('id','type','recipe','card','proberId'):
                if k in e:e[k]=e[k].lower()
        d['lots'][5]['platform']=' cp-a ; cp-b; '
        d['borrow'][0]['eqp']=d['borrow'][0]['eqp'].lower()
        valid=validate_snapshot(d)
        self.assertEqual(valid['lots'][5]['platform'],'CP-A;CP-B')
        trial(dict(state(),snapshot=valid),{})

    def test_expansion_record_persists_without_adoption(self):
        with tempfile.TemporaryDirectory() as tmp:
            config=Path(tmp)/'config.json';s=update(config,'demo',{})
            p=trial(s,{'scenario':'expansion','extraMachines':{'CP-A':1}})
            after=archive(config,p)
            self.assertIsNone(after['active'])
            self.assertEqual(read_plan(config,p['id']),p)
            self.assertEqual(len(archive(config,p)['history']),1)
            with self.assertRaisesRegex(ValueError,'编号'):
                read_plan(config,'../state')

    def test_hard_time_limit_stops_large_trial_and_never_adopts(self):
        s=state();d=s['snapshot'];template=d['lots'][5]
        d['lots']=[dict(template,uid='STRESS-%05d'%i,lotId='STRESS-%05d'%i) for i in range(3000)]
        s['rules']['maxRuntimeSeconds']=1
        jobs=TrialJobs()
        try:
            jobs.start(s,{})
            deadline=time.monotonic()+8
            while jobs.read()['status']=='running' and time.monotonic()<deadline:
                time.sleep(0.05)
            self.assertEqual(jobs.read()['status'],'timed_out')
            self.assertLess(jobs.read()['elapsedSeconds'],5)
            self.assertIsNone(s['active'])
        finally:
            jobs.close()


if __name__=='__main__':
    unittest.main()
