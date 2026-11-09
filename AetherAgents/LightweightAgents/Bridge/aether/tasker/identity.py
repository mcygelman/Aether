"""The one-file lightweight identity: System 0, 1 and 2 in one Aether stream (contract §9, lane M3).

    ⟪SYSTEM_0_BEGIN⟫ … ⟪SYSTEM_0_END⟫   who the agent is; always taken from the Staff Home
    ⟪SYSTEM_1_BEGIN⟫ … ⟪SYSTEM_1_END⟫   the workspace; replaced in full at closure
    ⟪SYSTEM_2_BEGIN⟫ … ⟪SYSTEM_2_END⟫   memory; reflection entries added at the top of [REFLECTION_INDEX]

Each `[CHECKSUM_SHA256_<BLOCK>] := …` line in System 0 is the sha256 of the text between the previous checksum line
(or the System 0 header) and itself. They are written at seed time and only reported on, never enforced.
"""
from __future__ import annotations

import hashlib
import re
from typing import Optional

BEGIN = "⟪SYSTEM_{n}_BEGIN⟫"
END = "⟪SYSTEM_{n}_END⟫"
STREAM_BEGIN = "⟪AETHER_UNIFIED_STREAM⟫"
STREAM_END = "⟪END_AETHER_UNIFIED_STREAM⟫"
INDEX_OPEN = re.compile(r"^[ \t]*\[REFLECTION_INDEX\][ \t]*:=[ \t]*\[[ \t]*(//.*)?$", re.MULTILINE)
ENTRY = re.compile(r"⟪(MICRO_)?REFLECTION_INDEX_ENTRY_BEGIN⟫.*?⟪(MICRO_)?REFLECTION_INDEX_ENTRY_END⟫", re.DOTALL)
CHECKSUM = re.compile(r"^\[CHECKSUM_SHA256_([A-Z0-9_]+)\][ \t]*:=[ \t]*(\S+)[ \t]*$", re.MULTILINE)
UNVERIFIED = "⌜UNVERIFIED_RUNTIME⌟"
ASSERT = re.compile(r"\[ASSERT\][ \t]*⌜([A-Z_]+)⌝[ \t]*:=[ \t]*⌞(.*?)⌟", re.DOTALL)
POINTER = re.compile(r"⟪([A-Z_]+)::(.*?)⟫")
ENTRY_FIELDS_MICRO = ("ID", "DATE", "AGENT", "TOPIC", "SUMMARY", "LESSON", "NEXT_STEP")


class IdentityError(ValueError):
    """The text is not a one-file identity in the shape this needs; the message says what is missing."""


def _span(text: str, n: int) -> tuple:
    begin, end = BEGIN.format(n=n), END.format(n=n)
    if text.count(begin) != 1 or text.count(end) != 1:
        raise IdentityError(f"the identity needs exactly one {begin} and one {end}")
    start = text.index(begin) + len(begin)
    stop = text.index(end)
    if stop < start:
        raise IdentityError(f"{end} comes before {begin}")
    return start, stop


def system(text: str, n: int) -> str:
    """The text between System n's markers, exactly."""
    start, stop = _span(text, n)
    return text[start:stop]


def replace_system(text: str, n: int, inner: str) -> str:
    start, stop = _span(text, n)
    return text[:start] + inner + text[stop:]


def problems(text: str) -> list:
    """What keeps this text from being a well-formed identity; [] when it is one."""
    found = []
    for marker in (STREAM_BEGIN, STREAM_END):
        if marker not in text:
            found.append(f"missing {marker}")
    spans = []
    for n in (0, 1, 2):
        try:
            spans.append(_span(text, n))
        except IdentityError as exc:
            found.append(str(exc))
    if len(spans) == 3 and not (spans[0][1] < spans[1][0] and spans[1][1] < spans[2][0]):
        found.append("Systems 0, 1 and 2 must come in that order")
    if not found:
        if not INDEX_OPEN.search(system(text, 2)):
            found.append("System 2 needs a line '[REFLECTION_INDEX] := [' with entries below it")
        if "∆WMC{" not in system(text, 1) or "}∆WMC" not in system(text, 1):
            found.append("System 1 needs a ∆WMC{ … }∆WMC block")
    return found


def require(text: str) -> None:
    found = problems(text)
    if found:
        raise IdentityError("; ".join(found))


# ---- System 2: reflections -------------------------------------------------------------------------------------------

def reflection_entries(text: str) -> list:
    """Each reflection index entry in System 2, newest first, markers included."""
    return [m.group(0) for m in ENTRY.finditer(system(text, 2))]


