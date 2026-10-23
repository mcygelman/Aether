"""Fabric box: writing to an agent's memory.

Inputs:  an open Cartridge plus what the agent wants to remember, link,
         bury, raise or adjust.
Outputs: note ids / link ids, and note records with their current strength.

Design rules carried here:
  * Notes double as tags: a note's title is always one of its tags.
  * The agent chooses a KIND in words and a SWITCH DATE; the profile supplies
    the numbers (decision 20). Per-note overrides are optional.
  * Links inherit their decay from the shorter-lived of their two notes,
    unless told otherwise, so the agent rarely sets a link dial.
  * There is NO delete. Forgetting = bury (decision 13).
"""
from __future__ import annotations

import re
import uuid
from typing import Iterable, Optional, Sequence

from .errors import MemoryFabricError
from . import kinds, schema
from .clock import add_days, days_between, parse_iso, to_iso
from .history import atomic


class FabricError(MemoryFabricError, ValueError):
    """Any refusal from the fabric box."""


def normalize_tag(tag: str) -> str:
    cleaned = re.sub(r"\s+", " ", str(tag).strip().lower())
    if not cleaned:
        raise FabricError("empty tag")
    return cleaned


def _iso(value, what: str) -> str:
    """Any date the engine stores goes through here, so strength can always be computed."""
    try:
        return to_iso(parse_iso(str(value)))
    except (TypeError, ValueError):
        raise FabricError(f"{what} must be a date like 2026-10-31 or 2026-10-31T09:00:00Z, "
                          f"not {value!r}") from None


def _require_note(cart, note_id: str) -> dict:
    row = cart.conn.execute("SELECT * FROM notes WHERE note_id = ?", (note_id,)).fetchone()
    if row is None:
        raise FabricError(f"unknown note: {note_id}")
    return dict(row)


def _check_unit(name: str, value: Optional[float], low: float = 0.0, high: float = 1.0) -> None:
    if value is not None and not low <= value <= high:
        raise FabricError(f"{name} must be within {low}..{high}")


@atomic
def remember(cart, title: str, body: str, kind: str, tags: Iterable[str] = (), *,
             written_by: str, switch_at: Optional[str] = None,
             switch_in_days: Optional[float] = None, category: str = "Information",
             stratum: Optional[str] = None, importance: Optional[float] = None,
             links: Sequence = (), proprietary: bool = False,
             source_ref: Optional[str] = None, confidence: Optional[float] = None,
             note_id: Optional[str] = None) -> str:
    """Write one memory. Returns its note id.

    `links` items are (other_note_id, relation) or (other_note_id, relation, strength).
    """
    if not title or not title.strip():
        raise FabricError("title is required")
    if not written_by or not written_by.strip():
        raise FabricError("written_by is required (who wrote this memory)")
    if category not in schema.CATEGORIES:
        raise FabricError(f"category must be one of {schema.CATEGORIES}")
    if stratum is not None and stratum not in schema.STRATA:
        raise FabricError(f"stratum must be one of {schema.STRATA}")
    _check_unit("importance", importance, 0.1, 1.0)
    _check_unit("confidence", confidence)
    if switch_at is not None and switch_in_days is not None:
        raise FabricError("give switch_at or switch_in_days, not both")
    if switch_at is not None:
        switch_at = _iso(switch_at, "switch_at")

    for item in links:   # validate links first, so a bad link writes nothing
        if len(item) < 2 or item[1] not in schema.RELATIONS:
            raise FabricError(f"bad link {item!r}: relation must be one of {schema.RELATIONS}")
        _require_note(cart, item[0])
        if len(item) > 2:
            _check_unit("link strength", item[2], 0.1, 1.0)

    profile_name = kinds.resolve(kind, [p["profile"] for p in cart.profiles()])
    profile = cart.profile(profile_name)
    now = cart.clock.now()
    if switch_at is None:
        switch_at = add_days(now, switch_in_days if switch_in_days is not None
                             else profile["horizon_days"])
    note_id = note_id or f"n-{uuid.uuid4().hex[:12]}"
    all_tags = {normalize_tag(title)} | {normalize_tag(t) for t in tags}

    with cart.transaction() as conn:
        if conn.execute("SELECT 1 FROM notes WHERE note_id = ?", (note_id,)).fetchone():
            raise FabricError(f"note id already exists: {note_id}")
        conn.execute(
            "INSERT INTO notes(note_id, title, body, profile, category, stratum, created_at,"
            " refreshed_at, switch_at, importance_override, proprietary, written_by, source_ref,"
            " confidence) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (note_id, title.strip(), body or "", profile_name, category, stratum, now, now,
             switch_at, importance, int(bool(proprietary)), written_by, source_ref, confidence))
        conn.executemany("INSERT INTO tags(note_id, tag) VALUES (?, ?)",
                         [(note_id, t) for t in sorted(all_tags)])
    for item in links:
        other, relation = item[0], item[1]
        strength = item[2] if len(item) > 2 else None
        link(cart, note_id, other, relation, strength)
    return note_id


