"""SANCTUM-1 integrity machinery for heavyweight profiles."""
from __future__ import annotations

import fnmatch
import json
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from aether.integrity import canon
from aether.integrity.undo_journal import write_entry

MANIFEST_NAME = "SANCTUM_MANIFEST.json"
RECOVERY_FLAG = "RECOVERY_FLAG.md"
RECOVERY_LOG = Path("logs") / "recovery_history.jsonl"
FORMAT = "SANCTUM-1"


class SanctumError(Exception):
    pass


def verified_identity_names(profile_dir: str | Path) -> tuple[str, ...]:
    """Identity authority from the exact protected bytes read, never roster/folder names.

    The caller still performs the complete profile launch gate. This narrower check
    also protects direct memory-pointer consumers and avoids a verify/read race.
    Unprotected identity supplements confer no authority.
    """
    profile = Path(profile_dir)
    protected = load_manifest(profile / MANIFEST_NAME)["protected"]
    names = set()
    for rel in ("identity/System_0.txt", "identity/AGENT_ID.json"):
        if rel not in protected:
            continue
        text = canon.decode_utf8((profile / rel).read_bytes())
        if "sha256:" + canon.canon_sha(text) != protected[rel]:
            raise SanctumError(f"identity hash mismatch: {rel}")
        if rel.endswith('.json'):
            data = json.loads(text)
            aliases = data.get('aliases', [])
            if not isinstance(aliases, list):
                raise SanctumError('identity aliases must be a list')
            values = [data.get('guid'), data.get('readable_id'), *aliases]
        else:
            values = re.findall(r"⌜AGENT_(?:ID|NAME)⌝\s*:=\s*⌞([^⌟]+)⌟", text)
        names.update(v.strip() for v in values if isinstance(v, str) and v.strip())
    return tuple(sorted(names))


@dataclass
class FileCheck:
    path: str
    expected: str | None
    actual: str | None
    ok: bool
    status: str

    def to_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass
class VerifyReport:
    ok: bool
    profile_dir: str
    files: list[FileCheck]
    error: str = ""

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "profile_dir": self.profile_dir,
            "files": [f.to_dict() for f in self.files],
            "error": self.error,
        }


def timestamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def load_manifest(path: str | Path) -> dict:
    manifest_path = Path(path)
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
        raise SanctumError(f"manifest parse failed: {e}")
    if not isinstance(data, dict):
        raise SanctumError("manifest must be a JSON object")
    if data.get("format") != FORMAT:
        raise SanctumError(f"unknown manifest format: {data.get('format')!r}")
    for field in ("agent", "authored", "authorized_by", "protected", "prior_manifest"):
        if field not in data:
            raise SanctumError(f"manifest missing field: {field}")
    if not isinstance(data["agent"], str) or not data["agent"]:
        raise SanctumError("manifest agent must be a non-empty string")
    if not isinstance(data["authored"], str) or not data["authored"]:
        raise SanctumError("manifest authored must be a non-empty string")
    if not isinstance(data["authorized_by"], str) or not data["authorized_by"]:
        raise SanctumError("manifest authorized_by must be a non-empty string")
    if data["prior_manifest"] is not None and not isinstance(data["prior_manifest"], str):
        raise SanctumError("manifest prior_manifest must be string or null")
    protected = data["protected"]
    if not isinstance(protected, dict) or not protected:
        raise SanctumError("manifest protected must be a non-empty object")
    for rel, value in protected.items():
        if not isinstance(rel, str) or not rel or rel.startswith("/") or "\\" in rel:
            raise SanctumError(f"bad protected path: {rel!r}")
        if ".." in Path(rel).parts:
            raise SanctumError(f"protected path escapes profile: {rel!r}")
        if not isinstance(value, str) or not value.startswith("sha256:") or len(value) != 71:
            raise SanctumError(f"bad protected hash for {rel}: {value!r}")
        hexpart = value.split(":", 1)[1]
        if any(c not in "0123456789abcdef" for c in hexpart):
            raise SanctumError(f"bad protected hash for {rel}: {value!r}")
    return data


def verify(profile_dir: str | Path) -> VerifyReport:
    profile = Path(profile_dir).resolve()
    try:
        manifest = load_manifest(profile / MANIFEST_NAME)
    except SanctumError as e:
        return VerifyReport(False, str(profile), [], str(e))
    checks = []
    for rel, expected_value in sorted(manifest["protected"].items()):
        expected = expected_value.split(":", 1)[1]
        path = (profile / rel).resolve()
        if not (path == profile or profile in path.parents):
            checks.append(FileCheck(rel, expected, None, False, "ESCAPES_PROFILE"))
            continue
        if not path.is_file():
            checks.append(FileCheck(rel, expected, None, False, "MISSING"))
            continue
        try:
            actual = canon.canon_sha(canon.decode_utf8(path.read_bytes()))
        except (OSError, UnicodeDecodeError) as e:
            checks.append(FileCheck(rel, expected, None, False, f"READ_FAIL: {e}"))
            continue
        checks.append(FileCheck(rel, expected, actual, actual == expected, "PASS" if actual == expected else "MISMATCH"))
    return VerifyReport(all(c.ok for c in checks), str(profile), checks)


