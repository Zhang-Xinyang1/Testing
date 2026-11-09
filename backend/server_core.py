#!/usr/bin/env python3
"""Config-driven Excel loader for the Testing scheduling demo (stdlib only)."""
import json, os, re, math, tempfile, urllib.request
from datetime import datetime, timedelta, timezone
from data_sources import oracle_settings, read_oracle

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
import sys
sys.path.insert(0, os.path.join(ROOT, 'tools'))
from build_data import read_workbook, clean_rows, num  # noqa: E402

DEFAULT_CONFIG = os.path.join(ROOT, 'config', 'config.json')


def norm_key(value):
    """Normalize a sheet/header name for tolerant matching.

    Case, whitespace, full-width spaces, BOM, underscores, hyphens and dots are
    ignored: ``EQP ID``, ``EQPID``, ``eqp_id`` and ``eqp-id`` are equivalent.
    """
    text = str(value or '').replace('\ufeff', '').replace('\xa0', ' ')
    text = text.casefold()
    return re.sub(r'[\s_\-.&＆]+', '', text)


def resolve_column(header, wanted):
    """Return the actual header name matching ``wanted``, or None."""
    if not wanted:
        return None
    if wanted in header:
        return wanted
    target = norm_key(wanted)
    for name in header:
        if norm_key(name) == target:
            return name
    return None


def mapped_column(header, value):
    """Resolve a string or a list of acceptable header names."""
    values = value if isinstance(value, (list, tuple)) else [value]
    for candidate in values:
        resolved = resolve_column(header, candidate)
        if resolved:
            return resolved
    return None


def parse_datetime(value):
    """Parse Excel/ISO date text, accepting common non-ISO separators."""
    text = str(value).strip()
    if not text:
        raise ValueError('日期为空')
    text = text.replace('年', '-').replace('月', '-').replace('日', '')
    text = text.replace('/', '-')
    if ' ' in text and 'T' not in text:
        text = text.replace(' ', 'T', 1)
    if 'T' not in text:
        text += 'T00:00:00+08:00'
    text = text.replace('Z', '+00:00')
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        match = re.match(r'^(\d{4})-(\d{1,2})-(\d{1,2})'
                         r'(?:T(\d{1,2}):(\d{2})(?::(\d{2}))?)?', text)
        if not match:
            raise
        parsed = datetime(
            int(match.group(1)), int(match.group(2)), int(match.group(3)),
            int(match.group(4) or 0), int(match.group(5) or 0),
            int(match.group(6) or 0), tzinfo=timezone(timedelta(hours=8)))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone(timedelta(hours=8)))
    return parsed


def route_patterns():
    with open(os.path.join(ROOT, 'config', 'route_patterns.json'), encoding='utf-8') as f:
        return json.load(f)


def load_config(path=DEFAULT_CONFIG):
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def read_source(path):
    """Read a local path or http(s) Excel URL and return a local filename."""
    if path.startswith('http://') or path.startswith('https://'):
        data = urllib.request.urlopen(path, timeout=60).read()
        fd, tmp = tempfile.mkstemp(suffix='.xlsx')
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
        return tmp
    p = path if os.path.isabs(path) else os.path.join(ROOT, path)
    if not os.path.exists(p):
        raise FileNotFoundError('Excel not found: ' + p)
    return p


def rows_for(sheets, sheet_name, header_row=1):
    def trimmed(rows):
        return clean_rows(rows[max(0, int(header_row) - 1):])

    for name, rows in sheets:
        if name == sheet_name:
            return trimmed(rows)
    target = norm_key(sheet_name)
    for name, rows in sheets:
        if norm_key(name) == target:
            return trimmed(rows)
    raise KeyError('Sheet not found: ' + sheet_name)


def flow_product(lot_id, explicit, flows):
    """Resolve the flow key from an explicit PPN or the lot-id prefix.

    ``PPN1-1`` and ``PPN1-2`` belong to the same flow ``PPN1``; the suffix is
    only a vendor marker.  Prefer an explicit, valid PPN, then the full lot id,
    then the longest prefix that exists in the flow table.
    """
    if explicit in flows:
        return explicit
    text = str(lot_id).strip()
    if text in flows:
        return text
    parts = text.split('-')
    for size in range(len(parts) - 1, 0, -1):
        prefix = '-'.join(parts[:size]).strip()
        if prefix in flows:
            return prefix
    return explicit or (parts[0].strip() if parts else '')


