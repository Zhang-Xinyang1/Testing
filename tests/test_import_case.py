"""Synthetic regression cases for mixed-case factory exports (no production data)."""
import copy
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from test_platforms import platform_case
from build_case import write_workbook
from build_data import clean_rows, read_workbook
from diagnostics import inspect_run
from platforms import normalize_platforms
from scheduler import run
from server_core import build_payload, load_config, rows_for, validate_schedule


def export_sheets():
    return [
        ('mPs', [['PLATFORM', 'pPn', 'mps'], ['alpha', 'ProductA-vendor', 25],
                 ['ALPHA', 'productB-vendor', 20]]),
        ('eQp', [['type', 'eqpid', 'proberID', 'Status', 'Current ProbeCard', 'Current Recipe'],
                 ['Alpha', 'Machine1', 'Ignored1', 'run', 'Card5', 'Recipe2'],
                 ['ALPHA', 'MACHINE2', 'Ignored2', 'Run', 'Card1', 'Recipe1']]),
        ('针卡', [['rECIPE', 'pROBEcARD lIST'],
                  ['rECIPE1', 'card1,CARD2；Card3\nCard4、Card5,card1'],
                  ['rECIPE2', 'Card5']]),
        ('测试FLOW&工时', [['pROD', 'sTEP', 'rECIPE', 'tIME'],
                          ['PRODUCTA', 'cp1', 'RECIPE1', 2],
                          ['producta', 'bake', '210c24h', ''],
                          ['ProductA', 'Cp2', 'recipe2', 3],
                          ['productb', 'CP1', 'Recipe1', 2],
                          ['PRODUCTB', 'Bake', '210C24h', ''],
                          ['ProductB', 'cp2', 'RECIPE2', 3]]),
        ('借机计划', [['date', 'eqpid', '借机时数'], [46289, 'machine1', 10.7]]),
        ('pm计划', []), ('约束条件', []),
    ]


def write_export(path, sheets):
    write_workbook(path, sheets)
    with zipfile.ZipFile(path) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    # WPS and localized Excel exports use built-in date style 58.
    files['xl/styles.xml'] = b'<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><cellXfs><xf numFmtId="58"/></cellXfs></styleSheet>'
    files['xl/worksheets/sheet5.xml'] = files['xl/worksheets/sheet5.xml'].replace(b'r="A2"', b'r="A2" s="0"')
    with zipfile.ZipFile(path, 'w') as archive:
        for name, value in files.items():
            archive.writestr(name, value)


