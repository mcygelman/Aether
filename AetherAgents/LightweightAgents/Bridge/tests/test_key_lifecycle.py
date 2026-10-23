"""HW-12 key lifecycle tests on disposable profiles only."""
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aether_bridge import key_lifecycle, sanctum, sealbox  # noqa: E402

SECRET = "correct horse battery staple"


def profile(root: Path) -> Path:
    template = root / "template"
    (template / "identity").mkdir(parents=True)
    (template / "memory" / "index").mkdir(parents=True)
    (template / "memory" / "archive").mkdir()
    (template / "identity" / "System_0.txt").write_text("Synthetic\n", encoding="utf-8")
    (template / "memory" / "index" / "index.md").write_text("INDEX SECRET\n", encoding="utf-8")
    (template / "memory" / "archive" / "one.md").write_text("ARCHIVE SECRET\n", encoding="utf-8")
    p = root / "profile"
    sanctum.hire(template, p, agent="KeyLifecycleTest")
    (p / "boot-critical.list").write_text(f"{sealbox.POLICY_NAME}\n", encoding="utf-8")
    sealbox.enable_profile(p)
    sanctum.reseal(p, "Disposable key lifecycle")
    return p


def test_backup_remove_restore_and_wrong_secret_refusal():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        p = profile(root)
        backup = root / "offline" / "profile.keybackup"
        result = key_lifecycle.backup_key(p, backup, SECRET, "Operator")
        fingerprint = result["key_fingerprint"]
        (p / ".sealbox_key").unlink()
        try:
            key_lifecycle.restore_key(p, backup, "wrong secret value", "Operator")
        except key_lifecycle.KeyLifecycleError as exc:
            assert "authentication failed" in str(exc)
        else:
            raise AssertionError("wrong backup secret was accepted")
        restored = key_lifecycle.restore_key(p, backup, SECRET, "Operator")
        assert restored["state"] == "ACTIVE"
        assert restored["key_fingerprint"] == fingerprint
        assert sealbox.verify_tree(p, p / ".sealbox_key")


def test_rotation_preserves_plaintext_and_old_key_fails():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        p = profile(root)
        old = root / "old.key"
        old.write_bytes((p / ".sealbox_key").read_bytes())
        before = sealbox.open_tree(p)
        result = key_lifecycle.rotate_key(p, "Operator")
        assert result["rotated_files"] == 2
        assert sealbox.open_tree(p) == before
        target = p / "memory" / "index" / "index.md"
        try:
            sealbox.open_file(target, old, require_sealed=True)
        except sealbox.SealboxError as exc:
            assert "tag mismatch" in str(exc)
        else:
            raise AssertionError("old key opened rotated memory")


def test_rotation_rolls_back_all_bytes_after_caught_write_failure():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        p = profile(root)
        originals = {
            path: path.read_bytes()
            for path in (p / "memory").rglob("*") if path.is_file()
        }
        old_key = (p / ".sealbox_key").read_bytes()
        real = sealbox.atomic_write_bytes
        calls = {"count": 0}

        def fail_once(path, data):
            if Path(path).is_relative_to(p / "memory"):
                calls["count"] += 1
                if calls["count"] == 2:
                    raise OSError("synthetic rotation interruption")
            return real(path, data)

        with patch.object(sealbox, "atomic_write_bytes", side_effect=fail_once):
            try:
                key_lifecycle.rotate_key(p, "Operator")
            except OSError as exc:
                assert "synthetic" in str(exc)
            else:
                raise AssertionError("synthetic rotation failure did not propagate")
        assert (p / ".sealbox_key").read_bytes() == old_key
        assert all(path.read_bytes() == raw for path, raw in originals.items())
        assert sealbox.verify_tree(p, p / ".sealbox_key")


def test_status_states_and_backup_location_guards():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        p = profile(root)
        for state in ("COMPROMISED", "RETIRED"):
            record = key_lifecycle.mark_state(p, state, "synthetic test", "Operator")
            assert record["state"] == state
        (p / ".sealbox_key").unlink()
        assert key_lifecycle.mark_state(
            p, "LOST", "synthetic test", "Operator"
        )["key_fingerprint"] is None
        try:
            key_lifecycle.backup_key(p, p / "unsafe.backup", SECRET, "Operator")
        except key_lifecycle.KeyLifecycleError as exc:
            assert "outside the profile" in str(exc)
        else:
            raise AssertionError("in-profile key backup was accepted")
