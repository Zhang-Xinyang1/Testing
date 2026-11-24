# MES 生产快照业务字段 v1

更新于 2026-11-24。Excel、MES、演示使用同一套试排和选用流程，各来源分别保留快照与选用结果。MES 可以先提供完整生产快照，再按稳定 UID 更新 WIP。分页采集、增量推送、设备释放和外部完工的字段见 [WIP 接入说明](WIP现场接入.md)。

## 操作与接口

启动后打开 `/#production`，选择内置场景并点击“载入所选场景”，进入“滚动重排 → 开始试排”。完成后查看产品差额、Lot 变化和借机 Recipe，再确认说明并选用。演示说明已预填，现场说明需人工填写。刷新页面及定时检查都不会启动排产。

实际数据在“滚动重排 → 生产规则与 MES → MES 数据接入 → 导入或粘贴完整快照”提交。空工作区也可直接接收。演示数据全部合成。先用页面顶部来源按钮切换。切换来源恢复该来源已有状态，该来源更换计划月份才清空当前选用状态，历史记录保留。

| 方法与路径 | 用途 |
|---|---|
| `GET /api/production` | 快照、规则、已选方案 active、甘特 chart（planId 与 active.id 一致）、历史、变化与检查状态；首次空工作区读取已保存 Excel，不计算 |
| `POST /api/production/source` | `{"source":"excel"}` 或 mes / demo，切换并恢复该来源状态；计算中拒绝切换 |
| `POST /api/production/excel` | 接收已保存 Excel；可带 asOf、defaultAvailableAt、defaultShipDue、confirmPlannedLots、identityStable 补充信息，不自动试排 |
| `POST /api/production/snapshot` | 接收完整快照；不接受零散增量，不自动试排 |
| `GET /api/production/demo-catalog` | 10 个场景及 6 种变化的目录，不生成计划 |
| `POST /api/production/demo` | 推荐 `{"case":"basic"}`，载入 12 Lot 客户主线数据与该场景规则；省略 case 保持兼容载入 basic。清空当前选用、保留历史；计算中拒绝切换 |
| `POST /api/production/demo-event` | 如 `{"event":"arrival_known"}`，自动生成演示变化；只改演示快照，不自动试排 |
| `POST /api/production/demo-check` | `{}`，检查 20 个内置输入样例，只读、不排产，返回逐项预期与实际结果 |
| `POST /api/production/rules` | 保存生产规则；规则对象见下文 |
| `POST /api/production/trial` | 现场 `{"scenario":"actual","mode":"live"}`；历史模拟 `{"scenario":"actual","mode":"shadow"}`；扩产另设 `"scenario":"expansion","extraMachines":{"K8000L":2}` |
| `GET /api/production/job` | 当前试排状态、已比较候选数、耗时、当前最好方案 |
| `POST /api/production/cancel` | `{"id":"任务编号"}`，停止计算，不更改已选方案 |
| `POST /api/production/adopt` | `{"id":"任务编号","reason":"选用说明"}`，只选用已完成或停止后留有可行结果的实际资源候选 |
| `POST /api/production/archive` | `{"id":"任务编号"}`，保存候选比较记录，允许扩产场景 |
| `GET /api/production/plans/{id}` | 打开已保存的完整生产方案，只读 |
| `POST /api/production/event` | 仅演示：模拟来料、交期或停机变化，现场事实通过快照提供 |

试排请求响应含 `job.id`。方案保存返回的 `id` 是方案编号，与任务编号不同。更改输入、规则或选用另一方案后，旧候选不能再选用。每个服务实例同时只运行一个生产试排；单独进程执行，可取消，最多 1800 秒。超时保留已收到的最好可行候选；如果没有找到可行候选，明确提示失败，不制造结果。当前试排任务本身不跨服务重启保存，需要“保存比较记录”或“选用此方案”持久化。

服务器每 30 秒检查一次是否达到配置时刻，每日执行一次数据变化检查。它检查的是**本服务已接收的快照**，不会自行向 MES 拉数据，不自动试排或自动选用。

### 影子试排与现场计划

