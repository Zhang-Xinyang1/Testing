import copy
from datetime import datetime
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'backend'),str(ROOT/'tools')]
from production import adopt, prepare, read_state, receive_wip_batch, trial, update
from production_time import milliseconds, live_facts
from production_store import StateConflict, connection
from server import Handler
from wip_example import exchange
from wip_collect import pull


class WipIntakeTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.config=Path(temp.name)/'config.json'
        self.base,self.first,self.second,self.third=[item for _,item in exchange(
            datetime.fromisoformat('2026-11-12T08:00:00+08:00'))]
        update(self.config,'snapshot',self.base)

    def send(self,batch):
        return receive_wip_batch(self.config,batch,publish=True)

    def state(self):
        return read_state(self.config)

    def batch(self, name, events):
        return dict(schemaVersion=1,batchId=name,baseSnapshotId=self.state()['snapshot']['snapshotId'],
                    asOf=events[-1]['recordedAt'],pageCount=1,total=len(events),
                    pages=[dict(batchId=name,page=1,events=events)])

    def test_lifecycle_uses_resources_external_forecast_and_actual_once(self):
        before=self.state()
        self.assertFalse(receive_wip_batch(self.config,self.first)['published'])
        self.assertEqual(self.state(),before)
        self.send(self.first)
        occupied=trial(self.state(),{'mode':'shadow'})
        task=next(l for l in occupied['lots'] if l['uid']=='0002')['tasks'][0]
        self.assertGreaterEqual(task['blockStart'],milliseconds('2026-11-12T08:30+08:00'))
        self.assertEqual(occupied['products'][0]['externalUnknownWafers'],25)
        adopt(self.config,occupied,'基准')
        self.send(self.second)
        state=self.state()
        self.assertEqual(state['active']['id'],occupied['id'])
        s=trial(state,{'mode':'shadow'})
        external=next(l for l in s['lots'] if l['uid']=='0001')
        self.assertEqual(external['state'],'EXTERNAL')
        self.assertEqual(external['tasks'],[])
        self.assertNotIn('current',external)
        self.assertEqual(s['products'][0]['forecast'],50)
        self.assertEqual(s['products'][0]['actual'],0)
        self.assertEqual(next(l for l in s['lots'] if l['uid']=='0002')['tasks'][0]['blockStart'],
                         milliseconds('2026-11-12T08:25+08:00'))
        self.send(self.third)
        finished=trial(self.state(),{'mode':'shadow'})
        self.assertEqual(finished['products'][0]['actual'],25)
        self.assertEqual(finished['products'][0]['forecast'],50)
        saved=self.state()
        self.assertTrue(self.send(self.first)['replay'])
        self.assertEqual(self.state(),saved)

    def test_failed_import_preserves_snapshot_then_same_batch_recovers(self):
        b=copy.deepcopy(self.first);b['pages'].pop()
        with self.assertRaisesRegex(ValueError,'缺页'):self.send(b)
        self.assertEqual(self.state()['snapshot']['snapshotId'],self.base['snapshotId'])
        with self.assertRaisesRegex(ValueError,'接收失败'):prepare(self.state(),{'mode':'shadow'})
        self.send(self.first)
        self.assertNotIn('receiptFailure',self.state())
        b=copy.deepcopy(self.first);b['pages'][0]['events'][0]['uid']='0001'
        with self.assertRaises(StateConflict):self.send(b)

    def test_pages_order_replays_and_revisions(self):
        b=copy.deepcopy(self.first);b['pages'].reverse()
        r=self.send(b)
        self.assertTrue(self.send(self.first)['replay'])
        sample=copy.deepcopy(self.first['pages'][0]['events'][0])
        next_batch=copy.deepcopy(self.second);next_batch.update(baseSnapshotId=r['snapshotId'],pageCount=1,total=2)
        sample2=dict(sample,version=2,at='2026-11-12T10:00:00+08:00')
        next_batch['pages']=[dict(batchId=next_batch['batchId'],page=1,events=[sample,sample2])]
        result=self.send(next_batch)
        self.assertEqual((result['newEvents'],result['replayedEvents']),(1,1))
        self.assertEqual(self.state()['snapshot']['lots'][1]['wipEta']['CP1'],sample2['at'])

    def test_mismatched_release_and_duplicate_event_conflict(self):
        self.send(self.first)
        for field,value in [('operationId','WRONG'),('eqp','CP-02')]:
            b=copy.deepcopy(self.second)
            b['pages'][0]['events'][0][field]=value
            with self.assertRaises(ValueError):self.send(b)
            self.assertEqual(self.state()['snapshot']['lots'][0]['state'],'RUNNING')
        b=copy.deepcopy(self.second)
        b['pages'][0]['events'][0]['eventId']=self.first['pages'][0]['events'][0]['eventId']
        with self.assertRaisesRegex(ValueError,'冲突'):self.send(b)
        self.send(self.second)

    def test_missing_uid_bad_page_version_order_and_unconfirmed_identity(self):
        for alter in (lambda b:b['pages'][0].update(batchId='OTHER'),
                      lambda b:b['pages'][0]['events'][0].update(uid='SAMPLE-L'),
                      lambda b:b['pages'][1].update(page=1),
                      lambda b:b['pages'][1]['events'][0].update(recordedAt='2026-11-12T07:59+08:00')):
            b=copy.deepcopy(self.first);alter(b)
            with self.assertRaises(ValueError):receive_wip_batch(self.config,b)
        draft=dict(self.base,identityStable=False,snapshotId='new')
        update(self.config,'snapshot',draft)
        with self.assertRaisesRegex(ValueError,'稳定 UID'):self.send(self.first)

    def test_intake_cannot_refresh_master_clock_or_release_by_omission(self):
        self.send(self.first);self.send(self.second)
        state=self.state()
        with self.assertRaisesRegex(ValueError,'过期'):live_facts(state['snapshot'],state['rules'],
                    milliseconds('2026-11-12T09:05:00+08:00'))
        erased=copy.deepcopy(state['snapshot']);erased['snapshotId']='lost';erased['lots'].pop(0)
        damaged=update(self.config,'snapshot',erased)
        self.assertTrue(damaged['factIssues'])

    def test_transaction_failure_can_retry_after_restart(self):
        before=self.state()
        with connection(self.config) as db:
            db.execute("CREATE TRIGGER fail_wip BEFORE INSERT ON sources BEGIN SELECT RAISE(ABORT,'fault'); END")
        with self.assertRaises(sqlite3.DatabaseError):self.send(self.first)
        self.assertEqual(self.state(),before)
        with connection(self.config) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM wip_events').fetchone()[0],0)
            db.execute('DROP TRIGGER fail_wip')
        self.assertEqual(self.send(self.first)['newEvents'],3)

    def test_registered_material_waits_for_confirmation_and_hold_release(self):
        at=self.base['asOf']
        def event(kind, **data):
            return dict(kind=kind,eventId=kind,version=1,uid='0003',recordedAt=at,**data)
        self.send(self.batch('register',[event('REGISTER',lotId='SAMPLE-L',ppn='sample',wafer=10,
            platform='t5830',shipDue=self.base['lots'][0]['shipDue']),event('ETA',step='cp1',at=at)]))
        self.assertIsNone(self.state()['snapshot']['lots'][2]['availableAt'])
        self.send(self.batch('hold',[event('AVAILABLE',step='cp1',at=at),event('HOLD',reason='等待确认')]))
        held=trial(self.state(),{'mode':'shadow'})
        self.assertFalse(next(l for l in held['lots'] if l['uid']=='0003')['tasks'])
        self.send(self.batch('unhold',[event('UNHOLD',at=at)]))
        self.assertEqual(self.state()['snapshot']['lots'][2]['state'],'READY')

    def test_extend_requires_instance_and_keeps_assembly_until_release(self):
        self.send(self.first)
        late='2026-11-12T08:45:00+08:00'
        update_time='2026-11-12T08:10:00+08:00'
        event=dict(kind='EXTEND',eventId='MORE',version=1,uid='0001',recordedAt=update_time,
                   operationId='OP1',expectedEnd=late)
        broken=dict(event,operationId='OLD')
        with self.assertRaisesRegex(ValueError,'operationId'):self.send(self.batch('extend',[broken]))
        self.send(self.batch('extend',[event]))
        projected=trial(self.state(),{'mode':'shadow'})
        self.assertEqual(next(l for l in projected['lots'] if l['uid']=='0002')['tasks'][0]['blockStart'],milliseconds(late))

    def test_prober_maintenance_blocks_new_cp_start(self):
        master=copy.deepcopy(self.base)
        at=master['asOf']
        master.update(snapshotId='MI',pm=[dict(id='W-01',state='RUNNING',eqp='PR-01',targetKind='PROBER',
            start=at,expectedEnd='2026-11-12T10:00:00+08:00',hours=2,early=at,due=at,over=at)])
        update(self.config,'snapshot',master)
        with self.assertRaisesRegex(ValueError,'执行 PM'):self.send(self.batch('pm',[self.first['pages'][2]['events'][0]]))
        self.assertEqual(self.state()['snapshot']['lots'][0]['state'],'FUTURE')

    def test_shared_bake_requires_same_batch_and_tracks_process_end_before_release(self):
        master=copy.deepcopy(self.base)
        master['snapshotId']='BAKE-BASE'
        master['equipment'].append(dict(id='OVEN1',type='OVEN',status='RUN'))
        master['flows']['SAMPLE'].append(['BAKE','210C24H',24])
        update(self.config,'snapshot',master)
        at=master['asOf']
        end='2026-11-13T08:10:00+08:00'
        finish='2026-11-13T08:00:00+08:00'
        def start(uid,batch):
            return dict(kind='START',eventId='START'+uid,version=1,uid=uid,recordedAt=at,
                        current=dict(operationId='OP'+uid,eqp='OVEN1',step='BAKE',recipe='210C24H',
                            chamber='OVEN1-C1',batchId=batch,start=at,processEnd=finish,end=end))
        wrong=self.batch('bake',[start('0001','A'),start('0002','B')])
        with self.assertRaisesRegex(ValueError,'腔体占用冲突'):self.send(wrong)
        self.send(self.batch('bake',[start('0001','A'),start('0002','A')]))
        events=[]
        for uid in ('0001','0002'):
            events.append(dict(kind='RELEASE',eventId='RELEASE'+uid,version=1,uid=uid,recordedAt=end,
                operationId='OP'+uid,eqp='OVEN1',step='BAKE',chamber='OVEN1-C1',processEnd=finish,releasedAt=end))
            events.append(dict(kind='COMPLETE',eventId='DONE'+uid,version=1,uid=uid,recordedAt=end,at=finish))
        self.send(self.batch('unload',events))
        finished=trial(self.state(),{'mode':'shadow'})
        self.assertEqual(finished['products'][0]['actual'],50)
        self.assertEqual(finished['lots'][0]['handoff']['releasedAt'],end)

    def test_collect_reads_one_version_and_aborts_if_pages_change(self):
        fixture=self.first
        queries=[]
        class Source(BaseHTTPRequestHandler):
            inconsistent=False
            def do_GET(handler):
                query=parse_qs(urlsplit(handler.path).query)
                queries.append(query)
                page=int(query['page'][0])
                data={k:v for k,v in fixture.items() if k!='pages'}
                data.update(fixture['pages'][page-1])
                if Source.inconsistent and page==2:data['asOf']='different'
                handler.send_response(200)
                handler.end_headers()
                handler.wfile.write(json.dumps(data).encode())
            def log_message(handler, *args):
                pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Source)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        url=f'http://127.0.0.1:{server.server_port}/wip'
        try:
            self.assertEqual(pull(url),fixture)
            self.assertNotIn('batchId',queries[0])
            self.assertEqual(queries[1]['batchId'],[fixture['batchId']])
            Source.inconsistent=True
            with self.assertRaisesRegex(ValueError,'分页不一致'):pull(url)
            with self.assertRaisesRegex(ValueError,'HTTPS'):pull(url,token='test')
        finally:
            server.shutdown();thread.join();server.server_close()

    def test_packaged_samples_over_http(self):
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler);server.config_path=str(self.config)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            for file in sorted((ROOT/'examples/wip').glob('*.json')):
                data=json.loads(file.read_text(encoding='utf-8'))
                endpoint='snapshot' if file.name.startswith('00') else 'wip-batch-import'
                # Other tests use their own independent baseline and clock.
                if endpoint=='snapshot':
                    server.config_path=str(self.config.parent/'sample'/'config.json')
                req=urllib.request.Request(f'http://127.0.0.1:{server.server_port}/api/production/{endpoint}',
                    json.dumps(data).encode(),{'Content-Type':'application/json'})
                with urllib.request.urlopen(req,timeout=5) as response:
                    received=json.load(response)
                    if endpoint!='snapshot':self.assertTrue(received['published'])
            self.assertEqual(read_state(server.config_path)['snapshot']['lots'][0]['state'],'DONE')
            with urllib.request.urlopen(f'http://127.0.0.1:{server.server_port}/api/production/wip-batches/'+data['batchId']) as response:
                entry=json.load(response)
                self.assertEqual(entry['batch'],data)
                self.assertEqual(entry['receipt']['rows'][0]['kind'],'COMPLETE')
        finally:
            server.shutdown();thread.join();server.server_close()
