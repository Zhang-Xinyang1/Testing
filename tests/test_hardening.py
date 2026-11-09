import copy
import math
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from test_platforms import platform_case
from test_scheduler import fixture
from build_case import write_workbook
from build_data import clean_rows, num, read_workbook
from diagnostics import inspect_run, ms
from input_rules import flow_product, parse_datetime
from scheduler import HOUR, MIN, parse_date, run
from server_core import build_payload, calendar_issues, load_config, resolve_column, validate_schedule


class HardeningTests(unittest.TestCase):
    def test_dates_are_strict_and_consistent_with_offsets(self):
        for text in ('2026-9-1T08:00:00+08:00', '2026/9/1 08:00+0800', '2026-09-01T00:00Z'):
            stamp = parse_date(text)
            self.assertEqual(stamp, parse_datetime(text).timestamp() * 1000)
            self.assertEqual(stamp, ms(text))
            self.assertEqual(stamp, parse_date('2026-09-01T00:00:00Z'))
        self.assertEqual(parse_date('2026-09-01T00:00:00.0005+08:00'),
                         ms('2026-09-01T00:00:00.0005+08:00'))
        for text in ('2026-9-1 garbage', '2026-9-1T08:99', '2026-09-01T08:00+08:99', '2026-02-30'):
            for parser in (parse_date, parse_datetime, ms):
                with self.subTest(text=text, parser=parser), self.assertRaises(ValueError):
                    parser(text)

    def test_invalid_api_numbers_and_references_are_rejected(self):
        for field, value in [('time', float('nan')), ('time', float('inf')), ('time', -1),
                             ('days', 0), ('days', True), ('mps', -1), ('release', '2026-9-1oops')]:
            data = platform_case()
            if field == 'time': data['flows']['P'][0][2] = value
            if field == 'days': data['meta']['days'] = value
            if field == 'mps':
                data['mps'][0]['mps'] = 101
                data['mps'].append({'ppn': 'P-vendor', 'platform': 'T5830', 'mps': value})
            if field == 'release': data['lots'][0][4] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                validate_schedule(data)
        data = platform_case()
        data['recipes'].append(copy.deepcopy(data['recipes'][0]))
        with self.assertRaisesRegex(ValueError, 'Recipe 名称'):
            validate_schedule(data)
        data = platform_case()
        data['borrow'] = [{'eqp': 'OVEN-1', 'date': '2026-09-01', 'hours': 2}]
        with self.assertRaisesRegex(ValueError, '不适用机台'):
            validate_schedule(data)
        data = platform_case()
        data['constraints'] = [{'eqp': 'MISSING', 'recipe': 'R'}]
        with self.assertRaisesRegex(ValueError, '未知 CP 机台'):
            validate_schedule(data)

    def test_explicit_vendor_ppn_resolves_before_arbitrary_lot_id(self):
        data = platform_case()
        data['lots'][0][:2] = ['BATCH-001', 'P-VENDOR']
        validate_schedule(data)
        result = run(data, {'algorithm': 'greedy', 'bakeWindowHours': 0})
        self.assertEqual(result['lots'][0]['prod'], 'P')
        self.assertEqual(flow_product('Q-01', 'P-VENDOR', {'P': [], 'Q': []}), 'P')

    def test_duplicate_wip_requires_row_key_and_excludes_only_selected_row(self):
        data = platform_case()
        data['lots'] = data['lots'][:2]
        data['lots'][1][0] = data['lots'][0][0]
        data['lots'][1][3] = 1
        data['mps'][0]['mps'] = 26
        data['wip'] = [{'lotId': data['lots'][0][0], 'eqp': ''}]
        with self.assertRaisesRegex(ValueError, '对应多行'):
            validate_schedule(data)
        with self.assertRaisesRegex(ValueError, '对应多行'):
            run(data)
        data['wip'][0]['lotKey'] = 'LOT-000001'
        before = copy.deepcopy(data)
        validate_schedule(data)
        result = run(data, {'bakeWindowHours': 0})
        self.assertEqual(data, before)
        self.assertEqual([lot['key'] for lot in result['lots']], ['LOT-000002'])
        self.assertEqual(result['metrics']['wipWafers'], 25)
        self.assertEqual(result['metrics']['completedWafers'], 1)
        codes = {finding['code'] for finding in inspect_run({'input': data, 'result': result})[0]}
        self.assertNotIn('R06', codes)
        data['wip'].append(dict(data['wip'][0], lotKey='LOT-000002'))
        self.assertEqual(len(validate_schedule(data)['wip']), 2)

    def test_wip_card_is_used_after_release_and_validator_does_not_mutate(self):
        data = fixture(2)
        data['eqp'][1]['status'] = 'LOST'
        data['recipes'][0]['cards'] = ['C1']
        data['flows']['P'] = [['CP1', 'R', 1]]
        data['wip'] = [{'lotId': 'L0', 'eqp': 'M1', 'card': 'C2',
                        'start': '2026/9/1', 'end': '2026/9/1 01:00'}]
        result = run(data, {'algorithm': 'greedy'})
        self.assertEqual(result['cpTasks'][0]['setup'], 5 * MIN)
        findings, _ = inspect_run({'input': data, 'result': result})
        self.assertNotIn('R10', {finding['code'] for finding in findings})
        result['cpTasks'][0]['blockStart'] = result['cpTasks'][0]['start']
        self.assertIn('R10', {f['code'] for f in inspect_run({'input': data, 'result': result})[0]})
        data = platform_case()
        data['wip'] = [{'lotId': 'L0', 'eqp': 'M1', 'start': '2026/9/1', 'end': '2026/9/2'}]
        before = copy.deepcopy(data)
        normalized = validate_schedule(data)
        self.assertEqual(data, before)
        self.assertEqual(normalized['wip'][0]['card'], 'C1')

    def test_fixed_calendar_appointments_precede_flexible_plans_and_keep_ids(self):
        data = fixture(1)
        data['pm'] = [
            {'eqp': 'M1', 'early': '2026-09-01', 'due': '2026-09-01', 'over': '2026-09-01', 'hours': 2},
            {'eqp': 'M1', 'early': '2026-09-01', 'due': '2026-09-01', 'over': '2026-09-01', 'hours': 2,
             'start': '2026-09-01T00:00:00+08:00'}]
        self.assertEqual(calendar_issues(data), [])
        result = run(data, {'algorithm': 'greedy', 'bakeWindowHours': 0})
        records = {r['id']: r for r in result['pmRecords']}
        self.assertEqual(records['PM-02']['start'], result['start'])
        self.assertEqual(records['PM-01']['start'], result['start'] + 2 * HOUR)
        data['pm'] = []
        data['borrow'] = [
            {'eqp': 'M1', 'date': '2026-09-01', 'hours': 2},
            {'eqp': 'M1', 'date': '2026-09-01', 'hours': 2, 'start': '2026-09-01T00:00:00+08:00'}]
        result = run(data, {'algorithm': 'greedy', 'bakeWindowHours': 0})
        records = {r['id']: r for r in result['borrows']}
        self.assertEqual(records['BR-02']['start'], result['start'])
        self.assertEqual(records['BR-01']['start'], result['start'] + 2 * HOUR)

    def test_missing_or_empty_configured_lot_sheet_never_generates_default_lots(self):
        config = load_config()
        config['dataSource']['sheetMap']['lots'] = 'Lot明细'
        config['fieldMap']['Lot明细'] = {'lotId': 'LotID', 'wafer': 'Wafer Qty'}
        with self.assertRaisesRegex(ValueError, 'Sheet 不存在'):
            build_payload(config=config)
        from server_core import read_source
        workbook = read_workbook(read_source(config['dataSource']['path']))
        with patch('server_core.read_workbook', return_value=workbook + [('Lot明细', [['LotID', 'Wafer Qty']])]):
            with self.assertRaisesRegex(ValueError, '没有有效记录'):
                build_payload(config=config)

    def test_numeric_ranges_and_ambiguous_columns_are_not_silently_truncated(self):
        for value in ('10-20', '1/2', 'oops25', '12.5.3'):
            self.assertTrue(math.isnan(num(value, float('nan'))), value)
        self.assertEqual(num('1,000 wafer'), 1000)
        self.assertEqual(num('18 min/wafer'), 18)
        with self.assertRaisesRegex(ValueError, '重复列名'):
            clean_rows([['MPS', 'MPS'], [100, 200]])
        with self.assertRaisesRegex(ValueError, '不唯一'):
            resolve_column(['EQPID', 'EQP ID'], 'EQPID')

    def test_xlsx_row_numbers_1904_dates_and_literal_units(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'dates.xlsx'
            write_workbook(path, [('Dates', [['Value'], [0.5]])])
            with zipfile.ZipFile(path) as archive:
                files = {name: archive.read(name) for name in archive.namelist()}
            sheet = files['xl/worksheets/sheet1.xml'].decode().replace('r="1"', 'r="3"').replace('r="2"', 'r="4"')
            sheet = sheet.replace('A1', 'A3').replace('A2', 'A4').replace('r="A4"', 'r="A4" s="0"')
            files['xl/worksheets/sheet1.xml'] = sheet.encode()
            wb = files['xl/workbook.xml'].decode().replace('<sheets>', '<workbookPr date1904="1"/><sheets>')
            files['xl/workbook.xml'] = wb.encode()
            files['xl/styles.xml'] = b'<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><cellXfs><xf numFmtId="14"/></cellXfs></styleSheet>'
            with zipfile.ZipFile(path, 'w') as archive:
                for name, value in files.items(): archive.writestr(name, value)
            rows = read_workbook(path)[0][1]
            self.assertEqual(rows[0][0], '')
            self.assertEqual(rows[2][0], 'Value')
            self.assertEqual(rows[3][0], '1904-01-01 12:00:00')
            files['xl/styles.xml'] = b'<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><numFmts><numFmt numFmtId="164" formatCode="0&amp;quot;min&amp;quot;"/></numFmts><cellXfs><xf numFmtId="164"/></cellXfs></styleSheet>'.replace(b'&amp;quot;', b'&quot;')
            with zipfile.ZipFile(path, 'w') as archive:
                for name, value in files.items(): archive.writestr(name, value)
            self.assertEqual(read_workbook(path)[0][1][3][0], '0.5')

    def test_platform_summary_counts_restricted_lots_and_month_end_is_in_week(self):
        data = fixture(2)
        data['lots'][0][2], data['lots'][1][2] = 'A', 'B'
        data['flows']['P'] = [['CP1', 'R', 1]]
        result = run(data, {'algorithm': 'greedy'})
        self.assertEqual(result['platformStats'][0]['completed'], 50)
        data = fixture(1)
        data['meta']['days'] = 1
        data['flows']['P'] = [['CP1', 'R', 1440 / 25]]
        data['weekly'] = [{'ppn': 'P', 'platform': 'A;B', 'week': 'W1',
                           'start': '2026-09-01', 'end': '2026-09-01', 'target': 25}]
        result = run(data, {'algorithm': 'greedy'})
        self.assertEqual(result['metrics']['completedWafers'], 25)
        self.assertEqual(result['weekly'][0]['plannedWeek'], 25)


if __name__ == '__main__':
    unittest.main()
