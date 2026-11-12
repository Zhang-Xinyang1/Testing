# MES 生产快照业务字段 v1

更新：2026-11-12。本契约用于新入口“生产看板 / Lot 查询 / 滚动重排”。Excel 排程沿用原导入方式和历史业务规则；两套工作区不混用输入或方案。当前提供 JSON 接收验证，MES 拉取、认证及计划下发尚未接入。

## 操作与接口

启动后打开 `/#production`，点击“载入演示数据”，进入“滚动重排 → 开始试排”。完成后查看产品差额、Lot 变化和借机 Recipe，再填写说明选用。刷新页面及定时检查都不会启动排产。

实际数据在“滚动重排 → 生产规则与 MES → 粘贴快照进行接收验证”提交。空工作区也可直接接收。演示数据全部合成；实际 MES 工作区不能通过“重置演示数据”被覆盖。切换数据来源或计划月份时清空当前选用状态，保留历史记录。

| 方法与路径 | 用途 |
|---|---|
| `GET /api/production` | 已接收快照、规则、已选方案、最近 50 条方案摘要、变化与检查状态 |
| `POST /api/production/snapshot` | 接收完整快照；不接受零散增量，不自动试排 |
| `POST /api/production/demo` | 显式载入合成示例，不需要请求字段 |
| `POST /api/production/rules` | 保存生产规则；规则对象见下文 |
| `POST /api/production/trial` | `{"scenario":"actual"}`；扩产为 `{"scenario":"expansion","extraMachines":{"K8000L":2}}` |
| `GET /api/production/job` | 当前试排状态、已比较候选数、耗时、当前最好方案 |
| `POST /api/production/cancel` | `{"id":"任务编号"}`，停止计算，不更改已选方案 |
| `POST /api/production/adopt` | `{"id":"任务编号","reason":"选用说明"}`，只选用已完成或停止后留有可行结果的实际资源候选 |
| `POST /api/production/archive` | `{"id":"任务编号"}`，保存候选比较记录，允许扩产场景 |
| `GET /api/production/plans/{id}` | 打开已保存的完整生产方案，只读 |
| `POST /api/production/event` | 仅演示：模拟来料、交期或停机变化，现场事实通过快照提供 |

试排请求响应含 `job.id`。方案保存返回的 `id` 是方案编号，与任务编号不同。更改输入、规则或选用另一方案后，旧候选不能再选用。每个服务实例同时只运行一个生产试排；单独进程执行，可取消，最多 1800 秒。超时保留已收到的最好可行候选；如果没有找到可行候选，明确提示失败，不制造结果。当前试排任务本身不跨服务重启保存，需要“保存比较记录”或“选用此方案”持久化。

服务器每 30 秒检查一次是否达到配置时刻，每日执行一次数据变化检查。它检查的是**本服务已接收的快照**，不会自行向 MES 拉数据，不自动试排或自动选用。

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
| snapshotId | 是 | 非空快照版本号，用于追溯 |
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

## demands：月度需求

| 字段 | 必填 | 用途 |
|---|---|---|
| ppn | 是 | 唯一产品前缀，与 Lot、flows 关联 |
| wafer | 是 | 本月目标 wafer，非负整数；允许大于已知来料数量，以展示缺口 |

## lots：物料、状态及交期

| 字段 | 必填 | 用途 |
|---|---|---|
| uid | 稳定标识模式必填 | 独立批次记录主键，跨快照匹配、冻结、比较。不可使用可重复的显示 LotID 代替 |
| lotId | 是 | 产线显示批次号，允许重复；重复行仍须不同 uid |
| ppn | 是 | 对应 demands、flows |
| wafer | 是 | 当前该 Lot wafer 数，整数 1～25 |
| platform | 是 | 可加工的实际 EQP.TYPE，如 `K8000L;T5830` |
| state | 是 | `READY`、`FUTURE`、`HOLD`、`RUNNING` 或 `DONE` |
| availableAt | READY 必填 | 物料确定可排时间；READY 不晚于 asOf；FUTURE 没有此值则只展示、不排产 |
| shipDue | 是 | 内部计划出货时刻；调度首要优先级，无包装、检验时间扣减 |
| location | 否 | 实际当前位置，展示用途，不从计划机台推测当前位置 |
| holdReason | 否 | HOLD 或不可排说明 |
| actualComplete | DONE 必填 | 最后一道加工实际完成时刻，不得晚于 asOf；用于实际达成与静置记录 |
| current | RUNNING 必填 | 当前工序占用对象，字段见下表 |
| externalComplete | RUNNING 可选 | 外部系统提供的最终加工完工预测，不得早于 current.end；缺失单列“外部未知” |

`READY/FUTURE` 从所提供完整路线开始排。`RUNNING` **只保留当前工序剩余占用**，不生成本系统的后续路线；外部完工预测只参加统计，不生成任务。`DONE` 不再排产。HOLD 不排产。已完成数量和在制外部预测各计一次。

稳定标识尚未确认时设 `identityStable=false`。缺少 uid 会生成仅本快照有效的行标识；不猜测跨快照关联、不合并重复 LotID。此模式不能跨快照冻结或替换已有选用方案；首个方案可试排、选用，后续关联需先约定稳定标识。

### current：在制当前占用

