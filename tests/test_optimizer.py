import copy
import json
import random
import unittest

from test_scheduler import fixture
from diagnostics import inspect_run
from optimizer import objective
from scheduler import run


def congested_case():
    data = fixture(12)
    data['meta']['days'] = 4
    data['mps'] = [{'ppn': p, 'platform': 'A;B', 'mps': 100} for p in 'PQR']
    data['flows'] = {
        p: [['CP1', 'R', before], ['BAKE', '210C24H', None], ['CP2', 'R', after]]
        for p, before, after in [('P', 90, 10), ('Q', 60, 90), ('R', 90, 60)]}
    for index, lot in enumerate(data['lots']):
        lot[1] = 'PQR'[index // 4]
    return data


class OptimizerTests(unittest.TestCase):
    def assert_feasible(self, data, result):
        findings, _ = inspect_run({'input': data, 'result': result})
        self.assertFalse([f for f in findings if f['code'].startswith('R')], findings)
        for lot in result['lots']:
            if not lot.get('unscheduledReason'):
                self.assertEqual(len(lot['routeTasks']), len(data['flows'][lot['prod']]))
        self.assertEqual(result['metrics']['completedWafers'],
                         sum(p['completed'] for p in result['productStats']))

    def test_operations_can_use_different_groups_when_no_group_supports_whole_route(self):
        data = fixture(1)
        data['recipes'].append({'recipe': 'R2', 'cards': ['C2']})
        data['flows']['P'] = [['CP1', 'R', 10], ['BAKE', '210C24H', None],
                             ['CP2', 'R2', 10]]
        data['constraints'] = [{'eqp': 'M1', 'recipe': 'R2'},
                               {'eqp': 'M2', 'recipe': 'R'}]
        baseline = run(data, {'algorithm': 'greedy'})
        result = run(data)
        self.assertEqual(baseline['metrics']['unscheduledLots'], 1)
        self.assertEqual(result['metrics']['completedWafers'], 25)
        self.assertEqual([task['machine'] for task in result['cpTasks']], ['M1', 'M2'])
        self.assert_feasible(data, result)

    def test_local_search_improves_over_all_initial_policies(self):
        data = congested_case()
        initial = run(data, {'searchIterations': 0})
        improved = run(data)
        self.assertGreater(objective(improved), objective(initial))
        self.assertGreater(improved['metrics']['completedWafers'],
                           initial['metrics']['completedWafers'])
        self.assertTrue(any(t['accepted'] and t['phase'] == 'local-search'
                            for t in improved['trials']))
        self.assert_feasible(data, improved)

    def test_search_keeps_baseline_and_constraints_on_varied_inputs(self):
        for seed in range(8):
            with self.subTest(seed=seed):
                rng = random.Random(seed)
                data = congested_case()
                data['meta']['days'] = rng.choice([3, 5, 8])
                for ops in data['flows'].values():
                    ops[0][2] = rng.choice([10, 30, 60])
                    ops[2][2] = rng.choice([10, 30, 90])
                data['pm'] = [{'eqp': 'M1', 'early': '2026-09-02',
                               'due': '2026-09-02', 'over': '2026-09-03', 'hours': 8}]
                data['borrow'] = [{'eqp': 'M2', 'date': '2026-09-01', 'hours': 2}]
                data['wip'] = [{'lotId': 'L0', 'eqp': 'M1', 'card': 'C1',
                                'start': '2026-09-01', 'end': '2026-09-01T03:00:00+08:00'}]
                baseline = run(data, {'algorithm': 'greedy'})
                result = run(data)
                self.assertGreaterEqual(objective(result), objective(baseline))
                self.assert_feasible(data, result)

    def test_duplicate_ids_stay_independent_and_search_is_reproducible(self):
        data = congested_case()
        for lot in data['lots']:
            lot[0] = lot[1] + '-vendor'
        before = copy.deepcopy(data)
        options = {'searchIterations': 5}
        first = run(data, options)
        second = run(data, options)
        self.assertEqual(data, before)
        self.assertEqual(options, {'searchIterations': 5})
        self.assertEqual(first['cpTasks'], second['cpTasks'])
        self.assertEqual(first['trials'], second['trials'])
        self.assertEqual(first['optimization'], second['optimization'])
        self.assertEqual(len({lot['key'] for lot in first['lots']}), 12)
        self.assertEqual(len({task['lotKey'] for task in first['cpTasks']}), 12)
        json.dumps(first, allow_nan=False)
        self.assert_feasible(data, first)

    def test_options_are_bounded_and_explicit_window_is_respected(self):
        result = run(congested_case(), {'bakeWindowHours': 4, 'searchIterations': 2})
        self.assertEqual({t['bakeWindowHours'] for t in result['trials']}, {4})
        self.assertLessEqual(result['optimization']['evaluations'], 6)
        for options in ({'algorithm': 'invalid'}, {'searchIterations': -1},
                        {'searchIterations': True}, {'searchIterations': 33},
                        {'bakeWindowHours': float('nan')}, {'bakeWindowHours': -1},
                        {'bakeWindowHours': list(range(9))}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                run(fixture(), options)


if __name__ == '__main__':
    unittest.main()
