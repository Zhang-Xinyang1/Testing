"""Offline run records and independent checks. No outbound network or export."""
import hashlib
import json
import math
import os
import re
import tempfile
import threading
import uuid
from datetime import datetime, timezone, timedelta

VERSION = 'diag-2-wafer'
LOCK = threading.Lock()
CATALOG = {
    'R01': ('机台重叠', '已确认', '查看冲突时段，核对资源占用。'),
    'R02': ('针卡重叠', '已确认', '查看同卡任务，核对安装与释放时间。'),
    'R03': ('工序顺序异常', '已确认', '检查 CP、BAKE 和 QTIME 的衔接。'),
    'R04': ('PM 越窗', '已确认', '检查维护窗口和连续时长。'),
    'R05': ('借机冲突', '已确认', '避开 PM、在制占用和其他借机。'),
    'R06': ('在制被重排', '已确认', '在制 Lot 应整批排除。'),
    'R07': ('BAKE 超容量', '已确认', '每腔每批不超过 4 lot，Recipe 一致。'),
    'R08': ('禁加工或资源资格异常', '已确认', '核对 RUN、机台组、候选针卡和禁加工规则。'),
    'R09': ('BAKE 时间异常', '已确认', '升温 2h、Recipe 加工 24h、降温 2h；加工结束放行 Lot，降温结束释放腔体。'),
    'C01': ('无可用资源', '待核查', '核对工艺、机台 TYPE 和候选针卡。'),
    'C02': ('RUN 缺快照', '已确认', '补充在制资源占用和预计释放时间。'),
    'P01': ('月度未达成', '已确认', '当前方案存在缺口，不等于已证明无解。'),
    'P02': ('完成量下降', '待核查', '比较输入及策略变化，不能直接认定算法错误。'),
    'S01': ('排产失败', '待核查', '在内网页面查看错误详情。'),
    'S02': ('排产耗时较长', '已确认', '检查输入规模和浏览器负载。'),
    'S03': ('重复运行结果变化', '待核查', '相同输入与策略产生不同任务，请检查确定性。'),
}


