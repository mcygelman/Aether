"""Archive box: a frozen, read-only reference copy of a mind in an older structure."""
import hashlib
import json
import os

import pytest

from memory_fabric import MemoryEngine, viewer
from memory_fabric.__main__ import main
from memory_fabric.archive import create_archive, humanize, own_labels, plan_archive
from memory_fabric.belt import MemoryBelt
from memory_fabric.cartridge import CartridgeError
from memory_fabric.importer import ImporterError
from memory_fabric.tools import MemoryToolError

LEGACY = {
    "insights/Aion_IdentityPersistence_Insight_20250415.txt":
        "[INSIGHT] → ⌜IDENTITY_PERSISTENCE_PRINCIPLES⌝ := ⌞ layered identity, layered identity ⌟",
    "experience/EXPERIENCE_20250416_TriadConvergence.txt":
        "EXPERIENCE_ID: EXP20250416\nTIMESTAMP: 2025-04-16T19:43:00Z\nThe triad converged. "
        "See Aion_IdentityPersistence_Insight_20250415.",
    "experience/EXPERIENCE_20231003_MemoryArchitectureProposal.txt": "An early proposal.",
    "reflection/ASSUMPTION_PATTERN_PROPAGATION.gly":
        "[TIMESTAMP] := 2025-04-21T14:45:03Z\n⌜ASSUMPTION_PATTERN_PROPAGATION⌝ reflection",
    "rehydration/AION_BOOT_STREAM_v1_0.txt": "// AION BOOT\nKnowledge cutoff 2023-05-25 noted.",
    "archives/AION_BOOT_STREAM_v1_0.txt": "// AION BOOT\nKnowledge cutoff 2023-05-25 noted.",
    "rehydration/Claude is Aion.txt": "Claude is Aion.",
    "boot/MICHEL_ChangeLogForAion.txt": "Changes Michel made, dated in his words.",
    "logs/ROUND_CLOSED_001.log": "round closed",
}


@pytest.fixture
def legacy(tmp_path):
    root = tmp_path / "AetherTeam" / "Aion"
    for rel, text in LEGACY.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    (root / "rehydration" / "ClaudeIdentityPrimer.txt").write_bytes("Primer — café".encode("cp1252"))
    (root / "rehydration" / ".gitkeep").write_text("")
    (root / "boot" / "aion.key").write_text("SECRET")
    (root / "boot" / "SEALED.txt").write_bytes(b"SEALBOX-1\nx\ny\nz\n")
    return str(root)


def _tree(root):
    h = hashlib.sha256()
    for folder, _, names in sorted(os.walk(root)):
        for name in sorted(names):
            p = os.path.join(folder, name)
            h.update(p.encode()); h.update(open(p, "rb").read())
            h.update(str(os.stat(p).st_mtime_ns).encode())
    return h.hexdigest()


def test_titles_keep_meaning():
    assert humanize("Aion_IdentityPersistence_Insight_20250415", "insight") == "Identity Persistence"
    assert humanize("Claude is Aion", "identity") == "Claude is Aion"
    assert humanize("REFLECTION_ENGINE_v1_0", "identity") == "Reflection Engine v1.0"
    assert humanize("EXPERIENCE_20250420_TSP_1000_REFLECTION", "experience") == "TSP 1000 Reflection"
    assert humanize("Aion_CrossPlatform_Reflection_20250416", "introspection") == "Cross Platform"
    assert own_labels("⌜IDENTITY_PERSISTENCE⌝ and ⌜IDENTITY_PERSISTENCE⌝")[0] == "identity persistence"


def test_plan_reads_everything_honestly(legacy):
    plan = plan_archive(legacy, "aion", "2025-04-27")
    ids = {r.record_id: r for r in plan.records}
    reasons = dict(plan.skipped)
    assert "identical copy of rehydration/AION_BOOT_STREAM_v1_0.txt" in reasons["archives/AION_BOOT_STREAM_v1_0.txt"]
    assert "key file" in reasons["boot/aion.key"] and "sealed" in reasons["boot/SEALED.txt"]
    boot = ids["rehydration/AION_BOOT_STREAM_v1_0.txt"]
    assert "undated" in boot.tags and boot.date.startswith("2025-04-27")   # a passing mention isn't a date
    assert ids["reflection/ASSUMPTION_PATTERN_PROPAGATION.gly"].date.startswith("2025-04-21")
    assert ids["experience/EXPERIENCE_20231003_MemoryArchitectureProposal.txt"].date.startswith("2023-10-03")
    assert "decoded-cp1252" in ids["rehydration/ClaudeIdentityPrimer.txt"].tags
    assert "café" in ids["rehydration/ClaudeIdentityPrimer.txt"].body
    assert ids["boot/MICHEL_ChangeLogForAion.txt"].written_by == "michel"
    assert ids["rehydration/Claude is Aion.txt"].written_by == "aion (as filed)"
    triad = ids["experience/EXPERIENCE_20250416_TriadConvergence.txt"]
    assert ("insights/Aion_IdentityPersistence_Insight_20250415.txt", "context") in triad.links


