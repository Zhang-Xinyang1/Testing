"""Audit parity with the archived loader, plus aggregation and read-only guarantees."""
import copy
import csv
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'backend'), str(ROOT / 'tools')]
from build_case import write_workbook
from server_core import build_payload, load_config
from validate_excel import Audit, main, read_for_audit, write_reports
from package_excel_validator import build


def case():
    cfg = load_config()
    cfg['dataSource']['month'] = '2026-09'
    cfg['dataSource']['sheetMap'].update(lots='Lots', wip='WIP', weekly='Weekly')
    cfg['fieldMap'].update({
        'Lots': {'lotId':'LotID','ppn':'PROD','platform':'Platform','wafer':'Wafer Qty',
                 'release':'Release','weeklyDue':'Weekly Due','monthlyDue':'Monthly Due','priority':'Priority'},
        'WIP': {'lotId':'LotID','lotKey':'LotKey','eqp':'EQPID','chamber':'Chamber','card':'Card','start':'Start','end':'End'},
        'Weekly': {'ppn':'PPN','week':'Week','start':'Start','end':'End','target':'Target'},
    })
    sheets = [
        ('MPS', [['Platform','PPN','MPS'], ['T5830','P-1',25], ['t5830','p-2',25]]),
        ('EQP', [['TYPE','EQPID','proberID','Current ProbeCard','Current Recipe','Status'],
                 ['t5830','M1','PROBE1','C1','R','RUN'], ['T5830','M2','PROBE2','C1','R','RUN'],
                 ['OVEN','OVEN1','','','','RUN']]),
        ('针卡', [['Recipe','ProbeCard List'], ['R','C1;c1,C2']]),
        ('测试flow&工时', [['PROD','STEP','RECIPE','Time'], ['P','CP1','R',2], ['p','BAKE','210C24H',''], ['P','CP2','r',3]]),
        ('借机计划', [['DATE','EQPID','借机时数']]),
        ('PM计划', [['EQP ID','TargetKind','Early Due','Due Date','Over Due','Estimated Hours','Start'],
                    ['m1','EQP','2026-09-02','2026-09-02','2026-09-02',4,'2026-09-02 08:00'],
                    ['probe1','prober','2026-09-02','2026-09-02','2026-09-02',4,'2026-09-02 10:00']]),
        ('约束条件', [['TYPE','EQPID','Constraint Recipe']]),
        ('Lots', [['LotID','PROD','Platform','Wafer Qty','Release','Weekly Due','Monthly Due','Priority'],
                  ['P-vendor','P','t5830',25,'','','',''], [],
                  ['P-vendor','p','T5830',25,'2026/9/1','2026-09-07','2026-10-01',1]]),
        ('WIP', [['LotID','LotKey','EQPID','Chamber','Card','Start','End']]),
        ('Weekly', [['PPN','Week','Start','End','Target'], ['P','W1','2026-09-01','2026-09-07',50]]),
    ]
    return cfg, sheets


def evaluate(cfg, sheets):
    return Audit(cfg).run(copy.deepcopy(sheets))


