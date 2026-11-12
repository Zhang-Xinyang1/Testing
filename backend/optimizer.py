"""Deterministic multi-start search over complete, feasible simulations.

The simulator is the schedule decoder. Product-priority moves and lot insertion
moves change its decisions; every neighbor is decoded and scored end to end.
No move edits task timestamps or bypasses resource/route constraints.
"""
import copy
import math
import time
from task_overrides import TaskConstraintError


POLICIES = (
    {'name': 'balanced', 'workWeight': 0.20, 'tailWeight': 0.5},
    {'name': 'long-route', 'workWeight': 0.40, 'tailWeight': 1.0},
    {'name': 'short-tail', 'workWeight': 0.0, 'tailWeight': -0.25},
)


def objective(result):
    """Lexicographic max-min attainment, output, feasibility, span, setups."""
    rates = tuple(sorted(round(min(1, p['rate']), 12)
                         for p in result['productStats'] if p['mps'] > 0))
    completed = [lot['complete'] for lot in result['lots']
                 if lot.get('complete') is not None and math.isfinite(lot['complete'])]
    span = max(completed + [result['start']]) - result['start']
    delivery = result.get('data', {}).get('productionRules')
    late = [(lot, max(0, lot['complete'] - lot['shipDue'])) for lot in result['lots']
            if lot.get('shipDue') is not None]
    due_score = (-sum(lot['wafer'] for lot, delay in late if delay > 0),
                 -sum(delay * lot['wafer'] for lot, delay in late)) if delivery else ()
    return due_score + (rates, result['metrics']['completedWafers'],
            -result['metrics']['unscheduledLots'], -span,
            -sum(m['setupMs'] for m in result['machines']))


def summary(result):
    score = objective(result)
    return {
        'minProductRate': result['metrics']['minProductRate'],
        'meanProductRate': result['metrics']['meanProductRate'],
        'completedMonth': result['metrics']['completedMonth'],
        'completedWafers': result['metrics']['completedWafers'],
        'unscheduledLots': result['metrics']['unscheduledLots'],
        'makespanHours': -score[-2] / 3600000,
        'setupMinutes': -score[-1] / 60000,
    }


def _neighbors(result, policy):
    """Raise weak products, or insert a near-late lot earlier in dispatch order."""
    base = policy or POLICIES[0]
    weak = sorted((p for p in result['productStats'] if p['mps'] > 0 and p['rate'] < 1),
                  key=lambda p: (p['rate'], p['ppn']))
    # Interleave neighborhoods so a small budget still tries both types.
    for product in weak[:4]:
        for amount in (0.25, 0.5):
            candidate = copy.deepcopy(base)
            bias = candidate.setdefault('productBias', {})
            bias[product['ppn']] = bias.get(product['ppn'], 0) - amount
            yield 'product:' + product['ppn'], candidate
        late = sorted((lot for lot in result['lots']
                       if lot['prod'] == product['ppn']
                       and lot['complete'] > result['monthEnd']
                       and math.isfinite(lot['complete'])),
                      key=lambda lot: (lot['complete'], lot['key']))
        for lot in late[:2]:
            candidate = copy.deepcopy(base)
            bias = candidate.setdefault('lotBias', {})
            bias[lot['key']] = bias.get(lot['key'], 0) - 0.75
            yield 'insert:' + lot['key'], candidate


def search_schedules(data, options, windows, simulate, progress=None):
    algorithm = options.get('algorithm', 'search')
    if algorithm not in ('greedy', 'search'):
        raise ValueError('algorithm 必须为 greedy 或 search')
    iterations = options.get('searchIterations', 8)
    if type(iterations) is not int or not 0 <= iterations <= 32:
        raise ValueError('searchIterations 必须为 0 至 32 的整数')
    options = dict(options, algorithm=algorithm, searchIterations=iterations)
    best, best_policy, best_window = None, None, windows[0]
    trials, seen, rejected = [], set(), []
    started = time.monotonic()
    budget = min(1800, data.get('productionRules', {}).get('maxRuntimeSeconds', 1800))

    def evaluate(window, policy, phase, move=''):
        nonlocal best, best_policy, best_window
        if time.monotonic() - started >= budget:
            return False
        # Identity excludes the human-readable name; equal neighbors are not rerun.
        identity = (window, None if policy is None else (
            policy['workWeight'], policy['tailWeight'],
            tuple(sorted(policy.get('productBias', {}).items())),
            tuple(sorted(policy.get('lotBias', {}).items()))))
        if identity in seen:
            return False
        seen.add(identity)
        try:
            result = simulate(data, dict(options, bakeWindowHours=window), policy)
        except TaskConstraintError as error:
            rejected.append(str(error))
            return True
        accepted = best is None or objective(result) > objective(best)
        trials.append(dict(summary(result), phase=phase,
                           policy=policy['name'] if policy else 'legacy', move=move,
                           bakeWindowHours=window, accepted=accepted))
        if accepted:
            best, best_policy, best_window = result, policy, window
        if progress:
            progress(best, len(trials), accepted)
        return True

    # The original scheduler is always included as an incumbent. Thus search
    # cannot worsen the objective for the same input and BAKE windows.
    for window in windows:
        evaluate(window, None, 'baseline')
    baseline = summary(best) if best else None
    if algorithm == 'search':
        for policy in POLICIES:
            for window in windows:
                evaluate(window, policy, 'multi-start')
        evaluated = 0
        while best is not None and evaluated < iterations:
            progressed = False
            # Freeze the parent of this neighborhood even if a move is accepted.
            parent, policy, window = best, best_policy, best_window
            for move, neighbor in _neighbors(parent, policy):
                if evaluate(window, neighbor, 'local-search', move):
                    evaluated += 1
                    progressed = True
                if evaluated >= iterations:
                    break
            if not progressed:
                break

    if best is None:
        raise TaskConstraintError((rejected[0] if rejected else '没有可行排程') + '。本次搜索未找到满足固定要求的方案，请调整或解除固定后重试。',
                                  {'table': 'taskOverrides'})
    best['trials'] = trials
    best['optimization'] = {
        'algorithm': algorithm, 'version': 1,
        'objective': ('shipping-deadline, ' if data.get('productionRules') else '') + 'leximin-attainment, wafers, scheduled-lots, makespan, setups',
        'evaluations': len(trials), 'localSearchBudget': iterations,
        'selectedPolicy': best_policy['name'] if best_policy else 'legacy',
        'selectedPolicyParameters': copy.deepcopy(best_policy),
        'baseline': baseline, 'selected': summary(best),
        'rejectedCandidates': len(rejected),
        'globalOptimalityProven': False,
    }
    return best