def write_recovery_flag(profile_dir: str | Path, report: VerifyReport) -> Path:
    profile = Path(profile_dir).resolve()
    lines = [
        "# Sanctum Recovery Flag",
        "",
        f"- Timestamp: `{timestamp()}`",
        f"- Profile: `{profile}`",
        f"- Gate: `{'PASS' if report.ok else 'FAIL'}`",
        "- State: `ACTIVE`",
        f"- Category: `{recovery_category(report)}`",
        "",
    ]
    if report.error:
        lines.extend(["## Manifest Error", "", report.error, ""])
    if report.files:
        lines.extend(["## File Checks", "", "| File | Status | Expected | Actual |", "|---|---|---|---|"])
        for item in report.files:
            lines.append(f"| `{item.path}` | `{item.status}` | `{item.expected or ''}` | `{item.actual or ''}` |")
    path = profile / RECOVERY_FLAG
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _log_recovery(profile, "ACTIVE", recovery_category(report), "verification failure")
    return path


def recovery_category(report: VerifyReport) -> str:
    if report.error:
        return "MANIFEST_ERROR"
    failures = sorted({item.status.split(":", 1)[0] for item in report.files if not item.ok})
    return "+".join(failures) if failures else "UNKNOWN_FAILURE"


def _log_recovery(profile: Path, state: str, category: str, detail: str) -> None:
    path = profile / RECOVERY_LOG
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({
            "ts": timestamp(), "state": state, "category": category,
            "detail": detail,
        }, ensure_ascii=False) + "\n")


def _set_recovery_state(profile_dir: str | Path, state: str, by: str) -> Path:
    profile = Path(profile_dir).resolve()
    flag = profile / RECOVERY_FLAG
    if not flag.is_file():
        raise SanctumError("no recovery flag exists")
    text = flag.read_text(encoding="utf-8")
    if "- State: `" not in text:
        raise SanctumError("recovery flag has no lifecycle state")
    text = re.sub(r"- State: `[^`]+`", f"- State: `{state}`", text, count=1)
    text += f"\n- {state.title()} by: `{by}` at `{timestamp()}`\n"
    flag.write_text(text, encoding="utf-8")
    category = re.search(r"- Category: `([^`]+)`", text)
    _log_recovery(profile, state, category.group(1) if category else "UNKNOWN", by)
    return flag


def acknowledge_recovery(profile_dir: str | Path, by: str) -> Path:
    if not by.strip():
        raise SanctumError("recovery acknowledgment requires attribution")
    return _set_recovery_state(profile_dir, "ACKNOWLEDGED", by.strip())


def resolve_recovery(profile_dir: str | Path, by: str) -> Path:
    if not by.strip():
        raise SanctumError("recovery resolution requires attribution")
    return _set_recovery_state(profile_dir, "RESOLVED", by.strip())


def archive_recovery(profile_dir: str | Path, by: str) -> Path:
    if not by.strip():
        raise SanctumError("recovery archive requires attribution")
    profile = Path(profile_dir).resolve()
    flag = _set_recovery_state(profile, "ARCHIVED", by.strip())
    archive = profile / "logs" / "recovery_flags"
    archive.mkdir(parents=True, exist_ok=True)
    target = archive / f"RECOVERY_FLAG_{int(time.time() * 1000)}.md"
    flag.replace(target)
    return target


def verify_or_refuse(profile_dir: str | Path, source: str) -> tuple[VerifyReport, dict]:
    """Shared CLI/Studio orchestration with a persistent recovery lifecycle."""
    profile = Path(profile_dir).resolve()
    report = verify(profile)
    if not report.ok:
        try:
            flag = write_recovery_flag(profile, report)
            recovery = {
                "state": "ACTIVE", "category": recovery_category(report),
                "artifact": str(flag), "artifact_written": True, "source": source,
            }
        except OSError as exc:
            recovery = {
                "state": "ACTIVE", "category": recovery_category(report),
                "artifact": None, "artifact_written": False, "source": source,
                "artifact_error": str(exc),
            }
        return report, recovery
    flag = profile / RECOVERY_FLAG
    if flag.is_file():
        text = flag.read_text(encoding="utf-8", errors="replace")
        if "- State: `ACTIVE`" in text or "- State: `ACKNOWLEDGED`" in text:
            resolve_recovery(profile, f"{source}: clean verification")
            return report, {"state": "RESOLVED", "artifact": str(flag), "source": source}
    return report, {"state": "NONE", "artifact": None, "source": source}


