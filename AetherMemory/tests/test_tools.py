"""Tools box: model-callable memory tools (schemas + dispatch)."""
import json

import pytest

from memory_fabric import BoardItem
from memory_fabric.tools import ALL_TOOLS, MemoryToolError, MemoryTools, ToolLimits


@pytest.fixture
def tools(engine):
    return MemoryTools(engine)


def test_schemas_are_openai_functions_and_json_safe(tools):
    schemas = tools.schemas()
    names = [s["function"]["name"] for s in schemas]
    assert names == [n for n in ALL_TOOLS if n != "memory_reform"]   # reform needs a handler
    for s in schemas:
        assert s["type"] == "function"
        params = s["function"]["parameters"]
        assert params["type"] == "object" and set(params["required"]) <= set(params["properties"])
    json.dumps(schemas)
    assert 0 < tools.schema_tokens() < 3000


def test_no_delete_tool_exists(tools):
    assert not any("delete" in n or "remove" in n for n in ALL_TOOLS)


def test_remember_recall_load_round_trip(tools, engine, clock):
    out = tools.dispatch("memory_remember", {
        "title": "Door alarm", "body": "Two wrong codes trigger the alarm.",
        "kind": "safety warning", "tags": ["door code", "alarm"], "category": "Alert"})
    note_id = out["id"]
    assert out["ok"] and note_id in out["text"]

    recall = tools.dispatch("memory_recall", {"tags": ["alarm"]})
    assert note_id in recall["ids"] and "Door alarm" in recall["text"]
    assert engine.note(note_id)["pull_count"] == 0           # looking changes nothing

    loaded = tools.dispatch("memory_load", {"ids": [note_id]})
    assert "Two wrong codes" in loaded["text"]
    assert engine.note(note_id)["pull_count"] == 1           # loading is pulling up


def test_agent_writes_as_itself(tools, engine):
    with pytest.raises(MemoryToolError, match="unexpected"):
        tools.dispatch("memory_remember", {"title": "x", "body": "y", "kind": "fact",
                                           "written_by": "someone-else"})
    note_id = tools.dispatch("memory_remember", {"title": "x", "body": "y", "kind": "fact"})["id"]
    row = engine.cart.conn.execute("SELECT written_by FROM notes WHERE note_id = ?",
                                   (note_id,)).fetchone()
    assert row["written_by"] == engine.agent_id


def test_comma_string_lists_are_accepted(tools):
    tools.dispatch("memory_remember", {"title": "GPU", "body": "b", "kind": "fact",
                                       "tags": "gpu, offload"})
    assert tools.dispatch("memory_recall", {"tags": "offload"})["ids"]


def test_words_not_numbers(tools, engine):
    vital = tools.dispatch("memory_remember", {"title": "Check the path", "body": "b",
                                               "kind": "lesson", "importance": "vital"})["id"]
    assert engine.note(vital)["importance"] >= engine.settings()["fog_importance_min"]
    with pytest.raises(MemoryToolError, match="importance must be one of"):
        tools.dispatch("memory_remember", {"title": "t", "body": "b", "kind": "fact",
                                           "importance": 0.99})
    with pytest.raises(MemoryToolError, match="choose|kinds|one of"):
        tools.dispatch("memory_remember", {"title": "t", "body": "b", "kind": "blorp"})


def test_switch_by_days_or_date(tools, engine):
    a = tools.dispatch("memory_remember", {"title": "a", "body": "b", "kind": "fact",
                                           "current_for_days": 3})["id"]
    assert engine.note(a)["switch_at"].startswith("2026-10-09")
    b = tools.dispatch("memory_remember", {"title": "b", "body": "b", "kind": "fact",
                                           "current_until": "2026-10-31"})["id"]
    assert engine.note(b)["switch_at"].startswith("2026-10-31")
    with pytest.raises(MemoryToolError, match="not both"):
        tools.dispatch("memory_remember", {"title": "c", "body": "b", "kind": "fact",
                                           "current_for_days": 3, "current_until": "2026-10-31"})
    with pytest.raises(MemoryToolError, match="date like"):
        tools.dispatch("memory_remember", {"title": "c", "body": "b", "kind": "fact",
                                           "current_until": "next friday"})


