#!/usr/bin/env python3
"""Development server launcher; replaces run.sh."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
HOST = os.environ.get('HOST', '0.0.0.0')
PORT = os.environ.get('PORT', '8080')
os.environ.setdefault('PYTHONUTF8', '1')
os.execv(sys.executable, [
    sys.executable, '-u', str(ROOT / 'backend/server.py'),
    '--host', HOST, '--port', str(PORT),
])