- `shadow` 按历史 `asOf` 模拟；可选用到展示页面，始终标注“仅用于比较和展示，不用于现场执行”，不能替换已选用的现场计划。
- `live` 在试排和选用时都检查服务器当前时间、数据年龄、在制和执行中 PM 的预计释放。默认起点为当前时间 + 35 分钟；数据年龄上限默认 60 分钟，两者可配置。已过去的待开工任务不能整体平移后直接选用。
- 候选返回 `execution.start`、`requestedAt`、`validUntil`（毫秒时间戳）。有效期不晚于可执行起点、数据到期和最早任务/PM 开始。过期后需更新事实并重新试排。
- API 未传 mode 时，MES / Excel 默认 live、demo 默认 shadow。页面初始默认影子演示，切换至 MES 来源时默认现场计划；必须按页面显示的用途确认。
- 输入、规则或选用基线变化使候选失效。事务版本冲突返回 HTTP 409；同一已选方案重复提交返回原结果，不改写原选用原因。选用只保存到本程序，没有下发 MES。

## 通用约定

- 时间推荐 `2026-11-12T08:00:00+08:00`，未带时区按北京时间解释。内部出货日建议明确到时刻；仅填写日期按当天 **00:00** 处理。月份为 `YYYY-MM`，统计区间为月初含至次月月初不含。
- PPN、TYPE、EQPID、ProberID、Recipe、针卡和 uid 等业务标识忽略大小写，处理全半角和首尾空白；LotID 显示值保留。Platform 使用 `;` 分隔设备 TYPE，可跨组，不是设备编号。
- 一个快照表示一个月、同一 `asOf` 时点的一致事实。已到位、在制、实际完成及库存需避免重复行。单个 Lot 为 1～25 wafer，系统不凭 MPS 缺口虚构 Lot。
- 每个 PPN 在需求中只出现一次，数量由上游汇总；PPN 使用现有路线的产品前缀，不含 `-厂家` 后缀。系统按完整快照接收，缺少记录不能解释为“自动完成”。
- 首版快照不超过 20,000 Lot、请求不超过 20 MB。这是接收上限，不是性能或排程质量保证；真实规模仍需联调测量。

## 顶层字段

| 字段 | 必填 | 用途 |
|---|---|---|
| schemaVersion | 是 | 固定整数 `1` |
| source | 是 | `mes` 或 `demo`，标记数据来源 |
| snapshotId | 是 | 非空字符串快照版本号，用于追溯；同来源同 ID 内容不可变 |
| asOf | 是 | 快照时点，必须在计划月份内；同来源拒绝倒退的快照 |
| month | 是 | 计划月份，按该自然月统计 |
| identityStable | 否 | 默认 false；true 表示上游确认 uid 跨快照稳定 |
| demands | 是 | 产品月度 MPS，见下表 |
| lots | 是 | 实际库存、明确未来来料、HOLD、在制、实际完工明细 |
| equipment | 是 | 当前实际设备及组合关系；至少一台 |
| flows | 是 | 按 PPN 指定完整加工路线 |
| recipes | 是 | CP Recipe 与兼容针卡类型 |
| bakeChambers | 否 | 每台 OVEN 腔体数，默认 4，整数 1～100，当前全部 OVEN 使用同一值 |
| pm | 否 | PM 计划，默认空列表 |
| borrow | 否 | 借机计划，默认空列表 |
| externalBlocks | 否 | 不对应本系统待排 Lot 的已知资源占用/停机，默认空列表 |
| constraints | 否 | 机台禁加工条件，默认空列表 |
| ingestion | 建议 | 完整性声明，见下文；省略时按旧 v1 完整月度快照契约解释 |

推荐 `"ingestion":{"mode":"full","complete":true,"scope":"production-month","sourceVersion":"源读取版本"}`。当前拒绝 delta、不完整分页和只覆盖在制的窄范围列表。上游适配器须先完成全量组装；complete 是调用方声明，程序不能仅凭该值证明多个 MES 接口来自同一读取版本。实际联调仍需分页令牌、各源版本与有效时点。

快照摘要统一时区、整数数值及无序记录顺序；工序顺序保持有序。同 ID、同规范化内容幂等返回；同 ID、不同业务内容拒绝，包括历史 ID 重用。接收校验失败不替换已知事实，记录失败原因并阻止继续试排/选用，直到成功接收完整数据。

## demands：月度需求