def test_links_and_bad_links_write_nothing(tools, engine):
    a = tools.dispatch("memory_remember", {"title": "a", "body": "b", "kind": "fact"})["id"]
    before = engine.cart.conn.execute("SELECT count(*) FROM notes").fetchone()[0]
    with pytest.raises(MemoryToolError):
        tools.dispatch("memory_remember", {"title": "c", "body": "b", "kind": "fact",
                                           "links": [{"to": "n-missing"}]})
    assert engine.cart.conn.execute("SELECT count(*) FROM notes").fetchone()[0] == before
    b = tools.dispatch("memory_remember", {"title": "c", "body": "b", "kind": "fact",
                                           "links": [{"to": a, "relation": "causal",
                                                      "strength": "strong"}]})["id"]
    out = tools.dispatch("memory_link", {"from_id": a, "to_id": b, "relation": "causal"})
    assert out["ok"] and out["link_id"]


def test_load_budget_refuses_before_changing_anything(engine):
    tools = MemoryTools(engine, ToolLimits(load_budget_tokens=20))
    big = tools.dispatch("memory_remember", {"title": "big", "body": "word " * 200,
                                             "kind": "fact"})["id"]
    with pytest.raises(MemoryToolError, match="Nothing was loaded"):
        tools.dispatch("memory_load", {"ids": [big]})
    assert engine.note(big)["pull_count"] == 0


def test_bury_and_raise(tools, engine):
    note_id = tools.dispatch("memory_remember", {"title": "old", "body": "b", "kind": "fact"})["id"]
    tools.dispatch("memory_bury", {"id": note_id})
    assert engine.note(note_id)["strength"] == pytest.approx(0.1)
    out = tools.dispatch("memory_raise", {"id": note_id})
    assert engine.note(note_id)["strength"] > 0.5 and "Raised" in out["text"]


def test_malformed_calls_are_recoverable_messages(tools):
    with pytest.raises(MemoryToolError, match="missing \\['ids'\\]"):
        tools.dispatch("memory_load", {})
    assert tools.run('memory_recall', {})['ok']  # Tags and explicit seeds are both optional.
    with pytest.raises(MemoryToolError, match="unknown memory tool"):
        tools.dispatch("memory_delete", {"id": "x"})
    out = tools.run("memory_bury", {"id": "n-nope"})
    assert out["ok"] is False and out["text"].startswith("MEMORY TOOL ERROR")
    out = tools.run("memory_recall", "not a dict")
    assert out["ok"] is False


def test_reform_tool_uses_the_host_handler(engine):
    board = [BoardItem("pcs", "PRIME CONTEXT STACK", "pcs"),
             BoardItem("plan", "the plan " * 20), BoardItem("log", "old log line " * 60),
             BoardItem("t1", "latest turn", "recent")]
    seen = {}

    def handler(decision):
        seen["decision"] = decision
        return engine.reform(board, decision, budget_tokens=400)

    tools = MemoryTools(engine, reform_handler=handler)
    assert "memory_reform" in [s["function"]["name"] for s in tools.schemas()]
    out = tools.dispatch("memory_reform", {
        "keep": ["plan"], "set_down": [{"item_id": "log", "title": "Old build log",
                                        "tags": ["build log"]}]})
    assert out["ok"] and len(out["written_ids"]) == 1 and seen["decision"].keep == ("plan",)
    assert tools.dispatch("memory_recall", {"tags": ["build log"]})["ids"]


def test_enabled_subset(engine):
    tools = MemoryTools(engine, enabled=("memory_recall", "memory_load"))
    assert [s["function"]["name"] for s in tools.schemas()] == ["memory_recall", "memory_load"]
    with pytest.raises(MemoryToolError):
        tools.dispatch("memory_remember", {"title": "t", "body": "b", "kind": "fact"})
    with pytest.raises(MemoryToolError):
        MemoryTools(engine, enabled=("memory_delete",))
