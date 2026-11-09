#!/usr/bin/env python3
"""Restart the background service; run with: python3 重启服务.py"""
from launcher import run

if __name__ == '__main__':
    raise SystemExit(run('restart'))