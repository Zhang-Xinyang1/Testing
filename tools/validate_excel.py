#!/usr/bin/env python3
"""Read-only, accumulating Excel audit for the Testing_11101136 baseline.

No server, scheduling run, configuration write, Excel write or third-party package
is required. Invalid cells/rows do not terminate independent checks.
"""
import argparse
import copy
import csv
import hashlib
import json
import math
from pathlib import Path
import posixpath
import re
import sys
import tempfile
from collections import Counter, defaultdict
from datetime import datetime, timedelta
import xml.etree.ElementTree as ET
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
sys.path.insert(0, str(ROOT / 'tools'))
from build_data import NS, RNS, col_index, column_name, excel_date, num
from input_rules import CST, identifier, base_ppn, flow_product, parse_datetime, resolve_pm_target, pm_events
from platforms import clean_type, split_platforms
from server_core import build_payload, route_patterns, norm_key

VERSION = '11101136-pm-audit-1'
LABELS = dict(mps='MPS', eqp='EQP', cards='针卡', flow='工艺路线', pm='PM计划',
              borrow='借机计划', constraints='禁加工', lots='Lot明细', wip='在制', weekly='周目标', params='参数')
REQUIRED = {'mps': ('platform', 'ppn', 'mps'), 'eqp': ('type', 'id'),
            'cards': ('recipe', 'cards'), 'flow': ('prod', 'step', 'recipe', 'time'),
            'pm': ('eqp', 'early', 'due', 'over', 'hours'), 'borrow': ('date', 'eqp', 'hours'),
            'lots': ('lotId', 'wafer'), 'wip': ('lotId',)}
FIELDS = {
    'mps': 'platform ppn mps', 'eqp': 'id type proberId currentCard currentRecipe status',
    'cards': 'recipe cards', 'flow': 'prod step recipe time',
    'pm': 'eqp targetKind early due over hours start', 'borrow': 'eqp date hours start generatedAt',
    'constraints': 'eqp type recipe', 'lots': 'lotId ppn platform wafer release week weeklyDue monthlyDue priority',
    'wip': 'lotId lotKey eqp chamber card start end', 'weekly': 'ppn week platform start end target',
    'params': 'category parameter object value unit basis',
}
FALLBACK = {'eqp': {'proberId': 'proberID'}, 'pm': {'targetKind': 'TargetKind', 'start': 'Start'}}


def blank(value):
    return value is None or not str(value).strip()


def norm(value):
    return norm_key(value)


def display(value):
    return '' if value is None else str(value)


