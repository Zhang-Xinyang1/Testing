#!/usr/bin/env bash
set -u
ROOT="$(cd "$(dirname "$0")" && pwd)"
bash "$ROOT/deploy.sh" restart
result=$?
if [ "$result" -eq 0 ]; then
  if [ "${XMC_OPEN_BROWSER:-}" = "0" ]; then
    echo "服务已重启并通过前后端校验，请使用上方访问地址。"
  else
    echo "服务已重启并通过前后端校验。桌面环境会自动打开浏览器；SSH 或无界面服务器请使用上方访问地址。"
  fi
else
  echo "重启未成功，请根据上方提示处理后重试。"
fi
if [ -t 0 ]; then
  read -r -p "按回车关闭窗口..." _ || true
fi
exit "$result"