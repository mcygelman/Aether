"""A mind whose cartridge the schema-2 engine wrote is carried forward the first time the atelier opens it:
the engine's migration with a full backup, the log the atelier kept in it comes along, and the log says
so. A seal that did not match is reported in that row, not held against the mind. No model, no network."""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from aether.atelier import mind_memory   # noqa: E402
from aether.atelier.participants import Participant   # noqa: E402
from aether.memory.fabric import memory_fabric   # noqa: E402

ROWS = [dict(kind='wrote', pointer='sha256:aa', tags=['reports'], line='wrote outputs/result.md', by='host'),
        dict(kind='noted', pointer='', tags=['cap'], line='decided to keep the cap on amounts', by='self')]


def _schema2_mind(tmp_path, *, edited_outside=False):
    """A home as the 10-19 atelier left it: schema 2, its log written beside the seal."""
    p = Participant.author(tmp_path / 'homes' / 'guide', id='guide', kind='member', name='Guide', system='You are Guide.',
                           roots={'outputs': 'write'}, mailboxes=tmp_path / 'share', cartridge=True)
    mind_memory.log_actions(p.home, ROWS, commission_id='harbor-ledger-b')
    memory_fabric()
    from memory_fabric.cartridge import fingerprint
    path = mind_memory.pointer(p.home)['path']
    raw = sqlite3.connect(path)
    with raw:
        from memory_fabric.foundations_schema import AUTHORED_TABLES
        ids = [r[0] for r in raw.execute('SELECT guide_id FROM guides UNION SELECT teachframe_id FROM teachframes')]
        for table in ('session_events', *reversed(AUTHORED_TABLES)):
            raw.execute('DROP TABLE ' + table)
        for nid in ids:
            raw.execute('DELETE FROM tags WHERE note_id=?', (nid,))
            raw.execute('DELETE FROM notes WHERE note_id=?', (nid,))
        raw.execute("DELETE FROM operation_receipts WHERE operation_id LIKE 'builtin:memory-foundations:%'")
        raw.execute("UPDATE meta SET value = '2' WHERE key = 'schema_version'")
        raw.execute("UPDATE meta SET value = ? WHERE key = 'seal'", (fingerprint(raw),))   # a v2 seal: no side log
        if edited_outside:
            raw.execute("UPDATE notes SET body = body || ' (edited outside)' WHERE rowid = (SELECT min(rowid) FROM notes)")
    raw.close()
    mind_memory._CURRENT.discard(path)
    return p, path


def test_a_schema2_mind_wakes_carried_forward_with_its_log(tmp_path):
    p, path = _schema2_mind(tmp_path)
    # Preview remains read-only; real activation performs the backed-up migration.
    mind_memory.begin_work(p.home, 'harbor-ledger-b')
    rows = p.log_recent('harbor-ledger-b', limit=10)
    assert [r['line'] for r in rows] == [r['line'] for r in reversed(ROWS)]
    newest = p.log_recent(limit=1)[0]
    assert newest['kind'] == 'migrated' and newest['by'] == 'Aether' and newest['commission'] is None
    assert 'from schema 2 to 4' in newest['line'] and 'seal did not match' not in newest['line']
    backup = Path(path).parent / newest['pointer']
    assert backup.is_file() and backup.parent.parent.name == 'backups'
    old = sqlite3.connect(backup)
    assert old.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()[0] == '2'
    assert old.execute('SELECT count(*) FROM sidelog').fetchone()[0] == 2                  # the old mind, log and all
    old.close()
    engine = mind_memory._open_read(p.home)
    try:
        assert engine.open_report.status == 'sealed'
    finally:
        engine.close()
    mind_memory._CURRENT.discard(path)
    assert mind_memory.carry_forward(p.home) is None                                    # once only
    assert [r['kind'] for r in p.log_recent(limit=10)].count('migrated') == 1
    assert 'Example warning' in p.wake_memory(['anything'])                              # the seeds are still there


def test_a_mind_edited_outside_is_carried_and_the_log_says_so(tmp_path):
    p, path = _schema2_mind(tmp_path, edited_outside=True)
    done = mind_memory.carry_forward(p.home)
    assert done['to_version'] == 4 and done['sealed'] is False
    newest = p.log_recent(limit=1)[0]
    assert newest['kind'] == 'migrated' and 'seal did not match' in newest['line']
    assert len(p.log_recent(limit=10)) == 3


def test_a_schema2_mind_whose_last_session_crashed_still_wakes(tmp_path):
    p, path = _schema2_mind(tmp_path)
    raw = sqlite3.connect(path)
    with raw:
        raw.execute("UPDATE meta SET value = '1' WHERE key = 'open_sessions'")
    raw.close()
    done = mind_memory.carry_forward(p.home)
    assert done['seal_status'] == 'unclean' and done['to_version'] == 4
    assert 'ended without closing' in p.log_recent(limit=1)[0]['line']