| 字段 | 必填 | 用途 |
|---|---|---|
| ppn | 是 | 唯一产品前缀，与 Lot、flows 关联 |
| wafer | 是 | 本月目标 wafer，非负整数；允许大于已知来料数量，以展示缺口 |

## lots：物料、状态及交期

| 字段 | 必填 | 用途 |
|---|---|---|
| uid | 稳定标识模式必填 | 字符串独立批次主键，跨快照匹配、冻结、比较。不可使用可重复的显示 LotID 代替；数值编号拒绝接收 |
| lotId | 是 | 字符串产线显示批次号，允许重复；重复行仍须不同 uid |
| ppn | 是 | 对应 demands、flows |
| wafer | 是 | 当前该 Lot wafer 数，整数 1～25 |
| platform | 是 | 可加工的实际 EQP.TYPE，如 `K8000L;T5830` |
| state | 是 | `READY`、`FUTURE`、`HOLD`、`RUNNING`、`EXTERNAL` 或 `DONE` |
| availableAt | READY 必填 | 物料确定可排时间；READY 不晚于 asOf；FUTURE 没有此值则只展示、不排产 |
| shipDue | 是 | 内部计划出货时刻；调度首要优先级，无包装、检验时间扣减 |
| location | 否 | 实际当前位置，展示用途，不从计划机台推测当前位置 |
| holdReason | 否 | HOLD 或不可排说明 |
| actualComplete | DONE 必填 | 最后一道加工实际完成时刻，不得晚于 asOf；用于实际达成与静置记录 |
| current | RUNNING 必填 | 当前工序占用对象，字段见下表 |
| externalComplete | RUNNING 或 EXTERNAL 可选 | 外部提供的最终加工完工预测，下限为当前加工结束。在机且未单列 processEnd 时取预计设备释放；缺失或过期列为“外部未知” |
| handoff | EXTERNAL 必填，移交后 DONE 保留 | eventId、operationId、eqp、step、start、processEnd、releasedAt；BAKE 含 chamber。依次表达原工序实例、实际加工结束和设备实际释放 |

`READY/FUTURE` 从所提供完整路线开始排。`RUNNING` **只保留当前工序剩余占用**，不生成本系统的后续路线；外部完工预测只参加统计，不生成任务。`DONE` 不再排产。HOLD 不排产。已完成数量和在制外部预测各计一次。

稳定标识尚未确认时设 `identityStable=false`。缺少 uid 会生成仅本快照有效的行标识；不猜测跨快照关联、不合并重复 LotID。此模式不能跨快照冻结或替换已有选用方案；首个方案可试排、选用，后续关联需先约定稳定标识。

跨快照发现原 RUNNING 消失、直接退回待排、改换当前工序/装配，或本月 DONE 消失/回退，均记录 `factIssues` 并阻止新方案。保留最后核实的事实基线。设备释放后由外部接续的 Lot 使用 EXTERNAL，保留 handoff，无本地后续任务；实际终工进入 DONE。拆合批、返工、报废和数量修订仍需约定独立业务事件。

### current：在制当前占用

| 字段 | 必填 | 用途 |
|---|---|---|
| step | 是 | CP 工序名（以 CP 开头）或 `BAKE` |
| operationId | 增量移交必填 | 非空字符串，标识该 Lot 的此次工序实例，贯穿 START、EXTEND、RELEASE |
| processEnd | 可选 | 预计加工结束，介于 start 与 end；BAKE 可以先加工结束、稍后才释放腔体 |
| batchId | WIP 事件中 BAKE 必填 | 实际同腔同批标识。用于区分同一时段的不同烘烤批 |
| eqp | 是 | 当前实际 EQPID |
| start | 是 | 已开始时刻，须不晚于 asOf |
| end | 是 | 预计释放资源时刻，须晚于 start；晚于 asOf 才能参与试排。已过预计释放但没有实际完成证据时保留事实、提示待确认，不自动释放；BAKE 应含剩余占腔时间 |
| recipe | 是 | CP Recipe 或 BAKE 的 `210C24H` / `250C24H` |
| card | CP 必填 | 当前针卡类型，须兼容 Recipe |
| chamber | BAKE 必填 | `EQPID-C1` 等真实腔体编号，不得超过 bakeChambers |

