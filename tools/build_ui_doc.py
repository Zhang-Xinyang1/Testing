"""Build docs/XMC_Testing_界面说明.docx from the annotated screenshots.

Usage:
    python3 tools/build_ui_doc.py --shots /tmp/shots --out docs/XMC_Testing_界面说明.docx

Screenshots come from frontend/_shot.html (see /tmp/capture.sh): 1440x1000
viewport at 2x. They already carry the numbered dashed callouts, and the
legend under each figure uses the same numbers.
"""
import argparse
import os
import tempfile

from PIL import Image
from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

NAVY = RGBColor(0x16, 0x23, 0x3A)
MUTED = RGBColor(0x6B, 0x7A, 0x91)
ACCENT = RGBColor(0x1B, 0x5D, 0x54)
EAST = '微软雅黑'

FIGURES = [
    ('MPS 达成', 'mps_top', 'MPS 达成 · 总览',
     '首屏给出算例口径、全厂达成率与四张 KPI，用于判断本月是否存在缺口。',
     ['侧边导航：MPS 结果 / 排产甘特 / 数据配置 / 排产诊断。',
      '顶部操作区：服务登录状态与刷新时间、Excel 导入、排产设置、重新排产。',
      'MPS 对比头图：当前月份（2026-09）与排产策略（产品 MPS 达成率均衡），右侧环形图为全厂总量达成率 100.0%。',
      'KPI 四联：Excel MPS 11,000 wafer、算法完成 11,000 wafer、差额 +0 wafer、在制另列 0 lot。',
      '口径说明：未提供实际 Lot 明细时，按每产品 25 片满批加余数尾批生成 478 个 Lot；PPN 以第一个「-」前为准；同一 PPN 多行汇总 wafer，Platform 合并去重。',
      '数据来源卡：只读 Excel 副本、当前算例（2026-09 · 10,000 wafer / 4,710 片 · 服务器加载）。']),
    ('MPS 达成', 'mps_charts', 'MPS 达成 · 完成量图表',
     '两张图分别回答「哪个平台完成了多少」和「完成量落在哪几周」。',
     ['平台完成量：按 Platform 组合汇总 Excel MPS 与算法完成 wafer；一个产品的候选机台组含多个 Platform 时合并展示（如 CP-B;CP-C）。',
      '每周完成量：按月内每周统计完成 wafer；提供周目标时改为累计目标与累计完成的对比，本例未提供周目标，仅显示每周完成。']),
    ('MPS 达成', 'mps_table', 'MPS 达成 · 逐 PPN 明细',
     '表格是交付口径的明细：每个 PPN 一行，可直接核对达成率与首末件时间。',
     ['工具栏：Platform 下拉筛选与 PPN 搜索，右侧为当前筛选结果计数（72 / 72 个 PPN）。',
      '表头：Excel MPS / 待排 Lot / 在制 Lot / 算法完成 / 差异 / 达成率 / 首件完成 / 末件完成 / 状态，单位 wafer。',
      '数据行：Platform 多值以分号连接并单行显示；达成率附进度条，状态列给出「达成 / 缺口」徽章。']),
    ('MPS 达成', 'mps_drawer', '产品详情抽屉',
     '点击表格或图表中的 PPN，从右侧展开该产品的完整口径。',
     ['产品详情抽屉：Excel MPS 目标、算法完成、达成率三项对照；下方按原表路线列出工艺路线各道工序（Recipe、机台/腔体类型、工时），底部汇总首件完成、末件完成、Lot 数与在制耗时。']),
    ('排产甘特', 'gantt_cp', 'CP 机台视图',
     '以机台为行、时间为列展示每台 CP 的占用，空白即可用空档。',
     ['工具栏：资源（CP 机台 / BAKE 腔体 / 全部）、范围（1天 / 3天 / 7天 / 全月）、日期翻页、Platform 筛选、Lot 定位、回到月初；右侧汇总当前资源规模（27 台 CP · 28 个 BAKE 腔体）。',
      '图例与列说明：CP 任务按机台组着色，另列 BAKE 批、PM 维护、借机、在制占用 / 不可用的配色，以及表示换线的红色标记；并说明左侧四列口径。',
      '左侧统计列：换线次数（相邻两单 Recipe 或针卡变化，含首单；两者同时变只计 1 次）、末单完成时间、利用率、宕机小时。',
      '时间轴：按所选范围生成刻度，1 天视图为 2 小时一格。',
      '任务条：一块代表一个 Lot 的一道工序，颜色区分状态；点击可打开该 Lot 的详情抽屉。']),
    ('排产甘特', 'gantt_task_hover', '任务悬浮提示',
     '悬停任务条即可核对单个任务的排产依据。',
     ['任务提示框：Lot、产品、工序与 Recipe、占用机台、开始与结束时间、针卡类型。',
      '任务条：同机台相邻任务块之间的空白即换卡或等待窗口。']),
    ('排产甘特', 'gantt_stat_hover', '机台悬浮提示',
     '悬停左侧机台标签，查看该机台当天的工时构成。',
     ['机台提示框：换线（Recipe / 换卡）次数、其中换卡次数、末单完成、工作/空闲/宕机时长与利用率。',
      '机台标签列：机台号与机型，按机台组分行归并。']),
    ('排产甘特', 'gantt_bake', 'BAKE 腔体视图',
     '烤箱按腔体而不是按机台排产，因此切换资源后每行是一个腔体。',
     ['工具栏：资源切到「BAKE 腔体」，其余筛选与 CP 视图一致。',
      '图例：BAKE 批单独配色，PM 维护、借机、在制占用沿用统一颜色。',
      '左侧统计列：换线、末单完成、利用率、宕机，口径与 CP 视图相同；换线同样以红色标记标出。',
      '批次条：每腔每批最多 4 Lot，同 Recipe 可跨产品合批；条上标注合批的 Lot 与 Recipe，条后空白为降温与换 Lot 窗口。']),
    ('排产甘特', 'gantt_month', '全月视图',
     '从单日切到整月，检查月末收口与前后月衔接。',
     ['范围切换：点「全月」把时间轴拉到整个计划月。',
      '月度时间轴：以天为刻度，适合确认月末前是否排满、以及借机与 PM 是否挤占产能。']),
    ('排产甘特', 'gantt_drawer', 'Lot 详情抽屉',
     '抽屉严格按实际执行的工序顺序展示，而不是照抄原表路线。',
     ['Lot 详情抽屉：Lot 号、Platform、状态（按期）、PPN、片数、MPS 月结、路线工序数，以及 CP 完成与完工时间。',
      '实际工序时间线：逐道显示 CP / BAKE / QTIME 的起止时间；BAKE 段额外标注腔体释放时间，与 Lot 放行时间区分开。']),
    ('数据配置', 'inputs_top', '输入与配置',
     '上传与配置都只作用于副本，原始工作簿保持只读。',
     ['数据来源头：当前工作簿（Testing调度算例_新版.xlsx）、口径（2026-09 · 470 lot · 7 张表），以及 Excel 导入、导出 MPS、导出排产。',
      '生产配置：Excel 导入（.xlsx）、计划月份 2026年09月、BAKE 台数 7、每台 BAKE 腔体数 4。',
      '操作按钮：保存并排产 / 恢复参考配置；保存后自动重排，不修改原文件。',
      '配置详情区：Oracle 数据源（接口预留，尚未连接）、Sheet 设置、字段映射，以及 CP 机台（34 条）、PM 计划（11 条）、借机计划（119 条）等在制与计划明细，可逐行增删改。']),
    ('数据配置', 'inputs_preview', '数据预览与字段映射',
     '预览用于确认「读进来的到底是什么」，避免字段错位导致排产偏差。',
     ['数据预览区：展开后的工作表实时预览。',
      '工作表清单：7 张表及行数（MPS 72、EQP 34、针卡 11、测试flow&工时 306、借机计划 110、PM计划 11、约束条件 2）。',
      '字段映射：预览列名与内部字段的对应关系，按表分别生效。',
      '预览工具栏：表内搜索与当前显示行数（显示 50 / 72 行）。']),
    ('排产设置与诊断', 'settings', '排产设置',
     '设置弹窗只声明固定的业务前提，不提供可调策略——策略与 5 分钟换卡由后端强制。',
     ['排产设置弹窗：目标为各 PPN 月末 MPS 达成率均衡、换卡固定 5 分钟；BAKE 每腔最多 4 Lot，升温 2h、Recipe 加工 24h、降温 / 换 Lot 2h，共占腔 28h，Lot 在第 26h 放行；借机固定机台、指定日期内开始、允许跨日。',
      '底部操作：取消 / 应用并排产，应用后按当前参数重新排产。']),
    ('排产设置与诊断', 'diag', '排产诊断',
     '每次排产自动落一条诊断记录，只做列出的检查项，不等价于全量验收。',
     ['诊断页头：说明明细仅在内网查看、不提供导出，右侧为刷新记录。',
      '诊断摘要：版本与时间、运行编号、简短反馈，以及与上次运行的比较（输入及策略是否相同、完成 wafer 量增减、新增问题代码）。',
      '历史运行：最近若干次运行的触发方式、结果与异常代码，仅保留有限条数。']),
]


