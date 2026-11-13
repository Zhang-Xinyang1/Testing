import copy
import tempfile
import unittest
from pathlib import Path

from test_import_case import export_sheets, write_export
from test_platforms import platform_case
from server_core import build_payload, load_config, validate_schedule
from legacy_case import build_payload, load_config


class DateInputTests(unittest.TestCase):
    def test_all_missing_date_fields_report_table_identity_and_field(self):
        data = platform_case()
        data['meta']['start'] = ''
        data['borrow'] = [{'eqp': 'M1', 'date': '', 'hours': 2}]
        data['pm'] = [{'eqp': 'M2', 'early': '', 'due': None, 'over': '', 'hours': 2}]
        data['wip'] = [{'lotId': 'L0', 'eqp': 'M1', 'start': '', 'end': ''}]
        data['weekly'] = [{'ppn': 'P', 'week': 'W1', 'start': '', 'end': '', 'target': 1}]
        data['lots'][1][4] = ''
        before = copy.deepcopy(data)
        with self.assertRaises(ValueError) as error:
            validate_schedule(data)
        message = str(error.exception)
        self.assertIn('共 10 项', message)
        for text in ('meta.start', '借机计划 第 1 条（eqp=M1）', '字段「DATE」 为空',
                     'PM计划 第 1 条（eqp=M2）', '字段「Early Due」 为空',
                     '字段「Due Date」 为空', '字段「Over Due」 为空',
                     '在制 第 1 条', '字段「预计释放」 为空', '周目标 第 1 条',
                     'Lot明细 第 2 条（lotId=L1）', '字段「Release Time」 为空'):
            self.assertIn(text, message)
        self.assertEqual(data, before)

    def test_valid_optional_empty_dates_remain_allowed(self):
        data = platform_case()
        data['pm'] = [{'eqp': 'M1', 'early': '2026-09-01', 'due': '2026-09-02',
                       'over': '2026-09-03', 'start': '', 'hours': 2}]
        data['borrow'] = [{'eqp': 'M2', 'date': '', 'start': '2026-09-04 08:00', 'hours': 2}]
        data['wip'] = [{'lotId': 'L0', 'eqp': '', 'start': '', 'end': ''}]
        validate_schedule(data)
        data['borrow'][0].update(date='2026-09-04', start='')
        validate_schedule(data)

    def test_invalid_values_are_shown_with_their_field(self):
        data = platform_case()
        data['borrow'] = [{'eqp': 'M1', 'date': '2026-02-30', 'hours': 2}]
        with self.assertRaises(ValueError) as error:
            validate_schedule(data)
        self.assertIn('字段「DATE」 无效', str(error.exception))
        self.assertIn('2026-02-30', str(error.exception))

    def test_import_error_reports_custom_sheet_column_and_override_origin(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'dates.xlsx'
            sheets = export_sheets()
            rows = sheets[4][1]
            rows[0][0], rows[1][0] = '借出日期', ''
            sheets[4] = ('设备预约', rows)
            write_export(path, sheets)
            config = load_config()
            config['dataSource'].update(path=str(path), displayName='日期样本.xlsx')
            config['dataSource']['sheetMap']['borrow'] = '设备预约'
            config['fieldMap']['设备预约'] = dict(config['fieldMap'].pop('借机计划'), date='借出日期')
            with self.assertRaises(ValueError) as error:
                build_payload(config=config)
            message = str(error.exception)
            for text in ('文件「日期样本.xlsx」', 'Sheet「设备预约」 第 1 条',
                         'eqp=MACHINE1', '字段「借出日期」 为空'):
                self.assertIn(text, message)
            config['scheduling']['borrow'] = [{'eqp': 'machine2', 'date': '', 'hours': 2}]
            with self.assertRaises(ValueError) as error:
                build_payload(config=config)
            message = str(error.exception)
            self.assertIn('手工配置「借机计划」 第 1 条（eqp=MACHINE2）', message)
            self.assertIn('字段「DATE」 为空', message)
            self.assertNotIn('Sheet「设备预约」', message)


if __name__ == '__main__':
    unittest.main()
