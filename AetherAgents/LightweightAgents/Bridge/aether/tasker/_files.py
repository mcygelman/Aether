"""Small file helpers for the Tasker Process: UTC stamps, LF text and JSON written atomically, and two byte locks.

The locks follow the codebase's pattern (roadmaps tracker `writer_lock`, `charter_lock.HostLock`): a one-byte lock on
a lock file, which the operating system releases if the process dies, so there is never a stale lock to delete.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_text(path: Path, text: str) -> None:
    """UTF-8 with LF line endings, replaced atomically."""
    write_bytes(path, text.encode("utf-8"))


def write_bytes(path: Path, data: bytes) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # a short temporary name: the target's name repeated in it once pushed a briefcase path past Windows' 260
    fd, tmp = tempfile.mkstemp(prefix=".w", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
        _replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _replace(tmp: str, path: Path) -> None:
    # A reader holding the file open on Windows makes os.replace fail for a moment; retry briefly.
    for attempt in range(50):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == 49:
                raise
            time.sleep(0.02)


def read_json(path: Path, default=None):
    # On Windows a reader that opens the file at the instant os.replace swaps it is refused for a moment; retry.
    for attempt in range(100):
        try:
            return json.loads(Path(path).read_text(encoding="utf-8"))
        except FileNotFoundError:
            return default
        except PermissionError:
            if attempt == 99:
                raise
            time.sleep(0.01)


def write_json(path: Path, data) -> None:
    write_text(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def _lock_byte(stream) -> None:
    if os.name == "nt":
        import msvcrt
        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_byte(stream) -> None:
    stream.seek(0)
    if os.name == "nt":
        import msvcrt
        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _open_lock(path: Path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    stream = path.open("a+b")
    if stream.seek(0, os.SEEK_END) == 0:
        stream.write(b"0")
        stream.flush()
    stream.seek(0)
    return stream


@contextmanager
def file_lock(path: Path, timeout: float = 30.0):
    """Wait for the lock (across threads and processes), hold it for the block, then release it."""
    stream = _open_lock(path)
    deadline = time.monotonic() + timeout
    try:
        while True:
            try:
                _lock_byte(stream)
                break
            except OSError:
                if time.monotonic() > deadline:
                    raise TimeoutError(f"{path} stayed locked for {timeout:.0f} s")
                time.sleep(0.01)
        try:
            yield
        finally:
            _unlock_byte(stream)
    finally:
        stream.close()


class ProcessLock:
    """Held for a process's lifetime; a second holder is refused at once."""

    def __init__(self, path: Path, refusal: str):
        self.stream = _open_lock(path)
        try:
            _lock_byte(self.stream)
        except OSError:
            self.stream.close()
            raise RuntimeError(refusal) from None

    def release(self) -> None:
        if self.stream is not None and not self.stream.closed:
            try:
                _unlock_byte(self.stream)
            except OSError:
                pass
            self.stream.close()
