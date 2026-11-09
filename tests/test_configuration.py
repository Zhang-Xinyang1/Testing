import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from server import Handler
from server_core import (build_payload, load_config, merge_mps,
                         validate_schedule)


class ConfigurationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = build_payload()

    def test_wip_is_validated_without_writing_workbook(self):
        d = copy.deepcopy(self.data)
        d['wip'] = [{'lotId': d['lots'][0][0], 'eqp': ''}]
        validate_schedule(d)
        d['wip'].append(d['wip'][0].copy())
        with self.assertRaises(ValueError):
            validate_schedule(d)

    def test_duplicate_machine_and_wrong_card_type_are_rejected(self):
        d = copy.deepcopy(self.data)
        d['eqp'].append(d['eqp'][0].copy())
        with self.assertRaises(ValueError):
            validate_schedule(d)
        d = copy.deepcopy(self.data)
        d['mps'][0]['platform'] = 'WRONG'
        with self.assertRaises(ValueError):
            validate_schedule(d)

    def test_duplicate_lot_id_rows_are_allowed(self):
        d = copy.deepcopy(self.data)
        first = d['lots'][0]
        d['lots'].append(list(first))
        d['mps'][0]['mps'] += first[3]
        validate_schedule(d)

    def test_duplicate_mps_rows_are_merged_by_ppn_prefix(self):
        d = copy.deepcopy(self.data)
        first = d['mps'][0]
        total = first['mps']
        first['ppn'] = str(first['ppn']) + '-1'
        first['mps'] = total // 2
        d['mps'].append({'platform': 'CP-B', 'ppn': 'PPN01-2',
                         'mps': total - total // 2})
        merged = next(m for m in merge_mps(d['mps'])
                      if m['ppn'] == 'PPN01')
        self.assertEqual(merged['mps'], total)
        self.assertIn('CP-A', merged['platform'])
        self.assertIn('CP-B', merged['platform'])
        validate_schedule(d)

    def test_merge_mps_sums_identical_orders(self):
        merged = merge_mps([
            {'platform': 'CP-A', 'ppn': 'P-1', 'mps': 500},
            {'platform': 'CP-A', 'ppn': 'P-2', 'mps': 500},
            {'platform': 'CP-B', 'ppn': 'P-3', 'mps': 250},
        ])
        self.assertEqual(merged, [
            {'platform': 'CP-A;CP-B', 'ppn': 'P', 'mps': 1250}])

    def test_bad_mapped_column_is_rejected(self):
        c = load_config()
        c['fieldMap']['MPS']['ppn'] = 'NonexistentColumn'
        with self.assertRaisesRegex(ValueError, '字段映射不存在') as error:
            build_payload(config=c)
        self.assertIn('实际表头：Platform、PPN、MPS', str(error.exception))

    def test_seven_sheet_mode_falls_back_without_helper_sheets(self):
        c = load_config()
        for key in ('params', 'lots', 'weekly'):
            c['dataSource']['sheetMap'].pop(key, None)
        data = build_payload(config=c)
        self.assertEqual(data['weekly'], [])
        self.assertEqual(data['rules'], [])
        self.assertEqual(sum(l[3] for l in data['lots']), sum(m['mps'] for m in data['mps']))
        self.assertTrue(all(1<=lot[3]<=25 for lot in data['lots']))

    def test_overlapping_wip_card_is_rejected(self):
        d = copy.deepcopy(self.data)
        e = d['eqp'][0]
        d['wip'] = [dict(lotId=r[0], eqp=e['id'], card=e['card'],
                         start='2026-09-01T00:00:00+08:00', end='2026-09-01T10:00:00+08:00')
                    for r in d['lots'][:2]]
        with self.assertRaises(ValueError):
            validate_schedule(d)

    def test_api_preview_save_reload_and_failure_are_atomic(self):
        workbook = ROOT / 'data/Testing调度算例_新版.xlsx'
        before = hashlib.sha256(workbook.read_bytes()).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'config.json'
            config.write_text(json.dumps(load_config(), ensure_ascii=False), encoding='utf-8')
            server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
            server.config_path = str(config)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = 'http://127.0.0.1:%d' % server.server_port
            def post(path, data, origin=None):
                headers = {'Content-Type': 'application/json'}
                if origin:
                    headers['Origin'] = origin
                request = urllib.request.Request(base + path, json.dumps(data).encode(), headers)
                try:
                    with urllib.request.urlopen(request) as response:
                        return json.load(response)
                except urllib.error.HTTPError as error:
                    error.msg = error.read().decode('utf-8')
                    raise
            try:
                update = {'scheduling': {'wip': [{'lotId': self.data['lots'][0][0], 'eqp': ''}],
                                          'bakeResources': {'machines': 9, 'chambers': 2}}}
                original = config.read_bytes()
                preview = post('/api/preview', update)
                self.assertEqual(preview['data']['bakeResources']['machines'], 7)
                self.assertEqual(config.read_bytes(), original)
                upload = urllib.request.Request(base + '/api/uploads', workbook.read_bytes(),
                    {'Content-Type': 'application/octet-stream', 'X-Filename': 'reference.xlsx'})
                with urllib.request.urlopen(upload) as response:
                    uploaded = json.load(response)
                self.assertEqual(Path(uploaded['path']).read_bytes(), workbook.read_bytes())
                self.assertIn('MPS', uploaded['sheets'])
                self.assertEqual(config.read_bytes(), original)
                scheduled = post('/api/schedule', {'data': self.data, 'options': {}})
                self.assertTrue(scheduled['ok'])
                self.assertEqual(scheduled['result']['metrics']['totalLots'],
                                 len(self.data['lots']))
                self.assertEqual(scheduled['result']['metrics']['completedWafers'], 11000)
                self.assertEqual(scheduled['result']['metrics']['completedWafers'],
                                 sum(p['completed'] for p in scheduled['result']['productStats']))
                # Exercise actual XLSX upload -> mapped preview -> scheduling,
                # including repeated LotIDs with distinct row identities.
                from build_case import write_workbook
                from build_data import read_workbook
                duplicate_book = Path(directory) / 'duplicate-lots.xlsx'
                sheets = read_workbook(str(workbook))
                sheets.append(('Lot明细', [['LotID', 'Wafer Qty']] + [
                    [lot[1] + '-vendor', lot[3]] for lot in self.data['lots']]))
                write_workbook(duplicate_book, sheets)
                request = urllib.request.Request(base + '/api/uploads', duplicate_book.read_bytes(),
                    {'Content-Type': 'application/octet-stream', 'X-Filename': 'duplicate-lots.xlsx'})
                with urllib.request.urlopen(request) as response:
                    duplicate_upload = json.load(response)
                mapping = load_config()
                mapping['dataSource']['path'] = duplicate_upload['path']
                mapping['dataSource']['sheetMap']['lots'] = 'Lot明细'
                mapping['fieldMap']['Lot明细'] = {'lotId': 'LotID', 'wafer': 'Wafer Qty'}
                imported = post('/api/preview', {key: mapping[key] for key in ('dataSource', 'fieldMap')})['data']
                duplicate_result = post('/api/schedule', {'data': imported,
                    'options': {'bakeWindowHours': 0, 'searchIterations': 0}})['result']
                self.assertEqual(len({lot['id'] for lot in duplicate_result['lots']}), 72)
                self.assertEqual(len({lot['key'] for lot in duplicate_result['lots']}), 476)
                self.assertEqual(duplicate_result['metrics']['completedWafers'], 11000)
                self.assertEqual(config.read_bytes(), original)
                reserved = post('/api/oracle/config', {'host': 'db.internal', 'port': 1521,
                    'service': 'MES', 'user': 'readonly', 'passwordEnv': 'ORACLE_PASSWORD', 'views': {'mps': 'MES.V_MPS'}})
                self.assertEqual(reserved['status'], 'reserved')
                self.assertEqual(load_config(str(config))['oracle']['service'], 'MES')
                with self.assertRaises(urllib.error.HTTPError):
                    post('/api/oracle/config', {'password': 'must-not-persist'})
                saved = post('/api/config', update)
                self.assertTrue(saved['ok'])
                self.assertEqual(build_payload(str(config))['wip'][0]['lotId'], self.data['lots'][0][0])
                persisted = config.read_bytes()
                with self.assertRaises(urllib.error.HTTPError) as error:
                    post('/api/config', {'scheduling': {'eqp': [{'id': '', 'type': 'E1'}]}})
                self.assertEqual(error.exception.code, 400)
                self.assertEqual(config.read_bytes(), persisted)
                with self.assertRaises(urllib.error.HTTPError) as error:
                    post('/api/config', update, 'https://example.invalid')
                self.assertEqual(error.exception.code, 403)
                with urllib.request.urlopen(base + '/') as response:
                    html = response.read().decode()
                self.assertIn('assets/js/configuration.js', html)
                self.assertNotIn('assets/js/resources.js', html)
                report = post('/api/runs', {'input': saved['data'], 'options': {},
                    'result': None, 'error': 'test failure', 'trigger': '测试'})['report']
                self.assertEqual(report['findings'][0]['code'], 'S01')
                with urllib.request.urlopen(base + '/api/runs') as response:
                    runs = json.load(response)['runs']
                self.assertEqual(runs[0]['id'], report['id'])
            finally:
                server.shutdown()
                server.server_close()
                thread.join()
        self.assertEqual(hashlib.sha256(workbook.read_bytes()).hexdigest(), before)


if __name__ == '__main__':
    unittest.main()
