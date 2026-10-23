"""L-01 TeachFrame schema and generated-card tests."""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aether_bridge.assembler import assemble_spec  # noqa: E402
from aether_bridge.teachframe import (  # noqa: E402
    CARD_TOKEN_LIMIT,
    TeachFrameError,
    generate_card,
    metadata,
    validate,
    with_generated_card,
)

BRIDGE = Path(__file__).resolve().parents[1]
LW = BRIDGE.parent
CATALOG = LW / "TeachFrames" / "CATALOG.json"


def sample_frame() -> str:
    return """⟪TEACHFRAME_SAMPLE⟫
⟪TYPE::TEACHABLE_FRAME⟫
⟪VERSION::1.0⟫
⟪DATE::2026-07-28⟫
⟪CREATOR::Test Author⟫
⟪SCHOOL::School Of Testing⟫
⟪AUDIENCE::any agent checking a generated card⟫
⟪PREREQUISITES::NONE⟫
⟪LINEAGE::Test Author 2026-07-28⟫
⟪PREVENTS::two independently maintained renderings drifting apart⟫
⟪EVIDENCE::L-01 card-generation acceptance test⟫
⟪MEASURED_BY::assembler projection equality check⟫
⟪RETIRES_WHEN::the TeachFrame format is replaced by a signed successor⟫
⟪OBSERVED_BY::L-01 test fixture⟫
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[ TITLE ]
  Generated Cards Stay in Sync

[ PURPOSE ]
  Generate the compact lesson rendering from its canonical TeachFrame.

[ EXAMPLES ]
  A changed PREVENTS field produces a different card and refuses stale assembly.

[ SELF_CHECK ]
  Q: May the card be edited directly?
  A: No.

⟪END_TEACHFRAME⟫
"""


def test_generated_card_is_ascii_deterministic_and_within_budget():
    rendered = with_generated_card(sample_frame())
    report = validate(rendered)
    assert report.card_tokens <= CARD_TOKEN_LIMIT
    assert generate_card(rendered).isascii()
    assert with_generated_card(rendered) == rendered


def test_hand_edited_card_is_refused():
    rendered = with_generated_card(sample_frame())
    tampered = rendered.replace("two independently", "three independently", 1)
    with pytest.raises(TeachFrameError, match="hand-edited or stale"):
        validate(tampered)


def test_assembler_refuses_hand_edited_canonical_card():
    rendered = with_generated_card(sample_frame()).replace("LESSON::SAMPLE", "LESSON::HAND_EDIT", 1)
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        frame = root / "TEACHFRAME_SAMPLE.txt"
        frame.write_text(rendered, encoding="utf-8")
        spec = root / "spec.json"
        spec.write_text(
            json.dumps({"header": {"agent": "TEST"}, "sections": [{"label": "01 TEACHFRAME: Sample", "path": frame.name}]}),
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="hand-edited or stale"):
            assemble_spec(spec)


def test_catalog_paths_and_all_curriculum_frames_validate():
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    assert len(catalog["entries"]) == 14
    assert {entry["state"] for entry in catalog["entries"]} == {
        "CURRICULUM",
        "ARCHIVE",
        "CONSTITUTION",
    }
    counts = {state: sum(entry["state"] == state for entry in catalog["entries"]) for state in {
        "CURRICULUM", "ARCHIVE", "CONSTITUTION"
    }}
    assert counts == {
        "CURRICULUM": 7,
        "ARCHIVE": 5,
        "CONSTITUTION": 2,
    }
    for entry in catalog["entries"]:
        path = LW / entry["path"]
        assert path.is_file(), entry["name"]
        if entry["state"] in {"CURRICULUM", "ARCHIVE"}:
            text = path.read_text(encoding="utf-8")
            validate(text)
            if entry["state"] == "ARCHIVE":
                assert "Kairo(o3)" in metadata(text)["LINEAGE"]
