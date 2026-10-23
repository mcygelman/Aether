"""BRIEFCASE-1 parser + verifier.

Port of wrapper/src/core/briefcase.rs (S00051). The parser is load-bearing and
FAILS CLOSED: any structural irregularity is an error, never a silent partial
parse. The hashed body span is the bytes between '=== BEGIN SECTION <label> ===\\n'
and 'SECTION_SEAL: <seal>\\n' (Sprint 48 oracle, parity-proven).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from aether.integrity import canon


class BriefcaseError(Exception):
    pass


@dataclass
class Header:
    format: str = ""
    agent: str = ""
    parent_core: str = ""
    task: str = ""
    sprint: str = ""
    part: str = ""
    encoding: str = ""
    assembled: str = ""
    operator_signed: str = ""
    assembly_seal: str = ""
    est_tokens: int = 0


@dataclass
class Section:
    label: str          # e.g. "01 IDENTITY"
    body: str           # exact body for hashing (ends in one LF)
    seal: str           # 6 lowercase hex
    manifest_sha: str   # 64 hex from the manifest line


@dataclass
class Briefcase:
    header: Header
    sections: list[Section]
    raw: str            # original text, retained for verbatim delivery (ATIS P2)


@dataclass
class VerifyReport:
    ok: bool
    failures: list[tuple[str, str]] = field(default_factory=list)


_MAN_RE = re.compile(r"^  (\S.*?)\s+sha256:([0-9a-f]{64})$", re.M)
_SEC_RE = re.compile(
    r"=== BEGIN SECTION (.+?) ===\n(.*?)SECTION_SEAL: ([0-9a-f]{6})\n=== END SECTION (\d+) ===",
    re.S,
)
_ASSEMBLY_SEAL_RE = re.compile(r"^ASSEMBLY_SEAL: sha256:([0-9a-f]{64})$", re.M)


def _header_field(text: str, key: str) -> str | None:
    m = re.search(rf"^{re.escape(key)}: (.+)$", text, re.M)
    return m.group(1).strip() if m else None


def _header_field_allow_empty(text: str, key: str) -> str | None:
    m = re.search(rf"^{re.escape(key)}:[ \t]*(.*)$", text, re.M)
    return m.group(1).strip() if m else None


def assembly_sha(raw: str) -> str:
    """Hash the canonical briefcase while excluding only the self-referential seal line."""
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    matches = list(_ASSEMBLY_SEAL_RE.finditer(text))
    if len(matches) != 1:
        raise BriefcaseError(
            f"expected exactly one valid ASSEMBLY_SEAL header, found {len(matches)}"
        )
    material = _ASSEMBLY_SEAL_RE.sub("", text, count=1)
    return canon.canon_sha(material)


def parse(raw: str) -> Briefcase:
    """Parse a BRIEFCASE-1 document. Fails closed on any structural problem."""
    text = raw.replace("\r\n", "\n").replace("\r", "\n")

    if not text.startswith("=== AETHER BRIEFCASE ==="):
        raise BriefcaseError("missing '=== AETHER BRIEFCASE ===' opening")
    if "\nFORMAT: BRIEFCASE-1" not in text:
        raise BriefcaseError("missing 'FORMAT: BRIEFCASE-1'")

    man_end = text.find("=== END MANIFEST ===")
    if man_end < 0:
        raise BriefcaseError("missing '=== END MANIFEST ==='")
    manifest = text[:man_end]

    man = [(m.group(1).strip(), m.group(2)) for m in _MAN_RE.finditer(manifest)]
    if not man:
        raise BriefcaseError("empty or unparseable manifest")

    sections = [
        Section(label=m.group(1).strip(), body=m.group(2), seal=m.group(3), manifest_sha="")
        for m in _SEC_RE.finditer(text)
    ]
    if not sections:
        raise BriefcaseError("no sections parsed (fail closed)")

    man_labels = [l for l, _ in man]
    sec_labels = [s.label for s in sections]
    if man_labels != sec_labels:
        raise BriefcaseError(
            f"manifest/section label or order mismatch:\n  manifest={man_labels}\n  sections={sec_labels}"
        )
    if len(set(sec_labels)) != len(sec_labels):
        raise BriefcaseError("duplicate section label")
    seals = [s.seal for s in sections]
    if len(set(seals)) != len(seals):
        raise BriefcaseError("duplicate section seal")

    man_hash = dict(man)
    for s in sections:
        s.manifest_sha = man_hash[s.label]

    agent = _header_field(text, "AGENT")
    if agent is None:
        raise BriefcaseError("missing AGENT header")
    est_s = _header_field(text, "EST_TOKENS")
    if est_s is None:
        raise BriefcaseError("missing EST_TOKENS header")
    try:
        est_tokens = int(est_s)
    except ValueError:
        raise BriefcaseError(f"EST_TOKENS not numeric: '{est_s}'")

    operator_signed = _header_field_allow_empty(text, "OPERATOR_SIGNED")
    assembly_seals = _ASSEMBLY_SEAL_RE.findall(text)
    if len(assembly_seals) > 1:
        raise BriefcaseError("duplicate ASSEMBLY_SEAL header")
    if "ASSEMBLY_SEAL:" in manifest and not assembly_seals:
        raise BriefcaseError("malformed ASSEMBLY_SEAL header")

    header = Header(
        format=_header_field(text, "FORMAT") or "",
        agent=agent,
        parent_core=_header_field(text, "PARENT_CORE") or "",
        task=_header_field(text, "TASK") or "",
        sprint=_header_field(text, "SPRINT") or "",
        part=_header_field(text, "PART") or "",
        encoding=_header_field(text, "PAYLOAD_ENCODING") or "",
        assembled=_header_field(text, "ASSEMBLED") or "",
        operator_signed=operator_signed or "",
        assembly_seal=assembly_seals[0] if assembly_seals else "",
        est_tokens=est_tokens,
    )
    return Briefcase(header=header, sections=sections, raw=raw)


def verify(bc: Briefcase) -> VerifyReport:
    """Recompute each section's canonical SHA-256 vs the manifest. One altered
    body byte changes the hash and yields a named failure."""
    failures = []
    for s in bc.sections:
        computed = canon.canon_sha(s.body)
        if computed != s.manifest_sha:
            failures.append((s.label, f"expected {s.manifest_sha} got {computed}"))
    operator_header_present = bool(
        re.search(r"^OPERATOR_SIGNED:[ \t]*(.*)$", bc.raw.replace("\r\n", "\n"), re.M)
    )
    if operator_header_present and not bc.header.assembly_seal:
        failures.append(("ASSEMBLY_SEAL", "OPERATOR_SIGNED is not covered by an assembly seal"))
    elif bc.header.assembly_seal:
        try:
            computed = assembly_sha(bc.raw)
        except BriefcaseError as exc:
            failures.append(("ASSEMBLY_SEAL", str(exc)))
        else:
            if computed != bc.header.assembly_seal:
                failures.append(
                    (
                        "ASSEMBLY_SEAL",
                        f"expected {bc.header.assembly_seal} got {computed}",
                    )
                )
    return VerifyReport(ok=not failures, failures=failures)


def parse_file(path: str | Path) -> Briefcase:
    return parse(canon.decode_utf8(Path(path).read_bytes()))
