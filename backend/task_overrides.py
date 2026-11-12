"""Explicit CP assignment and fixed processing times, tied to an input Lot row."""
import hashlib
import json
from input_errors import InputError
from input_rules import identifier, parse_datetime, row_key
from production_rules import recipe_minutes


class TaskConstraintError(InputError):
    pass


def lot_signature(data, index):
    row = data['lots'][index]
    identity = [row, data.get('flows', {}).get(row[1])]
    # JavaScript serializes 14.0 as 14. Keep the row identity stable across the
    # browser JSON round trip without ignoring real wafer or duration changes.
    def canonical(value):
        if isinstance(value, list):
            return [canonical(item) for item in value]
        if isinstance(value, float) and value.is_integer():
            return int(value)
        return value
    return hashlib.sha256(json.dumps(canonical(identity), ensure_ascii=False,
                                    sort_keys=True).encode()).hexdigest()


def validate_overrides(data):
    values = data.get('taskOverrides', [])
    if not isinstance(values, list):
        raise InputError('手工工序调整必须为列表', {'table': 'taskOverrides'})
    lots = {row_key(i): (i, row) for i, row in enumerate(data['lots'])}
    equipment = {identifier(e['id']): e for e in data['eqp']}
    excluded = {w.get('lotKey') for w in data.get('wip') or []}
    forbidden = {(c['eqp'], c['recipe']) for c in data.get('constraints') or []}
    seen, result = set(), []
    for position, source in enumerate(values, 1):
        location = {'table': 'taskOverrides', 'record': position, 'source': 'override'}
        def require(ok, message):
            if not ok:
                raise InputError('手工工序调整第 %d 条：%s' % (position, message), location)
        require(isinstance(source, dict), '记录格式无效')
        item = dict(source)
        key, op_index = identifier(item.get('lotKey')), item.get('opIndex')
        require(key in lots, 'Lot 行键不存在，请重新选择任务')
        index, lot = lots[key]
        require(item.get('signature') == lot_signature(data, index),
                'Lot 或工艺输入已变化，请删除旧调整后重新选择任务：' + key)
        route = data['flows'].get(lot[1], [])
        require(type(op_index) is int and 0 <= op_index < len(route), '工序位置无效')
        op = route[op_index]
        require(str(op[0]).startswith('CP'), '当前仅支持 CP 工序调整')
        require(key not in excluded, '在制 Lot 不可再次调整或排产')
        require((key, op_index) not in seen, '同一 Lot 工序只能有一条调整')
        seen.add((key, op_index))
        eqp = identifier(item.get('eqp'))
        require(eqp in equipment, '指定机台不存在')
        machine = equipment[eqp]
        from platforms import split_platforms
        require(machine.get('status') == 'RUN' and machine['type'] in split_platforms(lot[2]),
                '指定机台不是该 Lot 可用的 RUN 机台')
        require((eqp, op[1]) not in forbidden, '指定机台禁止加工此 Recipe')
        require(not (item.get('fixedStart') and item.get('earliestStart')), '固定开始和最早开始只能选择一种')
        for field in ('fixedStart', 'earliestStart'):
            if item.get(field):
                try:
                    when = parse_datetime(item[field])
                    require(when >= parse_datetime(data['meta']['start']), '开始时间早于计划月份')
                except (ValueError, TypeError) as error:
                    if isinstance(error, InputError):
                        raise
                    raise InputError('手工工序调整日期无效：' + field, dict(location, field=field)) from error
        if item.get('fixedStart'):
            cards = next((r['cards'] for r in data['recipes'] if r['recipe'] == op[1]), [])
            item['card'] = identifier(item.get('card'))
            require(item['card'] in cards, '固定任务必须保留有效针卡类型')
            if item.get('reserveStart'):
                try:
                    lead = (parse_datetime(item['fixedStart']) - parse_datetime(item['reserveStart'])).total_seconds()
                    require(0 <= lead <= (5 + recipe_minutes(data)) * 60, '切换预留超出配置的换卡与 Recipe 切换时长')
                except (TypeError, ValueError) as error:
                    raise InputError(str(error), dict(location, field='reserveStart')) from error
        item.update(lotKey=key, opIndex=op_index, eqp=eqp, lotId=lot[0])
        result.append(item)
    return result
