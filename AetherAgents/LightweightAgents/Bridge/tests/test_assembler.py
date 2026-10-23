"""S00068 BRIEFCASE-1 assembler tests."""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aether_bridge.ack import parse_ack, seal_gate  # noqa: E402
from aether_bridge.assembler import assemble, assemble_spec  # noqa: E402
from aether_bridge.briefcase import parse, parse_file, verify  # noqa: E402
from aether_bridge.canon import canon_sha  # noqa: E402

BRIDGE = Path(__file__).resolve().parents[1]
BRIEFCASES = BRIDGE.parents[0] / "Briefcases"


def active_path(name: str) -> Path:
    return BRIEFCASES / name / f"{name}_Briefcase.txt"


def test_roundtrip_active_briefcases_byte_stable():
    for name in ("Kirito", "Grace", "Linus"):
        path = active_path(name)
        bc = parse_file(path)
        raw = assemble(bc.header, bc.sections)
        assert raw == path.read_text(encoding="utf-8")
        reparsed = parse(raw)
        assert [s.manifest_sha for s in reparsed.sections] == [s.manifest_sha for s in bc.sections]
        assert [s.body for s in reparsed.sections] == [s.body for s in bc.sections]


def test_fixture_assemble_verify_and_seal_gate():
    spec = {
        "header": {
            "agent": "FIXTURE",
            "parent_core": "TestCore",
            "task": "{ASSIGNED_AT_DISPATCH}",
            "sprint": "00068",
            "part": "1 OF 1",
            "encoding": "AETHER_UNICODE",
            "assembled": "2026-07-04T00:00:00-04:00 BY TEST",
        },
        "sections": [
            {"label": "01 IDENTITY", "text": "Fixture identity.\n"},
            {"label": "02 TASK_PACKAGE", "text": "Fixture task.\n"},
        ],
    }
    with tempfile.TemporaryDirectory() as td:
        spec_path = Path(td) / "fixture.json"
        spec_path.write_text(json.dumps(spec), encoding="utf-8")
        raw = assemble_spec(spec_path)
    bc = parse(raw)
    assert verify(bc).ok
    assert "OPERATOR_SIGNED:" in raw
    assert "ASSEMBLY_SEAL: sha256:" in raw
    assert bc.header.operator_signed == ""
    ack = parse_ack("SEALS: " + " ".join(s.seal for s in bc.sections))
    assert seal_gate(bc, ack).passed


def test_operator_signature_is_covered_by_assembly_seal():
    spec = {
        "header": {
            "agent": "FIXTURE",
            "operator_signed": "Michel 2026-07-26",
        },
        "sections": [{"label": "01 IDENTITY", "text": "Fixture identity.\n"}],
    }
    with tempfile.TemporaryDirectory() as td:
        spec_path = Path(td) / "signed.json"
        spec_path.write_text(json.dumps(spec), encoding="utf-8")
        raw = assemble_spec(spec_path)
    signed = parse(raw)
    assert signed.header.operator_signed == "Michel 2026-07-26"
    assert verify(signed).ok
    tampered = parse(raw.replace("Michel 2026-07-26", "Mallory 2026-07-26", 1))
    report = verify(tampered)
    assert not report.ok
    assert report.failures[0][0] == "ASSEMBLY_SEAL"


def test_s00079_three_identity_delta_is_exactly_bounded():
    expected_unchanged = {
        "Kirito": "a1e2e8367a635fbd06ce00fef933298cfcbbf9154a6e94bbfdf9cfd2d8795e8d",
        "Grace": "e4f090c5ed10aca9119519c07d8f7f31f4eb21e0320c19e869360b55a953a902",
    }
    clause = (
        "INPUT VERIFICATION\n"
        "If the inputs a review depends on are absent, empty, or unread, say so and return BLOCKED.\n"
        "Never issue a verdict on inputs you did not read.\n"
        "A citation names a file you opened, not a path you were given.\n"
    )
    for name, expected_sha in expected_unchanged.items():
        bc = parse_file(active_path(name))
        identity = next(section for section in bc.sections if section.label == "01 IDENTITY")
        assert canon_sha(identity.body) == expected_sha
    linus = parse_file(active_path("Linus"))
    identity = next(section for section in linus.sections if section.label == "01 IDENTITY")
    assert identity.body.count(clause) == 1


def test_tampered_assembled_briefcase_fails_verify():
    bc = parse_file(active_path("Grace"))
    raw = assemble(bc.header, bc.sections)
    tampered = parse(raw.replace("Grace", "Mallory", 1))
    assert not verify(tampered).ok


def test_s00079_1_all_future_assembly_specs_use_canonical_crib_sheet():
    canonical = (
        BRIDGE.parents[0]
        / "Documentation"
        / "SupportDocs"
        / "Aether_Crib_Sheet.txt"
    ).read_text(encoding="utf-8").replace("\r\n", "\n")
    checked = 0
    for path in sorted((BRIDGE / "specs").glob("*.spec.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        for section in data.get("sections") or []:
            if str(section.get("label", "")).endswith("CRIB_SHEET"):
                assert section["text"] == canonical, path.name
                checked += 1
    assert checked == 10


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"PASS {fn.__name__}")
    print(f"{len(fns)}/{len(fns)} green")
