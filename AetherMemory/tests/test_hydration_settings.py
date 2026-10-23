"""Decision 49: the hydration budget is a share of the measured working context,
between a floor and a ceiling, and the agent tunes it itself (memory_settings)."""
import hashlib
import sqlite3

import pytest

from memory_fabric import MemoryEngine
from memory_fabric.errors import MemoryFabricError
from memory_fabric.hydration import (MAX_WINDOW_SHARE, HydrationLimits, load_session,
                                     orientation, session_budget)
from memory_fabric.tokens import estimate_tokens
from memory_fabric.tools import MemoryToolError


@pytest.mark.parametrize("window, budget", [
    (8_192, 600),          # small local model: the floor
    (32_768, 655),         # 2%
    (128_000, 2_560),      # 2%
    (1_000_000, 20_000),   # 2%
    (4_000_000, 40_000),   # the ceiling
    (2_000, 500),          # tiny window: held to 25% of the window, even below the floor
])
def test_default_budget_follows_the_window(window, budget):
    assert HydrationLimits().budget(window) == budget


@pytest.mark.parametrize("bad", [0, -5, 1.5, None, True, "128000"])
def test_window_must_be_a_positive_integer(bad):
    with pytest.raises(ValueError):
        HydrationLimits().budget(bad)


def test_budget_never_exceeds_a_quarter_of_the_window():
    limits = HydrationLimits(share=0.10, floor=20_000, ceiling=100_000)
    for window in (1_000, 8_000, 50_000, 3_000_000):
        assert limits.budget(window) <= int(window * MAX_WINDOW_SHARE)


def test_new_cartridges_carry_the_defaults_as_agent_tunable_settings(engine):
    rows = {r["key"]: r for r in engine.settings_table()}
    assert rows["hydration_share"]["value"] == 0.02
    assert rows["hydration_floor"]["value"] == 600
    assert rows["hydration_ceiling"]["value"] == 40_000
    assert all(rows[k]["tunable_by_agent"] for k in
               ("hydration_share", "hydration_floor", "hydration_ceiling"))


def test_session_uses_the_agents_own_settings_and_says_so(engine):
    engine.remember("Fonts must be licensed", "Check the licence.", "safety warning",
                    ["fonts"], category="Alert")
    sections, report = load_session(engine, working_context_tokens=128_000)
    assert report["budget_tokens"] == 2_560
    assert report["budget_basis"]["source"] == "agent settings"
    assert sum(estimate_tokens(s) for s in sections) <= 2_560
    text = orientation(report)
    assert "2.0% of a 128,000-token working context" in text and "memory_settings" in text

    engine.change_setting("hydration_share", 0.05, reason="I want more of my past at hand")
    _, report = load_session(engine, working_context_tokens=128_000)
    assert report["budget_tokens"] == 6_400


def test_host_override_and_unknown_window(engine):
    assert session_budget(engine, 128_000, budget_tokens=900)[0] == 900
    assert session_budget(engine, 128_000, budget_tokens=900)[1]["source"] == "host override"
    budget, basis = session_budget(engine)
    assert budget == 2_000 and basis["source"] == "default"
    with pytest.raises(ValueError):
        session_budget(engine, budget_tokens=0)


def test_engine_session_start_passes_the_window(engine):
    _, report = engine.session_start(working_context_tokens=1_000_000)
    assert report["budget_tokens"] == 20_000


# -- the agent tunes itself with memory_settings ----------------------------------

def test_agent_lists_its_settings(engine):
    result = engine.tools().dispatch("memory_settings", {})
    assert "hydration_share = 0.02 (0.005...0.1)" in result["text"]
    assert "floor = 0.1 (0.1...0.1) [fixed]" in result["text"]      # never-delete stays fixed
    assert result["settings"]["hydration_ceiling"] == 40_000


