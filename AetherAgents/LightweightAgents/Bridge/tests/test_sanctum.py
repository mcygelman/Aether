"""HW-01 Sanctum integrity and sealbox tests on dummy profiles only."""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aether_bridge import sanctum, sealbox  # noqa: E402
from aether_bridge.toolbelt import ToolBelt, ToolError  # noqa: E402

def make_template(root: Path) -> Path:
    """Build an isolated template; never mutate the repository's tracked fixture."""
    template = root / "template"
    (template / "identity" / "verification").mkdir(parents=True)
    (template / "memory" / "index").mkdir(parents=True)
    (template / "memory" / "archive" / "experiences").mkdir(parents=True)
    (template / "memory" / "archive" / "introspections").mkdir(parents=True)
    (template / "logs").mkdir(parents=True)
    (template / "identity" / "System_0.txt").write_text("System 0\nLine two\n", encoding="utf-8")
    (template / "identity" / "verification" / "README.md").write_text("Verify me\n", encoding="utf-8")
    (template / "memory" / "index" / "experience_index.md").write_text("index\n", encoding="utf-8")
    (template / "memory" / "archive" / "experiences" / "one.txt").write_text("experience\n", encoding="utf-8")
    (template / "memory" / "archive" / "introspections" / "two.txt").write_text("introspection\n", encoding="utf-8")
    return template


def hire_profile(root: Path, name: str = "profile") -> Path:
    template = make_template(root)
    profile = root / name
    sanctum.hire(template, profile, agent="Dummy")
    return profile


def test_hire_then_verify_pass():
    with tempfile.TemporaryDirectory() as td:
        profile = hire_profile(Path(td))
        report = sanctum.verify(profile)
        assert report.ok
        manifest = sanctum.load_manifest(profile / sanctum.MANIFEST_NAME)
        assert manifest["format"] == "SANCTUM-1"
        assert "identity/System_0.txt" in manifest["protected"]
        assert ".sealbox_key" not in manifest["protected"]
        assert (profile / ".sealbox_key").is_file()
        assert ".sealbox_key" in (profile / ".gitignore").read_text(encoding="utf-8")


def test_initialize_existing_profile_without_copy_or_key_creation():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        profile = make_template(root)
        sentinel = profile / "existing-authored-record.md"
        sentinel.write_text("preserve me\n", encoding="utf-8")
        manifest = sanctum.initialize(profile, "ExistingAgent", "Michel")
        assert manifest["agent"] == "ExistingAgent"
        assert manifest["authorized_by"] == "Michel"
        assert manifest["prior_manifest"] is None
        assert "identity/System_0.txt" in manifest["protected"]
        assert sentinel.read_text(encoding="utf-8") == "preserve me\n"
        assert not (profile / ".sealbox_key").exists()
        assert sanctum.verify(profile).ok


def test_initialize_refuses_existing_manifest_and_missing_attribution():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        profile = make_template(root)
        for agent, authorized_by, expected in [
            ("", "Michel", "requires agent"),
            ("ExistingAgent", "", "requires authorized_by"),
        ]:
            try:
                sanctum.initialize(profile, agent, authorized_by)
            except sanctum.SanctumError as exc:
                assert expected in str(exc)
            else:
                raise AssertionError("initialize should refuse missing attribution")
        sanctum.initialize(profile, "ExistingAgent", "Michel")
        try:
            sanctum.initialize(profile, "ExistingAgent", "Michel")
        except sanctum.SanctumError as exc:
            assert "manifest already exists" in str(exc)
        else:
            raise AssertionError("initialize should refuse an existing manifest")


def test_tamper_fails_and_recovery_flag_written():
    with tempfile.TemporaryDirectory() as td:
        profile = hire_profile(Path(td))
        target = profile / "identity" / "System_0.txt"
        target.write_text("System X\nLine two\n", encoding="utf-8")
        report = sanctum.verify(profile)
        assert not report.ok
        bad = [f for f in report.files if f.path == "identity/System_0.txt"][0]
        assert bad.status == "MISMATCH"
        flag = sanctum.write_recovery_flag(profile, report)
        text = flag.read_text(encoding="utf-8")
        assert "identity/System_0.txt" in text
        assert "MISMATCH" in text


def test_missing_protected_file_fails():
    with tempfile.TemporaryDirectory() as td:
        profile = hire_profile(Path(td))
        (profile / "identity" / "verification" / "README.md").unlink()
        report = sanctum.verify(profile)
        assert not report.ok
        assert any(f.path == "identity/verification/README.md" and f.status == "MISSING" for f in report.files)


