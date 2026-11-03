#!/usr/bin/env bash
set -euo pipefail
export PYTHONUTF8=1
ROOT="$(cd "$(dirname "$0")" && pwd)"
if [ -n "${PYTHON_BIN:-}" ]; then
  PYTHON="$PYTHON_BIN"
elif [ -x "$ROOT/.venv/bin/python3" ]; then
  PYTHON="$ROOT/.venv/bin/python3"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON="$(command -v python3)"
else
  echo "需要 Python 3.9+。请使用批准的离线介质安装，或设置 PYTHON_BIN。"
  exit 1
fi
if ! "$PYTHON" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1; then
  echo "需要 Python 3.9+；当前解释器：$PYTHON"
  "$PYTHON" -V 2>&1 || true
  exit 1
fi
exec "$PYTHON" "$ROOT/deployment/manage.py" "$@"
