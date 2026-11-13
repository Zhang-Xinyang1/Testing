"""Generate the original large regression fixture without changing the demo."""
import atexit
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from build_case import build_rows, write_workbook
from server_core import load_config as real_load_config, build_payload as real_build_payload

_directory = tempfile.TemporaryDirectory(prefix='testing-legacy-case-')
atexit.register(_directory.cleanup)
_path = Path(_directory.name) / 'legacy.xlsx'
write_workbook(_path, build_rows()[0])


def load_config(path=None):
    if path is not None:
        return real_load_config(path)
    config = real_load_config()
    config['dataSource'].update(path=str(_path), month='2026-09')
    for name in ('lots', 'production'):
        config['dataSource']['sheetMap'].pop(name, None)
    config['scheduling']['bakeResources'] = dict(machines=7, chambers=4)
    config['scheduling']['platformType'] = {t:t for t in ('CP-A', 'CP-B', 'CP-C')}
    return config


def build_payload(path=None, config=None):
    if path is None and config is None:
        config = load_config()
    if path is None:
        return real_build_payload(config=config)
    return real_build_payload(path, config=config)
