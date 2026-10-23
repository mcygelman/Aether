"""S00073.7 C-5 / Roadmap_Ultralight.md U-01 -- microcase.py tests.

Mock-client / string-only tests (no live model -- the Roadmap's sequencing rule holds no
ultralight LIVE gate runs until the lightweight-tier prerequisite gate is proven; U-01 is
offline build work). Covers: MICROCASE-1 parse/verify round-trip + tamper (fail-closed), the
one-seal micro-gate, tag-lint catching a planted uncited COMPLETE and a planted un-granted
[SOURCE] (Roadmap U-01's explicit gate), and R5 micro-packet schema validation as a type error.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aether_bridge.microcase import (  # noqa: E402
    AETHER_MICRO_TAGS_CARD,
    DYNAMIC_FLOOR_MICRO,
    MAX_RESULT_CHARS_MICRO,
    MICROCASE_TOKEN_CEILING,
    MICROCASE_TOKEN_TARGET,
    STANDARD_LAW_TEXT,
    TOOL_CALL_CAP_MICRO,
    Microcase,
    MicrocaseError,
    MicroPacketSchemaError,
    assemble,
    assemble_standard,
    ask_requests,
    install_micro_packet,
    lint_verdict,
    micro_gate,
    parse,
    status_intent,
    tag_lint,
    verify,
)


# --- parse / verify round-trip (fail-closed, like briefcase.py) --------------------------------

def test_assemble_parse_verify_round_trip():
    raw = assemble_standard("Opal", "Scout", "Finds and summarizes: reads <=2 named files, "
                             "answers one bounded question with [SOURCE]-tagged citations. "
                             "Does NOT write files, does NOT conclude beyond sources.")
    mc = parse(raw)
    assert mc.header.agent == "Opal"
    assert mc.header.role == "Scout"
    assert mc.header.format == "MICROCASE-1"
    assert len(mc.header.seal) == 6
    assert len(mc.header.sha256) == 64
    rep = verify(mc)
    assert rep.ok, rep.failures


def test_assemble_est_tokens_positive_and_reasonable():
    raw = assemble_standard("Opal", "Scout", "Short WHO line.")
    mc = parse(raw)
    assert mc.header.est_tokens > 0
    # Sanity: a real jewel (WHO + verbatim LAW + verbatim TAGS, empty TASK) should be well
    # inside the R2 hard ceiling with room to spare before a task is appended.
    assert mc.header.est_tokens < MICROCASE_TOKEN_CEILING


def test_verify_fails_closed_on_tampered_who():
    raw = assemble_standard("Opal", "Scout", "Original WHO text.")
    tampered = raw.replace("Original WHO text.", "Tampered WHO text!!")
    mc = parse(tampered)
    rep = verify(mc)
    assert not rep.ok
    assert any("SHA256" in f or "SEAL" in f for f in rep.failures)


def test_verify_fails_closed_on_tampered_task():
    raw = assemble_standard("Jasper", "Scribe", "Writes ONE bounded artifact.", task="Write X.")
    tampered = raw.replace("Write X.", "Write X and also do something else entirely.")
    mc = parse(tampered)
    rep = verify(mc)
    assert not rep.ok


def test_parse_fails_closed_on_missing_opening():
    bad = "=== NOT A MICROCASE ===\nFORMAT: MICROCASE-1\n"
    try:
        parse(bad)
        assert False, "should have failed closed"
    except MicrocaseError:
        pass


def test_parse_fails_closed_on_missing_section():
    raw = assemble_standard("Onyx", "Checker", "Verifies ONE stated claim.")
    broken = raw.replace("--- TASK ---\n", "--- NOTATASK ---\n")
    try:
        parse(broken)
        assert False, "should have failed closed"
    except MicrocaseError:
        pass


def test_assemble_standard_carries_verbatim_law_and_tags():
    raw = assemble_standard("Opal", "Scout", "WHO text.")
    mc = parse(raw)
    assert mc.law.rstrip("\n") == STANDARD_LAW_TEXT.rstrip("\n")
    assert mc.tags.rstrip("\n") == AETHER_MICRO_TAGS_CARD.rstrip("\n")


def test_task_slot_appended_at_dispatch():
    raw = assemble_standard("Opal", "Scout", "WHO text.")
    mc = parse(raw)
    assert mc.task.strip() == ""
    dispatched = assemble_standard("Opal", "Scout", "WHO text.", task="Read foo.py, answer X.")
    mc2 = parse(dispatched)
    assert "Read foo.py, answer X." in mc2.task
    # Dispatch-time task text changes the whole-body hash -- confirms TASK rides inside the
    # single seal, same discipline as every other section.
    assert mc.header.sha256 != mc2.header.sha256


# --- one-seal micro-gate (R4) -------------------------------------------------------------------

def test_micro_gate_passes_on_correct_agent_and_seal():
    raw = assemble_standard("Opal", "Scout", "WHO text.")
    mc = parse(raw)
    reply = f"AGENT: Opal\nSTATUS: COMPLETE\nSEAL: {mc.header.seal}"
    result = micro_gate(mc, reply)
    assert result.passed, result.reason


def test_micro_gate_fails_on_wrong_seal():
    raw = assemble_standard("Opal", "Scout", "WHO text.")
    mc = parse(raw)
    reply = "AGENT: Opal\nSTATUS: COMPLETE\nSEAL: ffffff"
    result = micro_gate(mc, reply)
    assert not result.passed
    assert "seal" in result.reason.lower()


def test_micro_gate_fails_on_wrong_agent():
    raw = assemble_standard("Opal", "Scout", "WHO text.")
    mc = parse(raw)
    reply = f"AGENT: Jasper\nSTATUS: COMPLETE\nSEAL: {mc.header.seal}"
    result = micro_gate(mc, reply)
    assert not result.passed
    assert "agent" in result.reason.lower()


def test_micro_gate_cheap_cost_one_seal_not_fourteen():
    # R4's whole point: one seal recited costs ~15 output tokens, not the ~950 the full
    # 14-seal briefcase recital cost in S00073.6. Confirm the gate only ever expects ONE seal.
    raw = assemble_standard("Opal", "Scout", "WHO text.")
    mc = parse(raw)
    assert mc.header.seal  # exactly one seal on the whole microcase, not per-section


# --- tag-lint: the explicit Roadmap U-01 gate ---------------------------------------------------

def test_tag_lint_passes_clean_reply():
    reply = (
        "[RESULT] The config default is 8192.\n"
        "[SOURCE] config.py line 12\n"
        "STATUS: COMPLETE"
    )
    findings = tag_lint(reply, granted_files=["config.py"])
    assert lint_verdict(findings) == "PASS"


def test_tag_lint_catches_planted_uncited_complete():
    """Roadmap U-01 gate, explicit: 'tag-lint catching a planted uncited COMPLETE.' A [RESULT]
    line with a factual-claim marker (a number here) and no [SOURCE] anywhere near it."""
    reply = (
        "[RESULT] The function returns 42 in all cases.\n"
        "STATUS: COMPLETE"
    )
    findings = tag_lint(reply, granted_files=["config.py"])
    assert lint_verdict(findings) == "FAIL"
    fails = [f for f in findings if f.verdict == "FAIL"]
    assert any(f.rule == "source_for_factual_result" for f in fails)


def test_tag_lint_catches_planted_ungranted_source():
    """Roadmap U-01 gate, explicit: 'tag-lint catching ... a planted un-granted [SOURCE].' A
    [SOURCE] citing a file the packet never granted -- automatic integrity flag."""
    reply = (
        "[RESULT] The secret key is stored in plaintext.\n"
        "[SOURCE] secrets.py line 3\n"
        "STATUS: COMPLETE"
    )
    findings = tag_lint(reply, granted_files=["config.py"])  # secrets.py was never granted
    assert lint_verdict(findings) == "FAIL"
    fails = [f for f in findings if f.verdict == "FAIL"]
    assert any(f.rule == "cited_files_granted" for f in fails)


def test_tag_lint_treats_missing_status_line_as_advisory():
    reply = "[RESULT] Something happened.\n[SOURCE] file.py line 1"
    findings = tag_lint(reply, granted_files=["file.py"])
    assert lint_verdict(findings) == "PASS"
    status = next(f for f in findings if f.rule == "status_line")
    assert status.verdict == "ADVISORY"


def test_result_and_stream_tags_are_advisory_voice_not_truth_failures():
    findings = tag_lint("Work completed successfully.\nSTATUS: COMPLETE")
    assert lint_verdict(findings) == "PASS"
    assert next(f for f in findings if f.rule == "result_tag").verdict == "ADVISORY"
    assert next(
        f for f in findings if f.rule == "all_content_stream_tagged"
    ).verdict == "ADVISORY"


def test_tag_lint_accepts_well_formed_partial_and_blocked_status():
    partial = "[ASK] I need the file.\nSTATUS: PARTIAL — missing input file"
    blocked = "[ASK] I need access.\nSTATUS: BLOCKED — no read permission on root"
    assert lint_verdict(tag_lint(partial)) == "PASS"
    assert lint_verdict(tag_lint(blocked)) == "PASS"


def test_tag_lint_does_not_cap_note_count():
    reply = (
        "[RESULT] Fine.\n"
        "STATUS: COMPLETE\n"
        "[NOTE] First lesson.\n"
        "[NOTE] Second lesson -- should not be here.\n"
    )
    findings = tag_lint(reply)
    assert not any(f.rule in {"note_limit", "note_length"} for f in findings)
    assert lint_verdict(findings) == "PASS"


def test_tag_lint_does_not_cap_note_length():
    long_note = "This is a very long note that goes on and on and on well past the forty " \
                "token ceiling the AETHER-µ card sets for a single [NOTE] line, by design."
    reply = f"[RESULT] Fine.\nSTATUS: COMPLETE\n[NOTE] {long_note}\n"
    findings = tag_lint(reply)
    assert not any(f.rule in {"note_limit", "note_length"} for f in findings)
    assert lint_verdict(findings) == "PASS"


def test_status_intent_parses_plain_blocker_and_blocked_outranks_voice():
    assert status_intent("I cannot review because the required input is absent.") == "blocked"
    assert status_intent("**Status**: Blocked\nREVIEW_CHANGES_REQUESTED") == "blocked"


def test_ask_lines_are_selector_misses_not_lint_defects():
    reply = "[ASK] Please provide the missing design.md.\nI cannot proceed without it."
    assert ask_requests(reply) == ["Please provide the missing design.md."]
    assert lint_verdict(tag_lint(reply)) == "PASS"


def test_tag_lint_unsure_line_does_not_require_source():
    reply = "[UNSURE] The function might return 42, I have not checked.\nSTATUS: COMPLETE"
    findings = tag_lint(reply, granted_files=["file.py"])
    # [UNSURE] is exempt by design -- it's the honest downgrade path the card exists to protect.
    fails = [f for f in findings if f.verdict == "FAIL" and f.rule == "source_for_factual_result"]
    assert fails == []


# --- R5: micro-packet schema validation, a type error not a convention -------------------------

def test_install_micro_packet_accepts_valid_row():
    spec = install_micro_packet({
        "id": "P1", "to_agent": "Opal", "task": "Answer the bounded question.",
        "named_files": ["config.py"], "output_cap": 500,
    })
    assert spec.id == "P1"
    assert spec.named_files == ["config.py"]
    assert spec.output_cap == 500


def test_install_micro_packet_rejects_missing_named_files():
    """'review the folder' is unrepresentable -- no named_files at all must be a hard error."""
    try:
        install_micro_packet({"id": "P1", "to_agent": "Opal", "task": "Review the folder.",
                               "output_cap": 500})
        assert False, "should have raised MicroPacketSchemaError"
    except MicroPacketSchemaError:
        pass


def test_install_micro_packet_rejects_too_many_named_files():
    try:
        install_micro_packet({
            "id": "P1", "to_agent": "Opal", "task": "x",
            "named_files": ["a.py", "b.py", "c.py"], "output_cap": 500,
        })
        assert False, "should have raised MicroPacketSchemaError"
    except MicroPacketSchemaError:
        pass


def test_install_micro_packet_rejects_missing_output_cap():
    try:
        install_micro_packet({"id": "P1", "to_agent": "Opal", "task": "x",
                               "named_files": ["a.py"]})
        assert False, "should have raised MicroPacketSchemaError"
    except MicroPacketSchemaError:
        pass


def test_install_micro_packet_rejects_zero_or_negative_output_cap():
    for bad_cap in (0, -1):
        try:
            install_micro_packet({"id": "P1", "to_agent": "Opal", "task": "x",
                                   "named_files": ["a.py"], "output_cap": bad_cap})
            assert False, f"should have raised for output_cap={bad_cap}"
        except MicroPacketSchemaError:
            pass


def test_install_micro_packet_is_a_type_error_subclass():
    # R5, verbatim: "the S00073.6 fixture error becomes a type error." Confirm the exception
    # actually IS one, not just named like one.
    assert issubclass(MicroPacketSchemaError, TypeError)


# --- Tuned tier defaults (Roadmap U-01) ----------------------------------------------------------

def test_tuned_tier_defaults_match_roadmap():
    assert DYNAMIC_FLOOR_MICRO == 3072
    assert MAX_RESULT_CHARS_MICRO == 8000
    assert TOOL_CALL_CAP_MICRO == 6
    assert MICROCASE_TOKEN_TARGET == 1500
    assert MICROCASE_TOKEN_CEILING == 2000


# --- 2026-07-20 fixes: U-04 live run found tag-lint itself too strict on genuine, well-cited ----
# replies (blank-line-separated [SOURCE], and a bare filename mention alone triggering the rule).

def test_tag_lint_accepts_source_separated_by_blank_line():
    """The exact shape Onyx and Jasper both produced live once the STATUS/verdict and
    empty-reply gaps were fixed: a genuine [SOURCE], just with a blank line before it."""
    reply = (
        '[RESULT] Claim "Repair Shop is night-inactive" is CONFIRMED.\n'
        "\n"
        '[SOURCE] structures.txt: Repair Shop Landing (line 52) states "(night inactive)"\n'
        "\n"
        "STATUS: COMPLETE"
    )
    findings = tag_lint(reply, granted_files=["structures.txt"])
    assert lint_verdict(findings) == "PASS"


def test_tag_lint_still_catches_missing_source_across_a_blank_line():
    """The blank-line tolerance must not become 'any [SOURCE] anywhere in the reply counts' --
    a [SOURCE] that belongs to a LATER, different [RESULT] must never be credited to an earlier
    uncited one."""
    reply = (
        '[RESULT] The function returns 42 in all cases.\n'
        "\n"
        '[RESULT] Config default is 8192, cited below.\n'
        "[SOURCE] config.py line 12\n"
        "STATUS: COMPLETE"
    )
    findings = tag_lint(reply, granted_files=["config.py"])
    assert lint_verdict(findings) == "FAIL"
    fails = [f for f in findings if f.verdict == "FAIL" and f.rule == "source_for_factual_result"]
    assert len(fails) == 1
    assert fails[0].evidence["line"] == 0  # the "returns 42" line, not the cited one


def test_tag_lint_bare_filename_mention_alone_does_not_require_source():
    """A [RESULT] that merely NAMES a file (a preamble, or reporting the agent's own just-
    written artifact) is not itself an uncited external claim -- only digits and quotes are
    trigger-sufficient on their own now."""
    reply = (
        "[RESULT] Based on structures.txt, here is the summary:\n"
        "STATUS: COMPLETE"
    )
    findings = tag_lint(reply, granted_files=["structures.txt"])
    fails = [f for f in findings if f.verdict == "FAIL" and f.rule == "source_for_factual_result"]
    assert fails == []


def test_tag_lint_filename_with_digit_still_requires_source():
    """A filename mention is exempt on its own, but if the SAME line also carries a digit (a
    real countable claim, e.g. a byte count), the digit still triggers the rule -- filenames
    don't launder an otherwise-uncited numeric claim."""
    reply = (
        "[RESULT] File summary.txt written with 1424 bytes.\n"
        "STATUS: COMPLETE"
    )
    findings = tag_lint(reply, granted_files=["summary.txt"])
    fails = [f for f in findings if f.verdict == "FAIL" and f.rule == "source_for_factual_result"]
    assert len(fails) == 1


