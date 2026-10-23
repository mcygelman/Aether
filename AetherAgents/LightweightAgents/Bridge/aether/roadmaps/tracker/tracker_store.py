"""Validated, atomic persistence for the Roadmap Tracker."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any


ROADMAP_STATUSES = ("Active", "Planned", "Completed", "Aborted")
SPRINT_STATUSES = ("Planned", "Open", "Closed", "Aborted")
ROOT_FIELDS = {"schemaVersion", "roadmaps", "sprints"}
ROADMAP_FIELDS = {"id", "name", "description", "status", "publication"}
SPRINT_FIELDS = {"id", "roadmapId", "name", "description", "status", "sequence"}
SCHEMA_VERSIONS = (1, 2)
# Schema 2 (Modularization MOD-02): status lives in fields, not in growing descriptions.
TRACKING_FIELDS = {"owner", "updated", "next", "evidence", "gates", "log"}
MISDECODED = ("\u00e2\u20ac", "\u00e2\u2020")          # UTF-8 read as Windows-1252: "â€", "â†"


class DataValidationError(ValueError):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


class RevisionConflictError(RuntimeError):
    pass


@contextmanager
def writer_lock(path: Path):
    """Serialize cooperating tracker processes around revision check and replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as stream:
        if stream.seek(0, os.SEEK_END) == 0:
            stream.write(b'0'); stream.flush()
        stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RevisionConflictError('Another tracker is saving; refresh and retry your edit') from exc
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == 'nt':msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _required_string(record: dict[str, Any], field: str, label: str, errors: list[str]) -> None:
    value = record.get(field)
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{label}.{field} must be a non-empty string")


def _is_day(value: Any) -> bool:
    import re
    return isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) is not None


def _check_tracking(record: dict[str, Any], label: str, errors: list[str]) -> None:
    """Schema 2 tracking fields: who owns it, when it was updated, what is next, evidence, gates, a short log."""
    for field in ("owner", "next"):
        if field in record and (not isinstance(record[field], str) or not record[field].strip()):
            errors.append(f"{label}.{field} must be a non-empty string")
    if "updated" in record and not _is_day(record["updated"]):
        errors.append(f"{label}.updated must be a date (YYYY-MM-DD)")
    for field in ("evidence", "gates"):
        if field in record and (not isinstance(record[field], list)
                                or not all(isinstance(v, str) and v.strip() for v in record[field])):
            errors.append(f"{label}.{field} must be a list of non-empty strings")
    if "log" in record:
        log = record["log"]
        if not isinstance(log, list):
            errors.append(f"{label}.log must be a list")
        else:
            for n, item in enumerate(log):
                if (not isinstance(item, dict) or set(item) - {"date", "by", "text"} or not _is_day(item.get("date"))
                        or not isinstance(item.get("text"), str) or not item["text"].strip()
                        or ("by" in item and (not isinstance(item["by"], str) or not item["by"].strip()))):
                    errors.append(f"{label}.log[{n}] must be {{date: YYYY-MM-DD, text, optional by}}")


def _check_encoding(value: Any, label: str, errors: list[str]) -> None:
    """Refuse mis-decoded text, so the 2026-10-06 cleanup (214 damaged fields) cannot recur."""
    if isinstance(value, str):
        if any(marker in value for marker in MISDECODED):
            errors.append(f"{label} contains mis-decoded text (UTF-8 read as Windows-1252); repair it before saving")
    elif isinstance(value, dict):
        for key, item in value.items():
            _check_encoding(item, f"{label}.{key}", errors)
    elif isinstance(value, list):
        for n, item in enumerate(value):
            _check_encoding(item, f"{label}[{n}]", errors)


def migrate_to_v2(data: dict[str, Any]) -> dict[str, Any]:
    """Schema 1 -> 2: same records, version raised; tracking fields become available. Pure, no I/O."""
    out = json.loads(json.dumps(data))
    out["schemaVersion"] = 2
    return out


