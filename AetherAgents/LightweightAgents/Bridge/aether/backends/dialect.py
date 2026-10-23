"""Pure vendor-dialect normalization for model response messages.

The adapter deliberately has no I/O, clock, or network dependency.  Logged
responses can therefore be replayed through :func:`normalize` byte-for-byte.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Callable


QUARANTINE_MARKER = "[unparsed backend markup — see transcript]"
_PREFIX = "｜｜DSML｜｜"
_TOOL_CALLS_OPEN = f"<{_PREFIX}tool_calls>"
_TOOL_CALLS_CLOSE = f"</{_PREFIX}tool_calls>"
_INVOKE_CLOSE = f"</{_PREFIX}invoke>"
_PARAMETER_CLOSE = f"</{_PREFIX}parameter>"
_FENCE_LINE_RE = re.compile(
    r"(?m)^[ \t]{0,3}(?P<fence>`{3,}|~{3,})(?P<rest>[^\r\n]*)"
)
_INLINE_TICKS_RE = re.compile(r"`+")
_VENDOR_WRAPPER_RE = re.compile(
    r"<(?P<signature>｜{1,2}[A-Za-z0-9_-]+｜{1,2})tool_calls>"
)


@dataclass(frozen=True)
class ParseResult:
    outcome: str
    content: str
    tool_calls: tuple[dict, ...] = ()
    reason: str = ""


@dataclass(frozen=True)
class Dialect:
    name: str
    detect: Callable[[str], bool]
    parse: Callable[[str], ParseResult]
    max_tokens_default: int


def _mask_content(content: str) -> tuple[str, bool]:
    """Mask fenced/inline-code spans and report an unterminated tail.

    A line-start fence closes only with the same marker character and a run
    at least as long as its opener. Unterminated spans mask their remaining
    tail so quoted calls cannot execute; ``normalize`` separately checks the
    raw text and quarantines if that masking hid a complete vendor wrapper.
    """
    chars = list(content)
    unterminated = False

    def blank(start: int, end: int) -> None:
        for index in range(start, end):
            if chars[index] not in "\r\n":
                chars[index] = " "

    open_fence: tuple[int, str, int] | None = None
    for match in _FENCE_LINE_RE.finditer(content):
        marker = match.group("fence")
        if open_fence is None:
            open_fence = (match.start(), marker[0], len(marker))
            continue
        start, marker_char, marker_length = open_fence
        if (
            marker[0] == marker_char
            and len(marker) >= marker_length
            and not match.group("rest").strip()
        ):
            end = match.end()
            blank(start, end)
            open_fence = None
    if open_fence is not None:
        start, _, _ = open_fence
        blank(start, len(content))
        unterminated = True

    # Inline code spans are paired within one line and by equal run length.
    # An unmatched run masks its tail and is reported for fail-closed handling.
    line_start = 0
    for line in content.splitlines(keepends=True):
        line_end = line_start + len(line)
        visible_line = "".join(chars[line_start:line_end])
        cursor = 0
        while True:
            opener = _INLINE_TICKS_RE.search(visible_line, cursor)
            if opener is None:
                break
            ticks = opener.group(0)
            closer = re.search(
                rf"(?<!`){re.escape(ticks)}(?!`)",
                visible_line[opener.end():],
            )
            if closer is None:
                blank(line_start + opener.start(), len(content))
                unterminated = True
                return "".join(chars), unterminated
            close_end = opener.end() + closer.end()
            blank(line_start + opener.start(), line_start + close_end)
            visible_line = "".join(chars[line_start:line_end])
            cursor = close_end
        line_start = line_end
    return "".join(chars), unterminated


def _masked(content: str) -> str:
    return _mask_content(content)[0]


def _dsml_present_masked(masked: str) -> bool:
    start = masked.find(_TOOL_CALLS_OPEN)
    if start < 0:
        return False
    return masked.find(_TOOL_CALLS_CLOSE, start + len(_TOOL_CALLS_OPEN)) >= 0


def _dsml_present(content: str) -> bool:
    return _dsml_present_masked(_masked(content))


def _unknown_vendor_present(masked: str) -> bool:
    for match in _VENDOR_WRAPPER_RE.finditer(masked):
        signature = match.group("signature")
        if masked.find(f"</{signature}tool_calls>", match.end()) >= 0:
            return True
    return False


_ATTRIBUTE_RE = re.compile(r'([A-Za-z_][A-Za-z0-9_-]*)="([^"]*)"')


def _attributes(tag: str, tag_name: str) -> dict[str, str] | None:
    prefix = f"<{_PREFIX}{tag_name}"
    if not tag.startswith(prefix) or not tag.endswith(">"):
        return None
    body = tag[len(prefix):-1]
    attributes: dict[str, str] = {}
    cursor = 0
    for match in _ATTRIBUTE_RE.finditer(body):
        if body[cursor:match.start()].strip():
            return None
        key, value = match.groups()
        if key in attributes:
            return None
        attributes[key] = value
        cursor = match.end()
    if body[cursor:].strip():
        return None
    return attributes


def _parameter_value(raw: str, string_hint: str) -> object:
    if string_hint == "true":
        return raw
    if string_hint != "false":
        raise ValueError("parameter string attribute must be true or false")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("non-string parameter is not valid JSON") from exc


def _parse_block(block: str, first_call_index: int) -> tuple[list[dict], str | None]:
    calls: list[dict] = []
    cursor = 0
    while cursor < len(block):
        while cursor < len(block) and block[cursor].isspace():
            cursor += 1
        if cursor == len(block):
            break
        tag_end = block.find(">", cursor)
        if tag_end < 0:
            return [], "unterminated invoke tag"
        invoke_tag = block[cursor:tag_end + 1]
        invoke_attrs = _attributes(invoke_tag, "invoke")
        if invoke_attrs is None or set(invoke_attrs) != {"name"} or not invoke_attrs["name"]:
            return [], "malformed invoke tag"
        invoke_end = block.find(_INVOKE_CLOSE, tag_end + 1)
        if invoke_end < 0:
            return [], "invoke block has no closing tag"

        params_text = block[tag_end + 1:invoke_end]
        params: dict[str, object] = {}
        param_cursor = 0
        while param_cursor < len(params_text):
            while param_cursor < len(params_text) and params_text[param_cursor].isspace():
                param_cursor += 1
            if param_cursor == len(params_text):
                break
            param_tag_end = params_text.find(">", param_cursor)
            if param_tag_end < 0:
                return [], "unterminated parameter tag"
            param_tag = params_text[param_cursor:param_tag_end + 1]
            param_attrs = _attributes(param_tag, "parameter")
            if (
                param_attrs is None
                or set(param_attrs) != {"name", "string"}
                or not param_attrs["name"]
                or param_attrs["name"] in params
            ):
                return [], "malformed or duplicate parameter"
            param_end = params_text.find(_PARAMETER_CLOSE, param_tag_end + 1)
            if param_end < 0:
                return [], "parameter has no closing tag"
            raw_value = params_text[param_tag_end + 1:param_end]
            try:
                params[param_attrs["name"]] = _parameter_value(raw_value, param_attrs["string"])
            except ValueError as exc:
                return [], str(exc)
            param_cursor = param_end + len(_PARAMETER_CLOSE)

        if not params:
            return [], "invoke block contains no parameters"
        index = first_call_index + len(calls)
        calls.append({
            "id": f"dsml_{index}",
            "type": "function",
            "function": {
                "name": invoke_attrs["name"],
                "arguments": json.dumps(params, ensure_ascii=False, separators=(",", ":")),
            },
        })
        cursor = invoke_end + len(_INVOKE_CLOSE)
    if not calls:
        return [], "tool_calls block contains no invoke blocks"
    return calls, None


def _dsml_parse(content: str, masked: str | None = None) -> ParseResult:
    masked = masked if masked is not None else _masked(content)
    calls: list[dict] = []
    clean_parts: list[str] = []
    cursor = 0
    while True:
        start = masked.find(_TOOL_CALLS_OPEN, cursor)
        if start < 0:
            clean_parts.append(content[cursor:])
            break
        end = masked.find(_TOOL_CALLS_CLOSE, start + len(_TOOL_CALLS_OPEN))
        if end < 0:
            return ParseResult("quarantined", QUARANTINE_MARKER, reason="unclosed tool_calls block")
        clean_parts.append(content[cursor:start])
        body_start = start + len(_TOOL_CALLS_OPEN)
        block_calls, reason = _parse_block(content[body_start:end], len(calls))
        if reason:
            return ParseResult("quarantined", QUARANTINE_MARKER, reason=reason)
        calls.extend(block_calls)
        cursor = end + len(_TOOL_CALLS_CLOSE)
    cleaned = "".join(clean_parts)
    return ParseResult("parsed", cleaned, tuple(calls))


def _openai_parse(content: str) -> ParseResult:
    return ParseResult("clean", content)


def _openai_detect(content: str) -> bool:
    return False


OPENAI_ONLY = Dialect(
    "openai", detect=_openai_detect, parse=_openai_parse, max_tokens_default=8192
)
DEEPSEEK = Dialect(
    "deepseek", detect=_dsml_present, parse=_dsml_parse, max_tokens_default=384_000
)
CONTINUATION_MARKER = "[…continued]"


def normalize(msg: dict, dialect: Dialect) -> dict:
    """Return a normalized copy when markup is present; never mutate ``msg``."""
    content = msg.get("content")
    if not isinstance(content, str):
        return msg
    masked, unterminated = _mask_content(content)
    if dialect == OPENAI_ONLY:
        return msg
    if dialect == DEEPSEEK:
        detected = _dsml_present_masked(masked)
    else:
        detected = dialect.detect(content)
    if not detected:
        hidden_complete_wrapper = (
            unterminated
            and (
                _dsml_present_masked(content)
                or _unknown_vendor_present(content)
            )
        )
        if hidden_complete_wrapper:
            result = ParseResult(
                "quarantined",
                QUARANTINE_MARKER,
                reason="complete vendor markup hidden by unterminated quoted span",
            )
        elif not _unknown_vendor_present(masked):
            return msg
        else:
            result = ParseResult(
                "quarantined", QUARANTINE_MARKER,
                reason="unregistered or incomplete vendor markup"
            )
    elif dialect == DEEPSEEK:
        result = _dsml_parse(content, masked)
    else:
        result = dialect.parse(content)

    normalized = dict(msg)
    if result.outcome == "parsed":
        normalized["content"] = result.content
        normalized["tool_calls"] = [
            *(msg.get("tool_calls") or []),
            *result.tool_calls,
        ]
        normalized["_dialect_outcome"] = "parsed"
        return normalized

    normalized["content"] = QUARANTINE_MARKER
    normalized["tool_calls"] = []
    normalized["_dialect_outcome"] = "quarantined"
    normalized["_dialect_error"] = result.reason or "dialect parse failed"
    normalized["_dialect_raw"] = content
    return normalized


def normalize_continuation(first: str, continued: dict, dialect: Dialect) -> dict:
    """Normalize one visible continuation join without inventing a cross-seam call.

    Each API response is normalized independently before this join in production.
    Re-checking the halves here also keeps scripted/test clients honest. If vendor
    markup becomes parseable only after the Bridge inserts its continuation seam,
    that call did not exist in either model response and must fail closed.

    The cross-seam check compares only what this join freshly produced with what
    each half freshly produced. Dialect telemetry inherited from ``continued`` is
    withheld from that comparison, then restored only when the join is clean.
    """
    second = continued.get("content") or ""
    joined = dict(continued)
    inherited = {}
    for key in ("_dialect_outcome", "_dialect_error", "_dialect_raw"):
        if key in joined:
            inherited[key] = joined.pop(key)
    joined["content"] = f"{first}\n\n{CONTINUATION_MARKER}\n\n{second}"
    joined["_continued"] = True
    normalized = normalize(joined, dialect)

    fresh_outcome = normalized.get("_dialect_outcome")
    if fresh_outcome is None:
        if inherited:
            normalized = dict(normalized)
            normalized.update(inherited)
        return normalized
    if fresh_outcome != "parsed":
        return normalized
    first_outcome = normalize({"content": first}, dialect).get("_dialect_outcome")
    second_outcome = normalize({"content": second}, dialect).get("_dialect_outcome")
    if first_outcome == "parsed" or second_outcome == "parsed":
        return normalized

    quarantined = dict(normalized)
    quarantined["content"] = QUARANTINE_MARKER
    quarantined["tool_calls"] = []
    quarantined["_dialect_outcome"] = "quarantined"
    quarantined["_dialect_error"] = (
        "vendor tool wrapper straddles automatic continuation boundary"
    )
    quarantined["_dialect_raw"] = joined["content"]
    return quarantined
