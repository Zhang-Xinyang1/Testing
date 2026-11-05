from pathlib import Path

from docx import Document
from docx.shared import Cm, Pt, RGBColor
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT, WD_CELL_VERTICAL_ALIGNMENT

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'docs' / '排产数据字段含义与关联说明.docx'
d = Document()
s = d.sections[0]
s.page_height, s.page_width = Cm(29.7), Cm(21)
s.top_margin, s.bottom_margin = Cm(1.65), Cm(1.6)
s.left_margin, s.right_margin = Cm(1.8), Cm(1.8)
s.header_distance, s.footer_distance = Cm(.7), Cm(.7)
for name in ['Normal', 'Title', 'Heading 1', 'Heading 2']:
    st = d.styles[name]
    st.font.name = 'Calibri'
    st._element.rPr.rFonts.set(qn('w:eastAsia'), 'Songti SC')
    st.font.color.rgb = RGBColor(0, 0, 0)
    st.paragraph_format.space_after = Pt(4)
d.styles['Normal'].font.size = Pt(10)
d.styles['Normal'].paragraph_format.line_spacing = 1.08
d.styles['Title'].font.size = Pt(23)
d.styles['Heading 1'].font.size = Pt(17)
d.styles['Heading 2'].font.size = Pt(12)
footer = s.footer.paragraphs[0]
footer.alignment = WD_ALIGN_PARAGRAPH.RIGHT
r = footer.add_run()
field = OxmlElement('w:fldSimple'); field.set(qn('w:instr'), 'PAGE'); r._r.addnext(field)

def p(t, style=None):
    return d.add_paragraph(t, style)

def h(t):
    d.add_heading(t, 2)

def page(t):
    d.add_page_break()
    d.add_heading(t, 1)

def table(headers, rows, widths):
    t = d.add_table(rows=1, cols=len(headers))
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    t.autofit = False
    for c, w in zip(t.columns, widths): c.width = Cm(w)
    for c, text in zip(t.rows[0].cells, headers): c.text = text
    for row in rows:
        for c, text in zip(t.add_row().cells, row): c.text = text
    for ri, row in enumerate(t.rows):
        pr = row._tr.get_or_add_trPr()
        pr.append(OxmlElement('w:cantSplit'))
        if ri == 0: pr.append(OxmlElement('w:tblHeader'))
        for ci, c in enumerate(row.cells):
            c.width = Cm(widths[ci]); c.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            cp = c._tc.get_or_add_tcPr()
            borders = OxmlElement('w:tcBorders')
            for edge in ['top', 'left', 'bottom', 'right']:
                e = OxmlElement('w:'+edge); e.set(qn('w:val'), 'single'); e.set(qn('w:sz'), '4'); e.set(qn('w:color'), 'D9D9D9'); borders.append(e)
            cp.append(borders)
            mar = OxmlElement('w:tcMar')
            for side in ['top', 'left', 'bottom', 'right']:
                e = OxmlElement('w:'+side); e.set(qn('w:w'), '80'); e.set(qn('w:type'), 'dxa'); mar.append(e)
            cp.append(mar)
            sh = OxmlElement('w:shd'); sh.set(qn('w:fill'), '24445C' if ri == 0 else ('F1F5F7' if ri % 2 else 'FFFFFF')); cp.append(sh)
            for para in c.paragraphs:
                para.paragraph_format.space_after = Pt(1)
                para.paragraph_format.line_spacing = 1.08
                for run in para.runs:
                    run.font.size = Pt(9)
                    if ri == 0: run.bold = True; run.font.color.rgb = RGBColor(255,255,255)
    spacer = p('')
    spacer.paragraph_format.space_after = Pt(2)
    spacer.paragraph_format.line_spacing = Pt(2)
    spacer.paragraph_format.space_before = Pt(0)
    return t

