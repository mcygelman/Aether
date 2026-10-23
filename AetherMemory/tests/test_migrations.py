"""Migrations box + additive top-up of new engine defaults."""
import os
import sqlite3

import pytest

from memory_fabric import MemoryEngine, migrations, schema
from memory_fabric.migrations import MigrationError, cartridge_version, migrate


def _make(tmp_path, clock, name="m.sqlite"):
    path = str(tmp_path / name)
    eng = MemoryEngine.create(path, "m", clock=clock)
    nid = eng.remember("Keep me", "body survives migration", "lesson", ["keep"])
    eng.close()
    return path, nid


def _step_add_mood(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS moods (at TEXT NOT NULL, mood TEXT NOT NULL)")


def test_current_cartridge_needs_nothing(tmp_path, clock):
    path, _ = _make(tmp_path, clock)
    report = migrate(path)
    assert not report.changed and report.backup_path is None
    assert cartridge_version(path) == schema.SCHEMA_VERSION


def test_structural_migration_backs_up_then_upgrades(tmp_path, clock, monkeypatch):
    path, nid = _make(tmp_path, clock)
    v = schema.SCHEMA_VERSION                                        # a stand-in step to the next version
    monkeypatch.setattr(schema, "SCHEMA_VERSION", v + 1)
    monkeypatch.setitem(migrations.MIGRATIONS, v, _step_add_mood)
    with pytest.raises(Exception, match="migration is required"):
        MemoryEngine.open(path, clock=clock)
    report = migrate(path)
    assert report.steps == [v] and report.to_version == v + 1
    assert cartridge_version(report.backup_path) == v              # the backup is the old mind
    with MemoryEngine.open(path, clock=clock) as eng:
        assert not eng.open_report.tampered                         # resealed after migrating
        assert eng.note(nid)["body"] == "body survives migration"
    monkeypatch.setattr(schema, "SCHEMA_VERSION", v + 2)
    monkeypatch.setitem(migrations.MIGRATIONS, v + 1, _step_add_mood)
    open(os.path.join(str(tmp_path), f"m.schema{v + 1}.bak.sqlite"), "wb").close()
    with pytest.raises(MigrationError, match="backup already exists"):
        migrate(path)
    assert cartridge_version(path) == v + 1                          # nothing changed


def test_refuses_tampered_newer_and_gapped(tmp_path, clock, monkeypatch):
    path, _ = _make(tmp_path, clock)
    raw = sqlite3.connect(path)
    with raw:
        raw.execute("UPDATE notes SET body = 'edited outside'")
    raw.close()
    v = schema.SCHEMA_VERSION                                        # a stand-in step to the next version
    monkeypatch.setattr(schema, "SCHEMA_VERSION", v + 1)
    monkeypatch.setitem(migrations.MIGRATIONS, v, _step_add_mood)
    with pytest.raises(MigrationError, match="edited outside"):
        migrate(path)
    assert cartridge_version(path) == v                              # untouched
    assert migrate(path, allow_tampered=True).to_version == v + 1   # operator override
    with pytest.raises(MigrationError, match="newer than this engine"):
        migrate(path, target=1)
    with pytest.raises(MigrationError, match=f"no migration step from schema {v + 1}"):
        migrate(path, target=v + 2)


def test_new_engine_defaults_are_added_on_open_and_logged(tmp_path, clock):
    path, _ = _make(tmp_path, clock)
    raw = sqlite3.connect(path)
    with raw:
        raw.execute("DELETE FROM config WHERE key = 'hop_decay'")       # as if written by an
        raw.execute("DELETE FROM profiles WHERE profile = 'scratch'")   # older engine
    raw.close()
    with MemoryEngine.open(path, clock=clock, read_only=True) as view:
        assert view.settings()["hop_decay"] == 0.90                     # read-only: defaults fill in
    with MemoryEngine.open(path, clock=clock) as eng:
        assert eng.cart.conn.execute("SELECT value FROM config WHERE key='hop_decay'").fetchone()
        assert "scratch" in [p["profile"] for p in eng.profiles()]
        log = eng.cart.conn.execute(
            "SELECT changed_by, reason FROM config_changes WHERE key='hop_decay'").fetchone()
        assert log["changed_by"] == "engine"
