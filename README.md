# XMC 测试排产

接收 Excel、MES 或内置演示数据，统一试排、比较和人工选用，并同步展示生产看板、MPS、甘特及 Lot 查询。原始 Excel 只读，配置独立保存；排产算法、测试与启动脚本均由 Python 运行，浏览器只负责展示和交互。

## 服务器一键部署

上传整个 Testing 文件夹。Windows 可双击 `一键启动.py`（`.py` 已关联 Python 时）或执行 `py -3 一键启动.py`；macOS/Linux 在终端执行 `python3 一键启动.py`。脚本会先校验后端文件、前端入口与静态资源、Excel 数据和端口，再后台启动服务，并确认 `/api/health`、首页和 `/api/data` 均可用；桌面环境自动打开本机浏览器。启动后访问 `http://服务器IP:8080`。详细说明见 [部署说明](部署说明.md)。需预装 Python 3.9+。

| 操作 | Windows | Linux / macOS |
|---|---|---|
| 启动 | 双击 `一键启动.py` 或 `py -3 一键启动.py` | `python3 一键启动.py` |
| 停止 | 双击 `停止服务.py` 或 `py -3 停止服务.py` | `python3 停止服务.py` |
| 重启 | 双击 `重启服务.py` 或 `py -3 重启服务.py` | `python3 重启服务.py` |
| 状态 | `py -3 deploy.py status` | `python3 deploy.py status` |
| 检查 | `py -3 deploy.py check` | `python3 deploy.py check` |
| 日志 | `py -3 deploy.py logs` | `python3 deploy.py logs` |

Windows 与带图形界面的 Linux/macOS 启动成功会自动打开本机浏览器；SSH/无界面服务器只打印访问地址。前端与 API 由同一个 Python 服务同源提供，统一试排通过 `POST /api/production/trial` 在 Python 后端执行，无需 Node.js、npm 或前端构建。更新后端或配置后请执行重启；只更新前端静态资源时刷新页面即可。关闭启动窗口后服务继续运行，但普通后台模式不保证退出登录或重启后恢复。Linux systemd 开机自启为可选操作，Windows 暂未集成开机自启。

运行依赖清单见 [requirements.txt](requirements.txt)：Python 3.9+，运行时没有第三方包；`python-pptx` 仅作为 PPT 生成工具的可选依赖。

## 开发启动

```bash
python3 run.py
# 打开 http://127.0.0.1:8080
```

请使用后端地址，不是 IDE 的静态 Live Server。统一工作区需要 Python 服务，静态 Live Server 不支持快照、试排与选用。旧 `/api/schedule` 仅保留兼容与回归用途。

## 使用

### 统一生产排产（2026-11-13）

**Excel／MES／内置演示 → 统一生产数据 → 统一试排与选用 → 看板、MPS、甘特、Lot 查询同步展示。** 五个主入口为“生产看板 · MPS、排产甘特、Lot 查询、滚动重排 · 方案、数据配置”。原两套结果已接到同一个选用方案；顶部方案编号用于核对一致性。

推荐客户演示：**演示数据 → 客户主线 · 12 Lot → 试排并选用 → 查看缺口和 Lot → 应用明确来料变化 → 再次试排比较并选用**。默认月目标 375 片，预测从 220 片提高到 245 片。具体点击步骤、讲解内容、Excel 字段和修改范围见 [统一排产与客户演示](docs/统一排产与客户演示.md)。

| 操作 | 行为 |
|---|---|
| 打开或刷新 | 读取快照和已选方案，不自动排产；空工作区读取已保存 Excel |
| Excel 仅保存 | 更新统一输入；同月已选方案继续保留，提示待重排 |
| 保存并前往试排 | 保存后进入试排页，需人工开始计算、比较和选用 |
| 试排 | 后台生成候选，可取消，上限 30 分钟；候选不自动覆盖结果 |
| 选用 | 看板、MPS、甘特、Lot 查询及导出同步使用新方案 |
| 切换 Excel／MES／演示 | 恢复对应来源快照、规则和方案；各来源分别保留，不叠加生产 |

