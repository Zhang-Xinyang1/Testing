"""WIP evidence can only release confirmed material in the shadow scenario."""
import copy
import json
import tempfile
import threading
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from http.server import ThreadingHTTPServer
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from production import trial, validate_snapshot
from production_demo import scenario_pack
from production_wip_preview import preview_wip
from scheduler import parse_date
from server import Handler


class WipPreviewTests(unittest.TestCase):
    def setUp(self):
        now = datetime.fromisoformat('2026-11-13T08:00:00+08:00')
        pack = scenario_pack('basic', now=now)
        self.snapshot = validate_snapshot(pack['snapshot'])
        self.rules = pack['rules']
        self.anchor = datetime.fromisoformat(self.snapshot['asOf'])
        self.unknown_uid = 'DEMO-LOT-003'

    def record(self, uid=None, step='CP1', kind='CONFIRMED', hours=24):
        return {'uid': uid or self.unknown_uid, 'step': step, 'kind': kind,
                'at': (self.anchor + timedelta(hours=hours)).isoformat()}

    def feed(self, records):
        return {'asOf': self.snapshot['asOf'], 'snapshotId': self.snapshot['snapshotId'],
                'records': records}

    def shadow(self, snapshot):
        state = dict(snapshot=snapshot, rules=self.rules, revision=1, active=None,
                     events=[], history=[], changePending=True)
        return trial(state, {'mode': 'shadow', 'scenario': 'actual'})

    def test_confirmed_future_arrival_changes_shadow_production_without_continuing_wip(self):
        arrival = self.record()
        original = copy.deepcopy(self.snapshot)
        candidate, report = preview_wip(self.snapshot, self.feed([arrival]))
        self.assertTrue(report['accepted'], report['errors'])
        self.assertEqual(self.snapshot, original)
        before, after = self.shadow(original), self.shadow(candidate)
        before_lots = {lot['uid']: lot for lot in before['lots']}
        after_lots = {lot['uid']: lot for lot in after['lots']}
        self.assertFalse(before_lots[self.unknown_uid]['tasks'])
        released = after_lots[self.unknown_uid]
        self.assertEqual(released['state'], 'FUTURE')
        self.assertEqual(parse_date(released['availableAt']), parse_date(arrival['at']))
        self.assertGreaterEqual(released['tasks'][0]['blockStart'], parse_date(arrival['at']))
        self.assertEqual([t['step'] for t in released['tasks']], ['CP1', 'BAKE', 'CP2'])
        self.assertEqual(sum(p['forecast'] for p in before['products']), 220)
        self.assertEqual(sum(p['forecast'] for p in after['products']), 245)
        running = after_lots['DEMO-LOT-001']
        self.assertEqual(running['current'], before_lots[running['uid']]['current'])
        self.assertFalse(running['tasks'])
        self.assertIsNone(running['complete'])
        self.assertTrue(after['validation']['passed'])

    def test_confirmed_availability_at_or_before_asof_is_ready(self):
        for hours in (-2, 0):
            with self.subTest(hours=hours):
                record = self.record(hours=hours)
                candidate, report = preview_wip(self.snapshot, self.feed([record]))
                self.assertTrue(report['accepted'], report['errors'])
                row = next(l for l in candidate['lots'] if l['uid'] == self.unknown_uid)
                self.assertEqual(row['state'], 'READY')
                self.assertEqual(parse_date(row['availableAt']), parse_date(record['at']))

    def test_eta_later_operations_and_existing_lot_facts_are_observations(self):
        records = [
            self.record(kind='ETA'),
            self.record(step='CP2'),
            self.record('DEMO-LOT-000'),  # DONE
            self.record('DEMO-LOT-001'),  # RUNNING
            self.record('DEMO-LOT-002'),  # FUTURE with a known availability
            self.record('DEMO-LOT-004'),  # HOLD
            self.record('DEMO-LOT-005'),  # READY
        ]
        candidate, report = preview_wip(self.snapshot, self.feed(records))
        self.assertTrue(report['accepted'], report['errors'])
        self.assertEqual(candidate['lots'], self.snapshot['lots'])
        self.assertEqual(report['applied'], 0)
        self.assertEqual(report['observed'], len(records))

    def test_bad_row_rejects_whole_feed_and_does_not_mutate_inputs(self):
        good = self.record()
        invalid = [
            dict(good),   # duplicate UID and step
            self.record(uid='MISSING'),
            self.record(step='NO_SUCH_STEP'),
            dict(self.record(uid='DEMO-LOT-002'), wafer=24),
            dict(self.record(uid='DEMO-LOT-002'), kind='OTHER'),
            dict(self.record(uid='DEMO-LOT-002'), at='invalid-date'),
            dict(self.record(uid='DEMO-LOT-002'), at='2026-11-23'),
        ]
        for bad in invalid:
            with self.subTest(bad=bad):
                original = copy.deepcopy(self.snapshot)
                feed = self.feed([good, bad])
                original_feed = copy.deepcopy(feed)
                candidate, report = preview_wip(self.snapshot, feed)
                self.assertFalse(report['accepted'])
                self.assertTrue(report['errors'])
                self.assertEqual(report['applied'], 0)
                if candidate is not None:
                    self.assertEqual(candidate['lots'], original['lots'])
                self.assertEqual(self.snapshot, original)
                self.assertEqual(feed, original_feed)

    def test_feed_is_bound_to_capture_and_cannot_match_a_display_id(self):
        feed = self.feed([self.record()])
        invalid = [dict(feed, snapshotId='another-capture'),
                   dict(feed, asOf=(self.anchor - timedelta(hours=1)).isoformat()),
                   self.feed([self.record(uid='DEMO03-L005')])]  # duplicate display LotID
        for item in invalid:
            with self.subTest(item=item):
                candidate, report = preview_wip(self.snapshot, item)
                self.assertFalse(report['accepted'])
                self.assertTrue(report['errors'])
                if candidate is not None:
                    self.assertEqual(candidate['lots'], self.snapshot['lots'])

    def test_replay_same_evidence_yields_same_candidate(self):
        feed = self.feed([self.record()])
        first, first_report = preview_wip(self.snapshot, feed)
        replay, second_report = preview_wip(self.snapshot, copy.deepcopy(feed))
        self.assertTrue(first_report['accepted'])
        self.assertTrue(second_report['accepted'])
        self.assertEqual(first, replay)
        reapplied, repeat_report = preview_wip(first, feed)
        self.assertTrue(repeat_report['accepted'])
        self.assertEqual(repeat_report['applied'], 0)
        self.assertEqual(reapplied, first)
        self.assertEqual(sum(l['wafer'] for l in first['lots']),
                         sum(l['wafer'] for l in self.snapshot['lots']))

    def test_preview_requires_demo_source_and_stable_identity(self):
        for changes in ({'source': 'mes'}, {'source': 'excel'}, {'identityStable': False}):
            with self.subTest(changes=changes):
                snapshot = dict(self.snapshot, **changes)
                candidate, report = preview_wip(snapshot, self.feed([self.record()]))
                self.assertFalse(report['accepted'])
                self.assertTrue(report['errors'])
                self.assertEqual(candidate, snapshot)

    def test_case_normalization_preserves_identity_and_detects_duplicate_records(self):
        record = self.record()
        variant = dict(record, uid=record['uid'].lower(), step=' ｃｐ１ ', kind='confirmed')
        candidate, report = preview_wip(self.snapshot, self.feed([variant]))
        self.assertTrue(report['accepted'])
        self.assertEqual(report['applied'], 1)
        rejected, duplicate_report = preview_wip(self.snapshot, self.feed([variant, record]))
        self.assertFalse(duplicate_report['accepted'])
        self.assertEqual(rejected, self.snapshot)

    def test_workspace_preview_apply_retry_and_stale_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
            server.config_path = str(Path(tmp) / 'config.json')
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            def post(action, body):
                url = f'http://127.0.0.1:{server.server_port}/api/production/{action}'
                req = urllib.request.Request(url, json.dumps(body).encode(), {'Content-Type':'application/json'})
                with urllib.request.urlopen(req, timeout=10) as response:
                    return json.load(response)
            try:
                base = post('demo', {'case':'basic'})
                snapshot = base['snapshot']
                feed = dict(snapshotId=snapshot['snapshotId'], asOf=snapshot['asOf'], records=[
                    dict(uid=self.unknown_uid, step='CP1', kind='CONFIRMED', at=snapshot['asOf'])])
                preview = post('wip-preview', feed)
                self.assertEqual(preview['report']['applied'], 1)
                changed = copy.deepcopy(feed)
                changed['records'][0]['location'] = '其他位置'
                with self.assertRaises(urllib.error.HTTPError) as err:
                    post('wip-apply', dict(contextHash=preview['contextHash'], feed=changed))
                self.assertEqual(err.exception.code, 409)
                apply = dict(contextHash=preview['contextHash'], feed=feed)
                updated = post('wip-apply', apply)
                self.assertEqual(updated['revision'], base['revision']+1)
                self.assertIsNone(updated['active'])
                self.assertEqual(post('wip-apply', apply)['revision'], updated['revision'])
                self.assertTrue(trial(updated, {'mode':'shadow'})['validation']['passed'])
                with self.assertRaisesRegex(ValueError, '仅可使用影子'):
                    trial(updated, {'mode':'live'})
            finally:
                server.shutdown()
                worker.join()
                server.server_close()


if __name__ == '__main__':
    unittest.main()