d.add_heading('排产数据字段含义与关联说明', 0)
p('Testing区域  字段理解与排产输入对照  2026年11月5日')
p('本文依次说明各表字段的业务含义、表间字段如何关联，以及排产需要的信息分别从哪些字段取得。核心关系是：MPS给出各产品的lot需求，Lot明细标识具体lot，工艺表给出各产品的flow，设备、针卡和日历信息共同限定每道任务的可执行条件。')
p('业务依据为《场景梳理.docx》（2026年11月2日版本）。“本版”均指该文档的开发算例；已确认业务规则优先于算例假设。“建议补充”仅用于现场接入或后续增强，不改变本版规则。字段说明、跨表核对建议与现场扩展信息分别列示。')
d.add_heading('一 各表字段的含义理解', 1)
h('1 MPS表 月度产品需求')
table(['字段', '含义理解', '用于排产时的理解'], [
('Platform', '产品所属平台或产品大类。', '通过平台与机台类型映射，确定可使用的CP机台类型。'),
('PPN', '月计划中的产品编号，表示产品种类。', '关联工艺表PROD；同一产品的各lot共用该产品路线。'),
('MPS', '该产品当月需要完成的lot数量，单位为lot。', '作为逐lot展开数量及月度完成量的依据；不是晶圆片数。')], [3,5,9.4])
h('2 EQP表 CP机台及初始状态')
table(['字段', '含义理解', '用于排产时的理解'], [
('TYPE', 'CP机台类型。', '与Platform映射后判断机台是否适合加工该产品。'),
('EQPID', '具体机台的设备编号。', '关联该机台的PM、借机计划及禁用Recipe记录。'),
('Current ProbeCard', '排程开始时机台上安装的物理针卡。', '用于初始化安装位置及判断是否需要换卡。'),
('Current Recipe', '机台初始状态所记录的Recipe。', '不应当作后续所有任务的Recipe过滤条件。'),
('status', '初始状态，允许RUN、LOST或空白。', '本版全部空白并按空闲处理；RUN缺剩余工时，LOST含义待确认。')], [3.6,5,8.8])

page('一 各表字段的含义理解 续')
h('3 针卡表 CP工艺与可用针卡')
table(['字段', '含义理解', '用于排产时的理解'], [
('Recipe', 'CP工艺配方编号。', '与当前CP工序的RECIPE匹配。'),
('ProbeCard List', '该Recipe可使用的物理针卡编号集合，以英文分号分隔。', '拆分为候选卡清单；每个编号按完整字符串保存，代表一张物理卡。')], [3.6,6,7.8])
p('同一个Recipe可对应多张针卡。表中记录数不等于物理针卡数：源文档为69条Recipe映射、40张物理针卡。判断可用性需要结合针卡类型、安装位置和占用情况；针卡类型在算例参数中补充。')
h('4 测试flow&工时表 产品工艺路线')
table(['字段', '含义理解', '用于排产时的理解'], [
('PROD', '工艺表中的产品编号。', '与MPS.PPN对应；一个PROD通常有多条工序记录。'),
('STEP', '工序名称，本版为BAKE、CP1至CP4、QTIME1。', '区分CP加工、BAKE加工和等待；影响资源选择及TIME解释。'),
('RECIPE', '当前工序的工艺配方。', 'CP用于找针卡；BAKE用于识别温度与加工时长条件。'),
('TIME', '该工序的加工或等待时长。', 'CP为分钟/lot，BAKE为小时/批，QTIME1为等待分钟；计算前统一单位。')], [3,5.8,8.6])
h('如何理解flow')
p('flow不是某一个单独字段，而是同一PROD下按顺序排列的多条STEP、RECIPE、TIME记录。源文档规定同一PROD的行顺序就是本算例的路线顺序；本版具体路线为2至4道CP，随后一次BAKE和一次QTIME1，属于算例假设。')
p('因此，既不能仅凭STEP名字推断所有产品路线，也不能在导入后随意重排工艺表。本版保留同一PROD的原始行顺序即可展开；独立工序序号是增强建议，不是本版运行前提。INK、VM及CP与BAKE反复交替的旧描述不作为本版依据。')
h('工时示例')
p('BAKE的RECIPE为210C24H时，源文档解释为210摄氏度、持续24小时，对应TIME=24，计算时转换为1440分钟。BAKE时长属于整批加工时长，不应再乘以该批lot数。该示例不等于完整的Recipe命名规范，解析格式与一致性校验仍需补充。')

