#!/usr/bin/env python3
"""Regenerate the synthetic seven-sheet workbook with Python's standard library.

This replaces .artifacts/build_case.mjs. The generated workbook is a
deterministic verification case, not production data.
"""
import argparse
import json
import math
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'data/Testing调度算例_新版.xlsx'
MANIFEST = ROOT / '.artifacts/case_manifest.json'


def column(index):
    letters = ''
    index += 1
    while index:
        index, remainder = divmod(index - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def cell_xml(reference, value):
    if value is None or value == '':
        return ''
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return '<c r="%s"><v>%s</v></c>' % (reference, value)
    text = escape(str(value))
    return '<c r="%s" t="inlineStr"><is><t xml:space="preserve">%s</t></is></c>' % (
        reference, text)


def sheet_xml(rows):
    parts = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
             '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">',
             '<sheetData>']
    for row_index, row in enumerate(rows, 1):
        cells = ''.join(cell_xml('%s%d' % (column(column_index), row_index), value)
                        for column_index, value in enumerate(row))
        parts.append('<row r="%d">%s</row>' % (row_index, cells))
    parts.extend(['</sheetData>', '</worksheet>'])
    return ''.join(parts)


def write_workbook(path, sheets):
    content_types = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">',
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>',
        '<Default Extension="xml" ContentType="application/xml"/>',
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>',
    ]
    for index in range(1, len(sheets) + 1):
        content_types.append(
            '<Override PartName="/xl/worksheets/sheet%d.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' % index)
    content_types.append('</Types>')

    workbook_sheets = ''.join(
        '<sheet name=%s sheetId="%d" r:id="rId%d"/>' %
        (quoteattr(name), index, index)
        for index, (name, _rows) in enumerate(sheets, 1))
    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets>%s</sheets></workbook>' % workbook_sheets)

    relationships = ''.join(
        '<Relationship Id="rId%d" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        'Target="worksheets/sheet%d.xml"/>' % (index, index)
        for index in range(1, len(sheets) + 1))
    workbook_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '%s</Relationships>' % relationships)
    package_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="xl/workbook.xml"/></Relationships>')

    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('[Content_Types].xml', ''.join(content_types))
        archive.writestr('_rels/.rels', package_rels)
        archive.writestr('xl/workbook.xml', workbook)
        archive.writestr('xl/_rels/workbook.xml.rels', workbook_rels)
        for index, (_name, rows) in enumerate(sheets, 1):
            archive.writestr('xl/worksheets/sheet%d.xml' % index, sheet_xml(rows))
