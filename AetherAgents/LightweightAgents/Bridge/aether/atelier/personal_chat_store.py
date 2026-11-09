"""Own one human account's SQLite chat cartridge and one-time JSON import.

Never access agent memory, accept caller-selected owners/paths, publish knowledge,
or delete legacy transcripts. HTTP authorization belongs to the account boundary.
"""
from contextlib import contextmanager
from copy import deepcopy
import json
from pathlib import Path
import re
import sqlite3
import threading
import time
import uuid

ID = re.compile(r'^[0-9a-f]{32}$')
KINDS = ('knowledge', 'preference', 'decision', 'question', 'summary')


def timestamp():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


class PersonalChatStore:
    def __init__(self, root, owner):
        if not isinstance(owner, str) or not ID.fullmatch(owner):
            raise ValueError('A personal cartridge needs a valid account owner.')
        self.root, self.owner = Path(root), owner
        self.path = self.root / 'personal-chat.sqlite3'
        self.lock = threading.RLock()
        self.root.mkdir(parents=True, exist_ok=True)
        with self.db(True) as db:
            db.execute('CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS chats (id TEXT PRIMARY KEY, body TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS notes (id TEXT PRIMARY KEY, body TEXT NOT NULL)')
            stored = db.execute("SELECT value FROM meta WHERE key='owner'").fetchone()
            if stored and stored[0] != owner:
                raise ValueError('This cartridge belongs to another account.')
            for key, value in [('owner', owner), ('revision', '0'), ('recall', 'true')]:
                db.execute('INSERT OR IGNORE INTO meta VALUES (?,?)', (key, value))
            if not db.execute("SELECT 1 FROM meta WHERE key='imported'").fetchone():
                count = 0
                for path in sorted(self.root.glob('*.json')):
                    if not ID.fullmatch(path.stem):
                        continue
                    with path.open('rb') as stream:
                        raw = stream.read(16 * 1024 * 1024 + 1)
                    if len(raw) > 16 * 1024 * 1024:
                        raise ValueError('A legacy chat is too large to import. Original files are unchanged.')
                    doc = json.loads(raw)
                    if doc.get('id') != path.stem or not isinstance(doc.get('messages'), list):
                        raise ValueError('A legacy chat is invalid. Original files are unchanged.')
                    db.execute('INSERT OR IGNORE INTO chats VALUES (?,?)', (path.stem, json.dumps(doc)))
                    count += 1
                db.execute("INSERT INTO meta VALUES ('imported',?)", (str(count),))

    @contextmanager
    def db(self, write=False):
        with self.lock:
            connection = sqlite3.connect(self.path, timeout=10)
            try:
                connection.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
                yield connection
                connection.commit()
            except sqlite3.Error as exc:
                connection.rollback()
                raise ValueError('The personal cartridge could not be accessed. Close other writers and retry; preserve the file for recovery if the problem continues.') from exc
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()

    def read_chat(self, cid):
        with self.db() as db:
            row = db.execute('SELECT body FROM chats WHERE id=?', (cid,)).fetchone()
            if not row:
                raise ValueError('This chat was not found.')
            return json.loads(row[0])

    def write_chat(self, doc):
        with self.db(True) as db:
            db.execute('INSERT OR REPLACE INTO chats VALUES (?,?)', (doc['id'], json.dumps(doc)))

    def chat_ids(self):
        with self.db() as db:
            return [r[0] for r in db.execute('SELECT id FROM chats')]

    def snapshot(self):
        with self.db() as db:
            meta = dict(db.execute('SELECT key,value FROM meta'))
            return dict(owner=self.owner, revision=int(meta['revision']), recall=meta['recall']=='true',
                        imported_chats=int(meta['imported']),
                        notes=sorted([json.loads(r[0]) for r in db.execute('SELECT body FROM notes')],
                                     key=lambda n: (n['updated_at'],n['id']), reverse=True))

    def change(self, body, source=None):
        with self.db(True) as db:
            revision = int(db.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0])
            if type(body.get('revision')) is not int or body['revision'] != revision:
                raise ValueError('Your cartridge changed in another window. Refresh before saving.')
            action = body.get('action')
            if action == 'settings':
                if type(body.get('recall')) is not bool:
                    raise ValueError('Choose whether personal recall is enabled.')
                db.execute("UPDATE meta SET value=? WHERE key='recall'", (json.dumps(body['recall']),))
            elif action in ('save', 'archive', 'restore'):
                nid = body.get('id')
                row = db.execute('SELECT body FROM notes WHERE id=?', (nid,)).fetchone() if nid else None
                if nid and not row:
                    raise ValueError('This saved item was not found in your cartridge.')
                if not row and action != 'save':
                    raise ValueError('Choose a saved item.')
                note = json.loads(row[0]) if row else dict(id=uuid.uuid4().hex, created_at=timestamp(), archived=False, source=source)
                if action == 'save':
                    for key, limit in [('title',120), ('content',8000)]:
                        value = body.get(key)
                        if not isinstance(value,str) or not value.strip() or len(value)>limit:
                            raise ValueError(f'Enter {key} up to {limit} characters.')
                        note[key] = value.strip()
                    if body.get('kind') not in KINDS or any(type(body.get(k)) is not bool for k in ('use_in_chat','confirmed')):
                        raise ValueError('Choose a knowledge type, review status and recall setting.')
                    if not row and db.execute('SELECT count(*) FROM notes').fetchone()[0]>=1000:
                        raise ValueError('This cartridge has reached its 1,000 saved-item limit.')
                    note.update(kind=body['kind'], use_in_chat=body['use_in_chat'], confirmed=body['confirmed'])
                else:
                    note['archived'] = action == 'archive'
                note['updated_at'] = timestamp()
                db.execute('INSERT OR REPLACE INTO notes VALUES (?,?)', (note['id'],json.dumps(note)))
            else:
                raise ValueError('Choose a supported personal-cartridge action.')
            db.execute("UPDATE meta SET value=? WHERE key='revision'", (str(revision+1),))
        return self.snapshot()

    def export(self):
        with self.db() as db:
            notes = [json.loads(r[0]) for r in db.execute('SELECT body FROM notes')]
            chats = []
            for row in db.execute('SELECT body FROM chats'):
                doc = json.loads(row[0])
                clean = {k:deepcopy(doc.get(k)) for k in ('id','title','tags','deleted','created_at','updated_at','messages','artifacts')}
                for message in clean['messages']:
                    message.pop('history',None)
                chats.append(clean)
            value = dict(format='aether-personal-chat',version=1,owner=self.owner,exported_at=timestamp(),notes=notes,chats=chats)
            if len(json.dumps(value).encode())>32*1024*1024:
                raise ValueError('This export exceeds 32 MiB. Export individual chats instead.')
            return value