| 字段 | 必填 | 用途 |
|---|---|---|
| step | 是 | CP 工序名（以 CP 开头）或 `BAKE` |
| eqp | 是 | 当前实际 EQPID |
| start | 是 | 已开始时刻，须不晚于 asOf |
| end | 是 | 预计释放资源时刻，须晚于 asOf；BAKE 应包含剩余降温/换 Lot 占腔时间 |
| recipe | 是 | CP Recipe 或 BAKE 的 `210C24H` / `250C24H` |
| card | CP 必填 | 当前针卡类型，须兼容 Recipe |
| chamber | BAKE 必填 | `EQPID-C1` 等真实腔体编号，不得超过 bakeChambers |

同一 CP 不能有重叠在制 Lot。BAKE 同一批次仅允许完全相同的占用开始/结束、Recipe 及腔体，最多 4 Lot。不得将同一段在制占用又重复放入 externalBlocks。

## equipment、flows、recipes

| 子表 | 字段 | 必填 | 用途 |
|---|---|---|---|
| equipment | id | 是 | EQPID，唯一；TYPE 可以重复 |
| equipment | type | 是 | CP 机台组 TYPE；烤箱 TYPE 包含 `OVEN` |
| equipment | status | 是 | `RUN` 可安排新加工；`DOWN/PM/OFFLINE` 不可安排。临时停机后恢复应使用 externalBlocks 表达有限区间 |
| equipment | proberId | 否 | 组装配对 Prober，供 PM 组合停机映射 |
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
| eqp | 是 | 原始维护对象 EQPID 或 ProberID |
| targetKind | 建议 | `EQP` / `PROBER`；同名歧义时必须明确 |
| early | 是 | 最早可开始日期 |
| due | 是 | 期望维护日期 |
| over | 是 | 最晚维护日期，窗口包括当天 |
| hours | 是 | 维护时长，正数小时 |
| start | 否 | 指定维护开始时刻；填入后不得自动移动 |

要求 early ≤ due ≤ over，指定开始和结束必须处于维护窗口。任何一方维护，EQP 与组装 Prober 对应的整组资源停机。两条指定 PM 重叠时各项维护仍保留，只合并停机区间；不同时段分别停机。不能仅因同属一组或同一天就去掉一条 PM。原始维护与资源映射详见 [EQP 与 PM 契约](MES输入要求_EQP与PM.md)。

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

借机时点 Recipe 由初始状态、此前实际在制、冻结任务和本次已排任务推导。借机后未提供新状态时保留原 Recipe；若业务涉及借机后换 Recipe，接入前需补充该状态约定。Recipe 不匹配的候选会被拒绝；固定时段无空档的借机作为取消/待处理记录展示，不声称已安排。

## 可配置规则与统计口径

| 字段 | 默认 | 单位/限制 |
|---|---|---|
| recipeChangeMinutes | 30 | 分钟，0～1440 |
| cardChangeMinutes | 5 | 固定分钟；与 Recipe 切换叠加，两项都发生默认 35 分钟 |
| restHours | 336 | 固定小时，仅静置信息 |
| maxRuntimeSeconds | 1800 | 秒，1～1800；可提前结束，不必跑满 |
| freezeHours | 0 | 小时，0～744，具体范围待产线确认 |
| checkTime | 08:00 | 北京时间 HH:MM，每日检查已接收数据 |
| delayAlertHours | 0 | 延期超过该小时数触发预警 |
| shortageAlertWafers | 1 | 月目标缺口达到该 wafer 数触发预警 |

默认始终保留 RUNNING 当前占用。冻结窗口内有计划任务的 Lot，会保留整条剩余路线，并连带同一 BAKE 批次的其他 Lot；这是首版保守冻结范围，页面会说明。冻结输入、资源资格、切换准备时间或停机冲突不满足时失败，要求核对事实或调整冻结范围。不能用冻结把已过去、但未提供执行事实的计划冒充实际。

计算从 asOf 向后安排已知可排物料，以本自然月末为 MPS 统计边界；为了展示超期，已知 Lot 的加工尾部预测可能延伸到下月。尚未接收下月 MPS 独立需求；后续如需跨月接单，应另行确认目标优先级和快照字段。

调度采用完整可行排程的多起点与局部搜索。比较顺序：延期 wafer 数、wafer 加权延期时长、各产品达成率均衡、月内完成量、未排数量、总周期与切换时间。交期优先可能与产量优先冲突；不承诺全局最优。

三个差额分别显示：①实际完成 − 原选方案截至 asOf 应完成；②新方案月度预计 − 原选方案月度预计；③新方案月度预计 − 本月 MPS。预测包含本月实际完成、本系统待排 Lot 月内预计完成和外部提供的在制月内最终预测，避免重复累加。没有基线显示“无基线”。产品缺口不直接归因为某个停机事件，避免虚构因果。

## 持久化与联调边界

工作区在配置文件同目录的 `production/`，包括 state.json、已选方案及手工保存的比较记录。上传 GitHub 排除此目录；部署迁移时应备份。历史摘要显示最近 50 条，不自动删除旧文件。

当前没有 MES 主动拉取、认证授权或下发执行，没有真实产线性能验收。上游需要确认稳定批次标识、事实快照一致性、在制预计释放、外部最终完工预测、实际状态字典和冻结范围。交付的演示数据用于功能验证，不代表现场供给、产能或承诺交期。