def validate_data(data: Any) -> list[str]:
    """Return all model errors; an empty list means the document is valid."""
    errors: list[str] = []
    if not isinstance(data, dict):
        return ["root must be a JSON object"]

    unexpected_root = sorted(set(data) - ROOT_FIELDS)
    if unexpected_root:
        errors.append(f"root contains unexpected fields: {', '.join(unexpected_root)}")

    version = data.get("schemaVersion")
    if type(version) is not int or version not in SCHEMA_VERSIONS:
        errors.append("schemaVersion must be 1 or 2")
        version = 1
    allowed_extra = TRACKING_FIELDS if version == 2 else set()

    roadmaps = data.get("roadmaps")
    sprints = data.get("sprints")
    if not isinstance(roadmaps, list):
        errors.append("roadmaps must be an array")
        roadmaps = []
    if not isinstance(sprints, list):
        errors.append("sprints must be an array")
        sprints = []

    roadmap_ids: set[str] = set()
    roadmap_records: dict[str, dict[str, Any]] = {}
    for index, roadmap in enumerate(roadmaps):
        label = f"roadmaps[{index}]"
        if not isinstance(roadmap, dict):
            errors.append(f"{label} must be an object")
            continue
        unexpected = sorted(set(roadmap) - ROADMAP_FIELDS - allowed_extra)
        if unexpected:
            errors.append(f"{label} contains unexpected fields: {', '.join(unexpected)}")
        for field in ("id", "name", "description"):
            _required_string(roadmap, field, label, errors)
        roadmap_id = roadmap.get("id")
        if isinstance(roadmap_id, str) and roadmap_id.strip():
            if roadmap_id in roadmap_ids:
                errors.append(f"duplicate Roadmap id: {roadmap_id}")
            roadmap_ids.add(roadmap_id)
            roadmap_records[roadmap_id] = roadmap
        if roadmap.get("status") not in ROADMAP_STATUSES:
            errors.append(f"{label}.status must be one of: {', '.join(ROADMAP_STATUSES)}")
        if 'publication' in roadmap:
            p = roadmap['publication']
            if (not isinstance(p, dict) or set(p) != {'schema', 'project_id', 'plan_sha256', 'approved_plan', 'ruling'} |
                    ({'plan_change'} if p.get('schema') == 'aether-roadmap-publication-2' else set())
                    or p.get('schema') not in ('aether-roadmap-publication-1', 'aether-roadmap-publication-2')
                    or (p.get('schema') == 'aether-roadmap-publication-2' and not isinstance(p.get('plan_change'), dict))
                    or not isinstance(p.get('project_id'), str) or not p['project_id']
                    or not isinstance(p.get('plan_sha256'), str) or len(p['plan_sha256']) != 64
                    or not isinstance(p.get('approved_plan'), dict) or not isinstance(p.get('ruling'), dict)):
                errors.append(f'{label}.publication must be an exact retained plan publication')

    sprint_ids: set[str] = set()
    sprint_statuses_by_roadmap: dict[str, list[str]] = {}
    sequences: set[tuple[str, int]] = set()
    for index, sprint in enumerate(sprints):
        label = f"sprints[{index}]"
        if not isinstance(sprint, dict):
            errors.append(f"{label} must be an object")
            continue
        unexpected = sorted(set(sprint) - SPRINT_FIELDS - allowed_extra)
        if unexpected:
            errors.append(f"{label} contains unexpected fields: {', '.join(unexpected)}")
        for field in ("id", "roadmapId", "name", "description"):
            _required_string(sprint, field, label, errors)
        sprint_id = sprint.get("id")
        if isinstance(sprint_id, str) and sprint_id.strip():
            if sprint_id in sprint_ids:
                errors.append(f"duplicate Sprint id: {sprint_id}")
            sprint_ids.add(sprint_id)
        roadmap_id = sprint.get("roadmapId")
        if isinstance(roadmap_id, str) and roadmap_id.strip() and roadmap_id not in roadmap_ids:
            errors.append(f"{label}.roadmapId references missing Roadmap: {roadmap_id}")
        if sprint.get("status") not in SPRINT_STATUSES:
            errors.append(f"{label}.status must be one of: {', '.join(SPRINT_STATUSES)}")
        elif isinstance(roadmap_id, str) and roadmap_id.strip():
            sprint_statuses_by_roadmap.setdefault(roadmap_id, []).append(sprint["status"])
        sequence = sprint.get("sequence")
        if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 1:
            errors.append(f"{label}.sequence must be an integer of 1 or greater")
        elif isinstance(roadmap_id, str) and roadmap_id.strip():
            key = (roadmap_id, sequence)
            if key in sequences:
                errors.append(f"duplicate Sprint sequence {sequence} in Roadmap {roadmap_id}")
            sequences.add(key)

    for roadmap_id, roadmap in roadmap_records.items():
        status = roadmap.get("status")
        sprint_statuses = sprint_statuses_by_roadmap.get(roadmap_id, [])
        unfinished = [value for value in sprint_statuses if value in ("Planned", "Open")]
        if status == "Active" and not unfinished:
            errors.append(f"Active Roadmap {roadmap_id} must own at least one unfinished Sprint")
        if status in ("Completed", "Aborted") and unfinished:
            errors.append(f"{status} Roadmap {roadmap_id} cannot own unfinished Sprints")

    for kind, records in (("roadmaps", roadmaps), ("sprints", sprints)):
        for index, record in enumerate(records):
            if isinstance(record, dict):
                _check_tracking(record, f"{kind}[{index}]", errors)
    _check_encoding(data, "data", errors)
    return errors


