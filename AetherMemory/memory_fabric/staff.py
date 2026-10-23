"""Staff box: agents as staff: reviews, verified outcomes, levels, card, résumé.

Inputs:  an open Cartridge; reviews written by others; outcomes verified by
         others; the avatar the agent chose for itself.
Outputs: a trading-card dictionary and a live Markdown résumé, both
         generated from the cartridge (decisions 10, 11, 23).

Rules enforced here:
  * Reviews are written by someone other than the agent. This box has no
    way to edit or delete a review: the agent can read and keep them only.
  * Levels come only from outcomes verified by someone else, are computed
    by the engine with one shared scale (so levels compare across agents),
    and never go down.
  * The avatar is chosen by the agent itself and attested as an original
    design (no known characters or logos).
  * The résumé never includes proprietary memories.
"""
from __future__ import annotations

import re
from typing import Optional

from .errors import MemoryFabricError

# One scale for every agent (levels must mean the same thing everywhere).
# Points needed for level 1..10: triangular numbers.
LEVEL_POINTS = (1, 3, 6, 10, 15, 21, 28, 36, 45, 55)
RESULT_POINTS = {"success": 1.0, "partial": 0.5, "failure": 0.0}
MAX_AVATAR_BYTES = 64 * 1024


class StaffError(MemoryFabricError, ValueError):
    """Any refusal from the staff box."""


def _same(name: str, agent_id: str) -> bool:
    """Identity comparison that spacing and capitals can't fool ("Grace" is grace)."""
    return name.strip().casefold() == agent_id.strip().casefold()


def level_for(points: float) -> int:
    return sum(1 for needed in LEVEL_POINTS if points >= needed)


def add_review(cart, reviewer: str, reviewer_role: str, text: str,
               rating: Optional[float] = None, project: Optional[str] = None) -> int:
    """Store a review written by someone else. Reviews cannot be edited or deleted."""
    if not reviewer or not reviewer.strip() or _same(reviewer, cart.agent_id):
        raise StaffError("a review must be written by someone other than the agent")
    if not text or not text.strip():
        raise StaffError("review text is required")
    if rating is not None and not 1.0 <= rating <= 5.0:
        raise StaffError("rating must be within 1..5")
    with cart.transaction() as conn:
        cursor = conn.execute(
            "INSERT INTO reviews(reviewer, reviewer_role, project, rating, text, created_at)"
            " VALUES (?,?,?,?,?,?)",
            (reviewer, reviewer_role, project, rating, text.strip(), cart.clock.now()))
        return int(cursor.lastrowid)


def reviews(cart) -> list:
    return [dict(r) for r in cart.conn.execute(
        "SELECT * FROM reviews ORDER BY created_at DESC, review_id DESC")]


def record_outcome(cart, specialty: str, task_ref: str, result: str, difficulty: float,
                   verified_by: str) -> int:
    """Record a verified task outcome and update the specialty's level.

    Returns the specialty's level afterwards (which can only stay or rise).
    """
    specialty = specialty.strip().lower()
    if not specialty:
        raise StaffError("specialty is required")
    if result not in RESULT_POINTS:
        raise StaffError(f"result must be one of {tuple(RESULT_POINTS)}")
    if not 1.0 <= difficulty <= 5.0:
        raise StaffError("difficulty must be within 1..5")
    if not verified_by or not verified_by.strip() or _same(verified_by, cart.agent_id):
        raise StaffError("outcomes must be verified by someone other than the agent")
    now = cart.clock.now()
    with cart.transaction() as conn:
        conn.execute(
            "INSERT INTO outcomes(specialty, task_ref, result, difficulty, verified_by, at)"
            " VALUES (?,?,?,?,?,?)", (specialty, task_ref, result, difficulty, verified_by, now))
        points = _points(conn, specialty)
        computed = level_for(points)
        row = conn.execute("SELECT level FROM levels WHERE specialty = ?", (specialty,)).fetchone()
        current = row["level"] if row else 0
        if computed > current:   # never down
            conn.execute("INSERT OR REPLACE INTO levels(specialty, level, achieved_at) VALUES (?,?,?)",
                         (specialty, computed, now))
            current = computed
    return current


def _points(conn, specialty: str) -> float:
    total = 0.0
    for row in conn.execute("SELECT result, difficulty FROM outcomes WHERE specialty = ?",
                            (specialty,)):
        total += RESULT_POINTS[row["result"]] * row["difficulty"]
    return total


# Anything that could run code or reach outside the image. Viewers additionally
# display avatars only as <img> data URIs, where SVG can do neither.
_UNSAFE_SVG = re.compile(
    r"<\s*(?:script|foreignObject|iframe|object|embed|link|meta)\b"
    r"|\bon\w+\s*="
    r"|(?:href|src)\s*=\s*[\"']?\s*(?:https?:|javascript:|data:|//)"
    r"|url\(\s*[\"']?\s*(?:https?:|javascript:|data:|//)"
    r"|@import",
    re.I)


def check_avatar(svg: str, description: str, original_design_attested: bool) -> str:
    """Validate an avatar without saving it. Returns the cleaned SVG."""
    if not original_design_attested:
        raise StaffError("the avatar must be an original design (no known characters or logos)")
    if not isinstance(svg, str):
        raise StaffError("avatar must be a single SVG image")
    text = svg.strip()
    if not text.lower().startswith("<svg") or "</svg>" not in text.lower():
        raise StaffError("avatar must be a single SVG image")
    if len(text.encode("utf-8")) > MAX_AVATAR_BYTES:
        raise StaffError("avatar is too large (64 KB maximum)")
    if _UNSAFE_SVG.search(text):
        raise StaffError("avatar must be self-contained: no scripts, handlers, embedded "
                         "documents or external links")
    if not isinstance(description, str) or not description.strip():
        raise StaffError("describe the avatar in words")
    return text


