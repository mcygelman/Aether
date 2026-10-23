import os
import sqlite3

import pytest

from memory_fabric import FixedClock, MemoryEngine
from memory_fabric.cartridge import Cartridge, CartridgeError, fingerprint
from memory_fabric import schema


def test_create_open_round_trip(tmp_path, clock):
    path = str(tmp_path / "grace.sqlite")
    eng = MemoryEngine.create(path, "grace", clock=clock)
    nid = eng.remember("Door code", "4471", "project fact", ["upstairs"])
    seal = eng.close()
    again = MemoryEngine.open(path, clock=clock)
    assert again.open_report.sealed and not again.open_report.tampered
    assert again.agent_id == "grace"
    assert again.note(nid)["body"] == "4471"
    assert again.close() == seal  # nothing changed: same fingerprint


def test_cartridge_file_holds_data_only(tmp_path, clock):
    """Decision 6/21: no views, triggers or logic are ever stored in the file."""
    path = str(tmp_path / "a.sqlite")
    eng = MemoryEngine.create(path, "a", clock=clock)
    eng.remember("x", "y", "lesson")
    eng.recall(["x"])           # uses the temporary views
    eng.close()
    raw = sqlite3.connect(path)
    kinds = {r[0] for r in raw.execute("SELECT type FROM sqlite_master")}
    raw.close()
    assert kinds <= {"table", "index"}


def test_outside_edit_is_detected(tmp_path, clock):
    path = str(tmp_path / "a.sqlite")
    eng = MemoryEngine.create(path, "a", clock=clock)
    eng.remember("Truth", "the real body", "lesson")
    eng.close()
    raw = sqlite3.connect(path)
    raw.execute("UPDATE notes SET body = 'tampered'")
    raw.commit()
    raw.close()
    again = MemoryEngine.open(path, clock=clock)
    assert again.open_report.tampered
    assert again.cart.conn.execute("SELECT value FROM meta WHERE key='tamper_events'").fetchone()[0] == "1"
    again.close()
    # once resealed by the engine, the next open is clean again
    assert not MemoryEngine.open(path, clock=clock).open_report.tampered


def test_unsealed_file_is_not_called_tampered(tmp_path, clock):
    path = str(tmp_path / "a.sqlite")
    cart = Cartridge.create(path, "a", clock=clock)
    cart.conn.commit()
    cart.conn.close()           # crash: closed without sealing
    report = Cartridge.open(path, clock=clock).report
    assert not report.sealed and not report.tampered


def test_refuses_existing_and_foreign_files(tmp_path, clock):
    path = str(tmp_path / "a.sqlite")
    MemoryEngine.create(path, "a", clock=clock).close()
    with pytest.raises(CartridgeError):
        Cartridge.create(path, "b", clock=clock)
    other = tmp_path / "other.sqlite"
    sqlite3.connect(str(other)).execute("CREATE TABLE t(x)").connection.close()
    with pytest.raises(CartridgeError):
        Cartridge.open(str(other), clock=clock)


def test_schema_version_mismatch_refused(tmp_path, clock):
    path = str(tmp_path / "a.sqlite")
    MemoryEngine.create(path, "a", clock=clock).close()
    raw = sqlite3.connect(path)
    raw.execute("UPDATE meta SET value='999' WHERE key='schema_version'")
    raw.commit()
    raw.close()
    with pytest.raises(CartridgeError, match="migration"):
        Cartridge.open(path, clock=clock)


def test_settings_ranges_tunability_and_audit(engine):
    engine.change_setting("surface_threshold", 0.4, reason="tighter focus")
    assert engine.settings()["surface_threshold"] == 0.4
    with pytest.raises(CartridgeError, match="within"):
        engine.change_setting("surface_threshold", 0.9)
    with pytest.raises(CartridgeError, match="not tunable"):
        engine.change_setting("floor", 0.1)                 # the agent cannot move the floor
    log = engine.cart.conn.execute("SELECT * FROM config_changes").fetchall()
    assert [(r["key"], r["old_value"], r["new_value"], r["changed_by"]) for r in log] == [
        ("surface_threshold", 0.3, 0.4, "grace")]


def test_profiles_seeded_and_tunable_with_log(engine):
    names = {p["profile"] for p in engine.profiles()}
    assert names == {p[0] for p in schema.DEFAULT_PROFILES}
    engine.tune_profile("project_fact", reason="projects run long", horizon_days=10)
    assert engine.cart.profile("project_fact")["horizon_days"] == 10
    assert engine.cart.profile("project_fact")["source"] == "tuned"
    with pytest.raises(CartridgeError):
        engine.tune_profile("project_fact", fast_rate=1.5)
    with pytest.raises(CartridgeError):
        engine.tune_profile("project_fact", bogus=1)


def test_fingerprint_ignores_open_time_meta(engine):
    before = fingerprint(engine.cart.conn)
    engine.cart.conn.execute("INSERT OR REPLACE INTO meta VALUES ('last_opened_at', 'later')")
    assert fingerprint(engine.cart.conn) == before


def _digest(path):
    import hashlib
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def test_read_only_open_writes_nothing(tmp_path, clock):
    path = str(tmp_path / "ro.sqlite")
    eng = MemoryEngine.create(path, "ro", clock=clock)
    nid = eng.remember("Door code", "4471", "project fact", ["upstairs"])
    eng.close()
    before = _digest(path)
    view = MemoryEngine.open(path, clock=clock, read_only=True)
    assert view.note(nid)["body"] == "4471"
    assert view.recall(["upstairs"]).ids() == [nid]
    view.card(); view.resume()
    with pytest.raises(CartridgeError, match="read-only"):
        view.remember("x", "y", "fact")
    with pytest.raises(CartridgeError, match="read-only"):
        view.load([nid])            # loading refreshes, so it is a write
    view.close()
    assert _digest(path) == before


