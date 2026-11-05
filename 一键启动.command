#!/usr/bin/env bash
# macOS 双击入口：.sh 在 Finder 中默认由编辑器打开，.command 才会在终端执行。
set -u
ROOT="$(cd "$(dirname "$0")" && pwd)"

if [ ! -f "$ROOT/一键启动.sh" ]; then
  echo "缺少 一键启动.sh，无法启动。请确认已完整上传 Testing 目录。"
  if [ -t 0 ]; then
    read -r -p "按回车关闭窗口..." _ || true
  fi
  exit 1
fi

cd "$ROOT" || exit 1
exec bash "$ROOT/一键启动.sh" "$@"