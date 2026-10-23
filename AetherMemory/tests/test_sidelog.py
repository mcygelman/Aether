"""Side-log box: append-only journal of actions, sealed since schema 3, not captured into history."""
import json
import os
import sqlite3

import pytest

from memory_fabric import MemoryEngine, schema, sidelog
from memory_fabric.cartridge import fingerprint
from memory_fabric.migrations import MigrationError, cartridge_version, migrate

ROWS = [dict(kind="wrote", pointer="sha256:aa", tags=["reports", "weekly"], line="wrote harbor/reports.py", by="host"),
        dict(kind="ran", pointer="tests/test_reports.py", tags=["tests"], line="ran the report tests", by="host"),
        dict(kind="noted", pointer="note-7", tags=["cap", "totals"], line="a cap on amounts is not a cap on totals",
             by="harbor-guide")]


def test_append_recent_and_search(engine):
    assert engine.log(ROWS, commission="harbor-ledger-b") == 3
    assert engine.log([dict(kind="", line="no kind"), dict(kind="ran", line=""), None]) == 0   # skipped, not raised
    engine.log([dict(kind="read-memory", pointer="n1", tags=[], line="loaded n1")], commission="harbor-ledger-c")
    newest = engine.log_recent()
    assert [r["kind"] for r in newest] == ["read-memory", "noted", "ran", "wrote"]
    assert newest[1] == dict(seq=3, at="2026-10-06T09:00:00Z", commission="harbor-ledger-b", kind="noted",
                             pointer="note-7", tags=["cap", "totals"], line="a cap on amounts is not a cap on totals",
                             by="harbor-guide")
    assert [r["kind"] for r in engine.log_recent("harbor-ledger-b", limit=2)] == ["noted", "ran"]
    assert [r["kind"] for r in engine.log_search(["cap", "totals"])] == ["noted"]
    assert engine.log_search(["tests", "reports"])[0]["kind"] in ("ran", "wrote")
    assert engine.log_search(["nothing-like-this"]) == []


def test_rows_are_bounded_and_carry_their_own_commission(engine):
    engine.log([dict(kind="k" * 40, pointer="p" * 300, tags=[str(i) for i in range(12)] + [""], line="l" * 300,
                     commission="own", at="2026-10-01T00:00:00Z")], commission="default")
    row = engine.log_recent()[0]
    assert len(row["kind"]) == 24 and len(row["pointer"]) == 200 and len(row["line"]) == 200
    assert row["tags"] == [str(i) for i in range(8)] and row["by"] == "host"
    assert row["commission"] == "own" and row["at"] == "2026-10-01T00:00:00Z"


def test_repeats_in_the_hosts_words():
    rows = [dict(kind="ran", pointer="tests/x.py", line="ran")] * 3 + [dict(kind="read-memory", pointer="n1", line="r")] * 2
    rows += [dict(kind="ran", pointer="tests/y.py", line="ran")] * 2
    assert sidelog.repeats(rows) == [
        "you ran the tests at tests/x.py 3 times; run them once after the last edit",
        "you read memory n1 2 times in one commission; once is enough, it stays loaded in your note"]


def test_appends_spend_no_revision_and_have_no_history(engine):
    before = engine.revision
    engine.log(ROWS)
    assert engine.revision == before                        # sealed, not captured: no memory_changes row
    assert "sidelog" not in __import__("memory_fabric.history", fromlist=["x"]).ACTIVE_TABLES
    assert "sidelog" in schema.DATA_TABLES and "sidelog" in schema.sealed_tables(3)
    assert "sidelog" not in schema.sealed_tables(2)


def test_the_seal_notices_a_row_added_outside_aether(tmp_path, clock):
    path = str(tmp_path / "m.sqlite")
    with MemoryEngine.create(path, "m", clock=clock) as eng:
        eng.log(ROWS)
    with MemoryEngine.open(path, clock=clock) as eng:
        assert eng.open_report.status == "sealed"
    raw = sqlite3.connect(path)
    with raw:
        raw.execute("INSERT INTO sidelog(at, commission, kind, pointer, tags, line, by) "
                    "VALUES ('2026-10-06T10:00:00Z', NULL, 'wrote', '', '[]', 'not written by Aether', 'x')")
    raw.close()
    with MemoryEngine.open(path, clock=clock) as eng:
        assert eng.open_report.status == "tampered"           # reported, not refused: the mind still opens
        assert eng.log_recent()[0]["line"] == "not written by Aether"


