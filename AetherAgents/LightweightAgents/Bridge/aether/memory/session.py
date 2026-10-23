"""Session memory (MOD-12): one agent's cartridge inside a Bridge session.

Document 06 rollout steps 2 to 4, plus the memory gate (decision 47). Everything is
switched on by ONE file, the pointer `memory/cartridge.json` in the agent's own profile
(decision 46). Without it the session hydrates exactly as before (HW-08). Removing the
pointer undoes everything.

Inputs:  a sanctum-verified profile folder and the identities the host verified for it
         (Memory is not an identity authority); the minds folder (decision 45), from the host,
         the AETHER_MINDS_DIR environment variable or the journaled installation setting
         `memory.minds_folder`; the session's measured working context.
Outputs:
    read_pointer(profile)        CartridgePointer or None (no pointer: memory stays off)
    hydrate(pointer, ...)        (sections, report): the budgeted memory block that replaces
                                 HW-08's index slices. Read-only: hydration never writes.
                                 task_tags= adds the task recall (report: recall_ids,
                                 revision, binding_sha256, block_sha256).
    cartridge_binding(pointer)   {owner, cartridge, revision, binding_sha256} for integrity
                                 guards; refuses a tampered cartridge (T07 seam 2).
    ClosureMemoryWriter(...)     files closure memories into the owner's cartridge with
                                 host provenance and operation receipts (T07 seam 1).
    SessionMemory(pointer, ...)  the session's writable owner handle:
        .wrap(belt)              the agent's memory tools beside its file tools (MemoryBelt)
        .task_start(text)        a memory check to show with a new task (trigger 2)
        .after_error(tool, r)    past memories beside a tool error (trigger 3)
        .gate(tool, args)        pause before an irreversible or config call when the agent's
                                 own memory holds a warning about it (decision 47)
        .setting / .check_kind   the agent's own settings and memory kinds (reformation)
        .file(filings, ...)      files reformation items with receipts
        .close()                 seals the cartridge
Never:   decides for the agent, writes a pointer, writes a cartridge during hydration, or
         opens a cartridge whose owner is not the verified profile.
"""
from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Dict, Optional, Tuple

from aether.paths import LIGHTWEIGHT_ROOT, REPOSITORY_ROOT

from .fabric import gate_recall, memory_fabric, open_engine, open_owner_engine

POINTER = Path("memory") / "cartridge.json"
ENV_MINDS = "AETHER_MINDS_DIR"
SETTING_MINDS = "memory.minds_folder"
INSTALLATION_SETTINGS = LIGHTWEIGHT_ROOT / ".aether_control" / "installation_settings.json"  # = aether.config.settings.DEFAULT_PATH
TRIGGER_BUDGET_TOKENS = 600


class SessionMemoryError(ValueError):
    pass


@dataclass(frozen=True)
class CartridgePointer:
    profile: Path
    owner: str
    home: Path
    mounts: Tuple[Path, ...]
    minds_folder: Path

    @property
    def inside_repository(self) -> bool:
        return self.home.is_relative_to(REPOSITORY_ROOT.resolve())


def minds_folder(explicit=None, *, settings_path=INSTALLATION_SETTINGS) -> Optional[Path]:
    """Host choice, then AETHER_MINDS_DIR, then the journaled installation setting."""
    if explicit:
        return Path(explicit)
    if os.environ.get(ENV_MINDS):
        return Path(os.environ[ENV_MINDS])
    if Path(settings_path).exists():
        from aether.config.settings import InstallationSettings
        value = InstallationSettings(settings_path).get(SETTING_MINDS)
        if value:
            return Path(value)
    return None


def _inside(folder: Path, name: Any, what: str) -> Path:
    if not isinstance(name, str) or not name.strip():
        raise SessionMemoryError(f"cartridge pointer: {what} must be a file name in the minds folder")
    relative = PurePosixPath(name.replace("\\", "/"))
    if relative.is_absolute() or ".." in relative.parts or ":" in name:
        raise SessionMemoryError(f"cartridge pointer: {what} must stay inside the minds folder")
    resolved = (folder / Path(*relative.parts)).resolve()
    if not resolved.is_relative_to(folder.resolve()):
        raise SessionMemoryError(f"cartridge pointer: {what} must stay inside the minds folder")
    return resolved


