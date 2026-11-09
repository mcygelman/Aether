"""The Staff Home (contract §9): where every lightweight identity lives, including during sprints.

    <data root>/staff/
      roster.json                          id, name, role, file, resume, model_rec, state (available | on:<sprint id>)
      <agent id>/Identity_<Role>_<Name>.txt   the home version: one file, System 0, 1, 2
      backups.sqlite                       every replaced version: versions(id, agent_id, sprint_id, at, kind, sha256, text)

Hire reserves the home identity without copying it into the sprint. identity_close stages the agent's own
closing words under its Staff Home; write-back preserves System 0 and backs up both versions before saving.
Never put an identity in a briefcase, overwrite a home changed since closure began, or apply a closure twice.
"""
from __future__ import annotations

import hashlib
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

from . import identity
from ._files import file_lock, now, read_json, write_json, write_text
from .sprint import Sprint

# The shipped standard staff: seven fresh identities with an empty System 2 (build_staff.py --product).
PRODUCT_STAFF = Path(__file__).resolve().parent / "assets" / "staff"
CLOSED_RECORD = "identity_closed.json"
BEFORE_CLOSE = "identity_before_close.txt"   # kept at home so a repeated identity_close re-files from one baseline


class StaffError(ValueError):
    """A Staff Home operation that cannot go ahead; the message says why."""


class ClosureFormatError(StaffError):
    """Authored closure shape is invalid; no draft or identity has been written."""


@dataclass
class WriteBack:
    agent_id: str
    done: bool
    lines: list = field(default_factory=list)   # what the Overseer and timer.log are told


def staff_root(data_root) -> Path:
    return Path(data_root) / "staff"


def _roster_path(data_root) -> Path:
    return staff_root(data_root) / "roster.json"


def _lock(data_root):
    return file_lock(staff_root(data_root) / ".roster.lock")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def roster(data_root) -> list:
    """The Staff Home roster. A member still marked on a sprint that was stopped for good (its terminal receipt; it
    never resumes, so it never writes them back) reads as available: found in the 11-07 opus12 refactor, a sprint
    stopped by its output check kept its staff forever, and no later sprint could hire them."""
    entries = list((read_json(_roster_path(data_root), {}) or {}).get("staff", []))
    for e in entries:
        state = e.get("state", "") if isinstance(e, dict) else ""
        if state.startswith("on:") and (Path(data_root) / "sprints" / state[3:] / "timer" / "RECOVERY_STOP.json").exists():
            e["state"] = "available"
    return entries


def _save(data_root, entries: list) -> None:
    write_json(_roster_path(data_root), {"schema": "AETHER-STAFF-ROSTER-1", "staff": entries})


def entry(data_root, agent_id: str) -> dict:
    for e in roster(data_root):
        if e["id"] == agent_id:
            return e
    raise StaffError(f"no staff agent {agent_id!r} in the Staff Home; staff_list() shows who is there")


def home_file(data_root, agent_id: str) -> Path:
    e = entry(data_root, agent_id)
    return staff_root(data_root) / agent_id / e["file"]


def _roster_entry(text: str, filename: str, agent_id: str) -> dict:
    name, role = identity.name_and_role(text, filename)
    found = identity.asserts(text)
    ptrs = identity.pointers(text)
    model = ptrs.get("MODEL_REC", "")
    return {
        "id": agent_id,
        "name": name,
        "role": role,
        "file": filename,
        "resume": found.get("SCAN_LINE") or found.get("AGENT_METHOD", "")[:200],
        "model_rec": model if isinstance(model, str) else "; ".join(model),
        "state": "available",
    }


def seed_staff(data_root, source_dir, *, only: Optional[Iterable[str]] = None) -> list:
    """Copy one-file identities (Identity_<Role>_<Name>.txt) into the Staff Home. An agent already at home is left
    as it is. Returns the ids added."""
    source_dir = Path(source_dir)
    files = sorted(source_dir.glob("Identity_*.txt"))
    if not files:
        raise StaffError(f"no Identity_*.txt in {source_dir}")
    wanted = {a.lower() for a in only} if only else None
    added = []
    with _lock(data_root):
        entries = roster(data_root)
        known = {e["id"] for e in entries}
        for path in files:
            text = path.read_text(encoding="utf-8")
            identity.require(text)
            name, _role = identity.name_and_role(text, path.name)
            agent_id = name.lower()
            if (wanted and agent_id not in wanted) or agent_id in known:
                continue
            write_text(staff_root(data_root) / agent_id / path.name, text)
            entries.append(_roster_entry(text, path.name, agent_id))
            known.add(agent_id)
            added.append(agent_id)
        _save(data_root, entries)
    return added


