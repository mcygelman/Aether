"""Atomic, reconstructable history. Descriptions and payloads live separately.

The cartridge revision is deliberately conservative: any intervening write requires
the owner to inspect again. Payloads are row deltas, including relationship rows.
No tokenizer, relevance judgment, or identity authority lives in this box.
"""
from __future__ import annotations

from functools import wraps
import json
from .foundations_schema import AUTHORED_TABLES

from .errors import MemoryFabricError

ACTIVE_TABLES = ('config', 'config_changes', 'profiles', 'notes', 'tags', 'links',
                 'reviews', 'outcomes', 'levels', 'card', 'review_responses') + AUTHORED_TABLES

DDL = [
    """CREATE TABLE IF NOT EXISTS history_data (
        history_id INTEGER PRIMARY KEY AUTOINCREMENT, payload TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS memory_changes (
        revision INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL,
        actor TEXT NOT NULL, session TEXT NOT NULL, operation TEXT NOT NULL,
        description TEXT NOT NULL, history_id INTEGER NOT NULL REFERENCES history_data(history_id))""",
    """CREATE TABLE IF NOT EXISTS operation_receipts (
        operation_id TEXT PRIMARY KEY, request_hash TEXT NOT NULL,
        actor TEXT NOT NULL, result TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS review_responses (
        response_id INTEGER PRIMARY KEY AUTOINCREMENT,
        review_id INTEGER NOT NULL REFERENCES reviews(review_id),
        author TEXT NOT NULL, text TEXT NOT NULL, evidence_refs TEXT NOT NULL,
        created_at TEXT NOT NULL)""",
]


def atomic(fn):
    """Compose existing writers without allowing a nested commit to escape."""
    @wraps(fn)
    def call(cart, *args, **kwargs):
        with cart.transaction(immediate=True, operation=fn.__name__):
            return fn(cart, *args, **kwargs)
    return call


def revision(cart):
    return cart.conn.execute('SELECT COALESCE(MAX(revision),0) FROM memory_changes').fetchone()[0]


def require_revision(cart, expected):
    current = revision(cart)
    if type(expected) is not int or expected != current:
        raise MemoryFabricError(f'stale revision: expected {expected}, current {current}; '
                                'inspect and consult history before reconciling')


def snapshot(conn):
    return {table: [dict(r) for r in conn.execute(f'SELECT * FROM {table}')]
            for table in ACTIVE_TABLES}


def install_capture(conn):
    """Engine-owned TEMP triggers, never stored in the cartridge.

    Capture only touched rows. Scanning the full mind per write made the existing
    5,000-note bench quadratic. SQLite rolls these TEMP rows back with the write.
    """
    # Python repr uses a shortest round-trip representation. SQLite quote() on
    # supported 3.37 builds can change the last bit even when printing 20 digits.
    # Only TEMP capture changes: persisted history remains ordinary JSON numbers.
    conn.create_function('memory_real_text', 1, repr, deterministic=True)
    conn.execute('CREATE TEMP TABLE IF NOT EXISTS memory_delta '
                 '(seq INTEGER PRIMARY KEY, table_name TEXT, phase TEXT, payload TEXT)')
    for table in ACTIVE_TABLES:
        columns = [r[1] for r in conn.execute(f'PRAGMA table_info({table})')]
        for event, phases in [('INSERT', ('NEW',)), ('DELETE', ('OLD',)), ('UPDATE', ('OLD', 'NEW'))]:
            commands = []
            for phase in phases:
                # Encode REAL as text before SQLite's lossy JSON number formatter.
                fields = ','.join(f"'{c}',CASE typeof({phase}.{c}) WHEN 'real' THEN "
                                  f"json_array(memory_real_text({phase}.{c})) ELSE {phase}.{c} END" for c in columns)
                commands.append("INSERT INTO memory_delta(table_name,phase,payload) VALUES "
                                f"('{table}','{phase}',json_object({fields}));")
            conn.execute(f'CREATE TEMP TRIGGER capture_{table}_{event} AFTER {event} ON main.{table} '
                         'BEGIN ' + ' '.join(commands) + ' END')


def _primary_key(conn, table):
    return [r[1] for r in sorted(conn.execute(f'PRAGMA table_info({table})'), key=lambda r: r[5]) if r[5]]


def _decode(raw):
    """A one-item list is the capture triggers' exact encoding of a REAL value."""
    return {k: float(v[0]) if isinstance(v, list) else v for k, v in json.loads(raw).items()}


def delta(conn, since=0):
    states = {}
    for row in conn.execute('SELECT table_name,phase,payload FROM temp.memory_delta WHERE seq>? ORDER BY seq', (since,)):
        table, phase, raw = row
        item = _decode(raw)
        pk = _primary_key(conn, table)
        key = (table, tuple(item[c] for c in pk))
        if key not in states:
            states[key] = [item if phase == 'OLD' else None, None]
        states[key][1] = item if phase == 'NEW' else None
    changes = {}
    for (table, _), (before, after) in states.items():
        if before != after:
            entry = changes.setdefault(table, {'before': [], 'after': []})
            if before is not None:
                entry['before'].append(before)
            if after is not None:
                entry['after'].append(after)
    return changes


def mark(conn):
    return conn.execute('SELECT COALESCE(MAX(seq),0) FROM temp.memory_delta').fetchone()[0]


def record(cart, before, operation, description):
    changes = delta(cart.conn)
    if changes:
        ref = cart.conn.execute('INSERT INTO history_data(payload) VALUES (?)',
                                (json.dumps(changes, sort_keys=True),)).lastrowid
        cart.conn.execute('INSERT INTO memory_changes(at,actor,session,operation,description,history_id) '
                          'VALUES (?,?,?,?,?,?)',
                          (cart.clock.now(), cart.actor, cart.session_id, operation,
                           description or operation.replace('_', ' '), ref))


def changes(cart, after=0, limit=20):
    if type(after) is not int or after < 0 or type(limit) is not int or not 1 <= limit <= 100:
        raise MemoryFabricError('history needs after >= 0 and limit 1..100')
    return [dict(r) for r in cart.conn.execute(
        'SELECT * FROM memory_changes WHERE revision > ? ORDER BY revision LIMIT ?', (after, limit))]


def payload(cart, change_revision):
    row = cart.conn.execute('SELECT h.payload FROM history_data h JOIN memory_changes c '
                            'ON c.history_id=h.history_id WHERE c.revision=?', (change_revision,)).fetchone()
    if row is None:
        raise MemoryFabricError('unknown historical revision')
    return json.loads(row[0])


def reconstruct(cart, target):
    """Reconstruct active tables at a revision without changing the cartridge."""
    if type(target) is not int or not 0 <= target <= revision(cart):
        raise MemoryFabricError('revision is outside retained history')
    state = snapshot(cart.conn)
    for row in cart.conn.execute('SELECT revision FROM memory_changes WHERE revision>? '
                                 'ORDER BY revision DESC', (target,)):
        for table, delta in payload(cart, row[0]).items():
            # Match rows by primary key, not by every value: a whole-row match
            # breaks on any representational difference in a retained payload.
            pk = _primary_key(cart.conn, table)
            gone = {tuple(item[c] for c in pk) for item in delta['after']}
            state[table] = [r for r in state[table] if tuple(r[c] for c in pk) not in gone]
            state[table].extend(delta['before'])
    return state
