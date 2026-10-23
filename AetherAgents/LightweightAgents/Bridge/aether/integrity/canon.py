"""AETHER-CANON-1 + SHA-256 helpers.

Byte-for-byte port of wrapper/src/core/canon.rs (S00051). There is exactly ONE
canonicalizer in the codebase and everything that hashes routes through it.
Canonical form: CRLF/CR -> LF, then NFC, then UTF-8 (no BOM).
"""
import hashlib
import unicodedata


def canon_bytes(text: str) -> bytes:
    lf = text.replace("\r\n", "\n").replace("\r", "\n")
    return unicodedata.normalize("NFC", lf).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canon_sha(text: str) -> str:
    return sha256_hex(canon_bytes(text))


def decode_utf8(data: bytes) -> str:
    """Decode file bytes, stripping a UTF-8 BOM if present. BOM handling lives here only."""
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    return data.decode("utf-8")
