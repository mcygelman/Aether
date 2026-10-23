"""Cartridge box: one agent's mind in one SQLite file.

Inputs:  a file path (or ":memory:"), an agent id, a Clock.
Outputs: an open Cartridge exposing a connection, settings, profiles and card
         values, plus an OpenReport saying whether the file was changed
         outside Aether since it was last closed.

The cartridge holds data only. On open, the engine installs TEMPORARY views
(decay.temp_view_sql) that exist only in this connection. On close, it
writes a fingerprint ("seal") of the whole mind; on the next open it checks
it, so an edit made outside Aether is noticed rather than silently trusted.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .errors import MemoryFabricError
from . import decay, schema
from .clock import SystemClock


class CartridgeError(MemoryFabricError):
    """Any refusal from the cartridge box."""


# What the engine could say about the seal when the cartridge was opened.
STATUS_TEXT = {
    "created": None,
    "sealed": None,
    "tampered": "edited outside the engine since it was last sealed",
    "unsealed": "no seal although no session has it open: edited outside the engine, "
                "or the seal was removed",
    "in_use": "open in another session (or a session ended without closing); seal not checked",
    "unclean": "the last session ended without closing; changes since its last seal "
               "could not be verified",
}


@dataclass(frozen=True)
class OpenReport:
    path: str
    agent_id: str
    schema_version: int
    sealed: bool        # a seal was present and matched
    tampered: bool      # the seal was checked and did not match the contents
    created: bool       # this open created the cartridge
    status: str = "sealed"   # one of STATUS_TEXT

    @property
    def warning(self):
        """A sentence for people and agents when something needs attention, else None."""
        return STATUS_TEXT.get(self.status)


# meta keys that change on open and must not affect the seal
_UNSEALED_META = ("seal", "tamper_events", "last_tamper_at", "last_opened_at", "open_sessions")


def _ensure_pow(conn: sqlite3.Connection) -> None:
    """Use SQLite's built-in pow(); register an identical one if missing.
    Also register fabric_compact(), the spacing-insensitive form recall uses
    for phrase matching (connection-local; never stored in the file)."""
    try:
        conn.execute("SELECT pow(2.0, 3.0)").fetchone()
    except sqlite3.OperationalError:
        conn.create_function("pow", 2, lambda a, b: float(a) ** float(b), deterministic=True)
    conn.create_function("fabric_compact", 1, compact, deterministic=True)


def compact(text) -> str:
    """'LMStudio', 'lm studio' and 'lm-studio' all become 'lmstudio'."""
    return re.sub(r"[^a-z0-9]", "", str(text or "").lower())


_DEFAULT_VALUES = {row[0]: float(row[1]) for row in schema.DEFAULT_CONFIG}


def _enable_wal(conn: sqlite3.Connection, path: str) -> None:
    if path != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")


def _alone(conn: sqlite3.Connection) -> bool:
    """True when no other connection has this cartridge open.

    Every writable engine session puts the file in WAL mode, and the last one
    to close takes it out again. So: not in WAL mode means nobody is in; in
    WAL mode, leaving it succeeds only if we are the only connection (a crash
    leaves WAL mode behind with no one in). Never waits."""
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0].lower()
    if mode != "wal":
        return True
    conn.execute("PRAGMA busy_timeout = 0")
    try:
        conn.execute("PRAGMA journal_mode = DELETE")
        return True
    except sqlite3.OperationalError:
        return False
    finally:
        conn.execute("PRAGMA busy_timeout = 5000")


def _top_up_defaults(conn: sqlite3.Connection, now: str) -> None:
    """Additive forward compatibility: settings and profiles that a newer
    engine knows and this cartridge lacks are added with their defaults
    (never changing an existing row), and each addition is logged."""
    have = {r[0] for r in conn.execute("SELECT key FROM config")}
    for row in schema.DEFAULT_CONFIG:
        if row[0] not in have:
            conn.execute("INSERT INTO config(key, value, min_value, max_value, tunable_by_agent,"
                         " description) VALUES (?,?,?,?,?,?)", row)
            conn.execute("INSERT INTO config_changes(key, old_value, new_value, changed_by, reason,"
                         " at) VALUES (?,?,?,?,?,?)",
                         (row[0], None, row[1], "engine", "new engine default added", now))
    have = {r[0] for r in conn.execute("SELECT profile FROM profiles")}
    for row in schema.DEFAULT_PROFILES:
        if row[0] not in have:
            conn.execute("INSERT INTO profiles(profile, importance, slow_rate, fast_rate,"
                         " horizon_days, refresh_extends_days, link_slow_rate, link_fast_rate,"
                         " description) VALUES (?,?,?,?,?,?,?,?,?)", row)


class Cartridge:
    """Open handle on one agent's cartridge. Use create() or open()."""

    def __init__(self, conn: sqlite3.Connection, path: str, agent_id: str, clock, report):
        self.conn = conn
        self.path = path
        self.agent_id = agent_id
        self.clock = clock
        self.report: OpenReport = report
        self.read_only = False
        self._closed = False
        self.actor = agent_id
        self.session_id = 'local-' + uuid.uuid4().hex
        self._transaction_depth = 0
        self._read_depth = 0

    # -- lifecycle ---------------------------------------------------------

    @classmethod
    def create(cls, path: str, agent_id: str, name: Optional[str] = None,
               clock=None, seed_resources=True) -> "Cartridge":
        clock = clock or SystemClock()
        if not agent_id or not agent_id.strip():
            raise CartridgeError("agent_id is required")
        if path != ":memory:" and os.path.exists(path) and os.path.getsize(path) > 0:
            raise CartridgeError(f"cartridge already exists (or another file is there): {path}")
        conn = cls._connect(path)
        now = clock.now()
        _enable_wal(conn, path)
        with conn:
            for statement in schema.DDL:
                conn.execute(statement)
            conn.executemany("INSERT INTO meta(key, value) VALUES (?, ?)", [
                ("schema_version", str(schema.SCHEMA_VERSION)),
                ("engine", schema.ENGINE_NAME),
                ("agent_id", agent_id),
                ("created_at", now),
                ("open_sessions", "1"),
            ])
            conn.executemany(
                "INSERT INTO profiles(profile, importance, slow_rate, fast_rate, horizon_days,"
                " refresh_extends_days, link_slow_rate, link_fast_rate, description)"
                " VALUES (?,?,?,?,?,?,?,?,?)", schema.DEFAULT_PROFILES)
            conn.executemany(
                "INSERT INTO config(key, value, min_value, max_value, tunable_by_agent,"
                " description) VALUES (?,?,?,?,?,?)", schema.DEFAULT_CONFIG)
            conn.executemany("INSERT INTO card(key, value) VALUES (?, ?)", [
                ("name", name or agent_id), ("scan_line", ""),
            ])
        report = OpenReport(path, agent_id, schema.SCHEMA_VERSION, False, False, True, "created")
        cart = cls(conn, path, agent_id, clock, report)
        cart._install_views()
        from .workspace import seed
        if seed_resources:
            seed(cart)
        return cart

    @classmethod
    def open(cls, path: str, clock=None, read_only: bool = False) -> "Cartridge":
        """Open a cartridge. read_only=True writes nothing at all (viewers,
        inspection, résumés): no open stamp, no tamper log, no seal on close.

        The seal is checked only when it can mean something: when no other
        session has the cartridge open and the last one closed cleanly.
        Otherwise the report says so (status in_use / unclean) instead of
        raising a false alarm."""
        clock = clock or SystemClock()
        if path == ":memory:" or not os.path.isfile(path):
            raise CartridgeError(f"no such cartridge: {path}")
        conn = cls._connect(path, read_only=read_only)
        try:
            is_cartridge = conn.execute(
                "SELECT count(*) FROM sqlite_master WHERE name='meta'").fetchone()[0]
        except sqlite3.DatabaseError as exc:
            conn.close()
            raise CartridgeError(f"not a cartridge: {path} ({exc})") from exc
        if not is_cartridge:
            conn.close()
            raise CartridgeError(f"not a cartridge: {path}")
        meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
        version = int(meta.get("schema_version", "0"))
        if version != schema.SCHEMA_VERSION:
            conn.close()
            raise CartridgeError(
                f"cartridge schema {version} is not supported by this engine "
                f"(expects {schema.SCHEMA_VERSION}); a migration is required")
        if meta.get("frozen_at"):
            # A frozen archive: its clock stands still on the day it was frozen, and it is
            # never written again. Nobody writes into another mind.
            if not read_only:
                conn.close()
                raise CartridgeError(
                    f"{path} is a frozen archive of {meta.get('archive_of', meta['agent_id'])} "
                    f"(frozen {meta['frozen_at'][:10]}); open it read-only")
            from .clock import FixedClock
            clock = FixedClock(meta["frozen_at"])
        sessions = int(meta.get("open_sessions", "0") or 0)
        alone = True if read_only else _alone(conn)
        in_wal = conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        seal = meta.get("seal")
        if read_only and (in_wal or sessions > 0):
            status = "in_use"
        elif not alone:
            status = "in_use"
        elif sessions > 0:
            status = "unclean"
        elif not seal:
            status = "unsealed"
        elif seal != fingerprint(conn):
            status = "tampered"
        else:
            status = "sealed"
        report = OpenReport(path, meta["agent_id"], version, status == "sealed",
                            status == "tampered", False, status)
        now = clock.now()
        if read_only:
            cart = cls(conn, path, meta["agent_id"], clock, report)
            cart.read_only = True
            cart._install_views()
            return cart
        _enable_wal(conn, path)
        with conn:
            conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('last_opened_at', ?)", (now,))
            # A crashed session's count is stale once we know we're alone.
            live = sessions if status == "in_use" else 0
            conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('open_sessions', ?)",
                         (str(live + 1),))
            if status == "tampered":
                events = int(meta.get("tamper_events", "0")) + 1
                conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('tamper_events', ?)",
                             (str(events),))
                conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('last_tamper_at', ?)",
                             (now,))
        cart = cls(conn, path, meta["agent_id"], clock, report)
        cart._install_views()
        cart.actor = 'engine'
        try:
            with cart.transaction(operation='engine_defaults', description='add missing engine defaults'):
                _top_up_defaults(conn, now)
                from .workspace import seed
                seed(cart)
        finally:
            cart.actor = cart.agent_id
        return cart

    @staticmethod
    def _connect(path: str, read_only: bool = False) -> sqlite3.Connection:
        if read_only:
            conn = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
        else:
            conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 5000")   # wait for another session's write, don't fail
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA recursive_triggers = ON")  # capture prior rows of INSERT OR REPLACE
        _ensure_pow(conn)
        return conn

    def _install_views(self) -> None:
        for statement in decay.temp_view_sql():
            self.conn.execute(statement)
        self.sync_clock()
        if not self.read_only:
            from .history import install_capture
            install_capture(self.conn)

    def sync_clock(self) -> str:
        """Point the temporary views at the clock's current time."""
        if self._read_depth:
            return self.conn.execute('SELECT now FROM temp.fabric_clock').fetchone()[0]
        now = self.clock.now()
        # Commit at once: Python's sqlite3 opens a transaction for these temp-table
        # writes, and leaving it open would pin a stale snapshot of the cartridge and
        # block other sessions' checkpoints (found by test_two_handles...).
        with self.transaction() if self._transaction_depth else self.conn:
            self.conn.execute("DELETE FROM temp.fabric_clock")
            self.conn.execute("INSERT INTO temp.fabric_clock(now) VALUES (?)", (now,))
        return now

    @contextmanager
    def read_snapshot(self):
        """Consistent content and revision, with no refresh or persistent writes."""
        self.sync_clock()
        name = f'read_{self._read_depth}'
        self.conn.execute(f'SAVEPOINT {name}')
        self._read_depth += 1
        try:
            yield self.conn
        finally:
            self._read_depth -= 1
            self.conn.execute(f'RELEASE {name}')

    def close(self) -> str:
        """Seal and close. Returns the seal (the fingerprint of the mind)."""
        if self._closed:
            raise CartridgeError("cartridge already closed")
        if self.read_only:
            row = self.conn.execute("SELECT value FROM meta WHERE key = 'seal'").fetchone()
            self.conn.close()
            self._closed = True
            return row["value"] if row else ""
        with self.conn:
            row = self.conn.execute("SELECT value FROM meta WHERE key = 'open_sessions'").fetchone()
            remaining = max(0, int(row["value"] if row else 1) - 1)
            self.conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('open_sessions', ?)",
                              (str(remaining),))
            seal = fingerprint(self.conn)
            self.conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('seal', ?)", (seal,))
        if self.path != ":memory:":
            # PASSIVE never waits: another session reading this mind must not make closing fail.
            self.conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
            # At rest a mind is ONE file: if no other session has it open, leave WAL mode
            # (best-effort, no waiting), so readers and copies need no -wal/-shm side files.
            self.conn.execute("PRAGMA busy_timeout = 0")
            try:
                self.conn.execute("PRAGMA journal_mode = DELETE")
            except sqlite3.OperationalError:
                pass
        self.conn.close()
        self._closed = True
        return seal

    def freeze(self, archive_of: str, frozen_at: str, note: str) -> None:
        """Make this cartridge a frozen archive (the last write it will ever take)."""
        if not note or not note.strip():
            raise CartridgeError("an archive needs a note saying what it is and what it is not")
        with self.transaction() as conn:
            conn.executemany("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", [
                ("archive_of", archive_of), ("frozen_at", frozen_at), ("archive_note", note.strip())])

    def archive_info(self) -> Optional[dict]:
        rows = dict(self.conn.execute(
            "SELECT key, value FROM meta WHERE key IN ('archive_of','frozen_at','archive_note')").fetchall())
        if not rows.get("frozen_at"):
            return None
        return {"of": rows.get("archive_of"), "frozen_at": rows["frozen_at"],
                "note": rows.get("archive_note", "")}

    def backup_to(self, destination: str) -> str:
        """A consistent full copy of the cartridge (SQLite's online backup), sealed
        state included. Refuses to overwrite. Works on read-only handles too."""
        if self.conn.in_transaction:
            raise CartridgeError('finish the active transaction before backing up')
        if os.path.exists(destination):
            raise CartridgeError(f"refusing to overwrite an existing file: {destination}")
        target = sqlite3.connect(destination)
        try:
            with target:
                self.conn.backup(target)
            # The copy is a closed, sealed mind of its own: no sessions, a fresh seal.
            with target:
                target.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('open_sessions', '0')")
                target.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('seal', ?)",
                               (fingerprint(target),))
            target.execute("PRAGMA journal_mode = DELETE")
        finally:
            target.close()
        return destination

    @contextmanager
    def transaction(self, immediate: bool = False, operation: str = 'maintenance', description: str = '',
                    transition: Optional[dict] = None, before_commit=None):
        """All-or-nothing writes. immediate=True takes the write lock at the start,
        so a check-then-insert can't race another session. Optional outer transition
        evidence captures semantic seals under that lock, including recorded history,
        and is published to the caller only after a successful commit. A trusted host
        before_commit callback may retain the candidate transition while the write
        lock is held; its success is not evidence that SQLite committed."""
        if self._closed:
            raise CartridgeError("cartridge is closed")
        if self.read_only:
            raise CartridgeError("cartridge was opened read-only; nothing can be written")
        from . import history
        outer = self._transaction_depth == 0
        if transition is not None and (not outer or type(transition) is not dict or transition):
            raise CartridgeError('transition evidence requires an empty dictionary and outer transaction')
        if before_commit is not None and (transition is None or not callable(before_commit)):
            raise CartridgeError('before_commit requires outer transition evidence and a callable')
        savepoint = f'fabric_{self._transaction_depth}'
        if outer:
            self.conn.execute('BEGIN IMMEDIATE')
        else:
            self.conn.execute(f'SAVEPOINT {savepoint}')
        self._transaction_depth += 1
        try:
            if outer:
                self.conn.execute('DELETE FROM temp.memory_delta')
            before = dict(revision=history.revision(self), content_seal=content_fingerprint(self.conn)) if transition is not None else None
            yield self.conn
            if outer:
                history.record(self, None, operation, description)
                after = dict(revision=history.revision(self), content_seal=content_fingerprint(self.conn)) if transition is not None else None
                if before_commit is not None:
                    guarded_seal = fingerprint(self.conn)
                    before_commit(dict(before=dict(before), after=dict(after)))
                    if not self.conn.in_transaction or fingerprint(self.conn) != guarded_seal:
                        raise CartridgeError('before_commit must not change the memory transaction')
                self.conn.commit()
                if transition is not None:
                    transition.update(before=before, after=after)
            else:
                self.conn.execute(f'RELEASE {savepoint}')
        except BaseException:
            if outer:
                self.conn.rollback()
            else:
                self.conn.execute(f'ROLLBACK TO {savepoint}')
                self.conn.execute(f'RELEASE {savepoint}')
            raise
        finally:
            self._transaction_depth -= 1

    # -- settings ----------------------------------------------------------

    def setting(self, key: str) -> float:
        row = self.conn.execute("SELECT value FROM config WHERE key = ?", (key,)).fetchone()
        if row is None:
            if key in _DEFAULT_VALUES:          # newer engine, older (read-only) cartridge
                return _DEFAULT_VALUES[key]
            raise CartridgeError(f"unknown setting: {key}")
        return float(row["value"])

    def settings(self) -> dict:
        stored = {r["key"]: float(r["value"]) for r in self.conn.execute("SELECT key, value FROM config")}
        return {**_DEFAULT_VALUES, **stored}

    def change_setting(self, key: str, value: float, changed_by: str, reason: str = "") -> None:
        """Tune a setting within its range. Every change is logged.

        The agent may change only settings marked tunable_by_agent; anyone else
        (operator, overseer, Librarian) may change any setting within range.
        """
        row = self.conn.execute("SELECT * FROM config WHERE key = ?", (key,)).fetchone()
        if row is None:
            raise CartridgeError(f"unknown setting: {key}")
        if changed_by == self.agent_id and not row["tunable_by_agent"]:
            raise CartridgeError(f"setting '{key}' is not tunable by the agent")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value:
            raise CartridgeError(f"setting '{key}' needs a number")
        if not row["min_value"] <= value <= row["max_value"]:
            raise CartridgeError(
                f"setting '{key}' must stay within {row['min_value']:g}..{row['max_value']:g}")
        current = self.settings()
        for low, high in schema.ORDERED_SETTINGS:
            if key == low and value > current[high]:
                raise CartridgeError(f"'{low}' cannot exceed '{high}' ({current[high]:g})")
            if key == high and value < current[low]:
                raise CartridgeError(f"'{high}' cannot be below '{low}' ({current[low]:g})")
        with self.transaction() as conn:
            conn.execute("UPDATE config SET value = ? WHERE key = ?", (value, key))
            conn.execute(
                "INSERT INTO config_changes(key, old_value, new_value, changed_by, reason, at)"
                " VALUES (?,?,?,?,?,?)", (key, row["value"], value, changed_by, reason, self.clock.now()))

    # -- profiles ----------------------------------------------------------

    def profile(self, name: str) -> dict:
        row = self.conn.execute("SELECT * FROM profiles WHERE profile = ?", (name,)).fetchone()
        if row is None:
            raise CartridgeError(f"unknown profile: {name}")
        return dict(row)

    def profiles(self) -> list:
        return [dict(r) for r in self.conn.execute("SELECT * FROM profiles ORDER BY profile")]

    _PROFILE_FIELDS = ("importance", "slow_rate", "fast_rate", "horizon_days",
                       "refresh_extends_days", "link_slow_rate", "link_fast_rate")

    def tune_profile(self, name: str, changed_by: str, reason: str = "", **values) -> None:
        """Adjust a profile's numbers (the agent's own calibration). Logged."""
        current = self.profile(name)
        for field, value in values.items():
            if field not in self._PROFILE_FIELDS:
                raise CartridgeError(f"unknown profile field: {field}")
            if field.endswith("rate") and not 0.0 <= value < 1.0:
                raise CartridgeError(f"{field} must be in [0, 1)")
            if field == "importance" and not 0.1 <= value <= 1.0:
                raise CartridgeError("importance must be in [0.1, 1]")
            if field.endswith("days") and value <= 0:
                raise CartridgeError(f"{field} must be positive")
        with self.transaction() as conn:
            for field, value in values.items():
                conn.execute(f"UPDATE profiles SET {field} = ?, source = 'tuned' WHERE profile = ?",
                             (value, name))
                conn.execute(
                    "INSERT INTO config_changes(key, old_value, new_value, changed_by, reason, at)"
                    " VALUES (?,?,?,?,?,?)",
                    (f"profile:{name}.{field}", current[field], value, changed_by, reason,
                     self.clock.now()))

    # -- card values -------------------------------------------------------

    def card_value(self, key: str) -> Optional[str]:
        row = self.conn.execute("SELECT value FROM card WHERE key = ?", (key,)).fetchone()
        return None if row is None else row["value"]

    def set_card_value(self, key: str, value: str) -> None:
        with self.transaction() as conn:
            conn.execute("INSERT OR REPLACE INTO card(key, value) VALUES (?, ?)", (key, value))


