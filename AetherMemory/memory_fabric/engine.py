"""Engine box: the single front door to an agent's memory.

Inputs:  a cartridge path (or ":memory:") and a Clock.
Outputs: a MemoryEngine whose methods are the whole public surface:
         remember / link / recall / load / bury / raise / adjust / reform /
         reviews / outcomes / card / résumé / log / export / settings / close.

The engine owns no logic of its own: it composes the boxes (cartridge,
fabric, recall, reformation, triggers, staff, export). Any box can be
replaced without changing callers, as long as its inputs and outputs stay
the same. There is deliberately no delete method (decision 13).
"""
from __future__ import annotations

from typing import Iterable, Optional, Sequence

from . import export as export_box
from . import fabric, recall as recall_box, reformation, sidelog, staff, triggers
from .cartridge import Cartridge


class MemoryEngine:
    def __init__(self, cartridge: Cartridge):
        self.cart = cartridge
        self.logical_session_id = None

    # -- open / close --------------------------------------------------------
    @classmethod
    def create(cls, path: str, agent_id: str, name: Optional[str] = None, clock=None, *, seed_resources=True):
        """Create a home; archives and isolated calibration may omit supplied resources."""
        return cls(Cartridge.create(path, agent_id, name=name, clock=clock, seed_resources=seed_resources))

    @classmethod
    def open(cls, path: str, clock=None, read_only: bool = False):
        return cls(Cartridge.open(path, clock=clock, read_only=read_only))

    @property
    def agent_id(self) -> str:
        return self.cart.agent_id

    @property
    def open_report(self):
        return self.cart.report

    def close(self) -> str:
        return self.cart.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        if not self.cart._closed:
            self.close()

    # -- writing memory --------------------------------------------------------
    def remember(self, title: str, body: str, kind: str, tags: Iterable[str] = (), **options) -> str:
        options.setdefault("written_by", self.agent_id)
        return fabric.remember(self.cart, title, body, kind, tags, **options)

    def link(self, a: str, b: str, relation: str = "context", strength: Optional[float] = None,
             switch_at: Optional[str] = None) -> int:
        return fabric.link(self.cart, a, b, relation, strength, switch_at)

    def add_tags(self, note_id: str, tags: Iterable[str]) -> None:
        fabric.add_tags(self.cart, note_id, tags)

    def bury(self, note_id: str) -> None:
        fabric.bury(self.cart, note_id)

    def raise_note(self, note_id: str, importance: Optional[float] = None) -> None:
        fabric.raise_note(self.cart, note_id, importance)

    def set_importance(self, note_id: str, importance: float) -> None:
        fabric.set_importance(self.cart, note_id, importance)

    def set_switch(self, note_id: str, switch_at: Optional[str] = None,
                   in_days: Optional[float] = None) -> None:
        fabric.set_switch(self.cart, note_id, switch_at, in_days)

    def adjust_depth(self, note_id: str, delta: float, fade_days: Optional[float] = None) -> None:
        fabric.adjust_depth(self.cart, note_id, delta, fade_days)

    def note(self, note_id: str) -> dict:
        return fabric.get_note(self.cart, note_id)

    def bind_session(self, owner_id: str, session_id: str):
        """Trusted host binding, never model arguments. No identity minting/backfill.

        GUID-based cartridges use the GUID as agent_id. Legacy cartridges require
        an explicit exact legacy identity from the host until owner-led migration.
        """
        from .errors import MemoryFabricError
        if owner_id != self.agent_id or not isinstance(session_id, str) or not session_id.strip():
            raise MemoryFabricError('owner/cartridge mismatch or missing trusted session')
        self.cart.actor, self.cart.session_id = owner_id, session_id
        return self

    def maintain(self, **args):
        from .maintenance import execute, READ_ACTIONS
        if args.get('action') in READ_ACTIONS:
            with self.cart.read_snapshot():
                return execute(self.cart, args)
        return execute(self.cart, args)

    def discover(self, query='', **options):
        from .discovery import search
        return search(self.cart, query, **options)

    @property
    def revision(self):
        from .history import revision
        return revision(self.cart)

    # -- recalling memory ------------------------------------------------------
    def recall(self, tags: Iterable[str] = (), **options):
        return recall_box.recall(self.cart, tags, **options)

    def workspace(self, action, arguments=None):
        from .workspace import execute
        return execute(self, action, arguments)

    def query(self, sql, parameters=(), **options):
        from .query import execute
        return execute(self.cart, sql, parameters, **options)

    def begin_session(self, source_session_id, **options):
        """Host calls once at working activation; repeated attaches reuse the opening."""
        from .lifecycle import open_session
        self.logical_session_id = open_session(self.cart, source_session_id, **options)
        return self.logical_session_id

    def dashboard(self, name='welcome', *, session_id=None):
        from .dashboard import retrieve
        return retrieve(self.cart, name, session_id=session_id or self.logical_session_id)

    def load(self, note_ids: Sequence[str], reinforce_links: bool = True):
        return recall_box.load(self.cart, note_ids, reinforce_links=reinforce_links)

    # -- context reformation -----------------------------------------------------
    def pressure(self, used_tokens: int, window_tokens: int, reserved_tokens: int):
        return reformation.pressure(used_tokens, window_tokens, reserved_tokens,
                                    self.cart.setting("reform_threshold"))

    def reform(self, board, decision, budget_tokens: int, auto_file: bool = True):
        return reformation.reform(self.cart, board, decision, budget_tokens,
                                  written_by=self.agent_id, auto_file=auto_file)

    # -- triggers --------------------------------------------------------------
    @staticmethod
    def trigger(event) -> triggers.TriggerAction:
        return triggers.decide(event)

    @staticmethod
    def pcs_directive() -> str:
        return triggers.pcs_directive()

    # -- staff: reviews, outcomes, card ----------------------------------------
    def add_review(self, reviewer: str, reviewer_role: str, text: str,
                   rating: Optional[float] = None, project: Optional[str] = None) -> int:
        return staff.add_review(self.cart, reviewer, reviewer_role, text, rating, project)

    def reviews(self) -> list:
        return staff.reviews(self.cart)

    def record_outcome(self, specialty: str, task_ref: str, result: str, difficulty: float,
                       verified_by: str) -> int:
        return staff.record_outcome(self.cart, specialty, task_ref, result, difficulty, verified_by)

    def set_avatar(self, svg: str, description: str, original_design_attested: bool) -> None:
        staff.set_avatar(self.cart, svg, description, self.agent_id, original_design_attested)

    def set_scan_line(self, scan_line: str) -> None:
        staff.set_scan_line(self.cart, scan_line)

    def card(self) -> dict:
        return staff.card(self.cart)

    def resume(self) -> str:
        return staff.resume_markdown(self.cart)

    # -- the side log: actions, append-only, sealed ------------------------------
    def log(self, rows: Iterable[dict], commission: Optional[str] = None) -> int:
        return sidelog.append(self.cart, rows, commission)

    def log_recent(self, commission: Optional[str] = None, limit: int = 8) -> list:
        return sidelog.recent(self.cart, commission, limit)

    def log_search(self, words: Iterable[str], limit: int = 10) -> list:
        return sidelog.search(self.cart, words, limit)

    # -- settings and calibration ----------------------------------------------
    def settings(self) -> dict:
        return self.cart.settings()

    def change_setting(self, key: str, value: float, changed_by: Optional[str] = None,
                       reason: str = "") -> None:
        self.cart.change_setting(key, value, changed_by or self.agent_id, reason)

    def profiles(self) -> list:
        return self.cart.profiles()

    def tune_profile(self, name: str, changed_by: Optional[str] = None, reason: str = "",
                     **values) -> None:
        self.cart.tune_profile(name, changed_by or self.agent_id, reason, **values)

    # -- model-callable tools -------------------------------------------------
    def tools(self, limits=None, reform_handler=None, enabled=None, prefix=None, label=None):
        """The agent's memory as model-callable tools (see tools.py)."""
        from .tools import MemoryTools, ToolLimits
        return MemoryTools(self, limits or ToolLimits(), reform_handler=reform_handler,
                           enabled=enabled, prefix=prefix, label=label)

    def archive_info(self):
        """For a frozen archive: whose, frozen when, and what it is. Otherwise None."""
        return self.cart.archive_info()

    def session_start(self, budget_tokens=None, task_tags=None, working_context_tokens=None):
        """What the agent sees of its memory when a session opens (see hydration.py).
        Returns (sections, report), the shape of the Bridge's memory_loader.load_index.
        Pass working_context_tokens (measured) and no budget, so the agent's own
        hydration settings decide the budget (decision 49)."""
        from .hydration import load_session
        return load_session(self, budget_tokens, task_tags,
                            working_context_tokens=working_context_tokens)

    def settings_table(self) -> list:
        """Every setting with its value, range, whether the agent may tune it, and what it does."""
        return [dict(r) for r in self.cart.conn.execute(
            "SELECT key, value, min_value, max_value, tunable_by_agent, description"
            " FROM config ORDER BY key")]

    def setting_history(self, key=None, limit: int = 5) -> list:
        """The settings journal, newest first (who changed what, from what, and why)."""
        sql = "SELECT key, old_value, new_value, changed_by, reason, at FROM config_changes"
        args = ()
        if key is not None:
            sql, args = sql + " WHERE key = ?", (key,)
        return [dict(r) for r in self.cart.conn.execute(
            sql + " ORDER BY rowid DESC LIMIT ?", args + (limit,))]

    # -- migration ---------------------------------------------------------------
    def import_profile(self, memory_dir: str, grace_days: float = 30, proprietary: bool = False):
        """Import an existing agent's markdown memory folder (see importer.py)."""
        from . import importer
        plan = importer.plan_import(memory_dir)
        with self.cart.transaction(immediate=True, operation='import_profile'):
            return plan, importer.apply_import(self, plan, grace_days=grace_days,
                                               proprietary=proprietary)

    def move_in(self, memory_dir: str, sheet, grace_days: float = 30):
        """Move in with the agent's own move-in sheet (a dict or a path) applied (movein.py)."""
        from . import movein
        if isinstance(sheet, str):
            sheet = movein.load_sheet(sheet)
        with self.cart.transaction(immediate=True, operation='move_in'):
            return movein.move_in(self, memory_dir, sheet, grace_days=grace_days)

    # -- sharing ---------------------------------------------------------------
    def backup(self, destination: str) -> str:
        """A full, consistent copy of this mind (everything, proprietary included)."""
        return self.cart.backup_to(destination)

    def export_clean(self, destination: str, include_flagged: bool = False):
        return export_box.export_clean(self.cart, destination, include_flagged=include_flagged)