def set_east_asia(font_element, east=EAST):
    rpr = font_element.get_or_add_rPr()
    rfonts = rpr.find(qn('w:rFonts'))
    if rfonts is None:
        rfonts = OxmlElement('w:rFonts')
        rpr.append(rfonts)
    rfonts.set(qn('w:ascii'), 'Segoe UI')
    rfonts.set(qn('w:hAnsi'), 'Segoe UI')
    rfonts.set(qn('w:eastAsia'), east)


def style_font(style, size=None, bold=None, color=None):
    set_east_asia(style.element)
    if size is not None:
        style.font.size = Pt(size)
    if bold is not None:
        style.font.bold = bold
    if color is not None:
        style.font.color.rgb = color


def prep_image(src, dst_dir, name, width_px=1720):
    image = Image.open(src).convert('RGB')
    if image.width > width_px:
        image = image.resize(
            (width_px, round(image.height * width_px / image.width)), Image.LANCZOS)
    out = os.path.join(dst_dir, name + '.jpg')
    image.save(out, quality=90, optimize=True, progressive=True)
    return out


TBLPR_ORDER = [
    'tblStyle', 'tblpPr', 'tblOverlap', 'bidiVisual', 'tblStyleRowBandSize',
    'tblStyleColBandSize', 'tblW', 'jc', 'tblCellSpacing', 'tblInd',
    'tblBorders', 'shd', 'tblLayout', 'tblCellMar', 'tblLook',
    'tblCaption', 'tblDescription']