def add_identity(data_root, filename: str, text: str) -> str:
    """Add one new identity (Create a specialist) to the Staff Home and return its id. An agent already at home is
    never replaced: the same name is refused."""
    identity.require(text)
    if not re.fullmatch(r"Identity_[A-Za-z0-9]+_[A-Za-z0-9]+\.txt", filename):
        raise StaffError("an identity file is named Identity_<Role>_<Name>.txt")
    name, _role = identity.name_and_role(text, filename)
    agent_id = name.lower()
    with _lock(data_root):
        entries = roster(data_root)
        if any(e["id"] == agent_id for e in entries) or (staff_root(data_root) / agent_id).exists():
            raise StaffError(f"{name} is already in the Staff Home; choose another name")
        write_text(staff_root(data_root) / agent_id / filename, text)
        entries.append(_roster_entry(text, filename, agent_id))
        _save(data_root, entries)
    return agent_id


def hire(sprint: Sprint, agent_ids: Iterable[str]) -> list:
    """Reserve each home identity for this sprint. No identity is copied into the working folder."""
    agent_ids = [a.strip() for a in agent_ids if a and a.strip()]
    if not agent_ids:
        raise StaffError("name at least one staff agent to hire")
    data_root = sprint.data_root
    with _lock(data_root):
        entries = roster(data_root)
        by_id = {e["id"]: e for e in entries}
        refusals = []
        for a in agent_ids:
            e = by_id.get(a)
            if e is None:
                refusals.append(f"{a} is not in the Staff Home")
            elif e["state"] not in ("available", f"on:{sprint.id}"):
                refusals.append(f"{a} is {e['state'].replace('on:', 'on sprint ')}; one sprint at a time")
        if refusals:
            raise StaffError("; ".join(refusals) + ". Nobody was hired; choose from staff_list()")
        for a in agent_ids:
            e = by_id[a]
            if e["state"] == f"on:{sprint.id}":
                continue
            if not (staff_root(data_root) / a / e["file"]).is_file():
                raise StaffError(f"{a}'s Staff Home identity is missing")
            e["state"] = f"on:{sprint.id}"
        _save(data_root, entries)
    sprint.update(lambda d: d.__setitem__("staff", list(dict.fromkeys(list(d.get("staff", [])) + agent_ids))))
    return agent_ids


def closure_folder(sprint: Sprint, agent_id: str) -> Path:
    entry(sprint.data_root, agent_id)
    return staff_root(sprint.data_root) / agent_id / "closures" / sprint.id


def closure_file(sprint: Sprint, agent_id: str) -> Path:
    return closure_folder(sprint, agent_id) / "pending_identity.txt"


def record_closure(sprint: Sprint, agent_id: str, text: str) -> None:
    """Written by identity_close after it files the agent's words: the write-back looks for it."""
    folder = closure_folder(sprint, agent_id)
    write_json(folder / CLOSED_RECORD, {"at": now(), "sha256": _sha(text),
               "base_sha256": _sha((folder / BEFORE_CLOSE).read_text(encoding="utf-8"))})


