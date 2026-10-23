"""Importer box: markdown agent profile -> cartridge (read-only on the source)."""
import hashlib
import os

import pytest

from memory_fabric import MemoryEngine
from memory_fabric import importer
from memory_fabric.__main__ import main
from memory_fabric.clock import days_between

INDEX = """# Experience Index — Test

| ID | Date | Title | Significance | Summary |
|----|------|-------|-------------|---------|
| EXP_20260725_S00076_Scoped_Edits | 2026-07-25 | S00076 Scoped Edits — `edit_file` lands | HIGH | Hotfix sprint: edit_file added to ToolBelt. |
| EXP_20260805_HDEC021_Window | 2026-08-05 | HDEC-021 accepted + window correction | CRITICAL | Configured 124K is not vendor 1M. |
"""

FILES = {
    "index/experience_index.md": INDEX,
    "archive/experiences/EXP_20260725_S00076_Scoped_Edits.md":
        "# EXP_20260725_S00076_Scoped_Edits\n\n**Date:** 2026-07-25\n**Significance:** HIGH\n\n"
        "write_file truncated DECISIONS.md; edit_file fixed it.\n",
    "archive/experiences/EXP_20260805_HDEC021_Window.md":
        "# EXP_20260805_HDEC021_Window\n\n**Significance:** CRITICAL\n\nI PASSed 124000 as capacity. "
        "See INT_20260805_Configured_Is_Not_Capability.\n",
    "archive/introspections/INT_20260805_Configured_Is_Not_Capability.md":
        "# INT_20260805_Configured_Is_Not_Capability\n\n**Date:** 2026-08-05\n"
        "**Triggers:** SG-2 window finding; Codex correction\n\nA number I can change is a decision.\n",
    "index/insights/INS_20260805_Configured_Is_Not_Capability.md":
        "# INS_20260805_Configured_Is_Not_Capability\n\n**Date:** 2026-08-05\n"
        "**Cross-experience pattern drawn from:** EXP_20260805 (SG-2 window finding), EXP_20260725 "
        "(vendor seam).\n\n## The rule\n\nVerify ceilings, don't inherit them.\n",
}


@pytest.fixture
def profile(tmp_path):
    memory = tmp_path / "Solace" / "memory"
    for rel, text in FILES.items():
        path = memory / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    (memory / "archive" / "experiences" / "EXP_20260901_Sealed.md").write_bytes(
        b"SEALBOX-1\nabc\ndef\nghi\n")
    (memory / "archive" / "experiences" / ".sealbox_key").write_text("SECRET")
    (memory / "archive" / "experiences" / "photo.png").write_bytes(b"\x89PNG\r\n")
    (memory / "archive" / "experiences" / "EXPERIENCE_TEMPLATE.txt").write_text("fill me in")
    (memory / "archive" / "introspections" / "INT_20260909_A_Template_Is_Not_A_Runtime.txt").write_text(
        "INT_20260909 A template is not a runtime.\n")
    (memory / "archive" / "experiences" / "EXP_20260902_Binary.md").write_bytes(b"\xff\xfe\x00bad")
    return str(memory)


def _tree_digest(root):
    h = hashlib.sha256()
    for folder, _, names in sorted(os.walk(root)):
        for name in sorted(names):
            path = os.path.join(folder, name)
            h.update(path.encode()); h.update(open(path, "rb").read())
            h.update(str(os.stat(path).st_mtime_ns).encode())
    return h.hexdigest()


