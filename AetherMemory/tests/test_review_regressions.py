"""Regression tests for the independent review of 2026-10-06 (12 findings, all fixed).

Each test reproduces the reviewer's failing scenario and checks the fixed behaviour.
"""
import json
import os

import pytest

from memory_fabric import BoardItem, MemoryEngine
from memory_fabric.belt import MemoryBelt
from memory_fabric.fabric import FabricError
from memory_fabric.importer import ImporterError
from memory_fabric.staff import StaffError
from memory_fabric.tools import ToolLimits


class Belt:
    max_result_chars = 300

    def openai_tools(self):
        return []

    def dispatch(self, name, args):
        raise RuntimeError("inner")


# 1. Malformed tool calls never crash ------------------------------------------------
@pytest.mark.parametrize("name,args", [
    ("memory_bury", {"id": ["n-1"]}),
    ("memory_raise", {"id": {"x": 1}}),
    ("memory_link", {"from_id": ["a"], "to_id": "b"}),
    ("memory_remember", {"title": "t", "body": "b", "kind": "fact", "links": [{"to": ["x"]}]}),
    ("memory_remember", {"title": "t", "body": "b", "kind": "fact", "links": 5}),
    ("memory_remember", {"title": "t", "body": "b", "kind": "fact", "current_for_days": float("nan")}),
    ("memory_remember", {"title": 7, "body": "b", "kind": "fact"}),
    ("memory_remember", {"title": "t", "body": "b", "kind": ["fact"]}),
    ("memory_reform", {"keep": ["w3"], "set_down": 5}),
    ("memory_reform", {"keep": ["w3"], "set_down": [{"item_id": ["x"], "title": "t"}]}),
    ("memory_reform", {"keep": ["w3"], "set_down": [{"item_id": "x", "title": 7}]}),
    ("memory_reform", {"keep": ["w3"], "set_down": [{"item_id": "x", "title": "t", "current_for_days": "3"}]}),
])
def test_malformed_calls_become_messages(engine, name, args):
    tools = engine.tools(reform_handler=lambda decision: None)
    out = tools.run(name, args)
    assert out["ok"] is False and out["text"].startswith("MEMORY TOOL ERROR")
    assert "error" in MemoryBelt(Belt(), tools).dispatch(name, args)


# 2 + 3. Reformation validates everything first, and "keep nothing" is allowed ----------
def _board():
    return [BoardItem("pcs", "PCS", "pcs"), BoardItem("w1", "one " * 30),
            BoardItem("w2", "two " * 30), BoardItem("w3", "three"), BoardItem("r", "recent", "recent")]


def test_reform_with_a_bad_entry_writes_nothing(engine):
    tools = engine.tools(reform_handler=lambda d: engine.reform(_board(), d, budget_tokens=500))
    before = engine.cart.conn.execute("SELECT count(*) FROM notes").fetchone()[0]
    out = tools.run("memory_reform", {"keep": ["w3"], "set_down": [
        {"item_id": "w1", "title": "one"}, {"item_id": "w2", "title": "two", "kind": "nonsense"}]})
    assert out["ok"] is False
    assert engine.cart.conn.execute("SELECT count(*) FROM notes").fetchone()[0] == before


def test_reform_may_keep_nothing(engine):
    tools = engine.tools(reform_handler=lambda d: engine.reform(_board(), d, budget_tokens=500))
    out = tools.dispatch("memory_reform", {"keep": [], "set_down": [
        {"item_id": "w1", "title": "one"}, {"item_id": "w2", "title": "two"},
        {"item_id": "w3", "title": "three"}]})
    assert out["ok"] and len(out["written_ids"]) == 3


# 6. A shortened write confirmation always fits ----------------------------------------
def test_shortened_confirmation_fits_even_with_escaping(engine):
    belt = MemoryBelt(Belt(), engine.tools())
    out = belt.dispatch("memory_remember", {"title": '"' * 400, "body": "b", "kind": "fact"})
    assert out["ok"] and len(json.dumps(out, ensure_ascii=False)) <= Belt.max_result_chars