def content_fingerprint(conn: sqlite3.Connection) -> str:
    """Reviewed memory content, excluding lifecycle facts and their retry receipts.

    The full integrity seal still covers these rows. Owner filing recovery uses
    this separate binding so a host opening cannot invalidate unchanged memory.
    """
    return fingerprint(conn, include_lifecycle=False)


def fingerprint(conn: sqlite3.Connection, version: Optional[int] = None, *, include_lifecycle=True) -> str:
    """SHA-256 over every data table the cartridge's schema version seals (its own, unless given), in a fixed
    order, excluding open-time meta. A schema-2 file's seal never covered the atelier's side log, so checking it
    against schema 3's tables would call every logged mind tampered."""
    if version is None:
        try:
            row = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
        except sqlite3.DatabaseError:
            row = None
        version = int(row[0]) if row else schema.SCHEMA_VERSION
    digest = hashlib.sha256()
    for table in schema.sealed_tables(version):
        if not include_lifecycle and table == 'session_events':
            continue
        columns = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
        if not columns:  # Schema-1 seal verification before an explicit migration.
            continue
        order = ", ".join(str(i + 1) for i in range(len(columns)))
        query = f"SELECT * FROM {table}"
        if table == "meta":
            marks = ",".join("?" * len(_UNSEALED_META))
            rows = conn.execute(f"{query} WHERE key NOT IN ({marks}) ORDER BY {order}",
                                _UNSEALED_META).fetchall()
        elif table == 'operation_receipts' and not include_lifecycle:
            rows = []
            event_ids = {r[0] for r in conn.execute('SELECT event_id FROM session_events')}
            names = {name: i for i, name in enumerate(columns)}
            for row in conn.execute(f'{query} ORDER BY {order}'):
                operation = row[names['operation_id']]
                try:
                    result = json.loads(row[names['result']])
                    event = result.get('event', {})
                    lifecycle_receipt = (operation.startswith(('lifecycle:', 'scaffold-start:', 'closure-publication:'))
                                         and isinstance(event, dict) and event.get('event_id') in event_ids)
                except (ValueError, AttributeError, TypeError):
                    lifecycle_receipt = False
                if not lifecycle_receipt:
                    rows.append(row)
        else:
            rows = conn.execute(f"{query} ORDER BY {order}").fetchall()
        digest.update(table.encode())
        for row in rows:
            digest.update(json.dumps(list(row), separators=(",", ":"), default=str).encode())
    return digest.hexdigest()