def base_ppn(value):
    """Product/flow key before the first hyphen; vendor suffix is ignored."""
    return str(value or '').strip().split('-', 1)[0].strip()


def merge_mps(rows):
    """Merge MPS rows by PPN prefix; multiple orders/vendors are summed.

    Platform groups are unioned in first-seen order and joined with ``;``.
    """
    merged, order = {}, []
    for row in rows or []:
        ppn = base_ppn(row.get('ppn', ''))
        if not ppn:
            continue
        if ppn not in merged:
            merged[ppn] = {'platform': '', 'ppn': ppn, 'mps': 0,
                           '_platforms': []}
            order.append(ppn)
        entry = merged[ppn]
        quantity = row.get('mps')
        if isinstance(quantity, str):
            quantity = num(quantity, 0)
        entry['mps'] += quantity or 0
        for group in re.split(r'[;,，；/|]+', str(row.get('platform') or '')):
            group = group.strip()
            if group and group not in entry['_platforms']:
                entry['_platforms'].append(group)
    result = []
    for ppn in order:
        entry = merged[ppn]
        entry['platform'] = ';'.join(entry.pop('_platforms'))
        result.append(entry)
    return result


def public_config(config_path=DEFAULT_CONFIG):
    cfg = load_config(config_path)
    src = cfg.get('dataSource', {})
    path = src.get('path', '')
    exists = None
    if path and not path.startswith('http'):
        exists = os.path.exists(path if os.path.isabs(path) else os.path.join(ROOT, path))
    return {
        'configPath': os.path.relpath(config_path, ROOT),
        'dataSource': src,
        'fieldMap': cfg.get('fieldMap', {}),
        'scheduling': cfg.get('scheduling', {}),
        'oracle': oracle_settings(cfg.get('oracle', {})),
        'serverMode': True,
        'sourceExists': exists,
    }