def set_avatar(cart, svg: str, description: str, chosen_by: str,
               original_design_attested: bool) -> None:
    """The agent chooses its own avatar: an original SVG design."""
    if not _same(chosen_by or "", cart.agent_id):
        raise StaffError("agents choose their own avatar")
    text = check_avatar(svg, description, original_design_attested)
    for key, value in (("avatar_svg", text), ("avatar_description", description.strip()),
                       ("avatar_chosen_at", cart.clock.now())):
        cart.set_card_value(key, value)


def check_scan_line(scan_line: str) -> str:
    if not isinstance(scan_line, str) or len(scan_line) > 200 or "\n" in scan_line.strip():
        raise StaffError("keep the scan line to one line (200 characters)")
    return scan_line.strip()


def set_scan_line(cart, scan_line: str) -> None:
    """One line: role, what the agent does, what it doesn't."""
    cart.set_card_value("scan_line", check_scan_line(scan_line))


def check_name(name: str) -> str:
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 60:
        raise StaffError("a name is 1 to 60 characters")
    return name.strip()


def card(cart) -> dict:
    """The trading card, generated from the cartridge."""
    cart.sync_clock()
    conn = cart.conn
    specialties = []
    for row in conn.execute("SELECT * FROM levels ORDER BY level DESC, specialty"):
        points = _points(conn, row["specialty"])
        last = conn.execute("SELECT MAX(at) FROM outcomes WHERE specialty = ?",
                            (row["specialty"],)).fetchone()[0]
        nxt = next((p for p in LEVEL_POINTS if p > points), None)
        specialties.append(dict(specialty=row["specialty"], level=row["level"],
                                points=points, last_active=last, next_level_at=nxt))
    totals = conn.execute(
        "SELECT count(*), SUM(result = 'success') FROM outcomes").fetchone()
    tasks, successes = totals[0], totals[1] or 0
    review_rows = reviews(cart)
    rated = [r["rating"] for r in review_rows if r["rating"] is not None]
    threshold = cart.setting("surface_threshold")
    active_alerts = conn.execute(
        "SELECT count(*) FROM temp.note_state WHERE category = 'Alert' AND strength >= ?",
        (threshold,)).fetchone()[0]
    return dict(
        agent_id=cart.agent_id,
        name=cart.card_value("name"),
        scan_line=cart.card_value("scan_line") or "",
        avatar_svg=cart.card_value("avatar_svg"),
        avatar_description=cart.card_value("avatar_description"),
        specialties=specialties,
        stats=dict(verified_tasks=tasks, successes=successes,
                   success_rate=round(successes / tasks, 3) if tasks else None,
                   reviews=len(review_rows),
                   average_rating=round(sum(rated) / len(rated), 2) if rated else None),
        recent_reviews=[dict(reviewer=r["reviewer"], role=r["reviewer_role"],
                             rating=r["rating"], text=r["text"][:200])
                        for r in review_rows[:3]],
        memory=dict(notes=conn.execute("SELECT count(*) FROM notes").fetchone()[0],
                    links=conn.execute("SELECT count(*) FROM links").fetchone()[0],
                    active_alerts=active_alerts),
    )


def resume_markdown(cart) -> str:
    """A live résumé for overseers, generated from the cartridge. No proprietary content."""
    data = card(cart)
    threshold = cart.setting("surface_threshold")
    strengths = cart.conn.execute(
        "SELECT title FROM temp.note_state WHERE proprietary = 0 AND strength >= ?"
        " AND (stratum IN ('Insight', 'Abstract') OR profile IN ('standing_lesson', 'core_knowledge'))"
        " ORDER BY strength DESC LIMIT 5", (threshold,)).fetchall()
    watch = cart.conn.execute(
        "SELECT title FROM temp.note_state WHERE proprietary = 0 AND category = 'Alert'"
        " AND strength >= ? ORDER BY strength DESC LIMIT 5", (threshold,)).fetchall()
    lines = [f"# {data['name']} ({data['agent_id']})", ""]
    if data["scan_line"]:
        lines += [f"> {data['scan_line']}", ""]
    lines += ["## Levels", ""]
    if data["specialties"]:
        lines += ["| Specialty | Level | Last active |", "|---|---|---|"]
        lines += [f"| {s['specialty']} | {s['level']} | {s['last_active'] or '-'} |"
                  for s in data["specialties"]]
    else:
        lines.append("No verified outcomes yet.")
    lines += ["", "## Strengths (from memory)", ""]
    lines += [f"- {r['title']}" for r in strengths] or ["- none recorded yet"]
    lines += ["", "## Watch-outs (active alerts)", ""]
    lines += [f"- {r['title']}" for r in watch] or ["- none active"]
    stats = data["stats"]
    lines += ["", "## Record", "",
              f"- Verified tasks: {stats['verified_tasks']}"
              + (f" (success rate {stats['success_rate']:.0%})" if stats["success_rate"] is not None else ""),
              f"- Reviews: {stats['reviews']}"
              + (f" (average {stats['average_rating']}/5)" if stats["average_rating"] else "")]
    for review in data["recent_reviews"]:
        lines.append(f"  - {review['role']} {review['reviewer']}: “{review['text']}”")
    lines += ["", f"_Generated from the cartridge on {cart.clock.now()}._"]
    return "\n".join(lines)
