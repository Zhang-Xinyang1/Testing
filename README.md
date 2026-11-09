# XMC 测试排产

读取历史 XLSX，配置机台、在制占用、PM 和借机，比较 Excel MPS 与算法月内完成量。原始 Excel 只读，配置独立保存；排产算法、测试与启动脚本均由 Python 运行，浏览器只负责展示和交互。

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

Windows 与带图形界面的 Linux/macOS 启动成功会自动打开本机浏览器；SSH/无界面服务器只打印访问地址。前端与 API 由同一个 Python 服务同源提供，排产通过 `POST /api/schedule` 在 Python 后端执行，无需 Node.js、npm 或前端构建。更新后端或配置后请执行重启；只更新前端静态资源时刷新页面即可。关闭启动窗口后服务继续运行，但普通后台模式不保证退出登录或重启后恢复。Linux systemd 开机自启为可选操作，Windows 暂未集成开机自启。

运行依赖清单见 [requirements.txt](requirements.txt)：Python 3.9+，运行时没有第三方包；`python-pptx` 仅作为 PPT 生成工具的可选依赖。

## 开发启动

```bash
python3 run.py
# 打开 http://127.0.0.1:8080
```

请使用后端地址，不是 IDE 的静态 Live Server。静态模式只展示内置数据，不能上传保存，也不执行排产；排产由 Python 后端通过 `/api/schedule` 完成。

## 使用

当前仅使用 `data/Testing调度算例_新版.xlsx`。新版按 2026-11-06 逐项确认、2026-11-09 修订 CP 工时口径的业务规则重建：72 个 PPN、11,000 wafer、三个 CP 机台组、31 个 CP Recipe、62 种针卡类型、119 条借机，7 台 OVEN × 4 腔。MPS 允许同一 PPN 多行（不同厂家或重复订单）；PPN 以第一个 `-` 前为准，`-` 后厂家后缀忽略，读取时按前缀汇总 wafer、合并 Platform，并用前缀匹配 flow.PROD。每 Lot 最多 25 片；没有实际 Lot 明细时默认满 25 片加尾批，当前共 476 Lot。实际 Lot 明细优先；同一 LotID 允许多行，按 LotID 第一个 `-` 前的前缀匹配 flow，`-` 后作为厂家标识不参与路线匹配，每行独立排产。BAKE 每批最多 4 Lot，无额外 wafer 总数限制；升温 2h、加工 24h、降温/换 Lot 2h，占腔 28h，Lot 在第 26h 放行。

规则详见 [业务确认清单](docs/排程业务规则_确认清单.md)。CP 按 RUN 资格及 Platform/TYPE 匹配，路线限定已确认七种模式并保留原表顺序，针卡数量充足、换类型 5 分钟，约束表为禁加工；CP 工时按 `Time`（分钟/wafer）× Lot 实际 wafer 数计算。可跨组 Lot 先按各组剩余 CP 机时预分配，优先使用非瓶颈组。MPS、月内完成量与各 PPN 达成率均按 wafer 统计；优化目标是各 PPN 月末达成率均衡。

**当前工作区没有真实 72 产品表，默认文件是新规则的合成验证算例。** CP 暂保留 27 台、三个组各 9 台，路线/工时/计划逐条内容为占位。合成 MPS 的 PPN 使用 `PPNxx-1` 形式，flow 的 `PROD` 使用 `PPNxx`，用于验证 `-` 后厂家后缀忽略逻辑。验证结果不能作为现场生产承诺。真实表替换后重新运行 `python3 tools/build_data.py` 并重启服务；需要重建合成算例时运行 `python3 tools/build_case.py`。

规则 PPT 与重跑结果在 `output/`；旧算例与旧 PPT 已删除。

1. 打开“数据配置 → 生产配置 → Excel 导入”，选择 `.xlsx` 上传只读副本；静态打开页面时该入口不可用，需通过后端地址访问。
2. 按需调整 Sheet/字段映射、机台、PM、借机、在制占用。
3. 点击保存并排产。采用新文件原有资源时用“恢复参考配置”，该操作会清空手工覆盖，含在制快照。
4. 查看 MPS 和甘特图，或导出 CSV。
5. 排产诊断页查看问题编号、内网明细和运行对比；诊断不提供导出或外传。

Oracle 配置入口和适配器已预留，但尚未连接数据库。密码只配置环境变量名称。

## 技术文档

- [Python 统一改造说明](docs/technical/Python统一改造说明.md)：算法迁移、接口、脚本和验证结果。
- [前端技术文档](docs/technical/前端技术文档.md)：模块、交互、排产规则、图示及现场限制。
- [后端技术文档](docs/technical/后端技术文档.md)：上传、排产接口、校验、Oracle 扩展、部署与安全。
- [运行环境与依赖说明](docs/technical/环境依赖说明.md)：Python、可选第三方库、浏览器和测试工具版本。
- [部署说明](部署说明.md)：跨平台一键启动、离线环境、维护与故障排查。
- [品牌素材来源](frontend/assets/brand/README.md)。

## 验证

```bash
python3 deploy.py check
python3 tools/check_fields.py
python3 -m unittest discover -s tests -v
python3 -m py_compile backend/server.py backend/server_core.py backend/data_sources.py backend/diagnostics.py backend/scheduler.py deployment/manage.py
python3 tools/verify_metrics.py
```

Windows 可使用 `py -3 -m unittest discover -s tests -v`。部署进程身份检查用例为 `tests/test_deployment.py`；Windows 实机双击及 Linux systemd 安装仍需在目标环境验收。

排产规则回归为 `tests/test_scheduler.py`；全算例独立核验脚本为 `tools/verify_metrics.py`。迁移前后的详细对应关系见 [Python 统一改造说明](docs/technical/Python统一改造说明.md)。

当前是启发式原型，并非最优解求解器。现场上线前需完成认证授权、滚动快照、真实路线、日历、并发与安全加固；详见技术文档。
