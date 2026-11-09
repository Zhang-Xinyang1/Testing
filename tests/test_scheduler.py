import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'backend'))
from scheduler import (HOUR, MIN, compact_result, parse_date,
                       run as run_schedule)  # noqa: E402


def fixture(count=4):
    return {
        'meta': {'start': '2026-09-01T00:00:00+08:00', 'days': 30},
        'mps': [{'ppn': 'P', 'platform': 'A;B', 'mps': count * 25}],
        'eqp': [
            {'id': 'M1', 'type': 'A', 'status': 'RUN', 'card': 'C1'},
            {'id': 'M2', 'type': 'B', 'status': 'RUN', 'card': 'C1'},
            {'id': 'OVEN-1', 'type': 'OVEN', 'status': 'RUN'},
        ],
        'recipes': [{'recipe': 'R', 'cards': ['C1', 'C2']}],
        'flows': {'P': [['CP1', 'R', 10], ['BAKE', '210C24H', None],
                        ['QTIME1', '14', None], ['CP2', 'R', 20]]},
        'lots': [['L%d' % i, 'P', 'A;B', 25, '2026-09-01', 'W1',
                  '2026-09-07', '2026-10-01', 1] for i in range(count)],
        'pm': [], 'borrow': [], 'wip': [], 'weekly': [], 'constraints': [],
        'bakeResources': {'machines': 1, 'chambers': 1, 'ids': ['OVEN-1']},
        'platformType': {'A': 'A', 'B': 'B'}, 'cardType': {},
    }


def run(data, options=None):
    merged = {'bakeWindowHours': 0}
    merged.update(options or {})
    return run_schedule(data, merged)