page('一 各表字段的含义理解 续')
h('5 借机计划表 设备借用需求')
table(['字段', '含义理解', '用于排产时的理解'], [
('DATE', '借机请求产生的日期。', '具体产生时刻由算例参数按Excel行号补齐；不是指定借机开始时间。'),
('EQPID', '申请借用的具体设备编号。', '关联设备，借机期间占用该设备的可用时间。'),
('借机时数', '该请求需要连续借用的时长，单位小时。', '统一为分钟后排入设备日历，不能拆段执行。')], [3,6.1,8.3])
p('本版请求在DATE当天08:00至15:00产生，实际开始不受白天限制。生产冲突时允许延期或取消；开始最多延期48小时、全月取消请求数不超过10%为待确认假设。借机须连续执行，允许跨日，月末可延续至10月。')
h('6 PM计划表 维护时间窗口')
table(['字段', '含义理解', '用于排产时的理解'], [
('EQPID', '需要维护的设备编号。', 'CP对应具体机台；BAKE对应整台设备及其四个腔体。'),
('Early Due', '最早允许开始维护的日期。', '本版从该日00:00起允许开始。'),
('Due Date', '期望维护日期。', '本版作为期望开始日期，不是唯一可安排日期。'),
('Over Due', '最迟允许完成维护的日期。', '本版要求在该日结束前完成。'),
('Estimated Hours', '预计连续维护时长，单位小时。', '维护区间必须完整落在允许窗口内，工时统一转换为分钟。')], [3.6,5.5,8.3])
p('同一BAKE设备的四腔须在同一区间停机维护；CP联合维护分组未给出，本版不启用。维护窗口为硬约束，跨月窗口原样保留，例如9月25日至10月5日，不裁剪至9月底。BAKE设备与腔体关系由参数补充。')
h('7 约束条件表 机台禁用工艺')
table(['字段', '含义理解', '用于排产时的理解'], [
('TYPE', '约束记录所属的机台类型。', '用于理解或核对设备类型；与EQPID组合的具体匹配口径仍需确认。'),
('EQPID', '受约束的具体机台。', '定位不能执行相应Recipe的设备。'),
('Constraint Recipe', '该机台不能执行的Recipe。', '本版暂不启用；启用后应在候选机台中排除对应组合。')], [3.6,5.5,8.3])

page('一 各表字段的含义理解 续')
h('8 算例参数表 补充规则和资源关系')
p('源文档没有列出该表的具体列名，不能把以下内容当作已存在的字段定义。它说明该表承载日历、时间单位、Platform与TYPE映射、针卡类型、BAKE设备与腔体、借机产生时刻，以及换卡时间、借机阈值等参数。实际读取方式需依据Excel列结构确定。')
h('9 Lot明细表 具体工件及其属性')
table(['字段', '含义理解', '用于排产时的理解'], [
('LotID', '具体lot的标识。', '以lot为调度基本单位，为每个lot展开工序任务。'),
('PROD', '该lot所属的产品编号。', '关联工艺表PROD，并对应MPS.PPN。'),
('Platform', '该lot所属产品的平台。', '用于机台类型匹配；应与MPS中的产品归属一致。'),
('Wafer Qty', '该lot包含的晶圆片数。', '本版取10、12、15、20、25；单lot不超过25片，组批合计不超过50片。'),
('Release Time', '该lot最早可投入的时间。', '第一道任务不得早于该时刻；本版全部在月初可投入。'),
('Target Week', '该lot的目标周标记。', '表示目标归属；是否形成逐lot约束需另行明确。'),
('Weekly Due', '该lot的周到期时间。', '可用于周交付表达；源文档主要按产品累计周目标考核。'),
('Monthly Due', '该lot的月到期时间。', '用于月度到期校验，边界应与计划月日历一致。'),
('Priority', '该lot的优先级。', '本版统一为1；产能不足诊断时可用于区分保护顺序。')], [3.6,5.3,8.5])
h('10 周目标表 产品周交付目标')
table(['字段', '含义理解'], [
('Platform / PPN', '目标所属的平台及产品；PPN对应MPS.PPN和Lot明细.PROD。'),
('Week', '周别标识，本版为W1至W5。'),
('Week Start / Week End', '该周在计划月内的覆盖起止日期。'),
('Target Lots', '该产品该周的目标完成lot数；五周合计应等于MPS。'),
('Cumulative Target Lots', '截至该周的累计目标lot数，源文档说明其为Excel公式。')], [5.3,12.1])

