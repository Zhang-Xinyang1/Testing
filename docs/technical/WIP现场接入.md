# MES WIP 接入、试排与验收

本版可从页面导入 JSON、由 MES 适配器推送事件，或用随包工具分页读取适配器。三个入口共用一次接收校验。先建立月度生产快照，后续批次只更新发生变化的 Lot。批次中省略的 Lot、设备及计划沿用原事实。真实源表、字段映射和访问凭据仍需与现场联调。

## 先用两批货验证一次

将代码包解压到一个新的测试目录，在该目录内运行以下命令，打开 `http://127.0.0.1:8088`。状态保存在这份程序自己的 config/production 下，按 Ctrl+C 结束服务。样例的来源标记为 MES，请在这个测试工作区操作。

```powershell
python backend/server.py --host 127.0.0.1 --port 8088
```

1. 点页面顶部的 **MES 数据**，在 **生产规则与 MES → MES 数据接入 → 导入或粘贴完整快照** 选择 [00_baseline.json](../../examples/wip/00_baseline.json)，点 **校验并接收快照**。月目标为 50 片，两个 Lot 的显示号相同，UID 分别是字符串 `0001`、`0002`，各 25 片。
2. 在同页 **接收 MES WIP 批次** 选择 [01_wip.json](../../examples/wip/01_wip.json)，点 **校验 WIP 批次**。预览中一行 ETA 仅供观察，另两行分别确认 0002 在 08:20 可用、0001 在 CP-01 开工。点 **接收本批 WIP**，在 **试排与比较** 选 **影子演示（历史数据）**、实际资源并开始试排。0001 预计占机到 08:30，0002 最早在 08:30 开始占用该机。0001 的 25 片列为外部预测待补。
3. 回到 **生产规则与 MES**，以同样方式接收 [02_wip.json](../../examples/wip/02_wip.json)，再做影子试排。0001 在 08:24 结束当前加工，08:25 实际释放设备，随后由外部接续，预计 08:40 最终完工。0002 可以从 08:25 占机。月预计为 50 片，实际仍为 0。若已选用上一步的方案，先比较两版，再人工选用，就能在甘特和 Lot 查询看到这 5 分钟差异。
4. 接收 [03_wip.json](../../examples/wip/03_wip.json) 并重排。外部在 08:40 上报 0001 于 08:39 实际完成最后一道加工。MPS 变为实际 25 片、含实际的月预计 50 片。再次发送同一批次，设备占用和片数保持一致。

固定样例为 2026 年 11 月的历史数据。要生成当月同一场景，可运行 `python tools/wip_example.py 新样例目录`。请使用未包含同名文件的目录。

## 接口与采集方式

| 方向 | 接口 | 行为 |
|---|---|---|
| 适配器 → 排产 | POST `/api/production/snapshot` | 首次及对账时提供同一时点的完整月度数据，含需求、路线、装配、资源日历和 Lot。现场增量要求 `source="mes"`、`identityStable=true`。[完整字段](MES输入要求_生产快照.md) |
| 适配器 → 排产 | POST `/api/production/wip-batch-preview` | 校验完整批次，返回页号、行号、UID、事件和处理结果。预览不写入生产事实 |
| 适配器 → 排产 | POST `/api/production/wip-batch-import` | 接收批次，把原始 JSON 内容、回执、事件版本与新快照放入同一事务。成功返回新的 `snapshotId`，供下批填入 `baseSnapshotId` |
| 审核者 → 排产 | GET `/api/production/wip-batches/{batchId}` | 按原始批次号查询完整报文和逐行回执。路径参数按 URL 编码。选用方案的 `input.wipBatchId` 可追到最后一批增量，`input.snapshotId` 可核对数据版本 |
| 采集器 → MES 适配器 | GET 已配置的源地址 | 第一页带 `page=1`，后续请求附带相同 `batchId` 和递增页码。各页须返回相同的批次头，另含本页的 `page` 和 `events`。采集器拼全后才提交排产服务 |

将采集器指向隔离测试服务，下面第一条只校验，第二条接收同一文件。对于分页地址，用 `--url` 替代 `--file`，例如第三条。源端如需 Bearer Token，从本机环境变量 `MES_WIP_TOKEN` 读取并使用 HTTPS。该令牌只发给源接口。调用方可按现场确认的周期运行采集命令。服务自身的定时检查只提示已接收数据的变化。

```powershell
python tools/wip_collect.py --file examples/wip/01_wip.json --server http://127.0.0.1:8088
python tools/wip_collect.py --file examples/wip/01_wip.json --server http://127.0.0.1:8088 --publish
python tools/wip_collect.py --url https://mes.example.org/wip --server http://127.0.0.1:8088
```

## 一批报文的字段

