"""Production counterexamples, independent checking and transactional failures."""
import copy
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from production import update, read_state, write_state, validate_snapshot, trial, adopt, read_plan, context_hash
from production_rules import rules
from production_time import now_ms
from production_validation import validate_plan
from production_ledger import output_ledger
from production_facts import digest
from scheduler import parse_date, HOUR
from optimizer import objective
import production_store as store


def base():
    return dict(schemaVersion=1,source='mes',snapshotId='MES-1',identityStable=True,
        month='2026-11',asOf='2026-11-23T08:00:00+08:00',
        demands=[dict(ppn='P',wafer=25)],
        equipment=[dict(id='M1',type='T5830',proberId='PR1',status='RUN',recipe='R1',card='C1')],
        flows={'P':[['CP1','R1',1]]},recipes=[dict(recipe='R1',cards=['C1'])],
        lots=[dict(uid='U1',lotId='L1',ppn='P',platform='T5830',wafer=25,state='READY',
                   availableAt='2026-11-23T08:00:00+08:00',shipDue='2026-11-30T20:00:00+08:00')],
        pm=[],borrow=[],externalBlocks=[],constraints=[])


def workspace(data):
    return dict(revision=1,snapshot=validate_snapshot(data),rules=rules({'maxRuntimeSeconds':10}),
                active=None,history=[],events=[],lastCheck=None,changePending=True)


def shadow(state):return trial(state,{'mode':'shadow'})


def running(data):
    data['lots'][0].update(state='RUNNING',current=dict(eqp='M1',step='CP1',recipe='R1',card='C1',
        start='2026-11-23T07:00:00+08:00',end='2026-11-23T10:00:00+08:00'))
    return data


