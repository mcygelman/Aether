"""Belt box (memory tools on an existing tool belt) and hydration box (session start)."""
import hashlib
import json

import pytest

from memory_fabric import MemoryEngine
from memory_fabric.belt import MemoryBelt
from memory_fabric.hydration import load_session, orientation
from memory_fabric.tokens import estimate_tokens
from memory_fabric.tools import MemoryToolError, ToolLimits


class FakeBridgeBelt:
    """The Aether Bridge ToolBelt contract, minimally."""
    def __init__(self, max_result_chars=24_000):
        self.max_result_chars = max_result_chars
        self.roots = {"project": "/p"}
        self.calls = []

    def openai_tools(self):
        return [{"type": "function", "function": {"name": "read_file", "description": "",
                 "parameters": {"type": "object", "properties": {}, "required": []}}}]

    def dispatch(self, name, args):
        self.calls.append(name)
        if name != "read_file":
            raise RuntimeError(f"unknown tool '{name}'")
        return {"content": "file text"}


def test_belt_routes_and_keeps_the_inner_contract(engine):
    inner = FakeBridgeBelt()
    belt = MemoryBelt(inner, engine.tools())
    names = [t["function"]["name"] for t in belt.openai_tools()]
    assert names[0] == "read_file" and "memory_recall" in names
    assert belt.max_result_chars == 24_000 and belt.roots == {"project": "/p"}   # same belt to checks
    assert belt.dispatch("read_file", {}) == {"content": "file text"} and inner.calls == ["read_file"]
    out = belt.dispatch("memory_remember", {"title": "t", "body": "b", "kind": "fact"})
    assert out["ok"] and inner.calls == ["read_file"]                           # memory never leaks inward
    err = belt.dispatch("memory_bury", {"id": "n-missing"})
    assert set(err) == {"error"} and "MEMORY TOOL ERROR" in err["error"]
    json.dumps(belt.openai_tools())


def test_belt_refuses_to_shadow_and_guards_result_size(engine):
    class Clashing(FakeBridgeBelt):
        def openai_tools(self):
            return [{"type": "function", "function": {"name": "memory_recall"}}]
    with pytest.raises(MemoryToolError, match="shadow"):
        MemoryBelt(Clashing(), engine.tools())
    # Without the limit in ToolLimits, the belt is the backstop: writes shortened, reads refused.
    tiny = MemoryBelt(FakeBridgeBelt(max_result_chars=120), engine.tools())
    out = tiny.dispatch("memory_remember", {"title": "A long memory title " * 4, "body": "x " * 40,
                                            "kind": "fact", "tags": ["long"]})
    assert out["ok"] and out["id"] and len(json.dumps(out)) <= 120     # written, never "failed"
    nid = out["id"]
    assert "error" in tiny.dispatch("memory_load", {"ids": [nid]})
    # With the limit in ToolLimits (as for_context sets it), load refuses before refreshing.
    before = engine.note(nid)["pull_count"]
    guarded = MemoryBelt(FakeBridgeBelt(max_result_chars=120),
                         engine.tools(ToolLimits(max_result_chars=120)))
    out = guarded.dispatch("memory_load", {"ids": [nid]})
    assert "Nothing was loaded" in out["error"]
    assert engine.note(nid)["pull_count"] == before


def test_limits_follow_the_capability_record():
    small = ToolLimits.for_context(8_192)
    big = ToolLimits.for_context(1_000_000)
    assert small.recall_budget_tokens == 400 and small.load_budget_tokens == 1500
    assert big.recall_budget_tokens == 4000 and big.load_budget_tokens == 24000
    capped = ToolLimits.for_context(1_000_000, max_tool_result_chars=24_000)
    assert capped.load_budget_tokens * 3.5 <= 24_000 * 0.8 + 3.5
    with pytest.raises(MemoryToolError):
        ToolLimits.for_context(0)
    with pytest.raises(MemoryToolError):
        ToolLimits.for_context(8192, max_tool_result_chars=100)


def _mind(path, clock):
    eng = MemoryEngine.create(path, "solace", clock=clock)
    eng.remember("Fonts must be licensed", "Check the licence first.", "safety warning",
                 ["fonts"], category="Alert")
    eng.remember("Verify ceilings, don't inherit them", "A default is a decision.", "principle",
                 ["capability"])
    eng.remember("Grid is 8px", "layout fact", "project fact", ["grid"])
    eng.close()


def test_session_hydration_is_budgeted_read_only_and_bridge_shaped(tmp_path, clock):
    path = str(tmp_path / "s.sqlite")
    _mind(path, clock)
    before = hashlib.sha256(open(path, "rb").read()).hexdigest()
    with MemoryEngine.open(path, clock=clock, read_only=True) as mind:
        sections, report = load_session(mind, 600, task_tags=["grid"])
        text = "".join(sections)
        assert sum(estimate_tokens(s) for s in sections) <= 600
        assert "RULE::MEMORY_CHECK" in text and "Fonts must be licensed" in text
        assert text.index("Fonts must be licensed") < text.index("Verify ceilings")   # alerts first
        assert "Grid is 8px" in text.split("MEMORY RECALL FOR THIS TASK")[1]
        assert report["policy"] == "AETHER-MEMORY-FABRIC-1" and len(report["orientation_ids"]) == 4
        assert "Nothing was loaded" in orientation(report)
    assert hashlib.sha256(open(path, "rb").read()).hexdigest() == before


def test_hydration_respects_tiny_budgets(engine):
    for i in range(50):
        engine.remember(f"Alert {i}", "x" * 200, "safety warning", ["a"], category="Alert")
    sections, report = load_session(engine, 300)
    assert sum(estimate_tokens(s) for s in sections) <= 300
    assert report["orientation_omitted"] > 0
    with pytest.raises(ValueError):
        load_session(engine, 20)


def test_belt_passes_private_helpers_and_settings_to_the_inner_belt(engine):
    # The Bridge's Studio calls belt._resolve and may set belt.max_result_chars after wrapping.
    inner = FakeBridgeBelt()
    inner._resolve = lambda root, path: (root, path)
    belt = MemoryBelt(inner, engine.tools())
    assert belt._resolve("workspace", "a.txt") == ("workspace", "a.txt")
    belt.max_result_chars = 1234
    assert inner.max_result_chars == 1234 and belt.max_result_chars == 1234
    with pytest.raises(AttributeError):
        belt._missing_helper
