import copy
import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest.mock import patch

from test_scheduler import fixture
from diagnostics import inspect_run
from platforms import normalize_platforms
from scheduler import run
from server import Handler
from server_core import build_payload, load_config, read_source, validate_schedule
from legacy_case import build_payload, load_config
from build_data import read_workbook


def platform_case():
    data = fixture(4)
    data['flows']['P'] = [['CP1', 'R', 10], ['BAKE', '210C24H', None], ['CP2', 'R', 20]]
    data['eqp'][0]['type'] = 'T5830'
    data['eqp'][1]['type'] = 'K8000L'
    data['eqp'].append(dict(data['eqp'][0], id='M3'))
    data['mps'][0]['platform'] = ' t5830；ｋ８０００Ｌ '
    for lot in data['lots']:
        lot[2] = '\ufeff t5830\u200b ; k8000L '
    data['platformType'] = {'t5830': 't5830', 'k8000L': 'k8000L'}
    return data


class PlatformTests(unittest.TestCase):
    def test_repeated_types_and_format_variants_validate_and_schedule(self):
        data = platform_case()
        before = copy.deepcopy(data)
        validate_schedule(data)
        result = run(data, {'bakeWindowHours': 0})
        self.assertEqual(data, before)
        self.assertEqual(result['metrics']['completedWafers'], 100)
        self.assertEqual(result['productStats'][0]['platform'], 'K8000L;T5830')
        self.assertEqual({lot['platform'] for lot in result['lots']}, {'K8000L;T5830'})
        self.assertEqual(len([m for m in result['machines'] if m['type'] == 'T5830']), 2)
        findings, _ = inspect_run({'input': data, 'result': result})
        self.assertFalse([f for f in findings if f['code'].startswith('R')], findings)

    def test_unknown_model_is_not_guessed_and_error_lists_actual_types(self):
        for field in ('mps', 'lots'):
            with self.subTest(field=field):
                data = platform_case()
                if field == 'mps':
                    data['mps'][0]['platform'] = 'T5830;K8000'
                else:
                    data['lots'][0][2] = 'T5830;K8000'
                with self.assertRaises(ValueError) as error:
                    validate_schedule(data)
                message = str(error.exception)
                self.assertIn('缺少机台组：K8000；', message)
                self.assertIn('当前 EQP.TYPE：K8000L、T5830', message)
                self.assertIn('TYPE 允许重复', message)

    def test_equivalent_mps_variants_merge_without_duplicate_groups(self):
        data = platform_case()
        data['mps'] = [{'ppn': 'P-1', 'platform': ' t5830 ', 'mps': 50},
                       {'ppn': 'P-2', 'platform': 'Ｔ５８３０\nK8000L', 'mps': 50}]
        result = run(data, {'algorithm': 'greedy', 'bakeWindowHours': 0})
        self.assertEqual(result['productStats'][0]['platform'], 'T5830;K8000L')
        self.assertEqual(result['metrics']['completedWafers'], 100)

    def test_case_variants_in_eqp_share_one_group_and_all_capacity(self):
        data = platform_case()
        data['eqp'].append(dict(data['eqp'][1], id='M4', type='k8000l'))
        normalized = normalize_platforms(data)
        self.assertEqual(len([e for e in normalized['eqp'] if e['type'] == 'K8000L']), 2)
        self.assertEqual(normalized['mps'][0]['platform'], 'K8000L;T5830')
        validate_schedule(normalized)

    def test_excel_import_resolves_groups_and_identifies_stale_overrides(self):
        config = load_config()
        original = build_payload()
        names = {'CP-A': 'T5830', 'CP-B': 'K8000L', 'CP-C': 'OTHER'}
        workbook = read_workbook(read_source(config['dataSource']['path']))
        changed = []
        for name, rows in workbook:
            rows = copy.deepcopy(rows)
            if name == 'MPS':
                for row in rows[1:]:
                    row[0] = ';'.join(names[group].lower() for group in row[0].split(';'))
            elif name in ('EQP', '约束条件'):
                for row in rows[1:]:
                    row[0] = names.get(row[0], row[0])
            changed.append((name, rows))
        with patch('server_core.read_workbook', return_value=changed):
            config['scheduling'] = {}
            data = build_payload(config=config)
            self.assertEqual(data['meta']['eqpSource'], 'excel')
            self.assertEqual(len([e for e in data['eqp'] if e['type'] == 'T5830']), 9)
            self.assertTrue(all(set(m['platform'].split(';')) <= set(names.values())
                                for m in data['mps']))
            config['scheduling']['eqp'] = original['eqp']
            with self.assertRaises(ValueError) as error:
                build_payload(config=config)
            self.assertIn('手工机台配置（覆盖 Excel EQP）', str(error.exception))
            self.assertIn('Excel 原表 TYPE：K8000L、OTHER、T5830', str(error.exception))
            self.assertIn('CP-A', str(error.exception))
            self.assertEqual(config['scheduling']['eqp'], original['eqp'])

    def test_schedule_api_uses_same_matching_and_error_details(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            data = platform_case()
            def request():
                body = {'data': data, 'options': {'bakeWindowHours': 0}}
                return urllib.request.Request('http://127.0.0.1:%d/api/schedule' % server.server_port,
                    json.dumps(body).encode(), {'Content-Type': 'application/json'})
            with urllib.request.urlopen(request()) as response:
                result = json.load(response)['result']
            self.assertEqual(result['metrics']['completedWafers'], 100)
            data['mps'][0]['platform'] = 'T5830;K8000'
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(request())
            self.assertEqual(error.exception.code, 400)
            self.assertIn('缺少机台组：K8000；', json.load(error.exception)['error'])
        finally:
            server.shutdown()
            server.server_close()
            worker.join()


if __name__ == '__main__':
    unittest.main()