page('二 各表之间字段的联系')
p('理解这些表时，应先以产品找到路线和设备类型，再以具体lot展开任务，最后检查机台、针卡、维护及借机时间是否允许执行。下面的关系同时包含直接编号匹配和参数映射。')
table(['关联对象', '字段联系', '关联后的作用'], [
('产品需求与工艺', 'MPS.PPN = 测试flow&工时.PROD', '一个产品需求对应多条有序工序；不能直接把每条工序当作新增lot。'),
('产品需求与具体lot', 'MPS.PPN = Lot明细.PROD', '同一产品的LotID数量应等于MPS；LotID不得重复。'),
('lot与工艺', 'Lot明细.PROD = 测试flow&工时.PROD', '每个lot复制所属产品的完整路线，形成逐lot工序任务。'),
('平台与CP机台', 'MPS.Platform或Lot明细.Platform → 参数映射 → EQP.TYPE', '本版K1→E1至K5→E5；一个平台包含多个产品，同一类型可有多台机台。'),
('CP工序与针卡', '工艺表.RECIPE = 针卡.Recipe，仅用于CP工序', '拆分ProbeCard List得到候选物理卡，并结合针卡类型检查兼容性。'),
('初始安装关系', 'EQP.Current ProbeCard对应物理针卡编号；Current Recipe描述初始Recipe', '初始化安装位置；新任务仍用自身RECIPE查候选卡。'),
('设备与维护', 'PM计划.EQPID对应设备编号', 'CP匹配EQP.EQPID；BAKE通过参数中的设备和腔体关系安排整机停机。'),
('设备与借机', '借机计划.EQPID = EQP.EQPID', '将连续借机时段加入对应CP机台的占用日历；请求产生时刻由参数补充。'),
('设备与禁用Recipe', '约束条件.EQPID对应机台；Constraint Recipe匹配工序RECIPE', '启用时排除禁用组合；本版未启用，TYPE等匹配细节待确认。'),
('月目标与周目标', '周目标.PPN = MPS.PPN；各周Target Lots合计 = MPS', '按同一产品核对需求，比较累计完成与累计周目标。')], [3.1,6.7,7.6])
h('跨表一致性要求')
p('同一PROD在MPS、Lot明细和周目标中的Platform归属应一致。若发现不一致，应作为输入问题报告，不能随意选择一张表覆盖另一张。BAKE和QTIME1的RECIPE不应被强制要求出现在CP针卡表中。')
p('源文档约定每条针卡编号是一张物理卡，因此多个Recipe即使都允许使用同一张卡，也共享该卡的占用状态，不能因出现于不同列表而被当作多张资源。')

page('三 排产所需信息与字段对应')
h('1 工件 数量 种类与flow')
p('本场景的调度基本单位是lot。晶圆片数是lot的装载属性；CP工序任务是lot经过路线展开后的执行单元；BAKE批次则是若干满足条件的lot在该道工序上的临时组合。三者不能混为同一个数量。')
table(['排产需要的信息', '对应表和字段', '取得或形成方式'], [
('工件标识', 'Lot明细.LotID', '每个lot一个标识。MPS只有汇总需求，不能单独识别具体工件。'),
('工件数量或lot数', 'MPS.MPS；Lot明细.LotID', 'MPS给出各产品需求lot数；展开后逐产品核对LotID数量。'),
('产品种类或型号', 'MPS.PPN；Lot明细.PROD；工艺表.PROD', '三者表示关联的产品编号；不同产品数按编号去重计数。'),
('产品大类或平台', 'MPS.Platform；Lot明细.Platform', '用于产品分组及映射CP机台类型，不能替代具体产品编号。'),
('晶圆片数', 'Lot明细.Wafer Qty', '逐lot读取；BAKE组批时求和，不能用lot数代替。'),
('工艺路线flow', '工艺表.PROD + 有序的STEP、RECIPE、TIME', '取同一PROD的所有工序，按源表行顺序组成路线。'),
('工序类型', '工艺表.STEP', 'CP选择机台和针卡；BAKE组批进腔体；QTIME1执行等待。'),
('工序配方', '工艺表.RECIPE', 'CP匹配候选针卡；BAKE识别温度及加工时长条件。'),
('加工或等待时长', '工艺表.TIME + STEP', '按工序转换单位，建议在计算中统一使用分钟。'),
('工序先后约束', '同一PROD在工艺表中的行顺序', 'lot的下一工序不得早于上一工序结束；建议补独立工序序号。'),
('工件释放时间', 'Lot明细.Release Time', 'lot首道工序最早可开始时间；后续工序还受前序完成限制。'),
('工件优先级', 'Lot明细.Priority', '本版均为1；真实优先产品和优先级方向需正式定义。')], [3.4,6.2,7.8])
h('数量统计的口径')
p('源文档中的35个PPN表示35种产品，5类Platform表示5个平台，11575 lot表示全部月需求工件数。174条工艺记录表示产品路线中的工序记录数；按各产品lot数展开后为33975道CP任务。工艺表行数、产品种数、lot数和展开任务数各有不同含义。')