class Audit:
    def __init__(self, config, config_path='', excel_only=False):
        self.config = copy.deepcopy(config)
        self.config_path = str(config_path)
        self.issues, self.tables, self.columns, self.ready = [], {}, {}, {}
        self.sheet_stats, self.effective, self.cell_issues = [], {}, []
        self.incomplete, self.engine_passed = False, False
        self.configured = set()
        self.source_path = ''
        self.source_hash = ''
        self.excel_only = excel_only

    def add(self, severity, code, message, fix='', row=None, field='', value=None, **location):
        item = dict(severity=severity, code=code, table='', sheet='', row='', column='',
                    record='', source='Excel', field=field, value=display(value), message=message, fix=fix)
        if isinstance(row, dict):
            item.update({key: row.get('_' + key, '') for key in ('table', 'sheet', 'row', 'record', 'source')})
            item['column'] = row.get('_columns', {}).get(field, '')
            if value is None:
                item['value'] = display(row.get(field))
            # Bad values in unused overrides are not import blockers; mapping
            # failures remain blockers because the loader parses before overriding.
            if severity == 'ERROR' and row.get('_inactive'):
                item['severity'] = 'WARNING'
                item['message'] += '（该原表目前被手工配置覆盖）'
            elif severity == 'ERROR' and row.get('_ignored'):
                item['severity'] = 'WARNING'
                item['message'] += '（程序会忽略此行，需确认是否遗漏业务数据）'
        elif row is not None:
            item['row'] = row
        item.update(location)
        self.issues.append(item)

    def check(self, condition, code, message, row=None, field='', fix='', **kwargs):
        if not condition:
            self.add('ERROR', code, message, fix, row, field, **kwargs)
        return bool(condition)

    def skip(self, table, message):
        if not any(i['code'] == 'DEPENDENCY' and i['table'] == table and i['message'] == message for i in self.issues):
            self.add('SKIPPED', 'DEPENDENCY', message, '修复对应前置错误后重新运行校验。', table=table)

    def number(self, row, field, low, high, integer=False, optional=False):
        if field not in row:
            return None
        if optional and blank(row[field]):
            return None
        value = num(row[field], float('nan'))
        valid = not isinstance(row[field], bool) and math.isfinite(value) and low <= value <= high
        valid = valid and (not integer or value == int(value))
        if not self.check(valid, 'NUMBER', '数值须为 %s 至 %s 的%s有限数值' % (low, high, '整数' if integer else ''),
                          row, field, '去掉错误值，按该字段单位填写合法数字。'):
            return None
        return int(value) if integer else value

    def date(self, row, field, required=True):
        if field not in row:
            return None
        if blank(row[field]) and not required:
            return None
        try:
            value = parse_datetime(row[field])
            row.setdefault('_dates', {})[field] = value
            return value
        except (ValueError, TypeError, OverflowError):
            self.add('ERROR', 'DATE', '日期/时间为空或无效',
                     '填写真实 Excel 日期单元格或 YYYY-MM-DD HH:MM:SS；纯数字序号须设置日期格式。', row, field)
            return None

    def config_check(self):
        if not isinstance(self.config, dict):
            self.add('ERROR', 'CONFIG', '配置根节点必须为 JSON 对象', source='配置')
            self.config = {}
        for key in ('dataSource', 'fieldMap', 'scheduling'):
            if not isinstance(self.config.get(key, {}), dict):
                self.add('ERROR', 'CONFIG', key + ' 必须为对象', source='配置', field=key)
                self.config[key] = {}
            self.config.setdefault(key, {})
        self.src, self.maps, self.scheduling = (self.config[k] for k in ('dataSource', 'fieldMap', 'scheduling'))
        if self.excel_only:
            for key in ('eqp', 'pm', 'borrow', 'wip'):
                self.scheduling.pop(key, None)
        if self.src.get('type', 'excel') != 'excel':
            self.add('ERROR', 'SOURCE_TYPE', '当前配置不是 Excel 数据源', '此脚本仅检查 Excel，核对 dataSource.type。', source='配置')
        self.sheet_map = self.src.get('sheetMap', {})
        if not isinstance(self.sheet_map, dict):
            self.add('ERROR', 'CONFIG', 'sheetMap 必须为对象', source='配置')
            self.sheet_map = {}
        configured_header = self.src.get('headerRow', 1)
        try:
            self.header = max(1, int(configured_header))
        except (ValueError, TypeError, OverflowError):
            self.add('ERROR', 'HEADER_ROW', '表头行无法转换为整数', source='配置', field='headerRow', value=configured_header)
            self.header = 1
        if str(configured_header) != str(self.header):
            self.add('WARNING', 'HEADER_CONVERSION', '按程序取整并将表头行修正为 ' + str(self.header), source='配置', value=configured_header)
        self.month_start = None
        month = self.src.get('month') or '2026-09'
        try:
            year, month_number = [int(part) for part in month.split('-')[:2]]
            self.month_start = datetime(year, month_number, 1, tzinfo=CST)
            self.month_end = (self.month_start.replace(day=28) + timedelta(days=4)).replace(day=1)
        except (ValueError, TypeError, AttributeError, OverflowError):
            self.add('ERROR', 'MONTH', '计划月份无效', '填写 YYYY-MM，例如 2026-11。', source='配置', field='month', value=month)
        self.overrides = {}
        for role in ('eqp', 'pm', 'borrow', 'wip'):
            if role not in self.scheduling:
                continue
            values = self.scheduling[role]
            if not isinstance(values, list):
                self.add('ERROR', 'OVERRIDE_FORMAT', '手工覆盖必须为记录列表', source='手工配置', table=role)
                values = []
            rows = []
            for index, item in enumerate(values, 1):
                if not isinstance(item, dict):
                    self.add('ERROR', 'OVERRIDE_FORMAT', '记录必须为对象', source='手工配置', table=role, record=index)
                    continue
                row = {k: item.get(k, '') for k in FIELDS[role].split()}
                row.update(item)
                if role == 'eqp':
                    row['currentCard'], row['currentRecipe'] = item.get('card', ''), item.get('recipe', '')
                row.update(_table=role, _source='手工配置', _record=index, _columns={})
                rows.append(row)
            self.overrides[role] = rows
            self.add('WARNING', 'OVERRIDE_ACTIVE', '本表采用手工配置，覆盖 Excel 原表（包括空列表）',
                     '核对服务器实际 config.json；需要恢复原表时在页面恢复参考配置，或使用 --excel-only 仅作对照。', table=role, source='手工配置')

    def structure(self, workbook):
        used = set()
        for role in LABELS:
            self.tables[role], self.columns[role], self.ready[role] = [], {}, set()
            name = self.sheet_map.get(role)
            if not name:
                if role in ('mps', 'eqp', 'cards', 'flow'):
                    self.add('ERROR', 'SHEET_UNMAPPED', '必需子表未配置', '填写 dataSource.sheetMap 和该表字段映射。', table=role, source='配置')
                continue
            self.configured.add(role)
            if not isinstance(name, str):
                self.add('ERROR', 'SHEET_NAME', 'Sheet 名称必须为文字', table=role, source='配置')
                continue
            matches = [(n, rows) for n, rows in workbook if norm(n) == norm(name)]
            if len(matches) != 1:
                self.add('ERROR', 'SHEET_MATCH', '已配置子表不存在或名称归一化后不唯一：' + name,
                         '核对 Sheet 名称；忽略大小写及空白等格式后仍须唯一。', table=role, sheet=name)
                self.skip(role, '子表无法唯一读取，无法检查其记录。')
                continue
            sheet, raw = matches[0]; used.add(sheet)
            header = [display(v).strip() for v in (raw[self.header - 1] if len(raw) >= self.header else [])]
            nonempty = [(n, row) for n, row in enumerate(raw[self.header:], self.header + 1) if any(not blank(v) for v in row)]
            self.sheet_stats.append(dict(table=role, sheet=sheet, records=len(nonempty), headerRow=self.header,
                                         source='手工配置' if role in self.overrides else 'Excel'))
            if not any(header) and not nonempty and role in ('pm', 'borrow', 'constraints', 'wip', 'weekly', 'params'):
                continue
            maps = [(n, m) for n, m in self.maps.items() if norm(n) == norm(name)]
            if len(maps) > 1:
                self.add('WARNING', 'MAPPING_AMBIGUOUS', '同一子表存在多份等价字段映射，程序优先精确同名项，否则取首项', '建议合并为一份映射。', table=role, sheet=sheet, source='配置')
            mapping = self.maps.get(name, maps[0][1] if maps else {})
            if not isinstance(mapping, dict):
                self.add('ERROR', 'MAPPING_FORMAT', '字段映射必须为对象', table=role, sheet=sheet, source='配置')
                mapping = {}
            required = set(REQUIRED.get(role, ()))
            if nonempty and role == 'weekly':
                required |= {'ppn', 'start', 'end'}
            if nonempty and role == 'constraints':
                required |= {'eqp', 'recipe'}
            positions = {}
            for field in FIELDS[role].split():
                if role == 'borrow' and field in ('start', 'generatedAt'):
                    if mapping.get(field): self.add('WARNING', 'IGNORED_MAPPING', '本版不从借机 Excel 表读取此字段；只有手工配置 start 生效', table=role, sheet=sheet, field=field)
                    continue
                wanted = mapping.get(field, FALLBACK.get(role, {}).get(field, ''))
                if role == 'eqp' and field == 'status' and not wanted:
                    wanted = next((h for h in header if h in ('Status', 'status')), '')
                candidates = wanted if isinstance(wanted, (list, tuple)) else [wanted]
                for candidate in candidates:
                    if not candidate:
                        continue
                    indices = [i for i, h in enumerate(header) if norm(h) == norm(candidate)]
                    if len(indices) > 1:
                        self.add('ERROR', 'HEADER_AMBIGUOUS', '实际使用的字段匹配到重复表头',
                                 '仅重命名参与读取的冲突列，并同步字段映射。未使用的重复列不用删除。',
                                 table=role, sheet=sheet, row=self.header, field=field, value=candidate,
                                 column=','.join(column_name(i) for i in indices))
                        break
                    if indices:
                        positions[field] = indices[0]
                        break
                else:
                    if field in required:
                        self.add('ERROR', 'COLUMN_MISSING', '必需字段未映射或实际列不存在',
                                 '核对真实表头及 fieldMap，不要仅按页面默认列名判断。',
                                 table=role, sheet=sheet, row=self.header, field=field, value=wanted)
                    elif wanted:
                        self.add('WARNING', 'OPTIONAL_COLUMN', '可选列不存在，本版会使用空值或默认值',
                                 '需要此功能时补列并核对映射。', table=role, sheet=sheet, row=self.header, field=field, value=wanted)
            self.columns[role] = {f: column_name(i) for f, i in positions.items()}
            self.ready[role] = set(positions)
            for key, count in Counter(norm(h) for h in header if h).items():
                indices = [i for i, h in enumerate(header) if norm(h) == key]
                if count > 1 and not set(indices) & set(positions.values()) and not any(
                        x['code'] == 'HEADER_AMBIGUOUS' and x['sheet'] == sheet and x['column'] == ','.join(column_name(i) for i in indices) for x in self.issues):
                    self.add('INFO', 'UNUSED_DUPLICATE', '未使用的重复表头不阻断导入', table=role, sheet=sheet,
                             row=self.header, column=','.join(column_name(i) for i in indices), value=header[indices[0]])
            for record, (number, raw_row) in enumerate(nonempty, 1):
                row = {f: raw_row[i] if i < len(raw_row) else '' for f, i in positions.items()}
                row.update(_table=role, _sheet=sheet, _row=number, _record=record,
                           _source='Excel', _columns=self.columns[role], _inactive=role in self.overrides)
                row['_ignored'] = (role == 'mps' and 'ppn' in row and blank(row['ppn'])) or (
                    role == 'flow' and any(field in row and blank(row[field]) for field in ('prod', 'step')))
                if role == 'cards':
                    separator = mapping.get('separator', ';')
                    row['_separator'] = (separator[0] if separator else ';') if isinstance(separator, list) else separator or ';'
                self.tables[role].append(row)
            if required - set(positions):
                self.skip(role, '缺失或歧义字段的依赖检查暂不执行；其余字段及其他表继续检查。')
            if not nonempty and role in ('mps', 'eqp', 'cards', 'flow'):
                self.add('WARNING' if role in self.overrides else 'ERROR', 'EMPTY_TABLE', '已配置表没有有效数据记录', '核对是否有有效手工覆盖；否则补齐数据。', table=role, sheet=sheet)
        for sheet, _ in workbook:
            if sheet not in used:
                self.add('INFO', 'UNUSED_SHEET', '本表未映射，不参与排产', sheet=sheet)
        for cell in self.cell_issues:
            roles = [role for role in LABELS if any(s['table'] == role and s['sheet'] == cell['sheet'] for s in self.sheet_stats)]
            fields = [(role, field) for role in roles for field, col in self.columns[role].items() if col == cell['column']]
            # Formula/cache warnings are not automatically blockers: optional
            # fields may legitimately use loader defaults. Required value checks
            # below decide whether the blank/error result actually prevents import.
            # A cell decoding failure also breaks the strict workbook reader.
            severity = 'ERROR' if cell['code'] == 'CELL_READ' else 'WARNING'
            self.add(severity, cell['code'], cell['message'], '用 Excel/WPS 重新计算并保存；公式结果不能为错误值。',
                     **{k: cell[k] for k in ('sheet', 'row', 'column', 'value')},
                     table=fields[0][0] if fields else '', field=','.join(f for _, f in fields))
        self.effective = {role: self.overrides.get(role, [r for r in self.tables[role] if not r.get('_ignored')]) for role in LABELS}

    def values(self):
        for role in LABELS:
            for row in self.tables[role] + self.overrides.get(role, []):
                self.guard('记录检查 ' + role, lambda row=row, role=role: self.row_values(role, row), row)

    def row_values(self, role, row):
        for field in FIELDS[role].split():
            if field in row and field not in ('lotId', 'type', 'platform'):
                row[field] = display(row[field]).strip()
        for field in {'mps': ('ppn', 'platform'), 'eqp': ('id', 'type'), 'cards': ('recipe', 'cards'),
                      'flow': ('prod', 'step', 'recipe'), 'pm': ('eqp',), 'borrow': ('eqp',),
                      'lots': ('lotId',), 'wip': ('lotId',), 'weekly': ('ppn',),
                      'constraints': ('eqp', 'recipe')}.get(role, ()):
            if field in row:
                self.check(not blank(row[field]), 'EMPTY_VALUE', '必填单元格为空', row, field, '补齐该行字段；仅有备注的行请移到数据区域外。')
        if role == 'mps': row['_quantity'] = self.number(row, 'mps', 0, float('inf'), True)
        if role == 'lots':
            row['_quantity'] = self.number(row, 'wafer', 1, 25, True)
            for field in ('release', 'weeklyDue', 'monthlyDue'):
                self.date(row, field, False)
            if 'priority' in row:
                value = num(row['priority'], 1)
                if not math.isfinite(value): self.add('ERROR', 'PRIORITY', 'Priority 无法转换为有限整数', row=row, field='priority')
                elif blank(row['priority']) or not math.isfinite(num(row['priority'], float('nan'))) or value != int(value):
                    self.add('WARNING', 'PRIORITY_DEFAULT', '本版会默认或截断 Priority 为 ' + str(int(value)), row=row, field='priority')
        if role == 'eqp':
            if 'type' in row:
                self.check(len(split_platforms(row['type'])) == 1, 'TYPE_MULTIPLE', '一台设备只能填写一个 TYPE；多台设备同 TYPE 合法', row, 'type')
            if clean_type(row.get('status')) not in ('RUN', 'LOST'):
                self.add('WARNING', 'STATUS', '本版只有 RUN 可参与新排产；此状态不会获得正常排产资格',
                         '核对实际设备状态；不要为通过检查而把停机设备改为 RUN。', row, 'status')
        if role == 'flow':
            step, recipe = identifier(row.get('step')), identifier(row.get('recipe'))
            if 'step' in row:
                self.check(step in ('CP1', 'CP2', 'CP3', 'CP4', 'BAKE', 'QTIME1'), 'STEP', '未知工序名称', row, 'step')
            if step.startswith('CP'):
                row['_duration'] = self.number(row, 'time', 0, float('inf'))
                if row['_duration'] == 0: self.add('ERROR', 'CP_TIME', 'CP 工时必须大于 0（分钟/wafer）', row=row, field='time')
            elif step == 'BAKE' and 'recipe' in row:
                self.check(recipe in ('210C24H', '250C24H'), 'BAKE_RECIPE', 'BAKE 仅支持 210C24H 或 250C24H', row, 'recipe')
            elif step == 'QTIME1' and 'recipe' in row:
                self.check(recipe in ('7', '14', '28'), 'QTIME_RECIPE', '本版 QTIME1 Recipe 只能为 7、14、28', row, 'recipe')
        if role in ('pm', 'borrow'):
            row['_hours'] = self.number(row, 'hours', 0.6 if role == 'borrow' else 0, 24 if role == 'borrow' else 744)
            if row['_hours'] == 0: self.add('ERROR', 'PM_HOURS', 'PM 时长必须大于 0', row=row, field='hours')
            for field in ('early', 'due', 'over') if role == 'pm' else ('date',): self.date(row, field)
            self.date(row, 'start', False)
            dates = row.get('_dates', {})
            if role == 'pm':
                kind = identifier(row.get('targetKind'))
                self.check(kind in ('', 'EQP', 'PROBER'), 'PM_KIND', 'TargetKind 只能为 EQP、PROBER 或空', row, 'targetKind')
                if all(k in dates for k in ('early', 'due', 'over')):
                    self.check(dates['early'].date() <= dates['due'].date() <= dates['over'].date(), 'PM_ORDER', 'PM 日期须满足 Early ≤ Due ≤ Over', row, 'due')
                    duration = row['_hours']
                    if duration is not None and duration > 0:
                        limit = dates['over'].replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
                        self.check(dates['early'] + timedelta(hours=duration) <= limit, 'PM_WINDOW', '维护窗口容不下要求的连续时长', row, 'hours')
                        if 'start' in dates:
                            self.check(dates['start'] >= dates['early'] and dates['start'] + timedelta(hours=duration) <= limit,
                                       'PM_FIXED_WINDOW', '指定维护开始或结束超出维护窗口', row, 'start')
            elif 'start' in dates and 'date' in dates:
                self.check(dates['start'].date() == dates['date'].date(), 'BORROW_DATE', '借机开始必须在 DATE 当天', row, 'start')
        if role == 'weekly':
            value = num(row.get('target'), 0)
            row['_quantity'] = int(value) if math.isfinite(value) else None
            if row['_quantity'] is None or row['_quantity'] < 0:
                self.add('ERROR', 'WEEK_TARGET', '周目标读取并取整后必须为非负整数', row=row, field='target')
            elif not math.isfinite(num(row.get('target'), float('nan'))) or value != int(value):
                self.add('WARNING', 'WEEK_TARGET_CONVERSION', '本版将周目标默认或截断为 ' + str(row['_quantity']), '这是旧版默认行为；请核对是否符合真实目标。', row, 'target')
            first, last = self.date(row, 'start'), self.date(row, 'end')
            if first and last: self.check(first < last + timedelta(days=1), 'WEEK_RANGE', '周目标结束不能早于开始', row, 'end')
        if role == 'wip':
            if not blank(row.get('eqp')):
                for field in ('start', 'end'):
                    if field not in row:
                        self.add('ERROR', 'WIP_DATE_MAPPING', '在制有资源占用，必须映射开始和结束时间', row=row, field=field)
            first, last = self.date(row, 'start', not blank(row.get('eqp'))), self.date(row, 'end', not blank(row.get('eqp')))
            if first and last: self.check(first < last, 'WIP_TIME', '在制结束必须晚于开始', row, 'end')
        if role == 'cards':
            row['_cards'] = list(dict.fromkeys(identifier(p) for p in re.split(r'[;,，；、\r\n]+|' + re.escape(display(row.get('_separator') or ';')), display(row.get('cards'))) if identifier(p)))
            if 'cards' in row:
                self.check(bool(row['_cards']), 'CARD_LIST', 'Recipe 至少需要一种非空候选针卡', row, 'cards')

    def has(self, role, fields):
        if role in self.overrides or set(fields) <= self.ready.get(role, set()): return True
        self.skip(role, '依赖字段不可用：' + ', '.join(sorted(set(fields) - self.ready.get(role, set()))))
        return False

    def unique(self, role, field):
        groups = defaultdict(list)
        for row in self.effective[role]:
            value = identifier(row.get(field))
            if value: groups[value].append(row)
        for value, rows in groups.items():
            if len(rows) > 1:
                for row in rows:
                    self.add('ERROR', 'DUPLICATE_KEY', '标识重复（忽略大小写）：' + value, '该字段必须唯一；TYPE 与 LotID 不受此唯一限制。', row, field)
        return {k: rows[0] for k, rows in groups.items()}

    def references(self):
        self.eqps = self.unique('eqp', 'id')
        self.recipes = self.unique('cards', 'recipe')
        self.cp = {k: e for k, e in self.eqps.items() if 'OVEN' not in clean_type(e.get('type'))}
        self.types = {clean_type(e.get('type')) for e in self.cp.values() if e.get('type')}
        self.equipment = [dict(id=k, type=clean_type(e.get('type')), proberId=identifier(e.get('proberId')),
                               status=clean_type(e.get('status')), card=identifier(e.get('currentCard'))) for k, e in self.eqps.items()]
        bake = self.scheduling.get('bakeResources', {'machines': 7, 'chambers': 4})
        if not isinstance(bake, dict):
            self.add('ERROR', 'BAKE_CONFIG', 'BAKE 容量配置必须为对象', source='配置'); bake = {}
        ovens = [k for k, e in self.eqps.items() if 'OVEN' in clean_type(e.get('type'))]
        machines, chambers = (len(ovens), 4) if ovens else (bake.get('machines'), bake.get('chambers'))
        if ovens and bake.get('chambers', 4) != 4:
            self.add('WARNING', 'OVEN_CAPACITY_FIXED', '此版本 EQP 有 OVEN 时固定使用每台 4 腔，配置中的其他腔体数不生效', source='配置', table='eqp')
        for name, value in (('machines', machines), ('chambers', chambers)):
            self.check(type(value) is int and 1 <= value <= 100, 'BAKE_CAPACITY', 'BAKE 台数/腔体须为 1 至 100 的整数',
                       field=name, value=value, source='配置', table='eqp')
        self.bake = dict(machines=machines if type(machines) is int and 1 <= machines <= 100 else 0,
                         chambers=chambers if type(chambers) is int and 1 <= chambers <= 100 else 0)
        ids = ovens or bake.get('ids', ['BAKE-%02d' % n for n in range(1, self.bake['machines'] + 1)])
        valid_ids = isinstance(ids, list) and len(ids) == self.bake['machines'] and all(isinstance(k, str) and identifier(k) for k in ids)
        valid_ids = valid_ids and len({identifier(k) for k in ids}) == len(ids) and not (set(map(identifier, ids)) & set(self.cp))
        if not self.check(valid_ids, 'BAKE_IDS', 'BAKE 编号须非空、唯一、数量正确，且不能与 CP 编号重复', source='配置', table='eqp'):
            ids = []
        self.bake['ids'] = [identifier(k) for k in ids]
        self.products, self.routes = {}, defaultdict(list)
        for row in self.effective['flow']:
            if identifier(row.get('prod')): self.routes[identifier(row['prod'])].append(row)
        for row in self.effective['mps']:
            key = base_ppn(row.get('ppn'))
            if not key: continue
            group = self.products.setdefault(key, dict(quantity=0, valid=True, platforms=set(), row=row))
            if row.get('_quantity') is None: group['valid'] = False
            else: group['quantity'] += row['_quantity']
            group['platforms'].update(split_platforms(row.get('platform')))
            if 'platform' in row: self.platform(row, row['platform'])
        if self.has('flow', ('prod', 'step')):
            patterns = route_patterns()
            for product, item in self.products.items():
                route = self.routes.get(product)
                if not route:
                    self.add('ERROR', 'FLOW_MISSING', '产品缺少路线：' + product, '核对 flow.PROD 与 MPS 的产品前缀。', item['row'], 'ppn')
                elif [identifier(r.get('step')) for r in route] not in patterns:
                    self.add('ERROR', 'ROUTE_ORDER', '工序顺序不属于此版本确认的七种路线：' + product,
                             '按原表实际顺序填写，不能按工序名称排序。允许路线见 config/route_patterns.json。', route[0], 'step')
        for product, route in self.routes.items():
            for row in route:
                if identifier(row.get('step')).startswith('CP') and 'recipe' in row and self.has('cards', ('recipe', 'cards')):
                    if identifier(row['recipe']) not in self.recipes:
                        self.add('ERROR' if product in self.products else 'WARNING', 'RECIPE_REFERENCE', 'CP Recipe 缺少针卡定义' + ('（非 MPS 产品的路线不会排产）' if product not in self.products else ''), row=row, field='recipe')
        forbidden = set()
        for row in self.effective['constraints']:
            eqp, recipe = identifier(row.get('eqp')), identifier(row.get('recipe'))
            if self.has('eqp', ('id', 'type')):
                self.check(eqp in self.cp, 'CONSTRAINT_EQP', '禁加工引用未知或非 CP 机台', row, 'eqp')
            if self.has('cards', ('recipe',)): self.check(recipe in self.recipes, 'CONSTRAINT_RECIPE', '禁加工引用未知 Recipe', row, 'recipe')
            if eqp in self.cp and row.get('type'):
                self.check(clean_type(row['type']) == clean_type(self.cp[eqp].get('type')), 'CONSTRAINT_TYPE', '禁加工 TYPE 与对应 EQP.TYPE 不一致', row, 'type')
            forbidden.add((eqp, recipe))
        for product, item in self.products.items():
            for row in self.routes.get(product, []):
                if identifier(row.get('step')).startswith('CP') and self.has('eqp', ('id', 'type')):
                    available = [k for k, e in self.cp.items() if clean_type(e.get('type')) in item['platforms'] and
                                 clean_type(e.get('status')) == 'RUN' and (k, identifier(row.get('recipe'))) not in forbidden]
                    if not available:
                        self.add('WARNING', 'NO_ELIGIBLE_MACHINE', '本工序没有兼容的 RUN 机台，可能无法排产：' + product,
                                 '核对 Platform、状态及禁加工；输入能读取不代表能够排产。', row, 'recipe')
        self.lots = []
        totals, total_valid = Counter(), True
        for row in self.effective['lots']:
            prod = flow_product(row.get('lotId', ''), row.get('ppn', ''), self.routes)
            row['_product'] = prod
            if self.has('mps', ('ppn',)) and self.has('flow', ('prod',)):
                self.check(prod in self.products, 'LOT_PRODUCT', 'Lot 未匹配 MPS 产品：' + prod, row, 'ppn')
            selected = row.get('platform') or ';'.join(sorted(self.products.get(prod, {}).get('platforms', [])))
            if selected:
                self.platform(row, selected)
                if prod in self.products:
                    self.check(set(split_platforms(selected)) <= self.products[prod]['platforms'], 'LOT_PLATFORM', 'Lot 机台组超出该产品 MPS 范围', row, 'platform', value=selected)
            if row.get('_quantity') is not None: totals[prod] += row['_quantity']
            else: total_valid = False
            self.lots.append(row)
        if 'lots' in self.configured and not self.tables['lots'] and any(p['quantity'] for p in self.products.values()):
            self.add('ERROR', 'EMPTY_LOTS', '已配置 Lot 明细表为空，但 MPS 有正数需求', '补齐明细，或删除 lots 映射使用该版默认拆批。', table='lots')
        if self.lots and total_valid and self.has('lots', ('lotId', 'wafer')):
            for product, item in self.products.items():
                if item['valid']:
                    self.check(totals[product] == item['quantity'], 'LOT_TOTAL',
                               '此旧版本要求 Lot wafer 合计等于 MPS：%s，Lot=%s，MPS=%s' % (product, totals[product], item['quantity']), item['row'], 'mps')
        elif 'lots' not in self.configured:
            self.add('WARNING', 'GENERATED_LOTS', '没有 Lot 明细映射；此版本按 MPS 拆为 25 片及尾批，不代表实际库存', '有现场 Lot 明细时配置 lots 表。', table='lots')
        self.plan_references()
        self.weekly_checks()

    def platform(self, row, value):
        if not self.has('eqp', ('id', 'type')): return
        groups = split_platforms(value)
        missing = sorted(set(groups) - self.types)
        if not groups or missing:
            source = '手工机台配置（覆盖 Excel EQP）' if 'eqp' in self.overrides else 'Excel EQP'
            self.add('ERROR', 'PLATFORM', 'Platform 未匹配 EQP.TYPE；缺少：%s；有效 TYPE：%s；来源：%s' %
                     (','.join(missing) or '空', ','.join(sorted(self.types)) or '无', source),
                     '型号必须直接匹配；K8000 与 K8000L 不会自动视为相同。TYPE 可重复，EQPID 不可重复。', row, 'platform', value=value)

    def plan_references(self):
        self.pm_rows, self.borrow_rows, self.wip_rows = [], [], []
        data = dict(eqp=self.equipment, bakeResources=self.bake)
        for row in self.effective['pm']:
            before = len(self.issues)
            plan = {k: row.get(k, '') for k in ('eqp', 'targetKind', 'early', 'due', 'over', 'start')}
            plan['eqp'], plan['targetKind'], plan['hours'] = identifier(plan['eqp']), identifier(plan['targetKind']), row.get('_hours')
            if self.has('eqp', ('id', 'type')) and self.bake['chambers']:
                try: resolve_pm_target(data, plan)
                except (ValueError, TypeError) as error:
                    self.add('ERROR', 'PM_TARGET', str(error), '保留维护对象编号，在有效 EQP 表补对应 proberID；编号有歧义时填 TargetKind。', row, 'eqp')
            dates = row.get('_dates', {})
            if len(self.issues) == before and all(k in dates for k in ('early', 'due', 'over')) and plan['hours'] and 0 < plan['hours'] <= 744:
                if not blank(plan['start']) and 'start' not in dates: continue
                try:
                    pm_events(dict(data, pm=[plan]))
                    if dates['early'].date() <= dates['due'].date() <= dates['over'].date(): self.pm_rows.append((row, plan))
                except (ValueError, OverflowError): pass  # Individual window errors were already reported.
        for row in self.effective['borrow']:
            eqp = identifier(row.get('eqp'))
            if self.has('eqp', ('id', 'type')): self.check(eqp in self.cp, 'BORROW_EQP', '借机只能引用 EQPID，不能填写 proberID 或烤箱', row, 'eqp')
            dates = row.get('_dates', {})
            if eqp in self.cp and row.get('_hours') is not None and 0.6 <= row['_hours'] <= 24 and 'date' in dates:
                if row.get('start') and 'start' not in dates: continue
                self.borrow_rows.append((row, dict(eqp=eqp, date=row.get('date'), start=row.get('start'), hours=row['_hours'])))
        # LotKey refers to the row order after blank rows have been removed.
        lot_keys = {('LOT-%06d' % (n + 1)): row for n, row in enumerate(self.lots)}
        if not self.lots and 'lots' not in self.configured:
            index = 0
            for product, item in self.products.items():
                if not item['valid']: continue
                # Only materialize identities needed by WIP; huge MPS is reported
                # without allocating millions of synthetic rows in the auditor.
                for row in self.effective['wip']:
                    lid = display(row.get('lotId'))
                    match = re.fullmatch(re.escape(product) + r'-L(\d+)', identifier(lid))
                    if match and 1 <= int(match[1]) <= math.ceil(item['quantity'] / 25):
                        lot_keys['LOT-%06d' % (index + int(match[1]))] = {'lotId': lid}
                index += math.ceil(item['quantity'] / 25)
        seen = set()
        for row in self.effective['wip']:
            lid, key = identifier(row.get('lotId')), identifier(row.get('lotKey'))
            matches = [k for k, lot in lot_keys.items() if identifier(lot.get('lotId')) == lid]
            can_bind = self.has('lots', ('lotId',)) if 'lots' in self.configured else self.has('mps', ('ppn', 'mps'))
            if not can_bind:
                self.skip('wip', 'Lot 标识来源不可读，暂不判断在制是否引用未知 Lot；资源和时间继续检查。')
            elif key:
                self.check(key in matches, 'WIP_LOT_KEY', '在制 LotKey 与 LotID 不匹配', row, 'lotKey')
            elif can_bind:
                self.check(len(matches) == 1, 'WIP_LOT', '在制 LotID 不存在或对应多行；重复 LotID 必须用 LotKey 指定', row, 'lotId')
                key = matches[0] if len(matches) == 1 else ''
            if key:
                self.check(key not in seen, 'WIP_DUPLICATE', '同一 Lot 行重复出现在在制表', row, 'lotKey'); seen.add(key)
            eqp, chamber = identifier(row.get('eqp')), identifier(row.get('chamber'))
            if not eqp:
                self.check(not chamber and not row.get('card'), 'WIP_WAIT', '等待中的 Lot 不应填写针卡或腔体', row, 'eqp'); continue
            valid_resource = eqp in self.eqps or eqp in self.bake['ids']
            self.check(valid_resource, 'WIP_EQP', '在制机台不存在', row, 'eqp')
            resource = eqp
            if eqp in self.bake['ids']:
                resource = chamber
                self.check(chamber in {eqp + '-C%d' % n for n in range(1, self.bake['chambers'] + 1)}, 'WIP_CHAMBER', 'BAKE 腔体不存在或不属于该烤箱', row, 'chamber')
                self.check(not row.get('card'), 'WIP_CARD', 'BAKE 在制不应填写 CP 针卡', row, 'card')
            elif eqp in self.cp:
                self.check(not chamber, 'WIP_CHAMBER', 'CP 在制不应填写 BAKE 腔体', row, 'chamber')
                self.check(bool(row.get('card') or self.cp[eqp].get('currentCard')), 'WIP_CARD', 'CP 在制需要针卡类型', row, 'card')
            dates = row.get('_dates', {})
            if valid_resource and resource and all(k in dates for k in ('start', 'end')) and dates['start'] < dates['end']:
                self.wip_rows.append((row, resource, dates['start'], dates['end'], eqp in self.bake['ids']))

    def weekly_checks(self):
        seen, spans = set(), {}
        for row in self.effective['weekly']:
            key = (base_ppn(row.get('ppn')), identifier(row.get('week')))
            self.check(key[0] in self.products, 'WEEK_PRODUCT', '周目标 PPN 不存在于 MPS', row, 'ppn')
            self.check(key not in seen, 'WEEK_DUPLICATE', '周目标 PPN/Week 组合重复', row, 'week'); seen.add(key)
            dates = row.get('_dates', {})
            if 'start' not in dates or 'end' not in dates: continue
            span = (dates['start'], dates['end'] + timedelta(days=1))
            if key[1] in spans:
                self.check(span == spans[key[1]][0], 'WEEK_INCONSISTENT', '同一 Week 的日期范围不一致', row, 'week')
            else: spans[key[1]] = (span, row)
        values = sorted(spans.values(), key=lambda x: x[0])
        for i, (span, row) in enumerate(values):
            for other, prior in values[:i]:
                if span[0] < other[1] and span[1] > other[0]:
                    self.add('ERROR', 'WEEK_OVERLAP', '不同 Week 的日期范围重叠：' + display(prior.get('week')), row=row, field='week')

    def calendars(self):
        blocks = defaultdict(list)
        groups = Counter()
        for row, resource, start, end, oven in self.wip_rows:
            for a, b, prior in blocks[resource]:
                if start < b and a < end and not (oven and (start, end) == (a, b)):
                    self.add('ERROR', 'WIP_CONFLICT', '在制资源占用重叠：%s，另一条为第 %s 行/记录 %s' % (resource, prior.get('_row', ''), prior.get('_record', '')), row=row, field='start')
            groups[(resource, start, end)] += 1
            if oven and groups[(resource, start, end)] > 4:
                self.add('ERROR', 'WIP_BATCH_SIZE', '同腔体同时间在制超过 4 Lot', row=row, field='chamber')
            blocks[resource].append((start, end, row))
        if len(self.pm_rows) != len(self.effective['pm']): self.skip('pm', '无效 PM 行未参与日历检查，其相关冲突需修复后复查。')
        data = dict(eqp=self.equipment, bakeResources=self.bake, pm=[p for _, p in self.pm_rows])
        events = pm_events(data)
        for event in events:
            rows = [self.pm_rows[int(s.split('-')[1]) - 1][0] for s in event['sourceIds']]
            first = parse_datetime(event.get('start') or event['early'])
            duration = timedelta(hours=event['hours'])
            limit = parse_datetime(event['over']) + timedelta(days=1)
            conflicts = sorted([b for r in event['resources'] for b in blocks[r]], key=lambda b: (b[0], b[1]))
            slot = first
            for a, b, _ in conflicts:
                if slot + duration <= a: break
                if slot < b: slot = b
            if slot + duration > limit or (event.get('start') and slot != first):
                for row in rows:
                    self.add('ERROR', 'PM_CONFLICT', '组合停机与在制或其他 PM 冲突，维护窗口内无连续空档',
                             '核对整组资源：' + ', '.join(event['resources']) + '；不要删除配对部件的维护任务。', row, 'start')
                continue
            if event['merged']:
                self.add('INFO', 'PM_MERGED', '配对部件维护时间重叠，合并停机但保留 %d 项维护任务' % len(rows),
                         row=rows[0], field='start', value=','.join(str(r.get('_row') or r.get('_record')) for r in rows))
            for resource in event['resources']: blocks[resource].append((slot, slot + duration, rows[0]))
        for row, plan in sorted(self.borrow_rows, key=lambda item: (not bool(item[1].get('start')), parse_datetime(item[1].get('start') or item[1]['date']))):
            first = parse_datetime(plan.get('start') or plan['date'])
            duration = timedelta(hours=plan['hours']); slot = first
            if clean_type(self.cp[plan['eqp']].get('status')) == 'LOST':
                self.add('WARNING', 'BORROW_LOST', '借机机台为 LOST，可能被取消', row=row, field='eqp')
            for a, b, _ in sorted(blocks[plan['eqp']], key=lambda b: (b[0], b[1])):
                if slot + duration <= a: break
                if slot < b: slot = b
            latest = first if plan.get('start') else parse_datetime(plan['date']) + timedelta(days=1) - timedelta(microseconds=1)
            if slot > latest:
                self.add('WARNING', 'BORROW_CONFLICT', '借机与 PM、在制或其他借机冲突；本版可能取消或推荐其他时段', row=row, field='start')
            else: blocks[plan['eqp']].append((slot, slot + duration, row))

    def guard(self, phase, action, row=None):
        try: action()
        except Exception as error:
            self.incomplete = True
            self.add('ERROR', 'INTERNAL_CHECK', phase + ' 未完整完成：' + type(error).__name__ + ': ' + str(error),
                     '此项不能判为通过；保留报告交给开发者，其他检查继续。', row=row)

    def run(self, workbook):
        self.guard('配置检查', self.config_check)
        self.guard('表头检查', lambda: self.structure(workbook))
        self.guard('单元格检查', self.values)
        self.guard('跨表关联检查', self.references)
        self.guard('日历检查', self.calendars)
        return self

    def engine_check(self):
        # Final parity check uses the unmodified production loader. It cannot
        # replace the accumulating checks above, and never starts the solver.
        if any(i['severity'] == 'ERROR' for i in self.issues):
            self.skip('loader', '批量检查已发现错误；实际导入函数复核待修复后执行。')
            return
        generated = sum(math.ceil(p['quantity'] / 25) for p in self.products.values() if p['valid'])
        if not self.lots and generated > 200000:
            self.skip('loader', '默认拆批超过 200000 Lot，未执行可能大量占用内存的程序复核；字段检查已完成。')
            return
        try:
            payload = build_payload(config_path=self.config_path, config=self.config)
            for message in payload.get('planIssues', []):
                self.add('ERROR', 'LOADER_CALENDAR', message, '参照前面的行级日历问题修正。', source='程序复核')
            self.engine_passed = not payload.get('planIssues')
        except Exception as error:
            self.add('ERROR', 'LOADER_CHECK', '实际导入函数复核未通过：' + str(error),
                     '这是程序实际读取时的首个阻断信息；前面的批量检查已继续检查其他表和记录。', source='程序复核')

    def report(self):
        counts = Counter(i['severity'] for i in self.issues)
        return dict(version=VERSION, createdAt=datetime.now(CST).isoformat(timespec='seconds'),
                    config=self.config_path, excel=self.source_path, sha256=self.source_hash,
                    mode='仅 Excel 对照（不写配置）' if self.excel_only else '服务器有效输入（包含手工覆盖）',
                    counts={k: counts[k] for k in ('ERROR', 'WARNING', 'SKIPPED', 'INFO')},
                    incomplete=self.incomplete, loaderPassed=self.engine_passed, sheets=self.sheet_stats,
                    issues=self.issues,
                    limits=['基于 Testing_11101136.zip 加 PM 修复版；不套用后续生产快照的新字段要求。',
                            '不启动排产；产能不足、优化耗时及完整排程可行性仍需运行程序验证。',
                            '缺失/损坏字段只跳过依赖检查，不阻止其他可读取记录检查；修复后须重跑。'])