自带 `data/Testing调度算例_新版.xlsx` 已精简为 **3 产品、12 Lot、4 CP、1 烤箱 × 2 腔、9 张表**。保留在制、完工、未来来料、未知来料、HOLD、尾批、重复 LotID、组合 PM、借机和跨机台路线。Excel 固定使用 2026-11；内置演示自动使用当前月份。缺少实际可用时间或内部出货日不会被自动补成可排；MPS 目标与实际库存片数可以不同。

借机按开始时 Recipe 检查，默认换 Recipe 30 分钟与换卡 5 分钟叠加。在制仅保留当前占用，后续由外部系统负责。最后加工结束后静置 336 小时独立记录，不延迟 MPS 统计。扩产场景可保存比较，不能选为实际资源方案。定时检查只提示已接收数据变化，MES 主动拉取及下发尚未接入。

“演示场景与输入自检”保留 10 个场景：客户主线、综合生产、借机切换、组合 PM、月末、扩产、冻结、标识待确认、500 Lot 规模、2000 Lot 超时取消。主线默认只需 12 Lot。变化记录内置六种一键变化；输入自检含 20 个有效／错误样例。专项测试报告见 [演示功能测试报告](output/演示功能测试报告.html)，可运行 `python tools/verify_demo_scenarios.py` 重测。

数据配置仍支持字段映射、资源与日历手工覆盖、恢复 Excel 设备与计划、分页预览、日期校验和错误定位。旧方案档案在“滚动重排 → 方案与比较记录 → 旧版历史档案”只读查看；旧规则不冒充当前方案。旧手工 CP 调整存在时会提示核对解除；当前主流程不提供旧版单工序调整入口。Oracle 保留配置入口，尚未连接数据库。

更新本版后请重启服务。现场配置与生产历史先备份；不要用演示配置直接覆盖现场配置。新状态位于配置目录的 `production/`，旧历史位于 `schedules/`，都不会上传 GitHub。

## 技术文档

- [Python 统一改造说明](docs/technical/Python统一改造说明.md)：算法迁移、接口、脚本和验证结果。
- [排程算法与优化验证](docs/technical/排程算法与优化验证.md)：LotID 修复核验、原算法结构、多起点局部搜索与压力测试结果。
- [前端技术文档](docs/technical/前端技术文档.md)：模块、交互、排产规则、图示及现场限制。
- [后端技术文档](docs/technical/后端技术文档.md)：上传、排产接口、校验、Oracle 扩展、部署与安全。
- [MES 输入要求：EQP 与 PM](docs/technical/MES输入要求_EQP与PM.md)：prober 对应关系、维护对象字段、两次 PM 与指定时间重叠合并规则及结果示例。
- [MES 输入要求：生产快照](docs/technical/MES输入要求_生产快照.md)：新工作台的物料、交期、在制、库存、实际产出、规则、接口及运行边界。
- [运行环境与依赖说明](docs/technical/环境依赖说明.md)：Python、可选第三方库、浏览器和测试工具版本。
- [部署说明](部署说明.md)：跨平台一键启动、离线环境、维护与故障排查。
- [品牌素材来源](frontend/assets/brand/README.md)。

## 验证

```bash
python3 deploy.py check
python3 tools/check_fields.py
python3 -m unittest discover -s tests -v
node --test tests/frontend_state.test.js
python3 tools/verify_demo_scenarios.py
python3 -m py_compile backend/server.py backend/server_core.py backend/data_sources.py backend/diagnostics.py backend/scheduler.py deployment/manage.py
python3 tools/verify_metrics.py
python3 tools/benchmark_scheduler.py
```

Windows 可使用 `py -3 -m unittest discover -s tests -v`。部署进程身份检查用例为 `tests/test_deployment.py`；Windows 实机双击及 Linux systemd 安装仍需在目标环境验收。

排产规则回归为 `tests/test_scheduler.py`；全算例独立核验脚本为 `tools/verify_metrics.py`。迁移前后的详细对应关系见 [Python 统一改造说明](docs/technical/Python统一改造说明.md)。

当前是启发式原型，并非最优解求解器。现场上线前需完成认证授权、滚动快照、真实路线、日历、并发与安全加固；详见技术文档。