def build_rows():
    seed = 20261106

    def random():
        nonlocal seed
        seed = (1664525 * seed + 1013904223) & 0xFFFFFFFF
        return seed / 4294967296

    types = ['CP-A', 'CP-B', 'CP-C']

    def recipe(index):
        return 'CP-R%02d' % (index + 1)

    def card(index):
        return 'CARD-T%02d' % (index + 1)

    quantities = [2] * 72
    quantities[0] = 800
    remaining = 11000 - sum(quantities)
    while remaining:
        index = 2 + math.floor(random() * 70)
        if quantities[index] < 800:
            amount = min(remaining, 800 - quantities[index],
                         1 + math.floor(random() * 80))
            quantities[index] += amount
            remaining -= amount

    patterns = json.loads((ROOT / 'config/route_patterns.json').read_text(encoding='utf-8'))
    mps, flows = [], []
    for index in range(72):
        product = 'PPN%02d' % (index + 1)
        first, second = types[index % 3], types[(index + 1) % 3]
        mps.append([first if index % 3 == 0 else first + ';' + second,
                    product + '-1', quantities[index], 'wafer'])
        bake_index = 0
        for step in patterns[index % len(patterns)]:
            if step == 'BAKE':
                recipe_name = ['210C24H', '250C24H'][(index + bake_index) % 2]
                bake_index += 1
                flows.append([product, step, recipe_name, ''])
            elif step == 'QTIME1':
                flows.append([product, step, [7, 14, 28][index % 3], ''])
            else:
                flows.append([product, step,
                              recipe((index * 3 + int(step[2:]) - 1) % 31),
                              8 + math.floor(random() * 33)])

    cards = []
    for index in range(31):
        values = [card(index * 2), card(index * 2 + 1)]
        if index % 4 == 0:
            values.extend([card((index * 2 + 2) % 62), card((index * 2 + 3) % 62)])
        cards.append([recipe(index), ';'.join(values)])

    eqp = [[types[math.floor(index / 9)], 'CP-%02d' % (index + 1),
            card((index * 2) % 62), recipe(index % 31), 'RUN'] for index in range(27)]
    for index in range(1, 8):
        eqp.append(['OVEN', 'OVEN-%02d' % index, '', '', 'RUN'])

    borrow = [['2026-09-%02d' % (1 + math.floor(index * 30 / 119)),
               eqp[(index * 7) % 27][1],
               math.floor((.6 + random() * 23.4) * 10 + .5) / 10]
              for index in range(119)]
    pm = [[eqp[(index * 3) % 27][1],
           '2026-09-%02d' % (1 + index * 2),
           '2026-09-%02d' % (3 + index * 2),
           '2026-09-%02d' % (5 + index * 2), 4 + index % 3] for index in range(9)]
    pm.extend([['OVEN-01', '2026-09-08', '2026-09-12', '2026-09-17', 6],
               ['OVEN-02', '2026-09-15', '2026-09-19', '2026-09-24', 6]])

    sheets = [
        ('MPS', [['Platform', 'PPN', 'MPS', '单位']] + mps),
        ('EQP', [['TYPE', 'EQPID', 'Current ProbeCard', 'Current Recipe', 'Status']] + eqp),
        ('针卡', [['Recipe', 'ProbeCard List']] + cards),
        ('测试flow&工时', [['PROD', 'STEP', 'RECIPE', 'Time']] + flows),
        ('借机计划', [['DATE', 'EQPID', '借机时数']] + borrow),
        ('PM计划', [['EQP ID', 'Early Due', 'Due Date', 'Over Due', 'Estimated Hours']] + pm),
        ('约束条件', [['TYPE', 'EQPID', 'Constraint Recipe'],
                     ['CP-A', 'CP-01', recipe(0)],
                     ['CP-B', 'CP-10', recipe(3)]]),
    ]
    manifest = {
        'synthetic': True, 'seed': 20261106, 'patterns': patterns,
        'routePatternCount': len(patterns),
        'products': len(mps),
        'sheets': [{'name': name, 'header': rows[0], 'rows': len(rows) - 1}
                   for name, rows in sheets],
        'mps': sum(row[2] for row in mps), 'unit': 'wafer',
        'lotSizing': 'default-25-tail',
        'assumptions': '27 CP / 9 per group; seven confirmed route patterns assigned '
                       'cyclically across 72 synthetic products; MPS PPN uses a -1 vendor '
                       'suffix while flow PROD uses the prefix; synthetic CP Time is minutes '
                       'per wafer, other times and plans are placeholders',
    }
    return sheets, manifest


def main():
    parser = argparse.ArgumentParser(description='生成合成验证 XLSX')
    parser.add_argument('--output', default=str(OUTPUT))
    parser.add_argument('--manifest', default=str(MANIFEST))
    args = parser.parse_args()
    output = Path(args.output)
    manifest_path = Path(args.manifest)
    sheets, manifest = build_rows()
    write_workbook(output, sheets)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                             encoding='utf-8')
    print('wrote', output)
    print('mps', len(sheets[0][1]) - 1, 'wafers', manifest['mps'],
          'flows', len(sheets[3][1]) - 1, 'borrow', len(sheets[4][1]) - 1)


if __name__ == '__main__':
    main()
