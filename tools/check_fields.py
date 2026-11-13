#!/usr/bin/env python3
"""Print the configured sheet/field mapping and compare it with the workbook."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'backend'))
sys.path.insert(0, os.path.join(ROOT, 'tools'))

from server_core import (DEFAULT_CONFIG, EMPTY_OPTIONAL_SHEETS, load_config, mapped_column,
                         norm_key, read_source, rows_for)  # noqa: E402
from build_data import read_workbook  # noqa: E402


FIELDS = {
    'mps': ('MPS 需求', [
        ('platform', True, 'Platform，多个机台组用分号'),
        ('ppn', True, '产品编号；- 后厂家后缀忽略，前缀匹配 flow.PROD；同一 PPN 多行按汇总'),
        ('mps', True, 'wafer 需求，非负整数'),
    ]),
    'eqp': ('EQP 设备', [
        ('type', True, 'TYPE，机台组；含 OVEN 表示 BAKE'),
        ('id', True, 'EQPID，唯一'),
        ('currentCard', False, 'Current ProbeCard，初始针卡类型'),
        ('currentRecipe', False, 'Current Recipe，初始 Recipe'),
        ('status', False, 'Status，仅 RUN 可排产'),
    ]),
    'cards': ('针卡', [
        ('recipe', True, 'Recipe'),
        ('cards', True, 'ProbeCard List，候选类型，分号分隔'),
    ]),
    'flow': ('测试flow&工时', [
        ('prod', True, 'PROD，匹配 MPS.PPN'),
        ('step', True, 'STEP，CP1-CP4 / BAKE / QTIME1'),
        ('recipe', True, 'RECIPE'),
        ('time', True, 'Time，CP 为分钟/wafer'),
    ]),
    'borrow': ('借机计划', [
        ('date', True, 'DATE，借机日期'),
        ('eqp', True, 'EQPID，借机机台'),
        ('hours', True, '借机时数，0.6-24 小时'),
    ]),
    'pm': ('PM计划', [
        ('eqp', True, 'EQP ID，维护机台'),
        ('early', True, 'Early Due'),
        ('due', True, 'Due Date'),
        ('over', True, 'Over Due'),
        ('hours', True, 'Estimated Hours'),
    ]),
    'constraints': ('约束条件', [
        ('type', True, 'TYPE，机台组'),
        ('eqp', True, 'EQPID，禁加工机台'),
        ('recipe', True, 'Constraint Recipe，禁止的 Recipe'),
    ]),
    'lots': ('Lot明细（可选）', [
        ('lotId', True, 'LotID；允许重复，- 后为厂家后缀'),
        ('ppn', False, 'PROD；可省略，省略时按 LotID 前缀匹配 flow'),
        ('platform', False, 'Platform；省略时取 MPS'),
        ('wafer', True, 'Wafer Qty，1-25 的整数'),
        ('release', False, 'Release Time；省略为月初'),
        ('week', False, 'Target Week；省略为 W1'),
        ('weeklyDue', False, 'Weekly Due；省略为月初+6天'),
        ('monthlyDue', False, 'Monthly Due；省略为下月1日'),
        ('priority', False, 'Priority；保留字段，当前派工使用公平序'),
    ]),
    'wip': ('在制 Lot（可选）', [
        ('lotId', True, 'LotID，必须存在于 Lot 列表'),
        ('lotKey', False, '行键；同名 Lot 必填，如 LOT-000001'),
        ('eqp', False, '占用机台；等待中可空'),
        ('chamber', False, 'BAKE 腔体，如 OVEN-01-C1'),
        ('card', False, 'CP 针卡类型；BAKE 留空'),
        ('start', False, '占用开始；有资源占用时必填'),
        ('end', False, '预计释放；有资源占用时必填'),
    ]),
    'weekly': ('周目标（可选）', [
        ('platform', False, 'Platform'),
        ('ppn', False, 'PPN'),
        ('week', False, 'Week'),
        ('start', False, 'Week Start'),
        ('end', False, 'Week End'),
        ('target', False, 'Target'),
    ]),
    'params': ('算例参数（可选）', [
        ('category', False, '类别'),
        ('parameter', False, '参数'),
        ('object', False, '对象'),
        ('value', False, '值'),
        ('unit', False, '单位'),
        ('basis', False, '依据'),
    ]),
}


def main():
    config_path = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_CONFIG
    cfg = load_config(config_path)
    source = cfg.get('dataSource', {})
    path = read_source(source.get('path', ''))
    try:
        workbook = read_workbook(path)
    finally:
        if source.get('path', '').startswith(('http://', 'https://')):
            os.unlink(path)
    header_row = int(source.get('headerRow', 1))
    sheet_map = source.get('sheetMap', {})
    field_map = cfg.get('fieldMap', {})
    failures = 0

    print('配置：' + config_path)
    print('数据源：' + source.get('path', ''))
    print('表头行：' + str(header_row))
    for key, (label, fields) in FIELDS.items():
        sheet = sheet_map.get(key)
        print('\n[%s] %s -> %s' % (key, label, sheet or '未配置'))
        if not sheet:
            print('  当前配置不读取此表；需要时在 Sheet 设置中增加 "%s"。' % key)
            continue
        try:
            header, data = rows_for(workbook, sheet, header_row)
        except (KeyError, ValueError) as error:
            print('  !! ' + str(error))
            failures += 1
            continue
        print('  实际表头：' + ' | '.join(str(x) for x in header))
        print('  数据行：%d' % len(data))
        if not any(header) and not data and key in EMPTY_OPTIONAL_SHEETS:
            print('  空的可选计划表，按无记录处理。')
            continue
        mapping = field_map.get(sheet)
        if mapping is None:
            mapping = next((value for name, value in field_map.items() if norm_key(name) == norm_key(sheet)), {})
        for logical, required, note in fields:
            column = mapping.get(logical)
            resolved = mapped_column(header, column)
            display = (column if isinstance(column, str)
                       else ' / '.join(map(str, column or [])))
            status = 'OK' if resolved else ('未映射' if not column else '缺失')
            mark = '*' if required else ' '
            print('  %s %-14s -> %-24s %-6s %s'
                  % (mark, logical, resolved or display or '—', status, note))
            if required and not resolved:
                failures += 1
        separator = mapping.get('separator')
        if separator:
            print('    separator      -> %s' % separator)

    print('\n结果：' + ('全部必需字段已匹配' if not failures else
                       '有 %d 个必需字段未匹配，请检查字段映射' % failures))
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