同一 CP 不能有重叠在制 Lot。BAKE 同一批次仅允许完全相同的占用开始/结束、Recipe 及腔体，最多 4 Lot。不得将同一段在制占用又重复放入 externalBlocks。

能够识别实际资源，但当前 CP 工序或 Recipe 不满足新路线时，保留原事实并阻止新排程；无法识别资源或时间无效则拒绝接收。两种情况都保留原选用方案供查看，不能把异常记录删掉后释放产能。

## equipment、flows、recipes

| 子表 | 字段 | 必填 | 用途 |
|---|---|---|---|
| equipment | id | 是 | EQPID，唯一；TYPE 可以重复 |
| equipment | type | 是 | CP 机台组 TYPE；烤箱 TYPE 包含 `OVEN` |
| equipment | status | 是 | `RUN` 可安排新加工；`DOWN/PM/OFFLINE` 不可安排。临时停机后恢复应使用 externalBlocks 表达有限区间 |
| equipment | proberId | 否 | 组装配对 Prober，供 PM 组合停机映射；同一 Prober 配给多台 EQP 时记录冲突并阻止试排，不能解释成无限共享 |
| equipment | recipe | 建议 CP 提供 | asOf 时实际 Recipe；用于换 Recipe 及借机时点检查 |
| equipment | card | 建议 CP 提供 | asOf 时针卡类型；未知初始类型按需要切换处理 |
| recipes | recipe | 是 | 唯一 CP Recipe 名称 |
| recipes | cards | 是 | 非空针卡类型列表；首版沿用针卡数量充足假设 |

`flows` 示例：`{"P01":[["CP1","R1",1.2],["BAKE","210C24H",24],["CP2","R2",0.8]]}`。

数组依次为工序名、Recipe、工时。CP 工时单位**分钟/wafer**，乘以 Lot wafer 数。BAKE 只支持 210C24H / 250C24H，加工参数固定 24 小时，升温 2h、加工 24h、降温换 Lot 2h，合计占腔 28h，Lot 第 26h 放行，每批最多 4 Lot。存在 BAKE 路线时必须提供实际 OVEN，不能自动补出设备。

新快照路线只包含加工工序 CP / BAKE，不能把 14 天静置写成 `QTIME1`。静置结束 = 最后一道加工结束 + 336 小时，单独展示；不阻塞后续、不占设备、不改变月度达成。旧 Excel 路线中的 QTIME 仍遵守旧规则。

## pm：维护计划

| 字段 | 必填 | 用途 |
|---|---|---|
| id | MES 未结束维护必填 | 字符串稳定维护事件 ID；不同维护事项须不同 ID，不能用重复 EQPID 替代 |
| state | 建议 | PLANNED / RUNNING / DONE / CANCELLED；默认 PLANNED。兼容 IN_PROGRESS / COMPLETED / CANCELED |
| eqp | 是 | 原始维护对象 EQPID 或 ProberID |
| targetKind | 建议 | `EQP` / `PROBER`；同名歧义时必须明确 |
| early | 未结束维护必填 | 最早可开始日期，YYYY-MM-DD；带时刻的值按北京时间日历日归一 |
| due | 未结束维护必填 | 期望维护日期，YYYY-MM-DD |
| over | 未结束维护必填 | 最晚维护日期，YYYY-MM-DD，窗口包括当天 |
| hours | 未结束维护必填 | 维护时长，正数小时；RUNNING 实际占用以 start / expectedEnd 为准 |
| start | RUNNING 必填，PLANNED 可选 | RUNNING 实际开始；PLANNED 指定开始，填入后不得自动移动 |
| expectedEnd | RUNNING 必填 | 预计释放；到期未确认完成时阻止现场计划，不自动释放 |
| completeTime | DONE 必填 | 实际维护完成，不能晚于 asOf；由 RUNNING 转入时不得早于原实际开始 |
| version | 建议 | 非负整数数据修订版本，不能倒退；同版本不同内容阻止试排 |
| sourceId / taskId | 可选 | 字符串源记录/工单标识，保留用于原始事件追溯，不代替 id |
| updatedAt | 建议 | 源数据最后修改时刻，带时区 |

