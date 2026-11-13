#!/usr/bin/env python3
"""Full-case verification for the Python scheduling engine.

Reads a normalized input (default .artifacts/input.json), runs the scheduler,
checks the confirmed rules, and writes the same result files formerly produced
by .artifacts/verify_metrics.js.
"""
import argparse
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from scheduler import DAY, HOUR, MIN, day_key, parse_date, run  # noqa: E402


def check(name, function, checks):
    function()
    checks[name] = 'pass'
    print('PASS', name)


def verify(data, output_dir):
    result = run(data, {})
    checks = {}
    calendar = {}

    def reserve(resource, start, end):
        assert isinstance(start, (int, float)) and isinstance(end, (int, float))
        assert math.isfinite(start) and math.isfinite(end) and end > start
        calendar.setdefault(resource, []).append([start, end])

    eqp = {equipment['id']: equipment for equipment in data['eqp']}
    recipes = {recipe['recipe']: recipe['cards'] for recipe in data['recipes']}
    initial = {equipment['id']: equipment.get('card') for equipment in data['eqp']}
    constraints = {(c['eqp'], c['recipe']) for c in data.get('constraints') or []}

    def counts():
        assert len(data['mps']) == 72
        assert sum(lot[3] for lot in data['lots']) == 11000
        assert all(isinstance(lot[3], int) and 1 <= lot[3] <= 25
                   for lot in data['lots'])
        assert len(data['borrow']) == 119
        assert len(data['recipes']) == 31
        assert len({card for recipe in data['recipes']
                    for card in recipe['cards']}) == 62

    def cp_eligibility():
        for machine in result['machines']:
            installed = initial[machine['id']]
            for task in sorted(machine['tasks'], key=lambda item: item['blockStart']):
                assert eqp[task['machine']]['status'] == 'RUN'
                assert machine['type'] in task['platform'].split(';')
                assert task['card'] in recipes[task['recipe']]
                assert (task['machine'], task['recipe']) not in constraints
                expected = 0 if task['card'] == installed else 5 * MIN
                assert task['setup'] == expected
                assert task['start'] - task['blockStart'] == task['setup']
                installed = task['card']
                reserve(machine['id'], task['blockStart'], task['end'])

    def bake_capacity():
        for batch in result['batches']:
            assert 1 <= len(batch['lots']) <= 4
            assert batch['recipe'] in ('210C24H', '250C24H')
            assert batch['processStart'] - batch['start'] == 2 * HOUR
            assert batch['processEnd'] - batch['processStart'] == 24 * HOUR
            assert batch['end'] - batch['processEnd'] == 2 * HOUR
            for lot in batch['lots']:
                task = next((item for item in lot['routeTasks']
                             if item.get('batchId') == batch['id']), None)
                assert task is not None
                assert task['recipe'] == batch['recipe']
                assert task['end'] == batch['processEnd']
            reserve(batch['chamber'], batch['start'], batch['end'])

    def routes_and_qtime():
        for lot in result['lots']:
            ops = data['flows'][lot['prod']]
            assert len(lot['routeTasks']) == len(ops)
            ready = parse_date(lot['release'])
            for op, task in zip(ops, lot['routeTasks']):
                assert task['step'] == op[0]
                assert task['recipe'] == op[1]
                assert task['start'] >= ready
                if str(op[0]).startswith('CP'):
                    expected = op[2] * MIN * lot['wafer']
                    assert abs((task['end'] - task['start']) - expected) < 1e-6
                    assert task['duration'] == expected
                    assert task['timePerWafer'] == op[2]
                if op[0] == 'QTIME1':
                    assert task['end'] - task['start'] == int(op[1]) * MIN
                ready = task['end']
            assert lot['complete'] == ready

    def pm_window():
        for record in result['pmRecords']:
            assert record['start'] >= parse_date(record['early'])
            assert record['end'] <= parse_date(record['over']) + DAY
            assert record['end'] - record['start'] == record['hours'] * HOUR
            if 'OVEN' in eqp[record['eqp']]['type'].upper():
                assert len(record['resources']) == 4
            for resource in record['resources']:
                reserve(resource, record['start'], record['end'])
    def borrows():
        assert len(result['borrows']) == 119
        for record in result['borrows']:
            assert record['eqp'] == record['req']['eqp']
            assert record['status'] == 'on-time'
            assert day_key(record['start']) == record['req']['date']
            assert abs((record['end'] - record['start'])
                       - record['req']['hours'] * HOUR) < 0.01
            reserve(record['eqp'], record['start'], record['end'])

    def no_overlap():
        for resource, spans in calendar.items():
            spans.sort(key=lambda span: span[0])
            for index in range(1, len(spans)):
                assert spans[index][0] >= spans[index - 1][1], 'overlap ' + resource

    def completed_counts():
        metrics = result['metrics']
        assert sum(product['completed'] for product in result['productStats']) \
            == metrics['completedWafers']
        completed = sum(1 for lot in result['lots']
                        if lot['complete'] <= result['monthEnd'])
        assert metrics['completedLots'] == completed
        assert metrics['completedWafers'] == metrics['totalWafers']
        assert metrics['completedLots'] == metrics['totalLots']
        for product in result['productStats']:
            expected = sum(lot['wafer'] for lot in result['lots']
                           if lot['prod'] == product['ppn']
                           and lot['complete'] <= result['monthEnd'])
            assert product['completed'] == expected
            assert product['rate'] == product['completed'] / product['mps']
        assert metrics['unscheduledLots'] == 0

    check('counts', counts, checks)
    check('cp_eligibility_card_type_setup_blacklist', cp_eligibility, checks)
    check('bake_capacity_recipe_duration_release', bake_capacity, checks)
    check('arbitrary_routes_qtime_full_completion', routes_and_qtime, checks)
    check('pm_window_all_chambers', pm_window, checks)
    check('borrow_date_duration_fixed_machine', borrows, checks)
    check('resource_no_overlap', no_overlap, checks)
    check('completed_counts_and_rates', completed_counts, checks)

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    result_path = ROOT / '.artifacts/result.json'
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')

    csv_path = output_dir / '新版排程_各产品达成率.csv'
    lines = ['PPN,Platform,MPS_wafer,月内完成_wafer,缺口_wafer,达成率']
    for product in result['productStats']:
        lines.append(','.join(str(value) for value in (
            product['ppn'], product['platform'], product['mps'],
            product['completed'], product['gap'],
            '%.2f%%' % (100 * product['rate']))))
    csv_path.write_text('\ufeff' + '\n'.join(lines), encoding='utf-8')

    report = {
        'options': result['options'], 'unit': 'wafer',
        'lotSizing': data['meta'].get('lotSizing'),
        'source': data['meta'].get('source'), 'synthetic': True,
        'metrics': result['metrics'], 'trials': result['trials'],
        'checks': checks, 'productStats': result['productStats'],
    }
    report_path = output_dir / '新版排程_重跑核验.json'
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                           encoding='utf-8')
    print(json.dumps({'metrics': result['metrics'], 'trials': result['trials'],
                      'checks': checks}, ensure_ascii=False, indent=2))
    return result, report


def main():
    parser = argparse.ArgumentParser(description='核验 Python 排产全算例')
    parser.add_argument('--input', default=str(ROOT / '.artifacts/input.json'))
    parser.add_argument('--output-dir', default=str(ROOT / 'output'))
    args = parser.parse_args()
    input_path = Path(args.input)
    if input_path.is_file():
        with open(input_path, encoding='utf-8') as handle:
            data = json.load(handle)
    else:
        sys.path.insert(0, str(ROOT / 'backend'))
        from server_core import build_payload
        print('未找到 %s，改用当前配置和 Excel 数据。' % input_path)
        data = build_payload()
    verify(data, args.output_dir)


if __name__ == '__main__':
    main()