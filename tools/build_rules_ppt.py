#!/usr/bin/env python3
"""Build the rules presentation from the verified rerun report.

This replaces .artifacts/build_rules_ppt.mjs. python-pptx is an optional
authoring dependency: it is not needed to run the scheduling service.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT = ROOT / 'output/新版排程_重跑核验.json'
DEFAULT_OUTPUT = ROOT / '.artifacts/rules-draft.pptx'


def pages_for(metrics):
    return [
        ('Testing 排程业务规则', 'wafer 口径与六种固定路线', [
            '72 个产品，MPS 合计 11,000 片 wafer',
            '按 Lot 排程，按完工 wafer 统计 MPS 达成率',
            'BAKE 恢复 2h 升温和 2h 降温，完整规则以本版为准']),
        ('需求与任务的单位', 'MPS 是 wafer 数，Lot 是执行任务', [
            '每个 PPN 的 MPS 是产品所需 wafer 片数，合计 11,000 片',
            '每 Lot 最多 25 片，CP 工时仍按分钟/lot，不乘片数',
            '月初全部需求已知，月末检查产品完成 wafer 数',
            '合成输入的需求分布、工时及计划仍需真实生产表替换']),
        ('Lot 拆分与尾批', '25 片满批加余数是默认假设，实际 Lot 明细优先', [
            '例如需求 62 片，缺少实际明细时拆为 25、25、12 片',
            '若实际是 20、20、22 片，同样有效，保留实际拆分',
            '本算例默认拆为 %d 个 Lot，各 PPN 片数合计严格对应 MPS'
            % metrics['totalLots'],
            '每 Lot 1–25 片是硬约束；必须满 25 片不是硬约束']),
        ('七张表的关联', 'PPN、Recipe 与 EQPID 连接业务对象', [
            'MPS.PPN 匹配路线表 PROD，Platform 直接匹配 EQP.TYPE',
            '每道 CP 的 RECIPE 匹配针卡表 Recipe，获得候选类型',
            'PM、借机用 EQPID 锁定具体设备与占用时段',
            '约束条件记录具体机台禁止加工的 Recipe']),
        ('工序顺序与时长', '只执行产品路线中实际存在的工序', [
            '同一 PROD 的原表行顺序就是执行顺序，不按 STEP 重排',
            '采用已确认六种顺序，所有等待步骤统一为 QTIME1',
            'CP：TIME 为分钟/lot，尾批也按一个 Lot 计算',
            'BAKE 和 QTIME 的 TIME 为空，从 RECIPE 解析加工/等待时长']),
        ('六种合法路线：1–3', 'FLOW 是顺序模式的概括，不新增业务表字段', [
            '1. CP1、BAKE、CP2、BAKE、CP3、QTIME1、CP4',
            '2. CP1、BAKE、CP2、QTIME1、CP3',
            '3. CP1、BAKE、CP2、QTIME1、CP3、CP4']),
        ('六种合法路线：4–6', '两次 BAKE 是不同工序，分别匹配各行 Recipe', [
            '4. CP1、BAKE、CP2、BAKE、CP3、CP4、QTIME1',
            '5. CP1、BAKE、CP2',
            '6. CP1、BAKE、CP2、BAKE、CP3',
            '合成验证暂按每种 12 个 PPN 分配，真实产品归属待替换']),
        ('CP 机台与针卡', '资格筛选与时间占用分别判断', [
            '仅 Status=RUN 可用于生产，月初默认空闲',
            '多 Platform 表示同一道 CP 可任选其中一个机台组',
            '针卡表 31 个 Recipe，每个 1–4 个候选类型，共 62 种类型',
            '针卡实物数量充足，同种类型可跨机并行使用']),
        ('换卡与禁加工', '按机台安装状态判断是否需要换卡', [
            'Current ProbeCard 是排程起点已安装的针卡类型',
            '沿用同类型无需换卡，换类型占机 5 分钟',
            '新 Recipe 若兼容当前类型，即可直接沿用',
            '命中机台对应的 Constraint Recipe，禁止分配该机台']),
        ('BAKE 合批条件', '7 台 OVEN，每台 4 个独立腔体', [
            '共 28 个腔体，每腔每批最多 4 Lot',
            '允许不同 PPN 合批，但 RECIPE 必须完全一致',
            '不再使用 50 或 70 片的批次上限；每 Lot 自身最多 25 片',
            '不满 4 Lot 可以开批，等待合批由算法决定']),
        ('BAKE 时间与 Lot 放行', '每批占腔 28h，Lot 在第 26h 放行', [
            '210C24H 为 210℃加工24h，250C24H 为 250℃加工24h',
            '每批升温 2h、Recipe 加工 24h、降温/换 Lot 2h',
            '加工结束时 Lot 可进入下一步，腔体继续占用至降温结束',
            '首批、同温或不同温后续批次，都计入上述补偿']),
        ('QTIME 与完整路线完成', '等待时长取自 RECIPE', [
            'QTIME1.RECIPE 仅 7、14、28，单位分钟，TIME 为空',
            '前序结束后开始等待，不占机台、针卡或腔体',
            '等满才能进入下一道工序，末尾 QTIME 也必须完成',
            '只有完整路线完工的 Lot，其 wafer 数才计入 MPS 完成量']),
        ('PM 维护', '固定设备，在允许窗口内连续执行', [
            'Early Due 是最早开始，Over Due 当日结束前完成',
            'Due Date 为期望日期，Estimated Hours 为连续维护小时数',
            '算法在窗口内选择维护空档，不与生产任务重叠',
            '一台 OVEN 维护时，所属四个腔体同时停用']),
        ('借机计划', '119 条，固定机台，指定日期内开始', [
            'DATE 仅日期，连续时长 0.6–24 小时，允许跨次日结束',
            '不能换 EQPID，不能缩短或拆分借机时长',
            '提前协调避冲突，不能打断已开始的生产',
            '当天实在无法执行时，保留异常并给原机台建议时段']),
        ('MPS 达成率与均衡目标', '数量统一按 wafer，资源调度仍按 Lot', [
            'PPN 达成率 = 月内完整路线完成 wafer / 该产品 MPS wafer',
            '优先改善低达成率产品，再比较月内完成 wafer 总量',
            '公平派工按累计 wafer 份额计算，避免把尾批当成满批产出',
            '启发式比较立即开批、等待4h、8h，不声称全局最优']),
        ('本轮合成算例结果', '基于 11,000 wafer 与 2+24+2h 的新口径', [
            '月内完整路线完成 {:,} / 11,000 wafer，达成率 {:.2f}%'.format(
                metrics['completedWafers'], 100 * metrics['completionRate']),
            '对应 {} / {} 个 Lot，最低产品达成率 {:.2f}%'.format(
                metrics['completedLots'], metrics['totalLots'],
                100 * metrics['minProductRate']),
            '{} / 119 条借机已安排，{} / {} 次 PM 在窗口内完成'.format(
                metrics['borrowScheduled'], metrics['pmInWindow'],
                metrics['pmCount']),
            '替换真实 Lot 拆分、工时、产品路线归属和计划后需重新计算']),
        ('需求单位如何影响任务量', 'wafer 数与 Lot 数不能互相替代', [
            '11,000 wafer 经默认拆批形成 476 Lot，不是 11,000 Lot',
            '一条含两次 BAKE 的路线仍执行两次，分别占腔 28h',
            'CP 用分钟/lot，BAKE 用小时/批，QTIME 用分钟等待',
            '不同工序单位分别计算，最终产出统一还原为 wafer']),
        ('输入假设与生产边界', '保留已确认规则，合成数据单独标明', [
            '27 台 CP 暂按三个组各 9 台，机台组名称为占位',
            '六种路线暂各分配 12 个产品，工时与计划明细为合成占位',
            '满25片加尾批只是缺少明细时的默认拆法，不强制实际满批',
            '此结果用于核对业务逻辑，不作为真实现场产能承诺']),
    ]
def build(report_path, output_path):
    try:
        from pptx import Presentation
        from pptx.dml.color import RGBColor
        from pptx.util import Inches, Pt
    except ImportError:
        print('缺少可选依赖 python-pptx。安装后再生成 PPT；服务运行不需要它。',
              file=sys.stderr)
        return 1
    report = json.loads(Path(report_path).read_text(encoding='utf-8'))
    metrics = report['metrics']
    pages = pages_for(metrics)
    deck = Presentation()
    deck.slide_width = Inches(1280 / 96)
    deck.slide_height = Inches(720 / 96)
    blank = deck.slide_layouts[6]

    def textbox(slide, text, x, y, width, height, size, color, bold=False):
        shape = slide.shapes.add_textbox(
            Inches(x / 96), Inches(y / 96), Inches(width / 96), Inches(height / 96))
        frame = shape.text_frame
        frame.word_wrap = True
        frame.margin_left = frame.margin_right = 0
        frame.margin_top = frame.margin_bottom = 0
        frame.text = text
        for paragraph in frame.paragraphs:
            for run in paragraph.runs:
                run.font.name = 'PingFang SC'
                run.font.size = Pt(size)
                run.font.bold = bold
                run.font.color.rgb = RGBColor.from_string(color.lstrip('#'))

    for index, (title, subtitle, body) in enumerate(pages):
        slide = deck.slides.add_slide(blank)
        dark = index == 0
        background = slide.background.fill
        background.solid()
        background.fore_color.rgb = RGBColor.from_string('123646' if dark else 'F8FAF9')
        textbox(slide, title, 64, 48, 1150, 64, 44,
                '#FFFFFF' if dark else '#123646', True)
        textbox(slide, subtitle, 66, 128, 1140, 44, 25,
                '#9ADACB' if dark else '#657D85')
        for offset, line in enumerate(body):
            textbox(slide, line, 70, 218 + offset * 94, 1120, 80, 29,
                    '#FFFFFF' if dark else '#183846')
        textbox(slide, '2026-11-06 确认的业务规则', 66, 676, 960, 24, 16, '#81999E')
        textbox(slide, '%02d' % (index + 1), 1180, 676, 50, 24, 16, '#81999E')
        slide.notes_slide.notes_text_frame.text = (
            '依据：本次用户确认 MPS=11,000 wafer、每Lot最多25片、实际拆分优先，'
            '并恢复升温2h和降温2h。规则见 docs/排程业务规则_确认清单.md；'
            '结果见 output/新版排程_重跑核验.json。合成验证参数不等于生产实际参数。')

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    deck.save(str(output))
    print('已生成', output, '页数', len(deck.slides))
    return 0


def main():
    parser = argparse.ArgumentParser(description='生成规则 PPTX')
    parser.add_argument('--report', default=str(DEFAULT_REPORT))
    parser.add_argument('--output', default=str(DEFAULT_OUTPUT))
    args = parser.parse_args()
    return build(args.report, args.output)


if __name__ == '__main__':
    raise SystemExit(main())