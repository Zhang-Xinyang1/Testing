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


def excel_date(v, date1904=False):
    try:
        f = float(v)
    except Exception:
        return v
    epoch = datetime(1904, 1, 1) if date1904 else datetime(1899, 12, 30 if f >= 60 else 31)
    d = epoch + timedelta(days=f)
    return d.strftime('%Y-%m-%d') if abs(f - int(f)) < 1e-9 else d.isoformat(sep=' ', timespec='microseconds').rstrip('0').rstrip('.')


def read_workbook(path):
    with zipfile.ZipFile(path) as z:
        shared = []
        if 'xl/sharedStrings.xml' in z.namelist():
            root = ET.fromstring(z.read('xl/sharedStrings.xml'))
            for si in root.findall('m:si', NS):
                shared.append(''.join(t.text or '' for t in si.iter('{%s}t' % NS['m'])))
        date_styles = set()
        if 'xl/styles.xml' in z.namelist():
            root = ET.fromstring(z.read('xl/styles.xml'))
            custom, declared = set(), set()
            for nf in root.iter('{%s}numFmt' % NS['m']):
                declared.add(int(nf.get('numFmtId')))
                code = nf.get('formatCode', '').lower()
                code = re.sub(r'"[^"]*"|\\.|\[[^]]*\]', '', code)
                if re.search(r'(?<!\[)[ymdhs]', code) and '#' not in code and '0.0' not in code:
                    custom.add(int(nf.get('numFmtId')))
            xfs = root.find('m:cellXfs', NS)
            if xfs is not None:
                for i, xf in enumerate(xfs.findall('m:xf', NS)):
                    fid = int(xf.get('numFmtId', '0'))
                    # Includes East Asian built-ins (e.g. WPS/Excel numFmtId 58).
                    builtin_date = (14 <= fid <= 22 or 27 <= fid <= 36
                                    or 45 <= fid <= 47 or 50 <= fid <= 58)
                    if fid in custom or (fid not in declared and builtin_date):
                        date_styles.add(i)
        wb = ET.fromstring(z.read('xl/workbook.xml'))
        props = wb.find('m:workbookPr', NS)
        date1904 = props is not None and props.get('date1904') in ('1', 'true')
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
                            val = excel_date(val, date1904)
                    else:
                        val = ''
                    vals[ci] = val
                    maxc = max(maxc, ci)
                row_number = int(row.get('r', len(rows) + 1))
                while len(rows) < row_number - 1:
                    rows.append([])
                rows.append([vals.get(i, '') for i in range(maxc + 1)])
            width = max([len(r) for r in rows] or [0])
            rows = [r + [''] * (width - len(r)) for r in rows]
            result.append((sh.get('name'), rows))
        return result


def clean_rows(rows):
    if not rows:
        return [], []
    header = [str(x).strip() for x in rows[0]]
    names = [name for name in header if name]
    if len(names) != len(set(names)):
        raise ValueError('Excel 表头有重复列名，请使用唯一名称：' + '、'.join(names))
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
        pass
    text = str(v or '').strip().replace(',', '').replace('，', '').replace('\xa0', ' ')
    match = re.fullmatch(r'([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)'
                         r'\s*(?:[A-Za-z\u4e00-\u9fff]+(?:/[A-Za-z\u4e00-\u9fff]+)?)?', text)
    if not match:
        return default
    try:
        return float(match.group(1))
    except Exception:
        return default


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, os.path.join(root, 'backend'))
    from server_core import build_payload, load_config
    cfg = load_config()
    if len(sys.argv) > 1:
        cfg['dataSource']['path'] = os.path.abspath(sys.argv[1])
    data = build_payload(config=cfg)
    data['meta']['serverMode'] = False
    out = os.path.join(root, 'frontend', 'assets', 'data.js')
    with open(out, 'w', encoding='utf-8') as f:
        f.write('/* Generated from confirmed business rules. */\nwindow.DEMO_DATA = ' + json.dumps(data, ensure_ascii=False, separators=(',', ':')) + ';\n')
    print('wrote', out, 'lots', len(data['lots']), 'products', len(data['mps']))


if __name__ == '__main__':
    main()