page('三 排产所需信息与字段对应 续')
h('2 资源 时间限制与交付目标')
table(['排产需要的信息', '对应表和字段或参数', '使用方式及口径'], [
('可选CP机台', '产品Platform → 参数映射 → EQP.TYPE、EQPID', '先筛机台类型，再从具体机台中选可用设备。'),
('可选针卡', '工序RECIPE → 针卡.Recipe、ProbeCard List；参数中的针卡类型', '按当前任务选卡，校验与机台同TYPE且该时段可用。'),
('初始针卡位置', 'EQP.Current ProbeCard', '初始化安装状态；空闲卡移机时清空原机台安装状态。'),
('换卡时间', '算例参数中的换卡设定', '本版5分钟，期间占用目标机台和针卡；安装人员不设容量上限。'),
('BAKE可用腔体', '算例参数中的BAKE设备与腔体清单', '7台设备、每台4腔，共28个并行资源；不从CP机台表推导。'),
('BAKE组批条件', 'BAKE的RECIPE、TIME；Lot明细.Wafer Qty；容量参数', '同温同时间，每批≤4 lot且≤50片；本版允许跨产品混批及非满载。'),
('维护占用', 'PM计划全部字段；BAKE设备与腔体关系', '安排连续维护区间并检查窗口；BAKE四腔同时停机。'),
('借机占用', '借机计划.DATE、EQPID、借机时数；请求产生时刻及阈值参数', '不早于产生时刻，连续执行；延期与取消按本版阈值核验。'),
('机台禁用工艺', '约束条件.TYPE、EQPID、Constraint Recipe', '本版不启用；后续启用前确认完整匹配规则。'),
('运行日历和初始状态', '算例参数；EQP.status；Lot明细.Release Time', '本版24小时运行、月初空闲、所有lot月初可投入；无真实在制品快照。'),
('月度完成要求', 'MPS.PPN、MPS；Lot明细.Monthly Due', '各PPN月度MPS全部完成为硬约束；不可行时另做缺口诊断。'),
('周度交付目标', '周目标.PPN、Week、Week End、Target Lots、Cumulative Target Lots', '按产品统计截至周末累计完成量；本版为软目标，允许提前完成抵扣后续。'),
('lot周目标信息', 'Lot明细.Target Week、Weekly Due', '辅助表达逐lot目标；不能据此自行增加逐lot周交付硬约束。')], [3.3,7.1,7])
h('完成时间的统计')
p('本版产品路线包含末尾QTIME1，必须等待结束后才计为该lot路线完成。周、月完成量均应使用路线完成时间统计，不能在最后一道CP或BAKE结束时提前计入。QTIME1本版不占CP或BAKE资源。')

page('三 排产所需信息与字段对应 续')
h('3 字段落到排程时应遵守的规则')
p('以下规则对应《场景梳理》第四至第八节，说明字段如何共同约束排程。现场新增信息仅用于替换假设或支持续排，不应自行改变这些规则。')
h('月度要求与缺口诊断')
p('先要求每个PPN的月度MPS全部完成。若产能不足，应明确报告原问题不可行，再单独进入缺口诊断：先保护特殊高优先级产品，其后最大化完成lot数量。本版未指定特殊产品，Priority均为1。不能直接把未完成月需求的排程当作满足原问题的结果。')
h('周目标与逐lot到期信息')
p('本版按2026年9月各自然周在月内的覆盖天数拆分MPS，采用最大余数法取整，逐产品五周合计等于月需求。周目标为软目标，按累计目标与累计完成量的缺口衡量，允许提前完成抵扣后续。Target Week和Weekly Due不能自行升级为逐lot周交付硬约束。')
h('针卡占用与安装位置')
p('当前CP任务按自身RECIPE选卡，机台和针卡同时可用且TYPE兼容。加工结束释放加工占用，并不等于抹去安装位置。空闲机台上的空闲针卡可移至同TYPE机台，同时清空原机台安装状态；换卡5分钟占用目标机台和针卡，安装人员不设容量上限。后两项为本版假设。')
h('BAKE容量与时间损耗')
p('7台BAKE设备的28个腔体独立组批，每腔同时执行一批。同温度、同时长、每批不超过4 lot且不超过50片，四项须同时成立。本版允许跨PROD混批和非满载运行；升降温、温度切换、换盘时间忽略，转运时间不另扣。不得擅自增加这些损耗或放宽容量限制。')
h('PM与借机的时间边界')
p('PM须在Early Due与Over Due限定的硬窗口内连续完成；Due Date仅为期望开始日期。跨月PM窗口原样保留，BAKE同设备四腔同时停机。借机产生时刻限制最早开始时间，08:00至15:00不是执行时段；借机连续且可跨日跨月。48小时延期和10%取消阈值仍属待确认假设。')
h('版本调整与规则启用')
p('本版约束条件表暂不启用，也不添加尚未落实的CP联合维护规则。修改MPS或工艺后，应同步调整Lot明细与周目标并重新核对；Excel不承担自动重建lot或重新排程。只有周目标累计列为公式，其余为固定版本的合成输入。')

