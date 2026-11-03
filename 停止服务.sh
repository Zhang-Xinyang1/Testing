#!/usr/bin/env bash
set -u
ROOT="$(cd "$(dirname "$0")" && pwd)"
bash "$ROOT/deploy.sh" stop
result=$?
if [ "$result" -eq 0 ]; then
  echo "服务已停止。"
else
  echo "停止未成功，请根据上方提示处理后重试。"
fi
if [ -t 0 ]; then
  read -r -p "按回车关闭窗口..." _ || true
fi
exit "$result"