def read_for_audit(path, audit):
    """Tolerant XLSX reader: preserve physical row positions and isolate bad cells/sheets.

    The final build_payload pass checks parity with the application's strict reader.
    Formula values are cached Excel values, never evaluated by this tool.
    """
    with ZipFile(path) as archive:
        shared, styles = [], set()
        if 'xl/sharedStrings.xml' in archive.namelist():
            root = ET.fromstring(archive.read('xl/sharedStrings.xml'))
            shared = [''.join(t.text or '' for t in si.iter('{%s}t' % NS['m'])) for si in root.findall('m:si', NS)]
        if 'xl/styles.xml' in archive.namelist():
            root = ET.fromstring(archive.read('xl/styles.xml')); custom, declared = set(), set()
            for nf in root.iter('{%s}numFmt' % NS['m']):
                fid = int(nf.get('numFmtId')); declared.add(fid)
                code = re.sub(r'"[^"]*"|\\.|\[[^]]*\]', '', nf.get('formatCode', '').lower())
                if re.search(r'[ymdhs]', code) and '#' not in code and '0.0' not in code: custom.add(fid)
            xfs = root.find('m:cellXfs', NS)
            for index, xf in enumerate(list(xfs) if xfs is not None else []):
                fid = int(xf.get('numFmtId', '0'))
                if fid in custom or fid not in declared and (14 <= fid <= 22 or 27 <= fid <= 36 or 45 <= fid <= 47 or 50 <= fid <= 58): styles.add(index)
        workbook = ET.fromstring(archive.read('xl/workbook.xml'))
        props = workbook.find('m:workbookPr', NS)
        date1904 = props is not None and props.get('date1904') in ('1', 'true')
        relationships = {r.get('Id'): r.get('Target') for r in ET.fromstring(archive.read('xl/_rels/workbook.xml.rels'))}
        sheets = []
        for sh in workbook.find('m:sheets', NS):
            name, rows = sh.get('name'), []
            try:
                target = relationships[sh.get('{%s}id' % RNS)]
                member = target.lstrip('/') if target.startswith('/') else posixpath.normpath('xl/' + target)
                root = ET.fromstring(archive.read(member)); data = root.find('m:sheetData', NS)
                for r in list(data) if data is not None else []:
                    number = int(r.get('r', len(rows) + 1)); values = {}
                    for cell in r:
                        ci = col_index(cell.get('r', '')); value = ''
                        v, inline = cell.find('m:v', NS), cell.find('m:is', NS)
                        try:
                            if cell.get('t') == 's' and v is not None: value = shared[int(v.text)]
                            elif cell.get('t') == 'inlineStr' and inline is not None:
                                value = ''.join(t.text or '' for t in inline.iter('{%s}t' % NS['m']))
                            elif v is not None:
                                value = v.text or ''
                                if cell.get('t', 'n') == 'n' and cell.get('s') is not None and int(cell.get('s')) in styles:
                                    value = excel_date(value, date1904)
                            if cell.get('t') == 'e':
                                audit.cell_issues.append(dict(code='EXCEL_ERROR', message='Excel 单元格包含错误值', sheet=name, row=number, column=column_name(ci), value=value))
                            if cell.find('m:f', NS) is not None:
                                audit.cell_issues.append(dict(code='FORMULA_CACHE' if v is not None and v.text else 'FORMULA_EMPTY',
                                    message='公式使用缓存结果，请确认已重新计算' if v is not None and v.text else '公式没有已保存的计算结果',
                                    sheet=name, row=number, column=column_name(ci), value=value))
                        except (ValueError, TypeError, IndexError, OverflowError) as error:
                            audit.cell_issues.append(dict(code='CELL_READ', message='单元格读取失败：' + str(error), sheet=name, row=number, column=column_name(ci), value=v.text if v is not None else ''))
                        values[ci] = value
                    while len(rows) < number: rows.append([])
                    rows[number - 1] = [values.get(i, '') for i in range(max(values, default=-1) + 1)]
                sheets.append((name, rows))
            except (ValueError, KeyError, ET.ParseError) as error:
                audit.add('ERROR', 'SHEET_READ', '此子表读取失败：' + str(error), '用 Excel/WPS 另存为标准 .xlsx 后检查；其他表仍继续。', sheet=name)
                audit.skip(name, '损坏子表无法检查其记录。')
        return sheets


