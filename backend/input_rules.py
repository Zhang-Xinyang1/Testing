"""Shared date, product and lot-reference semantics for every backend layer."""
import re
import unicodedata
from datetime import datetime, timedelta, timezone

CST = timezone(timedelta(hours=8))


def identifier(value):
    """Canonical business identifier; punctuation and internal spaces matter."""
    text = unicodedata.normalize('NFKC', str(value if value is not None else ''))
    return re.sub(r'[\ufeff\u200b\u200c\u200d\u2060]', '', text).strip().upper()


def normalize_identifiers(data):
    """Copy reference fields consistently for import, API, solver and diagnostics.

    Lot display IDs and user text are preserved. WIP binds display IDs separately
    so case variants still require a row key when they refer to multiple rows.
    """
    result = dict(data)
    fields = {
        'mps': ('ppn',), 'weekly': ('ppn', 'week'),
        'eqp': ('id', 'card', 'recipe'), 'recipes': ('recipe',),
        'pm': ('eqp',), 'borrow': ('eqp',),
        'constraints': ('eqp', 'recipe', 'type'),
        'wip': ('lotKey', 'eqp', 'chamber', 'card'),
    }
    for table, columns in fields.items():
        if table not in data:
            continue
        result[table] = [{**row, **{key: identifier(row[key]) for key in columns if key in row}}
                         for row in data[table]]
    for row in result.get('recipes', []):
        if isinstance(row.get('cards'), list):
            row['cards'] = [identifier(card) for card in row['cards']]
    if 'lots' in data:
        result['lots'] = [list(row) for row in data['lots']]
        for row in result['lots']:
            for index in (1, 5):
                if index < len(row):
                    row[index] = identifier(row[index])
    if 'flows' in data:
        flows = {}
        for product, route in data['flows'].items():
            key = identifier(product)
            normalized = [[identifier(op[0]), identifier(op[1]), *op[2:]] for op in route]
            if key in flows and flows[key] != normalized:
                raise ValueError('同一产品有大小写不同且内容冲突的路线：' + key)
            flows[key] = normalized
        result['flows'] = flows
    if 'bakeResources' in data:
        result['bakeResources'] = dict(data['bakeResources'])
        if 'ids' in data['bakeResources']:
            result['bakeResources']['ids'] = [identifier(v) for v in data['bakeResources']['ids']]
    if 'cardType' in data:
        result['cardType'] = {identifier(k): identifier(v) for k, v in data['cardType'].items()}
    return result


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
    return identifier(value).split('-', 1)[0].strip()


def flow_product(lot_id, explicit, flows):
    explicit = identifier(explicit)
    keys = {identifier(key): key for key in flows}
    for candidate in (explicit, base_ppn(explicit)):
        if candidate in keys:
            return keys[candidate]
    parts = identifier(lot_id).split('-')
    for size in range(len(parts), 0, -1):
        candidate = '-'.join(parts[:size]).strip()
        if candidate in keys:
            return keys[candidate]
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
        by_id.setdefault(identifier(row[0]), []).append(key)
    resolved, seen = [], set()
    for source in data.get('wip') or []:
        item = dict(source)
        lid, key = item.get('lotId'), identifier(item.get('lotKey'))
        if key:
            if key not in by_key or identifier(by_key[key][0]) != identifier(lid):
                raise ValueError('在制 Lot 行键与 LotID 不匹配：%s / %s' % (key, lid))
        else:
            choices = by_id.get(identifier(lid), [])
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
        item['lotId'] = by_key[key][0]
        resolved.append(item)
    return resolved
