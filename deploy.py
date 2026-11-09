#!/usr/bin/env python3
"""Cross-platform deployment entry point; replaces deploy.sh and deploy.bat.

Examples:
    python3 deploy.py start
    python3 deploy.py status
    python3 deploy.py check
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ACTIONS = ('start', 'stop', 'restart', 'status', 'check', 'logs', 'install-service')


def main():
    action = sys.argv[1] if len(sys.argv) > 1 else 'start'
    if action not in ACTIONS:
        print('未知操作：%s；可用：%s' % (action, '、'.join(ACTIONS)), file=sys.stderr)
        return 2
    environment = os.environ.copy()
    environment.setdefault('PYTHONUTF8', '1')
    python_bin = os.environ.get('PYTHON_BIN') or sys.executable
    return subprocess.call(
        [python_bin, str(ROOT / 'deployment/manage.py'), action],
        cwd=str(ROOT), env=environment)


if __name__ == '__main__':
    raise SystemExit(main())