def report_text(report):
    lines = ['Excel 导入批量校验报告', '版本：' + report['version'], '时间：' + report['createdAt'],
             'Excel：' + report['excel'], '配置：' + report['config'], '模式：' + report['mode'],
             '汇总：' + ' / '.join('%s=%s' % pair for pair in report['counts'].items()),
             '实际导入函数复核：' + ('通过' if report['loaderPassed'] else '未通过或未完成'),
             'ERROR=错误；WARNING=警告；SKIPPED=依赖不足未检查；INFO=说明。物理行号与手工记录序号分开。', '']
    for index, issue in enumerate(report['issues'], 1):
        where = ' / '.join(display(issue[k]) for k in ('source', 'table', 'sheet') if issue[k])
        if issue['row']: where += ' / Excel 第 %s 行' % issue['row']
        elif issue['record']: where += ' / 手工记录第 %s 条' % issue['record']
        if issue['column']: where += ' / %s 列' % issue['column']
        lines.extend(['%d. [%s] %s — %s' % (index, issue['severity'], issue['code'], where),
                      '   字段：%s；原值：%s' % (issue['field'], issue['value']), '   ' + issue['message']])
        if issue['fix']: lines.append('   修改建议：' + issue['fix'])
    lines.extend(['', '检查边界：'] + report['limits'])
    return '\n'.join(lines) + '\n'


