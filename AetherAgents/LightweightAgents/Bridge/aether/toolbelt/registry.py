"""Tool registry (MOD-08, Bridge Overhaul Milestone 2, FR-03): which tools may run, for every agent.

Tools belong to no agent. V2 approvals bind the full offered and source definitions;
the original name/description/parameters digest remains available for V1 replay.
Three states: APPROVED, UNAPPROVED, WITHDRAWN.
- Built-in tools (the Bridge's own reviewed code: the file tools, calculate, delete_file,
  run_command) are APPROVED from source, unless an operator withdraws one.
- Every other tool (an MCP server's, or any attached later) arrives UNAPPROVED and runs only
  once approved. An approval binds to the schema digest that was approved, so a tool whose
  schema changes is UNAPPROVED again.
- Session machinery the belt does not carry (memory_*, close_session, the durable controls,
  context_*) is outside this registry; it follows its session's own rules.
Every approval and withdrawal names who and why and is journaled by ConfigJournal.
Discovery/schema changes cannot be undone; restoring approval requires fresh inspection.
Registry withdrawal governs model-offered dispatch, not campaign staff writes, which
are governed by staffing authority.

Inputs:  the registry file (default `Lightweight/.aether_control/tool_registry.json`,
         git-ignored; with no file, every built-in is approved and nothing else is).
Outputs: ToolRegistry: state(schema), offered(tools), check(schema), register(schema,
         source), approve, withdraw, records, history, undo; digest(schema).
Never:   offers a tool it would refuse to run, or runs one it would not offer; approves a
         schema it has not seen; changes anything without who and why.

CLI:     python -m aether.toolbelt.registry list
         python -m aether.toolbelt.registry inspect NAME
         python -m aether.toolbelt.registry approve NAME --expected-token TOKEN --by WHO --reason WHY
         python -m aether.toolbelt.registry withdraw NAME --by WHO --reason WHY
         python -m aether.toolbelt.registry history [NAME]
         python -m aether.toolbelt.registry undo CHANGE_ID --expected-token TOKEN --by WHO --reason WHY
"""
from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from aether.config.journal import Change, ConfigJournal, ConfigJournalError
from aether.config.model_settings_store import StaleSettings
from aether.paths import LIGHTWEIGHT_ROOT

DEFAULT_PATH = LIGHTWEIGHT_ROOT / ".aether_control" / "tool_registry.json"
APPROVED, UNAPPROVED, WITHDRAWN = "approved", "unapproved", "withdrawn"
BUILTIN_TOOLS = frozenset({"list_roots", "list_dir", "read_file", "glob", "search", "write_file",
                           "edit_file", "calculate", "delete_file", "run_command"})
_PREFIX = "tool:"
POLICY = "tool-authority-2"
LEGACY_POLICY = "tool-authority-1"


def definition_digest(schema):
    """V2 binds the entire offered definition, including strict/output extensions."""
    try:
        raw = json.dumps(schema, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)
    except (ValueError, TypeError) as exc:
        raise ToolRegistryError('Tool definition or arguments must be finite JSON; correct the source before retrying') from exc
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def approval_token(record):
    return definition_digest(record or {})


class ToolRegistryError(ConfigJournalError):
    pass


class ToolRegistryUnavailable(ToolRegistryError):
    pass


def _who_why(by: Any, reason: Any) -> None:
    if not isinstance(by, str) or not by.strip():
        raise ToolRegistryError("say who is making the change (by)")
    if not isinstance(reason, str) or not reason.strip():
        raise ToolRegistryError("say why the change is made (reason); it is kept in the journal")


def tool_name(schema: Dict[str, Any]) -> str:
    return schema.get("function", {}).get("name", "")


