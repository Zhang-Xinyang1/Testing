"""PM object mapping and synchronized downtime; synthetic equipment only."""
import copy
import json
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from test_import_case import export_sheets, write_export
from test_platforms import platform_case
from diagnostics import inspect_run
from input_rules import resolve_pm_target
from scheduler import HOUR, run
from server import Handler
from server_core import build_payload, calendar_issues, load_config, validate_schedule


def pm(eqp, **extra):
    return dict(eqp=eqp, early='2026-09-01', due='2026-09-01',
                over='2026-09-01', hours=4, **extra)


def paired_case(shared=False):
    data = platform_case()
    for e in data['eqp']:
        if e['id'] in ('M1', 'M2', 'M3'):
            e['proberId'] = 'P1' if shared and e['id'] == 'M2' else 'P' + e['id'][1:]
    return data


class ProberPMTests(unittest.TestCase):
    def test_prober_and_tester_pm_produce_identical_resource_calendars(self):
        for algorithm in ('greedy', 'search'):
            data = paired_case()
            data['pm'] = [pm('p1', start='2026-09-01T00:00:00+08:00')]
            before = copy.deepcopy(data)
            normalized = validate_schedule(data)
            self.assertEqual(calendar_issues(normalized), [])
            options = dict(algorithm=algorithm, bakeWindowHours=0, searchIterations=0)
            result = run(data, options)
            self.assertEqual(data, before)
            record = result['pmRecords'][0]
            self.assertEqual(record['eqp'], 'P1')
            self.assertEqual(record['targetKind'], 'PROBER')
            self.assertEqual(record['affectedEqps'], ['M1'])
            self.assertEqual(record['resources'], ['M1'])
            self.assertEqual(result['metrics']['pmCount'], 1)
            direct = copy.deepcopy(data)
            direct['pm'][0]['eqp'] = 'm1'
            direct_result = run(direct, options)
            self.assertEqual(result['cpTasks'], direct_result['cpTasks'])
            self.assertEqual([m['pm'] for m in result['machines']],
                             [m['pm'] for m in direct_result['machines']])
            self.assertFalse([f for f in inspect_run({'input': data, 'result': result})[0]
                              if f['code'].startswith('R')])

    def test_shared_prober_reserves_common_gap_and_blocks_borrow(self):
        data = paired_case(shared=True)
        data['wip'] = [
            dict(lotId='L0', eqp='M1', start='2026-09-01', end='2026-09-01T02:00+08:00'),
            dict(lotId='L1', eqp='M2', start='2026-09-01T02:00+08:00', end='2026-09-01T03:00+08:00')]
        data['pm'] = [pm('P1')]
        data['borrow'] = [dict(eqp='M2', date='2026-09-01', hours=3)]
        data = validate_schedule(data)
        self.assertEqual(calendar_issues(data), [])
        result = run(data, dict(bakeWindowHours=0, searchIterations=0))
        record = result['pmRecords'][0]
        self.assertEqual(record['affectedEqps'], ['M1', 'M2'])
        self.assertEqual(record['start'], result['start'] + 3 * HOUR)
        self.assertEqual(record['end'], result['start'] + 7 * HOUR)
        for machine in result['machines']:
            if machine['id'] in ('M1', 'M2'):
                self.assertEqual(machine['pm'], [[record['start'], record['end']]])
                for task in machine['tasks']:
                    self.assertTrue(task['end'] <= record['start'] or task['blockStart'] >= record['end'])
        self.assertEqual(result['borrows'][0]['start'], record['end'])
        self.assertEqual(result['metrics']['pmCount'], 1)
        self.assertFalse([f for f in inspect_run({'input': data, 'result': result})[0]
                          if f['code'].startswith('R')])

    def test_fixed_prober_pm_still_conflicts_with_wip(self):
        data = paired_case(shared=True)
        data['pm'] = [pm('P1', start='2026-09-01T00:00+08:00')]
        data['wip'] = [dict(lotId='L0', eqp='M2', start='2026-09-01', end='2026-09-01T01:00+08:00')]
        data = validate_schedule(data)
        self.assertTrue(calendar_issues(data))
        with self.assertRaisesRegex(ValueError, 'PM 窗口内无连续空档'):
            run(data, dict(algorithm='greedy', bakeWindowHours=0))

    def test_overlapping_fixed_pair_is_one_union_interval_with_both_sources(self):
        for algorithm in ('greedy', 'search'):
            for hour, duration in [(8, 4), (10, 6), (9, 5)]:
                data = paired_case()
                data['pm'] = [pm('M1', start='2026-09-01T08:00+08:00'),
                              pm('p1', start='2026-09-01T%02d:00+08:00' % hour)]
                before = copy.deepcopy(data)
                normalized = validate_schedule(data)
                self.assertEqual(calendar_issues(normalized), [])
                result = run(data, dict(algorithm=algorithm, bakeWindowHours=0, searchIterations=0))
                self.assertEqual(data, before)
                self.assertEqual(result['metrics']['pmCount'], 1)
                self.assertEqual(result['metrics']['pmInputCount'], 2)
                self.assertEqual(result['metrics']['pmMergedRows'], 1)
                record = result['pmRecords'][0]
                self.assertEqual(record['sourceIds'], ['PM-01', 'PM-02'])
                self.assertEqual([p['eqp'] for p in record['sourcePlans']], ['M1', 'P1'])
                self.assertEqual([p['hours'] for p in record['sourcePlans']], [4, 4])
                self.assertEqual(record['affectedProbers'], ['P1'])
                self.assertTrue(record['merged'])
                self.assertEqual(record['targetKind'], 'COMBINED')
                self.assertEqual(record['start'], result['start'] + 8 * HOUR)
                self.assertEqual(record['end'], record['start'] + duration * HOUR)
                machine = next(m for m in result['machines'] if m['id'] == 'M1')
                self.assertEqual(machine['pmMs'], duration * HOUR)
                self.assertEqual(len(machine['pm']), 1)
                self.assertFalse([f for f in inspect_run({'input': data, 'result': result})[0]
                                  if f['code'].startswith('R')])

    def test_nested_and_transitive_overlaps_merge_but_touching_intervals_do_not(self):
        cases = [
            ([('M1', 8, 8), ('P1', 10, 2)], 1, 8),
            ([('M1', 8, 4), ('P1', 10, 4), ('M1', 13, 4)], 1, 9),
            ([('M1', 8, 4), ('P1', 12, 4)], 2, 8),
            ([('M1', 8, 2), ('P1', 12, 2)], 2, 4),
        ]
        for plans, count, hours in cases:
            data = paired_case()
            data['pm'] = [dict(pm(target, start='2026-09-01T%02d:00+08:00' % hour), hours=duration)
                          for target, hour, duration in plans]
            result = run(validate_schedule(data), dict(algorithm='greedy', bakeWindowHours=0))
            self.assertEqual(result['metrics']['pmCount'], count)
            self.assertEqual(next(m for m in result['machines'] if m['id'] == 'M1')['pmMs'], hours * HOUR)
            self.assertFalse([f for f in inspect_run({'input': data, 'result': result})[0]
                              if f['code'].startswith('R')])

    def test_unrelated_or_flexible_plans_are_not_merged(self):
        for second in (pm('P2', start='2026-09-01T08:00+08:00'), pm('P1')):
            data = paired_case()
            data['pm'] = [pm('M1', start='2026-09-01T08:00+08:00'), second]
            result = run(validate_schedule(data), dict(algorithm='greedy', bakeWindowHours=0))
            self.assertEqual(result['metrics']['pmCount'], 2)
            self.assertTrue(all(not p['merged'] for p in result['pmRecords']))

    def test_merge_does_not_hide_invalid_source_window_or_wip_conflict(self):
        data = paired_case()
        data['pm'] = [pm('M1', start='2026-09-01T08:00+08:00'),
                      pm('P1', start='2026-09-01T10:00+08:00')]
        data['wip'] = [dict(lotId='L0', eqp='M1', start='2026-09-01T13:00+08:00', end='2026-09-01T15:00+08:00')]
        self.assertTrue(calendar_issues(validate_schedule(data)))
        with self.assertRaisesRegex(ValueError, 'PM 窗口内无连续空档'):
            run(data, dict(algorithm='greedy', bakeWindowHours=0))
        data['wip'] = []
        data['pm'][1]['early'] = '2026-09-02'
        data['pm'][1]['due'] = data['pm'][1]['over'] = '2026-09-02'
        with self.assertRaisesRegex(ValueError, '超出维护窗口'):
            run(data, dict(algorithm='greedy', bakeWindowHours=0))

    def test_shared_prober_merge_blocks_all_peers_and_compares_absolute_times(self):
        data = paired_case(shared=True)
        data['pm'] = [pm('M1', start='2026-09-01T08:00+08:00'),
                      pm('P1', start='2026-09-01T02:00Z')]
        result = run(validate_schedule(data), dict(algorithm='greedy', bakeWindowHours=0))
        record = result['pmRecords'][0]
        self.assertEqual(record['resources'], ['M1', 'M2'])
        self.assertEqual(record['end'] - record['start'], 6 * HOUR)
        self.assertEqual(result['metrics']['pmCount'], 1)

    def test_paired_eqp_and_prober_rows_are_two_full_independent_pm_events(self):
        # Equal windows and durations do not make the tester/prober rows duplicates.
        for algorithm in ('greedy', 'search'):
            data = paired_case()
            data['pm'] = [pm('m1'), pm('p1')]
            normalized = validate_schedule(data)
            self.assertEqual(calendar_issues(normalized), [])
            result = run(normalized, dict(algorithm=algorithm, bakeWindowHours=0, searchIterations=0))
            records = result['pmRecords']
            self.assertEqual(result['metrics']['pmCount'], 2)
            self.assertEqual([p['eqp'] for p in records], ['M1', 'P1'])
            self.assertEqual([p['id'] for p in records], ['PM-01', 'PM-02'])
            self.assertEqual([p['resources'] for p in records], [['M1'], ['M1']])
            self.assertTrue(all(p['end'] - p['start'] == 4 * HOUR for p in records))
            self.assertEqual(records[0]['start'], result['start'])
            self.assertEqual(records[1]['start'], records[0]['end'])
            machine = next(m for m in result['machines'] if m['id'] == 'M1')
            self.assertEqual(machine['pmMs'], 8 * HOUR)
            self.assertEqual(len(machine['pm']), 2)
            for task in machine['tasks']:
                self.assertGreaterEqual(task['blockStart'], records[1]['end'])
            self.assertFalse([f for f in inspect_run({'input': data, 'result': result})[0]
                              if f['code'].startswith('R')])

    def test_pair_total_duration_must_fit_window_without_collapsing_rows(self):
        data = paired_case()
        data['pm'] = [dict(pm('M1'), hours=13), dict(pm('P1'), hours=13)]
        normalized = validate_schedule(data)
        self.assertTrue(calendar_issues(normalized))
        with self.assertRaisesRegex(ValueError, 'PM 窗口内无连续空档'):
            run(normalized, dict(algorithm='greedy', bakeWindowHours=0))

    def test_direct_tester_pm_stops_the_whole_prober_assembly(self):
        data = paired_case(shared=True)
        data['pm'] = [pm('M1'), pm('OVEN-1')]
        data['bakeResources']['chambers'] = 4
        result = run(validate_schedule(data), dict(algorithm='greedy', bakeWindowHours=0))
        self.assertEqual(result['pmRecords'][0]['resources'], ['M1', 'M2'])
        self.assertEqual(result['pmRecords'][0]['affectedProbers'], ['P1'])
        self.assertEqual([p['eqp'] for p in result['pmRecords'][0]['sourcePlans']], ['M1'])
        self.assertEqual(result['pmRecords'][1]['resources'], ['OVEN-1-C%d' % n for n in range(1, 5)])
        self.assertEqual(next(m for m in result['machines'] if m['id'] == 'M2')['pm'],
                         [[result['pmRecords'][0]['start'], result['pmRecords'][0]['end']]])

    def test_either_component_pm_stops_assembly_without_inventing_peer_maintenance(self):
        for algorithm in ('greedy', 'search'):
            for target in ('M1', 'P1'):
                with self.subTest(algorithm=algorithm, target=target):
                    data = paired_case(shared=True)
                    data['pm'] = [pm(target)]
                    data['borrow'] = [dict(eqp='M2', date='2026-09-01', hours=2)]
                    result = run(validate_schedule(data), dict(algorithm=algorithm, bakeWindowHours=0, searchIterations=0))
                    record = result['pmRecords'][0]
                    self.assertEqual(record['affectedEqps'], ['M1', 'M2'])
                    self.assertEqual(record['affectedProbers'], ['P1'])
                    self.assertEqual([p['eqp'] for p in record['sourcePlans']], [target])
                    self.assertEqual(result['metrics']['pmInputCount'], 1)
                    self.assertEqual(result['borrows'][0]['start'], record['end'])
                    for machine in result['machines']:
                        if machine['id'] in ('M1', 'M2'):
                            self.assertEqual(machine['pm'], [[record['start'], record['end']]])
                            self.assertTrue(all(t['blockStart'] >= record['end'] for t in machine['tasks']))

    def test_nonoverlapping_component_maintenance_stops_the_assembly_twice(self):
        data = paired_case(shared=True)
        data['pm'] = [dict(pm('M1', start='2026-09-01 08:00'), hours=2),
                      dict(pm('P1', start='2026-09-01 14:00'), hours=2)]
        result = run(validate_schedule(data), dict(algorithm='greedy', bakeWindowHours=0))
        records = result['pmRecords']
        self.assertEqual(result['metrics']['pmCount'], 2)
        self.assertEqual([p['sourceIds'] for p in records], [['PM-01'], ['PM-02']])
        for machine in result['machines']:
            if machine['id'] in ('M1', 'M2'):
                self.assertEqual(machine['pm'], [[p['start'], p['end']] for p in records])
                self.assertEqual(machine['pmMs'], 4 * HOUR)
        self.assertTrue(all(p['affectedProbers'] == ['P1'] for p in records))

    def test_fixed_eqp_pm_cannot_run_while_any_assembly_member_has_wip(self):
        data = paired_case(shared=True)
        data['pm'] = [pm('M1', start='2026-09-01 08:00')]
        data['wip'] = [dict(lotId='L0', eqp='M2', start='2026-09-01 10:00', end='2026-09-01 11:00')]
        self.assertTrue(calendar_issues(validate_schedule(data)))
        with self.assertRaisesRegex(ValueError, 'PM 窗口内无连续空档'):
            run(data, dict(algorithm='greedy', bakeWindowHours=0))

    def test_unknown_ambiguous_and_wrong_kind_fail_without_guessing(self):
        data = paired_case()
        data['eqp'][0]['proberId'] = 'm2'
        data['pm'] = [pm('M2')]
        with self.assertRaisesRegex(ValueError, '同时匹配 EQPID 和 proberID'):
            validate_schedule(data)
        for kind, expected in [('eqp', ['M2']), ('prober', ['M1'])]:
            data['pm'][0]['targetKind'] = kind
            normalized = validate_schedule(data)
            self.assertEqual(resolve_pm_target(normalized, normalized['pm'][0])['affectedEqps'], expected)
        for target, kind in [('MISSING', ''), ('M1', 'PROBER'), ('P2', 'EQP'), ('M2', 'invalid')]:
            data['pm'] = [pm(target, targetKind=kind)]
            with self.subTest(target=target, kind=kind), self.assertRaises(ValueError):
                validate_schedule(data)

    def test_derived_resource_lists_are_recomputed_after_pairing_changes(self):
        data = paired_case()
        data['pm'] = [pm('P1', affectedEqps=['M2'], resources=['M2'])]
        self.assertEqual(resolve_pm_target(data, data['pm'][0])['resources'], ['M1'])
        data['eqp'][0]['proberId'] = ''
        data['eqp'][1]['proberId'] = 'p1'
        result = run(data, dict(algorithm='greedy', bakeWindowHours=0))
        self.assertEqual(result['pmRecords'][0]['resources'], ['M2'])
        data['eqp'][1]['proberId'] = ''
        with self.assertRaisesRegex(ValueError, '对应关系'):
            validate_schedule(data)

    def test_xlsx_old_config_reads_pairing_and_optional_pm_columns(self):
        with tempfile.TemporaryDirectory() as directory:
            sheets = export_sheets()
            sheets[1][1][1][2] = 'prober-01'
            sheets[5] = ('pm计划', [
                ['EQP ID', 'Early Due', 'Due Date', 'Over Due', 'Estimated Hours', 'target_kind', 'start'],
                ['PROBER-01', '2026-09-01', '2026-09-01', '2026-09-01', 2, 'prober', '2026-09-01 08:00'],
                ['machine1', '2026-09-01', '2026-09-01', '2026-09-01', 2, 'eqp', '2026-09-01 09:00']])
            path = Path(directory) / 'paired.xlsx'
            write_export(path, sheets)
            cfg = load_config()
            cfg['dataSource']['path'] = str(path)
            cfg['fieldMap']['EQP'].pop('proberId', None)
            cfg['fieldMap']['PM计划'].pop('targetKind', None)
            cfg['fieldMap']['PM计划'].pop('start', None)
            data = build_payload(config=cfg)
            self.assertEqual(data['eqp'][0]['proberId'], 'PROBER-01')
            self.assertEqual(data['pm'][0]['targetKind'], 'PROBER')
            result = run(data, dict(algorithm='greedy', bakeWindowHours=0))
            self.assertEqual(result['pmRecords'][0]['affectedEqps'], ['MACHINE1'])
            self.assertEqual(result['pmRecords'][0]['start'], result['start'] + 8 * HOUR)
            self.assertEqual(result['pmRecords'][0]['end'], result['start'] + 11 * HOUR)
            self.assertEqual(result['metrics']['pmInputCount'], 2)
            self.assertEqual(result['metrics']['pmCount'], 1)
            self.assertEqual(data['planIssues'], [])
            # A full manual equipment override must supply its own pairing.
            cfg['scheduling']['eqp'] = [dict(e) for e in data['eqp']]
            for e in cfg['scheduling']['eqp']:
                e.pop('proberId', None)
            with self.assertRaisesRegex(ValueError, '手工机台覆盖'):
                build_payload(config=cfg)

    def test_old_workbook_without_prober_column_still_loads(self):
        data = build_payload()
        self.assertTrue(all(not e['proberId'] for e in data['eqp']))
        self.assertTrue(all(not p['start'] and not p['targetKind'] for p in data['pm']))

    def test_diagnostics_detect_missing_linked_resource_and_missing_pm(self):
        data = paired_case(shared=True)
        data['pm'] = [pm('P1')]
        result = run(data, dict(algorithm='greedy', bakeWindowHours=0))
        result['pmRecords'][0]['resources'] = ['M1']
        self.assertIn('R11', {f['code'] for f in inspect_run({'input': data, 'result': result})[0]})
        result['pmRecords'] = []
        self.assertIn('R11', {f['code'] for f in inspect_run({'input': data, 'result': result})[0]})

    def test_http_schedule_accepts_prober_pm_and_reports_real_tester(self):
        data = paired_case()
        data['pm'] = [pm('p1', start='2026-09-01T08:00+08:00'),
                      pm('m1', start='2026-09-01T10:00+08:00')]
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            request = urllib.request.Request('http://127.0.0.1:%d/api/schedule' % server.server_port,
                json.dumps({'data': data, 'options': {'algorithm': 'greedy', 'bakeWindowHours': 0}}).encode(),
                {'Content-Type': 'application/json'})
            with urllib.request.urlopen(request) as response:
                body = json.load(response)
            self.assertTrue(body['ok'])
            self.assertEqual(body['result']['pmRecords'][0]['eqp'], 'P1')
            self.assertEqual(body['result']['pmRecords'][0]['resources'], ['M1'])
            self.assertEqual(body['result']['metrics']['pmCount'], 1)
            self.assertEqual(body['result']['pmRecords'][0]['sourceIds'], ['PM-01', 'PM-02'])
            self.assertEqual(body['result']['pmRecords'][0]['hours'], 6)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == '__main__':
    unittest.main()