def write_reports(audit, directory):
    directory = Path(directory); directory.mkdir(parents=True, exist_ok=True)
    report = audit.report()
    # Each run has its own directory; never replace the workbook or server files.
    folder = directory / ('excel_audit_' + datetime.now(CST).strftime('%Y%m%d_%H%M%S_%f'))
    folder.mkdir()
    (folder / 'report.txt').write_text(report_text(report), encoding='utf-8-sig')
    (folder / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    fields = ['severity', 'code', 'source', 'table', 'sheet', 'row', 'column', 'record', 'field', 'value', 'message', 'fix']
    with (folder / 'issues.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields); writer.writeheader()
        for item in report['issues']:
            # Prevent source values becoming formulas when the report is opened in Excel.
            writer.writerow({k: ("'" + str(v) if str(v).lstrip().startswith(('=', '+', '-', '@')) else v) for k, v in item.items()})
    return folder, report


class ReportArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


def main(argv=None):
    parser = ReportArgumentParser(description='一次列出可检查的 Excel 导入错误，只输出报告文件，不启动排产、不改 Excel 或服务器配置。')
    parser.add_argument('--config', default=str(ROOT / 'config/config.json'), help='服务器实际 config.json')
    parser.add_argument('--excel', help='待校验 .xlsx；省略时读取配置中的路径')
    parser.add_argument('--month', help='仅本次校验的计划月份 YYYY-MM')
    parser.add_argument('--project-root', default=str(ROOT), help='配置中相对 Excel 路径的基准目录')
    parser.add_argument('--excel-only', action='store_true', help='仅本次忽略 EQP/PM/借机/在制手工覆盖，对照原 Excel')
    parser.add_argument('--output', default=str(Path.cwd() / 'excel_audit_reports'), help='报告输出目录')
    parser.add_argument('--version', action='version', version=VERSION)
    try:
        args = parser.parse_args(argv)
    except ValueError as error:
        arguments = list(sys.argv[1:] if argv is None else argv)
        output = str(Path.cwd() / 'excel_audit_reports')
        for index, item in enumerate(arguments):
            if item.startswith('--output='): output = item.split('=', 1)[1]
            elif item == '--output' and index + 1 < len(arguments): output = arguments[index + 1]
        audit = Audit({}); audit.incomplete = True
        audit.add('ERROR', 'ARGUMENTS', '命令参数有误：' + str(error), '按说明填写 --config、--excel、--output；含空格的路径用双引号包围。', source='参数')
        try: write_reports(audit, output)
        except OSError:
            try: write_reports(audit, Path(tempfile.gettempdir()) / 'TestingExcelAudit')
            except OSError: pass
        return 2
    config_path = Path(args.config).resolve()
    try:
        config = json.loads(config_path.read_text(encoding='utf-8-sig'))
    except (OSError, ValueError) as error:
        audit = Audit({}, config_path, args.excel_only)
        audit.incomplete = True
        audit.add('ERROR', 'CONFIG_READ', '配置无法读取：' + str(error), '传入服务器实际 config.json；不使用演示配置替代现场配置。', source='配置')
    else:
        audit = Audit(config, config_path, args.excel_only)
        if args.month and isinstance(audit.config, dict) and isinstance(audit.config.get('dataSource'), dict):
            audit.config['dataSource']['month'] = args.month
        audit.config_check()
        source = args.excel or audit.src.get('path', '')
        try:
            if not isinstance(source, str) or not source: raise ValueError('没有指定 Excel 路径')
            if source.startswith(('http://', 'https://')): raise ValueError('请先下载到本地，再使用 --excel 指定文件；本脚本不访问网络')
            path = Path(source)
            if not path.is_absolute(): path = Path(args.project_root) / path
            path = path.resolve(); audit.source_path = str(path)
            if path.suffix.lower() != '.xlsx': raise ValueError('仅支持 .xlsx；.xls/.xlsm/加密文件请另存为 .xlsx')
            audit.source_hash = hashlib.sha256(path.read_bytes()).hexdigest()
            audit.src['path'] = str(path)
            if path.stat().st_size > 20 * 1024 * 1024:
                audit.add('ERROR', 'UPLOAD_SIZE', 'Excel 超过页面上传的 20 MB 限制', '缩减无用内容或由管理员配置服务器本地文件路径。')
            workbook = read_for_audit(path, audit)
            audit.guard('表头检查', lambda: audit.structure(workbook))
            audit.guard('单元格检查', audit.values)
            audit.guard('关联检查', audit.references)
            audit.guard('日历检查', audit.calendars)
            audit.engine_check()
        except Exception as error:
            audit.incomplete = True
            audit.add('ERROR', 'FILE_READ', '文件无法完整读取：' + str(error), '核对路径、文件权限及是否为标准未加密 XLSX；能读取的配置问题仍列在本报告。')
            audit.skip('workbook', '工作簿无法完整读取，未检查内容不能判为通过。')
    try:
        folder, report = write_reports(audit, args.output)
    except OSError as error:
        audit.add('ERROR', 'REPORT_WRITE', '指定报告目录无法写入：' + str(error), '检查输出目录权限；本报告改存系统临时目录。', source='报告')
        try:
            write_reports(audit, Path(tempfile.gettempdir()) / 'TestingExcelAudit')
        except OSError:
            pass  # All configured destinations are unwritable; exit status remains failure.
        return 2
    return 2 if audit.incomplete else 1 if report['counts']['ERROR'] or report['counts']['SKIPPED'] or not audit.engine_passed else 0


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'): sys.stdout.reconfigure(errors='replace')
    raise SystemExit(main())
