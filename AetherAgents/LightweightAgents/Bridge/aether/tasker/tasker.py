"""The Tasker: the sprint's task list in SQLite (contract §3 and §5).

Four statuses, New, Active, Blocked and Closed, and a note. The table is the only state that says whose turn it is.
Every write goes through this API, in one BEGIN IMMEDIATE transaction, and triggers record every changed field in
`changes` with who made it. An illegal move is refused with a plain reason the mind can act on; nothing is ever
changed silently.

Actors:
    'overseer:<id>'   the sprint's Overseer: add, edit, unblock, drop
    'operator'        the human operator: the Overseer's moves too
    '<agent id>'      a staff agent: start, close and block its own assigned task

The actor reaches the triggers through the one-row `actor` table, set and cleared inside each write transaction
(SQLite triggers in the main schema cannot read a TEMP table). Writes are serialized by the IMMEDIATE lock, so the
row always belongs to the writer holding it. A write made outside this API finds no row and is recorded as
'operator', because no mind has any other way to touch the file.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional, Sequence

SCHEMA_VERSION = 1
STATUSES = ("New", "Active", "Blocked", "Closed")
FIELDS = ("title", "prompt", "status", "assigned_to", "note")
NOW = "strftime('%Y-%m-%dT%H:%M:%fZ','now')"
BY = "COALESCE((SELECT who FROM actor WHERE id = 1), 'operator')"


class TaskerError(ValueError):
    """A move the Tasker does not allow. The message says why and what to do instead."""


class _Unset:
    def __repr__(self) -> str:
        return "UNSET"


UNSET = _Unset()


@dataclass(frozen=True)
class Task:
    number: int
    title: str
    prompt: str
    status: str
    assigned_to: Optional[str]
    note: str
    depends_on: tuple
    created_at: str
    modified_at: str
    started_at: Optional[str]
    closed_at: Optional[str]


@dataclass(frozen=True)
class Change:
    id: int
    at: str
    task: int
    field: str
    old: Optional[str]
    new: Optional[str]
    by: str


def _field_trigger(field: str) -> str:
    return f"""
CREATE TRIGGER IF NOT EXISTS tasks_change_{field} AFTER UPDATE OF {field} ON tasks
WHEN OLD.{field} IS NOT NEW.{field}
BEGIN
  INSERT INTO changes (at, task, field, old, new, by)
  VALUES ({NOW}, NEW.number, '{field}', OLD.{field}, NEW.{field}, {BY});
END;"""


SCHEMA = f"""
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS tasks (
  number       INTEGER PRIMARY KEY,
  title        TEXT NOT NULL,
  prompt       TEXT NOT NULL,
  status       TEXT NOT NULL DEFAULT 'New' CHECK (status IN ('New','Active','Blocked','Closed')),
  assigned_to  TEXT,
  note         TEXT NOT NULL DEFAULT '',
  created_at   TEXT NOT NULL,
  modified_at  TEXT NOT NULL,
  started_at   TEXT,
  closed_at    TEXT
);
CREATE TABLE IF NOT EXISTS dependencies (
  task       INTEGER NOT NULL REFERENCES tasks(number),
  depends_on INTEGER NOT NULL REFERENCES tasks(number),
  PRIMARY KEY (task, depends_on), CHECK (task <> depends_on)
);
CREATE TABLE IF NOT EXISTS changes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  at TEXT NOT NULL, task INTEGER NOT NULL,
  field TEXT NOT NULL, old TEXT, new TEXT,
  by TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS actor (id INTEGER PRIMARY KEY CHECK (id = 1), who TEXT NOT NULL);

CREATE TRIGGER IF NOT EXISTS tasks_created AFTER INSERT ON tasks
BEGIN
  INSERT INTO changes (at, task, field, old, new, by) VALUES ({NOW}, NEW.number, 'created', NULL, NEW.title, {BY});
END;
CREATE TRIGGER IF NOT EXISTS tasks_modified AFTER UPDATE OF title, prompt, status, assigned_to, note ON tasks
BEGIN
  UPDATE tasks SET modified_at = {NOW} WHERE number = NEW.number;