def test_plan_maps_records_and_skips_unsafe_files(profile, monkeypatch):
    opened = []
    real_open = open

    def spy(path, *a, **k):
        opened.append(str(path))
        return real_open(path, *a, **k)
    monkeypatch.setattr("builtins.open", spy)
    plan = importer.plan_import(profile)
    monkeypatch.undo()
    assert not any(p.endswith(".sealbox_key") for p in opened)          # keys never opened
    summary = plan.summary()
    assert summary["experience"] == 2 and summary["introspection"] == 2 and summary["insight"] == 1
    assert "INT_20260909_A_Template_Is_Not_A_Runtime" in {r.record_id for r in plan.records}
    reasons = dict(plan.skipped)
    assert "sealed" in reasons["archive/experiences/EXP_20260901_Sealed.md"]
    assert "key file" in reasons["archive/experiences/.sealbox_key"]
    assert "not a text record" in reasons["archive/experiences/photo.png"]
    assert "not UTF-8" in reasons["archive/experiences/EXP_20260902_Binary.md"]
    assert "template" in reasons["archive/experiences/EXPERIENCE_TEMPLATE.txt"]
    by_id = {r.record_id: r for r in plan.records}
    exp = by_id["EXP_20260805_HDEC021_Window"]
    assert exp.importance == 0.85 and exp.significance == "CRITICAL"
    assert exp.title.startswith("HDEC-021 accepted") and "hdec-021" in exp.tags
    assert by_id["EXP_20260725_S00076_Scoped_Edits"].importance == 0.80
    ins = by_id["INS_20260805_Configured_Is_Not_Capability"]
    assert ("INT_20260805_Configured_Is_Not_Capability", "derived") in ins.links
    assert ("EXP_20260725_S00076_Scoped_Edits", "derived") in ins.links
    assert ("INT_20260805_Configured_Is_Not_Capability", "context") in exp.links
    assert "sg-2" in by_id["INT_20260805_Configured_Is_Not_Capability"].tags


def test_apply_dates_grace_and_idempotence(profile, engine):
    before = _tree_digest(profile)
    plan, counts = engine.import_profile(profile)
    assert counts["written"] == 5 and counts["links"] >= 3
    assert _tree_digest(profile) == before                               # source untouched
    note = engine.note("EXP_20260725_S00076_Scoped_Edits")
    assert note["refreshed_at"].startswith("2026-07-25")                 # recency is real
    assert days_between(engine.cart.clock.now(), note["switch_at"]) == pytest.approx(30, abs=0.01)
    assert note["category"] == "Information" and note["stratum"] == "Experience"
    ins = engine.note("INS_20260805_Configured_Is_Not_Capability")
    assert ins["category"] == "Revelation" and ins["profile"] == "core_knowledge"
    assert ins["importance"] < engine.settings()["fog_importance_min"]      # not fog-visible
    # recall finds the lesson by a natural tag, and it carries its experiences along
    ids = engine.recall(["configured is not capability"]).ids()
    assert "INS_20260805_Configured_Is_Not_Capability" in ids
    _, again = engine.import_profile(profile)
    assert again["written"] == 0 and again["already_present"] == 5


def test_imported_history_fades_unless_used(profile, engine, clock):
    engine.import_profile(profile)
    clock.advance(120)
    used = "INS_20260805_Configured_Is_Not_Capability"
    exp = "EXP_20260725_S00076_Scoped_Edits"
    assert engine.note(exp)["strength"] < 0.15                           # untouched fact sank
    assert engine.note(used)["strength"] > 0.5                           # the distilled lesson stays
    intro = engine.note("INT_20260805_Configured_Is_Not_Capability")["strength"]
    assert 0.15 < intro < engine.note(used)["strength"]                  # reflection fades in between


def test_cli_import_profile(profile, tmp_path, capsys):
    out = str(tmp_path / "carts" / "solace.sqlite")
    os.makedirs(os.path.dirname(out))
    assert main(["import-profile", profile, "solace", out, "--dry-run"]) == 0
    assert not os.path.exists(out)
    assert main(["import-profile", profile, "solace", out, "--name", "Solace"]) == 0
    assert os.path.exists(out)
    assert main(["import-profile", profile, "solace", out]) == 2                     # no overwrite
    inside = os.path.join(profile, "mind.sqlite")
    assert main(["import-profile", profile, "solace", inside]) == 2                  # never in source
    assert not os.path.exists(inside)
    with MemoryEngine.open(out, read_only=True) as mind:
        assert mind.card()["name"] == "Solace" and mind.card()["memory"]["notes"] == 7
