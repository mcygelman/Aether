"""SEALBOX-1 encryption-at-rest helpers.

Threat model: protects against off-machine copies and casual browsing, not a local admin
(operator transparency was accepted in the original spec). Weaker than AES-GCM by choice —
sovereignty over strength.
"""
from __future__ import annotations

import base64
import hmac
import json
import os
import secrets
from hashlib import sha256
from pathlib import Path

HEADER = "SEALBOX-1"
POLICY_NAME = "SEALED_MEMORY_POLICY.json"
POLICY_FORMAT = "AETHER-SEALED-MEMORY-1"


class SealboxError(Exception):
    pass


def read_key(keyfile: str | Path) -> bytes:
    try:
        raw = Path(keyfile).read_text(encoding="ascii").strip()
        key = bytes.fromhex(raw)
    except (OSError, ValueError) as e:
        raise SealboxError(f"bad keyfile {keyfile}: {e}")
    if len(key) != 32:
        raise SealboxError(f"bad key length in {keyfile}: {len(key)}")
    return key


def derive_keys(key: bytes) -> tuple[bytes, bytes]:
    enc_key = hmac.new(key, b"SEALBOX-1 enc", sha256).digest()
    mac_key = hmac.new(key, b"SEALBOX-1 mac", sha256).digest()
    return enc_key, mac_key


def crypt(data: bytes, enc_key: bytes, nonce: bytes) -> bytes:
    out = bytearray()
    block = 0
    while len(out) < len(data):
        offset = len(out)
        stream = hmac.new(enc_key, nonce + block.to_bytes(8, "big"), sha256).digest()
        take = min(len(stream), len(data) - offset)
        out.extend(data[offset + i] ^ stream[i] for i in range(take))
        block += 1
    return bytes(out)


def is_sealed_bytes(data: bytes) -> bool:
    return data.startswith((HEADER + "\n").encode("ascii")) or data.startswith((HEADER + "\r\n").encode("ascii"))


