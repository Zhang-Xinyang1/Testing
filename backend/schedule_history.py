"""Immutable, local schedule snapshots for viewing and comparing past plans."""
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import threading
from datetime import datetime, timezone, timedelta
import uuid

LOCK = threading.Lock()
ID_PATTERN = r'[0-9]{8}T[0-9]{12}-[a-f0-9]{8}'


def input_identity(data):
    clean = dict(data)
    clean['meta'] = {k: v for k, v in data.get('meta', {}).items()
                     if k not in ('loadedAt', 'configPath')}
    return hashlib.sha256(json.dumps(clean, ensure_ascii=False, sort_keys=True,
                                    allow_nan=False).encode()).hexdigest()


def directory(config_path):
    return Path(config_path).resolve().parent / 'schedules'


def _write(path, value):
    fd, temporary = tempfile.mkstemp(dir=path.parent, suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def save_schedule(config_path, body):
    if not isinstance(body, dict):
        raise ValueError('方案必须包含输入与结果')
    data, result = body.get('input'), body.get('result')
    if not isinstance(data, dict) or not isinstance(result, dict):
        raise ValueError('仅支持保存已完成的排产方案')
    if not all(isinstance(result.get(key), list) for key in
               ('lots', 'machines', 'chambers', 'cpTasks', 'batches', 'productStats')):
        raise ValueError('排产结果不完整，不能保存为历史方案')
    if not isinstance(result.get('metrics'), dict) or result.get('start') is None:
        raise ValueError('排产结果缺少指标或计划起点')
    now = datetime.now(timezone(timedelta(hours=8)))
    key = now.strftime('%Y%m%dT%H%M%S%f') + '-' + uuid.uuid4().hex[:8]
    metrics = result['metrics']
    summary = {'id': key, 'time': now.isoformat(), 'month': data.get('meta', {}).get('month'),
               'source': data.get('meta', {}).get('source', ''),
               'inputHash': input_identity(data), 'options': body.get('options') or {},
               'completedWafers': metrics.get('completedWafers', 0),
               'minProductRate': metrics.get('minProductRate', 0),
               'unscheduledLots': metrics.get('unscheduledLots', 0),
               'runMs': result.get('runMs', 0)}
    snapshot = {'version': 1, 'summary': summary, 'input': data,
                'options': body.get('options') or {}, 'result': result}
    # Validate JSON before creating files; never publish an incomplete summary.
    json.dumps(snapshot, allow_nan=False)
    root = directory(config_path)
    with LOCK:
        root.mkdir(parents=True, exist_ok=True)
        _write(root / (key + '.json'), snapshot)
        _write(root / (key + '.meta.json'), summary)
    return summary


def list_schedules(config_path):
    root = directory(config_path)
    if not root.is_dir():
        return []
    summaries = []
    for path in sorted(root.iterdir(), reverse=True):
        if re.fullmatch(ID_PATTERN + r'\.meta\.json', path.name):
            try:
                summaries.append(json.loads(path.read_text(encoding='utf-8')))
            except (OSError, ValueError):
                continue
            if len(summaries) >= 50:
                break
    return summaries


def read_schedule(config_path, key):
    if not re.fullmatch(ID_PATTERN, key):
        raise ValueError('方案编号无效')
    path = directory(config_path) / (key + '.json')
    if not path.is_file():
        raise ValueError('方案不存在，请刷新列表')
    return json.loads(path.read_text(encoding='utf-8'))