def build_payload(config_path=DEFAULT_CONFIG, config=None):
    cfg = config if config is not None else load_config(config_path)
    src = cfg.get('dataSource', {})
    if src.get('type', 'excel') == 'oracle':
        read_oracle(cfg.get('oracle', {}))
    if src.get('type', 'excel') != 'excel':
        raise ValueError('不支持的数据源类型')
    oracle_settings(cfg.get('oracle', {}))
    smap = src.get('sheetMap', {})
    fm = cfg.get('fieldMap', {})
    source_path = src.get('path', '')
    if not source_path:
        raise ValueError('dataSource.path is empty in config.json')
    filename = read_source(source_path)
    try:
        wb = read_workbook(filename)
    finally:
        if source_path.startswith(('http://', 'https://')):
            os.unlink(filename)

    sheet_headers = {}

    def mapping_for(sheet):
        if sheet in fm:
            return fm[sheet]
        target = norm_key(sheet)
        for name, mapping in fm.items():
            if norm_key(name) == target:
                return mapping
        return {}

    def rows_of(key):
        name = smap.get(key)
        if not name:
            return [], []
        try:
            header, rows = rows_for(wb, name, src.get('headerRow', 1))
        except KeyError:
            # Seven-sheet deployments omit the optional helper sheets.
            if key in ('params', 'lots', 'weekly', 'wip'):
                return [], []
            raise
        sheet_headers[name] = header
        mapping = mapping_for(name)
        required = {
            'mps': ('platform', 'ppn', 'mps'), 'eqp': ('type', 'id'),
            'cards': ('recipe', 'cards'), 'flow': ('prod', 'step', 'recipe', 'time'),
            'borrow': ('date', 'eqp', 'hours'), 'pm': ('eqp', 'early', 'due', 'over', 'hours'),
            'lots': ('lotId', 'wafer'), 'wip': ('lotId',),
        }
        for logical in required.get(key, ()):
            column = mapping.get(logical)
            if not mapped_column(header, column):
                if isinstance(column, (list, tuple)):
                    display = ' / '.join(map(str, column))
                else:
                    display = column
                raise ValueError('字段映射不存在：%s.%s → %s；实际表头：%s'
                                 % (name, logical, display,
                                    '、'.join(map(str, header))))
        return header, rows

    def field(key, name, default=''):
        sheet = smap.get(key, '')
        mapping = mapping_for(sheet)
        wanted = mapping.get(name, default)
        header = sheet_headers.get(sheet)
        if header is None:
            return wanted if isinstance(wanted, str) else default
        resolved = mapped_column(header, wanted)
        if resolved is not None:
            return resolved
        return wanted if isinstance(wanted, str) else default

    def get(row, key, name, default=''):
        col = field(key, name, default)
        value = row.get(col, default) if col else default
        return value.strip() if isinstance(value, str) else value

    def getn(row, key, name, default=0):
        return num(get(row, key, name, default), default)

    # ---- MPS ----
    _, mps_rows = rows_of('mps')
    mps = []
    for r in mps_rows:
        ppn = str(get(r, 'mps', 'ppn', '')).strip()
        if not ppn:
            continue
        quantity = getn(r, 'mps', 'mps', float('nan'))
        if not math.isfinite(quantity) or quantity < 0 or quantity != int(quantity):
            raise ValueError('MPS 必须为非负整数 wafer 数：' + ppn)
        mps.append({'platform': str(get(r, 'mps', 'platform', '')).strip(),
                    'ppn': ppn, 'mps': int(quantity)})
    mps = merge_mps(mps)
    prod_platform = {m['ppn']: m['platform'] for m in mps}

    # ---- EQP ----
    _, eqp_rows = rows_of('eqp')
    eqp = []
    for r in eqp_rows:
        eqp.append({'type': str(get(r, 'eqp', 'type', '')).strip(),
                    'id': str(get(r, 'eqp', 'id', '')).strip(),
                    'card': str(get(r, 'eqp', 'currentCard', '')).strip(),
                    'recipe': str(get(r, 'eqp', 'currentRecipe', '')).strip(),
                    'status': str(get(r, 'eqp', 'status', '') or r.get('Status', '') or r.get('status', '')).strip().upper()})

    # EQP's Current ProbeCard is the authoritative physical-card -> TYPE mapping.
    card_type = {e['card']: e['type'] for e in eqp if e.get('card')}

    # ---- Probe cards ----
    _, card_rows = rows_of('cards')
    sep = mapping_for(smap.get('cards', '')).get('separator', ';')
    if isinstance(sep, (list, tuple)):
        sep = sep[0] if sep else ';'
    sep = sep or ';'
    cards = []
    for r in card_rows:
        recipe = str(get(r, 'cards', 'recipe', '')).strip()
        raw = str(get(r, 'cards', 'cards', '') or r.get('ProberCard List', '') or r.get('ProbeCard List', ''))
        cards.append({'recipe': recipe, 'cards': [x.strip() for x in raw.split(sep) if x.strip()]})

    # ---- Flow ----
    # CP.TIME is minutes per wafer; BAKE/QTIME override duration from RECIPE.
    _, flow_rows = rows_of('flow')
    flow_order = {'BAKE': 90, 'QTIME1': 100}
    fl = {}
    for r in flow_rows:
        prod = str(get(r, 'flow', 'prod', '')).strip()
        step = str(get(r, 'flow', 'step', '')).strip()
        if not prod or not step:
            continue
        order = len(fl.get(prod, []))
        recipe = str(get(r, 'flow', 'recipe', '')).strip()
        duration = getn(r, 'flow', 'time', 0)
        if step == 'BAKE':
            match = re.fullmatch(r'(210|250)C(24)H', recipe)
            if not match:
                raise ValueError('BAKE Recipe 仅允许 210C24H 或 250C24H：' + prod)
            duration = int(match.group(2))
        elif step == 'QTIME1':
            if recipe not in ('7', '14', '28'):
                raise ValueError('QTIME1 Recipe 仅允许 7、14、28：' + prod)
            duration = int(recipe)
        elif step not in ('CP1', 'CP2', 'CP3', 'CP4') or duration <= 0:
            raise ValueError('未知 STEP 或无效 CP 工时：' + prod + '/' + step)
        fl.setdefault(prod, []).append({
            'step': step,
            'recipe': recipe,
            'time': duration,
            'order': order,
        })
    for p in fl:
        fl[p].sort(key=lambda x: x['order'])
    flows = {p: [[x['step'], x['recipe'], x['time']] for x in ops] for p, ops in fl.items()}

    # ---- PM / constraints / borrow / weekly ----
    _, pm_rows = rows_of('pm')
    pm = [{'eqp': str(get(r, 'pm', 'eqp', '')).strip(),
           'early': get(r, 'pm', 'early', ''), 'due': get(r, 'pm', 'due', ''),
           'over': get(r, 'pm', 'over', ''), 'hours': getn(r, 'pm', 'hours', 0)} for r in pm_rows]

    _, con_rows = rows_of('constraints')
    constraints = [{'type': str(get(r, 'constraints', 'type', '')).strip(),
                    'eqp': str(get(r, 'constraints', 'eqp', '')).strip(),
                    'recipe': str(get(r, 'constraints', 'recipe', '')).strip()} for r in con_rows]

    _, borrow_rows = rows_of('borrow')
    borrow = [{'date': get(r, 'borrow', 'date', ''), 'eqp': str(get(r, 'borrow', 'eqp', '')).strip(),
               'hours': getn(r, 'borrow', 'hours', 0)} for r in borrow_rows]

    _, weekly_rows = rows_of('weekly')
    weekly = [{'platform': str(get(r, 'weekly', 'platform', '')).strip(),
               'ppn': base_ppn(get(r, 'weekly', 'ppn', '')),
               'week': str(get(r, 'weekly', 'week', '')).strip(),
               'start': get(r, 'weekly', 'start', ''), 'end': get(r, 'weekly', 'end', ''),
               'target': int(getn(r, 'weekly', 'target', 0))} for r in weekly_rows]

    # ---- params / mappings / borrow generation times ----
    _, params = rows_of('params')

    def pget(r, key, default=''):
        return r.get(field('params', key, default), default)

    platform_type = {}
    for r in params:
        cat, par = str(pget(r, 'category')), str(pget(r, 'parameter'))
        if cat == '映射' and par == 'Platform对应TYPE':
            platform_type[str(pget(r, 'object'))] = str(pget(r, 'value'))
        if cat == '资源' and par == '物理针卡对应TYPE':
            card_type[str(pget(r, 'object'))] = str(pget(r, 'value'))
    for machine in eqp:
        if 'OVEN' not in machine['type'].upper():
            platform_type[machine['type']] = machine['type']
    for r in cards:
        for cid in r['cards']:
            if cid not in card_type:
                card_type[cid] = '共享类型'

    for r in params:
        if str(pget(r, 'category')) != '借机产生时刻':
            continue
        m = re.search(r'第(\d+)行', str(pget(r, 'object')))
        if m:
            idx = int(m.group(1)) - 2
            if 0 <= idx < len(borrow):
                borrow[idx]['generatedAt'] = str(pget(r, 'value'))

    rules = [{'category': str(pget(r, 'category')), 'parameter': str(pget(r, 'parameter')),
              'object': str(pget(r, 'object')), 'value': str(pget(r, 'value')),
              'unit': str(pget(r, 'unit')), 'basis': str(pget(r, 'basis'))}
             for r in params
             if str(pget(r, 'category')) != '借机产生时刻'
             and not (str(pget(r, 'category')) == '资源' and str(pget(r, 'parameter')) == '物理针卡对应TYPE')]

    # ---- Lots ----
    _, lot_rows = rows_of('lots')
    month = src.get('month') or '2026-09'
    y, mo = [int(x) for x in month.split('-')[:2]]
    month_start = '%04d-%02d-01' % (y, mo)
    ny, nm = (y + 1, 1) if mo == 12 else (y, mo + 1)
    next_month = '%04d-%02d-01' % (ny, nm)
    start_dt = datetime.strptime(month_start, '%Y-%m-%d')
    lots = []
    if lot_rows:
        for r in lot_rows:
            lid = str(get(r, 'lots', 'lotId', '')).strip()
            if not lid:
                continue
            prod = str(get(r, 'lots', 'ppn', '')).strip()
            # Actual lot files often encode the flow/product before the first
            # hyphen (PPN1-1, PPN1-2, ...). The suffix is a vendor marker and
            # is not part of the product key.
            prod = flow_product(lid, prod, flows)
            lots.append([
                lid, prod,
                str(get(r, 'lots', 'platform', '')).strip() or prod_platform.get(prod, ''),
                getn(r, 'lots', 'wafer', 0),
                get(r, 'lots', 'release', '') or month_start,
                str(get(r, 'lots', 'week', '')).strip() or 'W1',
                get(r, 'lots', 'weeklyDue', '') or (start_dt + timedelta(days=6)).strftime('%Y-%m-%d'),
                get(r, 'lots', 'monthlyDue', '') or next_month,
                int(getn(r, 'lots', 'priority', 1)),
            ])
    else:
        for m in mps:
            # This is a fallback assumption, not a restriction on actual Lot sizes.
            for offset in range(0, m['mps'], 25):
                lots.append(['%s-L%04d' % (m['ppn'], offset // 25 + 1), m['ppn'], m['platform'],
                             min(25, m['mps'] - offset),
                             month_start, 'W1', (start_dt + timedelta(days=6)).strftime('%Y-%m-%d'),
                             next_month, 1])

    summary = []
    for name, rows in wb:
        header, cleaned = clean_rows(rows)
        summary.append({'name': name, 'rows': len(cleaned), 'fields': header})

    meta = {
        'month': month,
        'start': month_start + 'T00:00:00+08:00',
        'days': (datetime.strptime(next_month, '%Y-%m-%d') - start_dt).days,
        'source': src.get('displayName') or source_path,
        'configPath': os.path.relpath(config_path, ROOT),
        'loadedAt': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'serverMode': True,
        'mpsUnit': 'wafer',
        'lotSizing': 'actual' if lot_rows else 'default-25-tail',
    }
    payload = {
        'meta': meta, 'mps': mps, 'eqp': eqp, 'flows': flows, 'recipes': cards,
        'pm': pm, 'borrow': borrow, 'weekly': weekly, 'constraints': constraints,
        'lotFields': ['LotID', 'PROD', 'Platform', 'Wafer Qty', 'Release Time', 'Target Week',
                      'Weekly Due', 'Monthly Due', 'Priority'],
        'lots': lots, 'platformType': platform_type, 'cardType': card_type,
        'rules': rules, 'sheetSummary': summary, 'fieldMap': fm, 'sheetMap': smap,
    }
    overrides = cfg.get('scheduling', {})
    for key in ('eqp', 'pm', 'borrow', 'platformType', 'cardType'):
        if key in overrides:
            payload[key] = overrides[key]
    _, wip_rows = rows_of('wip')
    wip = [{key: get(r, 'wip', key, '') for key in ('lotId', 'eqp', 'chamber', 'card', 'start', 'end')} for r in wip_rows]
    payload['wip'] = overrides.get('wip', wip)
    ovens = [e for e in payload['eqp'] if 'OVEN' in e['type'].upper()]
    payload['bakeResources'] = {'machines': len(ovens), 'chambers': 4, 'ids': [e['id'] for e in ovens]} if ovens else overrides.get('bakeResources', {'machines': 7, 'chambers': 4})
    payload['schemaVersion'] = 'confirmed-2026-11-06'
    validate_schedule(payload)
    payload['planIssues'] = calendar_issues(payload)
    return payload


def validate_schedule(data):
    """Validate business references and occupied resource calendars, without Excel writes."""
    def require(condition, message):
        if not condition:
            raise ValueError(message)

    def number(value):
        return isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value)

    def date(value):
        return parse_datetime(value)

    for key in ('eqp', 'pm', 'borrow', 'wip'):
        require(isinstance(data[key], list), key + ' 必须为记录列表')
    for recipe in data['recipes']:
        require(1 <= len(recipe['cards']) <= 4,
                'Recipe 候选针卡类型必须为 1 至 4 种：' + recipe['recipe'])
        require(len(set(recipe['cards'])) == len(recipe['cards']),
                'Recipe 候选针卡不能重复：' + recipe['recipe'])
    ids = [e['id'] for e in data['eqp']]
    require(all(isinstance(x, str) and x.strip()  for x in ids)
            and len(set(ids)) == len(ids), '设备编号不能为空或重复')
    machines = {e['id']: e for e in data['eqp']}
    for e in data['eqp']:
        require(bool(e.get('type')), '机台 TYPE 不能为空')
    bake = data['bakeResources']
    for key in ('machines', 'chambers'):
        require(type(bake.get(key)) is int and 1 <= bake[key] <= 100, 'BAKE 数量和腔体数必须为 1 至 100 的整数')
    bake_ids = set(bake.get('ids', ['BAKE-%02d' % n for n in range(1, bake['machines'] + 1)]))
    chamber_ids = {e + '-C%d' % c for e in bake_ids for c in range(1, bake['chambers'] + 1)}
    require(bool(data['mps']), 'MPS 表没有有效数据，请检查字段映射')
    mps_rows = merge_mps(data['mps'])
    require(bool(mps_rows), 'MPS 表没有有效数据，请检查字段映射')
    lot_ids = [r[0] for r in data['lots']]
    require(all(isinstance(value, str) and value.strip() for value in lot_ids),
            'LotID 不能为空')
    lots = set(lot_ids)
    wafer_totals = {}
    product_ids = {m['ppn'] for m in mps_rows}
    flows = data.get('flows') or {}
    for lot in data['lots']:
        product = flow_product(lot[0], lot[1], flows)
        require(product in product_ids, 'Lot 引用未知 PPN：' + lot[0])
        require(number(lot[3]) and lot[3] == int(lot[3]) and 1 <= lot[3] <= 25,
                'Lot 的 wafer 片数必须为 1 至 25 的整数：' + lot[0])
        wafer_totals[product] = wafer_totals.get(product, 0) + lot[3]
    for m in mps_rows:
        require(number(m['mps']) and m['mps'] == int(m['mps']) and m['mps'] >= 0,
                'MPS 必须为非负整数 wafer 数')
        require(wafer_totals.get(m['ppn'], 0) == m['mps'],
                '产品 Lot 的 wafer 合计必须等于 MPS：' + m['ppn'])
        groups = [v.strip() for v in re.split(r'[;,，；/|]+', m['platform']) if v.strip()]
        cp_types = {e['type'] for e in data['eqp'] if 'OVEN' not in e['type'].upper()}
        require(bool(groups) and set(groups) <= cp_types, 'Platform 必须直接匹配 EQP.TYPE：' + m['platform'])
        require(m['ppn'] in data['flows'], '产品缺少路线：' + m['ppn'])
        steps = [op[0] for op in data['flows'][m['ppn']]]
        require(steps in route_patterns(),
                '产品 STEP 顺序不属于已确认的七种路线：' + m['ppn'] + ' / ' + ','.join(steps))
        for op in data['flows'][m['ppn']]:
            if op[0].startswith('CP'):
                require(any(r['recipe'] == op[1] for r in data['recipes']), 'CP Recipe 缺少候选针卡：' + op[1])
    seen = set()
    occupied, card_busy = {}, {}
    for w in data['wip']:
        lid, eqp = w['lotId'], w.get('eqp', '')
        require(lid in lots and lid not in seen, '在制 Lot 无效或重复：' + lid)
        seen.add(lid)
        # Empty resource means the lot is in a waiting stage; it is still excluded.
        if not eqp:
            require(not w.get('card') and not w.get('chamber'), '等待中的在制 Lot 不应填写针卡或腔体')
            continue
        require(eqp in machines or eqp in bake_ids, '在制机台不存在：' + eqp)
        start, end = date(w['start']), date(w['end'])
        require(end > start, '在制任务结束时间必须晚于开始时间')
        resource = eqp
        if eqp in bake_ids:
            resource = w.get('chamber', '')
            require(resource in chamber_ids and resource.startswith(eqp + '-'), 'BAKE 在制必须选择所属腔体')
            require(not w.get('card'), 'BAKE 在制不应填写 CP 针卡')
        else:
            require(not w.get('chamber'), 'CP 在制不应填写 BAKE 腔体')
            card = w.get('card') or machines[eqp].get('card')
            require(bool(card), 'CP 在制需要针卡类型')
            w['card'] = card
            card_busy.setdefault(card, []).append((start, end))
        for a, b, other in occupied.get(resource, []):
            if start < b and end > a:
                require(eqp in bake_ids and (start, end) == (a, b), '在制资源时间冲突：' + resource)
        occupied.setdefault(resource, []).append((start, end, lid))
    for resource, entries in occupied.items():
        if resource in chamber_ids:
            groups = {}
            for a, b, lid in entries:
                groups.setdefault((a, b), []).append(lid)
            for group in groups.values():
                require(len(group) <= 4, 'BAKE 在制组批超过 4 lot')
    for key in ('borrow', 'pm'):
        for p in data[key]:
            require(p['eqp'] in (set(ids) if key == 'borrow' else set(ids) | bake_ids), '计划引用未知机台：' + p['eqp'])
            require(number(p['hours']) and 0 < p['hours'] <= 744, '计划时长必须大于 0 且不超过 744 小时')
            if key == 'borrow':
                require(0.6 <= p['hours'] <= 24, '借机时数必须为 0.6 至 24 小时')
                date(p.get('start') or p['date'])
            else:
                early, due, over = [date(p[k]).date() for k in ('early', 'due', 'over')]
                require(early <= due <= over, 'PM 日期必须满足 Early ≤ Due ≤ Over')
                if p.get('start'):
                    s = date(p['start'])
                    require(s.date() >= early and (s + timedelta(hours=p['hours']) - timedelta(microseconds=1)).date() <= over, 'PM 指定时间超出维护窗口')


