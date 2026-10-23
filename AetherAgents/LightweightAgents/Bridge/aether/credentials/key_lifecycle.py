"""Operator-controlled Sealbox key backup, restore, rotation, and status."""
from __future__ import annotations

import base64
import hmac
import json
import os
import secrets
import time
from hashlib import scrypt, sha256
from pathlib import Path

from aether.integrity import sealbox

FORMAT = "AETHER-KEY-BACKUP-1"
STATUS_NAME = "KEY_LIFECYCLE.json"
VALID_STATES = {"ACTIVE", "COMPROMISED", "LOST", "RETIRED"}


class KeyLifecycleError(Exception):
    pass


def _secret(value: str) -> bytes:
    if not isinstance(value, str) or len(value) < 12:
        raise KeyLifecycleError("backup secret must contain at least 12 characters")
    return value.encode("utf-8")


def _derive(secret: str, salt: bytes) -> tuple[bytes, bytes]:
    master = scrypt(_secret(secret), salt=salt, n=2**14, r=8, p=1, dklen=64)
    return master[:32], master[32:]


def _crypt(data: bytes, key: bytes, nonce: bytes) -> bytes:
    return sealbox.crypt(data, key, nonce)


def _write_status(profile: Path, state: str, fingerprint: str | None,
                  detail: str, authorized_by: str) -> dict:
    if state not in VALID_STATES or not authorized_by.strip():
        raise KeyLifecycleError("valid state and operator attribution are required")
    record = {
        "format": "AETHER-KEY-LIFECYCLE-1",
        "state": state,
        "key_fingerprint": fingerprint,
        "detail": detail,
        "authorized_by": authorized_by.strip(),
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    sealbox.atomic_write_bytes(
        profile / STATUS_NAME,
        (json.dumps(record, indent=2) + "\n").encode("utf-8"),
    )
    return record


def backup_key(profile_dir: str | Path, destination: str | Path,
               secret: str, authorized_by: str) -> dict:
    if not authorized_by.strip():
        raise KeyLifecycleError("key backup requires operator attribution")
    profile = Path(profile_dir).resolve()
    destination = Path(destination).resolve()
    if destination == profile or profile in destination.parents:
        raise KeyLifecycleError("key backup must be stored outside the profile")
    key = sealbox.read_key(profile / ".sealbox_key")
    salt, nonce = secrets.token_bytes(16), secrets.token_bytes(16)
    enc, mac = _derive(secret, salt)
    ciphertext = _crypt(key, enc, nonce)
    tag = hmac.new(mac, salt + nonce + ciphertext, sha256).digest()
    payload = {
        "format": FORMAT,
        "profile": profile.name,
        "key_fingerprint": sha256(key).hexdigest(),
        "salt": salt.hex(), "nonce": nonce.hex(), "tag": tag.hex(),
        "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "authorized_by": authorized_by.strip(),
    }
    if destination.exists():
        raise KeyLifecycleError(f"backup already exists: {destination}")
    sealbox.atomic_write_bytes(
        destination, (json.dumps(payload, indent=2) + "\n").encode("utf-8")
    )
    return {"backup": str(destination), "key_fingerprint": payload["key_fingerprint"]}


def _open_backup(path: Path, secret: str) -> tuple[dict, bytes]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("format") != FORMAT:
            raise KeyLifecycleError("unknown key backup format")
        salt, nonce = bytes.fromhex(data["salt"]), bytes.fromhex(data["nonce"])
        ciphertext = base64.b64decode(data["ciphertext"], validate=True)
        tag = bytes.fromhex(data["tag"])
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        raise KeyLifecycleError(f"invalid key backup: {exc}") from exc
    enc, mac = _derive(secret, salt)
    expected = hmac.new(mac, salt + nonce + ciphertext, sha256).digest()
    if not hmac.compare_digest(tag, expected):
        raise KeyLifecycleError("key backup authentication failed")
    key = _crypt(ciphertext, enc, nonce)
    if len(key) != 32 or sha256(key).hexdigest() != data.get("key_fingerprint"):
        raise KeyLifecycleError("key backup fingerprint mismatch")
    return data, key


def restore_key(profile_dir: str | Path, backup: str | Path, secret: str,
                authorized_by: str) -> dict:
    profile = Path(profile_dir).resolve()
    keyfile = profile / ".sealbox_key"
    if keyfile.exists():
        raise KeyLifecycleError("restore refuses to overwrite an existing key")
    data, key = _open_backup(Path(backup).resolve(), secret)
    if data.get("profile") != profile.name:
        raise KeyLifecycleError(
            f"backup belongs to profile {data.get('profile')!r}, not {profile.name!r}"
        )
    sealbox.atomic_write_bytes(keyfile, key.hex().encode("ascii") + b"\n")
    try:
        if sealbox.load_policy(profile):
            sealbox.verify_tree(profile, keyfile)
    except Exception:
        keyfile.unlink(missing_ok=True)
        raise
    return _write_status(
        profile, "ACTIVE", data["key_fingerprint"],
        "restored from authenticated backup", authorized_by,
    )


def mark_state(profile_dir: str | Path, state: str, detail: str,
               authorized_by: str) -> dict:
    profile = Path(profile_dir).resolve()
    key_exists = (profile / ".sealbox_key").is_file()
    if state == "ACTIVE" and not key_exists:
        raise KeyLifecycleError("ACTIVE state requires a readable profile key")
    if state == "LOST" and key_exists:
        raise KeyLifecycleError("LOST state requires the profile key to be absent")
    fingerprint = (
        sealbox.key_fingerprint(profile / ".sealbox_key")
        if key_exists else None
    )
    return _write_status(profile, state, fingerprint, detail, authorized_by)


def rotate_key(profile_dir: str | Path, authorized_by: str) -> dict:
    profile = Path(profile_dir).resolve()
    old_keyfile = profile / ".sealbox_key"
    old_key = old_keyfile.read_bytes()
    policy = sealbox.load_policy(profile)
    if policy is None:
        raise KeyLifecycleError("rotation requires an enabled sealed-memory policy")
    paths = [
        path for base in (profile / "memory" / "index", profile / "memory" / "archive")
        if base.is_dir() for path in sorted(p for p in base.rglob("*") if p.is_file())
    ]
    plaintext = {
        path: sealbox.open_bytes(path.read_bytes(), old_keyfile, require_sealed=True)
        for path in paths
    }
    originals = {path: path.read_bytes() for path in paths}
    temporary_key = profile / f".sealbox_key.rotate.{secrets.token_hex(6)}"
    sealbox.atomic_write_bytes(
        temporary_key, secrets.token_bytes(32).hex().encode("ascii") + b"\n"
    )
    staged = {path: sealbox.seal_bytes(data, temporary_key) for path, data in plaintext.items()}
    try:
        for path, raw in staged.items():
            sealbox.atomic_write_bytes(path, raw)
        sealbox.atomic_write_bytes(old_keyfile, temporary_key.read_bytes())
        sealbox.verify_tree(profile, old_keyfile)
    except Exception:
        for path, raw in originals.items():
            sealbox.atomic_write_bytes(path, raw)
        sealbox.atomic_write_bytes(old_keyfile, old_key)
        raise
    finally:
        temporary_key.unlink(missing_ok=True)
    fingerprint = sealbox.key_fingerprint(old_keyfile)
    _write_status(profile, "ACTIVE", fingerprint, "transactional key rotation", authorized_by)
    return {"rotated_files": len(paths), "key_fingerprint": fingerprint}