def test_read_only_reports_tamper_without_logging_it(tmp_path, clock):
    path = str(tmp_path / "t.sqlite")
    eng = MemoryEngine.create(path, "t", clock=clock)
    eng.remember("Truth", "real", "lesson")
    eng.close()
    raw = sqlite3.connect(path)
    with raw:
        raw.execute("UPDATE notes SET body = 'edited'")
    raw.close()
    view = MemoryEngine.open(path, clock=clock, read_only=True)
    assert view.open_report.tampered
    view.close()
    eng = MemoryEngine.open(path, clock=clock)       # a normal open logs it once
    assert eng.open_report.tampered
    assert eng.cart.conn.execute("SELECT value FROM meta WHERE key='tamper_events'").fetchone()[0] == "1"
    eng.close()


def test_read_only_refuses_missing_file(tmp_path):
    with pytest.raises(CartridgeError, match="no such cartridge"):
        MemoryEngine.open(str(tmp_path / "nope.sqlite"), read_only=True)


def test_backup_is_a_full_consistent_copy_and_never_overwrites(tmp_path, clock):
    from memory_fabric.__main__ import main
    path = str(tmp_path / "b.sqlite")
    eng = MemoryEngine.create(path, "b", clock=clock)
    nid = eng.remember("Secret recipe", "client data", "lesson", ["recipe"], proprietary=True)
    eng.close()
    before = _digest(path)
    dest = str(tmp_path / "copy.sqlite")
    assert main(["backup", path, dest]) == 0
    assert _digest(path) == before                                   # source untouched
    with MemoryEngine.open(dest, clock=clock) as copy:
        assert copy.note(nid)["body"] == "client data"               # everything, unlike export_clean
        assert not copy.open_report.tampered
    assert main(["backup", path, dest]) == 1                         # refuses to overwrite


def test_two_handles_can_share_one_cartridge(tmp_path, clock):
    path = str(tmp_path / "shared.sqlite")
    a = MemoryEngine.create(path, "s", clock=clock)
    b = MemoryEngine.open(path, clock=clock)
    na = a.remember("From session A", "a", "fact", ["shared"])
    nb = b.remember("From session B", "b", "fact", ["shared"])
    assert set(a.recall(["shared"]).ids()) == {na, nb}
    nb2 = b.remember("Later from B", "b2", "fact", ["shared"])       # A must see it: no stale snapshot
    assert nb2 in a.recall(["shared"]).ids()
    view = MemoryEngine.open(path, clock=clock, read_only=True)       # a viewer reading meanwhile
    view.recall(["shared"])
    b.close(); a.close(); view.close()


def test_seal_status_is_honest_about_live_and_crashed_sessions(tmp_path, clock):
    path = str(tmp_path / "live.sqlite")
    a = MemoryEngine.create(path, "a", clock=clock)
    a.remember("x", "y", "fact")
    a.close()
    a = MemoryEngine.open(path, clock=clock)
    assert a.open_report.status == "sealed" and a.open_report.warning is None
    a.remember("written while open", "y", "fact")
    b = MemoryEngine.open(path, clock=clock)                  # a second live session
    assert b.open_report.status == "in_use" and not b.open_report.tampered
    view = MemoryEngine.open(path, clock=clock, read_only=True)
    assert view.open_report.status == "in_use" and not view.open_report.tampered
    view.close(); b.close(); a.close()
    again = MemoryEngine.open(path, clock=clock)
    assert again.open_report.status == "sealed"
    assert again.cart.conn.execute("SELECT value FROM meta WHERE key='tamper_events'").fetchone() is None
    again.remember("then a crash", "y", "fact")
    again.cart.conn.close()                                   # no close(): crashed
    after = MemoryEngine.open(path, clock=clock)
    assert after.open_report.status == "unclean" and not after.open_report.tampered
    assert "without closing" in after.open_report.warning
    after.close()
    assert MemoryEngine.open(path, clock=clock).open_report.status == "sealed"


def test_removing_the_seal_does_not_hide_an_outside_edit(tmp_path, clock):
    path = str(tmp_path / "s.sqlite")
    MemoryEngine.create(path, "s", clock=clock).close()
    raw = sqlite3.connect(path)
    with raw:
        raw.execute("UPDATE card SET value = 'Someone else' WHERE key = 'name'")
        raw.execute("DELETE FROM meta WHERE key = 'seal'")
    raw.close()
    report = MemoryEngine.open(path, clock=clock, read_only=True).open_report
    assert report.status == "unsealed" and "outside the engine" in report.warning


def test_open_never_creates_or_converts_files(tmp_path, clock):
    missing = str(tmp_path / "typo.sqlite")
    with pytest.raises(CartridgeError, match="no such cartridge"):
        MemoryEngine.open(missing, clock=clock)
    assert not os.path.exists(missing)
    foreign = str(tmp_path / "other.db")
    raw = sqlite3.connect(foreign)
    raw.execute("CREATE TABLE t (x)"); raw.commit(); raw.close()
    with pytest.raises(CartridgeError, match="not a cartridge"):
        MemoryEngine.open(foreign, clock=clock)
    raw = sqlite3.connect(foreign)
    assert raw.execute("PRAGMA journal_mode").fetchone()[0] == "delete"   # untouched
    raw.close()
    with pytest.raises(CartridgeError, match="already exists"):
        MemoryEngine.create(foreign, "x", clock=clock)