def revision_for(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def keeps_crlf(path: Path) -> bool:
    """True when an existing file uses Windows line endings, so a save does not rewrite every line."""
    try:
        with open(path, "rb") as handle:
            return b"\r\n" in handle.read(4096)
    except OSError:
        return False


class RoadmapStore:
    """Thread-safe JSON store with optimistic concurrency and atomic replacement."""

    def __init__(self, path: Path):
        self.path = Path(path).resolve()
        self._lock = threading.RLock()

    def _read_unlocked(self) -> tuple[dict[str, Any], str]:
        if not self.path.exists():
            return {"schemaVersion": 1, "roadmaps": [], "sprints": []}, revision_for(b'')
        raw = self.path.read_bytes()
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DataValidationError([f"roadmaps.json is not valid UTF-8 JSON: {exc}"]) from exc
        errors = validate_data(data)
        if errors:
            raise DataValidationError(errors)
        return data, revision_for(raw)

    def read(self) -> tuple[dict[str, Any], str]:
        with self._lock:
            return self._read_unlocked()

    def write(self, data: Any, expected_revision: str | None = None, *, publish: bool = False) -> str:
        errors = validate_data(data)
        if errors:
            raise DataValidationError(errors)

        with self._lock, writer_lock(self.path.with_name(self.path.name + '.lock')):
            current, current_revision = self._read_unlocked()
            if expected_revision is not None:
                if current_revision != expected_revision:
                    raise RevisionConflictError(
                        "roadmaps.json changed outside this browser; refresh before saving"
                    )
            if not publish:
                def publications(document):
                    return {r['id']: {k: v for k, v in r.items() if k != 'status'}
                            for r in document['roadmaps'] if 'publication' in r}
                before, after = publications(current), publications(data)
                ids = set(before) | set(after)
                def chain(document):
                    return {s['id']: {k: v for k, v in s.items() if k != 'status'}
                            for s in document['sprints'] if s['roadmapId'] in ids}
                if before != after or chain(current) != chain(data):
                    raise DataValidationError(['Published plans and their Sprint chains are retained evidence. Publish a newly approved plan to revise them; only tracking status is editable.'])

            text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
            if keeps_crlf(self.path):          # keep the file's line endings (the canonical tracker is CRLF)
                text = text.replace("\n", "\r\n")
            encoded = text.encode("utf-8")
            self.path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent
            )
            try:
                with os.fdopen(descriptor, "wb") as temporary:
                    temporary.write(encoded)
                    temporary.flush()
                    os.fsync(temporary.fileno())
                os.replace(temporary_name, self.path)
            finally:
                if os.path.exists(temporary_name):
                    os.unlink(temporary_name)
            return revision_for(encoded)
