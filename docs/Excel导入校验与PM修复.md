# Testing_11101136：PM 修复与 Excel 批量校验

基线为用户提供的 `Testing_11101136.zip`，不是后来的统一生产快照版本。
原包 SHA256：`abdcc0d568a5f54953f4cecec64c924272f6cc81b75c0ab67f1bc3739a33037c`。
独立分支 `codex/archive-11101136-pm-excel-check`，不更新或合并 main。

## 服务器用法

推荐只复制交付的单文件 `Excel导入校验_11101136.py`。需要 Python 3.9+，无需安装任何第三方包。脚本自带本版的共用导入规则，在临时目录运行，不启动服务、不排产、不改 Excel、不保存配置。

```powershell
python "Excel导入校验_11101136.py" --config "D:\Testing\config\config.json" --excel "D:\数据\实际数据.xlsx" --output "D:\检查报告"
```

必须使用服务器实际 config.json，因为它决定 Sheet 名、字段映射、表头行、月份以及手工覆盖。不要用演示配置替代现场配置。`--excel` 覆盖本次检查的文件路径，不写回配置。

**运行时不在终端打印报错。** 直接打开输出目录内本次新建的 `excel_audit_日期_时间` 文件夹，阅读 `report.txt`。同时提供 `issues.csv` 和 `report.json`，便于筛选和分析。每次独立输出，不覆盖旧报告。若输出目录没有权限，会尝试将失败报告写到系统临时目录下的 `TestingExcelAudit`。

完整源码目录内可运行：

```powershell
python tools/validate_excel.py --config config/config.json --excel "D:\数据\实际数据.xlsx" --output "D:\检查报告"
```

源码脚本依赖同目录的后端规则，不能只复制这一份源码到另一版本中。独立交付脚本已把依赖打包在一个 .py 内。重新生成它：

```powershell
python tools/package_excel_validator.py "Excel导入校验_11101136.py"
```

其他参数：

| 参数 | 用途 |
|---|---|
| `--excel-only` | 本次检查忽略 EQP、PM、借机、在制的手工覆盖，检查原 Excel 作对照；保留 BAKE 配置，不修改服务器配置 |
| `--month 2026-11` | 仅本次检查使用的计划月份 |
| `--project-root "D:\Testing"` | Excel 路径为相对路径时的项目根目录。独立脚本默认从标准 config/config.json 位置推导；自定义配置布局请明确指定 |
| `--output "D:\检查报告"` | 报告保存位置；省略则在当前目录的 excel_audit_reports 内生成 |

默认报告使用“服务器有效输入”，会标明哪些表被手工配置覆盖。若 Excel 有机台而报告说缺少，可增加 `--excel-only` 再检查一次，比较两次报告。

## 报告口径

| 级别 | 含义 |
|---|---|
| ERROR | 导入、关联或 PM 日历等阻断问题 |
| WARNING | 不一定阻断，但可能使用默认值、忽略输入、没有可排机台或取消借机 |
| SKIPPED | 前置字段/文件不完整，关联检查尚未完成，不代表通过 |
| INFO | 未使用重复列、配对维护合并等说明 |

每条问题包含子表、字段、Excel **物理行号及列字母**、原值、问题、修改建议。手工覆盖用“手工记录第 N 条”，不冒充 Excel 行号。LotKey 按非空 Lot 记录的读取顺序编号，与物理行号不同。

一条错误不会阻断下一条记录；一张表字段缺失不会停止其他表。缺少关联键时只跳过依赖项，继续检查该行其他字段。不限制为前 20 条。文件完全不可读时只能报告文件和配置问题，不能声称检查了内部数据；修复后须重跑。

退出码：0=无阻断错误且程序导入复核通过；1=发现错误或检查尚未完整；2=无法读取文件/配置、脚本内部检查异常或报告无法写入。无论成功或失败，都优先查看 TXT 报告中的警告与检查边界。

## 完全按本版输入格式检查

以下是逻辑字段，实际 Excel 列名由 fieldMap 决定。脚本复用本版业务标识、Platform、日期、数字、PM 解析，并在批量检查通过后调用本版实际 `build_payload` 做最终复核。