def digest(schema: Dict[str, Any]) -> str:
    """The identity an approval binds to: name, description and parameters."""
    fn = schema.get("function", {})
    raw = json.dumps({"name": fn.get("name"), "description": fn.get("description", ""),
                      "parameters": fn.get("parameters", {})},
                     sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ToolState:
    name: str
    state: str
    source: str
    digest: str
    by: Optional[str] = None
    at: Optional[str] = None
    reason: Optional[str] = None

    @property
    def runnable(self) -> bool:
        return self.state == APPROVED


def evaluate(schema, record, *, policy=POLICY, source_definition=None):
    """One versioned rule for inspection, approval postcondition, dispatch and replay.

    Unversioned retained registry values can be interpreted with LEGACY_POLICY;
    new dispatch requires explicit V2 approval for external definitions.
    """
    if policy not in (POLICY, LEGACY_POLICY):
        raise ToolRegistryError('Unknown tool authority policy; use a supported recorded policy')
    name, seen = tool_name(schema), digest(schema)
    record = record or {}
    source_definition = record.get('source_definition', schema) if source_definition is None else source_definition
    meta = {k: record.get(k) for k in ('by', 'at', 'reason')}
    source = 'builtin' if name in BUILTIN_TOOLS else record.get('source', 'unregistered')
    state = UNAPPROVED
    if record.get('state') == WITHDRAWN:
        state = WITHDRAWN
    elif name in BUILTIN_TOOLS:
        state = APPROVED
    elif record.get('state') == APPROVED and record.get('digest') == seen == record.get('pending_digest'):
        if policy == LEGACY_POLICY or (record.get('policy') == POLICY and
                record.get('definition_digest') == definition_digest(schema) == record.get('pending_definition_digest') and
                record.get('source_definition_digest') == definition_digest(source_definition) == record.get('pending_source_definition_digest')):
            state = APPROVED
    return ToolState(name, state, source, seen, **meta)


def refusal(current):
    if current.state == WITHDRAWN:
        return (f"tool '{current.name}' is withdrawn by {current.by}: {current.reason}. "
                "Open Mission Control /projects/tools, inspect the definition and explicitly approve it before retrying.")
    return (f"tool '{current.name}' is unapproved (new, changed or legacy approval). "
            "Open Mission Control /projects/tools, refresh, inspect the current definition and approve it before retrying.")


def verify_admission(receipt):
    """Replay only the recorded policy/snapshot. Never substitute today's registry."""
    if receipt.get('schema') != 'aether-tool-admission-1':
        raise ToolRegistryError('Unsupported tool admission receipt version')
    auth = receipt['authorization']
    current = evaluate(receipt['definition'], auth['record'], policy=auth['policy'],
                       source_definition=receipt['source_definition'])
    if (receipt['tool'] != current.name or auth['state'] != asdict(current)
            or type(auth['allowed']) is not bool or auth['allowed'] != current.runnable
            or auth['reason'] != (None if current.runnable else refusal(current))
            or receipt['disposition'] != ('ADMITTED' if current.runnable else 'REFUSED')):
        raise ToolRegistryError('Tool admission receipt disagrees with its recorded authority')
    return current


class ToolRegistry:
    def __init__(self, path=DEFAULT_PATH):
        self.journal = ConfigJournal(path)

    @property
    def path(self) -> Path:
        return self.journal.path

    # -- reading -----------------------------------------------------------
    def _record(self, name: str) -> Optional[Dict[str, Any]]:
        return self.journal.get(_PREFIX + name)

    def state(self, schema: Dict[str, Any], *, source_definition=None) -> ToolState:
        with self.transaction():
            return evaluate(schema, self._record(tool_name(schema)), source_definition=source_definition)

    @contextmanager
    def transaction(self):
        """Same ConfigJournal path lock for all registry instances/processes.

        Admission callers persist/fsync their receipt here, then leave the scope
        BEFORE execution. Withdrawal is ordered against admission, not completion.
        """
        try:
            with self.journal.transaction():
                yield
        except ToolRegistryError:
            raise
        except ConfigJournalError as exc:
            if isinstance(exc.__cause__, StaleSettings):
                raise ToolRegistryUnavailable('Tool registry unavailable; retry after the registry transaction finishes. '
                                              'If it persists, inspect Mission Control /projects/tools and registry storage.') from exc
            raise ToolRegistryError(f'Tool registry refused: {exc}') from exc

    @contextmanager
    def admission(self, schema, *, source_definition=None):
        with self.transaction():
            record = self._record(tool_name(schema))
            current = evaluate(schema, record, source_definition=source_definition)
            yield dict(policy=POLICY, registry=str(self.path), record=deepcopy(record),
                       state=asdict(current), allowed=current.runnable,
                       reason=None if current.runnable else refusal(current))

    def offered(self, tools: Iterable[Dict[str, Any]], *, definitions=None) -> List[Dict[str, Any]]:
        """Only the tools that would run: what a model is offered equals what it may execute."""
        with self.transaction():
            return [deepcopy(t) for t in tools if self.state(t, source_definition=(definitions or {}).get(tool_name(t))).runnable]

    def check(self, schema: Dict[str, Any]) -> ToolState:
        current = self.state(schema)
        if not current.runnable:
            raise ToolRegistryError(refusal(current))
        return current

    def records(self) -> Dict[str, Dict[str, Any]]:
        with self.transaction():
            return {k[len(_PREFIX):]: v for k, v in self.journal.all().items() if k.startswith(_PREFIX)}

    def history(self, name: Optional[str] = None, limit: int = 50) -> List[Change]:
        with self.transaction():
            return self.journal.history(None if name is None else _PREFIX + name, limit)

    # -- changing ----------------------------------------------------------
    def register(self, schema: Dict[str, Any], *, source: str, by: str = "aether-bridge", source_definition=None) -> ToolState:
        """An external tool announces itself. It arrives UNAPPROVED; a seen schema is kept."""
        with self.transaction():
            return self._register(deepcopy(schema), source=source, by=by,
                                  source_definition=deepcopy(schema if source_definition is None else source_definition))

    def _register(self, schema, *, source, by, source_definition):
        name = tool_name(schema)
        if not name:
            raise ToolRegistryError("a tool schema needs a function name")
        if name in BUILTIN_TOOLS:
            raise ToolRegistryError(f"'{name}' is a built-in tool; an external tool may not take its name")
        record = self._record(name)
        if not isinstance(source, str) or not source.strip():
            raise ToolRegistryError('Name the source of the tool definition')
        if record and record.get('source') != source:
            raise ToolRegistryError(f"'{name}' belongs to {record.get('source')}; use a distinct source namespace")
        seen = digest(schema)
        # pending_digest is the latest schema announced; digest is the one an approval bound to.
        if (record is None or record.get("pending_definition_digest") != definition_digest(schema)
                or record.get('pending_source_definition_digest') != definition_digest(source_definition)):
            entry = dict(record or {"state": UNAPPROVED})
            entry.update(source=source, pending_digest=seen, schema=schema,
                         pending_definition_digest=definition_digest(schema), source_definition=source_definition,
                         pending_source_definition_digest=definition_digest(source_definition))
            if entry.get('state') != WITHDRAWN:
                entry['state'] = UNAPPROVED  # Reverting a schema cannot revive old approval.
            self.journal.set(_PREFIX + name, entry, by=by,
                             reason=f"announced by {source}" if record is None else f"schema changed at {source}")
        return self.state(schema)

    def approve(self, name: str, *, by: str, reason: str, expected_token=None) -> Change:
        with self.transaction():
            return self._approve(name, by=by, reason=reason, expected_token=expected_token)

    def _approve(self, name, *, by, reason, expected_token):
        _who_why(by, reason)
        record = self._record(name)
        if name in BUILTIN_TOOLS:
            if not record or record.get("state") != WITHDRAWN:
                raise ToolRegistryError(f"'{name}' is a built-in tool and already approved")
            self._expected(record, expected_token)
            return self.journal.remove(_PREFIX + name, by=by, reason=reason)
        if not record or "pending_digest" not in record:
            raise ToolRegistryError(f"'{name}' has not been announced; nothing to approve")
        self._expected(record, expected_token)
        entry = {k: v for k, v in record.items() if k not in ("by", "at", "reason")}
        schema = record.get('schema')
        if not isinstance(schema, dict) or tool_name(schema) != name:
            raise ToolRegistryError('Tool definition is missing; rediscover the tool before approval')
        entry.update(state=APPROVED, policy=POLICY, digest=digest(schema), pending_digest=digest(schema),
                     definition_digest=definition_digest(schema), pending_definition_digest=definition_digest(schema),
                     source_definition_digest=definition_digest(record.get('source_definition', schema)),
                     pending_source_definition_digest=definition_digest(record.get('source_definition', schema)))
        if not evaluate(schema, entry).runnable:
            raise ToolRegistryError('Tool definition cannot be approved; rediscover and inspect it')
        return self._change(name, entry, by=by, reason=reason)

    @staticmethod
    def _expected(record, expected_token):
        if expected_token is not None and expected_token != approval_token(record):
            raise ToolRegistryError('Tool definition or approval changed since inspection; refresh /projects/tools, inspect it and approve again')

    def withdraw(self, name: str, *, by: str, reason: str) -> Change:
        with self.transaction():
            return self._withdraw(name, by=by, reason=reason)

    def _withdraw(self, name, *, by, reason):
        _who_why(by, reason)
        record = self._record(name)
        if name not in BUILTIN_TOOLS and record is None:
            raise ToolRegistryError(f"'{name}' is not in the registry")
        if record and record.get("state") == WITHDRAWN:
            raise ToolRegistryError(f"'{name}' is already withdrawn")
        entry = {k: v for k, v in (record or {"source": "builtin"}).items() if k not in ("by", "at", "reason")}
        entry["state"] = WITHDRAWN
        return self._change(name, entry, by=by, reason=reason)

    def undo(self, change_id: int, *, by: str, reason: str = "undo", expected_token='') -> Change:
        _who_why(by, reason)
        with self.transaction():
            target = next((c for c in self.journal.history(limit=None) if c.id == change_id), None)
            if target is None or not target.key.startswith(_PREFIX):
                raise ToolRegistryError(f'no tool registry change {change_id}')
            name = target.key[len(_PREFIX):]
            current = self._record(name)
            restored = target.old if target.existed else None
            # Discovery facts are not reversible operator authority. Check both
            # the original transition and current state; undo-of-undo cannot bypass it.
            if name not in BUILTIN_TOOLS:
                def definition(record):
                    return {k: (record or {}).get(k) for k in (
                        'source', 'schema', 'source_definition', 'pending_digest',
                        'pending_definition_digest', 'pending_source_definition_digest')}
                if definition(target.old) != definition(target.new) or definition(current) != definition(restored):
                    raise ToolRegistryError('Registration or schema changes cannot be undone. Rediscover the current '
                                            'definition, inspect /projects/tools and explicitly approve it.')
            schema = (restored or {}).get('schema') or {'function': {'name': name}}
            if evaluate(schema, restored).runnable:
                self._expected(current, expected_token if expected_token is not None else '')
            return self.journal.undo(change_id, by=by, reason=reason)

    def _change(self, name: str, entry: Dict[str, Any], *, by: str, reason: str) -> Change:
        # Who, when and why also sit on the record itself, so a refusal can name them.
        entry = {**entry, "by": by.strip(), "at": self.journal.clock(), "reason": reason.strip()}
        return self.journal.set(_PREFIX + name, entry, by=by, reason=reason)


def main(argv=None) -> int:
    import argparse
    parser = argparse.ArgumentParser(prog="python -m aether.toolbelt.registry")
    parser.add_argument("--file", default=str(DEFAULT_PATH))
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list")
    for name in ("approve", "withdraw"):
        p = sub.add_parser(name)
        p.add_argument("name")
        p.add_argument("--by", required=True)
        p.add_argument("--reason", required=True)
        if name == 'approve':
            p.add_argument('--expected-token', default='', help='Token from inspect NAME; refuses stale inspection')
    p = sub.add_parser('inspect')
    p.add_argument('name')
    p = sub.add_parser("history")
    p.add_argument("name", nargs="?")
    p = sub.add_parser("undo")
    p.add_argument("change_id", type=int)
    p.add_argument('--expected-token', default='', help='Current inspection token required to restore approval')
    p.add_argument("--by", required=True)
    p.add_argument("--reason", default="undo")
    args = parser.parse_args(argv)
    registry = ToolRegistry(args.file)
    try:
        if args.command == "list":
            records = registry.records()
            for name in sorted(BUILTIN_TOOLS | set(records)):
                record = records.get(name, {})
                schema = record.get('schema') or {'type': 'function', 'function': {'name': name}}
                state = evaluate(schema, record).state
                source = "builtin" if name in BUILTIN_TOOLS else record.get("source", "?")
                note = f"  ({record['by']}: {record['reason']})" if record.get("by") else ""
                print(f"{name:20} {state:11} {source}{note}")
        elif args.command in ("approve", "withdraw"):
            options = dict(expected_token=args.expected_token) if args.command == 'approve' else {}
            change = getattr(registry, args.command)(args.name, by=args.by, reason=args.reason, **options)
            print(f"change {change.id}: {args.name} {args.command} by {change.by}")
        elif args.command == 'inspect':
            with registry.transaction():
                record = registry._record(args.name)
                if record is None and args.name not in BUILTIN_TOOLS:
                    raise ToolRegistryError('Tool is not registered; discover it before inspection')
                schema = (record or {}).get('schema') or {'type': 'function', 'function': {'name': args.name}}
                print(json.dumps(dict(name=args.name, record=record, state=evaluate(schema, record).state,
                                      approval_token=approval_token(record)), ensure_ascii=False, indent=2))
        elif args.command == "history":
            for c in registry.history(args.name):
                print(f"{c.id:4} {c.at} {c.by:12} {c.key[len(_PREFIX):]:20} {c.reason}")
        else:
            change = registry.undo(args.change_id, by=args.by, reason=args.reason, expected_token=args.expected_token)
            print(f"change {change.id} undoes {args.change_id}")
    except ConfigJournalError as exc:
        print(f"refused: {exc}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