def link(cart, a: str, b: str, relation: str = "context", strength: Optional[float] = None,
         switch_at: Optional[str] = None) -> int:
    """Join two notes. Linking an existing pair again strengthens the link."""
    if a == b:
        raise FabricError("a note cannot link to itself")
    if relation not in schema.RELATIONS:
        raise FabricError(f"relation must be one of {schema.RELATIONS}")
    na, nb = _require_note(cart, a), _require_note(cart, b)
    strength = cart.setting("default_link_strength") if strength is None else strength
    _check_unit("strength", strength, 0.1, 1.0)
    if switch_at is not None:
        switch_at = _iso(switch_at, "switch_at")
    now = cart.clock.now()
    pa, pb = cart.profile(na["profile"]), cart.profile(nb["profile"])
    shorter = pa if pa["link_fast_rate"] >= pb["link_fast_rate"] else pb
    if switch_at is None:
        # The earlier of the two notes' switch dates, but never in the past:
        # a new link is a new association and starts life in its slow phase.
        # (Calibration finding, scenario 06.)
        future = [s for s in (na["switch_at"], nb["switch_at"]) if s > now]
        switch_at = min(future) if future else add_days(now, shorter["refresh_extends_days"])
    # Check and write under one write lock, so two sessions linking the same pair
    # (in either direction) can't race into a crash or a duplicate.
    with cart.transaction(immediate=True) as conn:
        existing = conn.execute(
            "SELECT link_id FROM links WHERE relation = ? AND ((a = ? AND b = ?) OR (a = ? AND b = ?))",
            (relation, a, b, b, a)).fetchone()
        if existing:
            conn.execute(
                "UPDATE links SET strength = MAX(strength, ?), refreshed_at = ?,"
                " reinforce_count = reinforce_count + 1 WHERE link_id = ?",
                (strength, now, existing["link_id"]))
            return int(existing["link_id"])
        cursor = conn.execute(
            "INSERT INTO links(a, b, relation, strength, created_at, refreshed_at, switch_at,"
            " slow_rate, fast_rate) VALUES (?,?,?,?,?,?,?,?,?)",
            (a, b, relation, strength, now, now, switch_at,
             shorter["link_slow_rate"], shorter["link_fast_rate"]))
        return int(cursor.lastrowid)


def add_tags(cart, note_id: str, tags: Iterable[str]) -> None:
    _require_note(cart, note_id)
    with cart.transaction() as conn:
        conn.executemany("INSERT OR IGNORE INTO tags(note_id, tag) VALUES (?, ?)",
                         [(note_id, normalize_tag(t)) for t in tags])


def bury(cart, note_id: str) -> None:
    """Forget, the Aether way: sink the note to the floor. Nothing is deleted.

    Importance drops to the floor (so the fog cannot lift it), decay switches
    to its fast phase now, and any manual raise is cleared.
    """
    _require_note(cart, note_id)
    now = cart.clock.now()
    with cart.transaction() as conn:
        conn.execute(
            "UPDATE notes SET importance_override = ?, switch_at = ?, adjust = 0,"
            " adjust_set_at = NULL, adjust_until = NULL WHERE note_id = ?",
            (cart.setting("floor"), now, note_id))


@atomic
def raise_note(cart, note_id: str, importance: Optional[float] = None) -> None:
    """Bring a note back up: restore (or set) its importance and refresh it."""
    note = _require_note(cart, note_id)
    _check_unit("importance", importance, 0.1, 1.0)
    profile = cart.profile(note["profile"])
    now = cart.clock.now()
    new_switch = max(note["switch_at"], add_days(now, profile["horizon_days"]))
    with cart.transaction() as conn:
        conn.execute(
            "UPDATE notes SET importance_override = ?, refreshed_at = ?, switch_at = ?"
            " WHERE note_id = ?", (importance, now, new_switch, note_id))


