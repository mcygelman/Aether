"""Side-log box: a mind's journal of actions, beside its memories in the same cartridge.

Inputs:  an open Cartridge; rows of (kind, pointer, tags, line, by), each optionally with `at` and `commission`.
Outputs: how many rows were appended; rows newest first (recent, search); repeats, in the host's words.

One row per action: when, which commission, what kind of action, a pointer (a file's hash, a note's id, a memory's
id), a few tags, one line. Never the content. Append-only: the box offers no edit and no delete, as for reviews.

Sealed, not captured (schema 3). The table is in schema.DATA_TABLES, so the seal notices a row added or changed
outside the engine. It is not in history.ACTIVE_TABLES. History exists to reconstruct notes, links and settings
at an earlier revision. A log row is never revised, so capturing each append would write every row twice and
spend a revision on each action (a journal of a journal). The log is its own record; the seal keeps it honest.

The atelier wrote this table itself before schema 3 (Bridge aether/atelier/mind_memory.py, 10-19). The migration to
schema 3 adopts that table as it is: same name, same columns, every row kept.
"""
from __future__ import annotations

import json
from typing import Iterable, List, Optional

TABLE = "sidelog"
COLUMNS = ("seq", "at", "commission", "kind", "pointer", "tags", "line", "by")
DDL = ("CREATE TABLE IF NOT EXISTS sidelog (seq INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, commission TEXT, "
       "kind TEXT NOT NULL, pointer TEXT, tags TEXT NOT NULL, line TEXT NOT NULL, by TEXT NOT NULL)")
SEARCH_WINDOW = 400   # search reads the newest rows only; the log is a pointer list, not an archive to mine


def append(cart, rows: Iterable[dict], commission: Optional[str] = None) -> int:
    """Append rows; each needs a kind and a line (others are skipped, never raised on). Returns how many."""
    rows = [r for r in rows if r and r.get("kind") and r.get("line")]
    if not rows:
        return 0
    now = cart.clock.now()
    with cart.transaction(operation="sidelog", description="side log") as conn:
        conn.executemany(
            "INSERT INTO sidelog(at, commission, kind, pointer, tags, line, by) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(r.get("at") or now, r.get("commission", commission), r["kind"][:24], (r.get("pointer") or "")[:200],
              json.dumps([t for t in (r.get("tags") or []) if t][:8]), r["line"][:200], r.get("by") or "host")
             for r in rows])
    return len(rows)


def _rows(conn, where: str, args: tuple, limit: int) -> List[dict]:
    found = conn.execute(f"SELECT {', '.join(COLUMNS)} FROM sidelog {where} ORDER BY seq DESC LIMIT ?",
                         args + (limit,)).fetchall()
    return [dict(seq=r[0], at=r[1], commission=r[2], kind=r[3], pointer=r[4], tags=json.loads(r[5] or "[]"),
                 line=r[6], by=r[7]) for r in found]


def recent(cart, commission: Optional[str] = None, limit: int = 8) -> List[dict]:
    """The last actions, newest first; one commission's when given."""
    if commission:
        return _rows(cart.conn, "WHERE commission = ?", (commission,), limit)
    return _rows(cart.conn, "", (), limit)


def search(cart, words: Iterable[str], limit: int = 10) -> List[dict]:
    """Rows whose line, tags or pointer carry any of the words; ranked by how many, then newest first."""
    wanted = [w.lower() for w in words if w]
    rows = _rows(cart.conn, "", (), SEARCH_WINDOW)

    def score(r):
        hay = (r["line"] + " " + " ".join(r["tags"]) + " " + (r["pointer"] or "")).lower()
        return sum(1 for w in wanted if w in hay)
    hits = [r for r in rows if score(r) > 0] if wanted else rows
    hits.sort(key=lambda r: (-score(r), -r["seq"]))
    return hits[:limit]


def repeats(rows: Iterable[dict]) -> List[str]:
    """What the log says was done more than once: the same tests run three times or more, the same memory read
    twice or more. One line each, in the host's words; the mind decides whether it was waste."""
    counts: dict = {}
    for r in rows:
        key = (r["kind"], r.get("pointer") or r["line"])
        counts[key] = counts.get(key, 0) + 1
    out = []
    for (kind, pointer), n in sorted(counts.items(), key=lambda kv: -kv[1]):
        if kind == "ran" and n >= 3:
            out.append(f"you ran the tests at {pointer} {n} times; run them once after the last edit")
        elif kind == "read-memory" and n >= 2:
            out.append(f"you read memory {pointer} {n} times in one commission; once is enough, it stays loaded in your note")
    return out[:4]