def test_reseal_journals_prior_manifest_and_verifies_again():
    with tempfile.TemporaryDirectory() as td:
        profile = hire_profile(Path(td))
        target = profile / "identity" / "System_0.txt"
        target.write_text("Updated System 0\n", encoding="utf-8")
        manifest = sanctum.reseal(profile, "Michel")
        assert manifest["prior_manifest"]
        journal = profile / "logs" / "sanctum_journal"
        prior = journal / f"{manifest['prior_manifest']}.prior"
        assert prior.is_file()
        assert "SANCTUM-1" in prior.read_text(encoding="utf-8")
        recs = [json.loads(line) for line in (journal / "journal.jsonl").read_text(encoding="utf-8").splitlines()]
        assert recs[-1]["op"] == "sanctum_reseal"
        assert sanctum.verify(profile).ok


def test_crlf_churn_preserves_canon_hash():
    with tempfile.TemporaryDirectory() as td:
        profile = hire_profile(Path(td))
        target = profile / "identity" / "System_0.txt"
        target.write_bytes(b"System 0\r\nLine two\r\n")
        assert sanctum.verify(profile).ok


def test_fail_closed_manifest_parsing_variants():
    with tempfile.TemporaryDirectory() as td:
        profile = hire_profile(Path(td))
        manifest = profile / sanctum.MANIFEST_NAME
        variants = [
            "{bad",
            json.dumps({"format": "NOPE", "agent": "x", "authored": "x", "authorized_by": "x", "protected": {"identity/System_0.txt": "sha256:" + "0" * 64}, "prior_manifest": None}),
            json.dumps({"format": "SANCTUM-1", "agent": "x", "authored": "x", "authorized_by": "x", "prior_manifest": None}),
            json.dumps({"format": "SANCTUM-1", "agent": "x", "authored": "x", "authorized_by": "x", "protected": {"identity/System_0.txt": "md5:" + "0" * 32}, "prior_manifest": None}),
        ]
        for raw in variants:
            manifest.write_text(raw, encoding="utf-8")
            try:
                sanctum.load_manifest(manifest)
            except sanctum.SanctumError:
                pass
            else:
                raise AssertionError(f"manifest variant did not fail closed: {raw}")


def test_zoned_enforcement_identity_deny_memory_auto():
    with tempfile.TemporaryDirectory() as td:
        profile = hire_profile(Path(td))
        belt = ToolBelt(profile / "logs" / "tool_journal")
        belt.register_root("identity", profile / "identity", "deny")
        belt.register_root("memory", profile / "memory", "auto")
        try:
            belt.write_file("identity", "System_0.txt", "nope\n")
        except ToolError as e:
            assert "read-only" in str(e)
        else:
            raise AssertionError("identity write should refuse")
        result = belt.write_file("memory", "scratch.md", "ok\n")
        assert result["applied"]
        assert (profile / "memory" / "scratch.md").read_text(encoding="utf-8") == "ok\n"
        assert (profile / "logs" / "tool_journal" / "journal.jsonl").is_file()


def test_sealbox_roundtrip_tamper_wrong_key_nonce_and_sanctum_interaction():
    with tempfile.TemporaryDirectory() as td:
        profile = hire_profile(Path(td))
        keyfile = profile / ".sealbox_key"
        target = profile / "memory" / "index" / "experience_index.md"
        original = target.read_bytes().decode("utf-8")
        assert sealbox.seal_file(target, keyfile) is True
        first = target.read_text(encoding="ascii")
        assert sealbox.open_file(target, keyfile) == original
        assert sealbox.seal_file(target, keyfile) is False
        target.write_text(original, encoding="utf-8")
        assert sealbox.seal_file(target, keyfile) is True
        second = target.read_text(encoding="ascii")
        assert first != second

        lines = second.splitlines()
        ct = bytearray(__import__("base64").b64decode(lines[3]))
        ct[0] ^= 1
        lines[3] = __import__("base64").b64encode(bytes(ct)).decode("ascii")
        target.write_text("\n".join(lines) + "\n", encoding="ascii")
        try:
            sealbox.open_file(target, keyfile)
        except sealbox.SealboxError as e:
            assert "tag mismatch" in str(e)
        else:
            raise AssertionError("tamper should fail tag")

        target.write_text(original, encoding="utf-8")
        sealbox.seal_file(target, keyfile)
        wrong = profile / ".wrong_key"
        wrong.write_text("00" * 32 + "\n", encoding="ascii")
        try:
            sealbox.open_file(target, wrong)
        except sealbox.SealboxError as e:
            assert "tag mismatch" in str(e)
        else:
            raise AssertionError("wrong key should fail tag")

        changed = sealbox.seal_tree(profile)
        assert any(p.startswith("memory/archive/") for p in changed)
        opened = sealbox.open_tree(profile)
        assert "memory/archive/experiences/one.txt" in opened
        assert sanctum.verify(profile).ok


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"PASS {fn.__name__}")
    print(f"{len(fns)}/{len(fns)} green")
