# XMC 测试排产

读取历史 XLSX，配置机台、在制占用、PM 和借机，比较 Excel MPS 与算法月内完成量。原始 Excel 只读，配置独立保存，算法在浏览器运行。

## 服务器一键部署

上传整个 Testing 文件夹：Windows 双击 `一键启动.bat`，Linux 执行 `bash 一键启动.sh`。启动后访问 `http://服务器IP:8080`。后台运行、离线检查环境，详细说明见 [部署说明](部署说明.md)。需预装 Python 3.9+。

| 操作 | Windows | Linux / macOS |
|---|---|---|
| 启动 | 双击 `一键启动.bat` | `bash 一键启动.sh` |
| 停止 | 双击 `停止服务.bat` | `bash deploy.sh stop` |
| 重启 | 双击 `重启服务.bat` | `bash deploy.sh restart` |
| 状态 | `deploy.bat status` | `bash deploy.sh status` |
| 日志 | `deploy.bat logs` | `bash deploy.sh logs` |

Windows 启动成功会自动打开本机浏览器；其他电脑使用服务器 IP。关闭启动窗口后服务继续运行，但普通后台模式不保证退出登录或重启后恢复。Linux systemd 开机自启为可选操作，Windows 暂未集成开机自启。

## 开发启动

```bash
./run.sh
# 打开 http://127.0.0.1:8080
```

请使用后端地址，不是 IDE 的静态 Live Server。静态模式只能看内置算例，不能上传保存。

## 使用

1. 数据配置页选择 `.xlsx`，上传只读副本。
2. 按需调整 Sheet/字段映射、机台、PM、借机、在制占用。
3. 点击保存并排产。采用新文件原有资源时用“恢复参考配置”，该操作会清空手工覆盖，含在制快照。
4. 查看 MPS 和甘特图，或导出 CSV。
5. 排产诊断页查看问题编号、内网明细和运行对比；诊断不提供导出或外传。

Oracle 配置入口和适配器已预留，但尚未连接数据库。密码只配置环境变量名称。

## 技术文档

- [前端技术文档](docs/technical/前端技术文档.md)：模块、交互、排产规则、图示及现场限制。
- [后端技术文档](docs/technical/后端技术文档.md)：上传、接口、校验、Oracle 扩展、部署与安全。
- [部署说明](部署说明.md)：跨平台一键启动、离线环境、维护与故障排查。
- [品牌素材来源](frontend/assets/brand/README.md)。

## 验证

```bash
python3 -m unittest discover -s tests -v
python3 -m py_compile backend/server.py backend/server_core.py backend/data_sources.py backend/diagnostics.py deployment/manage.py
```

Windows 可使用 `py -3 -m unittest discover -s tests -v`。部署进程身份检查用例为 `tests/test_deployment.py`；Windows 实机双击及 Linux systemd 安装仍需在目标环境验收。

JavaScript 回归用例为 `tests/scheduler.test.js`，在加载 `frontend/assets/scheduler.js` 后执行。

当前是启发式原型，并非最优解求解器。现场上线前需完成认证授权、滚动快照、真实路线、日历、并发与安全加固；详见技术文档。
