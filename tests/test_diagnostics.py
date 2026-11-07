import copy
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from diagnostics import inspect_run, record_run, history


class DiagnosticsTests(unittest.TestCase):
    def fixture(self):
        return {'input': {'meta': {'start': '2026-09-01T00:00:00+08:00', 'days': 30},
            'eqp': [], 'wip': [], 'mps': [{'ppn': 'P', 'mps': 1}], 'flows': {}},
            'options': {}, 'result': {'cpTasks': [], 'batches': [], 'lots': [],
            'pmRecords': [], 'borrows': [], 'runMs': 10}}

    def test_overlap_and_wip_are_detected(self):
        body = self.fixture()
        body['input']['wip'] = [{'lotId': 'L', 'eqp': ''}]
        task = {'lotId': 'L', 'step': 'CP1', 'machine': 'M', 'card': 'C', 'start': 1, 'end': 10}
        body['result']['cpTasks'] = [task, dict(task, lotId='B')]
        codes = {f['code'] for f in inspect_run(body)[0]}
        self.assertTrue({'R01', 'R06', 'P01'} <= codes)

    def test_history_and_determinism(self):
        with tempfile.TemporaryDirectory() as root:
            cfg = root + '/config.json'
            body = self.fixture()
            first = record_run(cfg, body)
            second = record_run(cfg, body)
            self.assertTrue(second['comparison']['sameInput'])
            self.assertNotIn('S03', [f['code'] for f in second['findings']])
            changed = copy.deepcopy(body)
            changed['result']['lots'] = [{'id': 'L', 'prod': 'P', 'complete': 1}]
            report = record_run(cfg, changed)
            self.assertIn('S03', [f['code'] for f in report['findings']])
            self.assertEqual(len(history(cfg)), 3)
            self.assertNotEqual(first['id'], second['id'])

    def test_failure_is_recorded(self):
        body = self.fixture(); body['error'] = 'No resources'
        self.assertEqual(inspect_run(body)[0][0]['code'], 'S01')
