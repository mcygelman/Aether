"""Reformation box: when the working board fills, the agent reforms it.

Inputs:  the board (Prime Context Stack items, working items, recent turns),
         the agent's decision (what to keep, and how to file the rest), and
         the token budget for the rebuilt board.
Outputs: ReformedBoard: Prime Context Stack first, then the kept items, then
         a short "set down to memory" breadcrumb, then the recent turns, plus
         the ids of the notes written for everything set down.

This replaces a hard stop (today's `context_stop`) with agent-led
reformation (decision 25). Nothing is squeezed and nothing is lost: what the
agent sets down becomes memory in its fabric, and can be pulled back by tag.
The agent decides; this box only checks the decision is complete, writes the
memories, and rebuilds the board in a fixed, predictable order.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional, Sequence

from . import tokens as _tokens
from .errors import MemoryFabricError
from . import fabric, kinds, schema
from .history import atomic


class ReformationError(MemoryFabricError, ValueError):
    """The decision cannot be applied (the message says why)."""


def estimate_tokens(text: str) -> int:
    """Token estimate from the tokens box (host-replaceable)."""
    return _tokens.estimate_tokens(text)


@dataclass(frozen=True)
class BoardItem:
    item_id: str
    text: str
    role: str = "working"        # "pcs" (always kept, always first), "working", "recent"

    def tokens(self) -> int:
        return estimate_tokens(self.text)


@dataclass(frozen=True)
class SetDown:
    """How the agent wants one board item filed into memory."""
    item_id: str
    title: str
    kind: str = "offloaded context"
    tags: tuple = ()
    switch_in_days: Optional[float] = None
    category: str = "Information"


@dataclass(frozen=True)
class Decision:
    keep: tuple                  # item ids of working items to keep on the board
    set_down: tuple              # SetDown entries for the rest


@dataclass(frozen=True)
class Pressure:
    used_tokens: int
    usable_tokens: int
    ratio: float
    needs_reformation: bool


@dataclass
class ReformedBoard:
    items: List[BoardItem] = field(default_factory=list)
    tokens: int = 0
    written_note_ids: List[str] = field(default_factory=list)
    auto_filed: List[str] = field(default_factory=list)   # items the agent didn't mention

    def text(self) -> str:
        return "\n\n".join(item.text for item in self.items)


def pressure(used_tokens: int, window_tokens: int, reserved_tokens: int,
             threshold: float) -> Pressure:
    """Host-side check: is the board nearly full for THIS deployment's window?"""
    usable = max(1, window_tokens - reserved_tokens)
    ratio = used_tokens / usable
    return Pressure(used_tokens, usable, round(ratio, 4), ratio >= threshold)


@atomic
def reform(cart, board: Sequence[BoardItem], decision: Decision, budget_tokens: int, *,
           written_by: str, auto_file: bool = True) -> ReformedBoard:
    """Apply the agent's reformation decision.

    * Prime Context Stack items ("pcs") are always kept and always come first.
    * Recent turns ("recent") are always kept and always come last.
    * Every working item is either kept or set down. Items the agent did not
      mention are filed as offloaded context when auto_file=True (nothing is
      silently dropped), or refused when auto_file=False.
    * If the rebuilt board would exceed the budget, nothing is written and
      ReformationError says how much must be set down.
    """
    by_id = {item.item_id: item for item in board}
    if len(by_id) != len(board):
        raise ReformationError("board item ids must be unique")
    working = [i for i in board if i.role == "working"]
    working_ids = {i.item_id for i in working}
    for item_id in decision.keep:
        if item_id not in working_ids:
            raise ReformationError(f"cannot keep '{item_id}': not a working item on the board")
    set_down = {s.item_id: s for s in decision.set_down}
    for item_id in set_down:
        if item_id not in working_ids:
            raise ReformationError(f"cannot set down '{item_id}': not a working item on the board")
        if item_id in decision.keep:
            raise ReformationError(f"'{item_id}' is both kept and set down")
    unmentioned = [i.item_id for i in working
                   if i.item_id not in decision.keep and i.item_id not in set_down]
    if unmentioned and not auto_file:
        raise ReformationError(f"decide on every working item; undecided: {unmentioned}")

    pcs = [i for i in board if i.role == "pcs"]
    kept = [by_id[i] for i in decision.keep]
    recent = [i for i in board if i.role == "recent"]
    filed = [set_down[i.item_id] for i in working if i.item_id in set_down]
    filed += [SetDown(item_id=i, title=f"Set down from board: {i}") for i in unmentioned]
    breadcrumb = None
    if filed:
        lines = ["SET DOWN TO MEMORY (pull back by tag when needed):"]
        lines += [f"- {s.title} [tags: {', '.join(s.tags) or s.title.lower()}]" for s in filed]
        breadcrumb = BoardItem("reformation-breadcrumb", "\n".join(lines), "working")
    rebuilt = pcs + kept + ([breadcrumb] if breadcrumb else []) + recent
    total = sum(i.tokens() for i in rebuilt)
    if total > budget_tokens:
        raise ReformationError(
            f"reformed board needs {total} tokens but the budget is {budget_tokens}; "
            f"set down at least {total - budget_tokens} more tokens of working items")

    # Validate every entry BEFORE the first write, so a bad entry can't leave the
    # reformation half-written (independent review, 2026-10-06).
    available = [p["profile"] for p in cart.profiles()]
    for entry in filed:
        if not isinstance(entry.title, str) or not entry.title.strip():
            raise ReformationError(f"set-down entry '{entry.item_id}' needs a title")
        try:
            kinds.resolve(entry.kind, available)
            for tag in entry.tags:
                fabric.normalize_tag(tag)
        except MemoryFabricError as exc:
            raise ReformationError(f"set-down entry '{entry.item_id}': {exc}") from exc
        days = entry.switch_in_days
        if days is not None and (isinstance(days, bool) or not isinstance(days, (int, float))
                                 or not math.isfinite(days) or days < 0):
            raise ReformationError(f"set-down entry '{entry.item_id}': days must be a number >= 0")
        if entry.category not in schema.CATEGORIES:
            raise ReformationError(f"set-down entry '{entry.item_id}': category must be one of "
                                   f"{schema.CATEGORIES}")

    result = ReformedBoard(items=rebuilt, tokens=total, auto_filed=unmentioned)
    for entry in filed:
        result.written_note_ids.append(fabric.remember(
            cart, entry.title, by_id[entry.item_id].text, entry.kind,
            tags=entry.tags, written_by=written_by, switch_in_days=entry.switch_in_days,
            category=entry.category, stratum="Experience",
            source_ref=f"reformation:{entry.item_id}"))
    return result
