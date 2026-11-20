#!/bin/bash
# macOS 双击启动：在 Finder 中双击本文件即可启动服务并打开浏览器。
cd "$(dirname "$0")" || exit 1

PYTHON="${PYTHON_BIN:-}"
if [ -z "$PYTHON" ]; then
  for candidate in python3 /usr/local/bin/python3 /opt/homebrew/bin/python3 /usr/bin/python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
      PYTHON="$(command -v "$candidate")"
      break
    fi
  done
fi

if [ -z "$PYTHON" ]; then
  echo "未找到 Python 3.9+。请安装 Python 3 后重试，或设置 PYTHON_BIN 指向解释器。"
  read -r -p "按回车关闭窗口..." _
  exit 1
fi

XMC_NO_PAUSE=1 "$PYTHON" 一键启动.py
status=$?

if [ "$status" -ne 0 ]; then
  echo
  echo "启动失败，退出码 $status。"
  echo "日志：$(pwd)/.runtime/server.log"
  read -r -p "按回车关闭窗口..." _
fi
exit "$status"