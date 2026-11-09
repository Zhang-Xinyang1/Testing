# 字段读取放宽 + 重复 LotID 补丁

补丁文件：`read-and-duplicate-lot.patch`  
适用基线：当前仓库 `HEAD`（提交 `ccc8272` 之后的工作区改动已在补丁中体现）。  
补丁只包含代码和 `config/config.json`，不包含 Excel、前端 `data.js`、文档和测试。

## 应用前

1. 备份 `config/`、`backend/`、`frontend/assets/js/`。
2. 确认服务已停止，或应用后重启。
3. 如果服务器上有自己的 `config/config.json`，先另存一份；补丁会改其中的
   `针卡.cards`、`测试flow&工时.time`、`PM计划.eqp` 三个映射。

## 应用

```bash
cd Testing
git apply --check patches/read-and-duplicate-lot.patch
git apply patches/read-and-duplicate-lot.patch
```

如果现场 `config/config.json` 已有本地修改，建议只应用代码、保留现场配置：

```bash
git apply --exclude=config/config.json patches/read-and-duplicate-lot.patch
```

然后按“字段映射”小节手工调整配置。

没有 git 时：

```bash
patch -p1 < patches/read-and-duplicate-lot.patch
```

如果 `git apply` 报冲突，不要强制应用，按下面的“手工定位”逐段替换。

> 如果只需要放宽字段读取、不需要“重复 LotID”，不要删除 `LotID 唯一校验` 那一段；只替换 `server_core.py` 的 `norm_key` / `resolve_column` / `mapped_column` / `parse_datetime` / `rows_for` / `rows_of` / `field` / `get`，以及 `tools/build_data.py` 的 `num` 即可。

## 补丁做了什么

### 1. 放宽字段读取

- `backend/server_core.py`
  - 新增 `norm_key()`、`resolve_column()`、`mapped_column()`。
  - 表头/Sheet 名匹配忽略大小写、空格、全角空格、BOM、下划线、短横线、点、`&`。
  - `fieldMap` 的值可写字符串或候选列表，例如：
    ```json
    "cards": ["ProbeCard List", "ProberCard List"],
    "time": ["Time", "TIME", "时间"],
    "eqp": ["EQP ID", "EQPID", "设备编号"]
    ```
  - `get()` 对文本值统一 `strip()`。
  - 新增 `parse_datetime()`，日期支持 `2026-09-01`、`2026/9/1`、`2026年9月1日` 和带时间的文本。
- `backend/scheduler.py`
  - `parse_date()` 使用同样的日期容错。
- `tools/build_data.py`
  - `num()` 支持千分位逗号和单位后缀：`1,000`、`25 片`、`18 min`。
- `tools/check_fields.py`
  - 新增字段自检工具，支持候选列名，直接打印实际表头和匹配结果。

### 2. 允许重复 LotID

- `backend/server_core.py`
  - 删除 LotID 唯一校验。
  - `Lot明细` 只强制 `lotId`、`wafer`；`ppn` 可省略。
  - 新增 `flow_product()`：优先显式 PPN，其次完整 LotID，最后按 `-` 从长到短匹配 flow 前缀。`PPN1-1`、`PPN1-2` 都会匹配 `PPN1`。
- `backend/scheduler.py`
  - 每行 Lot 生成内部唯一 `key`（`LOT-000001` 形式），任务带 `lotKey`。
  - 重复 LotID 作为独立排产单元。
- `backend/diagnostics.py`
  - 诊断按 `lotKey` 区分重复 LotID。
- 前端
  - 甘特点击、Lot 详情优先用 `lotKey`，避免重复 LotID 打开错行。
  - `inputs.js` 的 MPS/flow 预览显示实际映射列名。

### 3. MPS 同一 PPN 多行与 PPN 后缀

