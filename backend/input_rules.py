"""Shared date, product and lot-reference semantics for every backend layer."""
import re
from datetime import datetime, timedelta, timezone

CST = timezone(timedelta(hours=8))


def parse_datetime(value):
    text = str(value or '').strip()
    text = text.replace('年', '-').replace('月', '-').replace('日', '').replace('/', '-')
    match = re.fullmatch(
        r'(\d{4})-(\d{1,2})-(\d{1,2})'
        r'(?:[Tt ](\d{1,2}):(\d{2})(?::(\d{2})(?:\.(\d{1,6}))?)?'
        r'(Z|z|[+-]\d{2}:?\d{2})?)?', text)
    if not match:
        raise ValueError('无效日期/时间：' + str(value))
    year, month, day, hour, minute, second, fraction, zone = match.groups()
    tz = CST
    if zone and zone.upper() == 'Z':
        tz = timezone.utc
    elif zone:
        offset = zone[1:].replace(':', '')
        hours, minutes = int(offset[:2]), int(offset[2:])
        if hours > 23 or minutes > 59:
            raise ValueError('无效时区：' + zone)
        tz = timezone((1 if zone[0] == '+' else -1) * timedelta(hours=hours, minutes=minutes))
    return datetime(int(year), int(month), int(day), int(hour or 0),
                    int(minute or 0), int(second or 0),
                    int((fraction or '').ljust(6, '0')), tzinfo=tz)


def base_ppn(value):
    return str(value or '').strip().split('-', 1)[0].strip()


def flow_product(lot_id, explicit, flows):
    explicit = str(explicit or '').strip()
    for candidate in (explicit, base_ppn(explicit)):
        if candidate in flows:
            return candidate
    parts = str(lot_id or '').strip().split('-')
    for size in range(len(parts), 0, -1):
        candidate = '-'.join(parts[:size]).strip()
        if candidate in flows:
            return candidate
    return explicit or base_ppn(lot_id)


def row_key(index):
    return 'LOT-%06d' % (index + 1)


def ordered_plans(plans, kind):
    """Reserve fixed appointments first; place flexible plans by deadline.

    Keep original indices so record IDs still identify the user's input rows.
    """
    def order(entry):
        index, plan = entry
        deadline = (plan.get('start') or plan['over']) if kind == 'pm' else (plan.get('date') or plan['start'])
        return (not bool(plan.get('start')), parse_datetime(deadline), index)
    return sorted(enumerate(plans), key=order)


def resolve_wip(data):
    """Bind WIP to exactly one source row, rejecting ambiguous display IDs."""
    rows = data.get('lots') or []
    by_key = {row_key(index): row for index, row in enumerate(rows)}
    by_id = {}
    for key, row in by_key.items():
        by_id.setdefault(row[0], []).append(key)
    resolved, seen = [], set()
    for source in data.get('wip') or []:
        item = dict(source)
        lid, key = item.get('lotId'), item.get('lotKey')
        if key:
            if key not in by_key or by_key[key][0] != lid:
                raise ValueError('在制 Lot 行键与 LotID 不匹配：%s / %s' % (key, lid))
        else:
            choices = by_id.get(lid, [])
            if not choices:
                raise ValueError('在制 Lot 无效或重复：' + str(lid))
            if len(choices) > 1:
                raise ValueError('在制 LotID 对应多行：%s；请填写 lotKey（行键）：%s'
                                 % (lid, '、'.join('%s（%s，%s wafer）' % (key, by_key[key][1], by_key[key][3])
                                                  for key in choices)))
            key = choices[0]
        if key in seen:
            raise ValueError('在制 Lot 无效或重复：' + str(lid))
        seen.add(key)
        item['lotKey'] = key
        resolved.append(item)
    return resolved