| 子表 | 本版需要检查的内容 |
|---|---|
| MPS | platform、ppn、mps；wafer 非负整数；产品前缀汇总；Platform 对应有效 TYPE |
| EQP | id、type；EQPID 唯一，TYPE 可重复；一台只能一个 TYPE；RUN 资格；proberId 对应关系；初始针卡/Recipe 可选 |
| 针卡 | recipe、cards；Recipe 唯一；针卡列表非空；按逗号、分号、换行及配置分隔符读取，重复针卡去重 |
| 工艺路线 | prod、step、recipe、time；按原表顺序检查七种路线；CP1–CP4 为正数分钟/wafer；BAKE 为 210C24H/250C24H；本版本保留 QTIME1=7/14/28 |
| PM计划 | eqp、early、due、over、hours；TargetKind、Start 可选；对象解析、日期顺序、连续时长、组合停机、在制及其他 PM 冲突 |
| 借机计划 | date、eqp、hours；CP EQPID、0.6–24 小时、日期、占用冲突；本版 Excel 不读取 Start/Recipe，不能按新版本强制补列 |
| 约束条件 | eqp 和 recipe 引用有效 CP 设备、Recipe；type 非空时与机台一致 |
| Lot 明细（可选） | lotId、wafer；LotID 允许重复；每行 1–25 片；产品、Platform、日期；此旧版本要求每产品 Lot wafer 合计等于 MPS |
| 在制（可选） | lotId；重复 LotID 需 lotKey；占用资源时必须有起止时间；CP 针卡、BAKE 腔体、最多 4 Lot 组批及重叠检查 |
| 周目标（可选） | PPN、PPN/Week 唯一、日期范围和周间重叠；目标沿用本版默认/取整行为并告警 |
| 参数（可选） | 子表及已配置字段读取；按本版规则用于参考参数，不强制新增字段 |

特别注意：

- 大小写及相应格式差异按程序规则归一化。型号不猜测：K8000 和 K8000L 仍然不同。
- TYPE、LotID 重复合法。只有实际参与映射的重复表头阻断；未使用的 Density/Core Volt/Tech 等重复列无需删除。
- 未配置的可选表不用强制存在；已配置但不存在的 Sheet 会报错。空 PM、借机等可选表按本版视为无记录。
- 无 Lot 表时默认按 MPS 拆 25 片及尾批，不代表现场物料。**不要求 UID、State、Ship Due、AsOf 等后续版本字段。**
- 该版本 EQP 有 OVEN 时固定每台 4 腔；没有 OVEN 时用保存容量；完全未配置容量时沿用旧版 7 台 × 4 腔。脚本对实际不生效的配置给警告，不擅自换规则。
- 周目标和 Priority 的默认、截断行为按本版执行并告警；MPS、Lot wafer 仍须满足各自数值规则。
- 原始 Excel 中公式只读取已保存的缓存值，脚本不计算公式。无缓存/错误值会指出位置；已有缓存也提示核对是否过期。
- 若批量检查已发现错误，则先保留完整报告，不让实际导入函数的首个异常盖住其他结果。修复后再运行最终复核。

工具不启动优化算法，不能据此承诺产能充足、所有任务按期完成或程序永不报错。

## PM 修复后的规则

1. EQP 保留唯一 EQPID，并在可选 proberID 列填写组装的 Prober。不要把 Prober 当作额外 CP 机台新增一行。
2. PM 的 EQP ID 可以是 EQPID 或 proberID；TargetKind 可空、EQP、PROBER。编号同时匹配两类对象时必须明确类型。
3. 任一部件维护，整个组合设备停机；加工和借机均避开整段停机。
4. 配对部件的指定时间重叠，合并停机区间、保留各项原始维护任务；不同时间分别停机。未指定 Start 的两项维护也分别安排；首尾相接不算重叠。
5. Early Due ≤ Due Date ≤ Over Due，Estimated Hours 大于 0、不超过 744 小时，须在 Over Due 当日结束前完成。
6. 结果保留 sourceIds/sourcePlans、affectedEqps/affectedProbers、resources。pmInputCount 是维护任务数，pmCount 是组合停机次数。
7. 页面增加 proberID、PM 对象类型及 Prober 选择，保存时保留关联关系。手工 EQP 覆盖须带自己的 proberId，Excel 配对不会绕过覆盖配置自动补入。

此次没有升级为新生产工作台，没有改变原有默认自动排产流程、QTIME 或 MPS/Lot 守恒口径。原始压缩包和主工作目录均不改写。
