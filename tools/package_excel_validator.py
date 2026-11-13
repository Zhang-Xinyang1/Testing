#!/usr/bin/env python3
"""Build one portable .py containing this version's exact import rules."""
import argparse
import base64
import io
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parents[1]
FILES = ('tools/validate_excel.py', 'tools/build_data.py', 'backend/server_core.py',
         'backend/input_rules.py', 'backend/platforms.py', 'backend/data_sources.py',
         'config/route_patterns.json')
BOOTSTRAP = '''#!/usr/bin/env python3
"""Testing_11101136 Excel audit, Python 3.9+, standard library only.
Use --config SERVER/config/config.json --excel DATA.xlsx --output REPORTS.
No audit errors are printed to the terminal. Read REPORTS/excel_audit_*/report.txt.
"""
import base64
import io
from pathlib import Path
import runpy
import sys
import tempfile
from zipfile import ZipFile

PAYLOAD = {payload!r}

def run():
    args = sys.argv[1:]
    config_value = next((a.split('=',1)[1] for a in args if a.startswith('--config=')), None)
    if '--config' in args and args.index('--config')+1 < len(args):
        config_value = args[args.index('--config')+1]
    if config_value and '--project-root' not in args and not any(a.startswith('--project-root=') for a in args):
        config = Path(config_value).resolve()
        root = config.parent.parent if config.parent.name.lower()=='config' else config.parent
        args += ['--project-root',str(root)]
    if not config_value and not any(a in ('-h','--help','--version') for a in args):
        # A missing real config produces a TXT report, never a demo-data success.
        args += ['--config', str(Path.cwd()/'config/config.json')]
    with tempfile.TemporaryDirectory(prefix='testing_excel_audit_') as folder:
        root = Path(folder).resolve()
        with ZipFile(io.BytesIO(base64.b64decode(PAYLOAD))) as archive:
            for member in archive.infolist():
                target = (root/member.filename).resolve()
                if root not in target.parents: raise ValueError('Invalid bundled path')
                target.parent.mkdir(parents=True,exist_ok=True)
                target.write_bytes(archive.read(member))
        namespace = runpy.run_path(str(root/'tools/validate_excel.py'), run_name='portable_validator')
        return namespace['main'](args)

if __name__ == '__main__':
    raise SystemExit(run())
'''


def build(destination):
    data = io.BytesIO()
    with ZipFile(data, 'w', ZIP_DEFLATED) as archive:
        for relative in FILES:
            archive.writestr(relative, (ROOT / relative).read_bytes())
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(BOOTSTRAP.format(payload=base64.b64encode(data.getvalue()).decode('ascii')), encoding='utf-8')
    return destination


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', help='独立 .py 文件输出路径')
    build(parser.parse_args().output)
