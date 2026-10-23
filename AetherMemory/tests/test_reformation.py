import pytest

from memory_fabric import BoardItem, Decision, SetDown
from memory_fabric.reformation import ReformationError


def _board():
    return [
        BoardItem("pcs", "PRIME CONTEXT STACK: identity and directives", "pcs"),
        BoardItem("spec", "The full GUI spec text " * 40),
        BoardItem("logs", "Long tool output from the last build " * 60),
        BoardItem("notes", "Working notes about the palette " * 10),
        BoardItem("turn", "Most recent user turn", "recent"),
    ]


def test_pressure_uses_the_deployments_own_window(engine):
    assert not engine.pressure(20_000, 52_992, 9_704).needs_reformation
    assert engine.pressure(40_000, 52_992, 9_704).needs_reformation
    assert not engine.pressure(40_000, 1_048_576, 9_704).needs_reformation


def test_reform_keeps_order_and_files_what_was_set_down(engine):
    decision = Decision(keep=("notes",), set_down=(
        SetDown("spec", "GUI spec", "project fact", ("gui spec",), switch_in_days=5),
        SetDown("logs", "Build log, last run", "offloaded context", ("build log",))))
    board = engine.reform(_board(), decision, budget_tokens=2_000)
    order = [i.item_id for i in board.items]
    assert order[0] == "pcs" and order[-1] == "turn"          # PCS first, recent last
    assert "notes" in order and "spec" not in order and "logs" not in order
    assert "reformation-breadcrumb" in order
    assert "GUI spec" in board.text()
    assert len(board.written_note_ids) == 2
    hits = engine.recall(["gui spec"]).ids()                    # nothing lost: pull it back by tag
    assert board.written_note_ids[0] in hits
    assert engine.load(hits)[0]["body"].startswith("The full GUI spec text")


def test_unmentioned_items_are_filed_not_dropped(engine):
    board = engine.reform(_board(), Decision(keep=("notes",), set_down=()), budget_tokens=2_000)
    assert sorted(board.auto_filed) == ["logs", "spec"]
    assert len(board.written_note_ids) == 2
    with pytest.raises(ReformationError, match="undecided"):
        engine.reform(_board(), Decision(keep=("notes",), set_down=()), 2_000, auto_file=False)


def test_over_budget_refuses_and_writes_nothing(engine):
    decision = Decision(keep=("spec", "logs", "notes"), set_down=())
    with pytest.raises(ReformationError, match="set down at least"):
        engine.reform(_board(), decision, budget_tokens=300)
    assert engine.cart.conn.execute("SELECT count(*) FROM notes").fetchone()[0] == 2  # supplied resources only


def test_invalid_decisions_refused(engine):
    with pytest.raises(ReformationError):
        engine.reform(_board(), Decision(keep=("pcs",), set_down=()), 5_000)       # PCS is not a working item
    with pytest.raises(ReformationError):
        engine.reform(_board(), Decision(keep=("spec",), set_down=(SetDown("spec", "x"),)), 5_000)
    with pytest.raises(ReformationError):
        engine.reform(_board(), Decision(keep=("ghost",), set_down=()), 5_000)