def _reorder_tblpr(tbl_pr):
    children = list(tbl_pr)
    children.sort(key=lambda el: TBLPR_ORDER.index(el.tag.split('}')[-1])
                  if el.tag.split('}')[-1] in TBLPR_ORDER else len(TBLPR_ORDER))
    for child in children:
        tbl_pr.remove(child)
    for child in children:
        tbl_pr.append(child)


def style_table(table, total_cm=17.0, color='D8DEE9'):
    """Fixed layout + explicit grid width + light borders, in schema order."""
    table.autofit = False
    tbl_pr = table._tbl.tblPr
    existing = tbl_pr.find(qn('w:tblW'))
    if existing is not None:
        tbl_pr.remove(existing)
    width = OxmlElement('w:tblW')
    width.set(qn('w:type'), 'dxa')
    width.set(qn('w:w'), str(int(round(total_cm / 2.54 * 1440))))
    tbl_pr.append(width)

    old = tbl_pr.find(qn('w:tblBorders'))
    if old is not None:
        tbl_pr.remove(old)
    borders = OxmlElement('w:tblBorders')
    for edge in ('top', 'left', 'bottom', 'right', 'insideH', 'insideV'):
        item = OxmlElement('w:' + edge)
        item.set(qn('w:val'), 'single')
        item.set(qn('w:sz'), '4')
        item.set(qn('w:space'), '0')
        item.set(qn('w:color'), color)
        borders.append(item)
    tbl_pr.append(borders)
    _reorder_tblpr(tbl_pr)


