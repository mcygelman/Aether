"""Tokens box: one place that turns text into an estimated token count.

Inputs:  text.
Outputs: an integer token estimate.

Default: characters / 3.5, rounded up. This is the same heuristic the Aether
Bridge uses (aether_bridge/budget.py: glyph-heavy Aether text is denser than
English), rounded up so that the memory block never overruns its budget.

A host that has a real tokenizer installs it with use_estimator(fn); every
budget in the engine (recall headers, reformed boards) then follows it.
"""
from __future__ import annotations

import math
from typing import Callable, Optional

CHARS_PER_TOKEN = 3.5


def _default(text: str) -> int:
    return math.ceil(len(text) / CHARS_PER_TOKEN)


_estimator: Callable[[str], int] = _default


def estimate_tokens(text: str) -> int:
    return int(_estimator(text or ""))


def use_estimator(fn: Optional[Callable[[str], int]]) -> None:
    """Install a host tokenizer (fn(text) -> int), or None to restore the default."""
    global _estimator
    _estimator = fn if fn is not None else _default
