"""Migrations box: carry a cartridge forward to the engine's schema version.

Inputs:  a cartridge file path (closed; nothing else may have it open).
Outputs: MigrationReport (from_version, to_version, steps applied, backup path).

Two kinds of change, kept apart on purpose:
  * ADDITIVE (no migration needed): a newer engine with new settings or new
    decay profiles simply adds them, with defaults, the next time a cartridge
    is opened for writing (cartridge._top_up_defaults; each addition logged).
  * STRUCTURAL (a migration): a table or column changes shape. Each step is
    one function, MIGRATIONS[n], that takes a schema-n cartridge to n + 1.
    Steps are small, forward-only and run in one transaction: all or nothing.

Safety, in order:
  0. A cartridge another session has open is not migrated (writing under a live
     session can corrupt it). One whose last session ended without closing, and
     that nobody holds now, is "unclean", as Cartridge.open calls it: it is
     migrated, its seal is not checked (it cannot mean anything), the stale
     session count is reset in the same transaction, and the report says so
     (seal_status). Before 2026-10-19 this case was refused with "open and close
     it once", which an engine of the newer schema cannot do.
  1. The seal is checked first. A cartridge edited outside the engine is not
     migrated (allow_tampered=True overrides, for an operator who knows why).
  2. A full backup is written next to the cartridge (or into backup_dir)
     before anything changes: <name>.schema<from>.bak.sqlite.
  3. All steps run in one transaction; then the version is set and the
     cartridge is resealed.
Schema 1 -> 2 adds separate history, descriptive change records, retry receipts
and review responses. Existing content is the revision-zero baseline; earlier
history is not invented. Migration is explicit and always backs up first.
Schema 2 -> 3 brings the side log under the seal (sidelog box). A schema-2 file
the atelier already logged into holds the same table; it is adopted as it is,
every row kept. The seal before the step is checked over the tables schema 2
sealed (the side log was outside it); the reseal after covers schema 3's.
"""
from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from . import schema
from .cartridge import fingerprint
from .errors import MemoryFabricError

# from_version -> function(conn) that takes the cartridge to from_version + 1
def _history_v2(conn):
    from .history import DDL
    for statement in DDL:
        conn.execute(statement)


def _sidelog_v3(conn):
    """Adopt the atelier's side log as it is; a table of that name with other columns is kept, renamed
    sidelog_before_v3 with every row, and the engine's log starts beside it. Nothing is refused or lost."""
    from .sidelog import COLUMNS, DDL
    found = tuple(r[1] for r in conn.execute("PRAGMA table_info(sidelog)"))
    if found == COLUMNS:
        return
    if found:
        conn.execute("ALTER TABLE sidelog RENAME TO sidelog_before_v3")
    conn.execute(DDL)


def _foundations_v4(conn):
    from .foundations_schema import DDL
    for statement in DDL:
        conn.execute(statement)


MIGRATIONS: Dict[int, Callable[[sqlite3.Connection], None]] = {1: _history_v2, 2: _sidelog_v3, 3: _foundations_v4}


class MigrationError(MemoryFabricError, ValueError):
    """The cartridge cannot be migrated (the message says why)."""


@dataclass
class MigrationReport:
    path: str
    from_version: int
    to_version: int
    steps: List[int] = field(default_factory=list)
    backup_path: Optional[str] = None
    seal_status: str = "sealed"     # sealed, unsealed, unclean (not checked), or tampered (allowed by the operator)

    @property
    def changed(self) -> bool:
        return bool(self.steps)


def cartridge_version(path: str) -> int:
    if not os.path.isfile(path):
        raise MigrationError(f"no such cartridge: {path}")
    conn = sqlite3.connect(path)
    try:
        row = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
    except sqlite3.DatabaseError as exc:
        raise MigrationError(f"not a cartridge: {path} ({exc})") from exc
    finally:
        conn.close()
    if row is None:
        raise MigrationError(f"not a cartridge (no schema version): {path}")
    return int(row[0])


def migrate(path: str, *, target: Optional[int] = None, backup_dir: Optional[str] = None,
            allow_tampered: bool = False) -> MigrationReport:
    target = schema.SCHEMA_VERSION if target is None else target
    version = cartridge_version(path)
    report = MigrationReport(path=path, from_version=version, to_version=version)
    if version == target:
        return report
    if version > target:
        raise MigrationError(f"cartridge schema {version} is newer than this engine ({target}); "
                             "use a newer engine, never downgrade a mind")
    missing = [v for v in range(version, target) if v not in MIGRATIONS]
    if missing:
        raise MigrationError(f"no migration step from schema {missing[0]} to {missing[0] + 1}")

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    try:
        sessions = conn.execute("SELECT value FROM meta WHERE key = 'open_sessions'").fetchone()
        unclean = bool(sessions and int(sessions[0] or 0) > 0)
        if unclean:
            from .cartridge import _alone
            if not _alone(conn):
                raise MigrationError("cartridge is open in another session; close every session before migrating")
            report.seal_status = "unclean"
        seal = conn.execute("SELECT value FROM meta WHERE key = 'seal'").fetchone()
        if not unclean:
            if not seal:
                report.seal_status = "unsealed"
            elif seal[0] != fingerprint(conn):
                if not allow_tampered:
                    raise MigrationError("cartridge was edited outside the engine since it was sealed; "
                                         "not migrating (an operator may pass allow_tampered=True)")
                report.seal_status = "tampered"
        folder = backup_dir or os.path.dirname(os.path.abspath(path))
        os.makedirs(folder, exist_ok=True)              # a new backup folder is made, not asked for
        stem = os.path.splitext(os.path.basename(path))[0]
        backup_path = os.path.join(folder, f"{stem}.schema{version}.bak.sqlite")
        if os.path.exists(backup_path):
            raise MigrationError(f"backup already exists, refusing to overwrite: {backup_path}")
        backup = sqlite3.connect(backup_path)
        with backup:
            conn.backup(backup)
        backup.close()
        report.backup_path = backup_path
        with conn:
            conn.execute('BEGIN IMMEDIATE')
            for step in range(version, target):
                MIGRATIONS[step](conn)
                report.steps.append(step)
            conn.execute("UPDATE meta SET value = ? WHERE key = 'schema_version'", (str(target),))
            if target == schema.SCHEMA_VERSION and not conn.execute("SELECT 1 FROM meta WHERE key='frozen_at'").fetchone():
                # Seed through the same writers/history inside the migration's
                # transaction. The first read-only capture sees the steady state.
                from .cartridge import Cartridge, OpenReport, _top_up_defaults, _ensure_pow
                from .clock import SystemClock
                from . import history
                from .workspace import seed
                owner = conn.execute("SELECT value FROM meta WHERE key='agent_id'").fetchone()[0]
                cart = Cartridge(conn, path, owner, SystemClock(),
                                 OpenReport(path, owner, target, False, False, False, 'migrating'))
                cart.actor = 'engine'
                cart._transaction_depth = 1  # migrate owns the outer transaction
                _ensure_pow(conn)
                cart._install_views()
                _top_up_defaults(conn, cart.clock.now())
                seed(cart)
                history.record(cart, None, 'migration_defaults', 'Supply adaptable ecosystem resources')
            if unclean:
                conn.execute("UPDATE meta SET value = '0' WHERE key = 'open_sessions'")
            conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('seal', ?)",
                         (fingerprint(conn),))
        report.to_version = target
        return report
    finally:
        conn.close()
