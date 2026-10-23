"""Decay box: the maths of the memory fabric, in two identical forms.

Inputs:  importance, dates (refreshed / switch / now), daily decay rates,
         an optional agent depth adjustment, and the floor.
Outputs: a strength between the floor and 1.0 (depth = 1 - strength).

Two-phase decay with a date switch (decisions 15-16):
  * slow phase: from the last refresh until the switch date,
  * fast phase: from the switch date (or the refresh, if later) onward.
  factor = (1 - slow) ** slow_days * (1 - fast) ** fast_days

The same formula exists twice:
  * in Python (functions below), used for single values and tests;
  * in SQL (temp_view_sql), used by recall so SQLite computes strength for
    every note at once. The SQL lives in TEMPORARY views created when a
    cartridge opens and is never stored in the cartridge file (decision 21).
A parity test keeps the two forms identical.
"""
from __future__ import annotations

from typing import Optional

from .clock import days_between


def phase_factor(refreshed_at: str, switch_at: str, now: str,
                 slow_rate: float, fast_rate: float) -> float:
    """Multiplier in (0, 1] from two-phase decay."""
    t_minus_r = days_between(refreshed_at, now)
    s_minus_r = days_between(refreshed_at, switch_at)
    slow_days = max(0.0, min(t_minus_r, s_minus_r))
    fast_days = max(0.0, t_minus_r - max(s_minus_r, 0.0))
    return (1.0 - slow_rate) ** slow_days * (1.0 - fast_rate) ** fast_days


def adjustment_value(adjust: float, set_at: Optional[str], until: Optional[str],
                     now: str) -> float:
    """An agent's manual depth nudge, fading linearly back to zero by `until`.

    Positive raises a note, negative buries it. With no `until` it stays.
    """
    if not adjust:
        return 0.0
    if until is None or set_at is None:
        return adjust
    span = days_between(set_at, until)
    if span <= 0:
        return 0.0
    fraction = 1.0 - days_between(set_at, now) / span
    return adjust * max(0.0, min(1.0, fraction))


def note_strength(importance: float, refreshed_at: str, switch_at: str, now: str,
                  slow_rate: float, fast_rate: float, floor: float,
                  adjust: float = 0.0, adjust_set_at: Optional[str] = None,
                  adjust_until: Optional[str] = None) -> float:
    raw = importance * phase_factor(refreshed_at, switch_at, now, slow_rate, fast_rate)
    raw += adjustment_value(adjust, adjust_set_at, adjust_until, now)
    return min(1.0, max(floor, raw))


def link_strength(strength: float, refreshed_at: str, switch_at: str, now: str,
                  slow_rate: float, fast_rate: float, floor: float) -> float:
    raw = strength * phase_factor(refreshed_at, switch_at, now, slow_rate, fast_rate)
    return min(1.0, max(floor, raw))


def depth(strength: float) -> float:
    """Depth is how buried a note is: 0 = at the surface, near 1 = deep."""
    return round(1.0 - strength, 6)


# --- SQL twin -------------------------------------------------------------

def _factor_sql(refreshed: str, switch: str, slow: str, fast: str) -> str:
    now = "julianday((SELECT now FROM temp.fabric_clock))"
    r, s = f"julianday({refreshed})", f"julianday({switch})"
    slow_days = f"MAX(0.0, MIN({now} - {r}, {s} - {r}))"
    fast_days = f"MAX(0.0, ({now} - {r}) - MAX({s} - {r}, 0.0))"
    return f"(pow(1.0 - {slow}, {slow_days}) * pow(1.0 - {fast}, {fast_days}))"


def _adjust_sql() -> str:
    now = "julianday((SELECT now FROM temp.fabric_clock))"
    return (
        "(CASE WHEN n.adjust = 0 THEN 0.0 "
        "WHEN n.adjust_until IS NULL OR n.adjust_set_at IS NULL THEN n.adjust "
        "WHEN julianday(n.adjust_until) <= julianday(n.adjust_set_at) THEN 0.0 "
        f"ELSE n.adjust * MAX(0.0, MIN(1.0, 1.0 - ({now} - julianday(n.adjust_set_at)) "
        "/ (julianday(n.adjust_until) - julianday(n.adjust_set_at)))) END)"
    )


FLOOR_SQL = "(SELECT value FROM main.config WHERE key = 'floor')"


def temp_view_sql() -> list:
    """Statements the engine runs on open. TEMP objects never reach the file."""
    note_importance = "COALESCE(n.importance_override, p.importance)"
    note_factor = _factor_sql("n.refreshed_at", "n.switch_at",
                              "COALESCE(n.slow_rate_override, p.slow_rate)",
                              "COALESCE(n.fast_rate_override, p.fast_rate)")
    link_factor = _factor_sql("l.refreshed_at", "l.switch_at", "l.slow_rate", "l.fast_rate")
    return [
        "CREATE TEMP TABLE IF NOT EXISTS fabric_clock (now TEXT NOT NULL)",
        "DROP VIEW IF EXISTS temp.note_state",
        "DROP VIEW IF EXISTS temp.link_state",
        f"""CREATE TEMP VIEW note_state AS
            SELECT n.note_id, n.title, n.body, n.profile, n.category, n.stratum,
                   n.proprietary, n.pull_count, n.switch_at, n.refreshed_at,
                   {note_importance} AS importance,
                   MIN(1.0, MAX({FLOOR_SQL},
                       {note_importance} * {note_factor} + {_adjust_sql()})) AS strength
            FROM main.notes n JOIN main.profiles p ON p.profile = n.profile""",
        f"""CREATE TEMP VIEW link_state AS
            SELECT l.link_id, l.a, l.b, l.relation, l.reinforce_count,
                   MIN(1.0, MAX({FLOOR_SQL}, l.strength * {link_factor})) AS strength
            FROM main.links l""",
    ]