class SchedulerRuleTests(unittest.TestCase):
    def test_arbitrary_route_order_bake_duration_and_qtime_minutes(self):
        result = run(fixture())
        for lot in result['lots']:
            self.assertEqual([task['step'] for task in lot['routeTasks']],
                             ['CP1', 'BAKE', 'QTIME1', 'CP2'])
            self.assertEqual(lot['routeTasks'][2]['end'] -
                             lot['routeTasks'][2]['start'], 14 * MIN)
            self.assertEqual(lot['complete'], lot['routeTasks'][3]['end'])

    def test_bake_occupies_28_hours_and_releases_lot_at_26_hours(self):
        result = run(fixture())
        self.assertTrue(result['batches'])
        for batch in result['batches']:
            self.assertEqual(batch['processStart'] - batch['start'], 2 * HOUR)
            self.assertEqual(batch['processEnd'] - batch['processStart'], 24 * HOUR)
            self.assertEqual(batch['end'] - batch['processEnd'], 2 * HOUR)
            self.assertEqual(batch['release'], batch['start'] + 26 * HOUR)

    def test_next_batch_heats_after_previous_cooldown(self):
        data = fixture(8)
        data['flows']['P'] = [['BAKE', '210C24H', None]]
        result = run(data)
        self.assertEqual(len(result['batches']), 2)
        self.assertEqual(result['batches'][1]['start'], result['batches'][0]['end'])
        self.assertEqual(result['batches'][1]['processStart'] -
                         result['batches'][0]['processStart'], 28 * HOUR)

    def test_four_lots_only_no_wafer_cap_and_cross_product_batch(self):
        data = fixture()
        data['mps'] = [{'ppn': 'P', 'platform': 'A', 'mps': 50},
                       {'ppn': 'Q', 'platform': 'A', 'mps': 50}]
        for index, lot in enumerate(data['lots']):
            if index > 1:
                lot[1] = 'Q'
        data['flows'] = {'P': [['BAKE', '210C24H', None]],
                         'Q': [['BAKE', '210C24H', None]]}
        result = run(data)
        self.assertEqual(len(result['batches']), 1)
        self.assertEqual(len(result['batches'][0]['lots']), 4)
        self.assertEqual(result['batches'][0]['wafers'], 100)
        self.assertEqual({lot['prod'] for lot in result['batches'][0]['lots']},
                         {'P', 'Q'})

    def test_different_bake_recipes_cannot_share_batch(self):
        data = fixture(2)
        data['mps'].append({'ppn': 'Q', 'platform': 'A', 'mps': 25})
        data['mps'][0]['mps'] = 25
        data['lots'][1][1] = 'Q'
        data['flows']['P'] = [['BAKE', '210C24H', None]]
        data['flows']['Q'] = [['BAKE', '250C24H', None]]
        self.assertEqual(len(run(data)['batches']), 2)

    def test_cp_card_type_unlimited_and_parallel_across_machines(self):
        data = fixture(2)
        data['flows']['P'] = [['CP1', 'R', 60]]
        result = run(data)
        self.assertEqual(result['cpTasks'][0]['start'], result['cpTasks'][1]['start'])
        self.assertEqual(result['cpTasks'][0]['card'], result['cpTasks'][1]['card'])

    def test_run_is_eligibility_and_non_run_machine_cannot_run(self):
        data = fixture(2)
        data['flows']['P'] = [['CP1', 'R', 10]]
        data['eqp'][0]['status'] = 'IDLE'
        result = run(data)
        self.assertTrue(all(task['machine'] == 'M2' for task in result['cpTasks']))
        self.assertFalse(any('缺少在制' in alert['title'] for alert in result['alerts']))

    def test_flexible_lot_moves_to_group_with_spare_monthly_capacity(self):
        data = fixture(2)
        data['meta']['days'] = 1
        data['mps'].append({'ppn': 'Q', 'platform': 'A', 'mps': 25})
        data['lots'].append(['Q0', 'Q', 'A', 25, '2026-09-01', 'W1',
                             '2026-09-07', '2026-10-01', 1])
        data['flows'] = {'P': [['CP1', 'R', 10]], 'Q': [['CP1', 'R', 50]]}
        result = run(data)
        p_tasks = [task for task in result['cpTasks'] if task['prod'] == 'P']
        q_tasks = [task for task in result['cpTasks'] if task['prod'] == 'Q']
        self.assertTrue(p_tasks)
        self.assertTrue(q_tasks)
        self.assertTrue(all(task['machine'] == 'M2' for task in p_tasks))
        self.assertTrue(all(task['machine'] == 'M1' for task in q_tasks))

    def test_alternative_platform_and_prohibited_recipe_choose_other_machine(self):
        data = fixture(1)
        data['flows']['P'] = [['CP1', 'R', 10]]
        data['constraints'] = [{'type': 'A', 'eqp': 'M1', 'recipe': 'R'}]
        self.assertEqual(run(data)['cpTasks'][0]['machine'], 'M2')

    def test_initial_installation_and_changed_card_charge_five_minutes(self):
        data = fixture(1)
        data['eqp'][1]['status'] = 'LOST'
        data['flows']['P'] = [['CP1', 'R', 10], ['CP2', 'R2', 10]]
        data['recipes'].append({'recipe': 'R2', 'cards': ['C2']})
        tasks = run(data)['cpTasks']
        self.assertEqual(tasks[0]['setup'], 0)
        self.assertEqual(tasks[1]['setup'], 5 * MIN)
        self.assertEqual(tasks[1]['end'] - tasks[1]['blockStart'],
                         10 * MIN * 25 + 5 * MIN)
    def test_oven_pm_blocks_all_four_chambers_inside_window(self):
        data = fixture()
        data['bakeResources']['chambers'] = 4
        data['flows']['P'] = [['BAKE', '210C24H', None]]
        data['pm'] = [{'eqp': 'OVEN-1', 'early': '2026-09-01',
                       'due': '2026-09-01', 'over': '2026-09-02', 'hours': 6}]
        result = run(data)
        self.assertEqual(len(result['pmRecords'][0]['resources']), 4)
        self.assertTrue(all(batch['start'] >= result['start'] + 6 * HOUR
                            for batch in result['batches']))

    def test_borrow_date_start_in_day_and_cross_midnight(self):
        data = fixture(1)
        data['flows']['P'] = [['CP1', 'R', 10]]
        data['pm'] = [{'eqp': 'M1', 'early': '2026-09-01', 'due': '2026-09-01',
                       'over': '2026-09-01', 'hours': 22}]
        data['borrow'] = [{'date': '2026-09-01', 'eqp': 'M1', 'hours': 6}]
        result = run(data)
        borrow = result['borrows'][0]
        self.assertEqual(borrow['eqp'], 'M1')
        self.assertEqual(borrow['start'], result['start'] + 22 * HOUR)
        self.assertEqual(borrow['end'], result['start'] + 28 * HOUR)

    def test_existing_production_preserved_and_borrow_suggests_same_machine(self):
        data = fixture(2)
        data['wip'] = [{'lotId': 'L0', 'eqp': 'M1', 'card': 'C1',
                        'start': '2026-09-01', 'end': '2026-09-03'}]
        data['borrow'] = [{'date': '2026-09-01', 'eqp': 'M1', 'hours': 4}]
        result = run(data)
        borrow = result['borrows'][0]
        self.assertEqual(borrow['status'], 'cancel')
        self.assertEqual(borrow['suggestion']['eqp'], 'M1')
        self.assertEqual(borrow['suggestion']['start'], parse_date('2026-09-03'))
        self.assertFalse(any(lot['id'] == 'L0' for lot in result['lots']))

    def test_route_may_omit_bake_and_qtime_entirely(self):
        data = fixture(1)
        data['flows']['P'] = [['CP3', 'R', 10]]
        result = run(data)
        self.assertEqual(result['batches'], [])
        self.assertEqual(result['lots'][0]['complete'], result['cpTasks'][0]['end'])

    def test_repeated_bake_uses_separate_recipes_and_release(self):
        data = fixture(1)
        data['flows']['P'] = [
            ['CP1', 'R', 10], ['BAKE', '210C24H', None], ['CP2', 'R', 10],
            ['BAKE', '250C24H', None], ['CP3', 'R', 10],
            ['QTIME1', '7', None], ['CP4', 'R', 10],
        ]
        result = run(data)
        lot = result['lots'][0]
        tasks = lot['routeTasks']
        self.assertEqual(len(result['batches']), 2)
        self.assertEqual(tasks[1]['recipe'], '210C24H')
        self.assertEqual(tasks[3]['recipe'], '250C24H')
        self.assertEqual(tasks[2]['start'], tasks[1]['end'])
        self.assertEqual(tasks[1]['resourceEnd'] - tasks[1]['end'], 2 * HOUR)
        self.assertGreaterEqual(tasks[3]['start'], tasks[1]['resourceEnd'])
        self.assertEqual(lot['complete'], tasks[6]['end'])

    def test_unavailable_capacity_emits_no_infinite_tasks(self):
        data = fixture()
        for equipment in data['eqp']:
            equipment['status'] = 'LOST'
        result = run(data)
        self.assertEqual(result['metrics']['unscheduledLots'], 4)
        self.assertEqual(result['cpTasks'], [])
        self.assertEqual(result['batches'], [])

    def test_scheduling_preserves_input_and_is_deterministic(self):
        data = fixture()
        before = json.dumps(data, ensure_ascii=False, sort_keys=True)
        first = run(data)
        second = run(data)
        self.assertEqual(json.dumps(data, ensure_ascii=False, sort_keys=True), before)
        self.assertEqual(first['productStats'], second['productStats'])

    def test_fair_weighted_dispatch_improves_smallest_attainment(self):
        data = fixture(12)
        data['meta']['days'] = 3
        data['mps'] = [{'ppn': 'P', 'platform': 'A', 'mps': 200},
                       {'ppn': 'Q', 'platform': 'A', 'mps': 100}]
        for index, lot in enumerate(data['lots']):
            if index >= 8:
                lot[1] = 'Q'
        data['flows'] = {'P': [['BAKE', '210C24H', None]],
                         'Q': [['BAKE', '210C24H', None]]}
        result = run(data)
        self.assertTrue(all(product['rate'] >= .5
                            for product in result['productStats']))

    def test_tiny_demand_product_receives_first_lot(self):
        data = fixture(102)
        data['meta']['days'] = 2
        data['mps'] = [{'ppn': 'P', 'platform': 'A', 'mps': 2500},
                       {'ppn': 'Q', 'platform': 'A', 'mps': 50}]
        for index, lot in enumerate(data['lots']):
            if index >= 100:
                lot[1] = 'Q'
        data['flows'] = {'P': [['BAKE', '210C24H', None]],
                         'Q': [['BAKE', '210C24H', None]]}
        result = run(data)
        self.assertTrue(all(product['completed'] > 0
                            for product in result['productStats']))
    def test_cp_processing_time_scales_with_actual_wafer_count(self):
        data = fixture(2)
        data['mps'][0]['mps'] = 26
        data['lots'][0][3] = 25
        data['lots'][1][3] = 1
        data['eqp'][1]['status'] = 'LOST'
        data['flows']['P'] = [['CP1', 'R', 10]]
        result = run(data)
        by_lot = {task['lotId']: task for task in result['cpTasks']}
        self.assertEqual(by_lot['L0']['duration'], 10 * MIN * 25)
        self.assertEqual(by_lot['L1']['duration'], 10 * MIN * 1)
        self.assertEqual(by_lot['L0']['timePerWafer'], 10)
        self.assertEqual(by_lot['L1']['timePerWafer'], 10)

    def test_wafer_completion_differs_from_lot_completion_for_tail_lot(self):
        data = fixture(2)
        data['meta']['days'] = 21
        data['mps'][0]['mps'] = 26
        data['lots'][0][3] = 25
        data['lots'][1][3] = 1
        data['eqp'][1]['status'] = 'LOST'
        data['flows']['P'] = [['CP1', 'R', 1200]]
        result = run(data)
        metrics = result['metrics']
        self.assertEqual(metrics['completedLots'], 1)
        self.assertEqual(metrics['completedWafers'], 25)
        self.assertEqual(metrics['totalWafers'], 26)
        self.assertEqual(result['productStats'][0]['rate'], 25 / 26)
        self.assertEqual(metrics['completionRate'], 25 / 26)
        self.assertEqual(sum(week['plannedWeek'] for week in result['weekly']), 25)
        self.assertEqual(result['cpTasks'][0]['duration'], 1200 * MIN * 25)
        self.assertEqual(result['cpTasks'][1]['duration'], 1200 * MIN * 1)

    def test_duplicate_mps_orders_are_merged_by_ppn_prefix(self):
        data = fixture(2)
        data['mps'] = [
            {'ppn': 'P-1', 'platform': 'A', 'mps': 25},
            {'ppn': 'P-2', 'platform': 'B', 'mps': 25},
        ]
        result = run(data)
        self.assertEqual(len(result['productStats']), 1)
        self.assertEqual(result['productStats'][0]['ppn'], 'P')
        self.assertEqual(result['productStats'][0]['mps'], 50)
        self.assertEqual(result['productStats'][0]['completed'], 50)

    def test_parse_date_accepts_common_excel_text_formats(self):
        expected = parse_date('2026-09-01T00:00:00+08:00')
        for value in ('2026/9/1', '2026-9-1', '2026年9月1日',
                      '2026-09-01 00:00'):
            self.assertEqual(parse_date(value), expected)

    def test_duplicate_lot_ids_and_prefixed_flow_are_scheduled_as_rows(self):
        data = fixture(2)
        data['lots'][0][0] = 'P-1'
        data['lots'][1][0] = 'P-1'
        data['lots'][0][1] = 'P-1'
        data['lots'][1][1] = 'P-1'
        result = run(data)
        self.assertEqual(len(result['lots']), 2)
        self.assertEqual({lot['id'] for lot in result['lots']}, {'P-1'})
        self.assertEqual(len({lot['key'] for lot in result['lots']}), 2)
        self.assertEqual({lot['prod'] for lot in result['lots']}, {'P'})
        self.assertEqual(len({task['lotKey'] for task in result['cpTasks']}), 2)

    def test_compact_result_keeps_browser_contract_without_mutating_full_result(self):
        full = run(fixture())
        compact = compact_result(full)
        self.assertNotIn('data', compact)
        self.assertEqual(len(compact['lots']), len(full['lots']))
        self.assertIn('ops', compact['lots'][0])
        self.assertIn('cpTasks', compact['lots'][0])
        self.assertEqual(set(compact['batches'][0]['lots'][0]),
                         {'id', 'key', 'prod', 'platform', 'wafer'})
        self.assertNotIn('daily', compact['machines'][0])
        self.assertIn('daily', full['machines'][0])
        self.assertIn('data', full)


if __name__ == '__main__':
    unittest.main()