class FactTests(unittest.TestCase):
    def test_valid_identical_excel_receipt_clears_previous_receipt_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg=Path(tmp)/'config.json';d=base();d['source']='excel'
            update(cfg,'excel',d)
            with self.assertRaises(ValueError):update(cfg,'snapshot',{})
            self.assertTrue(read_state(cfg)['receiptFailure'])
            s=update(cfg,'excel',d)
            self.assertNotIn('receiptFailure',s)
            self.assertNotIn('receiptFailure',read_state(cfg))

    def test_missing_running_is_blocked_across_repeated_snapshots_until_actual_completion(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg=Path(tmp)/'config.json';d=running(base())
            d['lots'].append(dict(base()['lots'][0],uid='U2',lotId='L2'))
            s=update(cfg,'snapshot',d);p=shadow(s);adopt(cfg,p,'baseline')
            for number in (2,3):
                missing=copy.deepcopy(d);missing['snapshotId']='MES-'+str(number);missing['lots']=missing['lots'][1:]
                s=update(cfg,'snapshot',missing)
                with self.assertRaisesRegex(ValueError,'缺失'):shadow(s)
                self.assertEqual(read_state(cfg)['active']['id'],p['id'])
            released=copy.deepcopy(d);released.update(snapshotId='MES-4',asOf='2026-11-23T10:05:00+08:00')
            released['lots'][0].update(state='DONE',actualComplete='2026-11-23T10:04:00+08:00')
            s=update(cfg,'snapshot',released)
            self.assertFalse(s['factIssues']);self.assertEqual(shadow(s)['products'][0]['forecast'],50)

    def test_overdue_running_and_unknown_route_preserve_fact_and_block_new_plan(self):
        for mutate in (lambda d:d.update(asOf='2026-11-23T10:05:00+08:00'),
                       lambda d:d['lots'][0]['current'].update(step='CP99')):
            d=running(base());mutate(d)
            valid=validate_snapshot(d)
            self.assertEqual(valid['lots'][0]['current']['eqp'],'M1')
            self.assertTrue(valid['factIssues'])
            with self.assertRaisesRegex(ValueError,'事实待核对'):shadow(workspace(d))

    def test_first_unresolved_wip_cannot_be_erased_by_followup_empty_snapshots(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg=Path(tmp)/'config.json';d=running(base());d['lots'][0]['current']['step']='CP99'
            update(cfg,'snapshot',d)
            for n in range(2,5):
                d=base();d.update(snapshotId='MES-'+str(n),lots=[])
                s=update(cfg,'snapshot',d)
                with self.assertRaisesRegex(ValueError,'缺失'):shadow(s)

    def test_done_cannot_roll_back_or_change_quantity(self):
        for change in (dict(state='READY'),dict(wafer=24)):
            with tempfile.TemporaryDirectory() as tmp:
                cfg=Path(tmp)/'config.json';d=base();d['lots'][0].update(state='DONE',actualComplete='2026-11-23T07:00:00+08:00')
                update(cfg,'snapshot',d);d['snapshotId']='MES-2';d['lots'][0].update(change)
                s=update(cfg,'snapshot',d)
                self.assertTrue(s['factIssues'])
                with self.assertRaisesRegex(ValueError,'事实待核对'):shadow(s)

    def test_snapshot_id_is_immutable_including_older_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg=Path(tmp)/'config.json';d=base();first=update(cfg,'snapshot',d)
            self.assertEqual(update(cfg,'snapshot',d),first)
            d['demands'][0]['wafer']=50
            with self.assertRaisesRegex(ValueError,'快照 ID'):update(cfg,'snapshot',d)
            d['snapshotId']='MES-2';update(cfg,'snapshot',d)
            d['snapshotId']='MES-1'
            with self.assertRaisesRegex(ValueError,'快照 ID'):update(cfg,'snapshot',d)
            self.assertEqual(read_state(cfg)['snapshot']['snapshotId'],'MES-2')

    def test_canonical_receipt_ignores_record_order_timezone_and_numeric_spelling(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg=Path(tmp)/'config.json';d=base();d['equipment'].append(dict(d['equipment'][0],id='M2',proberId='PR2'))
            first=update(cfg,'snapshot',d);d['equipment'].reverse();d['asOf']='2026-11-23T00:00:00Z';d['flows']['P'][0][2]=1.0
            self.assertEqual(update(cfg,'snapshot',d),first)

    def test_incomplete_delta_or_narrow_scope_cannot_publish_or_keep_candidate_eligible(self):
        for meta in ({'complete':False},{'mode':'delta'},{'scope':'running-only'}):
            with tempfile.TemporaryDirectory() as tmp:
                cfg=Path(tmp)/'config.json';d=base();s=update(cfg,'snapshot',d);p=shadow(s)
                bad=copy.deepcopy(d);bad.update(snapshotId='BAD',ingestion=meta)
                with self.assertRaises(ValueError):update(cfg,'snapshot',bad)
                self.assertEqual(read_state(cfg)['snapshot'],s['snapshot'])
                with self.assertRaises(ValueError):adopt(cfg,p,'cannot select')
                update(cfg,'snapshot',d)
                self.assertNotIn('receiptFailure',read_state(cfg))

    def test_ids_must_not_enter_as_numbers(self):
        d=base();d['lots'][0]['uid']=1955544086919462912
        with self.assertRaisesRegex(ValueError,'字符串'):validate_snapshot(d)

    def test_running_pm_cannot_disappear_or_be_cancelled_into_free_capacity(self):
        for replace in (None,'CANCELLED'):
            with tempfile.TemporaryDirectory() as tmp:
                cfg=Path(tmp)/'config.json';d=base()
                d['pm']=[dict(id='PMS-1',eqp='M1',state='RUNNING',start='2026-11-23T07:00:00+08:00',expectedEnd='2026-11-23T11:00:00+08:00',early='2026-11-01',due='2026-11-23',over='2026-11-24',hours=2)]
                update(cfg,'snapshot',d);d['snapshotId']='MES-2'
                if replace:d['pm'][0]['state']=replace
                else:d['pm']=[]
                s=update(cfg,'snapshot',d)
                with self.assertRaisesRegex(ValueError,'PM'):shadow(s)

    def test_shared_prober_is_retained_for_review_but_never_dispatched(self):
        d=base();d['equipment'].append(dict(d['equipment'][0],id='M2'))
        s=workspace(d);self.assertEqual(len(s['snapshot']['equipment']),2)
        with self.assertRaisesRegex(ValueError,'Prober'):shadow(s)


class FeasibilityTests(unittest.TestCase):
    def test_flexible_pm_respects_now_and_latest_completion(self):
        d=base();d['pm']=[dict(id='PMS-1',eqp='M1',early='2026-11-01',due='2026-11-23',over='2026-11-24',hours=2)]
        p=shadow(workspace(d));self.assertGreaterEqual(p['pm'][0]['start'],parse_date(d['asOf']))
        self.assertLessEqual(p['pm'][0]['end'],parse_date('2026-11-25'))
        self.assertEqual(p['pm'][0]['sourceIds'],['PMS-1'])
        d['pm'][0].update(due='2026-11-22',over='2026-11-22')
        with self.assertRaisesRegex(ValueError,'PM 窗口'):shadow(workspace(d))

    def test_completed_pm_does_not_stop_future_work_and_running_pm_uses_expected_end(self):
        d=base();pm=dict(id='PMS-1',eqp='M1',early='2026-11-01',due='2026-11-22',over='2026-11-22',hours=2)
        d['pm']=[dict(pm,state='Completed',completeTime='2026-11-22T09:00:00+08:00')]
        self.assertFalse(shadow(workspace(d))['pm'])
        d['pm']=[dict(pm,state='RUNNING',start='2026-11-23T07:00:00+08:00',expectedEnd='2026-11-23T11:00:00+08:00')]
        p=shadow(workspace(d));self.assertGreaterEqual(p['lots'][0]['tasks'][0]['blockStart'],parse_date('2026-11-23T11:00:00+08:00'))

    def test_frozen_machine_type_change_is_rejected_by_independent_checker(self):
        d=base();d['equipment'].append(dict(d['equipment'][0],id='M2',proberId='PR2'))
        s=workspace(d);s['active']=shadow(s);s['rules']['freezeHours']=1
        machine=s['active']['lots'][0]['tasks'][0]['machine']
        next(e for e in s['snapshot']['equipment'] if e['id']==machine)['type']='K8000L'
        with self.assertRaisesRegex(ValueError,'Platform'):shadow(s)

    def test_checker_rejects_tampering_even_without_using_decoder(self):
        s=workspace(base());p=shadow(s)
        mutations=[lambda p:p['lots'][0]['tasks'][0].update(machine='UNKNOWN'),
                   lambda p:p['lots'][0]['tasks'][0].update(end=p['lots'][0]['tasks'][0]['end']+1),
                   lambda p:p['products'][0].update(forecast=999),
                   lambda p:p['products'].append(copy.deepcopy(p['products'][0])),
                   lambda p:p['input']['lots'][0].update(wafer=24),
                   lambda p:p['lots'].append(copy.deepcopy(p['lots'][0])),
                   lambda p:p['equipment'].append(dict(p['equipment'][0],id='M2'))]
        for mutate in mutations:
            q=copy.deepcopy(p);mutate(q)
            with self.assertRaisesRegex(ValueError,'独立排程校验'):validate_plan(s['snapshot'],q,s['rules'])

    def test_waiting_before_borrowing_produces_feasible_on_time_plan(self):
        d=base();d['demands'].append(dict(ppn='Q',wafer=25));d['recipes'].append(dict(recipe='R2',cards=['C2']))
        d['flows']['Q']=[['CP1','R2',1]];d['lots'][0]['shipDue']='2026-11-23T09:00:00+08:00'
        d['lots'].append(dict(d['lots'][0],uid='U2',lotId='L2',ppn='Q',shipDue='2026-11-23T18:00:00+08:00'))
        d['borrow']=[dict(eqp='M1',recipe='R1',start='2026-11-23T10:00:00+08:00',hours=1)]
        p=shadow(workspace(d));self.assertTrue(p['validation']['passed']);self.assertTrue(p['borrows'][0]['recipeMatched'])
        q=next(l for l in p['lots'] if l['ppn']=='Q')
        self.assertGreaterEqual(q['tasks'][0]['blockStart'],parse_date('2026-11-23T11:00:00+08:00'))
        self.assertLessEqual(q['complete'],parse_date(q['shipDue']))

    def test_external_forecast_and_optimization_share_attainment(self):
        d=base();d['asOf']='2026-11-30T23:20:00+08:00';d['demands'].append(dict(ppn='Q',wafer=25))
        d['equipment'].append(dict(d['equipment'][0],id='M2',proberId='PR2'));d['flows']['Q']=[['CP1','R1',1]]
        d['lots'][0].update(availableAt=d['asOf'],shipDue='2026-12-01T02:00:00+08:00')
        d['lots'].append(dict(d['lots'][0],uid='U2',lotId='L2',ppn='Q'))
        d['lots'].append(dict(d['lots'][0],uid='U3',lotId='L3',state='RUNNING',current=dict(eqp='M2',step='CP1',recipe='R1',card='C1',start=d['asOf'],end='2026-11-30T23:59:00+08:00'),externalComplete='2026-11-30T23:59:00+08:00'))
        p=shadow(workspace(d));self.assertEqual({r['ppn']:r['forecast'] for r in p['products']},{'P':25,'Q':25})
        self.assertEqual(len(p['outputLedger']),3)

    def test_actual_and_external_replace_conflicting_plan_predictions_once(self):
        d=running(base());d['lots'][0]['externalComplete']='2026-11-23T12:00:00+08:00'
        ledger=output_ledger(d,[dict(uid='U1',complete=parse_date('2026-12-01'),frozen=True)])
        self.assertEqual(ledger['products'][0]['forecast'],25);self.assertEqual(ledger['rows'][0]['source'],'external')
        d['lots'][0].update(state='DONE',actualComplete='2026-11-23T07:00:00+08:00')
        ledger=output_ledger(d,[dict(uid='U1',complete=parse_date('2026-12-01'))])
        self.assertEqual(ledger['products'][0]['actual'],25);self.assertEqual(ledger['products'][0]['forecast'],25)

    def test_unscheduled_late_lot_cannot_escape_delivery_loss(self):
        d=base();d['lots'][0]['shipDue']='2026-11-23T08:00:00+08:00'
        def result(complete):
            return dict(data=dict(productionSnapshot=d,productionLotUids=['U1'],productionFrozen=[]),
                lots=[dict(key='LOT-000001',complete=complete)],start=parse_date(d['asOf']),machines=[dict(setupMs=0)])
        served=objective(result(parse_date(d['asOf'])+HOUR))
        missing=objective(result(float('inf')))
        self.assertGreater(served,missing);self.assertEqual(served[0],missing[0])

    def test_next_month_delivery_scope_is_the_same_for_served_and_unserved(self):
        d=base();d['lots'][0]['shipDue']='2026-12-01T00:00:00+08:00'
        served=output_ledger(d,[dict(uid='U1',complete=parse_date('2026-12-02'))])
        missing=output_ledger(d,[])
        self.assertEqual(served['lateWafers'],missing['lateWafers'])
        self.assertEqual(missing['unservedDueWafers'],0)


class AdoptionTests(unittest.TestCase):
    def test_equipment_asof_state_supersedes_older_external_recipe(self):
        d=base();d['recipes'].append(dict(recipe='R2',cards=['C2']))
        d['externalBlocks']=[dict(resource='M1',start='2026-11-23T06:00:00+08:00',
            end='2026-11-23T07:00:00+08:00',recipe='R2',card='C2')]
        p=shadow(workspace(d))
        self.assertEqual(p['lots'][0]['tasks'][0]['setup'],0)
        self.assertTrue(p['validation']['passed'])

    def test_pm_window_is_a_calendar_date_not_an_extra_partial_day(self):
        d=base();d['pm']=[dict(id='PM1',eqp='M1',early='2026-11-23T12:00:00+08:00',
            due='2026-11-23T12:00:00+08:00',over='2026-11-23T12:00:00+08:00',hours=1)]
        s=workspace(d)
        self.assertEqual(s['snapshot']['pm'][0]['over'],'2026-11-23')
        p=shadow(s)
        self.assertLessEqual(p['pm'][0]['end'],parse_date('2026-11-24'))

    def test_live_setup_uses_wip_ending_between_snapshot_and_executable_start(self):
        d=running(base());d['recipes'].append(dict(recipe='R2',cards=['C2']))
        d['flows']['P']=[['CP1','R2',1]];d['lots'][0]['current'].update(recipe='R2',card='C2',end='2026-11-23T08:10:00+08:00')
        d['lots'].append(dict(base()['lots'][0],uid='U2',lotId='L2'))
        s=workspace(d)
        with patch('production_time.now_ms',return_value=parse_date(d['asOf'])):p=trial(s,{'mode':'live'})
        task=next(l for l in p['lots'] if l['uid']=='U2')['tasks'][0]
        self.assertEqual(task['setup'],0)
        self.assertTrue(p['validation']['passed'])

    def test_candidate_expires_without_a_snapshot_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg=Path(tmp)/'config.json';d=base();s=update(cfg,'snapshot',d)
            now=parse_date(d['asOf'])
            with patch('production_time.now_ms',return_value=now):p=trial(s,{'mode':'live'})
            with patch('production_time.now_ms',return_value=p['execution']['validUntil']+1):
                with self.assertRaisesRegex(ValueError,'有效期已过'):adopt(cfg,p,'expired')
            self.assertIsNone(read_state(cfg)['active'])

    def test_stale_running_end_blocks_live_even_before_candidate_start(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg=Path(tmp)/'config.json';d=running(base());d['lots'][0]['current']['end']='2026-11-23T08:10:00+08:00'
            s=update(cfg,'snapshot',d)
            with patch('production_time.now_ms',return_value=parse_date(d['asOf'])):p=trial(s,{'mode':'live'})
            with patch('production_time.now_ms',return_value=parse_date('2026-11-23T08:11:00+08:00')):
                with self.assertRaisesRegex(ValueError,'释放待确认'):adopt(cfg,p,'no release')

    def test_two_candidates_on_one_baseline_only_one_wins_and_retry_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg=Path(tmp)/'config.json';s=update(cfg,'snapshot',base());plans=[shadow(s),shadow(s)]
            barrier=threading.Barrier(2);results=[]
            def choose(p):
                barrier.wait()
                try:adopt(cfg,p,'concurrent');results.append('ok')
                except ValueError:results.append('conflict')
            threads=[threading.Thread(target=choose,args=(p,)) for p in plans]
            for t in threads:t.start()
            for t in threads:t.join()
            self.assertCountEqual(results,['ok','conflict'])
            selected=read_state(cfg)['active'];before=read_state(cfg)
            self.assertEqual(adopt(cfg,selected,'retry'),before)

    def test_store_cas_rejects_writers_with_stale_database_revision(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg=Path(tmp)/'config.json';update(cfg,'snapshot',base())
            a,b=read_state(cfg),read_state(cfg);a['events'].append({'reason':'winner'});write_state(cfg,a)
            b['events'].append({'reason':'loser'})
            with self.assertRaisesRegex(ValueError,'其他操作'):write_state(cfg,b)
            self.assertEqual(read_state(cfg)['events'][-1]['reason'],'winner')

    def test_failed_transaction_leaves_neither_new_plan_nor_partial_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg=Path(tmp)/'config.json';s=update(cfg,'snapshot',base());p=shadow(s)
            with store.connection(cfg) as db:
                db.execute("CREATE TRIGGER fail_commit BEFORE INSERT ON sources BEGIN SELECT RAISE(ABORT,'injected disk failure'); END")
            with self.assertRaises(sqlite3.DatabaseError):adopt(cfg,p,'fault')
            self.assertEqual(read_state(cfg),s)
            with self.assertRaisesRegex(ValueError,'不存在'):read_plan(cfg,p['id'])

    def test_legacy_migration_keeps_files_and_reads_selected_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg=Path(tmp)/'config.json';root=Path(tmp)/'production';root.mkdir()
            s=workspace(base());p=shadow(s);s['active']=p
            (root/'state.json').write_text(json.dumps(s),encoding='utf-8')
            (root/(p['id']+'.json')).write_text(json.dumps(p),encoding='utf-8')
            original=(root/'state.json').read_bytes()
            restored=read_state(cfg)
            self.assertEqual(restored['active']['id'],p['id']);self.assertEqual(read_plan(cfg,p['id']),p)
            self.assertEqual((root/'state.json').read_bytes(),original)


if __name__=='__main__':unittest.main()