END;
CREATE TRIGGER IF NOT EXISTS tasks_status_times AFTER UPDATE OF status ON tasks
WHEN OLD.status IS NOT NEW.status AND NEW.status IN ('Active', 'Closed')
BEGIN
  UPDATE tasks SET
    started_at = CASE WHEN NEW.status = 'Active' THEN {NOW} ELSE started_at END,
    closed_at  = CASE WHEN NEW.status = 'Closed' THEN {NOW} ELSE closed_at END
  WHERE number = NEW.number;
END;
CREATE TRIGGER IF NOT EXISTS dependencies_added AFTER INSERT ON dependencies
BEGIN
  INSERT INTO changes (at, task, field, old, new, by)
  VALUES ({NOW}, NEW.task, 'depends_on', NULL, CAST(NEW.depends_on AS TEXT), {BY});
  UPDATE tasks SET modified_at = {NOW} WHERE number = NEW.task;
END;
CREATE TRIGGER IF NOT EXISTS dependencies_removed AFTER DELETE ON dependencies
BEGIN
  INSERT INTO changes (at, task, field, old, new, by)
  VALUES ({NOW}, OLD.task, 'depends_on', CAST(OLD.depends_on AS TEXT), NULL, {BY});
  UPDATE tasks SET modified_at = {NOW} WHERE number = OLD.task;
