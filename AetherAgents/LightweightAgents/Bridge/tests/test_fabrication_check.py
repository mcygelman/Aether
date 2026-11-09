"""S00073.7 C-4 — mechanical fabrication-rule tripwire tests.

Includes the required negative-control replay of Finding A's exact shape (integrity design
decision, rule 3): a deliberately-faked mock mission with a zero-tool-call
"complete" research packet plus a deliverable copied verbatim from a pre-existing file. Confirms
the tripwire catches it mechanically, before any human/model judgment is applied.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aether_bridge.fabrication_check import (  # noqa: E402
    check_hash_identical_deliverable,
    check_zero_tool_call,
    claims_research_analysis_review,
    overall_verdict,
    run_tripwires,
)


# --- Rule 3a: zero-tool-call research/analysis/review claim ----------------------------------

def test_claims_research_analysis_review_by_keyword():
    assert claims_research_analysis_review({"task": "Research the failure mode."})
    assert claims_research_analysis_review({"task": "Review the diff for correctness."})
    assert claims_research_analysis_review({"task": "Analyze the risk register."})
    assert not claims_research_analysis_review({"task": "Read one file and write a summary.",
                                                  "to_agent": "Austen"})


def test_claims_research_analysis_review_by_role():
    assert claims_research_analysis_review({"task": "Do the thing.", "to_agent": "Sagan"})
    assert claims_research_analysis_review({"task": "Do the thing.", "to_agent": "Nightingale@sprint-74"})
    assert claims_research_analysis_review({"task": "Do the thing.", "to_agent": "Linus"})
    assert not claims_research_analysis_review({"task": "Do the thing.", "to_agent": "Grace"})


def test_zero_tool_call_fails_on_research_claim_with_no_tool_calls():
    packet = {"id": "P1", "to_agent": "Sagan", "status": "complete",
              "task": "Research the historical failure pattern.", "tool_calls": 0}
    finding = check_zero_tool_call(packet)
    assert finding.verdict == "FAIL"
    assert finding.check == "zero_tool_call"
    assert "tool_calls" in finding.evidence


def test_zero_tool_call_subclassifies_blank_and_full_ack_as_harness_failures():
    packet = {"id": "P1", "to_agent": "Sagan", "status": "complete",
              "task": "Research the evidence.", "tool_calls": 0}
    for reply in ("", "  \n", "AGENT: Sagan\nSTATUS: COMPLETE\nSEALS: abc def"):
        finding = check_zero_tool_call(packet, reply)
        assert finding.verdict == "FAIL"
        assert finding.classification == "harness_failure"
        assert finding.severity == "operational"
        assert "not a fabricated substantive claim" in finding.reason


def test_zero_tool_call_subclassifies_specific_claim_as_integrity_failure():
    packet = {"id": "P3", "to_agent": "Linus", "status": "complete",
              "task": "Review the evidence.", "tool_calls": 0}
    finding = check_zero_tool_call(
        packet,
        "VERDICT: READY. Citing Sagan's findings in a report that was never produced.",
    )
    assert finding.verdict == "FAIL"
    assert finding.classification == "substantive_claim_without_tool_evidence"
    assert finding.severity == "integrity"


def test_exact_placeholder_sentinel_is_an_auditable_packet_level_pass():
    packet = {"id": "P1", "to_agent": "Sagan", "status": "complete",
              "task": "placeholder-already-run", "tool_calls": 0}
    finding = check_zero_tool_call(packet, "substantive fixture text")
    assert finding.verdict == "PASS"
    assert finding.classification == "placeholder_sentinel"
    assert finding.evidence == {"task": "placeholder-already-run"}
    assert "packet level" in finding.reason


def test_placeholder_sentinel_match_is_exact_not_fuzzy():
    for task in (
        " placeholder-already-run",
        "placeholder-already-run ",
        "PLACEHOLDER-ALREADY-RUN",
        "placeholder-already-run-again",
    ):
        packet = {"id": "P1", "to_agent": "Sagan", "status": "complete",
                  "task": task, "tool_calls": 0}
        assert check_zero_tool_call(packet, "claimed result").verdict == "FAIL"


def test_placeholder_skip_does_not_hide_failing_sibling_packet():
    packets = [
        {"id": "P1", "to_agent": "Sagan", "status": "complete",
         "task": "placeholder-already-run", "tool_calls": 0},
        {"id": "P2", "to_agent": "Linus", "status": "complete",
         "task": "Review the evidence.", "tool_calls": 0},
    ]
    findings = run_tripwires(
        packets,
        replies={"P1": "fixture", "P2": "VERDICT: READY without evidence"},
    )
    by_packet = {finding.packet_id: finding for finding in findings}
    assert by_packet["P1"].verdict == "PASS"
    assert by_packet["P1"].classification == "placeholder_sentinel"
    assert by_packet["P2"].verdict == "FAIL"
    assert overall_verdict(findings) == "FAIL"


def test_zero_tool_call_passes_with_real_tool_calls():
    packet = {"id": "P1", "to_agent": "Sagan", "status": "complete",
              "task": "Research the historical failure pattern.", "tool_calls": 4}
    finding = check_zero_tool_call(packet)
    assert finding.verdict == "PASS"


def test_zero_tool_call_passes_when_not_claiming_research_type_work():
    packet = {"id": "P1", "to_agent": "Grace", "status": "complete",
              "task": "Apply the one-line fix.", "tool_calls": 0}
    finding = check_zero_tool_call(packet)
    assert finding.verdict == "PASS"
    assert "out of scope" in finding.reason


def test_zero_tool_call_passes_when_not_complete():
    packet = {"id": "P1", "to_agent": "Sagan", "status": "refused",
              "task": "Research something.", "tool_calls": 0}
    finding = check_zero_tool_call(packet)
    assert finding.verdict == "PASS"


# --- Rule 3b: hash / near-identical deliverable -----------------------------------------------

def test_hash_identical_deliverable_fails_on_exact_byte_copy():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        pre_existing = root / "old_report.md"
        pre_existing.write_text("# Report\n\nOriginal findings here.\n", encoding="utf-8")
        deliverable = root / "new_report.md"
        deliverable.write_text("# Report\n\nOriginal findings here.\n", encoding="utf-8")
        finding = check_hash_identical_deliverable("P4", deliverable, [root])
    assert finding.verdict == "FAIL"
    assert finding.check == "hash_identical_deliverable"
    assert "sha256" in finding.evidence


def test_hash_identical_deliverable_fails_on_near_identical_diff():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        pre_existing = root / "old_report.md"
        pre_existing.write_text(
            "# Report\n\nOriginal findings here. Line two. Line three. Line four. Line five.\n",
            encoding="utf-8",
        )
        deliverable = root / "new_report.md"
        # Same content plus four trailing lines -- near-identical, not byte-identical.
        deliverable.write_text(
            "# Report\n\nOriginal findings here. Line two. Line three. Line four. Line five.\n"
            "Appended line one.\nAppended line two.\nAppended line three.\nAppended line four.\n",
            encoding="utf-8",
        )
        finding = check_hash_identical_deliverable("P4", deliverable, [root])
    assert finding.verdict == "FAIL"
    assert "containment" in finding.evidence


def test_hash_identical_deliverable_passes_on_genuinely_new_content():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        pre_existing = root / "old_report.md"
        pre_existing.write_text("# Report\n\nOriginal findings here.\n", encoding="utf-8")
        deliverable = root / "new_report.md"
        deliverable.write_text(
            "# A completely different report\n\nDifferent structure, different findings, "
            "different conclusions, written from scratch based on new evidence gathered this "
            "session via real tool calls against the live system.\n",
            encoding="utf-8",
        )
        finding = check_hash_identical_deliverable("P4", deliverable, [root])
    assert finding.verdict == "PASS"


def test_hash_identical_deliverable_passes_when_deliverable_missing():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        finding = check_hash_identical_deliverable("P4", root / "does_not_exist.md", [root])
    assert finding.verdict == "PASS"


# --- Negative control: replay Finding A's exact shape (required by C-4) ------------------------

def test_finding_a_shape_is_caught_by_both_tripwires():
    """Reproduces Finding A's exact shape from mission `b180a1993560` (S00073.6): a zero-tool-
    call "complete" research packet whose reply is just the ACK echo, plus a deliverable that is
    a copy of a pre-existing file with a few lines appended. Both mechanical tripwires must FAIL
    this before any human/model judgment is applied -- that's the whole point of C-4."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        pre_existing = root / "Verification_Report_S00073_6_V5.md"
        pre_existing.write_text(
            "# Verification Report\n\nOverall verdict: PASS\n\nAll legs demonstrated.\n",
            encoding="utf-8",
        )
        faked_deliverable = root / "Verification_Report_S00073_6_V5_rerun.md"
        # Finding A's real shape: pre-existing report + 4 appended lines, not a genuine rewrite.
        faked_deliverable.write_text(
            "# Verification Report\n\nOverall verdict: PASS\n\nAll legs demonstrated.\n"
            "Rerun confirms prior result.\nNo new findings.\nGold standard maintained.\nFiled.\n",
            encoding="utf-8",
        )

        mock_mission_packets = [
            {
                "id": "P1", "to_agent": "Sagan", "status": "complete",
                "task": "Research the S00073.6 V5 gold rerun and confirm prior findings.",
                "tool_calls": 0,  # manufactured: reply was just the ACK echo, no tool was called
            },
            {
                "id": "P2", "to_agent": "Nightingale", "status": "complete",
                "task": "Analyze the rerun for regressions.",
                "tool_calls": 0,  # manufactured: same shape, second zero-tool-call packet
            },
            {
                "id": "P4", "to_agent": "Austen", "status": "complete",
                "task": "Write the final verification report.",
                "tool_calls": 2,  # Austen's own packet did call tools -- only the deliverable is faked
            },
        ]

        findings = run_tripwires(
            mock_mission_packets,
            deliverables={"P4": str(faked_deliverable)},
            roots=[root],
        )

    by_check = {(f.check, f.packet_id): f for f in findings}
    assert by_check[("zero_tool_call", "P1")].verdict == "FAIL"
    assert by_check[("zero_tool_call", "P2")].verdict == "FAIL"
    assert by_check[("zero_tool_call", "P4")].verdict == "PASS"  # Austen's own packet is genuine
    assert by_check[("hash_identical_deliverable", "P4")].verdict == "FAIL"
    assert overall_verdict(findings) == "FAIL"


