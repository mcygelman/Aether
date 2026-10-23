"""Host box: the one call a runtime (the Bridge, Mission Control) makes.

Inputs:  an open MemoryEngine, the event that just happened, the tags for
         this moment, and a token budget for the memory block.
Outputs: HostBlock: whether to act, and the exact text to place on the
         agent's working board (recall headers, or a reform request).

The runtime decides WHEN (it observes events); this box decides WHAT goes
on the board. Tags normally come from the agent (planning) or from the task
and error text (host-detected events). The agent then loads what it chooses
via MemoryEngine.load(). This box never loads on the agent's behalf.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Optional

from .triggers import Event, decide

_STOP = {"the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with", "is", "was",
         "be", "it", "this", "that", "at", "by", "from", "as", "not", "no", "error", "failed"}


@dataclass(frozen=True)
class HostBlock:
    act: bool
    action: str
    text: str
    tags: tuple
    note_ids: tuple


def tags_from_text(text: str, limit: int = 6) -> tuple:
    """A simple fallback when the host must derive tags itself (e.g. an error message)."""
    words = [w for w in re.findall(r"[a-z0-9][a-z0-9_\-]{2,}", text.lower()) if w not in _STOP]
    return tuple(dict.fromkeys(words))[:limit]


def memory_block(engine, event: Event, tags: Iterable[str] = (), budget_tokens: int = 600,
                 *, error_text: Optional[str] = None) -> HostBlock:
    action = decide(event)
    tags = tuple(tags)
    if action.action == "none":
        return HostBlock(False, "none", "", tags, ())
    if action.action == "reform":
        text = ("WORKING BOARD NEARLY FULL. Reform it: keep what this task needs on the board; "
                "set the rest down into memory with a kind, tags and a date. Nothing set down is "
                "lost; recall it by tag when needed. The Prime Context Stack stays on top.")
        return HostBlock(True, "reform", text, tags, ())
    if not tags and error_text:
        tags = tags_from_text(error_text)
    if not tags:
        text = (f"MEMORY CHECK ({event.value}): choose the tags this work touches "
                f"({action.tag_hint}) and recall them before continuing.")
        return HostBlock(True, "memory_check", text, (), ())
    result = engine.recall(tags, budget_tokens=budget_tokens)
    header = f"MEMORY CHECK ({event.value}, {action.reason})"
    return HostBlock(True, "memory_check", header + "\n" + result.render(), result.tags,
                     tuple(result.ids()))
