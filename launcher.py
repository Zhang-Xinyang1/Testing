#!/usr/bin/env python3
"""Shared console wrapper for the one-click Python entry points."""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def pause():
    if sys.stdin.isatty() and os.environ.get('XMC_NO_PAUSE') != '1':
        try:
            input('按回车关闭窗口...')
        except EOFError:
            pass


def run(action):
    result = subprocess.call([sys.executable, str(ROOT / 'deploy.py'), action])
    if result == 0:
        if action == 'start':
            print('服务已在后台运行，请使用上方访问地址。')
        elif action == 'stop':
            print('服务已停止。')
        else:
            print('服务已重启并通过前后端校验。')
        if action != 'stop' and os.environ.get('XMC_OPEN_BROWSER') != '0':
            print('桌面环境会自动打开浏览器；SSH/无界面环境请使用上方访问地址。')
    else:
        print('操作未成功，请根据上方提示处理后重试。')
        print('日志：%s' % (ROOT / '.runtime/server.log'))
    pause()
    return result