def atomic_write_bytes(path: str | Path, data: bytes) -> None:
    """Durably stage bytes beside the target, then atomically replace the target."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.parent / f".{target.name}.{secrets.token_hex(8)}.tmp"
    try:
        with temporary.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()


def seal_bytes(data: bytes, keyfile: str | Path) -> bytes:
    key = read_key(keyfile)
    enc_key, mac_key = derive_keys(key)
    nonce = secrets.token_bytes(16)
    ciphertext = crypt(data, enc_key, nonce)
    tag = hmac.new(mac_key, nonce + ciphertext, sha256).digest()
    return "\n".join([
        HEADER,
        nonce.hex(),
        tag.hex(),
        base64.b64encode(ciphertext).decode("ascii"),
        "",
    ]).encode("ascii")


def open_bytes(raw: bytes, keyfile: str | Path, require_sealed: bool = False) -> bytes:
    if not is_sealed_bytes(raw):
        if require_sealed:
            raise SealboxError("plaintext encountered where SEALBOX-1 was required")
        return raw
    lines = raw.decode("ascii").splitlines()
    if len(lines) != 4 or lines[0] != HEADER:
        raise SealboxError("bad sealed file format")
    try:
        nonce = bytes.fromhex(lines[1])
        tag = bytes.fromhex(lines[2])
        ciphertext = base64.b64decode(lines[3], validate=True)
    except ValueError as e:
        raise SealboxError(f"bad sealed file encoding: {e}")
    key = read_key(keyfile)
    enc_key, mac_key = derive_keys(key)
    expected = hmac.new(mac_key, nonce + ciphertext, sha256).digest()
    if not hmac.compare_digest(tag, expected):
        raise SealboxError("sealbox tag mismatch")
    return crypt(ciphertext, enc_key, nonce)


def write_sealed_file(path: str | Path, text: str, keyfile: str | Path) -> None:
    atomic_write_bytes(path, seal_bytes(text.encode("utf-8"), keyfile))


def seal_file(path: str | Path, keyfile: str | Path) -> bool:
    target = Path(path)
    data = target.read_bytes()
    if is_sealed_bytes(data):
        return False
    atomic_write_bytes(target, seal_bytes(data, keyfile))
    return True


def open_file(path: str | Path, keyfile: str | Path,
              require_sealed: bool = False) -> str:
    target = Path(path)
    try:
        return open_bytes(
            target.read_bytes(), keyfile, require_sealed=require_sealed
        ).decode("utf-8")
    except (OSError, UnicodeDecodeError, SealboxError) as exc:
        if isinstance(exc, SealboxError):
            raise SealboxError(f"{exc}: {target}") from exc
        raise SealboxError(f"could not open {target}: {exc}") from exc


def provision_key(profile_dir: str | Path) -> Path:
    """Create a profile key exactly once; never overwrite existing key material."""
    profile = Path(profile_dir).resolve()
    if not profile.is_dir():
        raise SealboxError(f"profile not found: {profile}")
    keyfile = profile / ".sealbox_key"
    if keyfile.exists():
        raise SealboxError(f"key already exists: {keyfile}")
    gitignore = profile / ".gitignore"
    lines = gitignore.read_text(encoding="utf-8").splitlines() if gitignore.is_file() else []
    if ".sealbox_key" not in lines:
        content = "\n".join(lines)
        if content:
            content += "\n"
        atomic_write_bytes(gitignore, (content + ".sealbox_key\n").encode("utf-8"))
    atomic_write_bytes(keyfile, secrets.token_bytes(32).hex().encode("ascii") + b"\n")
    try:
        keyfile.chmod(0o600)
    except OSError:
        pass
    return keyfile


def key_fingerprint(keyfile: str | Path) -> str:
    return sha256(read_key(keyfile)).hexdigest()


def load_policy(profile_dir: str | Path) -> dict | None:
    profile = Path(profile_dir).resolve()
    path = profile / POLICY_NAME
    if not path.is_file():
        return None
    try:
        policy = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SealboxError(f"sealed-memory policy parse failed: {exc}") from exc
    if not isinstance(policy, dict) or policy.get("format") != POLICY_FORMAT:
        raise SealboxError("unknown sealed-memory policy format")
    if policy.get("enabled") is not True:
        raise SealboxError("sealed-memory policy must set enabled=true")
    if policy.get("keyfile") != ".sealbox_key":
        raise SealboxError("sealed-memory policy keyfile must be .sealbox_key")
    if policy.get("scope") != ["memory/index", "memory/archive"]:
        raise SealboxError(
            "sealed-memory policy scope must be memory/index and memory/archive"
        )
    keyfile = (profile / policy["keyfile"]).resolve()
    if profile not in keyfile.parents:
        raise SealboxError("sealed-memory policy keyfile escapes profile")
    read_key(keyfile)
    return {**policy, "path": path, "keyfile": keyfile}


def enable_profile(profile_dir: str | Path) -> dict:
    """Seal memory idempotently, then activate the profile policy as the final write."""
    profile = Path(profile_dir).resolve()
    keyfile = profile / ".sealbox_key"
    read_key(keyfile)
    changed = seal_tree(profile)
    policy = {
        "format": POLICY_FORMAT,
        "enabled": True,
        "keyfile": ".sealbox_key",
        "scope": ["memory/index", "memory/archive"],
    }
    atomic_write_bytes(
        profile / POLICY_NAME,
        (json.dumps(policy, indent=2) + "\n").encode("utf-8"),
    )
    return {"changed": changed, "policy": str(profile / POLICY_NAME),
            "key_fingerprint": key_fingerprint(keyfile)}


def verify_tree(profile_dir: str | Path, keyfile: str | Path) -> list[str]:
    """Authenticate every in-scope memory file and return its relative path."""
    profile = Path(profile_dir).resolve()
    verified = []
    for base in (profile / "memory" / "index", profile / "memory" / "archive"):
        if not base.is_dir():
            continue
        for path in sorted(p for p in base.rglob("*") if p.is_file()):
            open_file(path, keyfile, require_sealed=True)
            verified.append(path.relative_to(profile).as_posix())
    return verified


def seal_tree(profile_dir: str | Path) -> list[str]:
    profile = Path(profile_dir)
    keyfile = profile / ".sealbox_key"
    changed = []
    for base in (profile / "memory" / "index", profile / "memory" / "archive"):
        if not base.is_dir():
            continue
        for path in sorted(p for p in base.rglob("*") if p.is_file()):
            if seal_file(path, keyfile):
                changed.append(str(path.relative_to(profile).as_posix()))
    return changed


def open_tree(profile_dir: str | Path) -> dict[str, str]:
    profile = Path(profile_dir)
    keyfile = profile / ".sealbox_key"
    opened = {}
    for base in (profile / "memory" / "index", profile / "memory" / "archive"):
        if not base.is_dir():
            continue
        for path in sorted(p for p in base.rglob("*") if p.is_file()):
            opened[path.relative_to(profile).as_posix()] = open_file(path, keyfile)
    return opened