def calendar_issues(data):
    """Check fixed resource plans before committing a configuration."""
    zone = timezone(timedelta(hours=8))
    def date(value):
        return parse_datetime(value)
    def overlaps(a, b):
        return a[0] < b[1] and a[1] > b[0]
    def gap(blocks, first, duration, last):
        current = first
        for a, b in sorted(blocks):
            if current + duration <= a:
                break
            if current < b:
                current = b
        return current if current <= last else None
    issues, blocked, pm_blocked = [], {}, {}
    for w in data['wip']:
        resource = w.get('chamber') or w.get('eqp')
        if resource:
            blocked.setdefault(resource, []).append((date(w['start']), date(w['end'])))
    for resource, spans in blocked.items():
        pm_blocked[resource] = spans.copy()
    for e in data['eqp']:
        missing = False
        if missing or e.get('status') == 'LOST':
            blocked.setdefault(e['id'], []).append((date(data['meta']['start']), datetime.max.replace(tzinfo=zone)))
        if missing:
            pm_blocked.setdefault(e['id'], []).append((date(data['meta']['start']), datetime.max.replace(tzinfo=zone)))
    for index, p in enumerate(data['pm']):
        resources = [p['eqp']]
        if p['eqp'] in set(data['bakeResources'].get('ids', [])) or p['eqp'].startswith('BAKE-'):
            resources = [p['eqp'] + '-C%d' % i for i in range(1, data['bakeResources']['chambers'] + 1)]
        spans = [span for resource in resources for span in pm_blocked.get(resource, [])]
        early = date(p['early'])
        duration = timedelta(hours=p['hours'])
        latest = date(p['over']) + timedelta(days=1) - duration
        start = gap(spans, date(p['start']) if p.get('start') else early, duration, latest)
        if start is None or (p.get('start') and start != date(p['start'])):
            issues.append('PM 第 %d 条 %s：与在制占用或其他 PM 冲突，窗口内无可用时段' % (index + 1, p['eqp']))
            continue
        for resource in resources:
            span = (start, start + duration)
            blocked.setdefault(resource, []).append(span)
            pm_blocked.setdefault(resource, []).append(span)
    for index, b in enumerate(data['borrow']):
        first = date(b.get('start') or b['date'])
        duration = timedelta(hours=b['hours'])
        latest = first if b.get('start') else date(b['date']) + timedelta(days=1) - timedelta(microseconds=1)
        start = gap(blocked.get(b['eqp'], []), first, duration, latest)
        if start is not None:
            blocked.setdefault(b['eqp'], []).append((start, start + duration))
        # Unavailable requests remain eligible for a production-preserving suggestion.
    return issues
