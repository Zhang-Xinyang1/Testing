"""Confirmed production rules. Old snapshots keep their original rule profile."""
import math

DEFAULTS = {'recipeChangeMinutes': 30, 'cardChangeMinutes': 5,
            'restHours': 336, 'maxRuntimeSeconds': 1800, 'freezeHours': 0,
            'checkTime': '08:00', 'delayAlertHours': 0, 'shortageAlertWafers': 1}


def rules(values=None):
    result = dict(DEFAULTS)
    if values is not None:
        if not isinstance(values, dict) or set(values) - set(DEFAULTS):
            raise ValueError('生产规则包含未知字段')
        result.update(values)
    bounds = {'recipeChangeMinutes': (0, 1440), 'cardChangeMinutes': (5, 5),
              'restHours': (336, 336), 'maxRuntimeSeconds': (1, 1800),
              'freezeHours': (0, 744), 'delayAlertHours': (0, 744),
              'shortageAlertWafers': (1, 100000000)}
    for key, (low, high) in bounds.items():
        value = result[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
            raise ValueError('%s 必须在 %s 至 %s 范围内' % (key, low, high))
    import re
    if not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', str(result['checkTime'])):
        raise ValueError('检查时刻须为 HH:MM')
    return result


def recipe_minutes(data):
    # Legacy inputs and archived plans predate Recipe setup; the production
    # workspace explicitly carries the confirmed rules.
    return data.get('productionRules', {}).get('recipeChangeMinutes', 0)


def recipe_at(machine, when, tasks=(), commitments=()):
    """Recipe at a boundary, after tasks that finish exactly at that time."""
    value = machine.get('initialRecipe', machine.get('recipe'))
    events = [(t['end'], t.get('recipe')) for t in tasks if t['end'] <= when]
    events += [(t['end'], t.get('recipe')) for t in commitments if t['end'] <= when]
    for _, recipe in sorted(events, key=lambda item: item[0]):
        if recipe:
            value = recipe
    return value
