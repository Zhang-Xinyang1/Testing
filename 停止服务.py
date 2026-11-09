#!/usr/bin/env python3
"""Stop the background service; run with: python3 停止服务.py"""
from launcher import run

if __name__ == '__main__':
    raise SystemExit(run('stop'))