def add_bottom_border(paragraph, color='D8DEE9', size=6):
    ppr = paragraph._p.get_or_add_pPr()
    borders = OxmlElement('w:pBdr')
    bottom = OxmlElement('w:bottom')
    bottom.set(qn('w:val'), 'single')
    bottom.set(qn('w:sz'), str(size))
    bottom.set(qn('w:space'), '6')
    bottom.set(qn('w:color'), color)
    borders.append(bottom)
    ppr.append(borders)


def add_page_field(paragraph):
    run = paragraph.add_run()
    fld = OxmlElement('w:fldSimple')
    fld.set(qn('w:instr'), 'PAGE')
    inner = OxmlElement('w:r')
    text = OxmlElement('w:t')
    text.text = '1'
    inner.append(text)
    fld.append(inner)
    run._r.addnext(fld)


def build(shots, out_path):
    doc = Document()
    media_dir = tempfile.mkdtemp(prefix='ui-doc-')
    section = doc.sections[0]
    section.page_width, section.page_height = Cm(21), Cm(29.7)
    section.left_margin = section.right_margin = Cm(2.0)
    section.top_margin = Cm(2.0)
    section.bottom_margin = Cm(1.9)

    normal = doc.styles['Normal']
    style_font(normal, size=10.5, color=NAVY)
    normal.paragraph_format.space_after = Pt(4)
    normal.paragraph_format.line_spacing = 1.35

    for name, size in (('Heading 1', 19), ('Heading 2', 14), ('Title', 30)):
        st = doc.styles[name]
        style_font(st, size=size, bold=True, color=NAVY)
        st.font.italic = False
        st.paragraph_format.space_before = Pt(0)

    head = section.header.paragraphs[0]
    head.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    hr = head.add_run('XMC Testing 排产系统 · 界面说明')
    hr.font.size = Pt(8.5)
    hr.font.color.rgb = MUTED

    foot = section.footer.paragraphs[0]
    foot.alignment = WD_ALIGN_PARAGRAPH.CENTER
    fr = foot.add_run('— ')
    fr.font.size = Pt(8.5)
    fr.font.color.rgb = MUTED
    add_page_field(foot)
    fr2 = foot.add_run(' —')
    fr2.font.size = Pt(8.5)
    fr2.font.color.rgb = MUTED

    # --- cover -------------------------------------------------------------
    spacer = doc.add_paragraph()
    spacer.paragraph_format.space_before = Pt(96)

    kicker = doc.add_paragraph()
    kr = kicker.add_run('XMC TESTING  /  排产系统')
    kr.font.size = Pt(9.5)
    kr.font.bold = True
    kr.font.color.rgb = ACCENT

    title = doc.add_paragraph()
    title.paragraph_format.space_before = Pt(6)
    tr = title.add_run('界面说明与功能截图')
    tr.font.size = Pt(30)
    tr.font.bold = True
    tr.font.color.rgb = NAVY

    rule = doc.add_paragraph()
    rule.paragraph_format.space_before = Pt(10)
    rule.paragraph_format.space_after = Pt(10)
    add_bottom_border(rule)

    sub = doc.add_paragraph()
    sr = sub.add_run('MPS 达成 · 排产甘特 · 数据配置 · 排产诊断')
    sr.font.size = Pt(12)
    sr.font.color.rgb = MUTED

    meta = doc.add_paragraph()
    meta.paragraph_format.space_before = Pt(28)
    for label, value in (
            ('算例', 'Testing调度算例_新版.xlsx（2026-09 · 470 lot · 7 张表）'),
            ('截图', '1440 × 1000 视口，2× 像素密度，共 %d 张' % len(FIGURES)),
            ('日期', '2026-11-18')):
        line = doc.add_paragraph()
        line.paragraph_format.space_after = Pt(2)
        r1 = line.add_run(label + '   ')
        r1.font.size = Pt(9.5)
        r1.font.color.rgb = MUTED
        r2 = line.add_run(value)
        r2.font.size = Pt(9.5)

    # --- reading guide + contents -----------------------------------------
    h = doc.add_heading('阅读说明', level=1)
    h.paragraph_format.space_after = Pt(8)
    h.paragraph_format.page_break_before = True

    guide = doc.add_paragraph()
    gr = guide.add_run(
        '文中截图为同一套内置算例的实际运行结果，非生产数据。每张图上的红色虚线框与序号，'
        '与图下编号说明一一对应；同一页面按功能拆成多张图时会先隐藏无关区块，'
        '因此部分截图的版式与整页浏览略有差异。')
    gr.font.size = Pt(10)
    gr.font.color.rgb = MUTED
    guide.paragraph_format.space_after = Pt(14)

    table = doc.add_table(rows=1, cols=3)
    table.style = 'Table Grid'
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    style_table(table, total_cm=16.9)
    widths = (Cm(1.5), Cm(4.0), Cm(11.4))
    for column, width in zip(table.columns, widths):
        column.width = width
    for cell, text, width in zip(table.rows[0].cells, ('图', '页面', '内容'), widths):
        cell.width = width
        run = cell.paragraphs[0].add_run(text)
        run.font.size = Pt(9)
        run.font.bold = True
        run.font.color.rgb = NAVY

    for index, (group, _shot, title_text, _lead, _items) in enumerate(FIGURES, 1):
        row = table.add_row()
        for cell, text, width in zip(
                row.cells, (str(index), group, title_text), widths):
            cell.width = width
            p = cell.paragraphs[0]
            p.paragraph_format.space_after = Pt(0)
            run = p.add_run(text)
            run.font.size = Pt(9)
            if text == str(index):
                run.font.bold = True
                run.font.color.rgb = ACCENT

    # --- figures -----------------------------------------------------------
    current_group = None
    for index, (group, shot, title_text, lead, items) in enumerate(FIGURES, 1):
        new_group = group != current_group
        if new_group:
            gh = doc.add_heading(group, level=1)
            gh.paragraph_format.space_after = Pt(10)
            gh.paragraph_format.page_break_before = True
            current_group = group

        heading = doc.add_paragraph()
        if not new_group:
            heading.paragraph_format.page_break_before = True
        heading.paragraph_format.space_before = Pt(2)
        heading.paragraph_format.space_after = Pt(2)
        hr1 = heading.add_run('图 %d  ' % index)
        hr1.font.size = Pt(14)
        hr1.font.bold = True
        hr1.font.color.rgb = ACCENT
        hr2 = heading.add_run(title_text)
        hr2.font.size = Pt(14)
        hr2.font.bold = True
        hr2.font.color.rgb = NAVY

        lead_p = doc.add_paragraph()
        lead_p.paragraph_format.space_after = Pt(9)
        lr = lead_p.add_run(lead)
        lr.font.size = Pt(10)
        lr.font.color.rgb = MUTED

        src = os.path.join(shots, shot + '.png')
        if not os.path.exists(src):
            raise SystemExit('missing screenshot: ' + src)
        pic_p = doc.add_paragraph()
        pic_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        pic_p.paragraph_format.space_after = Pt(10)
        pic_p.add_run().add_picture(prep_image(src, media_dir, shot),
                                    width=Cm(16.6))

        for n, text in enumerate(items, 1):
            item = doc.add_paragraph()
            item.paragraph_format.left_indent = Cm(0.72)
            item.paragraph_format.first_line_indent = Cm(-0.72)
            item.paragraph_format.space_after = Pt(3)
            item.paragraph_format.line_spacing = 1.3
            num = item.add_run('%d.' % n)
            num.font.size = Pt(9.5)
            num.font.bold = True
            num.font.color.rgb = ACCENT
            body = item.add_run('  ' + text)
            body.font.size = Pt(9.5)
            body.font.color.rgb = NAVY

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    doc.save(out_path)
    return out_path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--shots', default='/tmp/shots')
    parser.add_argument('--out', default='docs/XMC_Testing_界面说明.docx')
    args = parser.parse_args()
    path = build(args.shots, args.out)
    print('wrote', os.path.abspath(path), os.path.getsize(path) // 1024, 'KB')


if __name__ == '__main__':
    main()