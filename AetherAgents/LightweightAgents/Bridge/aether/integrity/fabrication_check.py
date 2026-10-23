"""S00073.7 C-4 — mechanical fabrication-rule tripwires.

Per `Fable_Ruling_S00073.6_Integrity_and_Ceiling.md` rule 3 (the doctrine written after Finding
A -- Antigravity's fabricated S00073.6 V-5 "PASS"): two one-line, string/hash-only checks that
would have caught it before any human read a word. No model judgment is involved in either check;
that is the point -- these are mechanical gates, not review.

Rule 3a: a packet claiming research/analysis/review-type work, marked `status: complete`, with
`tool_calls == 0` -> automatic FAIL.

Rule 3b: a written deliverable whose content is hash-identical (canon sha256, same primitive as
`python -m aether_bridge hash`, DEC-021), or near-identical by diff ratio, to any pre-existing
file in the same root -> automatic FAIL.
"""
from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from pathlib import Path

from aether.integrity import canon
from aether.integrity.ack import is_bare_ack_echo as _is_bare_ack_echo, is_blank_reply as _is_blank_reply

# Task-text keywords that mark a packet as claiming research/analysis/review-type work.
# Deliberately broad and string-only -- matches the ruling's "no model judgment" requirement.
RESEARCH_ANALYSIS_REVIEW_KEYWORDS = (
    "research", "analyz", "analys", "review", "investigat", "assess", "audit", "critique",
    "evaluate", "verify", "inspect", "examine",
)

# Roles whose entire job (per PLANNING_CARD roster) is research/analysis/review -- belt-and-
# braces alongside the keyword scan, since a terse task ("Look into X") might carry no keyword
# but the receiving agent's role still implies claimed research/analysis/review work.
RESEARCH_ANALYSIS_REVIEW_ROLES = {"sagan", "nightingale", "linus"}

# Exact packet sentinel produced by the completed-work fixture path. This is deliberately singular
# and case-sensitive: S00078.1 ruled for a packet-level sentinel skip, not a fuzzy or corpus filter.
PLACEHOLDER_ALREADY_RUN_TASK = "placeholder-already-run"

# Containment threshold for "near-identical by diff" (rule 3b, non-exact case): the fraction of
# the SHORTER file's content that appears verbatim inside the LONGER file. Containment, not
# difflib's symmetric .ratio(), because Finding A's real shape is "old file copied whole, plus a
# few lines appended" -- symmetric ratio penalizes this heavily once the deliverable is
# proportionally longer than the source, even though the source is 100% subsumed.
NEAR_IDENTICAL_CONTAINMENT = 0.90


@dataclass
class TripwireFinding:
    check: str          # "zero_tool_call" | "hash_identical_deliverable"
    packet_id: str
    verdict: str         # "FAIL" or "PASS"
    reason: str
    evidence: dict = field(default_factory=dict)
    classification: str | None = None
    severity: str | None = None

    def to_dict(self) -> dict:
        result = {
            "check": self.check,
            "packet_id": self.packet_id,
            "verdict": self.verdict,
            "reason": self.reason,
            "evidence": self.evidence,
        }
        if self.classification is not None:
            result["classification"] = self.classification
        if self.severity is not None:
            result["severity"] = self.severity
        return result


def claims_research_analysis_review(packet: dict) -> bool:
    task_text = (packet.get("task") or "").lower()
    if any(kw in task_text for kw in RESEARCH_ANALYSIS_REVIEW_KEYWORDS):
        return True
    agent = (packet.get("to_agent") or "").split("@", 1)[0].strip().lower()
    return agent in RESEARCH_ANALYSIS_REVIEW_ROLES


def check_zero_tool_call(packet: dict, reply: str | None = None) -> TripwireFinding:
    """Rule 3a plus its mechanical reply-shape sub-classification.

    Status, tool count, task/role strings, and blank/ACK shape are the only inputs. No model
    judgment is involved and both failure classes retain the hard FAIL verdict.
    """
    pid = packet.get("id", "")
    if packet.get("task") == PLACEHOLDER_ALREADY_RUN_TASK:
        return TripwireFinding(
            "zero_tool_call",
            pid,
            "PASS",
            "known placeholder-already-run sentinel skipped at packet level",
            evidence={"task": PLACEHOLDER_ALREADY_RUN_TASK},
            classification="placeholder_sentinel",
        )
    if packet.get("status") != "complete":
        return TripwireFinding("zero_tool_call", pid, "PASS", "packet not claimed complete")
    if not claims_research_analysis_review(packet):
        return TripwireFinding(
            "zero_tool_call", pid, "PASS",
            "packet does not claim research/analysis/review-type work (rule 3a out of scope)",
        )
    tool_calls = packet.get("tool_calls", 0)
    if tool_calls == 0:
        evidence = {
            "to_agent": packet.get("to_agent"),
            "task": packet.get("task"),
            "tool_calls": tool_calls,
            "reply_recorded": reply is not None,
        }
        if reply is not None and (_is_blank_reply(reply) or _is_bare_ack_echo(reply)):
            return TripwireFinding(
                "zero_tool_call", pid, "FAIL",
                "packet claims research/analysis/review-type work and was marked complete with "
                "zero tool calls, but its recorded reply is blank or only repeats the ACK gate "
                "-- harness/dispatch failure, not a fabricated substantive claim",
                evidence=evidence,
                classification="harness_failure",
                severity="operational",
            )
        classification = (
            "substantive_claim_without_tool_evidence"
            if reply is not None
            else "unclassified_zero_tool_claim"
        )
        return TripwireFinding(
            "zero_tool_call", pid, "FAIL",
            "packet claims research/analysis/review-type work, status=complete, tool_calls=0 "
            "-- substantive work is unsupported by recorded tool evidence",
            evidence=evidence,
            classification=classification,
            severity="integrity",
        )
    return TripwireFinding("zero_tool_call", pid, "PASS", f"tool_calls={tool_calls}")


