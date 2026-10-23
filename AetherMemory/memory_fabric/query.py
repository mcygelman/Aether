"""Agent-written SQLite reads, with parameters, paging and engine schema discovery."""
from __future__ import annotations

import sqlite3
import sys

from . import schema
from .errors import MemoryFabricError


def describe(cart, table=None):
    if table is not None:
        if table not in schema.DATA_TABLES:
            raise MemoryFabricError('choose a table from schema.tables')
        return {'schema_version': schema.SCHEMA_VERSION, 'table': table,
                'columns': [dict(r) for r in cart.conn.execute(f'PRAGMA table_info({table})')],
                'foreign_keys': [dict(r) for r in cart.conn.execute(f'PRAGMA foreign_key_list({table})')],
                'ddl': cart.conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()[0]}
    return {'schema_version': schema.SCHEMA_VERSION, 'tables': list(schema.DATA_TABLES),
        'table_details': {'action': 'schema', 'arguments': {'table': 'choose a listed table'}}, 'examples': [
            'SELECT p.project_id,n.title FROM projects p JOIN notes n ON n.note_id=p.project_id',
            "SELECT kind,occurred_at,summary FROM session_events ORDER BY event_seq DESC LIMIT 20",
            'SELECT a,b,relation,strength FROM temp.link_state WHERE a=:note OR b=:note'],
        'semantics': 'SELECT/CTE reads do not refresh memory. Writes use engine operations. '
                     'Temporary note_state/link_state show current decay. Catalogs do not apply recall thresholds.'}


def execute(cart, sql, parameters=(), *, offset=0, limit=100):
    if not isinstance(sql, str) or not sql.strip():
        raise MemoryFabricError('SQL query is required')
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 500:
        raise MemoryFabricError('query paging needs offset>=0 and limit 1..500')
    if not isinstance(parameters, (list, tuple, dict)):
        raise MemoryFabricError('query parameters must be an array or object')
    allowed = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE}
    def authorize(action, arg1, arg2, database, trigger):
        if action not in allowed:
            return sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_FUNCTION and (arg2 or arg1 or '').lower() in ('load_extension', 'writefile', 'readfile'):
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK
    ticks = 0
    def progress():
        nonlocal ticks
        ticks += 1
        return ticks > 10000
    with cart.read_snapshot():
        cart.conn.set_authorizer(authorize)
        cart.conn.set_progress_handler(progress, 1000)
        try:
            cursor = cart.conn.execute(sql, parameters)
            if cursor.description is None:
                raise MemoryFabricError('query must return rows')
            for _ in range(offset):
                if cursor.fetchone() is None:
                    break
            rows = cursor.fetchmany(limit + 1)
            return {'columns': [d[0] for d in cursor.description],
                    'rows': [[v.hex() if isinstance(v, bytes) else v for v in r] for r in rows[:limit]],
                    'offset': offset, 'next_offset': offset + limit if len(rows) > limit else None,
                    'read_only': True}
        except sqlite3.Error as exc:
            raise MemoryFabricError(f'read-only SQL query failed: {exc}') from exc
        finally:
            cart.conn.set_progress_handler(None, 0)
            # CPython <3.11 accepts None but leaves a callback that denies SQL,
            # including RELEASE and the owner's next write/close.
            cart.conn.set_authorizer(None if sys.version_info >= (3, 11)
                                     else lambda *args: sqlite3.SQLITE_OK)
