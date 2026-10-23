"""Hydration box: what an agent sees of its memory when a session starts.

Inputs:  an open MemoryEngine (read-only is enough), a token budget, and
         optionally the tags of the task the session opens on.
Outputs: (sections, report): the same shape as the Bridge's current
         memory_loader.load_index(), so the Studio can switch an agent from
         the markdown index to its cartridge by swapping one call:
             sections: list of text blocks to place on the board;
             report:   a dict saying what was selected, what was left out,
                       and the budget used (policy AETHER-MEMORY-FABRIC-1).
         orientation(report) renders the report as a short text block, like
         memory_loader.orientation().

What goes on the board at session start (nothing is refreshed: looking is
free, loading is the agent's choice):
  1. The Prime Context Stack directive for memory checks (triggers box).
  2. Orientation: the strongest live alerts, then the strongest principles
     and lessons, as short headers (no tags needed: the session hasn't
     chosen its work yet).
  3. If task tags are known: a recall for them.
  4. One line on how to use the memory tools.
Everything is budgeted with the tokens box and never exceeds the budget.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Optional, Tuple

from .recall import Header, _one_line
from .tokens import estimate_tokens
from .triggers import pcs_directive

POLICY = "AETHER-MEMORY-FABRIC-1"
DEFAULT_BUDGET = 2000            # only when the host does not know the working context
MAX_WINDOW_SHARE = 0.25          # hard: memory never takes more than this of the window
ORIENTATION_SHARE = 0.5          # of the budget left after the directive and tool line
TOOLS_LINE = ("Your memory is yours: memory_recall(tags) shows headers; memory_load(ids) "
              "pulls chosen ones up; memory_remember writes; memory_bury forgets (nothing "
              "is ever deleted); memory_settings shows and tunes how your memory behaves. "
              "memory_workspace(action='dashboard') opens Welcome; action='schema' discovers records, "
              "Threads and layout editing. memory_query runs your own read-only SQL.")


@dataclass(frozen=True)
class HydrationLimits:
    """Box: working context -> session-start memory budget (decision 49).

    Inputs:  the agent's own settings (share, floor, ceiling) and the host's
             MEASURED working context in tokens (never the advertised window:
             "configured is not capability").
    Output:  budget = share of the window, raised to the floor, held to the
             ceiling, and never more than MAX_WINDOW_SHARE of the window (a hard
             limit that protects small models even from the floor).
    The agent dials share, floor and ceiling with memory_settings, inside the
    ranges stored in its cartridge (the harness's hard limits).
    """
    share: float = 0.02
    floor: int = 600
    ceiling: int = 40000

    @classmethod
    def from_settings(cls, settings: dict) -> "HydrationLimits":
        return cls(share=float(settings["hydration_share"]),
                   floor=int(settings["hydration_floor"]),
                   ceiling=int(settings["hydration_ceiling"]))

    def budget(self, working_context_tokens: int) -> int:
        if (isinstance(working_context_tokens, bool) or not isinstance(working_context_tokens, int)
                or working_context_tokens <= 0):
            raise ValueError("working_context_tokens must be a positive integer")
        wanted = max(self.floor, min(self.ceiling, int(working_context_tokens * self.share)))
        return min(wanted, int(working_context_tokens * MAX_WINDOW_SHARE))

    def describe(self, working_context_tokens: int) -> str:
        return (f"{self.share:.1%} of a {working_context_tokens:,}-token working context, "
                f"within {self.floor:,}..{self.ceiling:,}")


def _orientation_headers(engine, limit_tokens: int) -> Tuple[List[Header], int]:
    cart = engine.cart
    cart.sync_clock()
    s = cart.settings()
    rows = cart.conn.execute(
        "SELECT * FROM temp.note_state WHERE strength >= ? AND (category = 'Alert'"
        " OR profile IN ('core_knowledge', 'standing_lesson'))"
        " ORDER BY (category = 'Alert') DESC, strength DESC, note_id",
        (s["surface_threshold"],)).fetchall()
    headers, used = [], 0
    for row in rows:
        header = Header(
            note_id=row["note_id"], title=row["title"], category=row["category"],
            profile=row["profile"], strength=round(row["strength"], 4),
            depth=round(1 - row["strength"], 4), reach=round(row["strength"], 4),
            score=round(row["strength"], 4), one_line=_one_line(row["body"]),
            via="orientation", hops=0, through_fog=False, proprietary=bool(row["proprietary"]),
            recurring=row["category"] == "Alert" and row["pull_count"] >= s["recurring_pulls"],
            matched_tags=())
        cost = estimate_tokens(header.render()) + 2
        if used + cost > limit_tokens:
            break
        headers.append(header)
        used += cost
    return headers, len(rows) - len(headers)


def session_budget(engine, working_context_tokens: Optional[int] = None,
                   budget_tokens: Optional[int] = None) -> Tuple[int, dict]:
    """Which budget hydration uses, and why. Order: an explicit host budget
    (an operator override); else the agent's own settings applied to the measured
    working context; else DEFAULT_BUDGET when the window is unknown."""
    if budget_tokens is not None:
        if isinstance(budget_tokens, bool) or not isinstance(budget_tokens, int) or budget_tokens <= 0:
            raise ValueError("budget_tokens must be a positive integer")
        return budget_tokens, {"source": "host override", "rule": f"{budget_tokens:,} tokens set by the host"}
    if working_context_tokens is not None:
        limits = HydrationLimits.from_settings(engine.settings())
        budget = limits.budget(working_context_tokens)
        rule = limits.describe(working_context_tokens)
        if budget < limits.floor:
            rule += f"; held to {MAX_WINDOW_SHARE:.0%} of the window"
        return budget, {"source": "agent settings", "rule": rule,
                        "working_context_tokens": working_context_tokens,
                        "share": limits.share, "floor": limits.floor, "ceiling": limits.ceiling}
    return DEFAULT_BUDGET, {"source": "default",
                            "rule": f"{DEFAULT_BUDGET:,} tokens (working context unknown)"}


def load_session(engine, budget_tokens: Optional[int] = None,
                 task_tags: Optional[Iterable[str]] = None,
                 working_context_tokens: Optional[int] = None) -> Tuple[List[str], dict]:
    """The Bridge should pass working_context_tokens (its measured working context)
    and no budget, so the agent's own hydration settings decide (decision 49)."""
    budget_tokens, budget_basis = session_budget(engine, working_context_tokens, budget_tokens)
    agent = engine.agent_id
    sections: List[str] = []
    directive = f"\n=== MEMORY FABRIC: {agent} ===\n{pcs_directive()}\n"
    tools_line = f"\n{TOOLS_LINE}\n"
    used = estimate_tokens(directive) + estimate_tokens(tools_line)
    if used > budget_tokens:
        raise ValueError(f"budget {budget_tokens} cannot fit the memory directive ({used} tokens)")
    sections.append(directive)

    room = budget_tokens - used
    orient_room = int(room * ORIENTATION_SHARE) if task_tags else room
    headers, omitted = _orientation_headers(engine, orient_room - 20)
    selected = []
    if headers:
        block = "\n=== MEMORY ORIENTATION (strongest alerts and principles) ===\n" + "\n".join(
            f"  {i}. {h.render()}" for i, h in enumerate(headers, 1)) + "\n"
        sections.append(block)
        used += estimate_tokens(block)
        selected += [h.note_id for h in headers]

    recall_ids: List[str] = []
    more = 0
    tags = tuple(task_tags or ())
    if tags:
        remaining = budget_tokens - used - 20
        if remaining > 50:
            result = engine.recall(tags, budget_tokens=remaining - 20)
            block = "\n=== MEMORY RECALL FOR THIS TASK ===\n" + result.render() + "\n"
            if used + estimate_tokens(block) <= budget_tokens - estimate_tokens(tools_line):
                sections.append(block)
                used += estimate_tokens(block)
                recall_ids = result.ids()
                more = result.more_available

    sections.append(tools_line)
    report = {
        "policy": POLICY,
        "agent": agent,
        "budget_tokens": budget_tokens,
        "budget_basis": budget_basis,
        "used_estimated_tokens": used,
        "orientation_ids": selected,
        "orientation_omitted": omitted,
        "task_tags": list(tags),
        "recall_ids": recall_ids,
        "recall_more_available": more,
        "notes_total": engine.cart.conn.execute("SELECT count(*) FROM notes").fetchone()[0],
        "seal": {"status": engine.open_report.status, "warning": engine.open_report.warning,
                 "sealed": engine.open_report.sealed, "tampered": engine.open_report.tampered},
    }
    return sections, report


def orientation(report: dict) -> str:
    lines = [f"\n=== MEMORY HYDRATION REPORT ({report['policy']}) ===",
             f"AGENT: {report['agent']} · {report['notes_total']} memories in the cartridge",
             f"BUDGET: {report['used_estimated_tokens']}/{report['budget_tokens']} estimated tokens"
             + (f" ({report['budget_basis']['rule']}: your hydration settings,"
                " tunable with memory_settings)"
                if report.get("budget_basis", {}).get("source") == "agent settings" else ""),
             f"ORIENTATION: {len(report['orientation_ids'])} headers"
             + (f" (+{report['orientation_omitted']} more not shown)"
                if report['orientation_omitted'] else "")]
    if report["task_tags"]:
        lines.append(f"TASK RECALL: {', '.join(report['task_tags'])} -> "
                     f"{len(report['recall_ids'])} headers"
                     + (f" (+{report['recall_more_available']} more)"
                        if report['recall_more_available'] else ""))
    if report["seal"].get("warning") and report["seal"].get("status") in ("tampered", "unsealed"):
        lines.append(f"WARNING: this cartridge was {report['seal']['warning']}.")
    lines.append("Nothing was loaded or refreshed; pull up what you need with memory_load.")
    return "\n".join(lines)