def read_pointer(profile, *, identities=None, minds=None,
                 settings_path=INSTALLATION_SETTINGS) -> Optional[CartridgePointer]:
    """The pointer, checked: owner is this profile, every path inside the minds folder.

    identities: the names the HOST verified for this profile (GUID, readable ID, name,
    aliases). Memory is not an identity authority, so it never derives them itself.
    """
    profile = Path(profile)
    path = profile / POINTER
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SessionMemoryError(f"cartridge pointer unreadable: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("mounts", []), list):
        raise SessionMemoryError('cartridge pointer must be {"owner", "home", "mounts": [...]}')
    owner = data.get("owner")
    if identities is None:
        from aether.integrity.sanctum import verified_identity_names, SanctumError
        try:
            identities = verified_identity_names(profile)
        except (OSError, ValueError, SanctumError) as exc:
            raise SessionMemoryError(f"profile identity unverified: {exc}") from exc
    accepted = {n.casefold() for n in identities if isinstance(n, str) and n}
    if not isinstance(owner, str) or owner.casefold() not in accepted:
        raise SessionMemoryError(f"cartridge pointer owner {owner!r} is not this profile ({profile.name})")
    folder = minds_folder(minds, settings_path=settings_path)
    if folder is None:
        raise SessionMemoryError(f"{profile.name} has a cartridge pointer but no minds folder is set "
                                 f"({ENV_MINDS} or the installation setting {SETTING_MINDS})")
    folder = folder.resolve()
    home = _inside(folder, data.get("home"), "home")
    if not home.is_file():
        raise SessionMemoryError(f"cartridge not found: {home}")
    mounts = tuple(_inside(folder, m, "mount") for m in data.get("mounts", []))
    return CartridgePointer(profile, owner, home, mounts, folder)


def _binding_sha256(owner: str, cartridge: str, revision: int) -> str:
    raw = json.dumps({"owner": owner, "cartridge": cartridge, "revision": revision},
                     sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def cartridge_binding(pointer: CartridgePointer) -> dict:
    """The mind's identity for integrity guards (T07 seam 2, D-P1): owner, cartridge and the
    engine's revision, never a raw file digest (SQLite WAL and page reuse make that unstable).
    Read-only. A cartridge edited outside the engine (open_report.tampered) is refused."""
    with open_engine(str(pointer.home), read_only=True) as mind:
        if mind.agent_id != pointer.owner:
            raise SessionMemoryError(f"cartridge belongs to {mind.agent_id!r}, not {pointer.owner!r}")
        if mind.open_report.tampered:
            raise SessionMemoryError("cartridge was edited outside the engine since it was last sealed")
        revision = mind.revision
    return {"owner": pointer.owner, "cartridge": pointer.home.name, "revision": revision,
            "binding_sha256": _binding_sha256(pointer.owner, pointer.home.name, revision)}


def hydrate(pointer: CartridgePointer, *, working_context_tokens: Optional[int] = None,
            task_tags=None):
    """HW-08's (sections, report) shape, from the cartridge, opened read-only.

    task_tags (T07 seam 3): host-derived tags of the captured objective; hydration adds a
    "MEMORY RECALL FOR THIS TASK" block and reports recall_ids. Recall refreshes nothing.
    The report carries ids, the revision and the digest of the block, never memory prose
    beyond what the sections already hold."""
    memory_fabric()
    from memory_fabric import hydration
    tags = sorted({t for t in (task_tags or ()) if isinstance(t, str) and t.strip()})
    with open_engine(str(pointer.home), read_only=True) as mind:
        if mind.agent_id != pointer.owner:
            raise SessionMemoryError(f"cartridge belongs to {mind.agent_id!r}, not {pointer.owner!r}")
        sections, report = hydration.load_session(mind, working_context_tokens=working_context_tokens,
                                                  task_tags=tags or None)
        revision = mind.revision
    report = {**report, "source": "memory-fabric", "cartridge": pointer.home.name,
              "inside_repository": pointer.inside_repository, "revision": revision,
              "binding_sha256": _binding_sha256(pointer.owner, pointer.home.name, revision),
              "block_sha256": hashlib.sha256("\n".join(sections).encode("utf-8")).hexdigest()}
    return sections, report


class _Provenanced:
    """The engine as the memory tools see it, with host-stamped provenance on every write:
    written_by is the verified owner and source_ref the host's, never model arguments."""

    def __init__(self, engine, owner: str, source_ref: str):
        self._engine, self._owner, self._source_ref = engine, owner, source_ref

    def __getattr__(self, name):
        return getattr(self._engine, name)

    def remember(self, title, body, kind, tags=(), **options):
        options["written_by"] = self._owner
        options["source_ref"] = self._source_ref
        return self._engine.remember(title, body, kind, tags, **options)


class ClosureMemoryWriter:
    """T07 seam 1: file an agent's closure memories into its OWN cartridge.

    The owner comes from the sanctum-verified pointer (open_owner_engine binds it); the
    host stamps source_ref and adds host_tags; each entry has its own operation_id
    "<prefix>:memory:<ordinal>" so an identical retry files nothing twice; all entries go
    in one transaction. Returns ids and revisions only (no prose).
    """

    def __init__(self, pointer: CartridgePointer, *, session_id: str, source_ref: str,
                 operation_prefix: str, host_tags=(), engine=None):
        if not isinstance(source_ref, str) or not source_ref.strip():
            raise SessionMemoryError("closure memories need a host source_ref")
        self.pointer = pointer
        self.session_id = session_id
        self.source_ref = source_ref
        self.operation_prefix = operation_prefix
        self._engine = engine  # Ordinary Studio lends its already owner-bound connection.
        self.host_tags = tuple(dict.fromkeys(t for t in host_tags if isinstance(t, str) and t.strip()))

    def publish_closure(self, state_path, plan, failure=None):
        from .foundations import begin, publish
        engine = self._engine or open_owner_engine(str(self.pointer.home), owner_id=self.pointer.owner,
                                                   session_id=self.session_id)
        try:
            prior = engine.cart.conn.execute('SELECT session_id FROM session_events WHERE closure_id=? '
                'AND source_system=? ORDER BY event_seq LIMIT 1', (plan['closure_id'], 'bridge')).fetchone()
            sid = prior[0] if prior else (getattr(engine, 'logical_session_id', None) or
                begin(engine, self.session_id, source_system='bridge', source_ref=str(self.pointer.profile)))
            return publish(engine, sid, plan['closure_id'], state_path,
                           carry_text=plan.get('system1_content'), failure=failure)
        finally:
            if self._engine is None:
                engine.close()

    def start_closure(self, plan):
        from .foundations import begin
        memory_fabric()
        from memory_fabric import lifecycle
        engine = self._engine or open_owner_engine(str(self.pointer.home), owner_id=self.pointer.owner,
                                                   session_id=self.session_id)
        try:
            prior = engine.cart.conn.execute('SELECT event_id FROM session_events WHERE closure_id=? '
                'AND source_system=? LIMIT 1', (plan['closure_id'], 'bridge')).fetchone()
            if not prior:
                sid = getattr(engine, 'logical_session_id', None) or begin(
                    engine, self.session_id, source_system='bridge', source_ref=str(self.pointer.profile))
                # A second authored closure on a still-open handle is new work;
                # an old closure_id was handled by the prior-event lookup above.
                if engine.cart.conn.execute("SELECT 1 FROM session_events WHERE session_id=? AND kind='closed'", (sid,)).fetchone():
                    sid = begin(engine, self.session_id, source_system='bridge',
                                source_ref=str(self.pointer.profile), new_work=True)
                lifecycle.append(engine.cart, 'closure_started', sid, source_system='bridge',
                    source_event_id='closure-start:' + plan['closure_id'], closure_id=plan['closure_id'],
                    summary='Existing closure path started')
            else:
                sid = engine.cart.conn.execute('SELECT session_id FROM session_events WHERE event_id=?', (prior[0],)).fetchone()[0]
            # Retain original input for publication-only recovery after CLOSED.
            # The state file's plan digest authenticates it; no later carry is used.
            root = (self.pointer.profile / 'logs' / 'closure_state').resolve()
            packet = root / (plan['closure_id'] + '.publication.json')
            if packet.resolve().parent != root:
                raise SessionMemoryError('closure publication path escapes profile')
            root.mkdir(parents=True, exist_ok=True)
            from aether.agent.closure import ClosureManager
            from aether.integrity.sealbox import atomic_write_bytes
            data = json.dumps({'session_id': sid, 'plan_sha256': ClosureManager._plan_hash(plan),
                               'carry_text': plan['system1_content']}, sort_keys=True, ensure_ascii=False)
            if packet.exists():
                if packet.read_text(encoding='utf-8') != data:
                    raise SessionMemoryError('retained closure publication input changed')
            else:
                atomic_write_bytes(packet, data.encode('utf-8'))
        finally:
            if self._engine is None:
                engine.close()

    def operation_ids(self, count: int) -> list:
        return [f"{self.operation_prefix}:memory:{n}" for n in range(1, count + 1)]

    def current_revision(self):
        if self._engine is not None:
            return self._engine.revision
        with open_engine(str(self.pointer.home), read_only=True) as engine:
            return engine.revision

    def __call__(self, memories) -> dict:
        memory_fabric()
        from memory_fabric.tools import MemoryTools
        engine = self._engine if self._engine is not None else open_owner_engine(
            str(self.pointer.home), owner_id=self.pointer.owner, session_id=self.session_id)
        try:
            engine.bind_session(self.pointer.owner, self.session_id)
            before = engine.revision
            tools = MemoryTools(_Provenanced(engine, self.pointer.owner, self.source_ref),
                                enabled=("memory_remember",))
            ids = []
            with engine.cart.transaction(immediate=True,
                                         operation="studio_closure" if self._engine is not None else "campaign_closure",
                                         description=f"closure memories {self.operation_prefix}"):
                for entry, operation_id in zip(memories, self.operation_ids(len(memories))):
                    args = {k: v for k, v in entry.items() if v is not None}
                    args["tags"] = list(dict.fromkeys([*entry["tags"], *self.host_tags]))
                    args["operation_id"] = operation_id
                    result = tools.dispatch("memory_remember", args)
                    ids.append(result["id"])
            after = engine.revision
        finally:
            if self._engine is None:
                engine.close()
        return {"note_ids": ids, "revision_before": before, "revision_after": after}

    def verify(self, note_ids) -> None:
        if self._engine is not None:
            self._verify(self._engine, note_ids)
            return
        with open_engine(str(self.pointer.home), read_only=True) as mind:
            self._verify(mind, note_ids)

    def _verify(self, mind, note_ids):
        if mind.agent_id != self.pointer.owner:
            raise SessionMemoryError("cartridge owner changed")
        for note_id in note_ids:
            row = mind.cart.conn.execute("SELECT written_by, source_ref FROM notes WHERE note_id = ?",
                                         (note_id,)).fetchone()
            if row is None or tuple(row) != (self.pointer.owner, self.source_ref):
                raise SessionMemoryError(f"{note_id} lacks the closure's owner or provenance")


def orientation(report: dict) -> str:
    memory_fabric()
    from memory_fabric import hydration
    text = hydration.orientation(report)
    if report.get("inside_repository"):
        text += ("\nWARNING: this cartridge lives inside the repository; live minds belong in the "
                 "minds folder outside it (decision 45).")
    return text


class SessionMemory:
    def __init__(self, pointer: CartridgePointer, *, session_id: Optional[str] = None,
                 working_context_tokens: Optional[int] = None,
                 journal: Optional[Callable[[Dict[str, Any]], None]] = None,
                 trigger_budget_tokens: int = TRIGGER_BUDGET_TOKENS,
                 defer_activation: bool = False):
        self.pointer = pointer
        self.session_id = session_id or "bridge-" + uuid.uuid4().hex
        self.working_context_tokens = working_context_tokens
        self.trigger_budget_tokens = trigger_budget_tokens
        self.journal = journal
        self.engine = open_owner_engine(str(pointer.home), owner_id=pointer.owner,
                                        session_id=self.session_id)
        self.publication_recovery = []
        self._activated = False
        self._welcome_shown = False
        from aether.integrity.gate import MemoryGate
        self._gate = MemoryGate(recall=gate_recall(self.engine, trigger_budget_tokens),
                                journal=self._journal_gate)
        if not defer_activation:
            self.activate()

    def activate(self):
        """After attachment checks: recover advisory publication, then open work."""
        if self._activated:
            return
        from .foundations import begin, reconcile_publications
        try:
            self.publication_recovery = reconcile_publications(self.engine, self.pointer.profile)
        except Exception as exc:
            # Background publication availability must not prevent normal work.
            # Owner integrity is checked separately, before governed activation.
            self.publication_recovery = [{'status': 'publication_pending', 'error': str(exc)}]
        begin(self.engine, self.session_id, source_system='bridge', source_ref=str(self.pointer.profile), new_work=True)
        self._activated = True

    def _journal_gate(self, record: Dict[str, Any]) -> None:
        if self.journal is not None:
            # The gate's own "kind" (irreversible or config) must not replace the log kind.
            self.journal({**record, "kind": "memory_gate", "moment": record.get("kind")})

    @property
    def closed(self) -> bool:
        return self.engine is None

    def closure_writer(self, closure_id: str):
        """Host provenance for an already validated ordinary owner closure.

        Stable closure IDs, rather than a new hydration's session ID, preserve
        operation receipts and provenance across an interrupted closure retry.
        The caller owns the connection and seals it only after CLOSED.
        """
        if self.closed:
            raise SessionMemoryError('memory session is closed')
        from urllib.parse import quote
        source = f'aether-studio:{quote(self.pointer.owner, safe="")}/closure:{quote(closure_id, safe="")}'
        return ClosureMemoryWriter(self.pointer, session_id=self.session_id, source_ref=source,
            operation_prefix='studio:'+closure_id, host_tags=('studio-closure',), engine=self.engine)

    def wrap(self, belt):
        fabric = memory_fabric()
        from memory_fabric.belt import MemoryBelt
        limit = getattr(belt, "max_result_chars", None)
        if self.working_context_tokens:
            limits = fabric.ToolLimits.for_context(self.working_context_tokens, limit)
        else:
            limits = fabric.ToolLimits(max_result_chars=limit)
        return MemoryBelt(belt, self.engine.tools(limits))

    def _block(self, event, **kwargs):
        from memory_fabric.host import memory_block
        return memory_block(self.engine, event, budget_tokens=self.trigger_budget_tokens, **kwargs)

    def task_start(self, text: str) -> str:
        """Trigger 2: the memories this task touches, or a nudge to recall them."""
        from memory_fabric.host import tags_from_text
        from memory_fabric.triggers import Event
        block = self._block(Event.TASK_START, tags=tags_from_text(text or ""))
        memory = block.text if block.act and block.note_ids else ''
        if not self._welcome_shown:
            from memory_fabric.dashboard import render_text
            try:
                welcome = render_text(self.engine.dashboard())
            except Exception as exc:
                welcome = f'Welcome Dashboard unavailable: {type(exc).__name__}: {exc}'
            pending = [r for r in self.publication_recovery if r.get('status') == 'publication_pending']
            if pending:
                welcome += (f'\nClosure publication unresolved: {len(pending)} item(s). Advisory only; normal work may continue. '
                            'Inspect retained closure evidence in logs/closure_state for details.')
            self._welcome_shown = True
            return welcome + ('\n\n' + memory if memory else '')
        return memory

    def after_error(self, tool: str, result: Any) -> Any:
        """Trigger 3: put relevant past memories beside a new tool error, when there are any."""
        if not isinstance(result, dict) or not isinstance(result.get("error"), str) \
                or tool.startswith("memory_") or "memory_gate" in result:
            return result
        from memory_fabric.triggers import Event
        block = self._block(Event.TOOL_ERROR, error_text=f"{tool} {result['error']}")
        if not (block.act and block.note_ids):
            return result
        return {**result, "memory": block.text}

    def gate(self, tool: str, args: Any) -> Tuple[Optional[dict], Any]:
        """(pause_result or None, args to dispatch). confirm=true is the agent's, never the tool's."""
        if not isinstance(args, dict):
            return None, args
        decision = self._gate.check(tool, args)
        dispatched = args if decision.kind is None else {k: v for k, v in args.items() if k != "confirm"}
        if decision.pause:
            return {"error": decision.text,
                    "memory_gate": {"kind": decision.kind, "warnings": list(decision.warnings)}}, dispatched
        return None, dispatched

    def setting(self, key: str) -> float:
        """One of the agent's own memory settings, such as reform_threshold."""
        return self.engine.cart.setting(key)

    def check_kind(self, kind: str) -> str:
        """The memory kind these words name in this cartridge (UnknownKind lists the choices)."""
        from memory_fabric import kinds
        return kinds.resolve(kind, [p["profile"] for p in self.engine.cart.profiles()])

    def file(self, filings, *, operation_prefix: str, transition=None, before_commit=None) -> list:
        """File reformation items (document 08 §D6) with receipts: a retry never files twice."""
        tools = self.engine.tools()
        ids = []
        callback = (lambda candidate: before_commit(list(ids), candidate)) if before_commit is not None else None
        with self.engine.cart.transaction(immediate=True, operation="context_reformation",
                                          transition=transition, before_commit=callback):
            for f in filings:
                result = tools.dispatch("memory_remember", {
                    "title": f["title"], "body": f"[{f['source_ref']}]\n{f['body']}", "kind": f["kind"],
                    "tags": f["tags"], "operation_id": f"{operation_prefix}:{f['ref']}"})
                if "error" in result:
                    raise SessionMemoryError(str(result["error"]))
                ids.append(result.get("id"))
        return ids

    def close(self) -> Optional[str]:
        if self.engine is None:
            return None
        engine, self.engine = self.engine, None
        return engine.close()