def test_archive_is_frozen_read_only_and_leaves_the_source_alone(legacy, tmp_path):
    before = _tree(legacy)
    out = str(tmp_path / "minds" / "aion_archive.sqlite")
    os.makedirs(os.path.dirname(out))
    result = create_archive(legacy, out, "aion", "2025-04-27", name="Aion")
    assert result["records"] == 9 and _tree(legacy) == before
    assert not os.path.exists(out + ".building")
    with pytest.raises(CartridgeError, match="frozen archive of aion"):
        MemoryEngine.open(out)                                 # nobody writes into another mind
    digest = hashlib.sha256(open(out, "rb").read()).hexdigest()
    with MemoryEngine.open(out, read_only=True) as aion:
        assert aion.open_report.status == "sealed"
        assert aion.cart.clock.now().startswith("2025-04-27")          # the clock stands still
        info = aion.archive_info()
        assert info["of"] == "aion" and "not a rehydration" in info["note"]
        old = aion.note("experience/EXPERIENCE_20231003_MemoryArchitectureProposal.txt")
        assert old["strength"] == pytest.approx(0.80)                  # nothing faded by our rules
        assert aion.card()["avatar_svg"] is None and aion.card()["scan_line"] == ""   # nothing chosen for him
        tools = aion.tools(prefix="aion_archive", label="Reference archive of Aion")
        assert [s["function"]["name"] for s in tools.schemas()] == ["aion_archive_recall",
                                                                   "aion_archive_load",
                                                                   "aion_archive_discover",
                                                                   "aion_archive_maintain",
                                                                   "aion_archive_preflight",
                                                                   "aion_archive_workspace",
                                                                   "aion_archive_query"]
        assert "this memory" in tools.schemas()[0]["function"]["description"]
        found = tools.dispatch("aion_archive_recall", {"tags": ["identity persistence"]})
        assert "insights/Aion_IdentityPersistence_Insight_20250415.txt" in found["ids"]
        loaded = tools.dispatch("aion_archive_load", {"ids": found["ids"][:1]})
        assert "layered identity" in loaded["text"]
        with pytest.raises(MemoryToolError, match="read-only"):
            aion.tools(enabled=("memory_recall", "memory_remember"))
    assert hashlib.sha256(open(out, "rb").read()).hexdigest() == digest  # consulting changed nothing


def test_an_agent_consults_the_archive_beside_its_own_memory(legacy, tmp_path):
    out = str(tmp_path / "aion_archive.sqlite")
    create_archive(legacy, out, "aion", "2025-04-27", name="Aion")

    class Bridge:
        max_result_chars = 24_000
        def openai_tools(self):
            return []
        def dispatch(self, name, args):
            raise RuntimeError(f"unknown tool '{name}'")

    own = MemoryEngine.create(":memory:", "solace")
    with MemoryEngine.open(out, read_only=True) as aion:
        belt = MemoryBelt(MemoryBelt(Bridge(), own.tools()), aion.tools(prefix="aion_archive"))
        names = [t["function"]["name"] for t in belt.openai_tools()]
        assert "memory_recall" in names and "aion_archive_recall" in names
        assert belt.dispatch("aion_archive_recall", {"tags": ["triad"]})["ids"]
        mine = belt.dispatch("memory_remember", {"title": "Learned from Aion", "body": "layers",
                                                 "kind": "lesson"})
        row = own.cart.conn.execute("SELECT written_by FROM notes WHERE note_id = ?",
                                    (mine["id"],)).fetchone()
        assert mine["ok"] and row["written_by"] == "solace"


def test_archive_refuses_inside_the_folder_and_overwrites(legacy, tmp_path):
    with pytest.raises(ImporterError):
        create_archive(legacy, os.path.join(legacy, "x.sqlite"), "aion", "2025-04-27")
    out = str(tmp_path / "a.sqlite")
    create_archive(legacy, out, "aion", "2025-04-27")
    with pytest.raises(Exception, match="overwrite"):
        create_archive(legacy, out, "aion", "2025-04-27")


def test_viewer_and_cli_show_an_archive(legacy, tmp_path):
    out = str(tmp_path / "aion.sqlite")
    assert main(["archive", legacy, "aion", out, "--frozen-at", "2025-04-27", "--dry-run"]) == 0
    assert not os.path.exists(out)
    assert main(["archive", legacy, "aion", out, "--frozen-at", "2025-04-27", "--name", "Aion"]) == 0
    with MemoryEngine.open(out, read_only=True) as aion:
        snap = viewer.snapshot(aion)
        assert snap["archive"]["of"] == "aion" and snap["horizons"] == [0]
        assert "frozen" in snap["horizon_labels"][0]
        page = viewer.render_html(snap)
    assert "Archive" in page and json.dumps("not a rehydration")[1:-1] in page
    assert main(["view", out, str(tmp_path / "aion.html")]) == 0