def close_identity(sprint: Sprint, agent_id: str, s1: str, s2_entry: str) -> dict:
    """identity_close: stage the agent's new System 1 and one reflection in Staff Home, exactly as given.
    The shape is checked and a wrong one is answered with what to fix; nothing is ever reworded. Calling again
    re-files from the copy as it was before the first call, so the last call is the one that counts."""
    s1, s2_entry = s1 or "", s2_entry or ""
    problems = []
    if "∆WMC{" not in s1 or "}∆WMC" not in s1:
        problems.append("s1 must be one ∆WMC{ … }∆WMC block")
    elif "WORLD_MODEL" not in s1:
        problems.append("s1's ∆WMC block needs a WORLD_MODEL := ⌜…⌝ line")
    if any(m in s1 for m in ("⟪SYSTEM_1_BEGIN⟫", "⟪SYSTEM_1_END⟫", "⟪SYSTEM_0_BEGIN⟫", "⟪SYSTEM_2_BEGIN⟫")):
        problems.append("s1 is only what goes inside System 1: leave out the ⟪SYSTEM_…⟫ markers")
    for key in ("[GOALS]", "[NEXT_ACTION]"):
        if key not in s1:
            problems.append(f"s1 needs {key}")
    problems += [f"s2_entry: {p}" for p in identity.entry_problems(s2_entry)]
    if problems:
        raise ClosureFormatError("; ".join(problems) + ". Nothing was filed")
    with _lock(sprint.data_root):
        if entry(sprint.data_root, agent_id)["state"] != f"on:{sprint.id}":
            raise StaffError(f"{agent_id} is not reserved for this sprint; no identity changed")
        folder = closure_folder(sprint, agent_id)
        if (read_json(folder / CLOSED_RECORD, {}) or {}).get("applied"):
            raise StaffError("this closure is already saved at home")
        before = folder / BEFORE_CLOSE
        if not before.is_file():
            write_text(before, home_file(sprint.data_root, agent_id).read_text(encoding="utf-8"))
        text = before.read_text(encoding="utf-8")
        inner = "\n⟪TYPE::DYNAMIC_WORKSPACE_BLOCK⟫\n\n" + s1 + ("" if s1.endswith("\n") else "\n") + "\n"
        text = identity.replace_system(text, 1, inner)
        text = identity.add_reflection(text, s2_entry)
        write_text(closure_file(sprint, agent_id), text)
        record_closure(sprint, agent_id, text)
    entry_id = (s2_entry.split("[ID] :=", 1)[1].split("\n", 1)[0].strip() if "[ID] :=" in s2_entry else "")
    return {"system1_chars": len(s1), "reflection": entry_id}


def closed(sprint: Sprint, agent_id: str) -> bool:
    return (closure_folder(sprint, agent_id) / CLOSED_RECORD).is_file()


def write_back(sprint: Sprint, agent_id: str) -> WriteBack:
    """Save the home-staged closure under the roster lock, with backups and an idempotent receipt.
    No identity_close, no write-back. A changed home is never silently overwritten."""
    data_root = sprint.data_root
    folder = closure_folder(sprint, agent_id)
    copy_path = closure_file(sprint, agent_id)
    receipt_path = folder / CLOSED_RECORD
    home_path = home_file(data_root, agent_id)
    with _lock(data_root):
        receipt = read_json(receipt_path, {}) or {}
        if receipt.get("applied"):
            entries = roster(data_root)
            for owner in entries:
                if owner['id'] == agent_id and owner['state'] == f'on:{sprint.id}':
                    owner['state'] = 'available'
            _save(data_root, entries)
            copy_path.unlink(missing_ok=True)
            (folder / BEFORE_CLOSE).unlink(missing_ok=True)
            return WriteBack(agent_id, True, receipt["lines"])
        if not receipt or not copy_path.is_file():
            return WriteBack(agent_id, False, [f"{agent_id}: no identity_close this sprint; home identity untouched"])
        entries = roster(data_root)
        owner = next(e for e in entries if e["id"] == agent_id)
        if owner["state"] != f"on:{sprint.id}":
            return WriteBack(agent_id, False, [f"{agent_id}: no longer reserved for this sprint; home untouched"])
        home = home_path.read_text(encoding="utf-8")
        # Recover an interrupted save whose atomic home replacement already completed.
        if receipt.get("result_sha256") == _sha(home):
            lines = receipt["lines"]
        elif receipt.get("base_sha256") != _sha(home):
            return WriteBack(agent_id, False, [f"{agent_id}: home changed since closure began; home untouched, draft kept at home"])
        else:
            outcome = _apply_closure(sprint, agent_id, home_path, home, copy_path, receipt_path, receipt)
            if not outcome.done:
                return outcome
            lines = outcome.lines
        receipt = read_json(receipt_path)
        receipt.update(applied=True, lines=lines)
        write_json(receipt_path, receipt)
        owner["state"] = "available"
        _save(data_root, entries)
        copy_path.unlink(missing_ok=True)
        (folder / BEFORE_CLOSE).unlink(missing_ok=True)
        return WriteBack(agent_id, True, lines)


