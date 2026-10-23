"""ACK parsing + the seal gate.

Port of wrapper/src/core/ack.rs (S00051). Trust boundary: the model NEVER
self-certifies completeness. 'STATUS: COMPLETE' is informational; the verdict
is the wrapper's set-diff of recited seals against the briefcase's seals.
Seals are random 6-hex nonces, so a confabulated seal cannot clear a missing one.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from typing import TYPE_CHECKING

if TYPE_CHECKING:                       # an annotation only: Integrity does not import Agent
    from aether.agent.briefcase import Briefcase

_SEAL_TOK = re.compile(r"\b[0-9a-f]{6}\b")
_SEALS_LINE = re.compile(r"^\s*SEALS:\s*(.+)$", re.M)
_AGENT_LINE = re.compile(r"^\s*AGENT:\s*(.+)$", re.M)
_STATUS_LINE = re.compile(r"^\s*STATUS:\s*(.+)$", re.M)


@dataclass
class BriefcaseAck:
    agent: str | None
    status: str | None
    seals: list[str]


@dataclass
class SealGate:
    passed: bool
    missing: list[str] = field(default_factory=list)    # expected labels not recited
    unmatched: list[str] = field(default_factory=list)  # recited seals not in record


def parse_ack(text: str) -> BriefcaseAck:
    """Defensive parse: prefer the SEALS: line, fall back to scanning the whole
    text for 6-hex tokens (small models format loosely). De-duplicates."""
    seals: list[str] = []
    seen: set[str] = set()
    m = _SEALS_LINE.search(text)
    if m:
        for tok in _SEAL_TOK.findall(m.group(1)):
            if tok not in seen:
                seen.add(tok)
                seals.append(tok)
    if not seals:
        for tok in _SEAL_TOK.findall(text):
            if tok not in seen:
                seen.add(tok)
                seals.append(tok)

    am = _AGENT_LINE.search(text)
    sm = _STATUS_LINE.search(text)
    return BriefcaseAck(
        agent=am.group(1).strip() if am else None,
        status=sm.group(1).strip() if sm else None,
        seals=seals,
    )


def seal_gate(bc: Briefcase, ack: BriefcaseAck) -> SealGate:
    """Diff recited seals against the parsed briefcase. Pass iff every expected
    seal was recited."""
    expected = {s.seal: s.label for s in bc.sections}
    received = set(ack.seals)
    missing = sorted(label for seal, label in expected.items() if seal not in received)
    unmatched = sorted(s for s in received if s not in expected)
    return SealGate(passed=not missing, missing=missing, unmatched=unmatched)


# Reply classifiers (moved from the Runtime tool loop, 2026-10-07): empty_reply-class detection.
def is_blank_reply(text: str) -> bool:
    """Blank or whitespace-only counts as empty_reply-class (Fable ruling Q1.5)."""
    substantive = (text or "").replace("[…continued]", "")
    return not substantive.strip()


def is_bare_ack_echo(text: str) -> bool:
    """A reply consisting only of an ACK recital is empty_reply-class too.

    BRIEFCASE-1 ACKs include AGENT, STATUS, and SEALS; MICROCASE-1 ACKs include AGENT and SEAL.
    Require both identity and seal lines so a legitimate answer such as ``STATUS: COMPLETE`` is
    not mistaken for an ACK echo.
    """
    stripped = (text or "").strip()
    if not stripped:
        return False
    lines = [ln.strip() for ln in stripped.splitlines() if ln.strip()]
    if not lines:
        return False
    upper = [ln.upper() for ln in lines]
    allowed = ("AGENT:", "STATUS:", "SEAL:", "SEALS:")
    return (
        all(ln.startswith(allowed) for ln in upper)
        and any(ln.startswith("AGENT:") for ln in upper)
        and any(ln.startswith(("SEAL:", "SEALS:")) for ln in upper)
    )