def ms(value):
    if isinstance(value, (int, float)) and math.isfinite(value):
        return value
    if not value:
        return None
    dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone(timedelta(hours=8)))
    return dt.timestamp() * 1000


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def inspect_run(body):
    data, result = body['input'], body.get('result') or {}
    findings = {}
    def add(code, detail):
        entry = findings.setdefault(code, {'code': code, 'title': CATALOG[code][0],
            'certainty': CATALOG[code][1], 'action': CATALOG[code][2], 'count': 0, 'details': []})
        entry['count'] += 1
        if len(entry['details']) < 20:
            entry['details'].append(detail)
    if body.get('error'):
        add('S01', str(body['error'])[:3000])
        return list(findings.values()), None
    tasks, batches = result.get('cpTasks', []), result.get('batches', [])
    wip = data.get('wip', [])
    excluded = {w['lotId'] for w in wip}
    calendar = {}
    def reserve(key, start, end, label, kind):
        if start is None or end is None or end <= start:
            return
        calendar.setdefault(key, []).append((start, end, label, kind))
    def overlaps(key, entries):
        active = []
        for item in sorted(entries):
            active = [other for other in active if other[1] > item[0]]
            for other in active:
                # Multiple WIP lots in a BAKE batch share one occupied interval.
                if item[3] == other[3] == 'wip' and key.startswith('chamber:') and item[:2] == other[:2]:
                    continue
                code = 'R02' if key.startswith('card:') else 'R05' if 'borrow' in (item[3], other[3]) else 'R01'
                add(code, '%s：%s / %s' % (key, other[2], item[2]))
            active.append(item)
    eqps = {e['id']: e for e in data.get('eqp', [])}
    for w in wip:
        resource = w.get('chamber') or w.get('eqp')
        if resource:
            prefix = 'chamber:' if w.get('chamber') else 'machine:'
            reserve(prefix + resource, ms(w['start']), ms(w['end']), w['lotId'], 'wip')
        card = w.get('card') or eqps.get(w.get('eqp'), {}).get('card')
        if False and card and resource:
            reserve('card:' + card, ms(w['start']), ms(w['end']), w['lotId'], 'wip')
    lot_products={l['id']:l['prod'] for l in result.get('lots', [])}
    products={m['ppn']:m for m in data.get('mps', [])}
    releases={r[0]:r[4] for r in data.get('lots', [])}
    wafer_by_lot={r[0]:r[3] for r in data.get('lots', [])}
    by_lot = {}
    for t in tasks:
        start, end = t.get('blockStart', t['start']), t['end']
        if t['lotId'] in excluded:
            add('R06', t['lotId'])
        reserve('machine:' + t['machine'], start, end, t['lotId'] + '/' + t['step'], 'cp')
        machine=eqps.get(t['machine'], {})
        product=products.get(t.get('prod') or lot_products.get(t['lotId']))
        allowed=re.split(r'[;,，；/|]+', product.get('platform', '')) if product else []
        cards=next((r['cards'] for r in data.get('recipes', []) if r['recipe']==t.get('recipe')), [])
        denied=any(c['eqp']==t['machine'] and c['recipe']==t.get('recipe') for c in data.get('constraints', []))
        if machine.get('status')!='RUN' or (allowed and machine.get('type') not in allowed) or (cards and t['card'] not in cards) or denied:
            add('R08', t['lotId']+'/'+t['step'])
        by_lot.setdefault(t['lotId'], []).append(t)
    for lot, steps in by_lot.items():
        steps.sort(key=lambda task: task.get('opIndex', task['start']))
        for a, b in zip(steps, steps[1:]):
            if b['start'] < a['end']:
                add('R03', lot)
    for b in batches:
        if len(b['lots']) > 4:
            add('R07', b['id'])
        reserve('chamber:' + b['chamber'], b['start'], b['end'], b['id'], 'bake')
        if (b.get('processStart', 0)-b['start'] != 7200000 or b.get('processEnd', 0)-b.get('processStart', 0) != 86400000 or b['end']-b.get('processEnd', 0) != 7200000):
            add('R09', b['id'])
        for item in b['lots']:
            lid = item['id'] if isinstance(item, dict) else item
            if lid in excluded:
                add('R06', lid)
    for p in result.get('pmRecords', []):
        if p['start'] < ms(p['early']) or p['end'] > ms(p['over']) + 86400000:
            add('R04', p['id'])
        for resource in p['resources']:
            reserve(('machine:' if resource in eqps and 'OVEN' not in eqps[resource]['type'].upper() else 'chamber:') + resource,
                    p['start'], p['end'], p['id'], 'pm')
    for b in result.get('borrows', []):
        if b['status'] == 'cancel':
            add('R05', b['id'] + '：' + b.get('reason', '未执行'))
        else:
            reserve('machine:' + b['eqp'], b['start'], b['end'], b['id'], 'borrow')
    for key, entries in calendar.items():
        overlaps(key, entries)
    completed = {}
    month_end = ms(data['meta']['start']) + data['meta']['days'] * 86400000
    flows = data.get('flows', {})
    for lot in result.get('lots', []):
        if lot['id'] in excluded:
            add('R06', lot['id'])
        if lot.get('unscheduledReason'):
            add('C01', lot['id'] + '：' + lot['unscheduledReason'])
        if lot.get('complete') is not None and lot['complete'] <= month_end:
            completed[lot['prod']] = completed.get(lot['prod'], 0) + wafer_by_lot.get(lot['id'], lot.get('wafer', 0))
        route=lot.get('routeTasks', [])
        expected=flows.get(lot['prod'], [])
        if route and len(route)!=len(expected):
            add('R03', lot['id']+'：路线工序数量不符')
        previous=ms(releases.get(lot['id'], data['meta']['start']))
        for index, task in enumerate(route):
            if index>=len(expected):
                break
            op=expected[index]
            if task['step']!=op[0] or task['recipe']!=op[1] or task['start']<previous:
                add('R03', lot['id']+'：工序顺序或就绪时间异常')
            if op[0]=='QTIME1' and task['end']-task['start'] != int(op[1])*60000:
                add('R03', lot['id']+'：QTIME 不足')
            previous=task['end']
        if route and lot.get('complete') != previous:
            add('R03', lot['id']+'：末工序与最终完成时间不一致')
    for m in data.get('mps', []):
        done = completed.get(m['ppn'], 0)
        if done < m['mps']:
            add('P01', '%s：%d / %d' % (m['ppn'], done, m['mps']))
    if result.get('runMs', 0) > 5000:
        add('S02', '%d ms' % result['runMs'])
    return list(findings.values()), sum(completed.values())


