"""Single-host transactional production state, source banks and immutable plans.

Legacy JSON files are imported once and left untouched for rollback. A revision
comparison inside BEGIN IMMEDIATE also protects separate server processes.
"""
import copy
import json
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path


class StateConflict(ValueError):
    pass


def dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def directory(config):
    return Path(config).resolve().parent / 'production'


@contextmanager
def connection(config):
    root = directory(config)
    root.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(root / 'production.sqlite3', timeout=10, isolation_level=None)
    try:
        db.execute('PRAGMA synchronous=FULL')
        db.execute('BEGIN IMMEDIATE')
        db.execute('CREATE TABLE IF NOT EXISTS workspace (id INTEGER PRIMARY KEY CHECK(id=1), version INTEGER NOT NULL, body TEXT NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS sources (source TEXT PRIMARY KEY, body TEXT NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS plans (id TEXT PRIMARY KEY, body TEXT NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS snapshots (source TEXT NOT NULL, id TEXT NOT NULL, digest TEXT NOT NULL, PRIMARY KEY(source,id))')
        db.execute('CREATE TABLE IF NOT EXISTS metadata (name TEXT PRIMARY KEY, value TEXT NOT NULL)')
        if not db.execute("SELECT 1 FROM metadata WHERE name='legacyImported'").fetchone():
            # Import everything in this transaction. Corrupt files fail visibly;
            # neither the files nor a partially imported DB become authoritative.
            path = root / 'state.json'
            if path.exists():
                state = json.loads(path.read_text(encoding='utf-8'))
                db.execute('INSERT INTO workspace VALUES (1,0,?)', (dumps(state),))
                source = (state.get('snapshot') or {}).get('source') or state.get('selectedSource')
                if source:
                    db.execute('INSERT OR REPLACE INTO sources VALUES (?,?)', (source,dumps(state)))
            for source in ('excel','mes','demo'):
                path = root / 'sources' / (source+'.json')
                if path.exists():
                    db.execute('INSERT OR REPLACE INTO sources VALUES (?,?)', (source,dumps(json.loads(path.read_text(encoding='utf-8')))))
            for path in root.glob('*.json'):
                if re.fullmatch(r'[a-f0-9]{32}',path.stem):
                    db.execute('INSERT INTO plans VALUES (?,?)', (path.stem,dumps(json.loads(path.read_text(encoding='utf-8')))))
            db.execute("INSERT INTO metadata VALUES ('legacyImported','1')")
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def read(config, default):
    with connection(config) as db:
        row = db.execute('SELECT version,body FROM workspace WHERE id=1').fetchone()
        result = json.loads(row[1]) if row else default()
        result['_storeVersion'] = row[0] if row else -1
        return result


def source(config, name):
    with connection(config) as db:
        row = db.execute('SELECT body FROM sources WHERE source=?',(name,)).fetchone()
        return json.loads(row[0]) if row else None


def _save_plan(db, plan, adopted):
    row = db.execute('SELECT body FROM plans WHERE id=?',(plan['id'],)).fetchone()
    if row:
        previous = json.loads(row[0])
        stripped = lambda p:{k:v for k,v in p.items() if k not in ('adoptedAt','adoptReason')}
        if stripped(previous) != stripped(plan):
            raise StateConflict('方案编号对应不同内容，拒绝覆盖')
        if previous.get('adoptedAt') or not adopted:
            return
    db.execute('INSERT OR REPLACE INTO plans VALUES (?,?)',(plan['id'],dumps(plan)))


def save(config, state, plan=None, adopted=False, snapshot_identity=None):
    body = copy.deepcopy(state)
    expected = body.pop('_storeVersion', -1)
    with connection(config) as db:
        row = db.execute('SELECT version FROM workspace WHERE id=1').fetchone()
        actual = row[0] if row else -1
        if actual != expected:
            raise StateConflict('生产状态已被其他操作更新，请刷新后重试')
        if snapshot_identity:
            source_name, snapshot_id, digest = snapshot_identity
            row = db.execute('SELECT digest FROM snapshots WHERE source=? AND id=?',(source_name,snapshot_id)).fetchone()
            if row and row[0] != digest:
                raise StateConflict('同一快照 ID 对应不同业务内容，请提供新的 snapshotId')
            db.execute('INSERT OR IGNORE INTO snapshots VALUES (?,?,?)',snapshot_identity)
        if plan:
            _save_plan(db,plan,adopted)
        db.execute('INSERT OR REPLACE INTO workspace VALUES (1,?,?)',(actual+1,dumps(body)))
        name = (body.get('snapshot') or {}).get('source') or body.get('selectedSource')
        if name in ('excel','mes','demo'):
            db.execute('INSERT OR REPLACE INTO sources VALUES (?,?)',(name,dumps(body)))
    state['_storeVersion'] = actual+1
    return state


def read_plan(config, plan_id):
    with connection(config) as db:
        row = db.execute('SELECT body FROM plans WHERE id=?',(plan_id,)).fetchone()
        if not row:
            raise ValueError('方案记录不存在')
        return json.loads(row[0])
