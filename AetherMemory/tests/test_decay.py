import random

import pytest

from memory_fabric import decay
from memory_fabric.clock import add_days

T0 = "2026-10-06T00:00:00Z"


def test_slow_phase_then_fast_phase():
    switch = add_days(T0, 7)
    assert decay.phase_factor(T0, switch, add_days(T0, 3), 0.01, 0.4) == pytest.approx(0.99 ** 3)
    assert decay.phase_factor(T0, switch, add_days(T0, 10), 0.01, 0.4) == pytest.approx(
        0.99 ** 7 * 0.6 ** 3)


def test_refresh_after_switch_decays_fast_from_refresh():
    refreshed, switch = add_days(T0, 10), add_days(T0, 7)
    assert decay.phase_factor(refreshed, switch, add_days(T0, 12), 0.01, 0.4) == pytest.approx(0.6 ** 2)


def test_future_now_before_refresh_is_full_strength():
    assert decay.phase_factor(add_days(T0, 5), add_days(T0, 9), T0, 0.01, 0.4) == 1.0


def test_floor_and_ceiling():
    assert decay.note_strength(0.8, T0, T0, add_days(T0, 365), 0.5, 0.9, floor=0.1) == 0.1
    assert decay.note_strength(1.0, T0, T0, T0, 0.0, 0.0, 0.1, adjust=0.5) == 1.0


def test_adjustment_fades_back_to_computed():
    until = add_days(T0, 10)
    assert decay.adjustment_value(0.4, T0, until, T0) == pytest.approx(0.4)
    assert decay.adjustment_value(0.4, T0, until, add_days(T0, 5)) == pytest.approx(0.2)
    assert decay.adjustment_value(0.4, T0, until, add_days(T0, 20)) == 0.0
    assert decay.adjustment_value(-0.3, T0, None, add_days(T0, 99)) == -0.3


def test_sql_twin_matches_python(engine, clock):
    """The temporary SQL views must compute exactly what the Python maths does."""
    rng = random.Random(7)
    cart = engine.cart
    for i in range(300):
        nid = engine.remember(f"note {i}", "body", rng.choice(
            ["project fact", "safety warning", "standing lesson", "scratch", "core"]),
            switch_in_days=rng.uniform(0.5, 60), importance=rng.choice([None, 0.3, 0.95]))
        if rng.random() < 0.3:
            engine.adjust_depth(nid, rng.uniform(-0.5, 0.5), rng.choice([None, 4.0]))
        clock.advance(rng.uniform(0, 0.5))
    for later in (1, 9, 40, 200):
        clock.advance(later)
        cart.sync_clock()
        floor = cart.setting("floor")
        rows = cart.conn.execute(
            "SELECT n.*, p.importance AS p_imp, p.slow_rate AS p_slow, p.fast_rate AS p_fast,"
            " s.strength AS sql_strength FROM notes n JOIN profiles p USING(profile)"
            " JOIN temp.note_state s USING(note_id)").fetchall()
        for r in rows:
            expected = decay.note_strength(
                r["importance_override"] or r["p_imp"], r["refreshed_at"], r["switch_at"],
                clock.now(), r["slow_rate_override"] or r["p_slow"],
                r["fast_rate_override"] or r["p_fast"], floor, r["adjust"],
                r["adjust_set_at"], r["adjust_until"])
            assert r["sql_strength"] == pytest.approx(expected, abs=1e-9)


def test_sql_twin_links_match_python(engine, clock):
    a = engine.remember("a", "", "project fact", switch_in_days=3)
    b = engine.remember("b", "", "standing lesson")
    engine.link(a, b, "causal", 0.9)
    clock.advance(12)
    cart = engine.cart
    cart.sync_clock()
    row = cart.conn.execute("SELECT l.*, s.strength AS sql_strength FROM links l"
                            " JOIN temp.link_state s USING(link_id)").fetchone()
    expected = decay.link_strength(row["strength"], row["refreshed_at"], row["switch_at"],
                                   clock.now(), row["slow_rate"], row["fast_rate"],
                                   cart.setting("floor"))
    assert row["sql_strength"] == pytest.approx(expected, abs=1e-12)
