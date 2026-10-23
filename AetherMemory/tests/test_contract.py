"""The engine's public surface and end-to-end behaviour on a real file."""
import time

from memory_fabric import FixedClock, MemoryEngine
from memory_fabric.__main__ import main


def test_public_surface_is_stable():
    expected = {"create", "open", "close", "agent_id", "open_report", "remember", "link",
                "add_tags", "bury", "raise_note", "set_importance", "set_switch", "adjust_depth",
                "note", "recall", "load", "pressure", "reform", "trigger", "pcs_directive",
                "add_review", "reviews", "record_outcome", "set_avatar", "set_scan_line", "card",
                "resume", "settings", "change_setting", "profiles", "tune_profile", "export_clean"}
    assert expected <= {n for n in dir(MemoryEngine) if not n.startswith("_")}


def test_file_cartridge_end_to_end(tmp_path):
    clock = FixedClock("2026-10-06T09:00:00Z")
    path = str(tmp_path / "kirito.sqlite")
    with MemoryEngine.create(path, "kirito", clock=clock) as eng:
        a = eng.remember("Plan per-driver context", "Set allocation per deployment", "lesson",
                         ["planning", "context allocation"], category="Alert")
    clock.advance(30)
    with MemoryEngine.open(path, clock=clock) as eng:
        assert eng.recall(["planning"]).ids() == [a]
        eng.load([a])
    with MemoryEngine.open(path, clock=clock) as eng:
        assert eng.note(a)["pull_count"] == 1 and not eng.open_report.tampered


def test_recall_scales(engine, clock):
    """5,000 notes and 10,000 links: recall stays interactive."""
    ids = []
    for i in range(5000):
        ids.append(engine.remember(f"note {i}", "body " * 10, ["lesson", "project fact",
                                   "safety warning"][i % 3], [f"tag{i % 50}", "common"]))
    conn = engine.cart.conn
    now = clock.now()
    with conn:
        conn.executemany(
            "INSERT OR IGNORE INTO links(a,b,relation,strength,created_at,refreshed_at,switch_at,"
            "slow_rate,fast_rate) VALUES (?,?,?,?,?,?,?,?,?)",
            [(ids[i], ids[(i * 7 + 13) % 5000], "conceptual", 0.8, now, now, "2026-12-01T00:00:00Z",
              0.01, 0.1) for i in range(5000)] +
            [(ids[i], ids[(i * 11 + 3) % 5000], "context", 0.6, now, now, "2026-12-01T00:00:00Z",
              0.01, 0.1) for i in range(5000)])
    clock.advance(5)
    started = time.perf_counter()
    result = engine.recall(["tag7"], budget_tokens=2000)
    elapsed = time.perf_counter() - started
    assert result.headers and elapsed < 3.0


def test_cli_simulate_and_demo(capsys):
    assert main(["simulate"]) == 0
    assert "scenarios pass" in capsys.readouterr().out
    assert main(["demo"]) == 0
    assert "MEMORY RECALL" in capsys.readouterr().out
    assert main([]) == 2