class ImportCaseTests(unittest.TestCase):
    def test_unused_duplicate_headers_and_extra_sheets_do_not_block_import(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'export.xlsx'
            sheets = export_sheets()
            sheets[0][1][0].extend(['Density', 'Density'])
            for row in sheets[0][1][1:]:
                row.extend(['left', 'right'])
            sheets.append(('Unmapped reference', [['Tech', 'Tech'], ['old', 'other']]))
            write_export(path, sheets)
            cfg = load_config()
            cfg['dataSource']['path'] = str(path)
            data = build_payload(config=cfg)
            self.assertEqual(sum(row['mps'] for row in data['mps']), 45)
            self.assertEqual(len(data['lots']), 2)
            self.assertIn('Unmapped reference', [s['name'] for s in data['sheetSummary']])
            header, rows = clean_rows([['Used', 'Other', 'Other'], ['yes', 'left', 'right']], reject_duplicates=False)
            self.assertEqual(header, ['Used', 'Other', 'Other'])
            self.assertEqual(rows, [{'Used': 'yes'}])

    def test_used_duplicate_header_error_names_source_sheet_row_and_columns(self):
        for table, field in ((0, 'mPs'), (1, 'STATUS')):
            with self.subTest(table=table), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'uploaded.xlsx'
                sheets = export_sheets()
                sheets[table][1][0].append(field)
                for row in sheets[table][1][1:]:
                    row.append('bad')
                write_export(path, sheets)
                cfg = load_config()
                cfg['dataSource'].update(path=str(path), displayName='selected.xlsx')
                with self.assertRaisesRegex(ValueError, '字段匹配不唯一') as error:
                    build_payload(config=cfg)
                message = str(error.exception)
                self.assertIn('文件「selected.xlsx」', message)
                self.assertIn(str(path), message)
                self.assertIn('Sheet「%s」' % ('MPS' if table == 0 else 'EQP'), message)
                self.assertIn('表头第 1 行', message)
                self.assertIn('C列' if table == 0 else 'D列', message)
                self.assertIn('D列' if table == 0 else 'G列', message)

    def test_summary_honors_header_row_and_ignores_duplicate_title_cells(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'export.xlsx'
            write_export(path, export_sheets())
            workbook = [(name, [['Title', 'Title']] + rows) for name, rows in read_workbook(path)]
            cfg = load_config()
            cfg['dataSource']['headerRow'] = 2
            with patch('server_core.read_workbook', return_value=workbook):
                data = build_payload(config=cfg)
            summary = next(s for s in data['sheetSummary'] if s['name'] == 'mPs')
            self.assertEqual(summary['fields'], ['PLATFORM', 'pPn', 'mps'])
            self.assertEqual(summary['rows'], 2)

    def test_actual_xlsx_handles_case_lists_blank_plans_and_localized_date(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'export.xlsx'
            write_export(path, export_sheets())
            original = path.read_bytes()
            config = load_config()
            config['dataSource']['path'] = str(path)
            data = build_payload(config=config)
            self.assertEqual(data['borrow'][0]['date'], '2026-09-24')
            self.assertEqual(data['borrow'][0]['eqp'], 'MACHINE1')
            self.assertEqual(data['pm'], [])
            self.assertEqual(data['constraints'], [])
            self.assertEqual(data['recipes'][0]['cards'], ['CARD1', 'CARD2', 'CARD3', 'CARD4', 'CARD5'])
            self.assertEqual([row['ppn'] for row in data['mps']], ['PRODUCTA', 'PRODUCTB'])
            result = run(data)
            self.assertEqual(result['metrics']['completedWafers'], 45)
            self.assertEqual(result['metrics']['borrowScheduled'], 1)
            self.assertFalse([f for f in inspect_run({'input': data, 'result': result})[0]
                              if f['code'].startswith('R')])
            self.assertEqual(path.read_bytes(), original)

    def test_api_references_are_case_insensitive_without_mutating_input(self):
        original = platform_case()
        data = copy.deepcopy(original)
        data['flows'] = {k.lower(): [[op[0].lower(), str(op[1]).lower(), op[2]] for op in route]
                         for k, route in data['flows'].items()}
        data['mps'][0]['ppn'] = 'p-supplier'
        data['recipes'][0]['recipe'] = 'r'
        data['recipes'][0]['cards'] = ['c1', 'c2']
        for row in data['eqp']:
            row.update(id=row['id'].lower(), card=row.get('card', '').lower(), status='run')
        for row in data['lots']:
            row[1] = 'p-supplier'
        data['constraints'] = [{'type': 't5830', 'eqp': 'm1', 'recipe': 'r'}]
        data['pm'] = [{'eqp': 'm1', 'early': '2026-09-01', 'due': '2026-09-02',
                       'over': '2026-09-03', 'hours': 2}]
        data['borrow'] = [{'eqp': 'm2', 'date': '2026-09-01', 'hours': 2}]
        data['bakeResources']['ids'] = [s.lower() for s in data['bakeResources']['ids']]
        data['wip'] = [{'lotId': 'l0', 'lotKey': 'lot-000001', 'eqp': 'm1',
                        'card': 'c1', 'start': '2026-08-31', 'end': '2026-09-01'}]
        before = copy.deepcopy(data)
        validated = validate_schedule(data)
        result = run(data)
        self.assertEqual(data, before)
        self.assertEqual(validated['wip'][0]['lotId'], 'L0')
        self.assertEqual(result['metrics']['completedWafers'], 75)
        self.assertFalse([t for t in result['cpTasks'] if t['machine'] == 'M1'])
        self.assertFalse([f for f in inspect_run({'input': data, 'result': result})[0]
                          if f['code'].startswith('R')])

    def test_case_collisions_cannot_hide_equipment_or_conflicting_routes(self):
        data = platform_case()
        data['eqp'].append(dict(data['eqp'][0], id='m1'))
        with self.assertRaisesRegex(ValueError, '设备编号'):
            validate_schedule(data)
        data = platform_case()
        data['flows']['p'] = [['CP1', 'OTHER', 1]]
        with self.assertRaisesRegex(ValueError, '内容冲突'):
            normalize_platforms(data)
        with self.assertRaisesRegex(ValueError, 'Sheet 匹配不唯一'):
            rows_for([('MPS', []), ('m_p_s', [])], 'MPS')

    def test_case_variant_lot_ids_keep_display_and_require_unambiguous_wip(self):
        data = platform_case()
        data['lots'][0][0] = 'SampleLot'
        data['lots'][1][0] = 'samplelot'
        data['wip'] = [{'lotId': 'SAMPLELOT'}]
        with self.assertRaisesRegex(ValueError, '对应多行'):
            validate_schedule(data)
        data['wip'][0]['lotKey'] = 'lot-000002'
        result = run(data)
        self.assertEqual(result['wip'][0]['lotId'], 'samplelot')
        self.assertIn('SampleLot', [lot['id'] for lot in result['lots']])
        self.assertNotIn('LOT-000002', [lot['key'] for lot in result['lots']])

    def test_empty_optional_sheet_is_allowed_but_nonempty_bad_header_is_rejected(self):
        config = load_config()
        sheets = export_sheets()
        sheets[-2] = ('PM计划', [['Not an equipment column'], ['Machine1']])
        with patch('server_core.read_workbook', return_value=sheets), self.assertRaisesRegex(ValueError, '字段映射不存在'):
            build_payload(config=config)

    def test_date_builtin_and_explicit_override_are_distinguished(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'export.xlsx'
            write_export(path, export_sheets())
            self.assertEqual(dict(read_workbook(path))['借机计划'][1][0], '2026-09-24')
            with zipfile.ZipFile(path) as archive:
                files = {name: archive.read(name) for name in archive.namelist()}
            files['xl/styles.xml'] = files['xl/styles.xml'].replace(b'<cellXfs>',
                b'<numFmts><numFmt numFmtId="58" formatCode="0"/></numFmts><cellXfs>')
            with zipfile.ZipFile(path, 'w') as archive:
                for name, value in files.items():
                    archive.writestr(name, value)
            self.assertEqual(dict(read_workbook(path))['借机计划'][1][0], '46289')


if __name__ == '__main__':
    unittest.main()
