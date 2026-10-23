"""Config journal box (MOD-04): every setting change recorded, and every change undoable.

Michel, 2026-10-06: "it is the config changes that cause all the problems" (a 12-minute
change cost him an evening). This box is the one front door for settings: it keeps
the current values in a JSON file and appends every change to a journal beside it.

Inputs:  a settings file path; changes as (key, value, by, reason).
Outputs: current values; Change records (id, at, by, key, old, new, existed, reason);
         undo(change_id) restores the value from before that change, as a new change.
Refuses: a change without a person or agent named (by) or without a reason; values
         that are not JSON; undoing a change that a later change to the same key
         superseded (undo the later one first, so nothing is silently lost).
The journal is append-only. Writes are atomic (temporary file, then replace).
Crash-safe (write-ahead, 2026-10-08): each change is journaled and fsynced before its value is
written. If a crash comes between the two, the next read finishes the change; a torn final
journal line (a crash mid-entry) is discarded, and its value was never written.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from contextlib import contextmanager, ExitStack
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from .model_settings_store import writer_lock, StaleSettings

_MISSING = object()


class ConfigJournalError(ValueError):
    pass


@dataclass(frozen=True)
class Change:
    id: int
    at: str
    by: str
    key: str
    old: Any
    new: Any
    existed: bool              # whether the key existed before this change
    removed: bool              # whether this change removed the key
    reason: str
    undoes: Optional[int] = None


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class ConfigJournal:
    """Settings in `path` (JSON object); journal in `path` + '.journal.jsonl'."""

    def __init__(self, path, clock: Callable[[], str] = _now):
        self.path = Path(path).resolve()
        self.journal_path = self.path.with_name(self.path.name + ".journal.jsonl")
        self.clock = clock
        self._lock = threading.RLock()
        self._lock_depth = 0

    @contextmanager
    def _serialized(self):
        """One path lock across instances/processes; recursive calls reuse it."""
        with self._lock:
            if self._lock_depth:
                yield
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            deadline = time.monotonic() + 10
            with ExitStack() as stack:
                while True:
                    try:
                        stack.enter_context(writer_lock(self.path.with_name(self.path.name + '.lock')))
                        break
                    except StaleSettings as exc:
                        if time.monotonic() >= deadline:
                            raise ConfigJournalError('settings writer lock timed out; retry after inspection') from exc
                        time.sleep(.01)
                self._lock_depth = 1
                try:
                    yield
                finally:
                    self._lock_depth = 0

    # -- reading -----------------------------------------------------------
    @contextmanager
    def transaction(self):
        """Public composite-operation scope using the same reentrant path lock.

        Callers may read and change values while this scope is held; all ordinary
        journal writers participate. The existing bounded lock refusal applies.
        """
        with self._serialized():
            yield

    def all(self) -> Dict[str, Any]:
        with self._serialized():
            self._recover()
            return self._read_values()

    def _read_values(self) -> Dict[str, Any]:
        if not self.path.exists():
            return {}
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ConfigJournalError(f"{self.path} must hold a JSON object")
        return data

    def get(self, key: str, default: Any = None) -> Any:
        return self.all().get(key, default)

    def history(self, key: Optional[str] = None, limit: int = 50) -> List[Change]:
        with self._serialized():
            changes = self._changes()
        if key is not None:
            changes = [c for c in changes if c.key == key]
        return list(reversed(changes))[:limit]

    # -- changing ----------------------------------------------------------
    def set(self, key: str, value: Any, *, by: str, reason: str) -> Change:
        return self._apply(key, value, by=by, reason=reason)

    def remove(self, key: str, *, by: str, reason: str) -> Change:
        return self._apply(key, _MISSING, by=by, reason=reason)

    def undo(self, change_id: int, *, by: str, reason: str = "undo") -> Change:
        with self._serialized():
            changes = self._changes()
            target = next((c for c in changes if c.id == change_id), None)
            if target is None:
                raise ConfigJournalError(f"no change {change_id}")
            same = [c for c in changes if c.key == target.key]
            active, before = [], {}
            for change in same:
                before[change.id] = list(active)
                if change.undoes is None:
                    active.append(change.id)
                else:
                    active = list(before[change.undoes])
            if target.id != same[-1].id and (not active or active[-1] != target.id):
                raise ConfigJournalError(
                    f"change {change_id} to {target.key!r} was superseded or already undone; undo the active later change first")
            restore = target.old if target.existed else _MISSING
            return self._apply(target.key, restore, by=by, reason=reason, undoes=change_id)

    # -- internals ---------------------------------------------------------
    def _apply(self, key: str, value: Any, *, by: str, reason: str, undoes: Optional[int] = None) -> Change:
        if not isinstance(key, str) or not key.strip():
            raise ConfigJournalError("key must be a non-empty string")
        if not isinstance(by, str) or not by.strip():
            raise ConfigJournalError("say who is making the change (by)")
        if not isinstance(reason, str) or not reason.strip():
            raise ConfigJournalError("say why the change is made (reason); it is kept in the journal")
        if value is not _MISSING:
            try:
                json.dumps(value)
            except (TypeError, ValueError) as exc:
                raise ConfigJournalError(f"value for {key!r} is not JSON: {exc}") from exc
        with self._serialized():
            data = self.all()
            existed = key in data
            old = data.get(key)
            if value is _MISSING:
                if not existed:
                    raise ConfigJournalError(f"{key!r} is not set")
                del data[key]
            else:
                data[key] = value
            changes = self._changes()
            change = Change(id=(changes[-1].id + 1 if changes else 1), at=self.clock(), by=by.strip(), key=key,
                            old=old, new=None if value is _MISSING else value, existed=existed,
                            removed=value is _MISSING, reason=reason.strip(), undoes=undoes)
            # Write-ahead (2026-10-08): the journal entry first, then the value. A crash between
            # the two leaves a recorded change that the next open finishes; a crash while the entry
            # is being written leaves a torn line that is discarded. Never an unrecorded change.
            self._append(change)
            self._write_atomic(self.path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
            return change

    def _append(self, change: Change) -> None:
        self.journal_path.parent.mkdir(parents=True, exist_ok=True)
        if self.journal_path.exists():
            raw = self.journal_path.read_bytes()
            if raw and not raw.endswith(b"\n"):          # a torn entry from an interrupted write
                self._write_atomic(self.journal_path, raw[: raw.rfind(b"\n") + 1].decode("utf-8"))
        with self.journal_path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(asdict(change), ensure_ascii=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    def _recover(self) -> None:
        """Finish a change that was recorded but not applied (the value file shows the state
        just before it). A value file changed outside the journal is left as it is."""
        changes = self._changes()
        if not changes:
            return
        last = changes[-1]
        data = self._read_values()
        applied = (last.key not in data) if last.removed else (last.key in data and data[last.key] == last.new)
        if applied:
            return
        before = (last.key in data and data[last.key] == last.old) if last.existed else last.key not in data
        if before:
            if last.removed:
                del data[last.key]
            else:
                data[last.key] = last.new
            self._write_atomic(self.path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")

    def _changes(self) -> List[Change]:
        if not self.journal_path.exists():
            return []
        raw = self.journal_path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            raw = raw[:raw.rfind(b"\n") + 1]  # trim before decoding a torn UTF-8 character
        text = raw.decode('utf-8')
        out = []
        for line in text.splitlines():
            if line.strip():
                out.append(Change(**json.loads(line)))
        return out

    @staticmethod
    def _write_atomic(path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(text)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
