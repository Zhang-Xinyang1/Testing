#!/usr/bin/env python3
"""Validate a generated PPTX package and copy it to the final output path.

This replaces .artifacts/finalize_rules.mjs, which depended on a private
artifact-tool installation. It performs package integrity checks with the
Python standard library only.
"""
import argparse
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / '.artifacts/rules-draft.pptx'
DEFAULT_OUTPUT = ROOT / 'output/Testing排程_wafer口径与七种路线.pptx'
REQUIRED_PARTS = ('[Content_Types].xml', 'ppt/presentation.xml')


def validate(path):
    if not path.is_file():
        raise ValueError('PPTX 不存在：' + str(path))
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        for part in REQUIRED_PARTS:
            if part not in names:
                raise ValueError('PPTX 缺少必要部件：' + part)
        if not any(name.startswith('ppt/slides/slide') and name.endswith('.xml')
                   for name in names):
            raise ValueError('PPTX 没有幻灯片')
        if any(name.startswith('/') or '..' in Path(name).parts for name in names):
            raise ValueError('PPTX 包含不安全路径')
        total = sum(entry.file_size for entry in archive.infolist())
        if total > 200 * 1024 * 1024:
            raise ValueError('PPTX 解压后过大')
        bad = archive.testzip()
        if bad:
            raise ValueError('PPTX 部件损坏：' + bad)


def main():
    parser = argparse.ArgumentParser(description='校验并输出规则 PPTX')
    parser.add_argument('--source', default=str(DEFAULT_SOURCE))
    parser.add_argument('--output', default=str(DEFAULT_OUTPUT))
    args = parser.parse_args()
    source, output = Path(args.source), Path(args.output)
    try:
        validate(source)
    except (ValueError, zipfile.BadZipFile) as error:
        print('失败：' + str(error), file=sys.stderr)
        return 1
    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, output)
    print('已输出', output)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())