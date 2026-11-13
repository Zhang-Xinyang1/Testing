"""End-to-end contracts shared by Excel, MES, selected plans and all views."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from server_core import build_payload, load_config, validate_schedule
from production_excel import excel_snapshot
from production_chart import chart_projection
from production import read_state, update, trial, adopt
from production_demo import demo_event
from server import Handler
from build_data import read_workbook


class UnifiedProductionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = build_payload()
        cls.snapshot = excel_snapshot(cls.data)

    def workspace(self):
        return dict(read_state(ROOT/'.artifacts/nonexistent-test/config.json'),
                    snapshot=copy.deepcopy(self.snapshot), active=None)

    def test_small_excel_has_all_business_facts_and_independent_demand(self):
        d = self.snapshot
        self.assertEqual(d['inputIssues'], [])
        self.assertEqual(len(d['demands']), 3)
        self.assertEqual(len(d['lots']), 12)
        self.assertEqual(sum(x['wafer'] for x in d['lots']), 295)
        self.assertEqual(sum(x['wafer'] for x in d['demands']), 375)
        self.assertTrue(d['identityStable'])
        self.assertEqual(d['asOf'], '2026-11-13 08:00:00')
        self.assertEqual({x['state'] for x in d['lots']}, {'DONE','RUNNING','FUTURE','HOLD','READY'})
        self.assertEqual(len({x['lotId'] for x in d['lots']}), 11)
        self.assertEqual(len(d['pm']), 4)
        self.assertEqual(d['borrow'][0]['recipe'], 'R1')

    def test_actual_stock_may_be_above_or_below_demand_but_synthetic_split_must_match(self):
        d = copy.deepcopy(self.data)
        for target in (0, 25, 1000):
            d['mps'][0]['mps'] = target
            validate_schedule(d)
        d['meta']['lotSizing'] = 'generated'
        with self.assertRaisesRegex(ValueError,'合计'):
            validate_schedule(d)

    def test_missing_dates_are_visible_and_never_invented_from_legacy_defaults(self):
        d = copy.deepcopy(self.data)
        d['productionFacts'][5]['shipDue'] = ''
        d['productionFacts'][6].update(state='', availableAt='')
        snapshot = excel_snapshot(d)
        self.assertIsNone(snapshot['lots'][5]['shipDue'])
        self.assertIsNone(snapshot['lots'][6]['availableAt'])
        self.assertEqual(snapshot['inputIssues'], [])
        s = self.workspace();s['snapshot'] = snapshot
        p = trial(s,{})
        lots={l['uid']:l for l in p['lots']}
        self.assertFalse(lots[snapshot['lots'][5]['uid']]['tasks'])
        self.assertFalse(lots[snapshot['lots'][6]['uid']]['tasks'])
        self.assertIn('内部出货日',lots[snapshot['lots'][5]['uid']]['reason'])

    def test_mps_only_input_does_not_turn_demand_into_stock(self):
        c=load_config();c['dataSource']['sheetMap'].pop('lots')
        snapshot=excel_snapshot(build_payload(config=c))
        self.assertTrue(all(l['plannedLot'] and not l['availableAt'] for l in snapshot['lots']))
        s=self.workspace();s['snapshot']=snapshot
        self.assertEqual(sum(p['forecast'] for p in trial(s,{})['products']),0)

    def test_blank_configured_lot_sheet_is_not_replaced_with_generated_stock(self):
        sheets=read_workbook(ROOT/load_config()['dataSource']['path'])
        sheets=[(n,rows[:1] if n=='Lot明细' else rows) for n,rows in sheets]
        with patch('server_core.read_workbook',return_value=sheets), self.assertRaisesRegex(ValueError,'Lot'):
            build_payload()

    def test_case_insensitive_headers_and_identifiers_reach_unified_engine(self):
        sheets=read_workbook(ROOT/load_config()['dataSource']['path'])
        sheets=[(n.lower(),[[v.lower() if isinstance(v,str) else v for v in row] for row in rows]) for n,rows in sheets]
        with patch('server_core.read_workbook',return_value=sheets):
            snapshot=excel_snapshot(build_payload())
        self.assertEqual(snapshot['inputIssues'],[])
        self.assertEqual(snapshot['lots'][1]['current']['eqp'],'CP-A-01')
        self.assertTrue(snapshot['identityStable'])
        s=self.workspace();s['snapshot']=snapshot
        self.assertEqual(sum(p['forecast'] for p in trial(s,{})['products']),220)

    def test_missing_borrow_recipe_and_current_occupancy_block_trial(self):
        for table,key in [('borrow','recipe'),('productionFacts','currentStep')]:
            d=copy.deepcopy(self.data)
            d[table][0 if table=='borrow' else 1][key]=''
            snapshot=excel_snapshot(d)
            self.assertTrue(snapshot['inputIssues'])
            s=self.workspace();s['snapshot']=snapshot
            with self.assertRaisesRegex(ValueError,'待补充'):trial(s,{})

    def test_chart_preserves_uid_batches_setup_wip_and_combined_pm(self):
        p=trial(self.workspace(),{});c=chart_projection(p)
        self.assertEqual(c['planId'],p['id'])
        self.assertEqual(len(c['cpTasks']),16)
        self.assertEqual(sum(len(b['lotKeys']) for b in c['batches']),8)
        self.assertEqual(len(c['pmRecords']),3)
        self.assertEqual(sum(len(pm['sourcePlans']) for pm in c['pmRecords']),4)
        self.assertEqual(sum(len(r['wip']) for r in c['machines']),1)
        self.assertFalse(next(l for l in p['lots'] if l['uid']==self.snapshot['lots'][1]['uid'])['tasks'])
        duplicate=[l for l in p['lots'] if l['lotId']==self.snapshot['lots'][5]['lotId']]
        self.assertEqual(len({l['uid'] for l in duplicate}),2)
        self.assertTrue(any(t['setup']==35*60000 for t in c['cpTasks']))
        cp={(t['lotKey'],t['step'],t['start']) for t in c['cpTasks']}
        self.assertEqual(cp,{(l['uid'],t['step'],t['start']) for l in p['lots'] for t in l['tasks'] if t.get('machine')})

    def test_frozen_tasks_remain_in_chart_without_recomputing_or_duplicating_them(self):
        s=self.workspace();s['active']=trial(s,{});s['rules']['freezeHours']=1
        p=trial(s,{});before=copy.deepcopy(p);c=chart_projection(p)
        self.assertGreater(p['frozenLots'],0)
        self.assertEqual(p,before)
        self.assertEqual(len(c['cpTasks']),sum(bool(t.get('machine')) for l in p['lots'] for t in l['tasks']))

    def test_sources_restore_selected_plans_and_same_excel_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg=Path(tmp)/'config.json'
            s=update(cfg,'excel',self.snapshot);p=trial(s,{})
            chosen=adopt(cfg,p,'Excel 测试选用')
            self.assertEqual(update(cfg,'excel',self.snapshot),chosen)
            update(cfg,'source',{'source':'demo'})
            demo=update(cfg,'demo',{'case':'basic'});dp=trial(demo,{})
            adopt(cfg,dp,'演示选用')
            restored=update(cfg,'source',{'source':'excel'})
            self.assertEqual(restored['active']['id'],p['id'])
            self.assertEqual(update(cfg,'source',{'source':'demo'})['active']['id'],dp['id'])
            mes=copy.deepcopy(demo['snapshot']);mes['source']='mes'
            self.assertIsNone(update(cfg,'snapshot',mes)['active'])
            self.assertEqual(update(cfg,'source',{'source':'excel'})['active']['id'],p['id'])

    def test_arrival_change_updates_all_results_only_after_adoption(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg=Path(tmp)/'config.json';s=update(cfg,'excel',self.snapshot)
            p=trial(s,{});adopt(cfg,p,'基线')
            changed=copy.deepcopy(self.snapshot);changed['source']='demo'
            changed,_=demo_event(changed,'arrival_known');changed['source']='excel'
            s=update(cfg,'excel',changed)
            self.assertEqual(s['active']['id'],p['id'])
            candidate=trial(s,{})
            self.assertEqual(sum(p['forecast'] for p in candidate['products']),245)
            self.assertEqual(sum(p['forecastDelta'] for p in candidate['products']),25)
            self.assertEqual(read_state(cfg)['active']['id'],p['id'])
            selected=adopt(cfg,candidate,'来料已明确')
            self.assertEqual(chart_projection(selected['active'])['planId'],candidate['id'])
            self.assertEqual(len(chart_projection(selected['active'])['cpTasks']),18)

    def test_api_initial_read_and_saved_config_share_snapshot_without_starting_jobs(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg=Path(tmp)/'config.json';cfg.write_text(json.dumps(load_config(),ensure_ascii=False),encoding='utf-8')
            server=ThreadingHTTPServer(('127.0.0.1',0),Handler);server.config_path=str(cfg)
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            base='http://127.0.0.1:'+str(server.server_port)
            def request(path,body=None):
                req=urllib.request.Request(base+path,None if body is None else json.dumps(body).encode(),{'Content-Type':'application/json'})
                with urllib.request.urlopen(req) as r:return json.load(r)
            try:
                s=request('/api/production')
                self.assertEqual(s['snapshot']['source'],'excel');self.assertIsNone(s['active'])
                self.assertIsNone(request('/api/production/job')['job'])
                p=trial(read_state(cfg),{});adopt(cfg,p,'接口测试')
                state=request('/api/production')
                self.assertEqual(state['active']['id'],state['chart']['planId'])
                result=request('/api/config',{'scheduling':load_config()['scheduling']})
                self.assertEqual(result['production']['chart']['planId'],p['id'])
                self.assertFalse(result['production']['changePending'])
                self.assertIsNone(request('/api/production/job')['job'])
            finally:
                server.shutdown();server.server_close();thread.join()


if __name__=='__main__':unittest.main()
