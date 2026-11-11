import copy
import json
from pathlib import Path
import tempfile
import unittest
import threading
import urllib.request
import urllib.error
from http.server import ThreadingHTTPServer
from unittest.mock import patch

from test_scheduler import fixture
from server import Handler
from scheduler import run, compact_result, parse_date, MIN
from task_overrides import lot_signature, TaskConstraintError
from input_errors import InputError
from schedule_history import save_schedule, list_schedules, read_schedule, input_identity
from server_core import validate_schedule, build_payload, load_config
from diagnostics import inspect_run


def adjustment(data, index=0, op=0, **kwargs):
    return dict(lotKey='LOT-%06d' % (index + 1), opIndex=op, eqp='M1',
                signature=lot_signature(data, index), **kwargs)


class TaskAdjustmentTests(unittest.TestCase):
    def test_malformed_empty_adjustment_values_are_not_silently_ignored(self):
        for value in (None, '', {}, False):
            data = fixture(1)
            data['flows']['P'].pop(2)
            data['taskOverrides'] = value
            with self.subTest(value=value), self.assertRaisesRegex(InputError, '必须为列表'):
                validate_schedule(data)

    def test_input_location_does_not_leak_from_previous_table(self):
        cases = [
            ('mps', lambda data: data['mps'][0].update(mps=0.5)),
            ('constraints', lambda data: data['constraints'].append({'eqp':'missing', 'recipe':'R'})),
            ('weekly', lambda data: data['weekly'].append({'ppn':'missing', 'week':'W1'})),
        ]
        for table, change in cases:
            data = fixture(1)
            data['flows']['P'].pop(2)
            change(data)
            with self.subTest(table=table), self.assertRaises(InputError) as caught:
                validate_schedule(data)
            self.assertEqual(caught.exception.issues[0]['table'], table)
            self.assertEqual(caught.exception.issues[0]['record'], 1)

    def test_lot_signature_survives_javascript_integral_float_serialization(self):
        data = fixture(1)
        data['flows']['P'][0][2] = 10.0
        signature = lot_signature(data, 0)
        data['flows']['P'][0][2] = 10
        self.assertEqual(signature, lot_signature(data, 0))

    def test_machine_and_earliest_start_are_hard_constraints(self):
        data = fixture(3)
        item = adjustment(data, earliestStart='2026-09-02T08:00:00+08:00')
        data['taskOverrides'] = [item]
        result = run(data)
        task = next(t for t in result['cpTasks'] if t['lotKey'] == item['lotKey'] and t['opIndex'] == 0)
        self.assertEqual(task['machine'], 'M1')
        self.assertGreaterEqual(task['blockStart'], parse_date(item['earliestStart']))
        findings, _ = inspect_run({'input': data, 'result': result})
        self.assertFalse([f for f in findings if f['code'].startswith('R')], findings)

    def test_fixed_task_keeps_processing_start_and_reserves_setup(self):
        data = fixture(3)
        data['eqp'][0]['card'] = 'OTHER'
        item = adjustment(data, fixedStart='2026-09-01T08:00:00+08:00', card='C1')
        data['taskOverrides'] = [item]
        result = run(data)
        fixed = next(t for t in result['cpTasks'] if t['lotKey'] == item['lotKey'] and t['opIndex'] == 0)
        self.assertEqual(fixed['start'], parse_date(item['fixedStart']))
        self.assertEqual(fixed['end'] - fixed['start'], 25 * 10 * MIN)
        tasks = sorted((t for t in result['cpTasks'] if t['machine'] == 'M1'), key=lambda t: t['blockStart'])
        self.assertTrue(all(b['blockStart'] >= a['end'] for a, b in zip(tasks, tasks[1:])))

    def test_adjacent_original_tasks_can_both_be_fixed_without_extra_setup(self):
        from datetime import datetime, timezone
        data = fixture(2)
        for row in data['lots']:
            row[2] = 'A'
        data['mps'][0]['platform'] = 'A'
        before = run(data, {'bakeWindowHours': 0})
        items = []
        for task in before['cpTasks']:
            if task['opIndex'] == 0:
                iso = lambda ms: datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat()
                items.append(adjustment(data, int(task['lotKey'].split('-')[1]) - 1,
                                        fixedStart=iso(task['start']), reserveStart=iso(task['blockStart']), card=task['card']))
        data['taskOverrides'] = items
        after = run(data, {'bakeWindowHours': 0})
        expected = {(t['lotKey'], t['opIndex']): t['start'] for t in before['cpTasks'] if t['opIndex'] == 0}
        self.assertEqual({(t['lotKey'], t['opIndex']): t['start'] for t in after['cpTasks'] if t['opIndex'] == 0}, expected)

    def test_fixed_pm_conflict_and_unready_predecessor_are_not_silently_moved(self):
        data = fixture(1)
        data['taskOverrides'] = [adjustment(data, fixedStart='2026-09-01T08:00:00+08:00', card='C1')]
        data['pm'] = [dict(eqp='M1', early='2026-09-01', due='2026-09-01', over='2026-09-01', hours=12, start='2026-09-01')]
        with self.assertRaises(TaskConstraintError):
            run(data)
        data['pm'] = []
        data['taskOverrides'] = [adjustment(data, op=3, fixedStart='2026-09-01T08:00:00+08:00', card='C1')]
        with self.assertRaisesRegex(TaskConstraintError, '前序|未能执行'):
            run(data)

    def test_reimported_lot_or_route_cannot_inherit_stale_constraints(self):
        data = fixture(2)
        data['taskOverrides'] = [adjustment(data)]
        data['lots'].reverse()
        with self.assertRaisesRegex(InputError, '输入已变化'):
            run(data)
        data = fixture(1)
        data['taskOverrides'] = [adjustment(data)]
        data['flows']['P'][0][2] += 1
        with self.assertRaisesRegex(InputError, '输入已变化'):
            run(data)

    def test_invalid_resource_and_duplicate_override_are_rejected(self):
        data = fixture(1)
        item = adjustment(data)
        data['taskOverrides'] = [item, copy.deepcopy(item)]
        with self.assertRaisesRegex(InputError, '只能有一条'):
            run(data)
        item['eqp'] = 'OVEN-1'
        data['taskOverrides'] = [item]
        with self.assertRaisesRegex(InputError, 'RUN 机台'):
            run(data)


