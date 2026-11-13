#!/usr/bin/env python3
"""Config-driven Excel loader for the Testing scheduling demo (stdlib only)."""
import json, os, re, math, tempfile, urllib.request
from datetime import datetime, timedelta, timezone
from data_sources import oracle_settings, read_oracle
from platforms import clean_type, normalize_platforms, split_platforms
from input_rules import base_ppn, flow_product, identifier, ordered_plans, parse_datetime, resolve_wip

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
import sys
sys.path.insert(0, os.path.join(ROOT, 'tools'))
from build_data import read_workbook, clean_rows, column_name, num  # noqa: E402

DEFAULT_CONFIG = os.path.join(ROOT, 'config', 'config.json')
EMPTY_OPTIONAL_SHEETS = {'pm', 'borrow', 'constraints', 'wip', 'weekly', 'params'}


def norm_key(value):
    """Normalize a sheet/header name for tolerant matching.

    Case, whitespace, full-width spaces, BOM, underscores, hyphens and dots are
    ignored: ``EQP ID``, ``EQPID``, ``eqp_id`` and ``eqp-id`` are equivalent.
    """
    text = identifier(value)
    text = text.casefold()
    return re.sub(r'[\s_\-.&＆]+', '', text)


def resolve_column(header, wanted):
    """Return the actual header name matching ``wanted``, or None."""
    if not wanted:
        return None
    target = norm_key(wanted)
    positions = [i for i, name in enumerate(header) if norm_key(name) == target]
    matches = [header[i] for i in positions]
    if len(matches) > 1:
        details = '、'.join('%s列（%s）' % (column_name(i), header[i]) for i in positions)
        raise ValueError('字段匹配不唯一：%s → %s；请给实际使用的列设置唯一表头。' % (wanted, details))
    return matches[0] if matches else None


def mapped_column(header, value):
    """Resolve a string or a list of acceptable header names."""
    values = value if isinstance(value, (list, tuple)) else [value]
    for candidate in values:
        resolved = resolve_column(header, candidate)
        if resolved:
            return resolved
    return None


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
        return clean_rows(rows[max(0, int(header_row) - 1):], reject_duplicates=False)

    target = norm_key(sheet_name)
    matches = [(name, rows) for name, rows in sheets if norm_key(name) == target]
    if len(matches) > 1:
        raise ValueError('Sheet 匹配不唯一：%s；请统一 Sheet 名称。' % sheet_name)
    if matches:
        return trimmed(matches[0][1])
    raise KeyError('Sheet not found: ' + sheet_name)


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
        for group in split_platforms(row.get('platform')):
            if group and group not in entry['_platforms']:
                entry['_platforms'].append(group)
    result = []
    for ppn in order:
        entry = merged[ppn]
        entry['platform'] = ';'.join(entry.pop('_platforms'))
        result.append(entry)
    return result


def display_path(path):
    """Use a relative label when possible, including Windows cross-drive configs."""
    try:
        return os.path.relpath(path, ROOT)
    except ValueError:
        return os.path.abspath(path)