def test_agent_changes_a_setting_with_a_reason_and_can_undo(engine):
    tools = engine.tools()
    result = tools.dispatch("memory_settings", {"key": "hydration_share", "value": 0.03,
                                                "reason": "headers were cut off"})
    assert result["old_value"] == 0.02 and result["new_value"] == 0.03
    assert "To undo: memory_settings(key='hydration_share', value=0.02" in result["text"]
    assert engine.settings()["hydration_share"] == 0.03

    history = tools.dispatch("memory_settings", {"key": "hydration_share"})["text"]
    assert "grace: 0.02 -> 0.03 (headers were cut off)" in history

    tools.dispatch("memory_settings", {"key": "hydration_share", "value": 0.02, "reason": "undo"})
    assert engine.settings()["hydration_share"] == 0.02
    journal = engine.setting_history("hydration_share")
    assert [(h["old_value"], h["new_value"]) for h in journal[:2]] == [(0.03, 0.02), (0.02, 0.03)]


@pytest.mark.parametrize("args, message", [
    ({"key": "hydration_share", "value": 0.5, "reason": "more"}, "within 0.005..0.1"),
    ({"key": "hydration_ceiling", "value": 250_000, "reason": "more"}, "within 1000..100000"),
    ({"key": "hydration_floor", "value": 100, "reason": "less"}, "within 400..20000"),
    ({"key": "hydration_share", "value": 0.03}, "say why"),
    ({"key": "hydration_share", "value": 0.03, "reason": "  "}, "say why"),
    ({"key": "no_such_setting", "value": 1, "reason": "x"}, "unknown setting"),
    ({"value": 0.03, "reason": "x"}, "give key, value and reason"),
    ({"key": "floor", "value": 0.1, "reason": "x"}, "not tunable by the agent"),
    ({"key": "hydration_share", "value": "lots", "reason": "x"}, "number"),
])
def test_refusals_change_nothing(engine, args, message):
    before = engine.settings()
    with pytest.raises(MemoryToolError, match=message):
        engine.tools().dispatch("memory_settings", args)
    assert engine.settings() == before
    assert not [h for h in engine.setting_history() if h["changed_by"] == "grace"]


def test_floor_and_ceiling_cannot_cross(engine):
    engine.change_setting("hydration_ceiling", 5_000, reason="small")
    with pytest.raises(MemoryFabricError, match="cannot exceed"):
        engine.change_setting("hydration_floor", 6_000, reason="above the ceiling")
    engine.change_setting("hydration_floor", 5_000, reason="equal is fine")
    with pytest.raises(MemoryFabricError, match="cannot be below"):
        engine.change_setting("hydration_ceiling", 4_000, reason="below the floor")
    assert (engine.settings()["hydration_floor"], engine.settings()["hydration_ceiling"]) == (5_000, 5_000)


def test_read_only_memory_offers_no_settings_tool(tmp_path, clock):
    path = str(tmp_path / "r.sqlite")
    MemoryEngine.create(path, "grace", clock=clock).close()
    with MemoryEngine.open(path, clock=clock, read_only=True) as mind:
        assert "memory_settings" not in mind.tools().enabled


def test_older_cartridges_get_the_hydration_settings(tmp_path, clock):
    path = str(tmp_path / "old.sqlite")
    MemoryEngine.create(path, "grace", clock=clock).close()
    raw = sqlite3.connect(path)            # make it look like an engine before decision 49
    raw.execute("DELETE FROM config WHERE key LIKE 'hydration_%'")
    raw.commit()
    raw.close()
    before = hashlib.sha256(open(path, "rb").read()).hexdigest()
    with MemoryEngine.open(path, clock=clock, read_only=True) as mind:   # defaults, no writes
        assert load_session(mind, working_context_tokens=128_000)[1]["budget_tokens"] == 2_560
    assert hashlib.sha256(open(path, "rb").read()).hexdigest() == before
    with MemoryEngine.open(path, clock=clock) as mind:                    # topped up, journaled
        assert {"hydration_share", "hydration_floor", "hydration_ceiling"} <= {
            r["key"] for r in mind.settings_table()}
        assert {h["key"] for h in mind.setting_history(limit=10)
                if h["changed_by"] == "engine"} >= {"hydration_share"}
