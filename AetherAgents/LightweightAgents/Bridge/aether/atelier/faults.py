"""Deterministic fault injection for recovery tests (Fable, 2026-10-22, v0.1 lane).

A recovery test needs the process to die at an exact point, the way a power cut or a killed task dies: no `finally`,
no flush, no cleanup. `point(name)` does that when the environment names that point, and does nothing otherwise.
It is inert in normal use: the variable is never set by the product, and the check is one dictionary lookup.

    AETHER_ATELIER_FAULT=after_think  py tools/atelier_place.py serve ...

Points, in the order a turn reaches them:
  after_think           the model answered and tools ran; nothing is marked read or sent yet
  after_send            the note is in the outbox; the inbox and the atelier are not marked read yet
  after_marks           the inbox and the atelier are marked read; the note is in the outbox, not yet collected
  after_collect_post    the postmaster appended the note to the daybook; the outbox copy is not yet moved to sent
  after_closure_filed   a mind's closing words were filed into its home; the atelier has not recorded the filing
"""
from __future__ import annotations

import os

VARIABLE = 'AETHER_ATELIER_FAULT'
EXIT_CODE = 86
POINTS = ('after_think', 'after_send', 'after_marks', 'after_collect_post', 'after_closure_filed')


def point(name: str) -> None:
    """Die here, without cleanup, when the environment names this point."""
    if os.environ.get(VARIABLE) == name:
        os._exit(EXIT_CODE)