| 字段 | 约定 |
|---|---|
| `schemaVersion`、`batchId` | 版本固定整数 1。批次号为来源全局唯一、首尾无空白的字符串，原样用于重放核对 |
| `baseSnapshotId`、`asOf` | 当前 MES 工作区的快照编号，以及本批覆盖到的数据时点。新批次按顺序承接上一张回执。时点在同一计划月份内且不能倒退，换月先接收完整快照 |
| `pageCount`、`total` | 分别为页数和事件数。1～2,000 页、1～20,000 条。单次请求最大 20,000,000 字节。无变化时可跳过发送 |
| `pages[]` | 每页带相同 `batchId`、从 1 起的 `page` 和本页 `events[]`。必须齐页，不能重复页，事件数合计等于 total |
| 每行公共字段 | `eventId` 为来源全局唯一的逻辑事件字符串标识，`version` 为正整数，`kind` 为下表的事件类型，`uid` 为稳定的物料字符串标识，`recordedAt` 为来源记录或修订此事件的时刻 |

按页码、行序处理新事件。`recordedAt` 从基线时点到本批 asOf 单调不减。同一时刻先发解除占用，再发依赖该资源的开工。业务时间与上报时间分别填写；新上报的延迟释放可以提供更早的实际时间。时间建议带 `+08:00` 并至少明确到分钟。UID、工序和设备业务编号按既有规则忽略大小写，`0001` 等 UID 须保持为字符串。显示 LotID 可以重复。原始来源若只有可重复显示号或行号，先补充稳定映射再开启接收。

| kind | 专有字段 | 对生产的影响 |
|---|---|---|
| REGISTER | lotId、ppn、wafer、platform、shipDue，可选 location | 登记真实新增 Lot，1～25 片，PPN 已有需求和路线。初始 FUTURE，没有可排时间 |
| ETA | step、at | 记录工序预计到达。多道工序分别保留，同一 Lot 的库存仍只有一份 |
| AVAILABLE | step、at | 明确首工序可用时刻。适用于 READY/FUTURE。首工序须可唯一匹配；可以提前确认未来时间 |
| HOLD / UNHOLD | HOLD 给 reason，UNHOLD 给 at | 暂停待排 Lot，或明确放行可用时刻 |
| START | current 中的 operationId、eqp、step、recipe、start、end。CP 加 card，BAKE 加 chamber、batchId。可选 processEnd | start 为实际开工，end 为预计设备释放，processEnd 为预计加工结束。绑定实际工序实例，仅占用当前 CP 或腔体，后续由外部系统负责 |
| EXTEND | operationId、expectedEnd，可选 processEnd | 用新预计释放时间更新同一次在机占用。BAKE 同腔同批成员的预计占腔结束须一致 |
| RELEASE | operationId、eqp、step、processEnd、releasedAt，BAKE 加 chamber，可选 location | 用实际加工结束、实际设备释放和实例标识完成移交。Lot 进入 EXTERNAL，结束现场资源占用，等待外部终工 |
| FORECAST | at | RUNNING / EXTERNAL 的外部最终加工结束预测。显式 null 撤销预测。过期预测归入待补 |
| COMPLETE | at | 已移交 Lot 的实际最终加工结束。按此时刻和 Wafer 数计入实际产出；最后一道 BAKE 加工结束可早于占腔结束 |

INIT 时已在机的 Lot 在全量快照的 `current.operationId` 填入真实工序实例标识。只有这份对应关系确认后才能通过增量延长或释放。CP 的 EQP、Prober 沿用基线装配；BAKE 同腔同批最多四个 Lot，须有一致的 Recipe 和占用起止。单批内可先登记再确认或上机。拆合批、返工撤销和数量冲正仍需现场确定专用事件，不能通过覆盖 UID、片数或复用事件号表达。

## 对账、重试与现场启用

- **重复投递**。同批次相同内容返回原回执。跨批重复的同一事件版本逐行标为 replay。仅 ETA、AVAILABLE、EXTEND、FORECAST 支持同一 UID 和事件类型的更高版本修订。重复版本对应不同内容会报错。
- **中断和冲突**。缺页、未知 UID、状态矛盾或释放实例不符时整批拒绝，显示来源页行（跨记录问题显示批次错误）。当前基线的一次正式接收失败会暂停新试排和选用。修正并重发同批，或对账后重新提交完整快照。旧基线、同批号不同内容或并发修改返回 409，应先核对当前版本。同一批接收落库后可跨进程重启重放。
- **数据年龄**。WIP 只推进物料时点 `asOf`，资源与计划的时点 `masterAsOf` 保留最近完整基线的时间。现场试排和选用仍检查主数据年龄、在制预计释放和 PM。借机从未来计划进入执行时，应补交完整基线，用实际外部占用表达执行中的借机。跨月、超期、补历史开工或装配变化也应先对账。
- **选用**。接收成功只更新输入。计划员在同一工作区试排、比较并选用后，看板、MPS、甘特及 Lot 查询才显示新方案。影子模式可回放历史；现场选用需满足数据时效和冻结规则。选用结果保存在排产程序中。
- **部署边界**。服务使用原程序的内网访问模型。写入口和报文追溯包含生产数据，接入现场网络时复用企业网关的访问控制。真实投产前需确认 UID 与工序实例、明确可用标记、设备释放与最终完工的来源语义、分页版本与晚到策略。用连续的脱敏报文重放，再按实际峰值规模实测整月排产的 30 分钟目标。