page('四 从原始字段形成可排任务')
h('1 一个lot的字段匹配示例')
p('以下编号及CP工时仅用于解释匹配过程，不代表真实产品或新增业务设定。假设某产品PPN为P_A、Platform为K1、MPS为2；其一个lot为L_A01，PROD为P_A，Wafer Qty为20。')
table(['步骤', '读取的信息', '形成的排产理解'], [
('确认需求与工件', 'MPS中的P_A需求2 lot；Lot明细提供两个不同LotID', 'L_A01只是其中一个具体工件，不能把MPS=2理解为2片晶圆。'),
('取得路线', '工艺表中PROD=P_A的有序记录', '例如CP1 → CP2 → BAKE → QTIME1；为L_A01逐道展开任务。'),
('匹配CP资源', 'K1映射E1；CP1的RECIPE为R_A；针卡表R_A列出卡C01、C02', '只在E1类机台中选择，再检查候选针卡的类型、位置和可用时间。'),
('安排CP时长', '假设CP1.TIME为30', '占用所选机台和针卡30分钟；需要换卡时另计本版5分钟安装。'),
('安排BAKE', '假设RECIPE为210C24H且TIME为24；该lot为20片', '可与同温同时间的其他lot组批；仍需同时满足4 lot和50片上限。'),
('统计交付', 'QTIME1.TIME；周目标及Monthly Due', 'BAKE结束后还须完成等待，再按路线结束时间统计周、月完成量。')], [3.1,6.7,7.6])
h('2 排程过程中需要形成的动态信息')
p('原始输入提供需求、路线及资源条件；实际排程还需要生成“某lot的某道工序选择哪台机台或哪个腔体、使用哪张针卡、开始与结束时间、属于哪个BAKE批次”等任务信息，并持续维护设备和针卡的占用状态。这些是排程计算结果或内部状态，不应误认为七张业务表已经提供的输入字段。')
h('3 输入核对')
p('数据调整后应重新核对：各产品LotID数量是否等于MPS、flow是否完整、CP Recipe是否都有候选针卡、Platform与TYPE是否可映射、BAKE片数是否齐全、维护及借机是否能定位设备，以及各产品周目标合计是否等于月需求。')

