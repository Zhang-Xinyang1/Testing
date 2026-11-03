#!/usr/bin/env bash
set -u
ROOT="$(cd "$(dirname "$0")" && pwd)"
bash "$ROOT/deploy.sh" start
result=$?
if [ "$result" -eq 0 ]; then
  echo "服务已在后台运行，可以关闭此窗口。请在浏览器打开上方访问地址。"
else
  echo "启动未成功，请根据上方提示处理后重试。"
fi
if [ -t 0 ]; then
  read -r -p "按回车关闭窗口..." _ || true
fi
exit "$result"
