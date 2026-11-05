#!/usr/bin/env bash
set -u
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT" || exit 1

pause_before_exit() {
  if [ -t 0 ]; then
    read -r -p "按回车关闭窗口..." _ || true
  fi
}

if [ ! -f "$ROOT/deploy.sh" ]; then
  echo "缺少 deploy.sh，无法启动。请确认已完整上传 Testing 目录。"
  pause_before_exit
  exit 1
fi

bash "$ROOT/deploy.sh" start
result=$?
if [ "$result" -eq 0 ]; then
  case "$(printf '%s' "${XMC_OPEN_BROWSER:-}" | tr '[:upper:]' '[:lower:]')" in
    0|false|no|off)
      echo "服务已在后台运行，请使用上方访问地址。"
      ;;
    *)
      if [ -n "${SSH_CONNECTION:-}" ] || [ -n "${SSH_TTY:-}" ]; then
        echo "服务已在后台运行。当前为 SSH/无界面环境，请在浏览器打开上方访问地址。"
      else
        echo "服务已在后台运行。桌面环境会自动打开浏览器；无界面环境请使用上方访问地址。"
      fi
      ;;
  esac
else
  echo "启动未成功，请根据上方提示处理后重试。"
  echo "日志：$ROOT/.runtime/server.log"
fi
pause_before_exit
exit "$result"