def directory(config_path):
    return os.path.join(os.path.dirname(config_path), 'runs')


def history(config_path):
    root = directory(config_path)
    if not os.path.isdir(root):
        return []
    result = []
    for name in sorted(os.listdir(root), reverse=True):
        if re.fullmatch(r'[0-9]{8}T[0-9]{12}-[a-f0-9]{8}\.json', name):
            with open(os.path.join(root, name), encoding='utf-8') as f:
                result.append(json.load(f)['report'])
            if len(result) == 20:
                break
    return result


def record_run(config_path, body):
    if not isinstance(body, dict) or not isinstance(body.get('input'), dict):
        raise ValueError('缺少排产输入快照')
    input_hash = digest({'input': body['input'], 'options': body.get('options', {})})
    schedule = body.get('result') or {}
    output_hash = digest({key: schedule.get(key) for key in ('cpTasks', 'batches', 'lots', 'borrows', 'pmRecords')})
    findings, completed = inspect_run(body)
    with LOCK:
        previous = history(config_path)
        last = next((r for r in previous if r.get('unit') == 'wafer'), None)
        if completed is not None and last and last.get('completed') is not None and completed < last['completed']:
            findings.append({'code': 'P02', 'title': CATALOG['P02'][0], 'certainty': '待核查', 'action': CATALOG['P02'][2], 'count': 1, 'details': ['比上次减少 %d wafer' % (last['completed'] - completed)]})
        same = next((r for r in previous if r['inputHash'] == input_hash and r['version'] == VERSION and not r.get('failed')), None)
        if same and completed is not None and same['outputHash'] != output_hash:
            findings.append({'code': 'S03', 'title': CATALOG['S03'][0], 'certainty': '待核查', 'action': CATALOG['S03'][2], 'count': 1, 'details': ['与运行 ' + same['id'] + ' 比较']})
        now = datetime.now(timezone(timedelta(hours=8)))
        run_id = now.strftime('%Y%m%dT%H%M%S%f') + '-' + uuid.uuid4().hex[:8]
        codes = [f['code'] for f in findings]
        report = {'id': run_id, 'version': VERSION, 'time': now.isoformat(), 'completed': completed, 'unit': 'wafer',
                  'failed': bool(body.get('error')), 'inputHash': input_hash, 'outputHash': output_hash,
                  'findings': findings, 'trigger': body.get('trigger', '手动重排'),
                  'comparison': None if not last else {'previous': last['id'], 'sameInput': last['inputHash'] == input_hash,
                      'completedDelta': None if completed is None or last.get('completed') is None else completed - last['completed'],
                      'newCodes': sorted(set(codes) - {f['code'] for f in last['findings']})},
                  'feedback': VERSION + '｜' + ('、'.join(codes) if codes else '未发现已检查项异常') + '｜' + str(body.get('trigger', '手动重排'))[:40]}
        root = directory(config_path)
        os.makedirs(root, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=root, suffix='.tmp')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump({'report': report, 'snapshot': body}, f, ensure_ascii=False, allow_nan=False)
            os.replace(tmp, os.path.join(root, run_id + '.json'))
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        return report
