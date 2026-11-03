#!/usr/bin/env python3
"""Build assets/data.js from the scheduling workbook, using Python stdlib only."""
import zipfile, re, json, os, sys
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta

NS = {'m': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main',
      'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'}
RNS = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'


def col_index(ref):
    m = re.match(r'([A-Z]+)', ref or '')
    if not m:
        return 0
    n = 0
    for ch in m.group(1):
        n = n * 26 + ord(ch) - 64
    return n - 1


def excel_date(v):
    try:
        f = float(v)
    except Exception:
        return v
    d = datetime(1899, 12, 30) + timedelta(days=f)
    return d.strftime('%Y-%m-%d') if abs(f - int(f)) < 1e-9 else d.strftime('%Y-%m-%d %H:%M')


def read_workbook(path):
    z = zipfile.ZipFile(path)
    shared = []
    if 'xl/sharedStrings.xml' in z.namelist():
        root = ET.fromstring(z.read('xl/sharedStrings.xml'))
        for si in root.findall('m:si', NS):
            shared.append(''.join(t.text or '' for t in si.iter('{%s}t' % NS['m'])))
    date_styles = set()
    if 'xl/styles.xml' in z.namelist():
        root = ET.fromstring(z.read('xl/styles.xml'))
        custom = set()
        for nf in root.iter('{%s}numFmt' % NS['m']):
            code = nf.get('formatCode', '').lower()
            if re.search(r'(?<!\[)[ymdhs]', code) and '#' not in code and '0.0' not in code:
                custom.add(int(nf.get('numFmtId')))
        xfs = root.find('m:cellXfs', NS)
        if xfs is not None:
            for i, xf in enumerate(xfs.findall('m:xf', NS)):
                fid = int(xf.get('numFmtId', '0'))
                if fid in custom or 14 <= fid <= 22 or 45 <= fid <= 47:
                    date_styles.add(i)
    wb = ET.fromstring(z.read('xl/workbook.xml'))
    rels = ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))
    relmap = {r.get('Id'): r.get('Target') for r in rels}
    result = []
    for sh in wb.find('m:sheets', NS).findall('m:sheet', NS):
        target = relmap[sh.get('{%s}id' % RNS)]
        path_in = ('xl/' + target.lstrip('/')) if not target.startswith('/') else target.lstrip('/')
        root = ET.fromstring(z.read(path_in))
        data = root.find('m:sheetData', NS)
        rows, maxc = [], 0
        for row in (data.findall('m:row', NS) if data is not None else []):
            vals = {}
            for c in row.findall('m:c', NS):
                ci = col_index(c.get('r', ''))
                t, s = c.get('t', 'n'), c.get('s')
                v, isel = c.find('m:v', NS), c.find('m:is', NS)
                if t == 's' and v is not None:
                    val = shared[int(v.text)]
                elif t == 'inlineStr' and isel is not None:
                    val = ''.join(x.text or '' for x in isel.iter('{%s}t' % NS['m']))
                elif v is not None:
                    val = v.text
                    if t == 'n' and s is not None and int(s) in date_styles:
                        val = excel_date(val)
                else:
                    val = ''
                vals[ci] = val
                maxc = max(maxc, ci)
            rows.append([vals.get(i, '') for i in range(maxc + 1)])
        width = max([len(r) for r in rows] or [0])
        rows = [r + [''] * (width - len(r)) for r in rows]
        result.append((sh.get('name'), rows))
    return result


def clean_rows(rows):
    if not rows:
        return [], []
    header = [str(x).strip() for x in rows[0]]
    out = []
    for r in rows[1:]:
        if not any(str(x).strip() for x in r):
            continue
        out.append({header[i]: (r[i] if i < len(r) else '') for i in range(len(header))})
    return header, out