# 7. Linking the same pair from two sessions makes one link ------------------------------
def test_two_sessions_link_the_same_pair_once(tmp_path, clock):
    path = str(tmp_path / "l.sqlite")
    a = MemoryEngine.create(path, "l", clock=clock)
    x = a.remember("x", "x", "fact")
    y = a.remember("y", "y", "fact")
    b = MemoryEngine.open(path, clock=clock)
    a.link(x, y, "causal")
    b.link(y, x, "causal")                         # opposite direction, other session
    assert a.cart.conn.execute("SELECT count(*) FROM links").fetchone()[0] == 1
    b.close(); a.close()


# 8. Dates are validated before they are stored ----------------------------------------
def test_bad_switch_dates_are_refused_and_recall_keeps_working(engine):
    n = engine.remember("Door", "b", "fact", ["door"])
    with pytest.raises(FabricError, match="date like"):
        engine.set_switch(n, switch_at="next friday")
    with pytest.raises(FabricError, match="date like"):
        engine.remember("x", "b", "fact", switch_at="soon")
    m = engine.remember("Other", "b", "fact")
    with pytest.raises(FabricError, match="date like"):
        engine.link(n, m, "context", switch_at="whenever")
    engine.set_switch(n, switch_at="2026-12-01")    # a plain date is fine
    assert engine.note(n)["switch_at"].startswith("2026-12-01T00:00:00")
    assert engine.recall(["door"]).ids() == [n]


# 10 + 11. The importer guard holds for the API too; re-import leaves links alone ----------
def test_import_guard_covers_the_whole_profile_and_the_api(tmp_path, clock):
    memory = tmp_path / "Aion" / "memory" / "index" / "insights"
    memory.mkdir(parents=True)
    (memory / "INS_20260801_Rule.md").write_text("# INS\n\n**Date:** 2026-08-01\n\nA rule.\n")
    profile_memory = str(tmp_path / "Aion" / "memory")
    inside_root = str(tmp_path / "Aion" / "mind.sqlite")      # beside memory/, still the profile
    with MemoryEngine.create(inside_root, "aion", clock=clock) as mind:
        with pytest.raises(ImporterError, match="inside the agent's profile"):
            mind.import_profile(profile_memory)
    outside = str(tmp_path / "minds" / "aion.sqlite")
    os.makedirs(os.path.dirname(outside))
    with MemoryEngine.create(outside, "aion", clock=clock) as mind:
        mind.import_profile(profile_memory)


def test_reimport_does_not_touch_existing_links(tmp_path, clock):
    from tests.test_importer import FILES        # the realistic sample profile
    memory = tmp_path / "Solace" / "memory"
    for rel, text in FILES.items():
        path = memory / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    out = str(tmp_path / "minds" / "s.sqlite")
    os.makedirs(os.path.dirname(out))
    with MemoryEngine.create(out, "solace", clock=clock) as mind:
        mind.import_profile(str(memory))
        before = mind.cart.conn.execute(
            "SELECT link_id, refreshed_at, reinforce_count FROM links ORDER BY link_id").fetchall()
        clock.advance(3)
        _, again = mind.import_profile(str(memory))
        after = mind.cart.conn.execute(
            "SELECT link_id, refreshed_at, reinforce_count FROM links ORDER BY link_id").fetchall()
    assert again["links"] == 0 and [tuple(r) for r in before] == [tuple(r) for r in after]


# 12. Capitals and spaces don't let an agent review or verify itself ----------------------
def test_identity_checks_ignore_case_and_spaces(engine):
    me = engine.agent_id
    with pytest.raises(StaffError):
        engine.add_review(me.upper(), "peer", "great work", 5)
    with pytest.raises(StaffError):
        engine.record_outcome("gui", "T-1", "success", 3, verified_by=f" {me} ")
