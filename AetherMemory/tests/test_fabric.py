import pytest

from memory_fabric import MemoryEngine
from memory_fabric.clock import add_days
from memory_fabric.fabric import FabricError
from memory_fabric.kinds import UnknownKind


def test_remember_defaults(engine, clock):
    nid = engine.remember("Door Code", "4471", "project fact", ["Upstairs", "  door   code "])
    note = engine.note(nid)
    assert note["profile"] == "project_fact"
    assert note["switch_at"] == add_days(clock.now(), 7)            # profile horizon
    assert note["tags"] == ["door code", "upstairs"]                 # title is a tag; normalized
    assert note["strength"] == pytest.approx(0.8)
    assert note["depth"] == pytest.approx(0.2)


def test_remember_validates_before_writing(engine):
    with pytest.raises(FabricError):
        engine.remember("", "x", "lesson")
    with pytest.raises(FabricError):
        engine.remember("t", "x", "lesson", category="Gossip")
    with pytest.raises(UnknownKind):
        engine.remember("t", "x", "banana")
    with pytest.raises(FabricError):
        engine.remember("t", "x", "lesson", importance=2)
    with pytest.raises(FabricError):     # bad link: nothing at all is written
        engine.remember("t", "x", "lesson", links=[("missing-note", "causal")])
    assert engine.cart.conn.execute("SELECT count(*) FROM notes").fetchone()[0] == 2  # supplied resources only


def test_link_inherits_shorter_life_and_reinforces_on_repeat(engine):
    fact = engine.remember("fact", "", "project fact", switch_in_days=7)
    lesson = engine.remember("lesson", "", "standing lesson")
    link_id = engine.link(fact, lesson, "causal", 0.6)
    row = engine.cart.conn.execute("SELECT * FROM links WHERE link_id=?", (link_id,)).fetchone()
    assert row["fast_rate"] == engine.cart.profile("project_fact")["link_fast_rate"]
    assert row["switch_at"] == engine.note(fact)["switch_at"]
    assert engine.link(lesson, fact, "causal", 0.9) == link_id       # same pair, either direction
    row = engine.cart.conn.execute("SELECT * FROM links WHERE link_id=?", (link_id,)).fetchone()
    assert row["strength"] == 0.9 and row["reinforce_count"] == 1
    with pytest.raises(FabricError):
        engine.link(fact, fact)
    with pytest.raises(FabricError):
        engine.link(fact, lesson, "gossip")


def test_new_link_never_starts_in_the_past(engine, clock):
    """Regression for calibration finding (scenario 06)."""
    old = engine.remember("old", "", "project fact", switch_in_days=7)
    clock.advance(60)
    new = engine.remember("new", "", "project fact", switch_in_days=14)
    link_id = engine.link(new, old, "causal", 0.9)
    switch = engine.cart.conn.execute("SELECT switch_at FROM links WHERE link_id=?",
                                      (link_id,)).fetchone()[0]
    assert switch > clock.now()


def test_bury_is_not_delete(engine, clock):
    nid = engine.remember("wrong idea", "x", "standing lesson", ["ports"])
    other = engine.remember("right idea", "y", "standing lesson", ["ports"], links=[(nid, "conceptual")])
    engine.bury(nid)
    clock.advance(1)
    note = engine.note(nid)
    assert note["strength"] == pytest.approx(engine.settings()["floor"])
    assert note["importance"] == pytest.approx(engine.settings()["floor"])
    assert engine.cart.conn.execute("SELECT count(*) FROM notes").fetchone()[0] == 4  # two supplied, two authored
    assert engine.cart.conn.execute("SELECT count(*) FROM links").fetchone()[0] == 1
    assert nid not in engine.recall(["ports"]).ids()
    engine.raise_note(nid, 0.7)
    assert nid in engine.recall(["ports"]).ids()
    assert other


def test_engine_has_no_delete_anything():
    """Decision 13: the public surface offers no way to delete a memory or a review."""
    forbidden = ("delete", "remove", "forget", "purge", "drop", "erase", "edit_review")
    names = [n for n in dir(MemoryEngine) if not n.startswith("_")]
    assert not [n for n in names if any(word in n for word in forbidden)]


def test_adjust_depth_fades_back(engine, clock):
    nid = engine.remember("x", "", "standing lesson")
    base = engine.note(nid)["strength"]
    engine.adjust_depth(nid, -0.4, fade_days=10)
    assert engine.note(nid)["strength"] == pytest.approx(base - 0.4, abs=1e-3)
    clock.advance(10)
    assert engine.note(nid)["strength"] == pytest.approx(
        engine.note(nid)["importance"] * (1 - 0.002) ** 10, abs=1e-6)
    with pytest.raises(FabricError):
        engine.adjust_depth(nid, 3)


def test_set_switch_moves_the_date(engine, clock):
    nid = engine.remember("project", "", "project fact", switch_in_days=7)
    engine.set_switch(nid, in_days=21)
    assert engine.note(nid)["switch_at"] == add_days(clock.now(), 21)
    with pytest.raises(FabricError):
        engine.set_switch(nid)


def test_load_refreshes_and_extends_but_never_shortens(engine, clock):
    nid = engine.remember("warning", "", "safety warning", category="Alert", switch_in_days=7)
    clock.advance(20)
    weak = engine.note(nid)["strength"]
    engine.load([nid])
    note = engine.note(nid)
    assert note["strength"] > weak and note["pull_count"] == 1
    assert note["switch_at"] == add_days(clock.now(), 30)    # refresh_extends_days
    long_lived = engine.remember("plan", "", "project fact", switch_in_days=60)
    engine.load([long_lived])
    assert engine.note(long_lived)["switch_at"] == add_days(clock.now(), 60)


def test_proprietary_and_provenance_are_recorded(engine):
    nid = engine.remember("client price list", "secret", "project fact", proprietary=True,
                          source_ref="task T-12", confidence=0.8, stratum="Experience")
    row = engine.cart.conn.execute("SELECT * FROM notes WHERE note_id=?", (nid,)).fetchone()
    assert row["proprietary"] == 1 and row["written_by"] == "grace"
    assert row["source_ref"] == "task T-12" and row["confidence"] == 0.8