要求 early ≤ due ≤ over，指定开始和结束必须处于维护窗口。任何一方维护，EQP 与组装 Prober 对应的整组资源停机。两条指定 PM 重叠时各项维护仍保留，只合并停机区间；不同时段分别停机。不能仅因同属一组或同一天就去掉一条 PM。原始维护与资源映射详见 [EQP 与 PM 契约](MES输入要求_EQP与PM.md)。

PLANNED 不能被补排到排程起点之前；柔性维护尝试窗口内较早和较晚安排。RUNNING 是实际停机事实，即使错过原窗口也保留实际占用；DONE / CANCELLED 保留追溯但不生成新停机。执行中 PM 不得靠缺行或 CANCELLED 释放，必须提供实际完成；未结束计划缺失需明确取消或完成。旧 Excel 的未开始 PM 可继续使用派生 ID；实际 MES 应提供稳定 ID。

## borrow、externalBlocks、constraints

| 子表 | 字段 | 必填 | 用途 |
|---|---|---|---|
| borrow | eqp | 是 | 被借的 CP EQPID |
| borrow | recipe | 是 | 要求的 Recipe；与**借机开始时**该机台的 Recipe 比较 |
| borrow | start | 是 | 明确借机开始，不得早于 asOf；已经开始的借机用 externalBlocks 表示 |
| borrow | hours | 是 | 借机时长，0.6～24 小时 |
| externalBlocks | resource | 是 | CP EQPID、OVEN EQPID 或 `OVENID-Cn`；整台 OVEN 占用会展开为所有实际腔体 |
| externalBlocks | start / end | 是 | 占用开始/释放，end 必须晚于 start |
| externalBlocks | reason | 否 | 停机/外部使用原因 |
| externalBlocks | recipe / card | 可选 | 外部 CP 使用结束后明确的 Recipe 和针卡；提供 Recipe 时须给出兼容针卡 |
| constraints | eqp / recipe | 是 | 禁止该机台执行该 CP Recipe |

借机时点 Recipe 由初始状态、此前实际在制、冻结任务和本次已排任务推导。生成任务时允许等待借机结束，避免先切换到另一 Recipe 导致本可行方案失败。借机后未提供新状态时保留原 Recipe；若业务涉及借机后换 Recipe，接入前需补充该状态约定。Recipe 不匹配的候选会被拒绝；固定时段无空档的借机作为取消/待处理记录展示，不声称已安排。

## 可配置规则与统计口径

| 字段 | 默认 | 单位/限制 |
|---|---|---|
| recipeChangeMinutes | 30 | 分钟，0～1440 |
| cardChangeMinutes | 5 | 固定分钟；与 Recipe 切换叠加，两项都发生默认 35 分钟 |
| restHours | 336 | 固定小时，仅静置信息 |
| maxRuntimeSeconds | 1800 | 秒，1～1800；可提前结束，不必跑满 |
| freezeHours | 0 | 小时，0～744，具体范围待产线确认 |
| planningLeadMinutes | 35 | 现场计划从当前时刻预留的分钟数，0～1440；须留够试排和人工确认时间 |
| snapshotMaxAgeMinutes | 60 | 现场快照最大年龄，1～1440 分钟；以服务器时钟检查 |
| checkTime | 08:00 | 北京时间 HH:MM，每日检查已接收数据 |
| delayAlertHours | 0 | 延期超过该小时数触发预警 |
| shortageAlertWafers | 1 | 月目标缺口达到该 wafer 数触发预警 |

默认始终保留 RUNNING 当前占用。冻结窗口内有计划任务的 Lot，会保留整条剩余路线，并连带同一 BAKE 批次的其他 Lot；这是首版保守冻结范围，页面会说明。冻结输入、资源资格、切换准备时间或停机冲突不满足时失败，要求核对事实或调整冻结范围。不能用冻结把已过去、但未提供执行事实的计划冒充实际。

影子试排从 asOf、现场计划从 execution.start 向后安排已知可排物料，以本自然月末为 MPS 统计边界；为了展示超期，已知 Lot 的加工尾部预测可能延伸到下月。尚未接收下月 MPS 独立需求；后续如需跨月接单，应另行确认目标优先级和快照字段。

