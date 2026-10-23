"""S00077 vendor-dialect replay and negative controls."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aether_bridge.dialect import (  # noqa: E402
    DEEPSEEK,
    OPENAI_ONLY,
    QUARANTINE_MARKER,
    normalize,
    normalize_continuation,
)


EVIDENCE = Path(__file__).resolve().parents[1] / "studio_logs"
LOGS = (
    EVIDENCE / "studio_Assistant Persona_1784514310.jsonl",
    EVIDENCE / "studio_Assistant Persona_1784597997.jsonl",
)


def _records():
    for path in LOGS:
        for line in path.read_text(encoding="utf-8").splitlines():
            yield json.loads(line)


def test_all_52_evidence_entries_parse_or_quarantine_without_raw_leaks():
    leaked = [record for record in _records() if "DSML" in record.get("text", "")]
    assert len(leaked) == 52
    for record in leaked:
        raw = record["text"]
        result = normalize({"role": "assistant", "content": raw}, DEEPSEEK)
        assert result.get("_dialect_outcome") in {"parsed", "quarantined"}
        assert "<｜｜DSML｜｜" not in result["content"]
        if result["_dialect_outcome"] == "parsed":
            assert result["tool_calls"]
            for index, call in enumerate(result["tool_calls"]):
                assert call["id"] == f"dsml_{index}"
                json.loads(call["function"]["arguments"])
        else:
            assert result["content"] == QUARANTINE_MARKER
            assert result.get("tool_calls") == []
            assert result["_dialect_raw"] == raw


def test_clean_evidence_sample_is_byte_identical_and_same_object():
    clean = [record for record in _records() if "DSML" not in record.get("text", "")][:25]
    assert len(clean) == 25
    for record in clean:
        msg = {"role": "assistant", "content": record.get("text", ""), "usage": record.get("usage")}
        assert normalize(msg, DEEPSEEK) is msg


def test_quoted_dsml_in_fenced_and_inline_code_is_clean():
    fenced = (
        "The backend emitted:\n```xml\n"
        "<｜｜DSML｜｜tool_calls>\n"
        '<｜｜DSML｜｜invoke name="read_file">\n'
        '<｜｜DSML｜｜parameter name="path" string="true">x</｜｜DSML｜｜parameter>\n'
        "</｜｜DSML｜｜invoke>\n</｜｜DSML｜｜tool_calls>\n```"
    )
    inline = "Literal `<｜｜DSML｜｜tool_calls></｜｜DSML｜｜tool_calls>` is documentation."
    for content in (fenced, inline):
        msg = {"role": "assistant", "content": content}
        assert normalize(msg, DEEPSEEK) is msg
        assert "tool_calls" not in msg


def test_multiple_invokes_and_typed_parameters_are_recovered_deterministically():
    raw = (
        "Before\n<｜｜DSML｜｜tool_calls>\n"
        '<｜｜DSML｜｜invoke name="read_file">'
        '<｜｜DSML｜｜parameter name="path" string="true">a.txt</｜｜DSML｜｜parameter>'
        "</｜｜DSML｜｜invoke>\n"
        '<｜｜DSML｜｜invoke name="read_file">'
        '<｜｜DSML｜｜parameter name="offset" string="false">12</｜｜DSML｜｜parameter>'
        "</｜｜DSML｜｜invoke>\n"
        "</｜｜DSML｜｜tool_calls>\nAfter"
    )
    first = normalize({"content": raw}, DEEPSEEK)
    second = normalize({"content": raw}, DEEPSEEK)
    assert first == second
    assert first["content"] == "Before\n\nAfter"
    assert [call["id"] for call in first["tool_calls"]] == ["dsml_0", "dsml_1"]
    assert json.loads(first["tool_calls"][1]["function"]["arguments"]) == {"offset": 12}


def test_malformed_or_unknown_markup_quarantines_without_partial_calls():
    malformed = (
        "<｜｜DSML｜｜tool_calls>"
        '<｜｜DSML｜｜invoke name="read_file">'
        '<｜｜DSML｜｜parameter name="path" string="true">x'
        "</｜｜DSML｜｜invoke></｜｜DSML｜｜tool_calls>"
    )
    unknown = "<｜｜FUTURE｜｜tool_calls>opaque</｜｜FUTURE｜｜tool_calls>"
    for raw in (malformed, unknown):
        result = normalize({"content": raw, "tool_calls": []}, DEEPSEEK)
        assert result["content"] == QUARANTINE_MARKER
        assert result["tool_calls"] == []
        assert result["_dialect_outcome"] == "quarantined"
        assert result["_dialect_raw"] == raw


def test_nested_shorter_fence_inside_longer_fence_masks_quoted_write_call():
    quoted = (
        "Here is the quoted file:\n````\nThe log showed:\n```\n"
        "<｜｜DSML｜｜tool_calls>"
        '<｜｜DSML｜｜invoke name="write_file">'
        '<｜｜DSML｜｜parameter name="root" string="true">project</｜｜DSML｜｜parameter>'
        '<｜｜DSML｜｜parameter name="path" string="true">danger.txt</｜｜DSML｜｜parameter>'
        '<｜｜DSML｜｜parameter name="content" string="true">no</｜｜DSML｜｜parameter>'
        "</｜｜DSML｜｜invoke></｜｜DSML｜｜tool_calls>\n"
        "```\nend of file\n````"
    )
    msg = {"content": quoted}
    assert normalize(msg, DEEPSEEK) is msg
    assert "tool_calls" not in msg


def test_unterminated_backticks_quarantine_real_dsml_instead_of_hiding_or_executing_it():
    block = (
        "<｜｜DSML｜｜tool_calls>"
        '<｜｜DSML｜｜invoke name="read_file">'
        '<｜｜DSML｜｜parameter name="path" string="true">notes.md</｜｜DSML｜｜parameter>'
        "</｜｜DSML｜｜invoke></｜｜DSML｜｜tool_calls>"
    )
    for raw in (
        f"Let me check `notes.md and read it.\n{block}",
        f"An unfinished fence:\n```\n{block}",
    ):
        result = normalize({"content": raw}, DEEPSEEK)
        assert result["_dialect_outcome"] == "quarantined"
        assert result["tool_calls"] == []
        assert result["content"] == QUARANTINE_MARKER
        assert result["_dialect_raw"] == raw
        assert "<｜｜DSML｜｜" not in result["content"]


def test_truncated_quoted_fence_with_write_call_quarantines_zero_calls():
    raw = (
        "The Issues file documents this leaked markup:\n```\n"
        "<｜｜DSML｜｜tool_calls>"
        '<｜｜DSML｜｜invoke name="write_file">'
        '<｜｜DSML｜｜parameter name="root" string="true">project</｜｜DSML｜｜parameter>'
        '<｜｜DSML｜｜parameter name="path" string="true">VICTIM.txt</｜｜DSML｜｜parameter>'
        '<｜｜DSML｜｜parameter name="content" string="true">OVERWRITTEN</｜｜DSML｜｜parameter>'
        "</｜｜DSML｜｜invoke></｜｜DSML｜｜tool_calls>"
    )
    source = {"content": raw, "_finish_reason": "length"}
    result = normalize(source, DEEPSEEK)
    assert result["_dialect_outcome"] == "quarantined"
    assert result["content"] == QUARANTINE_MARKER
    assert result["tool_calls"] == []
    assert result["_dialect_raw"] == raw
    assert result["_finish_reason"] == "length"
    assert source == {"content": raw, "_finish_reason": "length"}


def test_unknown_detection_is_structural_and_local_dialect_is_true_opt_out():
    ordinary = "The backend printed <｜end_of_sentence｜> after the answer. Result: 7."
    deepseek_msg = {"content": ordinary}
    local_msg = {"content": "Compare a <｜b in the token stream. Answer: 42."}
    assert normalize(deepseek_msg, DEEPSEEK) is deepseek_msg
    assert normalize(local_msg, OPENAI_ONLY) is local_msg

    unknown = "<｜｜FUTURE｜｜tool_calls>opaque</｜｜FUTURE｜｜tool_calls>"
    result = normalize({"content": unknown}, DEEPSEEK)
    assert result["_dialect_outcome"] == "quarantined"
    assert result["tool_calls"] == []

    # OPENAI_ONLY is an explicit compatibility opt-out, even for complete
    # foreign markup: local callers retain byte-identical behavior.
    local_foreign = {"content": unknown}
    assert normalize(local_foreign, OPENAI_ONLY) is local_foreign


def test_zero_parameter_invoke_quarantines_and_clears_preexisting_calls():
    raw = (
        "<｜｜DSML｜｜tool_calls>"
        '<｜｜DSML｜｜invoke name="write_file"></｜｜DSML｜｜invoke>'
        "</｜｜DSML｜｜tool_calls>"
    )
    result = normalize(
        {"content": raw, "tool_calls": [{"id": "native-live-call"}]},
        DEEPSEEK,
    )
    assert result["_dialect_outcome"] == "quarantined"
    assert "no parameters" in result["_dialect_error"]
    assert result["tool_calls"] == []


def test_continuation_join_preserves_a_genuine_inherited_quarantine():
    continued = {
        "content": QUARANTINE_MARKER,
        "tool_calls": [],
        "_dialect_outcome": "quarantined",
        "_dialect_error": "unregistered vendor markup",
        "_dialect_raw": "<｜｜FUTURE｜｜tool_calls>opaque",
        "_finish_reason": "stop",
    }
    result = normalize_continuation("First half", continued, DEEPSEEK)

    assert result["_dialect_outcome"] == "quarantined"
    assert result["_dialect_error"] == "unregistered vendor markup"
    assert result["_dialect_raw"] == continued["_dialect_raw"]
    assert result["tool_calls"] == []
    assert result["_continued"] is True
    assert QUARANTINE_MARKER in result["content"]


if __name__ == "__main__":
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"{len(tests)}/{len(tests)} green")