END;
""" + "".join(_field_trigger(f) for f in reversed(FIELDS))  # SQLite fires the newest trigger first


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _numbers(values: Optional[Iterable]) -> list:
    if values is None:
        return []
    if isinstance(values, (int, str)):
        values = [values]
    out = []
    for v in values:
        try:
            n = int(v)
        except (TypeError, ValueError):
            raise TaskerError(f"dependency {v!r} is not a task number")
        if n not in out:
            out.append(n)
    return out


class Tasker:
    """One actor's handle on a sprint's tasker.sqlite. Each call opens its own connection, so a Tasker can be used
    from any thread and several Taskers on one file never lose a write."""

    def __init__(self, path, actor: str):
        self.path = Path(path)
        actor = (actor or "").strip()
        if not actor:
            raise TaskerError("an actor is required: an agent id, 'overseer:<id>' or 'operator'")
        if not self.path.is_file():
            raise TaskerError(f"no Tasker at {self.path}")
        self.actor = actor
        meta = self.meta()
        self.sprint_id = meta.get("sprint_id", "")
        self.overseer = meta.get("overseer", "")
        if actor.startswith("overseer:") and actor != f"overseer:{self.overseer}":
            raise TaskerError(f"{actor} is not this sprint's Overseer (overseer:{self.overseer})")

    # ---- creation ---------------------------------------------------------------------------------------------

    @classmethod
    def create(cls, path, sprint_id: str, overseer: str) -> "Tasker":
        path = Path(path)
        if path.exists():
            raise TaskerError(f"a Tasker already exists at {path}; open it instead")
        if not sprint_id or not overseer:
            raise TaskerError("a sprint id and an Overseer id are required")
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = _connect(path)
        try:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.executescript(SCHEMA)
            conn.execute("BEGIN IMMEDIATE")
            conn.executemany("INSERT INTO meta (key, value) VALUES (?, ?)", [
                ("schema_version", str(SCHEMA_VERSION)),
                ("sprint_id", sprint_id),
                ("overseer", overseer),
            ])
            conn.execute(f"INSERT INTO meta (key, value) VALUES ('created_at', {NOW})")
            conn.execute("COMMIT")
        finally:
            conn.close()
        return cls(path, f"overseer:{overseer}")

    @classmethod
    def open(cls, path, actor: str) -> "Tasker":
        return cls(path, actor)

    def as_actor(self, actor: str) -> "Tasker":
        return Tasker(self.path, actor)

    # ---- reads -------------------------------------------------------------------------------------------------

    def meta(self) -> dict:
        conn = _connect(self.path)
        try:
            return {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM meta")}
        finally:
            conn.close()

    def get(self, number: int) -> Task:
        conn = _connect(self.path)
        try:
            return self._get(conn, number)
        finally:
            conn.close()

    def list(self, status: Optional[str] = None, assigned_to: Optional[str] = None) -> list:
        if status is not None and status not in STATUSES:
            raise TaskerError(f"unknown status {status!r}; the statuses are {', '.join(STATUSES)}")
        sql, args = "SELECT number FROM tasks WHERE 1=1", []
        if status is not None:
            sql += " AND status = ?"
            args.append(status)
        if assigned_to is not None:
            sql += " AND assigned_to = ?"
            args.append(assigned_to)
        conn = _connect(self.path)
        try:
            return [self._get(conn, r["number"]) for r in conn.execute(sql + " ORDER BY number", args)]
        finally:
            conn.close()

    def ready(self) -> list:
        """New tasks with an assignee whose every dependency is Closed, in number order."""
        conn = _connect(self.path)
        try:
            rows = conn.execute(
                "SELECT t.number FROM tasks t WHERE t.status = 'New' AND t.assigned_to IS NOT NULL"
                " AND NOT EXISTS (SELECT 1 FROM dependencies d JOIN tasks u ON u.number = d.depends_on"
                "                 WHERE d.task = t.number AND u.status <> 'Closed')"
                " ORDER BY t.number").fetchall()
            return [self._get(conn, r["number"]) for r in rows]
        finally:
            conn.close()

    def changes_after(self, cursor: int = 0) -> list:
        conn = _connect(self.path)
        try:
            rows = conn.execute("SELECT * FROM changes WHERE id > ? ORDER BY id", (int(cursor),)).fetchall()
            return [Change(r["id"], r["at"], r["task"], r["field"], r["old"], r["new"], r["by"]) for r in rows]
        finally:
            conn.close()

    def counts(self) -> dict:
        conn = _connect(self.path)
        try:
            out = {s: 0 for s in STATUSES}
            for r in conn.execute("SELECT status, COUNT(*) AS n FROM tasks GROUP BY status"):
                out[r["status"]] = r["n"]
            return out
        finally:
            conn.close()

    # ---- the Overseer's moves ------------------------------------------------------------------------------------

    def add(self, title: str, prompt: str, assigned_to: Optional[str] = None,
            depends_on: Optional[Sequence[int]] = None) -> int:
        self._require_overseer("add a task")
        title, prompt = (title or "").strip(), (prompt or "").strip()
        if not title or not prompt:
            raise TaskerError("a task needs a title and a prompt")
        deps = _numbers(depends_on)
        with self._write() as conn:
            self._require_exist(conn, deps)
            cur = conn.execute(
                f"INSERT INTO tasks (title, prompt, assigned_to, created_at, modified_at)"
                f" VALUES (?, ?, ?, {NOW}, {NOW})", (title, prompt, _who(assigned_to)))
            number = cur.lastrowid
            from .verification import task_declared
            task_declared(self.path, number)
            conn.executemany("INSERT INTO dependencies (task, depends_on) VALUES (?, ?)",
                             [(number, d) for d in deps])
        return number

    def edit(self, number: int, title=UNSET, prompt=UNSET, assigned_to=UNSET, depends_on=UNSET) -> Task:
        self._require_overseer("edit a task")
        with self._write() as conn:
            task = self._get(conn, number)
            self._require_editable(task, "edit")
            self._apply_edits(conn, task, title, prompt, assigned_to, depends_on)
            return self._get(conn, number)

    def unblock(self, number: int, note: str = "", prompt=UNSET, assigned_to=UNSET) -> Task:
        self._require_overseer("unblock a task")
        with self._write() as conn:
            task = self._get(conn, number)
            if task.status != "Blocked":
                raise TaskerError(f"task {number} is {task.status}, not Blocked; only a Blocked task is unblocked")
            self._apply_edits(conn, task, UNSET, prompt, assigned_to, UNSET)
            conn.execute("UPDATE tasks SET status = 'New', note = ? WHERE number = ?", ((note or "").strip(), number))
            return self._get(conn, number)

    def drop(self, number: int, note: str) -> Task:
        self._require_overseer("drop a task")
        from .verification import before_drop
        before_drop(self.path)
        note = (note or "").strip()
        if not note:
            raise TaskerError(f"say why task {number} is dropped; tasks that depend on it will treat it as Closed")
        if not note.lower().startswith("dropped:"):
            note = "dropped: " + note
        with self._write() as conn:
            task = self._get(conn, number)
            self._require_editable(task, "drop")
            conn.execute("UPDATE tasks SET status = 'Closed', note = ? WHERE number = ?", (note, number))
            return self._get(conn, number)

    # ---- the staff agent's moves ---------------------------------------------------------------------------------

    def start(self, number: int) -> Task:
        with self._write() as conn:
            task = self._own(conn, number, "start")
            if task.status != "New":
                raise TaskerError(f"task {number} is {task.status}; only a New task is started")
            waiting = [r["depends_on"] for r in conn.execute(
                "SELECT d.depends_on FROM dependencies d JOIN tasks u ON u.number = d.depends_on"
                " WHERE d.task = ? AND u.status <> 'Closed' ORDER BY d.depends_on", (number,))]
            if waiting:
                raise TaskerError(f"task {number} waits on {_list(waiting)} (not Closed yet); wait for the Timer")
            busy = [r["number"] for r in conn.execute(
                "SELECT number FROM tasks WHERE assigned_to = ? AND status = 'Active'", (self.actor,))]
            if busy:
                raise TaskerError(f"you already have task {busy[0]} Active; close or block it first")
            from .verification import before_start
            before_start(self.path, task.depends_on)
            conn.execute("UPDATE tasks SET status = 'Active' WHERE number = ?", (number,))
            return self._get(conn, number)

    def close(self, number: int, note: str) -> Task:
        note = (note or "").strip()
        if not note:
            raise TaskerError(f"give task {number} a one-line result note; put the detail in outputs/T{number}/")
        return self._finish(number, "Closed", note, "close")

    def block(self, number: int, note: str) -> Task:
        note = (note or "").strip()
        if not note:
            raise TaskerError(f"say what task {number} needs to go on; the Overseer reads the note")
        return self._finish(number, "Blocked", note, "block")

    # ---- inside ----------------------------------------------------------------------------------------------------

    def _finish(self, number: int, status: str, note: str, verb: str) -> Task:
        with self._write() as conn:
            task = self._own(conn, number, verb)
            if task.status != "Active":
                hint = f"; call task_start({number}) first" if task.status == "New" else ""
                raise TaskerError(f"task {number} is {task.status}, not Active; you can {verb} only an Active task{hint}")
            if status == 'Closed':
                from .verification import before_close
                before_close(self.path, number)
            conn.execute("UPDATE tasks SET status = ?, note = ? WHERE number = ?", (status, note, number))
            return self._get(conn, number)

    def _write(self):
        return _Write(self.path, self.actor)

    def _is_overseer(self) -> bool:
        return self.actor == "operator" or self.actor == f"overseer:{self.overseer}"

    def _require_overseer(self, what: str) -> None:
        if not self._is_overseer():
            raise TaskerError(f"only the Overseer can {what}; tell the Overseer what you need (block your task with a note)")

    def _own(self, conn, number: int, verb: str) -> Task:
        task = self._get(conn, number)
        if self._is_overseer():
            raise TaskerError(f"only the assigned staff agent can {verb} task {number}"
                              f" ({task.assigned_to or 'unassigned'}); message that agent instead")
        if task.assigned_to != self.actor:
            raise TaskerError(f"task {number} is assigned to {task.assigned_to or 'no one'}, not you;"
                              f" work only on the task the Timer gave you")
        return task

    @staticmethod
    def _require_editable(task: Task, verb: str) -> None:
        if task.status == "Active":
            raise TaskerError(f"task {task.number} is Active; message {task.assigned_to} instead of trying to {verb} it")
        if task.status == "Closed":
            raise TaskerError(f"task {task.number} is Closed and never reopens; add a new task for rework"
                              f" (it can depend on {task.number})")

    @staticmethod
    def _require_exist(conn, deps: list) -> None:
        if not deps:
            return
        known = {r[0] for r in conn.execute(
            f"SELECT number FROM tasks WHERE number IN ({','.join('?' * len(deps))})", deps)}
        missing = [d for d in deps if d not in known]
        if missing:
            raise TaskerError(f"no task {_list(missing)}; a dependency must be an existing task number")

    def _apply_edits(self, conn, task: Task, title, prompt, assigned_to, depends_on) -> None:
        sets, args = [], []
        if title is not UNSET:
            title = (title or "").strip()
            if not title:
                raise TaskerError("a task's title cannot be empty")
            sets.append("title = ?"), args.append(title)
        if prompt is not UNSET:
            prompt = (prompt or "").strip()
            if not prompt:
                raise TaskerError("a task's prompt cannot be empty")
            sets.append("prompt = ?"), args.append(prompt)
        if assigned_to is not UNSET:
            sets.append("assigned_to = ?"), args.append(_who(assigned_to))
        deps = None
        if depends_on is not UNSET:
            deps = _numbers(depends_on)
            if task.number in deps:
                raise TaskerError(f"task {task.number} cannot depend on itself")
            self._require_exist(conn, deps)
            cycle = _cycle(conn, task.number, deps)
            if cycle:
                raise TaskerError("that would make a dependency cycle: " + " -> ".join(str(n) for n in cycle))
        if sets:
            conn.execute(f"UPDATE tasks SET {', '.join(sets)} WHERE number = ?", args + [task.number])
        if deps is not None:
            old = set(task.depends_on)
            for d in sorted(old - set(deps)):
                conn.execute("DELETE FROM dependencies WHERE task = ? AND depends_on = ?", (task.number, d))
            for d in deps:
                if d not in old:
                    conn.execute("INSERT INTO dependencies (task, depends_on) VALUES (?, ?)", (task.number, d))

    @staticmethod
    def _get(conn, number) -> Task:
        try:
            number = int(number)
        except (TypeError, ValueError):
            raise TaskerError(f"{number!r} is not a task number")
        row = conn.execute("SELECT * FROM tasks WHERE number = ?", (number,)).fetchone()
        if row is None:
            raise TaskerError(f"no task {number}")
        deps = tuple(r[0] for r in conn.execute(
            "SELECT depends_on FROM dependencies WHERE task = ? ORDER BY depends_on", (number,)))
        return Task(row["number"], row["title"], row["prompt"], row["status"], row["assigned_to"], row["note"],
                    deps, row["created_at"], row["modified_at"], row["started_at"], row["closed_at"])


class _Write:
    """One BEGIN IMMEDIATE transaction with the actor set for the triggers; rolled back on any error."""

    def __init__(self, path: Path, actor: str):
        self.path, self.actor = path, actor

    def __enter__(self):
        self.conn = _connect(self.path)
        self.conn.execute("BEGIN IMMEDIATE")
        self.conn.execute("INSERT OR REPLACE INTO actor (id, who) VALUES (1, ?)", (self.actor,))
        return self.conn

    def __exit__(self, kind, value, tb):
        try:
            if kind is None:
                self.conn.execute("DELETE FROM actor")
                self.conn.execute("COMMIT")
            else:
                self.conn.execute("ROLLBACK")
        finally:
            self.conn.close()
        return False


def _who(agent) -> Optional[str]:
    agent = (agent or "").strip() if isinstance(agent, str) else agent
    return agent or None


def _list(numbers) -> str:
    numbers = [str(n) for n in numbers]
    return numbers[0] if len(numbers) == 1 else ", ".join(numbers[:-1]) + " and " + numbers[-1]


def _cycle(conn, task: int, deps: list) -> list:
    """The path back to `task` if it were to depend on `deps`, or [] when there is no cycle."""
    graph: dict = {}
    for r in conn.execute("SELECT task, depends_on FROM dependencies"):
        graph.setdefault(r[0], []).append(r[1])
    graph[task] = list(deps)
    stack = [(d, [task, d]) for d in deps]
    seen = set()
    while stack:
        node, path = stack.pop()
        if node == task:
            return path
        if node in seen:
            continue
        seen.add(node)
        for nxt in graph.get(node, []):
            stack.append((nxt, path + [nxt]))
    return []