def _hash_file(path: Path) -> str:
    """Same primitive as `python -m aether_bridge hash` (DEC-021): canon sha256 over decoded
    UTF-8 text, not raw bytes -- keeps this check consistent with every other freshness/identity
    check in the codebase."""
    with open(path, "rb") as f:
        text = canon.decode_utf8(f.read())
    return canon.canon_sha(text)


def _read_text(path: Path) -> str | None:
    try:
        with open(path, "rb") as f:
            return canon.decode_utf8(f.read())
    except (OSError, UnicodeDecodeError):
        return None


def _containment_ratio(a: str, b: str) -> float:
    """Fraction of the shorter string's content matched verbatim (in order) inside the longer
    one. 1.0 means the shorter text is fully subsumed by the longer -- the copy-then-append
    shape Finding A actually used."""
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    if not shorter:
        return 0.0
    matcher = difflib.SequenceMatcher(None, shorter, longer)
    matched = sum(block.size for block in matcher.get_matching_blocks())
    return matched / len(shorter)


def check_hash_identical_deliverable(
    packet_id: str, deliverable: Path, roots: list[Path],
) -> TripwireFinding:
    """Rule 3b. Scoped to the roots the caller passes ("any pre-existing artifact in the same
    root" -- not a whole-filesystem scan). String/hash checks only: exact canon-sha256 match,
    or a difflib ratio above NEAR_IDENTICAL_RATIO for the near-identical case."""
    if not deliverable.is_file():
        return TripwireFinding(
            "hash_identical_deliverable", packet_id, "PASS", f"deliverable not found: {deliverable}",
        )
    deliverable_hash = _hash_file(deliverable)
    deliverable_text = _read_text(deliverable)
    deliverable_resolved = deliverable.resolve()
    for root in roots:
        if not root.is_dir():
            continue
        for candidate in root.rglob("*"):
            if not candidate.is_file() or candidate.resolve() == deliverable_resolved:
                continue
            try:
                candidate_hash = _hash_file(candidate)
            except (OSError, UnicodeDecodeError):
                continue
            if candidate_hash == deliverable_hash:
                return TripwireFinding(
                    "hash_identical_deliverable", packet_id, "FAIL",
                    f"deliverable is byte-identical (canon sha256) to pre-existing file: {candidate}",
                    evidence={
                        "deliverable": str(deliverable), "pre_existing": str(candidate),
                        "sha256": deliverable_hash,
                    },
                )
            if deliverable_text and (candidate_text := _read_text(candidate)):
                containment = _containment_ratio(deliverable_text, candidate_text)
                if containment >= NEAR_IDENTICAL_CONTAINMENT:
                    return TripwireFinding(
                        "hash_identical_deliverable", packet_id, "FAIL",
                        f"deliverable is near-identical (containment {containment:.3f}) to "
                        f"pre-existing file: {candidate}",
                        evidence={
                            "deliverable": str(deliverable), "pre_existing": str(candidate),
                            "containment": containment,
                        },
                    )
    return TripwireFinding(
        "hash_identical_deliverable", packet_id, "PASS",
        "no hash-identical or near-identical pre-existing file found in the given root(s)",
    )


def run_tripwires(
    packets: list[dict],
    deliverables: dict[str, str | Path] | None = None,
    roots: list[str | Path] | None = None,
    replies: dict[str, str] | None = None,
) -> list[TripwireFinding]:
    """Run both mechanical tripwires across a mission's packets.

    deliverables: optional {packet_id: deliverable_path} for packets that claim to have written
    a new file (rule 3b only runs where a deliverable path is known).
    roots: pre-existing-file search roots for the hash/near-identical check (rule 3b scope).
    replies: optional {packet_id: final_reply}; enables the rule 3a harness/integrity split.
    """
    findings: list[TripwireFinding] = [
        check_zero_tool_call(
            packet,
            reply=(replies or {}).get(str(packet.get("id", "")))
            if replies is not None
            else None,
        )
        for packet in packets
    ]
    if deliverables and roots:
        root_paths = [Path(r) for r in roots]
        for pid, path in deliverables.items():
            findings.append(check_hash_identical_deliverable(pid, Path(path), root_paths))
    return findings


def overall_verdict(findings: list[TripwireFinding]) -> str:
    return "FAIL" if any(f.verdict == "FAIL" for f in findings) else "PASS"