def public_config(config_path=DEFAULT_CONFIG):
    cfg = load_config(config_path)
    src = cfg.get('dataSource', {})
    path = src.get('path', '')
    exists = None
    if path and not path.startswith('http'):
        exists = os.path.exists(path if os.path.isabs(path) else os.path.join(ROOT, path))
    return {
        'configPath': display_path(config_path),
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

    def source_context(sheet):
        label = src.get('displayName') or os.path.basename(source_path.replace('\\', '/'))
        return '文件「%s」（读取：%s），Sheet「%s」，表头第 %s 行：' % (
            label, source_path, sheet, src.get('headerRow', 1))

    def mapped_in_sheet(header, wanted, sheet):
        try:
            return mapped_column(header, wanted)
        except ValueError as error:
            raise ValueError(source_context(sheet) + str(error)) from error

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
            raise ValueError(source_context(name) + '已配置的 Sheet 不存在：%s → %s；请修正映射，'
                             '不使用此表时删除对应 Sheet 设置。' % (key, name))
        except ValueError as error:
            raise ValueError(source_context(name) + str(error)) from error
        sheet_headers[name] = header
        if not any(header) and not rows and key in EMPTY_OPTIONAL_SHEETS:
            return header, rows
        mapping = mapping_for(name)
        required = {
            'mps': ('platform', 'ppn', 'mps'), 'eqp': ('type', 'id'),
            'cards': ('recipe', 'cards'), 'flow': ('prod', 'step', 'recipe', 'time'),
            'borrow': ('date', 'eqp', 'hours'), 'pm': ('eqp', 'early', 'due', 'over', 'hours'),
            'lots': ('lotId', 'wafer'), 'wip': ('lotId',),
        }
        for logical in required.get(key, ()):
            column = mapping.get(logical)
            if not mapped_in_sheet(header, column, name):
                if isinstance(column, (list, tuple)):
                    display = ' / '.join(map(str, column))
                else:
                    display = column
                raise ValueError(source_context(name) + '字段映射不存在：%s.%s → %s；实际表头：%s'
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
        resolved = mapped_in_sheet(header, wanted, sheet)
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
        # Server exports use commas; retain configured separators as well.
        parts = re.split(r'[;,，；、\r\n]+|' + re.escape(sep), raw)
        cards.append({'recipe': recipe, 'cards': list(dict.fromkeys(identifier(x) for x in parts if identifier(x)))})

    # ---- Flow ----
    # CP.TIME is minutes per wafer; BAKE/QTIME override duration from RECIPE.
    _, flow_rows = rows_of('flow')
    flow_order = {'BAKE': 90, 'QTIME1': 100}
    fl = {}
    for r in flow_rows:
        prod = identifier(get(r, 'flow', 'prod', ''))
        step = identifier(get(r, 'flow', 'step', ''))
        if not prod or not step:
            continue
        order = len(fl.get(prod, []))
        recipe = identifier(get(r, 'flow', 'recipe', ''))
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
    if smap.get('lots') and not lot_rows and any(m['mps'] for m in mps):
        raise ValueError('已配置 Lot 明细表，但没有有效记录；请补齐明细或移除 lots 映射后使用默认拆批。')
    month = src.get('month') or '2026-09'
    y, mo = [int(x) for x in month.split('-')[:2]]
    month_start = '%04d-%02d-01' % (y, mo)
    ny, nm = (y + 1, 1) if mo == 12 else (y, mo + 1)
    next_month = '%04d-%02d-01' % (ny, nm)
    start_dt = datetime.strptime(month_start, '%Y-%m-%d')
    lots = []
    if lot_rows:
        for index, r in enumerate(lot_rows, 1):
            lid = str(get(r, 'lots', 'lotId', '')).strip()
            if not lid:
                raise ValueError('Lot 明细第 %d 条的 LotID 不能为空' % index)
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
        header, cleaned = clean_rows(rows[max(0, int(src.get('headerRow', 1)) - 1):], reject_duplicates=False)
        summary.append({'name': name, 'rows': len(cleaned), 'fields': header})

    meta = {
        'month': month,
        'start': month_start + 'T00:00:00+08:00',
        'days': (datetime.strptime(next_month, '%Y-%m-%d') - start_dt).days,
        'source': src.get('displayName') or source_path,
        'configPath': display_path(config_path),
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
    meta['eqpSource'] = 'override' if 'eqp' in overrides else 'excel'
    meta['excelCpTypes'] = sorted({clean_type(e['type']) for e in eqp
                                  if 'OVEN' not in clean_type(e['type']).upper()})
    for key in ('eqp', 'pm', 'borrow', 'platformType', 'cardType'):
        if key in overrides:
            payload[key] = overrides[key]
    _, wip_rows = rows_of('wip')
    wip = [{key: get(r, 'wip', key, '') for key in ('lotId', 'lotKey', 'eqp', 'chamber', 'card', 'start', 'end')} for r in wip_rows]
    payload['wip'] = overrides.get('wip', wip)
    payload = normalize_platforms(payload)
    ovens = [e for e in payload['eqp'] if 'OVEN' in e['type'].upper()]
    payload['bakeResources'] = {'machines': len(ovens), 'chambers': 4, 'ids': [e['id'] for e in ovens]} if ovens else overrides.get('bakeResources', {'machines': 7, 'chambers': 4})
    payload['schemaVersion'] = 'confirmed-2026-11-06'
    payload = validate_schedule(payload)
    payload['planIssues'] = calendar_issues(payload)
    return payload


def validate_schedule(data):
    """Validate business references and occupied resource calendars, without Excel writes."""
    data = normalize_platforms(data)
    def require(condition, message):
        if not condition:
            raise ValueError(message)

    def number(value):
        return isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value)

    def date(value):
        return parse_datetime(value)

    require(type(data['meta'].get('days')) is int and 1 <= data['meta']['days'] <= 366,
            '排产天数必须为 1 至 366 的整数')
    date(data['meta'].get('start'))
    for key in ('eqp', 'pm', 'borrow', 'wip', 'mps', 'lots', 'recipes'):
        require(isinstance(data[key], list), key + ' 必须为记录列表')
    recipe_ids = [r.get('recipe') for r in data['recipes']]
    require(all(isinstance(r, str) and r.strip() for r in recipe_ids)
            and len(set(recipe_ids)) == len(recipe_ids), 'Recipe 名称不能为空或重复')
    for recipe in data['recipes']:
        require(isinstance(recipe['cards'], list) and len(recipe['cards']) >= 1,
                'Recipe 至少需要一种候选针卡类型：' + recipe['recipe'])
        require(all(isinstance(c, str) and c.strip() for c in recipe['cards']),
                'Recipe 候选针卡类型不能为空：' + recipe['recipe'])
        require(len(set(recipe['cards'])) == len(recipe['cards']),
                'Recipe 候选针卡不能重复：' + recipe['recipe'])
    ids = [e['id'] for e in data['eqp']]
    require(all(isinstance(x, str) and x.strip()  for x in ids)
            and len(set(ids)) == len(ids), '设备编号不能为空或重复')
    machines = {e['id']: e for e in data['eqp']}
    for e in data['eqp']:
        require(bool(e.get('type')), '机台 TYPE 不能为空')
        require(len(split_platforms(e['type'])) == 1, '每台设备只能填写一个 TYPE：' + e['id'])
    cp_types = {e['type'] for e in data['eqp'] if 'OVEN' not in e['type'].upper()}

    def validate_platform(value, label):
        selected = split_platforms(value)
        missing = [name for name in selected if name not in cp_types]
        if selected and not missing:
            return
        meta = data.get('meta') or {}
        source = {'override': '手工机台配置（覆盖 Excel EQP）',
                  'excel': 'Excel EQP 表'}.get(meta.get('eqpSource'), '本次排产输入')
        message = ('Platform 未匹配 EQP.TYPE（%s）：%s；缺少机台组：%s；'
                   '当前 EQP.TYPE：%s；机台来源：%s。TYPE 允许重复，EQPID 必须唯一。'
                   % (label, value or '空', '、'.join(missing) or 'Platform 为空',
                      '、'.join(sorted(cp_types)) or '无 CP 机台组', source))
        if meta.get('eqpSource') == 'override':
            message += (' Excel 原表 TYPE：%s。请核对页面机台配置；'
                        '“恢复参考配置”会清空机台、PM、借机和在制的全部手工覆盖。'
                        % ('、'.join(meta.get('excelCpTypes') or []) or '无'))
        raise ValueError(message)
    bake = data['bakeResources']
    for key in ('machines', 'chambers'):
        require(type(bake.get(key)) is int and 1 <= bake[key] <= 100, 'BAKE 数量和腔体数必须为 1 至 100 的整数')
    oven_ids = bake.get('ids', ['BAKE-%02d' % n for n in range(1, bake['machines'] + 1)])
    require(isinstance(oven_ids, list) and len(oven_ids) == bake['machines']
            and all(isinstance(x, str) and x.strip() for x in oven_ids)
            and len(set(oven_ids)) == len(oven_ids), 'BAKE 编号必须非空、唯一且与台数一致')
    bake_ids = set(oven_ids)
    cp_ids = {e['id'] for e in data['eqp'] if e['type'] in cp_types}
    require(not (bake_ids & cp_ids), 'BAKE 编号不能与 CP 机台重复')
    chamber_ids = {e + '-C%d' % c for e in bake_ids for c in range(1, bake['chambers'] + 1)}
    require(bool(data['mps']), 'MPS 表没有有效数据，请检查字段映射')
    for row in data['mps']:
        quantity = num(row.get('mps'), float('nan')) if isinstance(row.get('mps'), str) else row.get('mps')
        require(number(quantity) and quantity >= 0 and quantity == int(quantity),
                'MPS 每行必须为非负整数 wafer 数：' + str(row.get('ppn')))
        row['mps'] = int(quantity)
    mps_rows = merge_mps(data['mps'])
    require(bool(mps_rows), 'MPS 表没有有效数据，请检查字段映射')
    data['mps'] = mps_rows
    lot_ids = [r[0] for r in data['lots']]
    require(all(isinstance(value, str) and value.strip() for value in lot_ids),
            'LotID 不能为空')
    lots = set(lot_ids)
    wafer_totals = {}
    product_ids = {m['ppn'] for m in mps_rows}
    platforms = {m['ppn']: set(split_platforms(m['platform'])) for m in mps_rows}
    for m in mps_rows:
        validate_platform(m['platform'], 'PPN ' + m['ppn'])
    flows = data.get('flows') or {}
    for lot in data['lots']:
        require(isinstance(lot, list) and len(lot) >= 9, 'Lot 明细需要完整的 9 个字段')
        product = flow_product(lot[0], lot[1], flows)
        require(product in product_ids, 'Lot 引用未知 PPN：' + lot[0])
        lot[1] = product
        require(number(lot[3]) and lot[3] == int(lot[3]) and 1 <= lot[3] <= 25,
                'Lot 的 wafer 片数必须为 1 至 25 的整数：' + lot[0])
        validate_platform(lot[2], 'Lot ' + lot[0])
        require(set(split_platforms(lot[2])) <= platforms[product],
                'Lot 的 Platform 超出该 PPN 允许的机台组：' + lot[0])
        for index, label in ((4, 'Release Time'), (6, 'Weekly Due'), (7, 'Monthly Due')):
            try:
                date(lot[index])
            except (ValueError, TypeError) as error:
                raise ValueError('Lot %s 的 %s 无效：%s' % (lot[0], label, lot[index])) from error
        wafer_totals[product] = wafer_totals.get(product, 0) + lot[3]
    for m in mps_rows:
        require(number(m['mps']) and m['mps'] == int(m['mps']) and m['mps'] >= 0,
                'MPS 必须为非负整数 wafer 数')
        require(wafer_totals.get(m['ppn'], 0) == m['mps'],
                '产品 Lot 的 wafer 合计必须等于 MPS：' + m['ppn'])
        validate_platform(m['platform'], 'PPN ' + m['ppn'])
        require(m['ppn'] in data['flows'], '产品缺少路线：' + m['ppn'])
        steps = [op[0] for op in data['flows'][m['ppn']]]
        require(steps in route_patterns(),
                '产品 STEP 顺序不属于已确认的七种路线：' + m['ppn'] + ' / ' + ','.join(steps))
        for op in data['flows'][m['ppn']]:
            require(len(op) == 3, '工序需包含 STEP、Recipe、Time：' + m['ppn'])
            if op[0].startswith('CP'):
                require(any(r['recipe'] == op[1] for r in data['recipes']), 'CP Recipe 缺少候选针卡：' + op[1])
                require(number(op[2]) and op[2] > 0, 'CP Time 必须为有限正数：' + m['ppn'])
            elif op[0] == 'BAKE':
                require(op[1] in ('210C24H', '250C24H'), 'BAKE Recipe 无效：' + m['ppn'])
            elif op[0] == 'QTIME1':
                require(str(op[1]) in ('7', '14', '28'), 'QTIME1 Recipe 无效：' + m['ppn'])
    for constraint in data.get('constraints') or []:
        require(constraint.get('eqp') in cp_ids, '禁加工引用未知 CP 机台：' + str(constraint.get('eqp')))
        require(constraint.get('recipe') in recipe_ids, '禁加工引用未知 Recipe：' + str(constraint.get('recipe')))
        require(not constraint.get('type') or clean_type(constraint['type']) == machines[constraint['eqp']]['type'],
                '禁加工 TYPE 与机台不一致：' + constraint['eqp'])
    weekly_keys, week_spans = set(), {}
    for weekly in data.get('weekly') or []:
        key = (base_ppn(weekly['ppn']), weekly['week'])
        require(key[0] in product_ids and key not in weekly_keys, '周目标 PPN 无效或 PPN/Week 重复')
        weekly_keys.add(key)
        weekly['ppn'] = key[0]
        require(number(weekly['target']) and weekly['target'] >= 0 and weekly['target'] == int(weekly['target']),
                '周目标必须为非负整数 wafer 数')
        span = (date(weekly['start']), date(weekly['end']) + timedelta(days=1))
        require(span[0] < span[1], '周目标日期范围无效')
        require(weekly['week'] not in week_spans or week_spans[weekly['week']] == span,
                '同一 Week 的日期范围必须一致：' + weekly['week'])
        week_spans[weekly['week']] = span
    spans = sorted(week_spans.values())
    require(all(spans[i][0] >= spans[i - 1][1] for i in range(1, len(spans))), '不同 Week 的日期范围不能重叠')
    data['wip'] = resolve_wip(data)
    occupied = {}
    for w in data['wip']:
        lid, eqp = w['lotId'], w.get('eqp', '')
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
        for a, b, other in occupied.get(resource, []):
            if start < b and end > a:
                require(eqp in bake_ids and (start, end) == (a, b), '在制资源时间冲突：' + resource)
        occupied.setdefault(resource, []).append((start, end, w['lotKey']))
    for resource, entries in occupied.items():
        if resource in chamber_ids:
            groups = {}
            for a, b, lid in entries:
                groups.setdefault((a, b), []).append(lid)
            for group in groups.values():
                require(len(group) <= 4, 'BAKE 在制组批超过 4 lot')
    for key in ('borrow', 'pm'):
        for p in data[key]:
            require(p['eqp'] in (cp_ids if key == 'borrow' else set(ids) | bake_ids), '计划引用未知或不适用机台：' + p['eqp'])
            require(number(p['hours']) and 0 < p['hours'] <= 744, '计划时长必须大于 0 且不超过 744 小时')
            if key == 'borrow':
                require(0.6 <= p['hours'] <= 24, '借机时数必须为 0.6 至 24 小时')
                date(p.get('start') or p['date'])
                if p.get('start') and p.get('date'):
                    require(date(p['start']).date() == date(p['date']).date(), '借机 start 必须在指定 DATE 当天')
            else:
                early, due, over = [date(p[k]).date() for k in ('early', 'due', 'over')]
                require(early <= due <= over, 'PM 日期必须满足 Early ≤ Due ≤ Over')
                if p.get('start'):
                    s = date(p['start'])
                    require(s.date() >= early and (s + timedelta(hours=p['hours']) - timedelta(microseconds=1)).date() <= over, 'PM 指定时间超出维护窗口')
    return data


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
    bake = data['bakeResources']
    bake_ids = set(bake.get('ids', ['BAKE-%02d' % n for n in range(1, bake['machines'] + 1)]))
    for index, p in ordered_plans(data['pm'], 'pm'):
        resources = [p['eqp']]
        if p['eqp'] in bake_ids:
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
    for index, b in ordered_plans(data['borrow'], 'borrow'):
        first = date(b.get('start') or b['date'])
        duration = timedelta(hours=b['hours'])
        latest = first if b.get('start') else date(b['date']) + timedelta(days=1) - timedelta(microseconds=1)
        start = gap(blocked.get(b['eqp'], []), first, duration, latest)
        if start is not None:
            blocked.setdefault(b['eqp'], []).append((start, start + duration))
        # Unavailable requests remain eligible for a production-preserving suggestion.
    return issues