- `backend/server_core.py` 新增 `base_ppn()` 和 `merge_mps()`。
- MPS 的 PPN 以第一个 `-` 前为准，`-` 后视为厂家后缀，不参与 flow 匹配。
- 同一前缀 PPN 的多行 MPS（不同厂家、同一厂家重复下单、完全相同的订单）全部读取，MPS 数量相加。
- Platform 按分号拆分后去重合并。
- 排产按汇总后的一个 PPN 需求执行，避免同一产品重复统计。
- `backend/scheduler.py` 和 `backend/diagnostics.py` 同样按前缀汇总后的 MPS 计算产品统计和诊断，直接传入未汇总的 MPS 也不会重复出产品行。
- `frontend/assets/js/mps.js` 增加提示：PPN 的 `-` 后后缀忽略，同一 PPN 多行按 PPN 汇总。

## 手工定位（补丁冲突时）

按函数/关键字搜索：

- `backend/server_core.py`
  - `DEFAULT_CONFIG =` 后：加 `norm_key`、`resolve_column`、`mapped_column`、`parse_datetime`。
  - `def rows_for`：替换为带规范名回退的版本。
  - `def build_payload` 内：加 `sheet_headers` / `mapping_for`，替换 `def rows_of`、`def field`、`def get`。
  - `def merge_mps`：新增；`build_payload` 读完 MPS 后调用。
  - `def base_ppn`：新增；PPN 第一个 `-` 前作为产品键。
  - `sep = fm.get(`：改为 `mapping_for(...).get('separator', ';')`。
  - `def validate_schedule` 内 `def date`：改为 `return parse_datetime(value)`。
  - `def calendar_issues` 内 `def date`：同上。
  - `lots = {r[0]: r for r in data['lots']}`：删除，改为 `lot_ids` 非空检查。
  - `# ---- Lots ----`：`prod = ...` 后调用 `flow_product(lid, prod, flows)`。
- `backend/scheduler.py`
  - `def parse_date`：替换为容错版本。
  - `def merge_mps`：新增；`simulate` 的 MPS 和 productStats 使用汇总结果。
  - `def base_ppn`：新增；MPS 前缀作为产品键。
  - `def _fair` 前：加 `flow_product`。
  - `def _fair`：相同 id 时用 `key` 兜底。
  - `for lot in sorted(flexible`：排序键加 `item.get('key')`。
  - `for row in data.get('lots')`：改为 `enumerate`，加 `key`、`flow_product`。
  - CP task 字典：加 `'lotKey': lot['key']`。
  - `batch['lots']`：加 `'key': lot.get('key')`。
- `backend/diagnostics.py`
  - 引入 `merge_mps`，MPS 统计按汇总结果。
  - `lot_products` / `releases` / `wafer_by_lot` / `by_lot` 相关段落：按 `lotKey` 取，保留 LotID 兜底。
- `tools/build_data.py`
  - `def num`：替换为带正则回退的版本。
- `config/config.json`
  - `针卡.cards` → `ProbeCard List`
  - `测试flow&工时.time` → `Time`
  - `PM计划.eqp` → `EQP ID`

## 应用后

```bash
python3 tools/check_fields.py
python3 重启服务.py
```

`check_fields.py` 输出应为“全部必需字段已匹配”。如果某个字段显示“缺失”，按它打印的实际表头修改 `fieldMap`；如果值格式不标准，先看补丁后的 `num()`/`parse_datetime()` 是否已覆盖，再决定是否改 Excel。

## 仍然严格的部分

- `headerRow` 必须指向真实表头行（默认 1）；补丁不做自动找表头。
- 必需字段必须存在；可选字段可缺失。
- `MPS` 同一 PPN 多行会按 PPN 汇总数量并合并 Platform，不会拒绝；MPS 数量仍必须是 wafer 整数。
- 在制表同一 LotID 仍只允许一行。
- 每 Lot 1–25 片、每产品 Lot 合计等于 MPS、七种路线、Recipe/机台引用等业务校验不变。
- 合并单元格、完全重复的列头仍可能造成读取异常，需要先整理 Excel。