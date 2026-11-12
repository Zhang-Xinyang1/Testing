import copy
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from production_demo import demo_catalog, scenario_pack, check_examples, demo_event
from production import update, read_state, trial, adopt, archive, read_plan, validate_snapshot, context_hash
from production_jobs import TrialJobs


class DemoScenarioTests(unittest.TestCase):
    def test_all_builtins_validate_in_short_leap_and_year_end_months(self):
        for date in ('2026-02-01','2028-02-29','2026-12-31'):
            now=datetime.fromisoformat(date).replace(tzinfo=timezone(timedelta(hours=8)))
            for case in demo_catalog()['scenarios']:
                with self.subTest(date=date,case=case['key']):
                    pack=scenario_pack(case['key'],now)
                    data=validate_snapshot(pack['snapshot'])
                    self.assertEqual(len(data['lots']),case['lots'])
                    self.assertEqual(data['source'],'demo')
                    self.assertTrue(data['asOf'].startswith(data['month']))

    def test_twenty_self_checks_include_expected_accepts_and_rejections(self):
        report=check_examples()
        self.assertEqual(len(report['checks']),20)
        self.assertTrue(report['passed'],report)
        self.assertEqual(sum(c['accepted'] for c in report['checks']),4)

    def test_demo_load_resets_case_rules_but_keeps_history_and_never_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            config=Path(tmp)/'config.json';config.write_text('{}')
            state=update(config,'demo',{'case':'basic'})
            plan=trial(state,{})
            adopt(config,plan,'模拟验证')
            result=update(config,'demo',{'case':'freeze'})
            self.assertIsNone(result['active'])
            self.assertEqual(result['rules']['freezeHours'],1)
            self.assertEqual(len(result['history']),1)
            self.assertEqual(config.read_text(),'{}')
            result=update(config,'demo',{'case':'timeout2000'})
            self.assertEqual(result['rules']['maxRuntimeSeconds'],2)

    def test_presets_have_complete_values_are_once_only_and_keep_selected_baseline(self):
        with tempfile.TemporaryDirectory() as tmp:
            config=Path(tmp)/'config.json'
            state=update(config,'demo',{'case':'full'})
            plan=trial(state,{})
            adopt(config,plan,'人工模拟选用')
            for event in demo_catalog()['events']:
                with self.subTest(event=event['key']):
                    before=read_state(config)
                    result=update(config,'demo-event',{'event':event['key']})
                    self.assertEqual(result['active']['id'],plan['id'])
                    self.assertNotEqual(context_hash(before),context_hash(result))
                    saved=(Path(tmp)/'production/state.json').read_bytes()
                    with self.assertRaisesRegex(ValueError,'已应用'):
                        update(config,'demo-event',{'event':event['key']})
                    self.assertEqual((Path(tmp)/'production/state.json').read_bytes(),saved)

    def test_demo_operations_do_not_overwrite_mes(self):
        with tempfile.TemporaryDirectory() as tmp:
            config=Path(tmp)/'config.json'
            d=scenario_pack('full')['snapshot'];d['source']='mes'
            update(config,'snapshot',d)
            original=(Path(tmp)/'production/state.json').read_bytes()
            for action,body in [('demo',{'case':'basic'}),('demo-event',{'event':'release_hold'})]:
                with self.assertRaises(ValueError):update(config,action,body)
                self.assertEqual((Path(tmp)/'production/state.json').read_bytes(),original)

    def test_saving_an_already_adopted_candidate_preserves_audit_reason(self):
        with tempfile.TemporaryDirectory() as tmp:
            config=Path(tmp)/'config.json'
            state=update(config,'demo',{'case':'recipe'})
            plan=trial(state,{})
            adopt(config,plan,'必须保留的选用理由')
            archive(config,plan)
            self.assertEqual(read_plan(config,plan['id'])['adoptReason'],'必须保留的选用理由')

    def test_running_jobs_cannot_be_cleared_as_if_idle(self):
        jobs=TrialJobs();jobs.job={'status':'running'}
        with self.assertRaisesRegex(ValueError,'取消'):jobs.clear()
        jobs.job={'status':'completed'};jobs.clear();self.assertIsNone(jobs.read())

    def test_unsupported_event_has_no_side_effect(self):
        snapshot=scenario_pack('month_end')['snapshot'];before=copy.deepcopy(snapshot)
        with self.assertRaisesRegex(ValueError,'没有符合条件'):demo_event(snapshot,'release_hold')
        self.assertEqual(snapshot,before)

    def test_manual_arrival_cannot_turn_completed_or_running_lots_into_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            config=Path(tmp)/'config.json'
            state=update(config,'demo',{'case':'basic'})
            for lot in state['snapshot']['lots']:
                if lot['state'] not in ('DONE','RUNNING','HOLD'):continue
                with self.assertRaisesRegex(ValueError,'不能改写'):
                    update(config,'event',dict(kind='arrival',uid=lot['uid'],time=state['snapshot']['asOf'],reason='误操作测试'))
            self.assertEqual(read_state(config),state)


if __name__=='__main__':unittest.main()
