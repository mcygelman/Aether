"""Own transactional account/role/grant storage and lossless JSON import.

Never store plaintext passwords/API keys, migrate roadmaps, or change private data IDs.
"""
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3

from aether.integrity.sealbox import atomic_write_bytes

SCHEMA = (
    'CREATE TABLE IF NOT EXISTS options (id INTEGER PRIMARY KEY CHECK(id=1), suspended INTEGER NOT NULL)',
    'CREATE TABLE IF NOT EXISTS metadata (id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL)',
    'CREATE TABLE IF NOT EXISTS accounts (id TEXT PRIMARY KEY, username TEXT NOT NULL COLLATE NOCASE UNIQUE, name TEXT NOT NULL, role TEXT NOT NULL, active INTEGER NOT NULL, password TEXT NOT NULL, legacy_chats INTEGER NOT NULL)',
    'CREATE TABLE IF NOT EXISTS permissions (account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE, permission TEXT NOT NULL, PRIMARY KEY(account_id,permission))',
    'CREATE TABLE IF NOT EXISTS roles (id TEXT PRIMARY KEY, name TEXT NOT NULL COLLATE NOCASE UNIQUE, permissions TEXT NOT NULL)',
    'CREATE TABLE IF NOT EXISTS account_roles (account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE, role_id TEXT NOT NULL REFERENCES roles(id), PRIMARY KEY(account_id,role_id))',
    'CREATE TABLE IF NOT EXISTS key_grants (account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE, key_id TEXT NOT NULL, PRIMARY KEY(account_id,key_id))',
)


class AccountStore:
    def __init__(self, root):
        self.root = Path(root)
        self.path = self.root / 'accounts.sqlite3'
        self.legacy = self.root / 'accounts.json'

    def connect(self):
        self.root.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=10)
        db.execute('PRAGMA foreign_keys=ON')
        try:
            db.execute('BEGIN IMMEDIATE')
            for sql in SCHEMA:
                db.execute(sql)
            if not db.execute('SELECT 1 FROM metadata').fetchone():
                doc = dict(revision=0, accounts=[], roles=[])
                if self.legacy.exists():
                    raw = self.legacy.read_bytes()
                    if len(raw) > 4*1024*1024:
                        raise ValueError('The account directory exceeds its size limit.')
                    doc = json.loads(raw)
                    # Preserve the exact recoverable input before importing it once.
                    backup = self.root / 'accounts.legacy-v1.json'
                    if backup.exists() and backup.read_bytes() != raw:
                        raise ValueError('Account migration backup differs; reconcile it before importing.')
                    if not backup.exists():
                        atomic_write_bytes(backup, raw)
                self.write(db, doc)
            db.commit()
            return db
        except BaseException:
            db.rollback()
            db.close()
            raise

    @staticmethod
    def read_db(db):
        doc = dict(version=2, revision=db.execute('SELECT revision FROM metadata').fetchone()[0], accounts=[], roles=[])
        row=db.execute('SELECT suspended FROM options WHERE id=1').fetchone()
        doc['suspended']=bool(row and row[0])
        for rid, name, perms in db.execute('SELECT id,name,permissions FROM roles ORDER BY rowid'):
            doc['roles'].append(dict(id=rid, name=name, permissions=json.loads(perms)))
        for aid, username, name, role, active, password, legacy in db.execute('SELECT * FROM accounts ORDER BY rowid'):
            doc['accounts'].append(dict(id=aid,username=username,name=name,role=role,active=bool(active),password=json.loads(password),legacy_chats=bool(legacy),
                permissions=[r[0] for r in db.execute('SELECT permission FROM permissions WHERE account_id=? ORDER BY permission',(aid,))],
                roles=[r[0] for r in db.execute('SELECT role_id FROM account_roles WHERE account_id=? ORDER BY role_id',(aid,))],
                keys=[r[0] for r in db.execute('SELECT key_id FROM key_grants WHERE account_id=? ORDER BY key_id',(aid,))]))
        return doc

    @staticmethod
    def write(db, doc):
        for table in ('key_grants','account_roles','permissions','accounts','roles'):
            db.execute('DELETE FROM '+table)
        for role in doc.get('roles',[]):
            db.execute('INSERT INTO roles VALUES (?,?,?)',(role['id'],role['name'],json.dumps(role['permissions'])))
        for u in doc['accounts']:
            db.execute('INSERT INTO accounts VALUES (?,?,?,?,?,?,?)',(u['id'],u['username'],u['name'],u['role'],int(u['active']),json.dumps(u['password']),int(u.get('legacy_chats',False))))
            db.executemany('INSERT INTO permissions VALUES (?,?)',[(u['id'],p) for p in u['permissions']])
            db.executemany('INSERT INTO account_roles VALUES (?,?)',[(u['id'],p) for p in u.get('roles',[])])
            db.executemany('INSERT INTO key_grants VALUES (?,?)',[(u['id'],p) for p in u.get('keys',[])])
        db.execute('INSERT OR REPLACE INTO metadata VALUES (1,?)',(doc['revision'],))
        db.execute('INSERT OR REPLACE INTO options VALUES (1,?)',(int(doc.get('suspended',False)),))

    def read(self):
        db = self.connect()
        try:
            db.execute('BEGIN')
            return self.read_db(db)
        finally:
            db.close()

    @contextmanager
    def edit(self, revision=None):
        db = self.connect()
        try:
            db.execute('BEGIN IMMEDIATE')
            doc = self.read_db(db)
            if revision is not None and (type(revision) is not int or revision != doc['revision']):
                raise ValueError('Accounts changed in another window. Refresh before saving.')
            yield doc
            doc['revision'] += 1
            self.write(db,doc)
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()
