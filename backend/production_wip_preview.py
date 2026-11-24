"""Preview WIP arrivals against one stable demo snapshot without publishing it."""
from copy import deepcopy
from input_rules import identifier, wip_timestamp as _timestamp


def _identifier(value, field):
    if not isinstance(value, str) or not identifier(value):
        raise ValueError(field + ' 须为非空字符串')
    return identifier(value)


def _check_fields(obj, allowed, label):
    if not isinstance(obj, dict):
        raise ValueError(label + ' 须为对象')
    extra = sorted(str(key) for key in set(obj) - allowed)
    if extra:
        raise ValueError(label + ' 含有未支持的字段 ' + '、'.join(extra))


def _base_index(snapshot, feed):
    if not isinstance(snapshot, dict) or snapshot.get('source') != 'demo':
        raise ValueError('WIP 预览只接受演示来源的基线')
    if snapshot.get('identityStable') is not True:
        raise ValueError('需要已确认的稳定 Lot UID')
    _check_fields(feed, {'snapshotId', 'asOf', 'records'}, 'WIP 输入')
    version = feed.get('snapshotId')
    if not isinstance(version, str) or not version.strip() or version != snapshot.get('snapshotId'):
        raise ValueError('snapshotId 与当前基线不一致')
    asof = _timestamp(feed.get('asOf'), 'WIP asOf')
    if asof != _timestamp(snapshot.get('asOf'), '基线 asOf'):
        raise ValueError('WIP 数据时点与基线不一致')
    if not isinstance(feed.get('records'), list):
        raise ValueError('records 须为数组')
    if not isinstance(snapshot.get('lots'), list) or not isinstance(snapshot.get('flows'), dict):
        raise ValueError('基线缺少 Lot 明细或产品路线')
    lots = {}
    for lot in snapshot['lots']:
        if not isinstance(lot, dict):
            raise ValueError('基线 Lot 明细无效')
        uid = _identifier(lot.get('uid'), '基线 UID')
        if uid in lots:
            raise ValueError('基线 UID 重复 ' + uid)
        lots[uid] = lot
    routes = {}
    for product, route in snapshot['flows'].items():
        ppn = _identifier(product, '路线产品')
        if ppn in routes or not isinstance(route, list) or not route:
            raise ValueError('基线路线重复或为空 ' + ppn)
        if any(not isinstance(op, (list, tuple)) or not op for op in route):
            raise ValueError('基线路线工序无效 ' + ppn)
        routes[ppn] = [_identifier(op[0], '路线工序') for op in route]
    return lots, routes, asof


def _classify(row, detail, lots, routes, asof, seen):
    _check_fields(row, {'uid', 'step', 'kind', 'at', 'wafer', 'location'}, 'WIP 记录')
    for key in ('uid', 'step', 'kind'):
        detail[key] = _identifier(row.get(key), key)
    uid, step, kind = detail['uid'], detail['step'], detail['kind']
    if (uid, step) in seen:
        raise ValueError('同一 UID 与工序重复')
    seen.add((uid, step))
    if uid not in lots:
        raise ValueError('UID 不在基线中')
    if kind not in ('CONFIRMED', 'ETA'):
        raise ValueError('kind 须为 CONFIRMED 或 ETA')
    lot = lots[uid]
    if 'wafer' in row and (type(row['wafer']) is not int or row['wafer'] != lot.get('wafer')):
        raise ValueError('wafer 须为与基线一致的整数')
    if 'location' in row and not isinstance(row['location'], str):
        raise ValueError('location 须为字符串')
    at = _timestamp(row['at'], 'at') if row.get('at') is not None else None
    if kind == 'CONFIRMED' and at is None:
        raise ValueError('确认可用须提供 at')
    route = routes.get(_identifier(lot.get('ppn'), 'Lot 产品'), [])
    if step not in route:
        raise ValueError('工序不属于该 Lot 的路线')
    state = _identifier(lot.get('state'), 'Lot 状态')
    if state not in ('READY', 'FUTURE', 'HOLD', 'RUNNING', 'DONE'):
        raise ValueError('基线 Lot 状态无效')
    # Only first-step confirmed supply can create eligibility for new work.
    if kind == 'ETA':
        return 'observed', '预计到达只作参考', None
    if step != route[0]:
        return 'observed', '后续工序来料不触发本系统派工', None
    if route.count(step) != 1:
        return 'observed', '路线重复经过此工序，无法确认对应首道工序', None
    if state in ('HOLD', 'RUNNING', 'DONE'):
        return 'observed', '保留原状态 ' + state, None
    effective_state = 'READY' if at <= asof else 'FUTURE'
    if lot.get('availableAt'):
        if at == _timestamp(lot['availableAt'], '基线 availableAt') and state == effective_state:
            return 'unchanged', '可用时间和状态已一致', None
        return 'observed', '已有可用时间，保留基线', None
    if state != 'FUTURE':
        return 'observed', '仅对未确认可用时间的未来来料生效', None
    return 'applied', '已确认首道工序可用时间', {'availableAt': at.isoformat(), 'state': effective_state}


def preview_wip(snapshot, feed):
    """Return an isolated candidate and row diagnostics, applying the entire feed or none."""
    candidate = deepcopy(snapshot)
    report = dict(accepted=False, rows=[], applied=0, observed=0, errors=[])
    try:
        lots, routes, asof = _base_index(candidate, feed)
    except ValueError as error:
        report['errors'].append(str(error))
        return candidate, report

    changes = []
    seen = set()
    for index, row in enumerate(feed['records'], 1):
        detail = dict(uid='', step='', kind='', status='', reason='')
        try:
            status, reason, change = _classify(row, detail, lots, routes, asof, seen)
            if change is not None:
                changes.append((lots[detail['uid']], change))
        except ValueError as error:
            status, reason = 'error', str(error)
            report['errors'].append('第 %d 行 %s' % (index, reason))
        detail.update(status=status, reason=reason)
        report['rows'].append(detail)
    report['observed'] = sum(detail['status'] == 'observed' for detail in report['rows'])
    if report['errors']:
        for detail in report['rows']:
            if detail['status'] == 'applied':
                detail.update(status='blocked', reason='同批记录有误，未应用此行')
        return candidate, report
    for lot, change in changes:
        lot.update(change)
    report.update(accepted=True, applied=len(changes))
    return candidate, report
