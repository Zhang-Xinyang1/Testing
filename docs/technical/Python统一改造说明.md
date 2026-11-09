# Python 统一改造说明

更新日期：2026-11-09。

## 目标与边界

本次改造把**排产算法、测试、构建工具和启动部署脚本**统一到 Python。浏览器仍然需要 HTML/CSS/JavaScript，因此前端只保留展示与交互层，不再包含排产算法。

- 后端：Python 3.9+，仅使用标准库。
- 前端：原生 HTML/CSS/JavaScript，通过 `POST /api/schedule` 获取排产结果。
- 测试与工具：Python `unittest` / 标准库脚本。
- 不再需要 Node.js、npm、Bash 启动脚本或 Batch 启动脚本。

## 主要变更

| 原实现 | 新实现 |
|---|---|
| `frontend/assets/scheduler.js` 浏览器排产 | `backend/scheduler.py` Python 离散事件排产 |
| 浏览器 `TestingScheduler.run` | `POST /api/schedule` + `frontend/assets/schedule-client.js` |
| `tests/scheduler.test.js` | `tests/test_scheduler.py`（20 项规则回归） |
| `.artifacts/verify_metrics.js` | `tools/verify_metrics.py` |
| `.artifacts/build_case.mjs` | `tools/build_case.py`（标准库生成 XLSX） |
| `.artifacts/build_rules_ppt.mjs` | `tools/build_rules_ppt.py`（可选 `python-pptx`） |
| `.artifacts/finalize_rules.mjs` | `tools/finalize_rules.py`（标准库校验并复制 PPTX） |
| `run.sh` / `deploy.sh` / `deploy.bat` | `run.py` / `deploy.py` |
| 一键启动、停止、重启的 `.sh` / `.bat` / `.command` | `一键启动.py` / `停止服务.py` / `重启服务.py` |

## 排产接口

```http
POST /api/schedule
Content-Type: application/json

{"data": {"meta": "...", "...": "可选规范化输入"}, "options": {"bakeWindowHours": null}}
```

- 省略 `data` 时，后端重新读取当前配置和 Excel。
- 提供 `data` 时，用于配置预览和保存前验证。
- 后端先执行 `validate_schedule`，再调用 `scheduler.run`。
- 返回 `{"ok": true, "result": {...}}`。结果字段与原前端引擎保持 camelCase 兼容。
- 响应会压缩重复引用：不返回输入副本，批次内只保留 Lot 摘要；完整结果仍保留给 Python 工具和测试。

## 验证结果

- Python 测试：**53 项全部通过**。
- 全算例核验：**8 类硬约束全部通过**。
- 与迁移前 JavaScript 引擎逐项对比（2026-11-06 旧口径）：72 产品、11,000 wafer、476 Lot、1,610 道 CP、209 个 BAKE 批次、产品达成率、任务时间、批次组合和 Lot 路线完全一致。2026-11-09 起 CP 工时改为按 wafer 计算，本历史对比不再代表当前完成率结果。
- 全算例三组组批方案运行约 0.7–1.0 秒；`/api/schedule` 压缩后响应约 3.4 MB。

## 使用命令

```bash
python3 run.py                 # 开发启动
python3 一键启动.py             # 一键启动（Windows 可用 py -3）
python3 停止服务.py
python3 重启服务.py
python3 deploy.py status       # status/check/logs/restart/stop
python3 -m unittest discover -s tests -v
python3 tools/verify_metrics.py
python3 tools/build_case.py    # 仅标准库重建合成算例
```

## 行为变化

1. 旧 `.sh`、`.bat`、`.command` 入口已删除，请改用 Python 入口。
2. 直接打开 `frontend/index.html` 的静态模式只展示内置数据，不执行排产；排产必须通过 Python 服务。
3. 浏览器不再下载或执行排产算法；页面首次加载会调用 `/api/schedule`。
4. PPT 重新生成需要可选依赖 `python-pptx`；不安装不影响服务、测试和 Excel 读取。
5. `tools/build_case.py` 生成的合成 XLSX 与现有算例数据逐字段一致，但不再包含原 JS 工具的表格样式。