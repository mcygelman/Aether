"""HW-11 shared Sanctum recovery lifecycle tests on disposable profiles."""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aether_bridge import sanctum  # noqa: E402


def profile(root: Path) -> Path:
    template = root / "template"
    (template / "identity").mkdir(parents=True)
    (template / "identity" / "System_0.txt").write_text("Original\n", encoding="utf-8")
    target = root / "profile"
    sanctum.hire(template, target, agent="RecoveryTest")
    return target


def test_shared_failure_classification_acknowledge_resolve_archive():
    with tempfile.TemporaryDirectory() as td:
        p = profile(Path(td))
        (p / "identity" / "System_0.txt").write_text("Changed\n", encoding="utf-8")
        cli_report, cli = sanctum.verify_or_refuse(p, "cli")
        assert not cli_report.ok
        assert cli["category"] == "MISMATCH"
        assert cli["state"] == "ACTIVE"

        flag = sanctum.acknowledge_recovery(p, "Operator")
        assert "State: `ACKNOWLEDGED`" in flag.read_text(encoding="utf-8")
        sanctum.reseal(p, "Operator repair")
        clean, state = sanctum.verify_or_refuse(p, "studio")
        assert clean.ok and state["state"] == "RESOLVED"
        assert "State: `RESOLVED`" in flag.read_text(encoding="utf-8")

        archived = sanctum.archive_recovery(p, "Operator")
        assert archived.is_file()
        assert not (p / sanctum.RECOVERY_FLAG).exists()
        assert "State: `ARCHIVED`" in archived.read_text(encoding="utf-8")

        events = [
            json.loads(line)
            for line in (p / sanctum.RECOVERY_LOG).read_text(encoding="utf-8").splitlines()
        ]
        assert [item["state"] for item in events] == [
            "ACTIVE", "ACKNOWLEDGED", "RESOLVED", "ARCHIVED"
        ]


def test_manifest_failure_has_same_category_for_cli_and_studio_sources():
    with tempfile.TemporaryDirectory() as td:
        p = profile(Path(td))
        (p / sanctum.MANIFEST_NAME).unlink()
        first, cli = sanctum.verify_or_refuse(p, "cli")
        second, studio = sanctum.verify_or_refuse(p, "studio")
        assert not first.ok and not second.ok
        assert cli["category"] == studio["category"] == "MANIFEST_ERROR"


def test_clean_profile_without_history_has_none_state():
    with tempfile.TemporaryDirectory() as td:
        p = profile(Path(td))
        report, recovery = sanctum.verify_or_refuse(p, "studio")
        assert report.ok
        assert recovery["state"] == "NONE"
