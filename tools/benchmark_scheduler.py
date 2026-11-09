#!/usr/bin/env python3
"""Compare greedy/search on the current input under bounded production windows."""
import argparse
import copy
import hashlib
import json
import platform
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from diagnostics import inspect_run
from optimizer import objective
from scheduler import run
from server_core import build_payload


def main():
    parser = argparse.ArgumentParser(description='比较原贪心和多起点局部搜索')
    parser.add_argument('--days', nargs='+', type=int, default=[30, 10, 7])
    parser.add_argument('--output', type=Path, default=ROOT / 'output/算法搜索对比.json')
    args = parser.parse_args()
    if any(day < 1 for day in args.days):
        parser.error('--days 必须是正整数')
    source = build_payload()
    digest_input = copy.deepcopy(source)
    digest_input['meta'].pop('loadedAt', None)
    report = {
        'source': source['meta']['source'], 'python': platform.python_version(),
        'inputSha256': hashlib.sha256(json.dumps(
            digest_input, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest(),
        'note': '缩短统计窗口的压力测试；路线、Lot、资源、PM、借机保留原输入。',
        'cases': [],
    }
    for days in args.days:
        data = copy.deepcopy(source)
        data['meta']['days'] = days
        results = {}
        case = {'days': days}
        for algorithm in ('greedy', 'search'):
            result = run(data, {'algorithm': algorithm})
            findings, _ = inspect_run({'input': data, 'result': result})
            violations = [finding for finding in findings if finding['code'].startswith('R')]
            if violations:
                raise AssertionError(violations)
            results[algorithm] = result
            case[algorithm] = dict(result['optimization']['selected'],
                                   runMs=result['runMs'],
                                   evaluations=result['optimization']['evaluations'],
                                   selectedPolicy=result['optimization']['selectedPolicy'],
                                   ruleViolations=0)
            print('%dd %s: %d wafer, min %.2f%%, %d ms' % (
                days, algorithm, case[algorithm]['completedWafers'],
                case[algorithm]['minProductRate'] * 100, result['runMs']), flush=True)
        assert objective(results['search']) >= objective(results['greedy'])
        case['waferGain'] = case['search']['completedWafers'] - case['greedy']['completedWafers']
        case['searchTrials'] = results['search']['trials']
        report['cases'].append(case)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(args.output)


if __name__ == '__main__':
    main()