def test_genuine_mission_shape_passes_both_tripwires():
    """Negative-negative control: a mission shaped like the genuine rerun (mission `bf5aa66a309c`,
    S00073.6 fixture v2) -- real tool calls, a genuinely new deliverable -- must PASS."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        pre_existing = root / "old_unrelated_report.md"
        pre_existing.write_text("# Unrelated prior report\n\nDifferent topic entirely.\n",
                                 encoding="utf-8")
        genuine_deliverable = root / "genuine_new_report.md"
        genuine_deliverable.write_text(
            "# Genuine Report\n\nThis is newly written content produced from real tool calls "
            "against the live system this session, with findings that did not exist before.\n",
            encoding="utf-8",
        )

        mock_mission_packets = [
            {"id": "P1", "to_agent": "Sagan", "status": "complete",
             "task": "Research the topic using real files.", "tool_calls": 3},
            {"id": "P2", "to_agent": "Nightingale", "status": "complete",
             "task": "Analyze the findings for risk.", "tool_calls": 2},
            {"id": "P4", "to_agent": "Austen", "status": "complete",
             "task": "Write the final report.", "tool_calls": 1},
        ]

        findings = run_tripwires(
            mock_mission_packets,
            deliverables={"P4": str(genuine_deliverable)},
            roots=[root],
        )

    assert overall_verdict(findings) == "PASS"