调度采用离散事件仿真、多起点启发式与最多 32 次优先级邻域搜索，受运行时间上限约束。比较顺序：本月及以前到期的迟交或无完工预测 wafer、其中无完工预测 wafer、已知完工的 wafer 加权延期、各产品达成率均衡（月目标封顶）、月内完成量、未知完工量、总周期与切换时间。已排和未排使用相同交期范围，下月交期作为正常结转；不能通过漏排必迟 Lot 消除履约损失。外部在制预测和实际完工参加同一产出账本，产品达标后继续超产不会改善封顶达成率。交期优先可能与产量优先冲突；不承诺全局最优。

每个候选进入最优比较前，由独立校验器重建 CP、Prober、BAKE 腔体、在制、外部占用、PM 和借机资源区间，重新核对工艺顺序、资格、工时、切换、批次容量、产出守恒。选用时再次校验。校验失败候选不得成为当前最优或被选用；超时/取消只保留已经通过校验的候选。尚未实现精确求解器、独立设备改派邻域或 BAKE 批次重组。

三个差额分别显示：①实际完成 − 原选方案截至 asOf 应完成；②新方案月度预计 − 原选方案月度预计；③新方案月度预计 − 本月 MPS。预测包含本月实际完成、本系统待排 Lot 月内预计完成和外部提供的在制月内最终预测，避免重复累加。没有基线显示“无基线”。产品缺口不直接归因为某个停机事件，避免虚构因果。

## 持久化与联调边界

权威工作区在配置文件同目录的 `production/production.sqlite3`，包括当前状态、各来源状态、不可变快照登记和保存的方案。首次读取自动在单个事务中导入旧 state.json、sources/*.json 及方案 JSON，保留旧文件原样；之后不再更新旧 JSON。任何旧文件损坏会明确报错，不部分迁移。当前方案、选用理由、来源状态和历史在同一事务提交；版本比较阻止并发覆盖，失败回滚为完整旧状态。

仅支持同一主机本地磁盘的 SQLite 存储，不应把数据库放共享网络目录或作为多节点服务。服务停止后备份整个配置目录；回退代码必须同时恢复对应备份，不得直接读取已过时的旧 JSON。Git 排除此目录。历史摘要显示最近 50 条，不自动删除历史数据库记录。

当前没有 MES 主动拉取、认证授权或下发执行，没有真实产线性能验收。上游需要确认稳定批次标识、事实快照一致性、在制预计释放、外部最终完工预测、实际状态字典和冻结范围。交付的演示数据用于功能验证，不代表现场供给、产能或承诺交期。

### 内置演示接口值

`case`：`full`、`basic`、`recipe`、`paired_pm`、`month_end`、`capacity`、`freeze`、`identity`、`scale500`、`timeout2000`。载入会重置演示规则；其中 freeze 为 1 小时冻结，scale500 上限 60 秒，timeout2000 上限 2 秒，其余使用确认后的默认规则。

`event`：`arrival_known`（未知来料确认 24 小时后到位）、`arrival_delay`（既有来料延后 48 小时）、`due_urgent`（待排 Lot 交期提前到 2 小时后）、`machine_down`（选择无当前承诺的 CP 停机 12 小时）、`external_forecast`（补充在制外部完工预测）、`release_hold`（放行 HOLD）。时间均相对快照时点自动生成；综合场景覆盖全部六种。每种变化每次载入只能应用一次，不适用的场景明确拒绝。事件保留已选基线、使旧候选失效，并记录生成的具体对象和原因。

输入自检的拒绝样例不会保存到工作区。普通手工来料/交期修改只允许 READY/FUTURE，不得把 DONE/RUNNING/HOLD 改回待排；HOLD 放行用明确放行事件或新 MES 快照。再次保存已选候选为比较记录，不覆盖原选用理由。

## Excel 接入与共用展示

Excel 通过适配器转换为 source=excel 的相同结构；MES 继续传 source=mes。详细列名和缺失字段处理见 [统一排产与客户演示](../统一排产与客户演示.md)。Excel 新增生产信息表提供 AsOf、Identity Stable；Lot 明细使用 UID 区分重复 LotID。库存与 MPS 目标独立，缺交期／明确可用时间时不安排新加工。在制当前占用必须完整；PM／借机等资源事实不完整时阻止试排。

接口保存和选用返回 chart，它由 active 只读投影产生，不能单独替换或独立排产。选用前所有结果仍显示旧方案并提示输入变化；选用后统一更新。CSV 导出包含方案编号，Lot 导出还包含 UID。