class ExcelAuditTests(unittest.TestCase):
    def test_allowed_repeated_types_lots_vendors_case_and_pm_union(self):
        cfg, sheets = case()
        audit = evaluate(cfg, sheets)
        self.assertFalse(audit.incomplete, audit.issues)
        self.assertFalse([i for i in audit.issues if i['severity']=='ERROR'], audit.issues)
        self.assertTrue(any(i['code']=='PM_MERGED' for i in audit.issues))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'good.xlsx'; write_workbook(path,sheets)
            audit.config['dataSource']['path'] = str(path)
            audit.engine_check()
            self.assertTrue(audit.engine_passed, audit.issues)

    def test_all_independent_errors_continue_to_last_sheet_and_physical_row(self):
        cfg, sheets = case(); tables = dict(sheets)
        tables['MPS'][1] = ['K8000','P-1','bad']
        tables['EQP'][2][1] = 'm1'
        tables['测试flow&工时'][1][3] = 0
        tables['测试flow&工时'][3][2] = 'UNKNOWN'
        tables['PM计划'][1] = ['UNKNOWN','','bad-date','','2026-09-01',-1,'bad-start']
        tables['借机计划'].append(['bad-borrow-date','PROBE1',25])
        tables['Lots'][3][3] = 26
        tables['Lots'][3][4] = '2026-02-30'
        tables['WIP'].append(['P-vendor','','UNKNOWN','','','bad-start','bad-end'])
        tables['Weekly'][1][3] = 'bad-final-table-date'
        audit = evaluate(cfg,sheets)
        self.assertFalse(audit.incomplete, audit.issues)
        codes = {i['code'] for i in audit.issues if i['severity']=='ERROR'}
        self.assertTrue({'NUMBER','PLATFORM','DUPLICATE_KEY','CP_TIME','RECIPE_REFERENCE','DATE','PM_TARGET','BORROW_EQP','WIP_LOT','WIP_EQP'} <= codes, codes)
        self.assertTrue(any(i['table']=='weekly' and i['code']=='DATE' for i in audit.issues))
        self.assertTrue(any(i['table']=='lots' and i['row']==4 and i['column']=='E' and i['code']=='DATE' for i in audit.issues))

    def test_missing_and_ambiguous_headers_do_not_stop_other_tables(self):
        cfg,sheets=case(); tables=dict(sheets)
        tables['MPS'][0][2]='Not MPS'
        tables['EQP'][0].append('eqp_id')
        tables['PM计划'][1][2]='invalid'
        audit=evaluate(cfg,sheets)
        self.assertFalse(audit.incomplete,audit.issues)
        self.assertTrue({'COLUMN_MISSING','HEADER_AMBIGUOUS','DATE','DEPENDENCY'} <= {i['code'] for i in audit.issues})

    def test_unused_duplicate_headers_and_candidate_names_are_allowed(self):
        cfg,sheets=case()
        cfg['fieldMap']['MPS']['mps']=['MissingFirst','mPs']
        dict(sheets)['MPS'][0] += ['Density','density','Core Volt','Core Volt','Tech','Tech']
        audit=evaluate(cfg,sheets)
        self.assertFalse([i for i in audit.issues if i['severity']=='ERROR'],audit.issues)
        self.assertEqual(sum(i['code']=='UNUSED_DUPLICATE' for i in audit.issues),3)

    def test_old_config_auto_reads_optional_pm_and_prober_fields(self):
        cfg,sheets=case()
        cfg['fieldMap']['EQP'].pop('proberId')
        for key in ('targetKind','start'): cfg['fieldMap']['PM计划'].pop(key)
        audit=evaluate(cfg,sheets)
        self.assertTrue(any(i['code']=='PM_MERGED' for i in audit.issues))
        self.assertFalse([i for i in audit.issues if i['severity']=='ERROR'],audit.issues)

    def test_effective_override_is_distinguished_from_excel_only_without_writes(self):
        cfg,sheets=case(); original=copy.deepcopy(cfg)
        cfg['scheduling']['eqp']=[{'id':'M1','type':'CP-A','status':'RUN'}]
        saved=copy.deepcopy(cfg)
        effective=evaluate(cfg,sheets)
        self.assertTrue(any(i['code']=='PLATFORM' and '手工机台' in i['message'] for i in effective.issues))
        direct=Audit(cfg,excel_only=True).run(copy.deepcopy(sheets))
        self.assertFalse([i for i in direct.issues if i['severity']=='ERROR'],direct.issues)
        self.assertEqual(cfg,saved)
        self.assertEqual(original['dataSource'],cfg['dataSource'])

    def test_no_twenty_row_truncation(self):
        cfg,sheets=case(); tables=dict(sheets)
        tables['PM计划'][1:]=[['M1','','invalid','2026-09-02','2026-09-02',4,''] for _ in range(125)]
        audit=evaluate(cfg,sheets)
        dates=[i for i in audit.issues if i['code']=='DATE' and i['table']=='pm']
        self.assertEqual(len(dates),125)
        self.assertEqual(dates[-1]['row'],126)

    def test_wip_dates_chambers_shared_pm_conflicts_and_week_overlap(self):
        cfg,sheets=case(); tables=dict(sheets)
        tables['WIP'] += [['P-vendor','LOT-000001','M1','','C1','2026-09-02 09:00','2026-09-02 11:00'],
                          ['P-vendor','LOT-000002','M1','','C1','2026-09-02 10:00','2026-09-02 12:00']]
        tables['Weekly'].append(['P','W2','2026-09-07','2026-09-14',5])
        audit=evaluate(cfg,sheets)
        self.assertTrue({'WIP_CONFLICT','PM_CONFLICT','WEEK_OVERLAP'} <= {i['code'] for i in audit.issues})

    def test_loader_default_semantics_do_not_turn_into_false_errors(self):
        for scenario in ('no-bake-config','oven-fixed-four','weekly-fraction','weekly-text','priority-fraction','ignored-rows','borrow-unused-start','status-fallback','header-string','month-short'):
            cfg,sheets=case(); tables=dict(sheets)
            if scenario=='no-bake-config':
                cfg['scheduling'].pop('bakeResources'); tables['EQP'].pop()
            elif scenario=='oven-fixed-four': cfg['scheduling']['bakeResources']['chambers']=1
            elif scenario=='weekly-fraction': tables['Weekly'][1][4]=2.8
            elif scenario=='weekly-text': tables['Weekly'][1][4]='not-a-number'
            elif scenario=='priority-fraction': tables['Lots'][1][7]=1.7
            elif scenario=='ignored-rows':
                tables['MPS'].append(['bad-platform','','bad-quantity'])
                tables['测试flow&工时'].append(['','unknown','bad','bad-time'])
            elif scenario=='borrow-unused-start':
                tables['借机计划'][0].append('Start');tables['借机计划'].append(['2026-09-10','M1',1,'bad-date'])
                cfg['fieldMap']['借机计划']['start']='Start'
            elif scenario=='status-fallback': cfg['fieldMap']['EQP'].pop('status')
            elif scenario=='header-string': cfg['dataSource']['headerRow']='1'
            elif scenario=='month-short': cfg['dataSource']['month']='2026-9'
            with self.subTest(scenario=scenario),tempfile.TemporaryDirectory() as folder:
                path=Path(folder)/'case.xlsx';write_workbook(path,sheets);cfg['dataSource']['path']=str(path)
                build_payload(config=cfg)
                audit=evaluate(cfg,sheets)
                self.assertFalse([i for i in audit.issues if i['severity']=='ERROR'],audit.issues)

    def test_formula_missing_cache_and_bad_cell_do_not_hide_other_sheets(self):
        cfg,sheets=case()
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'cells.xlsx';write_workbook(path,sheets)
            with ZipFile(path) as z: contents={n:z.read(n) for n in z.namelist()}
            # First MPS value is an uncached formula; final weekly date is invalid.
            import re
            contents['xl/worksheets/sheet1.xml']=re.sub(rb'<c r="C2"[^>]*>.*?</c>',b'<c r="C2"><f>1+1</f></c>',contents['xl/worksheets/sheet1.xml'])
            contents['xl/worksheets/sheet10.xml']=contents['xl/worksheets/sheet10.xml'].replace(b'2026-09-07',b'bad-date')
            with ZipFile(path,'w') as z:
                for n,v in contents.items():z.writestr(n,v)
            audit=Audit(cfg);workbook=read_for_audit(path,audit);audit.run(workbook)
            self.assertFalse(audit.incomplete,audit.issues)
            self.assertTrue(any(i['code']=='FORMULA_EMPTY' for i in audit.issues))
            self.assertTrue(any(i['code']=='NUMBER' and i['table']=='mps' and i['severity']=='ERROR' for i in audit.issues))
            self.assertTrue(any(i['code']=='DATE' and i['table']=='weekly' for i in audit.issues))

    def test_cli_report_outputs_exit_codes_and_input_immutability(self):
        cfg,sheets=case()
        with tempfile.TemporaryDirectory() as folder:
            folder=Path(folder);path=folder/'data.xlsx';config_path=folder/'config.json'
            write_workbook(path,sheets);cfg['dataSource']['path']=str(path)
            config_path.write_text(json.dumps(cfg),encoding='utf-8')
            before=(path.read_bytes(),config_path.read_bytes())
            output=io.StringIO()
            with redirect_stdout(output):code=main(['--config',str(config_path),'--output',str(folder/'reports')])
            self.assertEqual(output.getvalue(),'')
            self.assertEqual(code,0)
            self.assertEqual(before,(path.read_bytes(),config_path.read_bytes()))
            report_path=next((folder/'reports').glob('*/report.json'))
            report=json.loads(report_path.read_text(encoding='utf-8'))
            self.assertTrue(report['loaderPassed']);self.assertFalse(report['incomplete'])
            audit=evaluate(cfg,sheets);audit.add('WARNING','CSV_TEST','message',value='=1+1')
            out,_=write_reports(audit,folder/'csv')
            with (out/'issues.csv').open(encoding='utf-8-sig',newline='') as f:
                self.assertEqual(list(csv.DictReader(f))[-1]['value'],"'=1+1")
            with redirect_stdout(io.StringIO()):
                self.assertEqual(main(['--config',str(config_path),'--excel',str(folder/'missing.xlsx'),'--output',str(folder/'reports')]),2)
            dict(sheets)['Weekly'][1][2]='invalid'
            write_workbook(path,sheets)
            with redirect_stdout(io.StringIO()):
                self.assertEqual(main(['--config',str(config_path),'--output',str(folder/'reports')]),1)

    def test_portable_script_is_standalone_silent_and_reports_bad_arguments(self):
        cfg,sheets=case()
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory); script=build(folder/'check.py')
            path=folder/'data.xlsx';write_workbook(path,sheets)
            cfg['dataSource']['path']='data.xlsx'
            (folder/'config.json').write_text(json.dumps(cfg),encoding='utf-8')
            for broken in (False,True):
                if broken:
                    dict(sheets)['Weekly'][1][2]='invalid-date';write_workbook(path,sheets)
                result=subprocess.run([sys.executable,str(script),'--config',str(folder/'config.json'),'--output',str(folder/'reports')],cwd=folder,capture_output=True,timeout=30)
                self.assertEqual(result.returncode,1 if broken else 0,result.stderr)
                self.assertEqual(result.stdout,b'');self.assertEqual(result.stderr,b'')
            result=subprocess.run([sys.executable,str(script),'--nonsense','--output',str(folder/'bad-args')],cwd=folder,capture_output=True,timeout=30)
            self.assertEqual(result.returncode,2)
            self.assertEqual(result.stdout,b'');self.assertEqual(result.stderr,b'')
            report=json.loads(next((folder/'bad-args').glob('*/report.json')).read_text(encoding='utf-8'))
            self.assertEqual(report['issues'][0]['code'],'ARGUMENTS')

    def test_malformed_config_and_unreadable_sheet_produce_reports(self):
        for bad in (None, [], {'dataSource':[], 'fieldMap':[], 'scheduling':None}):
            audit=Audit(bad).run([])
            self.assertTrue(audit.report()['counts']['ERROR'])
        cfg,sheets=case()
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'bad-sheet.xlsx';write_workbook(path,sheets)
            with ZipFile(path) as z: contents={n:z.read(n) for n in z.namelist()}
            contents['xl/worksheets/sheet1.xml']=b'<broken'
            contents['xl/worksheets/sheet10.xml']=contents['xl/worksheets/sheet10.xml'].replace(b'2026-09-07',b'bad-date')
            with ZipFile(path,'w') as z:
                for n,v in contents.items():z.writestr(n,v)
            audit=Audit(cfg);workbook=read_for_audit(path,audit);audit.run(workbook)
            self.assertTrue({'SHEET_READ','DATE'} <= {i['code'] for i in audit.issues})


if __name__=='__main__':unittest.main()