def entry_problems(entry: str) -> list:
    entry = (entry or "").strip()
    if len(ENTRY.findall(entry)) > 1:
        return ["give one reflection entry, not several"]
    m = ENTRY.fullmatch(entry)
    if not m:
        return ["a reflection entry is one ⟪MICRO_REFLECTION_INDEX_ENTRY_BEGIN⟫ … ⟪MICRO_REFLECTION_INDEX_ENTRY_END⟫"
                " block (or the REFLECTION_INDEX_ENTRY form)"]
    if (m.group(1) or "") != (m.group(2) or ""):
        return ["the entry's BEGIN and END markers do not match"]
    fields = set(re.findall(r"^\s*\[([A-Z_]+)\]\s*:=", entry, re.MULTILINE))
    wanted = ENTRY_FIELDS_MICRO if m.group(1) else ("ID", "DATE", "AGENT", "TOPIC")
    missing = [f for f in wanted if f not in fields]
    return [f"the entry is missing [{', '.join(missing)}]"] if missing else []


def add_reflection(text: str, entry: str) -> str:
    """Put one entry at the top of [REFLECTION_INDEX], below the index line and its header comments, exactly as given
    (only a final newline is added when missing): what a mind files is never reworded or re-indented."""
    s2 = system(text, 2)
    m = INDEX_OPEN.search(s2)
    if not m:
        raise IdentityError("System 2 has no '[REFLECTION_INDEX] := [' line")
    lines = s2[m.end():].split("\n")
    at = m.end()
    # skip the rest of the index line, then the comment lines that head the index
    at += len(lines[0]) + 1
    for line in lines[1:]:
        if line.strip().startswith("//"):
            at += len(line) + 1
        else:
            break
    new_s2 = s2[:at] + entry + ("" if entry.endswith("\n") else "\n") + s2[at:]
    return replace_system(text, 2, new_s2)


# ---- System 0: asserts, pointers and checksums -----------------------------------------------------------------

def asserts(text: str) -> dict:
    return {k: v.strip() for k, v in ASSERT.findall(system(text, 0))}


def pointers(text: str) -> dict:
    """Dynamic pointers of System 0, by kind; a kind that repeats keeps a list."""
    out: dict = {}
    for kind, value in POINTER.findall(system(text, 0)):
        if kind in ("TYPE", "VERSION"):
            continue
        if kind in out:
            out[kind] = out[kind] if isinstance(out[kind], list) else [out[kind]]
            out[kind].append(value)
        else:
            out[kind] = value
    return out


def _checksum_blocks(s0: str) -> list:
    """(name, start of the line, end of the line, covered text) for each checksum line in System 0."""
    blocks = []
    header = re.search(r"⟪TYPE::[^⟫]*⟫[^\n]*\n(?:⟪VERSION::[^⟫]*⟫[^\n]*\n)?", s0)
    previous = header.end() if header else 0
    for m in CHECKSUM.finditer(s0):
        blocks.append((m.group(1), m.start(), m.end(), s0[previous:m.start()]))
        previous = m.end() + 1
    return blocks


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def seal_checksums(text: str) -> str:
    """Write the real sha256 into every System 0 checksum line (in place of ⌜UNVERIFIED_RUNTIME⌟)."""
    s0 = system(text, 0)
    out, cursor = [], 0
    for name, start, end, covered in _checksum_blocks(s0):
        out.append(s0[cursor:start])
        out.append(f"[CHECKSUM_SHA256_{name}] := ⌜{_sha(covered)}⌝")
        cursor = end
    out.append(s0[cursor:])
    return replace_system(text, 0, "".join(out))


def checksum_report(text: str) -> dict:
    """{block: 'ok' | 'changed' | 'unsealed'} for each System 0 checksum line. A report, never a refusal."""
    report = {}
    for m, (name, _s, _e, covered) in zip(CHECKSUM.finditer(system(text, 0)), _checksum_blocks(system(text, 0))):
        value = m.group(2)
        if value == UNVERIFIED:
            report[name] = "unsealed"
        else:
            report[name] = "ok" if value.strip("⌜⌝") == _sha(covered) else "changed"
    return report


def name_and_role(text: str, filename: Optional[str] = None) -> tuple:
    """(name, role) from CORE_IDENTITY, falling back to Identity_<Role>_<Name>.txt."""
    found = asserts(text)
    name, role = found.get("AGENT_NAME", ""), found.get("AGENT_ROLE", "")
    if (not name or not role) and filename:
        m = re.fullmatch(r"Identity_(.+)_([^_]+)\.txt", filename)
        if m:
            role, name = role or m.group(1), name or m.group(2)
    if not name:
        raise IdentityError("the identity has no ⌜AGENT_NAME⌝ assert")
    return name, role
