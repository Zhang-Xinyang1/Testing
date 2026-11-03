# XMC 测试排产

读取历史 XLSX，配置机台、在制占用、PM 和借机，比较 Excel MPS 与算法月内完成量。原始 Excel 只读，配置独立保存，算法在浏览器运行。

## 启动

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

Oracle 配置入口和适配器已预留，但尚未连接数据库。密码只配置环境变量名称。

## 技术文档

- [前端技术文档](docs/technical/前端技术文档.md)：模块、交互、排产规则、图示及现场限制。
- [后端技术文档](docs/technical/后端技术文档.md)：上传、接口、校验、Oracle 扩展、部署与安全。
- [品牌素材来源](frontend/assets/brand/README.md)。

## 验证

```bash
python3 -m unittest discover -s tests -v
python3 -m py_compile backend/server.py backend/server_core.py backend/data_sources.py
```

JavaScript 回归用例为 `tests/scheduler.test.js`，在加载 `frontend/assets/scheduler.js` 后执行。

当前是启发式原型，并非最优解求解器。现场上线前需完成认证授权、滚动快照、真实路线、日历、并发与安全加固；详见技术文档。