def protected_paths(profile_dir: str | Path) -> list[str]:
    profile = Path(profile_dir)
    out: set[str] = set()
    identity = profile / "identity"
    if identity.is_dir():
        for path in identity.rglob("*"):
            if path.is_file():
                out.add(path.relative_to(profile).as_posix())
    boot_list = profile / "boot-critical.list"
    if boot_list.is_file():
        out.add("boot-critical.list")
        for line in boot_list.read_text(encoding="utf-8").splitlines():
            rel = line.strip()
            if not rel or rel.startswith("#"):
                continue
            out.add(rel.replace("\\", "/"))
    return sorted(out)


def build_protected_map(profile_dir: str | Path) -> dict[str, str]:
    profile = Path(profile_dir).resolve()
    protected = {}
    for rel in protected_paths(profile):
        path = (profile / rel).resolve()
        if not path.is_file() or not (path == profile or profile in path.parents):
            raise SanctumError(f"protected file missing or invalid: {rel}")
        protected[rel] = "sha256:" + canon.canon_sha(canon.decode_utf8(path.read_bytes()))
    if not protected:
        raise SanctumError("no protected files discovered")
    return protected


def _write_manifest(profile: Path, manifest: dict) -> None:
    with (profile / MANIFEST_NAME).open("w", encoding="utf-8", newline="\n") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
        f.write("\n")


def reseal(profile_dir: str | Path, authorized_by: str) -> dict:
    if not authorized_by.strip():
        raise SanctumError("reseal requires authorized_by")
    profile = Path(profile_dir).resolve()
    manifest_path = profile / MANIFEST_NAME
    manifest = load_manifest(manifest_path)
    journal = profile / "logs" / "sanctum_journal"
    journal.mkdir(parents=True, exist_ok=True)
    prior_id = write_entry(journal, "sanctum_reseal", "profile", profile, manifest_path,
                           manifest_path.read_text(encoding="utf-8"))
    manifest["protected"] = build_protected_map(profile)
    manifest["authorized_by"] = authorized_by
    manifest["authored"] = timestamp()
    manifest["prior_manifest"] = prior_id
    _write_manifest(profile, manifest)
    return manifest


def initialize(profile_dir: str | Path, agent: str, authorized_by: str) -> dict:
    """Create the first manifest for an existing authored profile without copying or key creation."""
    if not agent.strip():
        raise SanctumError("initialize requires agent")
    if not authorized_by.strip():
        raise SanctumError("initialize requires authorized_by")
    profile = Path(profile_dir).resolve()
    if not profile.is_dir():
        raise SanctumError(f"profile not found: {profile}")
    manifest_path = profile / MANIFEST_NAME
    if manifest_path.exists():
        raise SanctumError(f"manifest already exists: {manifest_path}")
    manifest = {
        "format": FORMAT,
        "agent": agent,
        "authored": timestamp(),
        "authorized_by": authorized_by,
        "protected": build_protected_map(profile),
        "prior_manifest": None,
    }
    _write_manifest(profile, manifest)
    return manifest


def hire(template_dir: str | Path, profile_dir: str | Path, agent: str = "UNAUTHORED") -> dict:
    template = Path(template_dir).resolve()
    profile = Path(profile_dir).resolve()
    if profile.exists():
        raise SanctumError(f"profile already exists: {profile}")
    if not template.is_dir():
        raise SanctumError(f"template not found: {template}")
    shutil.copytree(template, profile)
    keyfile = profile / ".sealbox_key"
    if not keyfile.exists():
        from aether.integrity import sealbox
        sealbox.provision_key(profile)
    else:
        append_gitignore(profile, ".sealbox_key")
    manifest = {
        "format": FORMAT,
        "agent": agent,
        "authored": timestamp(),
        "authorized_by": "Bridge sanctum hire",
        "protected": build_protected_map(profile),
        "prior_manifest": None,
    }
    _write_manifest(profile, manifest)
    return manifest


def append_gitignore(profile: Path, pattern: str) -> None:
    path = profile / ".gitignore"
    existing = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    if pattern not in existing:
        with path.open("a", encoding="utf-8", newline="\n") as f:
            if existing and existing[-1] != "":
                f.write("\n")
            f.write(pattern + "\n")


def report_text(report: VerifyReport) -> str:
    lines = [f"SANCTUM VERIFY {'PASS' if report.ok else 'FAIL'}: {report.profile_dir}"]
    if report.error:
        lines.append(f"ERROR: {report.error}")
    for item in report.files:
        lines.append(f"{item.status}: {item.path} expected={item.expected} actual={item.actual}")
    return "\n".join(lines)


def should_skip(path: Path, patterns: list[str]) -> bool:
    rel = path.as_posix()
    return any(fnmatch.fnmatch(rel, pat) for pat in patterns)
