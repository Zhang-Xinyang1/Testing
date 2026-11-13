import os
import sys
import unittest
import re
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'backend'))
sys.path.insert(0, os.path.join(ROOT, 'tools'))
from server_core import (build_payload, load_config, parse_datetime,
                         route_patterns, validate_schedule)
import copy
from build_data import read_workbook, clean_rows

from legacy_case import build_payload, load_config

class CaseRelationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data=build_payload()
        cls.field_map=load_config()['fieldMap']
        cls.sheets={n:clean_rows(rows)[1] for n,rows in read_workbook(os.path.join(ROOT,load_config()['dataSource']['path']))}

    def test_counts_and_need_units(self):
        d=self.data
        self.assertEqual(len(d['mps']),72)
        self.assertEqual(sum(m['mps'] for m in d['mps']),11000)
        self.assertEqual(len(d['lots']),sum((m['mps']+24)//25 for m in d['mps']))
        self.assertEqual(sum(l[3] for l in d['lots']),11000)
        self.assertEqual(d['meta']['mpsUnit'],'wafer')
        self.assertTrue(all(2<=m['mps']<=800 for m in d['mps']))
        self.assertEqual(len(d['borrow']),119)

    def test_lot_wafer_bounds_and_product_totals_are_enforced(self):
        for invalid in (0,26,1.5,None):
            d=copy.deepcopy(self.data);d['lots'][0][3]=invalid
            with self.assertRaisesRegex(ValueError,'wafer'):
                validate_schedule(d)
        d=copy.deepcopy(self.data);d['lots'][0][3]-=1
        with self.assertRaisesRegex(ValueError,'合计'):
            validate_schedule(d)

    def test_actual_lot_sizes_override_full_25_assumption(self):
        from unittest.mock import patch
        cfg=load_config()
        cfg['dataSource']['sheetMap']['lots']='Lot明细'
        cfg['fieldMap']['Lot明细']={'lotId':'LotID','ppn':'PROD','wafer':'Wafer Qty'}
        rows=[['LotID','PROD','Wafer Qty']]
        for m in self.data['mps']:
            sizes=[l[3] for l in self.data['lots'] if l[1]==m['ppn']]
            if m['ppn']=='PPN01':sizes=[10,15]+sizes[1:]
            rows.extend([[m['ppn']+'-A'+str(i),m['ppn'],q] for i,q in enumerate(sizes)])
        workbook=read_workbook(os.path.join(ROOT,cfg['dataSource']['path']))+[('Lot明细',rows)]
        with patch('server_core.read_workbook',return_value=workbook):
            actual=build_payload(config=cfg)
        self.assertEqual(actual['meta']['lotSizing'],'actual')
        self.assertEqual([l[3] for l in actual['lots'] if l[1]=='PPN01'][:2],[10,15])
        self.assertEqual(sum(l[3] for l in actual['lots']),11000)
        self.assertEqual(len(actual['lots']),len(self.data['lots'])+1)

    def test_lot_id_prefix_and_duplicate_rows_are_accepted(self):
        from unittest.mock import patch
        cfg=load_config()
        cfg['dataSource']['sheetMap']['lots']='Lot明细'
        cfg['fieldMap']['Lot明细']={'lotId':'LotID','wafer':'Wafer Qty'}
        rows=[['LotID','Wafer Qty']]
        for index,lot in enumerate(self.data['lots']):
            lot_id=lot[0]+'-V1'
            if index==1:
                lot_id=self.data['lots'][0][0]+'-V1'
            rows.append([lot_id,lot[3]])
        workbook=read_workbook(os.path.join(ROOT,cfg['dataSource']['path']))+[('Lot明细',rows)]
        with patch('server_core.read_workbook',return_value=workbook):
            actual=build_payload(config=cfg)
        self.assertEqual(actual['meta']['lotSizing'],'actual')
        self.assertEqual(sum(l[3] for l in actual['lots']),11000)
        self.assertEqual(len({l[0] for l in actual['lots']}),len(actual['lots'])-1)
        self.assertTrue(all(l[1]==l[0].split('-',1)[0] for l in actual['lots']))

    def test_current_header_mapping_matches_workbook(self):
        summary={s['name']:s['fields'] for s in self.data['sheetSummary']}
        self.assertEqual(self.field_map['针卡']['cards'],'ProbeCard List')
        self.assertEqual(self.field_map['测试flow&工时']['time'],'Time')
        self.assertEqual(self.field_map['PM计划']['eqp'],'EQP ID')
        self.assertIn('ProbeCard List',summary['针卡'])
        self.assertIn('Time',summary['测试flow&工时'])
        self.assertIn('EQP ID',summary['PM计划'])

    def test_relaxed_header_and_number_matching(self):
        from unittest.mock import patch
        cfg=load_config()
        cfg['fieldMap']['MPS']={'platform':['Platform','平台'],
                                'ppn':['PPN','产品编号'],
                                'mps':['MPS','需求']}
        cfg['fieldMap']['针卡']['cards']=['ProbeCard List','ProberCard List']
        cfg['fieldMap']['测试flow&工时']['time']=['Time','TIME','时间']
        cfg['fieldMap']['PM计划']['eqp']=['EQP ID','EQPID','设备编号']
        workbook=[]
        for name,rows in read_workbook(os.path.join(ROOT,cfg['dataSource']['path'])):
            rows=[list(r) for r in rows]
            if name=='MPS':
                rows[0]=['platform','产品编号','需求','单位']
                for r in rows[1:]:
                    r[2]='%s wafer' % r[2]
            elif name=='针卡':
                rows[0]=['Recipe','ProberCard List']
            elif name=='测试flow&工时':
                rows[0]=['PROD','STEP','RECIPE','time']
                for r in rows[1:]:
                    if str(r[3]).strip():
                        r[3]='%s min' % r[3]
            elif name=='PM计划':
                rows[0]=['eqp_id','Early Due','Due Date','Over Due','Estimated Hours']
            workbook.append((name,rows))
        with patch('server_core.read_workbook',return_value=workbook):
            data=build_payload(config=cfg)
        self.assertEqual(len(data['mps']),72)
        self.assertEqual(sum(m['mps'] for m in data['mps']),11000)
        self.assertEqual(data['flows']['PPN01'][0][2],18)
        self.assertEqual(len(data['pm']),11)
        self.assertEqual(data['recipes'][0]['cards'],
                         ['CARD-T01','CARD-T02','CARD-T03','CARD-T04'])

    def test_duplicate_mps_rows_are_summed_by_ppn(self):
        from unittest.mock import patch
        cfg=load_config()
        workbook=[]
        total=0
        for name,rows in read_workbook(os.path.join(ROOT,cfg['dataSource']['path'])):
            rows=[list(r) for r in rows]
            if name=='MPS':
                total=int(float(rows[1][2]))
                rows[1][1]=str(rows[1][1])+'-1'
                rows[1][2]=total//2
                rows.append(['CP-B','PPN01-2',total-total//2,rows[1][3]])
            workbook.append((name,rows))
        with patch('server_core.read_workbook',return_value=workbook):
            data=build_payload(config=cfg)
        self.assertEqual(len(data['mps']),72)
        self.assertEqual(data['mps'][0]['ppn'],'PPN01')
        self.assertEqual(data['mps'][0]['mps'],total)
        self.assertEqual(data['mps'][0]['platform'],'CP-A;CP-B')
        self.assertEqual(sum(l[3] for l in data['lots']),11000)

    def test_parse_datetime_accepts_common_excel_text_formats(self):
        expected = parse_datetime('2026-09-01T00:00:00+08:00')
        for value in ('2026/9/1', '2026-9-1', '2026年9月1日',
                      '2026-09-01 00:00'):
            self.assertEqual(parse_datetime(value), expected)

    def test_three_cp_groups_and_28_chambers(self):
        cp=[e for e in self.data['eqp'] if 'OVEN' not in e['type']]
        self.assertEqual(len(cp),27)
        self.assertEqual(len({e['type'] for e in cp}),3)
        self.assertEqual(len(self.data['bakeResources']['ids']),7)
        self.assertEqual(self.data['bakeResources']['chambers'],4)
        for m in self.data['mps']:
            self.assertTrue(set(m['platform'].split(';'))<={e['type'] for e in cp})

    def test_31_recipes_62_card_types_no_stock_or_wafer_limit(self):
        d=self.data
        self.assertEqual(len(d['recipes']),31)
        self.assertEqual(len({c for r in d['recipes'] for c in r['cards']}),62)
        self.assertTrue(all(1<=len(r['cards'])<=4 for r in d['recipes']))
        self.assertTrue(all(isinstance(l[3],int) and 1<=l[3]<=25 for l in d['lots']))
        for m in d['mps']:
            self.assertEqual(sum(l[3] for l in d['lots'] if l[1]==m['ppn']),m['mps'])

    def test_route_preserves_source_rows_and_parses_recipe_times(self):
        time_field=self.field_map['测试flow&工时']['time']
        for m in self.data['mps']:
            rows=[r for r in self.sheets['测试flow&工时'] if r['PROD']==m['ppn']]
            ops=self.data['flows'][m['ppn']]
            self.assertEqual([r['STEP'] for r in rows],[o[0] for o in ops])
            for row,op in zip(rows,ops):
                if op[0]=='BAKE':
                    self.assertIn(op[1],('210C24H','250C24H'));self.assertEqual(op[2],24);self.assertFalse(row[time_field])
                elif op[0]=='QTIME1':
                    self.assertIn(op[1],('7','14','28'));self.assertEqual(op[2],int(op[1]));self.assertFalse(row[time_field])
                else:
                    self.assertIn(op[1],{r['recipe'] for r in self.data['recipes']});self.assertGreater(op[2],0)

    def test_seven_configured_route_patterns_and_current_case_subset(self):
        expected = {
            ('CP1','BAKE','CP2','BAKE','CP3','QTIME1','CP4'),
            ('CP1','BAKE','CP2','QTIME1','CP3'),
            ('CP1','BAKE','CP2','QTIME1','CP3','CP4'),
            ('CP1','BAKE','CP2','BAKE','CP3','CP4','QTIME1'),
            ('CP1','BAKE','CP2'),
            ('CP1','BAKE','CP2','BAKE','CP3'),
            ('CP1','BAKE','CP2','CP3'),
        }
        self.assertEqual({tuple(p) for p in route_patterns()}, expected)
        actual={tuple(op[0] for op in ops) for ops in self.data['flows'].values()}
        self.assertTrue(actual <= expected)
        self.assertIn(('CP1','BAKE','CP2','CP3'), expected)
        self.assertTrue(all(op[0]!='QTIME' for ops in self.data['flows'].values() for op in ops))

    def test_seventh_route_is_accepted(self):
        d=copy.deepcopy(self.data)
        product=d['mps'][0]['ppn']
        d['flows'][product]=[
            ['CP1','CP-R01',10],['BAKE','210C24H',None],
            ['CP2','CP-R02',10],['CP3','CP-R03',10],
        ]
        validate_schedule(d)

    def test_invalid_route_is_rejected_without_reordering(self):
        d=copy.deepcopy(self.data)
        product=d['mps'][0]['ppn']
        d['flows'][product][0],d['flows'][product][1]=d['flows'][product][1],d['flows'][product][0]
        with self.assertRaisesRegex(ValueError,'七种路线'):
            validate_schedule(d)

    def test_initial_installation_matches_recipe(self):
        candidates={r['recipe']:r['cards'] for r in self.data['recipes']}
        for e in self.data['eqp']:
            if 'OVEN' not in e['type']:
                self.assertEqual(e['status'],'RUN');self.assertIn(e['card'],candidates[e['recipe']])

    def test_borrow_and_pm_references(self):
        ids={e['id'] for e in self.data['eqp']}
        for b in self.data['borrow']:
            self.assertIn(b['eqp'],ids);self.assertTrue(.6<=b['hours']<=24);self.assertNotIn('generatedAt',b)
        self.assertEqual(self.data['planIssues'],[])

    def test_exact_seven_business_sheets(self):
        self.assertEqual(set(self.sheets),{'MPS','EQP','针卡','测试flow&工时','借机计划','PM计划','约束条件'})

if __name__=='__main__':unittest.main()