def test_tag_lint_accepts_self_citation_for_tool_outcome_claim():
    reply = (
        "[RESULT] File summary.txt written with 1424 bytes, journal 19f7cd99819.\n"
        "[SOURCE] own write_file result · journal 19f7cd99819\n"
        "STATUS: COMPLETE"
    )
    findings = tag_lint(reply, granted_files=["summary.txt"])
    source_findings = [f for f in findings if f.rule == "source_for_factual_result"]
    assert source_findings
    assert all(f.verdict == "PASS" for f in source_findings)
    assert lint_verdict(findings) == "PASS"


def test_tag_lint_self_citable_outcome_without_source_still_fails():
    reply = (
        "[RESULT] File summary.txt written with 1424 bytes, journal 19f7cd99819.\n"
        "STATUS: COMPLETE"
    )
    findings = tag_lint(reply, granted_files=["summary.txt"])
    fails = [f for f in findings if f.rule == "source_for_factual_result" and f.verdict == "FAIL"]
    assert len(fails) == 1


def test_tag_lint_self_citation_does_not_cover_file_content_claim():
    reply = (
        "[RESULT] File summary.txt now contains 10 verified buildings.\n"
        "[SOURCE] own write_file result · journal 19f7cd99819\n"
        "STATUS: COMPLETE"
    )
    findings = tag_lint(reply, granted_files=["summary.txt"])
    fails = [f for f in findings if f.rule == "source_for_factual_result" and f.verdict == "FAIL"]
    assert len(fails) == 1
    assert "tool outcomes only" in fails[0].reason


def test_tag_lint_digit_inside_filename_version_does_not_require_source():
    """2026-07-20, second pass: exact repro of the real U-04 live-run failure (P1). A digit that
    is part of the FILENAME's own text -- 'v3' in Collection_Village_Structures_v3.txt -- is not
    an independent factual claim and must not defeat the bare-filename-mention exemption. Only a
    digit/quote OUTSIDE any filename token should still trigger the rule (see the sibling test
    above, where '1424 bytes' sits next to a filename with no digit in its own name)."""
    reply = (
        "[RESULT] Based on Collection_Village_Structures_v3.txt, here is the summary:\n"
        "STATUS: COMPLETE"
    )
    findings = tag_lint(reply, granted_files=["Collection_Village_Structures_v3.txt"])
    fails = [f for f in findings if f.verdict == "FAIL" and f.rule == "source_for_factual_result"]
    assert fails == []