def _apply_closure(sprint, agent_id, home_path, home, copy_path, receipt_path, receipt):
    """Called only while holding the Staff Home roster lock."""
    copy = copy_path.read_text(encoding="utf-8")
    if _sha(copy) != receipt["sha256"]:
        return WriteBack(agent_id, False, [f"{agent_id}: closure draft changed after filing; home untouched"])
    found = identity.problems(copy)
    if found:
        return WriteBack(agent_id, False, [
            f"{agent_id}: closure draft is not a well-formed identity ({'; '.join(found)}); home untouched"])
    lines = []
    if identity.system(copy, 0) != identity.system(home, 0):
        lines.append(f"{agent_id}: closure draft's System 0 differs from home; home System 0 kept (draft backed up)")
    known = set(identity.reflection_entries(home))
    new_entries = [e for e in identity.reflection_entries(copy) if e not in known]
    result = identity.replace_system(home, 1, identity.system(copy, 1))
    for e in reversed(new_entries):
        result = identity.add_reflection(result, e)
    # 'sprint-copy' is the legacy backup kind; it now holds the home-staged closure version.
    backups = [_backup(sprint.data_root, agent_id, sprint.id, "home-before", home),
               _backup(sprint.data_root, agent_id, sprint.id, "sprint-copy", copy)]
    lines.insert(0, f"{agent_id}: System 1 replaced, {len(new_entries)} reflection(s) added to home")
    write_json(receipt_path, dict(receipt, result_sha256=_sha(result), backups=backups, lines=lines))
    write_text(home_path, result)
    return WriteBack(agent_id, True, lines)


def release(sprint: Sprint, agent_id: str) -> None:
    with _lock(sprint.data_root):
        entries = roster(sprint.data_root)
        for e in entries:
            if e["id"] == agent_id and e["state"] == f"on:{sprint.id}":
                e["state"] = "available"
        _save(sprint.data_root, entries)


# ---- backups.sqlite ------------------------------------------------------------------------------------------------

def _backups(data_root) -> sqlite3.Connection:
    path = staff_root(data_root) / "backups.sqlite"
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30)
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("CREATE TABLE IF NOT EXISTS versions (id INTEGER PRIMARY KEY AUTOINCREMENT, agent_id TEXT NOT NULL,"
                 " sprint_id TEXT NOT NULL, at TEXT NOT NULL, kind TEXT NOT NULL"
                 " CHECK (kind IN ('home-before', 'sprint-copy')), sha256 TEXT NOT NULL, text TEXT NOT NULL)")
    return conn


def _backup(data_root, agent_id: str, sprint_id: str, kind: str, text: str) -> int:
    conn = _backups(data_root)
    try:
        with conn:
            cur = conn.execute("INSERT INTO versions (agent_id, sprint_id, at, kind, sha256, text)"
                               " VALUES (?, ?, ?, ?, ?, ?)", (agent_id, sprint_id, now(), kind, _sha(text), text))
            return cur.lastrowid
    finally:
        conn.close()


def versions(data_root, agent_id: str) -> list:
    conn = _backups(data_root)
    try:
        rows = conn.execute("SELECT id, sprint_id, at, kind, sha256 FROM versions WHERE agent_id = ? ORDER BY id",
                            (agent_id,)).fetchall()
        return [dict(zip(("id", "sprint_id", "at", "kind", "sha256"), r)) for r in rows]
    finally:
        conn.close()


def was_home(data_root, agent_id: str, sha256: str) -> bool:
    """True when that exact text was this member's home until a later save backed it up. Read only."""
    path = staff_root(data_root) / "backups.sqlite"
    if not path.is_file():
        return False
    conn = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, timeout=30)
    try:
        return conn.execute("SELECT 1 FROM versions WHERE agent_id = ? AND kind = 'home-before' AND sha256 = ? LIMIT 1",
                            (agent_id, sha256)).fetchone() is not None
    finally:
        conn.close()


def restore(data_root, agent_id: str, version_id: int) -> str:
    """Put a backed-up version back as the home file. The version it replaces is backed up first."""
    conn = _backups(data_root)
    try:
        row = conn.execute("SELECT text FROM versions WHERE id = ? AND agent_id = ?", (int(version_id), agent_id)).fetchone()
    finally:
        conn.close()
    if row is None:
        raise StaffError(f"no backup {version_id} for {agent_id}")
    path = home_file(data_root, agent_id)
    with _lock(data_root):
        _backup(data_root, agent_id, f"restore:{version_id}", "home-before", path.read_text(encoding="utf-8"))
        write_text(path, row[0])
    return row[0]