page('五 实际排产仍需补充的信息')
p('本版月初全空闲、全部需求lot可投入，可按现有假设开发。接入现场续排时，需补充状态快照。下列英文名均为建议命名，可用等价字段或关联表表达；其中RUN的运行lot及剩余时长是源文档明确指出的缺口，其余为现场接入建议，不能视为已核实的Excel缺列。')
h('1 现场续排所需的状态信息')
table(['建议字段或记录', '字段应包含的信息', '需要它的原因或条件'], [
('Snapshot Time 状态时点', '机台、针卡和在制lot状态对应的同一采集时间。', '现场续排必需，明确“当前状态”和剩余时长从哪一刻开始计算。'),
('Lot State 工件状态', '逐LotID记录未投、待加工、加工中、等待、暂停或完成等状态。', '续排必需，避免已完成工件重复投产；枚举和可排规则需定义。'),
('Current Operation 当前工序', '逐lot记录当前或下一待执行工序；可用原表顺序索引，或新增OpSeq定位。', '续排时区分已完成与剩余路线；须明确当前加工中、待加工两种状态的口径。'),
('Current LotID 当前加工lot', 'RUN机台当前加工的LotID，并与当前工序关联。', '机台运行时必需，仅有status=RUN无法确定在做什么。'),
('Remaining Minutes 剩余时长', '状态时点之后加工或QTIME还需的分钟数；也可提供可推导该值的起止时间。', '已有任务或等待未结束时必需，防止设备提前释放或QTIME被重新计满。'),
('Current Batch 当前BAKE批次', '批次ID、所属设备与腔体、批内LotID、Recipe及剩余时长。', 'BAKE已有在制批次时必需；CP的EQP状态不能覆盖28个腔体。'),
('ProbeCard State 针卡状态', '逐物理卡记录可用或占用状态、安装机台或未安装位置、预计释放时间。', '现场针卡占用或不可用时需要；Current ProbeCard已有安装信息，应同步更新并覆盖未安装卡。'),
('Available From 恢复可用时间', '明确受限对象、状态原因及预计恢复时刻；无法预计时标记未知。', '有停机或lot暂停时需要；LOST须先定义，不能直接等同于故障。')], [4.1,6.6,6.7])

page('五 实际排产仍需补充的信息 续')
h('2 按现场情况选用的增强字段')
table(['建议字段或记录', '字段应包含的信息', '何时需要及与本版的关系'], [
('OpSeq 工序序号', '每个PROD下稳定的工序顺序号；任务可用LotID与OpSeq定位。', '导入、排序或重复STEP时建议使用；本版依原行顺序即可，不是必补项。'),
('Request ID 借机请求编号', '每条借机请求的稳定唯一编号；产生时刻与该编号关联。', '实际滚动更新建议补充，避免依赖Excel行号后发生请求错配。'),
('Flow Version 工艺版本', '路线版本、适用产品、有效起止时间，必要时指定lot使用版本。', '同产品存在多版本或工艺变更时需要；固定单版本算例可不加。')], [4.1,6.6,6.7])
h('3 已有字段和参数需要替换为真实值')
p('需求与工艺：MPS.PPN、Platform、MPS已有产品归属及月需求字段；测试flow&工时已有PROD、STEP、RECIPE、TIME。应提供真实产品、路线顺序、配方及标准工时，不把它们重复列为新增字段。')
p('工件属性与交付：Lot明细已有LotID、PROD、Platform、Wafer Qty、Release Time、Target Week、Weekly Due、Monthly Due、Priority；周目标已有产品、周别、日期、目标及累计目标字段。需填真实lot片数、投入时刻、交付计划和优先级，并确认优先级方向与周目标口径。')
p('资源与日历：EQP已有设备类型、编号、状态及初始安装信息；针卡表已有Recipe与候选卡。平台到机台、针卡类型、BAKE设备与腔体、日历及换卡规则由参数承载。需核对真实资源清单、兼容关系、可用时间及换卡时长；本版已有7台BAKE和28腔的资源描述。')
p('维护与借机：PM已有设备、窗口日期及预计工时；借机已有日期、设备及时长，具体产生时刻已由参数按Excel行号提供。应更新真实计划，确认PM日期边界及借机延期、取消阈值，不把产生时刻误列为本版完全缺失的信息。')
h('4 需业务确认的规则与条件性补充')
p('应确认LOST的真实含义、正式flow顺序、特殊产品及优先级、真实周交付计划，以及借机48小时延期和10%取消是否适用。原文未规定的规则不能因增加字段而默认启用。')
p('若现场存在非24小时运行、复杂换型、安装人员容量限制、禁止跨产品混批或CP联合维护，需分别提供资源可用时段、换型时间规则、人员数量与班次、混批许可范围或维护分组。若计入升降温、换盘及转运，另提供对应时长与适用条件。这些均为条件性补充，本版仍沿用原文设定。')

for st in d.styles:
    for border in st._element.xpath('.//w:pBdr'):
        border.getparent().remove(border)
for para in list(d.paragraphs) + [p for t in d.tables for row in t.rows for c in row.cells for p in c.paragraphs]:
    for run in para.runs:
        rf = run._r.get_or_add_rPr().get_or_add_rFonts()
        rf.set(qn('w:eastAsia'), 'Songti SC')
d.save(OUT)
print(OUT)