def num(v, default=0):
    try:
        return float(v)
    except Exception:
        return default


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    xlsx = os.path.join(root, 'data', 'Testing调度算例_v1.xlsx')
    if not os.path.exists(xlsx):
        sys.exit('workbook not found: ' + xlsx)
    sheets = read_workbook(xlsx)
    sheet_rows = {name: clean_rows(rows)[1] for name, rows in sheets}
    sheet_headers = {name: clean_rows(rows)[0] for name, rows in sheets}
    sheet_summary = [{'name': name, 'rows': len(sheet_rows[name]), 'fields': sheet_headers[name]}
                     for name, _ in sheets]

    mps = [{'platform': r['Platform'], 'ppn': r['PPN'], 'mps': int(num(r['MPS']))}
           for r in sheet_rows['MPS'] if r.get('PPN')]
    eqp = [{'type': r['TYPE'], 'id': r['EQPID'], 'card': r['Current ProbeCard'],
            'recipe': r['Current Recipe'], 'status': r.get('status', '')}
           for r in sheet_rows['EQP']]
    flows = {}
    flow_order = {'BAKE': 90, 'QTIME1': 100}
    for r in sheet_rows['测试flow&工时']:
        p, step = r['PROD'], r['STEP']
        order = int(step[2:]) if step.startswith('CP') else flow_order.get(step, 99)
        flows.setdefault(p, []).append({'step': step, 'recipe': r['RECIPE'],
                                        'time': num(r['TIME']), 'order': order})
    for p in flows:
        flows[p].sort(key=lambda x: x['order'])
    flow_out = {p: [[x['step'], x['recipe'], x['time']] for x in ops] for p, ops in flows.items()}

    cards = [{'recipe': r['Recipe'], 'cards': [x for x in r['ProbeCard List'].split(';') if x]}
             for r in sheet_rows['针卡']]
    pm = [{'eqp': r['EQPID'], 'early': r['Early Due'], 'due': r['Due Date'],
           'over': r['Over Due'], 'hours': num(r['Estimated Hours'])}
          for r in sheet_rows['PM计划']]
    constraints = [{'type': r['TYPE'], 'eqp': r['EQPID'], 'recipe': r['Constraint Recipe']}
                   for r in sheet_rows['约束条件']]
    borrow = [{'date': r['DATE'], 'eqp': r['EQPID'], 'hours': num(r['借机时数'])}
              for r in sheet_rows['借机计划']]
    weekly = [{'platform': r['Platform'], 'ppn': r['PPN'], 'week': r['Week'],
               'start': r['Week Start'], 'end': r['Week End'], 'target': int(num(r['Target Lots']))}
              for r in sheet_rows['周目标']]
    _, lot_rows = clean_rows(sheets[[n for n, _ in sheets].index('Lot明细')][1])
    lots = [[r['LotID'], r['PROD'], r['Platform'], int(num(r['Wafer Qty'])), r['Release Time'],
             r['Target Week'], r['Weekly Due'], r['Monthly Due'], int(num(r['Priority']))]
            for r in lot_rows]
    params = sheet_rows['算例参数']
    platform_type = {r['对象']: r['值'] for r in params
                     if r['类别'] == '映射' and r['参数'] == 'Platform对应TYPE'}
    card_type = {r['对象']: r['值'] for r in params
                 if r['类别'] == '资源' and r['参数'] == '物理针卡对应TYPE'}
    for r in params:
        if r['类别'] != '借机产生时刻':
            continue
        m = re.search(r'第(\d+)行', str(r['对象']))
        if m:
            idx = int(m.group(1)) - 2
            if 0 <= idx < len(borrow):
                borrow[idx]['generatedAt'] = r['值']
    missing = sum(1 for x in borrow if not x.get('generatedAt'))
    if missing:
        print('WARN: borrow rows without generatedAt:', missing)
    rules = [{'category': r['类别'], 'parameter': r['参数'], 'object': r['对象'],
              'value': r['值'], 'unit': r.get('单位', ''), 'basis': r.get('依据', '')}
             for r in params
             if r['类别'] != '借机产生时刻'
             and not (r['类别'] == '资源' and r['参数'] == '物理针卡对应TYPE')]
    data = {
        'meta': {'month': '2026-09', 'start': '2026-09-01T00:00:00+08:00',
                 'days': 30, 'source': os.path.basename(xlsx),
                 'generatedAt': datetime.now().strftime('%Y-%m-%d %H:%M:%S')},
        'mps': mps, 'eqp': eqp, 'flows': flow_out, 'recipes': cards,
        'pm': pm, 'borrow': borrow, 'weekly': weekly, 'constraints': constraints,
        'lotFields': sheet_headers['Lot明细'], 'lots': lots,
        'platformType': platform_type, 'cardType': card_type,
        'rules': rules, 'sheetSummary': sheet_summary,
    }
    out = os.path.join(root, 'frontend', 'assets', 'data.js')
    text = ('/* Auto-generated from %s. Do not edit by hand. */\nwindow.DEMO_DATA = %s;\n'
            % (os.path.basename(xlsx), json.dumps(data, ensure_ascii=False, separators=(',', ':'))))
    with open(out, 'w', encoding='utf-8') as f:
        f.write(text)
    print('wrote', out, '%.1f KB' % (len(text.encode('utf-8')) / 1024))
    print('lots', len(lots), 'mps', len(mps), 'flows', len(flow_out), 'pm', len(pm),
          'borrow', len(borrow), 'weekly', len(weekly), 'rules', len(rules))
    print('sheets', [(s['name'], s['rows']) for s in sheet_summary])


if __name__ == '__main__':
    main()
def num(v, default=0):
    try:
        return float(v)
    except Exception:
        return default