class HistoryTests(unittest.TestCase):
    def test_snapshot_round_trip_never_overwrites_live_config(self):
        data = fixture(1)
        result = compact_result(run(data, {'algorithm': 'greedy', 'bakeWindowHours': 0}))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'config.json'
            path.write_text('{"live":true}')
            summary = save_schedule(path, {'input':data, 'options':{}, 'result':result})
            self.assertEqual(list_schedules(path), [summary])
            restored = read_schedule(path, summary['id'])
            self.assertEqual(restored['result'], result)
            self.assertEqual(restored['input'], data)
            self.assertEqual(path.read_text(), '{"live":true}')
            self.assertRaises(ValueError, read_schedule, path, '../config')
            self.assertRaises(ValueError, save_schedule, path, {'input':data, 'result':{}})

    def test_input_identity_ignores_loading_timestamps_but_not_constraints(self):
        data = fixture(1)
        other = copy.deepcopy(data)
        other['meta'].update(loadedAt='later', configPath='other')
        self.assertEqual(input_identity(data), input_identity(other))
        other['taskOverrides'] = [adjustment(data)]
        self.assertNotEqual(input_identity(data), input_identity(other))


class LocatedErrorTests(unittest.TestCase):
    def test_mapping_error_identifies_sheet_and_field(self):
        cfg = load_config()
        cfg['fieldMap']['MPS']['mps'] = 'NOT_A_HEADER'
        with self.assertRaises(InputError) as caught:
            build_payload(config=cfg)
        self.assertEqual(caught.exception.issues[0]['sheet'], 'MPS')
        self.assertEqual(caught.exception.issues[0]['field'], 'mps')

    def test_date_error_identifies_record_and_field(self):
        data = fixture(1)
        data['pm'] = [dict(eqp='M1', early='not-date', due='2026-09-01', over='2026-09-02', hours=4)]
        with self.assertRaises(InputError) as caught:
            validate_schedule(data)
        issue = caught.exception.issues[0]
        self.assertEqual((issue['table'], issue['record'], issue['field']), ('pm', 1, 'early'))


class WorkflowApiTests(unittest.TestCase):
    def test_save_without_run_and_history_endpoints(self):
        with tempfile.TemporaryDirectory() as folder:
            config = Path(folder) / 'config.json'
            cfg = load_config()
            config.write_text(json.dumps(cfg), encoding='utf-8')
            server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
            server.config_path = str(config)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = 'http://127.0.0.1:%s' % server.server_port
            def request(path, body=None):
                req = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None,
                                             headers={'Content-Type': 'application/json'})
                with urllib.request.urlopen(req) as response:
                    return json.load(response)
            try:
                with patch('server.run_schedule', side_effect=AssertionError('must not schedule')):
                    saved = request('/api/config', {'dataSource': cfg['dataSource']})
                self.assertTrue(saved['ok'])
                before = config.read_bytes()
                data = fixture(1)
                result = compact_result(run(data, {'algorithm':'greedy', 'bakeWindowHours':0}))
                summary = request('/api/schedules', {'input':data, 'result':result, 'options':{}})['summary']
                self.assertEqual(request('/api/schedules')['schedules'][0]['id'], summary['id'])
                restored = request('/api/schedules/' + summary['id'])
                self.assertEqual(restored['result'], result)
                self.assertEqual(config.read_bytes(), before)
                invalid = copy.deepcopy(cfg)
                invalid['fieldMap']['MPS']['mps'] = 'MISSING'
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    request('/api/preview', {'fieldMap': invalid['fieldMap']})
                response = json.load(caught.exception)
                self.assertEqual(response['issues'][0]['field'], 'mps')
                self.assertEqual(config.read_bytes(), before)
            finally:
                server.shutdown(); server.server_close(); thread.join()
