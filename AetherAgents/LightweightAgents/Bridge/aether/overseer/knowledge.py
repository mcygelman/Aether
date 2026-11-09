"""Read existing shared sources and preserve Librarian-selected text with provenance.

No ingestion service, personal-memory crawl, network fetch, or autonomous source
selection. The mind selects ids; the host supplies exact bytes and hashes.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import sqlite3

from aether.tasker._files import file_lock
from aether.knowledge.school import DEFAULT_CATALOG_PATH

MAX_SOURCE_BYTES = 512_000
MAX_SOURCES = 2000
TEXT_SUFFIXES = {'.txt', '.md', '.json', '.jsonl', '.py', '.csv', '.html', '.yaml', '.yml', '.toml', '.sql'}


def inside(path, root):
    path, root = Path(path).resolve(), Path(root).resolve()
    return path == root or root in path.parents


def permitted(path, root):
    return (inside(path, root) and path.is_file() and path.suffix.lower() in TEXT_SUFFIXES
            and path.name != '.sealbox_key' and not any(p.lower() in ('credentials', '.ssh', '.aws') for p in path.parts))


class Sources:
    def __init__(self, sprint):
        self.sprint = sprint
        self.items = {}
        self.reads = {}
        self.warnings = []
        self._catalog()

    def add(self, path, root, title, kind, *, content=None, source=None):
        path = Path(path)
        if content is None and not permitted(path, root):
            return
        if len(self.items) >= MAX_SOURCES:
            if not self.warnings:
                self.warnings.append('Source listing reached its limit; narrow the configured collections.')
            return
        source = source or str(path.resolve())
        sid = hashlib.sha256(source.encode('utf-8')).hexdigest()[:24]
        self.items[sid] = dict(id=sid, title=title, kind=kind, source=source, path=path, root=Path(root), content=content)

    def _catalog(self):
        root = self.sprint.data_root.resolve()
        retained = self.sprint.root / 'knowledge.sqlite'
        if retained.is_file():
            with sqlite3.connect(retained.resolve().as_uri() + '?mode=ro', uri=True) as conn:
                for title, kind, source, text in conn.execute('SELECT title,kind,source,text FROM items ORDER BY id'):
                    self.add(retained, self.sprint.root, title, kind, content=text, source=source)
        for cat in sorted((root / 'libraries').glob('*/catalog.json')):
            if not inside(cat, root / 'libraries'):
                continue
            data = json.loads(cat.read_text(encoding='utf-8'))
            for ident, item in data.get('entries', {}).items():
                item_root = cat.parent / 'items' / ident
                if not inside(item_root, cat.parent / 'items') or not inside(item_root, root / 'libraries'):
                    continue
                for f in item.get('files', []):
                    self.add(item_root / f['name'], item_root, item.get('title', ident), item.get('kind', 'note'))
        configured = self.sprint.record().get('teachframe_catalog')
        installed = root / 'TeachFrames/CATALOG.json'
        cat = Path(configured) if configured else installed if installed.exists() else DEFAULT_CATALOG_PATH
        if cat.exists():
            data = json.loads(cat.read_text(encoding='utf-8'))
            for entry in data.get('entries', []):
                path = cat.parent.parent / entry['path']
                self.add(path, cat.parent, entry['name'], 'teachframe')
        for path in sorted((root / 'sprints').glob('*/sprint.json')):
            if not inside(path, root / 'sprints'):
                continue
            data = json.loads(path.read_text(encoding='utf-8'))
            if data.get('state') not in ('closed-pass', 'closed-fail'):
                continue
            folder = path.parent
            self.add(folder / 'SPRINT_OVERVIEW.md', folder, data['id'] + ' overview', 'sprint-overview')
            db = folder / 'tasker.sqlite'
            if db.is_file() and inside(db, folder):
                with sqlite3.connect(db.resolve().as_uri() + '?mode=ro', uri=True) as conn:
                    conn.row_factory = sqlite3.Row
                    tasks = [dict(r) for r in conn.execute('SELECT number,title,prompt,status,assigned_to,note FROM tasks ORDER BY number')]
                    deps = [dict(r) for r in conn.execute('SELECT task,depends_on FROM dependencies ORDER BY task,depends_on')]
                self.add(db, folder, data['id'] + ' task list', 'tasker',
                         content=json.dumps(dict(tasks=tasks, dependencies=deps), ensure_ascii=False, sort_keys=True),
                         source=str(db.resolve()) + '#tasks-and-dependencies')
            if inside(folder / 'outputs', folder):
                for p in sorted((folder / 'outputs').rglob('*')):
                    self.add(p, folder / 'outputs', data['id'] + ': ' + p.name, 'sprint-output')

    def search(self, query='', limit=12):
        wanted = set(re.findall(r'\w+', query.lower()))
        ranked = []
        for item in self.items.values():
            hay = set(re.findall(r'\w+', (item['title'] + ' ' + item['source'] + ' ' + item['kind']).lower()))
            score = len(wanted & hay)
            if not wanted or score:
                ranked.append((score, item))
        ranked.sort(key=lambda x: (-x[0], x[1]['id']))
        return [dict((k, i[k]) for k in ('id', 'title', 'kind', 'source')) for _, i in ranked[:min(max(1, limit), 30)]]

    def material(self, sid):
        item = self.items[sid]
        if item['content'] is not None:
            text = item['content']
            raw = text.encode('utf-8')
        else:
            if not permitted(item['path'], item['root']):
                raise PermissionError('source moved outside its shared collection')
            with item['path'].open('rb') as stream:
                raw = stream.read(MAX_SOURCE_BYTES + 1)
            text = raw.decode('utf-8')
        if len(raw) > MAX_SOURCE_BYTES:
            raise ValueError('source exceeds the bounded reader; select a smaller source')
        if '\x00' in text:
            raise ValueError('source is not plain text')
        result = {k: item[k] for k in ('id', 'title', 'kind', 'source')}
        result.update(sha256=hashlib.sha256(raw).hexdigest(), text=text)
        self.reads[sid] = {k: result[k] for k in ('id', 'source', 'sha256')}
        return result

    def read(self, sid, offset=0):
        material = self.material(sid)
        offset = max(0, int(offset))
        text = material.pop('text')
        return dict(material, length=len(text), offset=offset, text=text[offset:offset + 8000],
                    next_offset=offset + 8000 if len(text) > offset + 8000 else None)

    def select(self, sid):
        material = self.material(sid)
        path = self.sprint.root / 'knowledge.sqlite'
        with file_lock(path.with_suffix('.lock')):
            with sqlite3.connect(path) as conn:
                conn.executescript('''
                    CREATE TABLE IF NOT EXISTS items(id TEXT PRIMARY KEY, title TEXT NOT NULL,
                      kind TEXT NOT NULL, source TEXT NOT NULL, sha256 TEXT NOT NULL, text TEXT NOT NULL);
                    CREATE VIRTUAL TABLE IF NOT EXISTS items_fts USING fts5(id UNINDEXED, title, text);
                ''')
                conn.execute('INSERT OR REPLACE INTO items VALUES (:id,:title,:kind,:source,:sha256,:text)', material)
                conn.execute('DELETE FROM items_fts WHERE id=?', (sid,))
                conn.execute('INSERT INTO items_fts(id,title,text) VALUES (:id,:title,:text)', material)
        return {k: v for k, v in material.items() if k != 'text'}