def _schema2(tmp_path, clock, name, atelier_rows=None):
    """A schema-2 cartridge as the 10-19 engine left it: no side log in DDL or seal; optionally the atelier's
    own table with rows, written beside the seal as mind_memory did."""
    path = str(tmp_path / name)
    with MemoryEngine.create(path, "m", clock=clock, seed_resources=False) as eng:
        nid = eng.remember("Keep me", "body survives", "lesson", ["keep"])
    raw = sqlite3.connect(path)
    with raw:
        from memory_fabric.foundations_schema import AUTHORED_TABLES
        for table in ('session_events', *reversed(AUTHORED_TABLES)):
            raw.execute('DROP TABLE ' + table)
        raw.execute("DROP TABLE sidelog")
        raw.execute("UPDATE meta SET value = '2' WHERE key = 'schema_version'")
        raw.execute("UPDATE meta SET value = ? WHERE key = 'seal'", (fingerprint(raw),))
        if atelier_rows:
            raw.execute(sidelog.DDL)    # the atelier's LOG_DDL, the same text
            raw.executemany("INSERT INTO sidelog(at, commission, kind, pointer, tags, line, by) VALUES (?,?,?,?,?,?,?)",
                            [("2026-10-19T12:00:00+00:00", "c", r["kind"], r["pointer"], json.dumps(r["tags"]),
                              r["line"], r["by"]) for r in atelier_rows])
    raw.close()
    return path, nid


def test_migration_creates_the_log_where_absent(tmp_path, clock):
    path, nid = _schema2(tmp_path, clock, "plain.sqlite")
    with pytest.raises(Exception, match="migration is required"):
        MemoryEngine.open(path, clock=clock)
    report = migrate(path)
    assert report.steps == [2, 3] and cartridge_version(path) == 4 and cartridge_version(report.backup_path) == 2
    with MemoryEngine.open(path, clock=clock) as eng:
        assert eng.open_report.status == "sealed"
        assert eng.note(nid)["body"] == "body survives" and eng.log_recent() == []
        assert eng.log([dict(kind="migrated", line="carried to schema 3")]) == 1


def test_migration_adopts_the_ateliers_table_with_every_row(tmp_path, clock):
    path, nid = _schema2(tmp_path, clock, "logged.sqlite", atelier_rows=ROWS)
    raw = sqlite3.connect(path)
    sealed_v2 = raw.execute("SELECT value FROM meta WHERE key='seal'").fetchone()[0]
    assert fingerprint(raw) == sealed_v2              # the v2 seal never covered the log: not tampered
    assert fingerprint(raw, 3) != sealed_v2           # a schema-3 seal would cover it
    raw.close()
    report = migrate(path)                             # no allow_tampered needed
    assert report.steps == [2, 3]
    with MemoryEngine.open(path, clock=clock) as eng:
        assert eng.open_report.status == "sealed"
        rows = eng.log_recent(limit=10)
        assert [r["line"] for r in rows] == [r["line"] for r in reversed(ROWS)]
        assert rows[0]["tags"] == ["cap", "totals"] and rows[0]["at"] == "2026-10-19T12:00:00+00:00"
        assert eng.note(nid)["body"] == "body survives"
    backup = sqlite3.connect(report.backup_path)
    assert backup.execute("SELECT count(*) FROM sidelog").fetchone()[0] == 3   # the backup is the old mind, log and all
    backup.close()


def test_migration_keeps_a_foreign_sidelog_table_aside(tmp_path, clock):
    path, nid = _schema2(tmp_path, clock, "foreign.sqlite")
    raw = sqlite3.connect(path)
    with raw:
        raw.execute("CREATE TABLE sidelog (seq INTEGER PRIMARY KEY, note TEXT)")
        raw.execute("INSERT INTO sidelog(note) VALUES ('someone else''s table')")
        raw.execute("UPDATE meta SET value = ? WHERE key = 'seal'", (fingerprint(raw),))
    raw.close()
    assert migrate(path).steps == [2, 3]                  # carried forward, not refused
    with MemoryEngine.open(path, clock=clock) as eng:
        assert eng.open_report.status == "sealed" and eng.log_recent() == []
        kept = eng.cart.conn.execute("SELECT note FROM sidelog_before_v3").fetchall()
        assert [r[0] for r in kept] == ["someone else's table"] and eng.note(nid)["body"] == "body survives"


def test_a_crashed_schema2_cartridge_is_carried_forward_and_says_so(tmp_path, clock):
    path, nid = _schema2(tmp_path, clock, "crashed.sqlite", atelier_rows=ROWS)
    raw = sqlite3.connect(path)
    with raw:
        raw.execute("UPDATE meta SET value = '1' WHERE key = 'open_sessions'")      # the last session never closed
    raw.close()
    report = migrate(path, backup_dir=str(tmp_path / "new" / "backups"))         # a folder that does not exist yet
    assert report.steps == [2, 3] and report.seal_status == "unclean" and os.path.isfile(report.backup_path)
    with MemoryEngine.open(path, clock=clock) as eng:
        assert eng.open_report.status == "sealed" and len(eng.log_recent(limit=10)) == 3
        assert eng.note(nid)["body"] == "body survives"


def test_a_cartridge_another_session_holds_is_not_migrated(tmp_path, clock):
    path, _ = _schema2(tmp_path, clock, "held.sqlite")
    holder = sqlite3.connect(path)
    holder.execute("PRAGMA journal_mode = WAL")
    with holder:
        holder.execute("UPDATE meta SET value = '1' WHERE key = 'open_sessions'")
    holder.execute("SELECT count(*) FROM notes").fetchone()                           # still open, as a live session is
    try:
        with pytest.raises(MigrationError, match="open in another session"):
            migrate(path)
        assert cartridge_version(path) == 2
    finally:
        holder.close()