def set_importance(cart, note_id: str, importance: float) -> None:
    _require_note(cart, note_id)
    _check_unit("importance", importance, 0.1, 1.0)
    with cart.transaction() as conn:
        conn.execute("UPDATE notes SET importance_override = ? WHERE note_id = ?",
                     (importance, note_id))


def set_switch(cart, note_id: str, switch_at: Optional[str] = None,
               in_days: Optional[float] = None) -> None:
    """Move a note's switch date (e.g. the project was extended)."""
    _require_note(cart, note_id)
    if (switch_at is None) == (in_days is None):
        raise FabricError("give exactly one of switch_at or in_days")
    when = _iso(switch_at, "switch_at") if switch_at is not None else add_days(cart.clock.now(), in_days)
    with cart.transaction() as conn:
        conn.execute("UPDATE notes SET switch_at = ? WHERE note_id = ?", (when, note_id))


def adjust_depth(cart, note_id: str, delta: float, fade_days: Optional[float] = None) -> None:
    """The agent's own nudge ("mind over deterministic computer").

    `delta` > 0 raises the note, < 0 sinks it. With `fade_days`, the nudge
    fades back to the computed value by then, so it cannot go stale.
    """
    _require_note(cart, note_id)
    _check_unit("delta", delta, -1.0, 1.0)
    if fade_days is not None and fade_days <= 0:
        raise FabricError("fade_days must be positive")
    now = cart.clock.now()
    until = add_days(now, fade_days) if fade_days else None
    with cart.transaction() as conn:
        conn.execute(
            "UPDATE notes SET adjust = ?, adjust_set_at = ?, adjust_until = ? WHERE note_id = ?",
            (delta, now, until, note_id))


def refresh(cart, note_ids: Iterable[str]) -> None:
    """Pulling a memory up and using it resets its decay clock.

    The switch date only ever moves later: a refresh extends a memory's
    current life by its profile's refresh_extends_days.
    """
    now = cart.clock.now()
    with cart.transaction() as conn:
        for note_id in note_ids:
            note = _require_note(cart, note_id)
            extends = cart.profile(note["profile"])["refresh_extends_days"]
            new_switch = max(note["switch_at"], add_days(now, extends))
            conn.execute(
                "UPDATE notes SET refreshed_at = ?, switch_at = ?, pull_count = pull_count + 1,"
                " last_pulled_at = ? WHERE note_id = ?", (now, new_switch, now, note_id))


def reinforce(cart, note_ids: Sequence[str]) -> int:
    """Notes used together strengthen the links between them (Hebbian)."""
    ids = list(dict.fromkeys(note_ids))
    if len(ids) < 2:
        return 0
    delta = cart.setting("reinforce_delta")
    now = cart.clock.now()
    marks = ",".join("?" * len(ids))
    rows = cart.conn.execute(
        f"SELECT link_id, a, b, switch_at FROM links WHERE a IN ({marks}) AND b IN ({marks})",
        ids + ids).fetchall()
    with cart.transaction() as conn:
        for row in rows:
            extends = min(cart.profile(_require_note(cart, n)["profile"])["refresh_extends_days"]
                          for n in (row["a"], row["b"]))
            conn.execute(
                "UPDATE links SET strength = MIN(1.0, strength + ?), refreshed_at = ?,"
                " switch_at = MAX(switch_at, ?), reinforce_count = reinforce_count + 1"
                " WHERE link_id = ?", (delta, now, add_days(now, extends), row["link_id"]))
    return len(rows)


def get_note(cart, note_id: str) -> dict:
    """One note with its current strength, depth and tags."""
    _require_note(cart, note_id)
    cart.sync_clock()
    state = dict(cart.conn.execute("SELECT * FROM temp.note_state WHERE note_id = ?",
                                   (note_id,)).fetchone())
    state["depth"] = round(1.0 - state["strength"], 6)
    state["tags"] = [r["tag"] for r in cart.conn.execute(
        "SELECT tag FROM tags WHERE note_id = ? ORDER BY tag", (note_id,))]
    return state


def count_notes(cart) -> int:
    return int(cart.conn.execute("SELECT count(*) FROM notes").fetchone()[0])


def days_until_switch(cart, note_id: str) -> float:
    return days_between(cart.clock.now(), _require_note(cart, note_id)["switch_at"])
