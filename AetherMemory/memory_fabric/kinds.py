"""Kinds box: agents choose memory kinds in words, not numbers.

Inputs:  a kind as the agent says it ("safety warning", "lesson", "todo"...)
         and the cartridge's profile names.
Outputs: the profile name to use.

Why: models are good at categories and inconsistent with numbers. One model's
0.9 is another's 0.6, and that inconsistency would travel with lent agents.
"Safety warning, current until Friday" means the same to every model. The
engine turns the words into numbers through the profile table.
"""
from __future__ import annotations

import re

from .errors import MemoryFabricError

# Phrases agents naturally use, mapped to default profile names.
ALIASES = {
    "project_fact": ("project fact", "fact", "project", "project detail", "assignment",
                     "location", "directions", "for this project", "context fact"),
    "safety_warning": ("safety warning", "warning", "alert", "caution", "danger", "hazard",
                       "trap", "gotcha", "scar"),
    "standing_lesson": ("standing lesson", "lesson", "lesson learned", "know-how", "how-to",
                        "technique", "practice", "habit", "pattern", "insight"),
    "core_knowledge": ("core knowledge", "core", "principle", "foundation", "fundamental",
                       "identity", "axiom", "law"),
    "task_obligation": ("task obligation", "task", "todo", "to-do", "to do", "promise",
                        "obligation", "commitment", "deadline", "follow-up", "follow up"),
    "scratch": ("scratch", "temporary", "temp", "guess", "hypothesis", "note to self",
                "draft", "ephemeral", "scratchpad"),
    "offloaded_context": ("offloaded context", "offloaded", "set down", "set aside",
                          "board", "parked", "context"),
    "decision": ("decision", "ruling", "operator decision", "decided"),
}


class UnknownKind(MemoryFabricError, ValueError):
    """The words did not match any profile. The message lists the choices."""


def _normalize(text: str) -> str:
    return re.sub(r"[\s_\-]+", " ", text.strip().lower())


def resolve(kind: str, available_profiles) -> str:
    """Map an agent's words to a profile name available in its cartridge."""
    if not kind or not kind.strip():
        raise UnknownKind("a memory kind is required")
    wanted = _normalize(kind)
    available = list(available_profiles)
    # 1) exact profile name (agents may also name tuned or custom profiles)
    for name in available:
        if _normalize(name) == wanted:
            return name
    # 2) exact alias
    for name, phrases in ALIASES.items():
        if name in available and wanted in (_normalize(p) for p in phrases):
            return name
    # 3) alias contained in the words ("a safety warning about doors")
    best = None
    for name, phrases in ALIASES.items():
        if name not in available:
            continue
        for phrase in phrases:
            p = _normalize(phrase)
            if re.search(rf"\b{re.escape(p)}\b", wanted) and (best is None or len(p) > best[1]):
                best = (name, len(p))
    if best:
        return best[0]
    raise UnknownKind(f"unknown memory kind '{kind}'. Choose one of: {', '.join(available)}")
