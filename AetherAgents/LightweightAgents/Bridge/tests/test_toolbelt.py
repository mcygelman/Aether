"""tests/test_toolbelt.py -- ToolBelt.dispatch() argument-validation regression.

2026-07-20, live bug found in a real DeepSeek chat session (Michel's Aether Studio persona
chat): the model called `search` without the required `root` argument. `dispatch()`'s main
table[name](**args) path had no guard against a malformed call -- unlike the run_command branch,
which already wrapped its call in try/except and converted any exception to ToolError. The bad
call raised a raw TypeError, which studio.py's `except ToolError` doesn't catch, so it propagated
all the way to the HTTP layer as an unhandled 500 -- killing the turn AND leaving
StudioSession.messages with a dangling assistant tool_calls entry with no matching tool response,
corrupting every later turn in the same session (the model/API expects one tool-role reply per
tool_call_id).
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aether_bridge.toolbelt import ToolBelt, ToolError  # noqa: E402


def _belt_with_root(tmp_path: Path) -> ToolBelt:
    journal = tmp_path / "journal"
    root_dir = tmp_path / "proj"
    root_dir.mkdir()
    (root_dir / "a.txt").write_text("hello world\n", encoding="utf-8")
    belt = ToolBelt(journal)
    belt.register_root("proj", str(root_dir), "auto")
    return belt


def test_dispatch_missing_required_argument_raises_tool_error_not_typeerror():
    with tempfile.TemporaryDirectory() as td:
        belt = _belt_with_root(Path(td))
        # Exact repro: `search` called without its required `root` argument.
        try:
            belt.dispatch("search", {"regex": "hello"})
            assert False, "expected ToolError"
        except ToolError as e:
            assert "search" in str(e)
        except TypeError:
            assert False, "TypeError leaked instead of being converted to ToolError"


def test_dispatch_unexpected_keyword_argument_raises_tool_error():
    with tempfile.TemporaryDirectory() as td:
        belt = _belt_with_root(Path(td))
        try:
            belt.dispatch("read_file", {"root": "proj", "path": "a.txt", "not_a_real_arg": 1})
            assert False, "expected ToolError"
        except ToolError:
            pass
        except TypeError:
            assert False, "TypeError leaked instead of being converted to ToolError"


def test_dispatch_still_works_normally_for_well_formed_calls():
    """Regression guard: the new try/except must not change behavior for correct calls."""
    with tempfile.TemporaryDirectory() as td:
        belt = _belt_with_root(Path(td))
        hits = belt.dispatch("search", {"root": "proj", "regex": "hello"})
        assert hits and hits[0]["file"] == "a.txt"
        text = belt.dispatch("read_file", {"root": "proj", "path": "a.txt"})
        assert "hello world" in text


def test_dispatch_unknown_tool_still_raises_tool_error_unchanged():
    """Pre-existing behavior (unknown tool name) must be untouched by this fix."""
    with tempfile.TemporaryDirectory() as td:
        belt = _belt_with_root(Path(td))
        try:
            belt.dispatch("not_a_real_tool", {})
            assert False, "expected ToolError"
        except ToolError as e:
            assert "unknown tool" in str(e)


# ---- Sprint 00076 -- ToolBelt.edit_file() ----
# 2026-07-20, live incident: DeepSeek tried to add one row to a ~15KB DECISIONS.md via
# write_file (the only write tool that existed). write_file always replaces a file's *entire*
# content, so the model had to regenerate all ~15KB in one response; its own output budget
# (shared with its extended-thinking trace) ran out mid-regeneration, and the truncated result
# silently overwrote the good file -- the tool reported {"applied": true} but the change never
# actually landed. edit_file closes this gap: change an exact snippet without touching the rest
# of the file, for any backend (local or API), so no agent needs to reproduce a whole file just
# to change part of it.

def test_edit_file_happy_path_replaces_unique_match():
    with tempfile.TemporaryDirectory() as td:
        belt = _belt_with_root(Path(td))
        result = belt.dispatch("edit_file", {"root": "proj", "path": "a.txt",
                                              "old_string": "hello", "new_string": "goodbye"})
        assert result["applied"] is True
        assert "journal" in result and result["replacements"] == 1
        assert belt.dispatch("read_file", {"root": "proj", "path": "a.txt"}) == "goodbye world\n"


def test_edit_file_not_found_raises_tool_error_and_leaves_file_untouched():
    with tempfile.TemporaryDirectory() as td:
        belt = _belt_with_root(Path(td))
        try:
            belt.dispatch("edit_file", {"root": "proj", "path": "a.txt",
                                        "old_string": "not in the file", "new_string": "x"})
            assert False, "expected ToolError"
        except ToolError as e:
            assert "not found" in str(e)
        assert belt.dispatch("read_file", {"root": "proj", "path": "a.txt"}) == "hello world\n"


def test_edit_file_ambiguous_match_refuses_without_replace_all():
    with tempfile.TemporaryDirectory() as td:
        belt = _belt_with_root(Path(td))
        belt.dispatch("write_file", {"root": "proj", "path": "dup.txt", "content": "x x x\n"})
        try:
            belt.dispatch("edit_file", {"root": "proj", "path": "dup.txt",
                                        "old_string": "x", "new_string": "y"})
            assert False, "expected ToolError"
        except ToolError as e:
            assert "not unique" in str(e) and "3" in str(e)
        assert belt.dispatch("read_file", {"root": "proj", "path": "dup.txt"}) == "x x x\n"


def test_edit_file_replace_all_replaces_every_match():
    with tempfile.TemporaryDirectory() as td:
        belt = _belt_with_root(Path(td))
        belt.dispatch("write_file", {"root": "proj", "path": "dup.txt", "content": "x x x\n"})
        result = belt.dispatch("edit_file", {"root": "proj", "path": "dup.txt",
                                              "old_string": "x", "new_string": "y",
                                              "replace_all": True})
        assert result["replacements"] == 3
        assert belt.dispatch("read_file", {"root": "proj", "path": "dup.txt"}) == "y y y\n"


def test_edit_file_empty_old_string_raises_tool_error():
    with tempfile.TemporaryDirectory() as td:
        belt = _belt_with_root(Path(td))
        try:
            belt.dispatch("edit_file", {"root": "proj", "path": "a.txt",
                                        "old_string": "", "new_string": "x"})
            assert False, "expected ToolError"
        except ToolError as e:
            assert "empty" in str(e)


def test_edit_file_deny_root_refuses_and_leaves_file_untouched():
    with tempfile.TemporaryDirectory() as td:
        root_dir = Path(td) / "p"; root_dir.mkdir()
        (root_dir / "a.txt").write_text("hello world\n", encoding="utf-8")
        belt = ToolBelt(Path(td) / "j")
        belt.register_root("p", root_dir, "deny")
        try:
            belt.dispatch("edit_file", {"root": "p", "path": "a.txt",
                                        "old_string": "hello", "new_string": "goodbye"})
            assert False, "expected ToolError"
        except ToolError as e:
            assert "read-only" in str(e)
        assert (root_dir / "a.txt").read_text() == "hello world\n"


def test_edit_file_reserved_aether_path_refuses():
    with tempfile.TemporaryDirectory() as td:
        belt = _belt_with_root(Path(td))
        (Path(td) / "proj" / ".aether").mkdir()
        (Path(td) / "proj" / ".aether" / "audit.txt").write_text("secret\n", encoding="utf-8")
        try:
            belt.dispatch("edit_file", {"root": "proj", "path": ".aether/audit.txt",
                                        "old_string": "secret", "new_string": "leaked"})
            assert False, "expected ToolError"
        except ToolError as e:
            assert ".aether" in str(e)


def test_edit_file_not_a_file_raises_tool_error():
    with tempfile.TemporaryDirectory() as td:
        belt = _belt_with_root(Path(td))
        (Path(td) / "proj" / "adir").mkdir()
        try:
            belt.dispatch("edit_file", {"root": "proj", "path": "adir",
                                        "old_string": "x", "new_string": "y"})
            assert False, "expected ToolError"
        except ToolError as e:
            assert "not a file" in str(e)
        try:
            belt.dispatch("edit_file", {"root": "proj", "path": "nope.txt",
                                        "old_string": "x", "new_string": "y"})
            assert False, "expected ToolError"
        except ToolError as e:
            assert "not a file" in str(e)


def test_edit_file_undo_restores_exact_prior_content():
    with tempfile.TemporaryDirectory() as td:
        belt = _belt_with_root(Path(td))
        result = belt.dispatch("edit_file", {"root": "proj", "path": "a.txt",
                                              "old_string": "hello", "new_string": "goodbye"})
        assert belt.dispatch("read_file", {"root": "proj", "path": "a.txt"}) == "goodbye world\n"
        belt.undo(result["journal"])
        assert belt.dispatch("read_file", {"root": "proj", "path": "a.txt"}) == "hello world\n"


def test_same_millisecond_writes_have_unique_ids_and_undo_exact_versions():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        belt = _belt_with_root(tmp)
        frozen_time = 1_700_000_000.123
        expected_prefix = f"{int(frozen_time * 1000):x}-"

        with patch("aether_bridge.toolbelt.time.time", return_value=frozen_time):
            first = belt.write_file("proj", "a.txt", "version one\n")
            second = belt.write_file("proj", "a.txt", "version two\n")

        first_id = first["journal"]
        second_id = second["journal"]
        assert first_id.startswith(expected_prefix)
        assert second_id.startswith(expected_prefix)
        assert first_id != second_id

        journal = tmp / "journal"
        assert (journal / f"{first_id}.prior").read_text(encoding="utf-8") == "hello world\n"
        assert (journal / f"{second_id}.prior").read_text(encoding="utf-8") == "version one\n"

        belt.undo(second_id)
        assert belt.read_file("proj", "a.txt") == "version one\n"
        belt.undo(first_id)
        assert belt.read_file("proj", "a.txt") == "hello world\n"


# ---- Sprint 00077 -- whole-file replacement truncation tripwire ------------------------------

def test_write_file_large_replacement_shrink_is_flagged_with_both_sizes():
    with tempfile.TemporaryDirectory() as td:
        belt = _belt_with_root(Path(td))
        large = "é" * 1_500  # 3,000 UTF-8 bytes: threshold is intentionally byte-based.
        belt.dispatch("write_file", {"root": "proj", "path": "large.txt", "content": large})
        result = belt.dispatch(
            "write_file", {"root": "proj", "path": "large.txt", "content": "short"}
        )
        assert result["truncation_flagged"] is True
        records = [
            json.loads(line)
            for line in (Path(td) / "journal" / "journal.jsonl").read_text().splitlines()
        ]
        record = next(item for item in records if item["id"] == result["journal"])
        assert record["flagged"] is True
        assert record["flag"] == "possible_truncation"
        assert record["prior_bytes"] == 3_000
        assert record["new_bytes"] == 5
        assert "3000 bytes to 5 bytes" in record["detail"]


def test_write_file_small_or_new_file_is_not_truncation_flagged():
    with tempfile.TemporaryDirectory() as td:
        belt = _belt_with_root(Path(td))
        new = belt.dispatch(
            "write_file", {"root": "proj", "path": "new.txt", "content": "new"}
        )
        small = belt.dispatch(
            "write_file", {"root": "proj", "path": "a.txt", "content": "x"}
        )
        assert new["truncation_flagged"] is False
        assert small["truncation_flagged"] is False


if __name__ == "__main__":
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"{len(tests)}/{len(tests)} green")
