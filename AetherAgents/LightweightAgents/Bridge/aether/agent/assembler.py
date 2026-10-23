"""BRIEFCASE-1 assembler.

Spec format (JSON):

{
  "header": {
    "agent": "GRACE",
    "parent_core": "SoftwareDeveloper v1.1",
    "task": "{ASSIGNED_AT_DISPATCH}",
    "sprint": "00068",
    "part": "1 OF 1",
    "encoding": "AETHER_UNICODE",
    "assembled": "2026-07-04T00:00:00-04:00 BY BRIDGE"
  },
  "sections": [
    {"label": "01 IDENTITY", "path": "sections/01_identity.txt"},
    {"label": "02 CRIB_SHEET", "text": "inline body also works\n"}
  ]
}

Section bodies are hashed exactly as written after LF normalization; the
assembler ensures each body ends in one LF before `SECTION_SEAL`.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from aether.models import budget
from aether.integrity import canon
from aether.agent.briefcase import Briefcase, Header, Section, assembly_sha, parse_file, verify
from aether.knowledge.teachframe import TeachFrameError, validate as validate_teachframe

MANIFEST_LABEL_WIDTH = 46


def section_body(text: str) -> str:
    body = text.replace("\r\n", "\n").replace("\r", "\n")
    return body if body.endswith("\n") else body + "\n"


def section_from_spec(item: dict, base_dir: Path) -> Section:
    label = item["label"].strip()
    source_path: Path | None = None
    if "text" in item:
        body = section_body(str(item["text"]))
    elif "path" in item:
        source_path = base_dir / item["path"]
        body = section_body(source_path.read_text(encoding="utf-8"))
    else:
        raise ValueError(f"section '{label}' needs text or path")
    # L-01: canonical TeachFrame sources must carry their generated card, and
    # any inline section that opts into the new schema is held to the same
    # rule.  Legacy sealed inline snapshots without a CARD remain readable;
    # changing historical briefcases is not part of this migration.
    canonical_source = source_path is not None and source_path.name.startswith("TEACHFRAME_")
    has_card = "[ CARD ]" in body
    if canonical_source or has_card:
        try:
            validate_teachframe(body)
        except TeachFrameError as exc:
            raise ValueError(f"section '{label}' has invalid TeachFrame: {exc}") from exc
    sha = canon.canon_sha(body)
    return Section(label=label, body=body, seal=sha[:6], manifest_sha=sha)


def load_spec(path: str | Path) -> tuple[Header, list[Section]]:
    spec_path = Path(path)
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    header_data = spec.get("header", {})
    header = Header(
        format="BRIEFCASE-1",
        agent=header_data.get("agent", spec.get("agent", "")).upper(),
        parent_core=header_data.get("parent_core", ""),
        task=header_data.get("task", "{ASSIGNED_AT_DISPATCH}"),
        sprint=header_data.get("sprint", ""),
        part=header_data.get("part", "1 OF 1"),
        encoding=header_data.get("encoding", "AETHER_UNICODE"),
        assembled=header_data.get("assembled", ""),
        operator_signed=header_data.get("operator_signed", ""),
        est_tokens=0,
    )
    sections = [section_from_spec(item, spec_path.parent) for item in spec["sections"]]
    return header, sections


def spec_from_briefcase(bc: Briefcase) -> dict:
    return {
        "header": {
            "agent": bc.header.agent,
            "parent_core": bc.header.parent_core,
            "task": bc.header.task,
            "sprint": bc.header.sprint,
            "part": bc.header.part,
            "encoding": bc.header.encoding,
            "assembled": bc.header.assembled,
            "operator_signed": bc.header.operator_signed,
        },
        "sections": [{"label": s.label, "text": s.body} for s in bc.sections],
    }


def render(
    header: Header,
    sections: list[Section],
    est_tokens: int,
    assembly_seal: str,
) -> str:
    lines = [
        "=== AETHER BRIEFCASE ===",
        "FORMAT: BRIEFCASE-1",
        f"AGENT: {header.agent}",
        f"PARENT_CORE: {header.parent_core}",
        f"TASK: {header.task}",
        f"SPRINT: {header.sprint}",
        f"PART: {header.part}",
        f"PAYLOAD_ENCODING: {header.encoding}",
        f"ASSEMBLED: {header.assembled}",
        (
            f"OPERATOR_SIGNED: {header.operator_signed}"
            if header.operator_signed
            else "OPERATOR_SIGNED:"
        ),
        f"ASSEMBLY_SEAL: sha256:{assembly_seal}",
        f"EST_TOKENS: {est_tokens}",
        "SECTIONS:",
    ]
    for section in sections:
        lines.append(f"  {section.label:<{MANIFEST_LABEL_WIDTH}}sha256:{section.manifest_sha}")
    lines.extend(["=== END MANIFEST ===", ""])
    for section in sections:
        number = section.label.split(" ", 1)[0]
        lines.append(f"=== BEGIN SECTION {section.label} ===")
        lines.append(section.body.rstrip("\n"))
        lines.append(f"SECTION_SEAL: {section.seal}")
        lines.append(f"=== END SECTION {number} ===")
        lines.append("")
    return "\n".join(lines)


def assemble(header: Header, sections: list[Section]) -> str:
    est = 0
    while True:
        unsigned = render(header, sections, est, "0" * 64)
        raw = render(header, sections, est, assembly_sha(unsigned))
        next_est = budget.est_tokens(raw)
        if next_est == est:
            return raw
        est = next_est


def assemble_spec(path: str | Path) -> str:
    header, sections = load_spec(path)
    return assemble(header, sections)


def write_roundtrip_spec(source: str | Path, spec_out: str | Path) -> None:
    bc = parse_file(source)
    Path(spec_out).write_text(json.dumps(spec_from_briefcase(bc), ensure_ascii=False, indent=2), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="aether_bridge assemble")
    ap.add_argument("agent_spec", help="JSON assembler spec")
    ap.add_argument("--out", required=True, help="Output BRIEFCASE-1 file")
    args = ap.parse_args(argv)
    raw = assemble_spec(args.agent_spec)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="\n") as f:
        f.write(raw)
    bc = parse_file(out)
    rep = verify(bc)
    if not rep.ok:
        print(f"ASSEMBLE FAIL: {rep.failures}")
        return 1
    print(f"ASSEMBLE PASS: {out} agent={bc.header.agent} sections={len(bc.sections)} est_tokens={bc.header.est_tokens}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
