"""Tools box: the agent's own hands on its memory, as model-callable tools.

Inputs:  an open MemoryEngine, plus host limits (recall and load budgets,
         and optionally a reform handler); then (tool name, arguments) pairs
         exactly as a model emits them.
Outputs: schemas()  -> OpenAI-style function tool definitions (the format
                       the Aether Bridge tool belt already uses);
         dispatch() -> {"ok": True, "text": <what the model reads>, ...data};
                       raises MemoryToolError for a malformed or refused call;
         run()      -> the same, but never raises: an error comes back as
                       {"ok": False, "text": "MEMORY TOOL ERROR: ..."} so the
                       turn continues and the model can correct itself.

House rules this box enforces:
  * The agent writes as itself: `written_by` is always the engine's agent id,
    never an argument.
  * Words, not numbers (decision 20): kinds, importance ("low", "normal",
    "high", "vital") and link strength ("weak", "normal", "strong") are
    words. A switch date is given as days from now or a date.
  * Looking is free; loading is a choice (decision 2): recall returns short
    headers and refreshes nothing; load refreshes, and is refused BEFORE
    anything changes if the chosen notes would not fit the load budget.
  * There is no delete tool (decision 13). Forgetting is memory_bury.
  * A malformed call is a message the model can recover from, never a crash
    (the Bridge's DEC-044 lesson). Every error names the valid choices.
"""
from __future__ import annotations

import datetime as _dt
import json
import math
import re
import hashlib
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from . import schema
from .errors import MemoryFabricError
from .kinds import ALIASES
from .reformation import Decision, SetDown
from .tokens import CHARS_PER_TOKEN, estimate_tokens

IMPORTANCE_WORDS = ("low", "normal", "high", "vital")
STRENGTH_WORDS = {"weak": 0.40, "normal": None, "strong": 0.95}
DEFAULT_RECALL_BUDGET = 600
DEFAULT_LOAD_BUDGET = 3000
MAX_IDS_PER_LOAD = 12

READ_TOOLS = ("memory_recall", "memory_load", "memory_discover", "memory_maintain", "memory_preflight", "memory_workspace", "memory_query")
ALL_TOOLS = ("memory_recall", "memory_load", "memory_remember", "memory_link",
             "memory_bury", "memory_raise", "memory_settings", "memory_reform",
             "memory_discover", "memory_maintain", "memory_preflight", "memory_workspace", "memory_query")


def importance_value(word: Optional[str], fog_importance_min: float) -> Optional[float]:
    """Importance words -> numbers (None = the kind's own default).
    low 0.40 · normal (default) · high 0.85 · vital: at or above the fog bar."""
    if word in (None, "", "normal"):
        return None
    if word == "low":
        return 0.40
    if word == "high":
        return 0.85
    if word == "vital":
        return max(0.95, float(fog_importance_min))
    raise MemoryToolError(f"importance must be one of {list(IMPORTANCE_WORDS)}")


class MemoryToolError(MemoryFabricError, ValueError):
    """A tool call was malformed or refused. The message tells the model how to fix it."""


_JSON_TYPES = {"string": "a string", "number": "a number", "integer": "a whole number",
               "boolean": "true or false", "array": "a list", "object": "an object"}


def _check_type(value, schema: dict, where: str) -> None:
    """Check a model-supplied value against its JSON-schema type, recursively.
    A list of strings may also be given as one comma-separated string."""
    kind = schema.get("type")
    ok = True
    if kind == "string":
        ok = isinstance(value, str)
    elif kind in ("number", "integer"):
        ok = (isinstance(value, (int, float)) and not isinstance(value, bool)
              and math.isfinite(value))
        if kind == 'integer':
            ok = type(value) is int
    elif kind == "boolean":
        ok = isinstance(value, bool)
    elif kind == "object":
        ok = isinstance(value, dict)
        if ok:
            props = schema.get("properties", {})
            for req in schema.get("required", []):
                if value.get(req) is None or value.get(req) == "":
                    raise MemoryToolError(f"{where} needs '{req}'")
            for key, inner in value.items():
                if key not in props:
                    raise MemoryToolError(f"{where} has an unexpected field '{key}'; "
                                          f"allowed: {sorted(props)}")
                if inner is not None:
                    _check_type(inner, props[key], f"{where}.{key}")
    elif kind == "array":
        items = schema.get("items", {})
        if isinstance(value, str) and items.get("type") == "string":
            return
        ok = isinstance(value, list)
        if ok:
            for i, item in enumerate(value):
                _check_type(item, items, f"{where}[{i}]")
    if not ok:
        if "enum" in schema:
            raise MemoryToolError(f"{where} must be one of {list(schema['enum'])}")
        raise MemoryToolError(f"{where} must be {_JSON_TYPES.get(kind, kind)}, "
                              f"not {type(value).__name__}")
    if 'enum' in schema and not where.endswith('kind') and value not in schema['enum']:
        raise MemoryToolError(f"{where} must be one of {list(schema['enum'])}")


@dataclass(frozen=True)
class ToolLimits:
    recall_budget_tokens: int = DEFAULT_RECALL_BUDGET
    load_budget_tokens: int = DEFAULT_LOAD_BUDGET
    max_headers: int = 12
    max_result_chars: Optional[int] = None     # the host's per-tool-result limit, if any

    @classmethod
    def for_context(cls, working_context_tokens: int,
                    max_tool_result_chars: Optional[int] = None) -> "ToolLimits":
        """Memory budgets that follow the deployment's capability record.

        recall: 1% of the working context, between 400 and 4,000 tokens;
        load:   5% of the working context, between 1,500 and 24,000 tokens.
        Both stay under the host's per-tool-result character limit with a 20%
        margin (JSON escaping and ids ride along), because a tool result over
        that limit stops the run.
        """
        if not isinstance(working_context_tokens, int) or working_context_tokens <= 0:
            raise MemoryToolError("working_context_tokens must be a positive integer")
        recall = max(400, min(4000, working_context_tokens // 100))
        load = max(1500, min(24000, working_context_tokens // 20))
        if max_tool_result_chars is not None:
            cap = int(max_tool_result_chars * 0.8 / CHARS_PER_TOKEN)
            if cap < 200:
                raise MemoryToolError(
                    f"max_tool_result_chars={max_tool_result_chars} is too small for memory tools")
            recall, load = min(recall, cap), min(load, cap)
        headers = max(6, min(24, recall // 50))
        return cls(recall_budget_tokens=recall, load_budget_tokens=load, max_headers=headers,
                   max_result_chars=max_tool_result_chars)


def _kind_words() -> List[str]:
    return [aliases[0] for aliases in ALIASES.values()]


def _fn(name: str, description: str, properties: dict, required: List[str]) -> dict:
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": properties, "required": required}}}


def _string_list(description: str) -> dict:
    return {"type": "array", "items": {"type": "string"}, "description": description}


class MemoryTools:
    """Model-callable memory tools bound to one agent's engine."""

    def __init__(self, engine, limits: ToolLimits = ToolLimits(),
                 reform_handler: Optional[Callable[[Decision], object]] = None,
                 enabled: Optional[tuple] = None, prefix: Optional[str] = None,
                 label: Optional[str] = None):
        """prefix: offer the tools under other names ("aion_archive" -> aion_archive_recall),
        so an agent can consult another memory beside its own. label: one line telling
        the model whose memory this is. A read-only engine offers only recall and load,
        and loading from it refreshes nothing."""
        self.engine = engine
        self.limits = limits
        self.reform_handler = reform_handler
        self.preflight_handler = None  # trusted host may supply current request accounting
        self.observed_revision = engine.revision
        self.read_only = bool(getattr(engine.cart, "read_only", False))
        if prefix is not None and not re.fullmatch(r"[a-z][a-z0-9_]{1,30}", prefix):
            raise MemoryToolError("prefix must be lowercase letters, digits and _ (2-31 chars)")
        self.prefix = prefix
        self.label = label
        names = ALL_TOOLS if enabled is None else tuple(enabled)
        unknown = [n for n in names if n not in ALL_TOOLS]
        if unknown:
            raise MemoryToolError(f"unknown memory tools {unknown}; choose from {list(ALL_TOOLS)}")
        if self.read_only:
            writes = [n for n in names if n not in READ_TOOLS]
            if enabled is not None and writes:
                raise MemoryToolError(f"this memory is read-only; {writes} cannot be offered")
            names = tuple(n for n in names if n in READ_TOOLS)
        if reform_handler is None:
            names = tuple(n for n in names if n != "memory_reform")
        self._internal = {self._external(n): n for n in names}
        self.enabled = tuple(self._internal)
        self._table: Dict[str, Callable[[dict], dict]] = {
            "memory_recall": self._recall, "memory_load": self._load,
            "memory_remember": self._remember, "memory_link": self._link,
            "memory_bury": self._bury, "memory_raise": self._raise,
            "memory_settings": self._settings, "memory_reform": self._reform,
            "memory_discover": self._discover, "memory_maintain": self._maintain,
            "memory_preflight": self._preflight,
            "memory_workspace": self._workspace, "memory_query": self._query,
        }

    def _external(self, internal: str) -> str:
        return internal if not self.prefix else f"{self.prefix}_{internal[len('memory_'):]}"

    def internal_name(self, external: str) -> Optional[str]:
        return self._internal.get(external)

    # -- schemas ---------------------------------------------------------------
    def schemas(self) -> List[dict]:
        kinds = _kind_words()
        tags = _string_list("Short topic words or phrases, e.g. ['door code', 'alarm'].")
        ids = _string_list("Memory ids exactly as shown in recall headers (e.g. 'n-1a2b3c4d5e6f').")
        all_schemas = {
            "memory_recall": _fn(
                "memory_recall",
                "Look in your own memory. Give the tags this work touches; you get back short "
                "ranked headers (alerts first). Nothing is loaded or changed. Then load only "
                "what you need with memory_load.",
                {"tags": tags, "seed_note_ids": ids}, []),
            "memory_load": _fn(
                "memory_load",
                "Pull chosen memories onto your working board in full. Pulling a memory up "
                "keeps it alive and strengthens the links between memories used together.",
                {"ids": ids, "refresh": {"type": "boolean"},
                 "reinforce_links": {"type": "boolean"}}, ["ids"]),
            "memory_remember": _fn(
                "memory_remember",
                "Write a memory. Choose its kind in words and say how long it stays current "
                "(after that it fades at its kind's pace; it is never deleted).",
                {"title": {"type": "string", "description": "A short, findable title."},
                 "body": {"type": "string", "description": "What to remember, in full."},
                 "kind": {"type": "string", "enum": kinds,
                          "description": "What sort of memory this is."},
                 "tags": tags,
                 "category": {"type": "string", "enum": list(schema.CATEGORIES),
                              "description": "Alert for warnings; Task for obligations; "
                                             "Information otherwise (default)."},
                 "importance": {"type": "string", "enum": list(IMPORTANCE_WORDS),
                                "description": "Default normal. Vital is rare: vital memories "
                                               "can be reached through the fog even when buried."},
                 "current_for_days": {"type": "number",
                                      "description": "Days it stays current (default: the kind's)."},
                 "current_until": {"type": "string",
                                   "description": "Or a date it stays current until (YYYY-MM-DD)."},
                 "links": {"type": "array", "description": "Memories this one connects to.",
                           "items": {"type": "object", "properties": {
                               "to": {"type": "string"},
                               "relation": {"type": "string", "enum": list(schema.RELATIONS)},
                               "strength": {"type": "string", "enum": list(STRENGTH_WORDS)}},
                               "required": ["to"]}},
                 "proprietary": {"type": "boolean",
                                 "description": "True if it belongs to a client or operator and "
                                                "must never leave with you when you are shared."}},
                ["title", "body", "kind"]),
            "memory_link": _fn(
                "memory_link",
                "Connect two memories. Linking a pair again strengthens the link.",
                {"from_id": {"type": "string"}, "to_id": {"type": "string"},
                 "relation": {"type": "string", "enum": list(schema.RELATIONS)},
                 "strength": {"type": "string", "enum": list(STRENGTH_WORDS)}},
                ["from_id", "to_id"]),
            "memory_bury": _fn(
                "memory_bury",
                "Forget a memory by burying it as deep as it goes. It is never deleted and can "
                "rise again if it becomes useful.",
                {"id": {"type": "string"}}, ["id"]),
            "memory_raise": _fn(
                "memory_raise",
                "Bring a buried memory back up to its kind's normal strength.",
                {"id": {"type": "string"}}, ["id"]),
            "memory_settings": _fn(
                "memory_settings",
                "See or tune how your own memory behaves. With no arguments: every setting, "
                "its value, its allowed range and what it does. With key, value and reason: "
                "change one setting within its range. Every change is journaled with the old "
                "value, so you can always set it back.",
                {"key": {"type": "string",
                         "description": "The setting to see or change, e.g. 'hydration_share'."},
                 "value": {"type": "number", "description": "The new value (within its range)."},
                 "reason": {"type": "string",
                            "description": "Why you are changing it (kept in your journal)."}},
                []),
            "memory_reform": _fn(
                "memory_reform",
                "Your working board is nearly full. Say which working items to keep; set the "
                "rest down into memory with a title and tags. Nothing set down is lost.",
                {"keep": _string_list("Ids of working items to keep on the board."),
                 "set_down": {"type": "array", "items": {"type": "object", "properties": {
                     "item_id": {"type": "string"}, "title": {"type": "string"},
                     "tags": {"type": "array", "items": {"type": "string"}},
                     "kind": {"type": "string", "enum": kinds},
                     "current_for_days": {"type": "number"}},
                     "required": ["item_id", "title"]}}},
                ["keep", "set_down"]),
        }
        from .maintenance import READ_ACTIONS, WRITE_ACTIONS
        all_schemas['memory_preflight'] = _fn('memory_preflight',
            'Measure selected memories without loading or refreshing. Reports serialized size and '
            'labeled estimates; a bound host also reports actual remaining context. Fetch rechecks fit.',
            {'ids': ids}, ['ids'])
        all_schemas['memory_discover'] = _fn('memory_discover',
            'Find incomplete recollections across body, title and tags using lexical stemming/prefixes. '
            'Supply alternate wording yourself. Empty query browses; nothing refreshes.',
            {'query': {'type': 'string'}, 'alternatives': tags,
             'match': {'type': 'string', 'enum': ['any', 'all']},
             'include_buried': {'type': 'boolean'}, 'include_history': {'type': 'boolean'},
             'category': {'type': 'string', 'enum': list(schema.CATEGORIES) + ['Change']},
             'offset': {'type': 'integer'}, 'limit': {'type': 'integer'}}, [])
        all_schemas['memory_maintain'] = _fn('memory_maintain',
            'Maintain your memory. Inspect returns content, links and revision. Every change needs '
            'expected_revision from inspection and a reason. Revise/organize replace content/tags; '
            'merge needs target id, source_ids and your resulting body; sources are buried and links transferred. '
            'History pages descriptions; reconstruct reads id at at_revision; restore records reversal. '
            'Reviews are immutable; respond attaches body/evidence_refs to review_id. '
            'Use operation_id unchanged when retrying a write after a lost acknowledgment.',
            {'action': {'type': 'string', 'enum': list(READ_ACTIONS if self.read_only else READ_ACTIONS+WRITE_ACTIONS)},
             'id': {'type': 'string'}, 'expected_revision': {'type': 'integer'},
             'reason': {'type': 'string'}, 'title': {'type': 'string'}, 'body': {'type': 'string'},
             'tags': tags, 'source_ids': ids, 'to_id': {'type': 'string'},
             'relation': {'type': 'string', 'enum': list(schema.RELATIONS)},
             'strength': {'type': 'string', 'enum': list(STRENGTH_WORDS)},
             'importance': {'type': 'string', 'enum': list(IMPORTANCE_WORDS)},
             'kind': {'type': 'string', 'enum': kinds}, 'current_until': {'type': 'string'},
             'profile_values': {'type': 'object', 'properties': {
                 key: {'type': 'number'} for key in self.engine.cart._PROFILE_FIELDS}},
             'current_for_days': {'type': 'number'}, 'delta': {'type': 'number'},
             'fade_days': {'type': 'number'}, 'proprietary': {'type': 'boolean'},
             'at_revision': {'type': 'integer'}, 'after': {'type': 'integer'}, 'limit': {'type': 'integer'},
             'review_id': {'type': 'integer'}, 'evidence_refs': tags}, ['action'])
        from .workspace import ACTIONS, READ_ACTIONS as WORKSPACE_READS
        all_schemas['memory_workspace'] = _fn('memory_workspace',
            'action=schema: fields/examples; dashboard: Welcome. arguments_json encodes an object. Reuse operation_id on retries.',
            {'action': {'type': 'string', 'enum': list(WORKSPACE_READS if self.read_only else ACTIONS)},
             'arguments_json': {'type': 'string'}}, ['action'])
        all_schemas['memory_query'] = _fn('memory_query',
            'Paged read-only SQL. Tables: memory_workspace(action=schema).',
            {'sql': {'type': 'string'}, 'parameters_json': {'type': 'string'},
             'offset': {'type': 'integer'}, 'limit': {'type': 'integer'}}, ['sql'])
        for name, definition in all_schemas.items():
            if name not in ('memory_recall', 'memory_discover', 'memory_preflight', 'memory_workspace', 'memory_query') and not self.read_only:
                definition['function']['parameters']['properties']['operation_id'] = {
                    'type': 'string', 'description': 'Stable retry key for this exact operation; reuse after lost reply.'}
                definition['function']['parameters']['properties'].setdefault('expected_revision', {
                    'type': 'integer', 'description': 'Observed cartridge revision; intervening changes require inspection.'})
        out = []
        for external, internal in self._internal.items():
            tool = json.loads(json.dumps(all_schemas[internal]))
            tool["function"]["name"] = external
            if self.prefix:          # someone else's memory, consulted beside one's own
                tool["function"]["description"] = (tool["function"]["description"]
                                                   .replace("in your own memory", "in this memory")
                                                   .replace("onto your working board", "into view"))
            if self.label:
                tool["function"]["description"] = f"[{self.label}] " + tool["function"]["description"]
            if self.read_only and internal == "memory_load":
                tool["function"]["description"] = tool["function"]["description"].split(
                    " Pulling a memory up")[0] + " Read-only: nothing is changed."
            out.append(tool)
        return out

    def schema_tokens(self) -> int:
        """Estimated tokens the tool definitions cost (for the host's budget)."""
        return estimate_tokens(json.dumps(self.schemas(), separators=(",", ":")))

    # -- dispatch --------------------------------------------------------------
    def dispatch(self, name: str, args: Optional[dict]) -> dict:
        if name not in self.enabled:
            raise MemoryToolError(f"unknown memory tool '{name}'; available: {list(self.enabled)}")
        if args is None:
            args = {}
        if not isinstance(args, dict):
            raise MemoryToolError(f"arguments for '{name}' must be an object")
        internal = self._internal[name]
        spec = self._spec(name)
        allowed = set(spec["properties"])
        unexpected = sorted(set(args) - allowed)
        missing = [r for r in spec["required"] if args.get(r) is None or args.get(r) == ""]
        if unexpected or missing:
            raise MemoryToolError(
                f"bad arguments for '{name}':"
                + (f" missing {missing};" if missing else "")
                + (f" unexpected {unexpected};" if unexpected else "")
                + f" expected {name}({self._signature(spec)})")
        for key, value in args.items():
            if value is not None:
                _check_type(value, spec["properties"][key], key)
        try:
            result = self._execute(internal, args)
        except MemoryToolError:
            raise
        except MemoryFabricError as exc:
            raise MemoryToolError(f"{name} refused: {exc}") from exc
        except Exception as exc:          # backstop: a model's call must never crash the host
            raise MemoryToolError(f"{name} failed: {type(exc).__name__}: {exc}") from exc
        result.setdefault("ok", True)
        self.observed_revision = result.get('revision', self.engine.revision)
        return result

    def _execute(self, internal, args):
        from . import history
        from .maintenance import READ_ACTIONS
        if internal in ('memory_workspace', 'memory_query'):
            return self._table[internal](args)  # These operations own their read/write transactions and receipts.
        writing = internal not in ('memory_recall', 'memory_discover', 'memory_preflight')
        if internal == 'memory_maintain' and args['action'] in READ_ACTIONS:
            writing = False
        if internal == 'memory_settings' and args.get('value') is None:
            writing = False
        if internal == 'memory_load' and (self.read_only or not args.get('refresh', True)):
            writing = False
        if not writing:
            result = self._table[internal](args)
            result.setdefault('revision', self.engine.revision)
            return result
        if self.read_only:
            raise MemoryToolError('reference mount is read-only')
        cart = self.engine.cart
        operation_id = args.get('operation_id')
        digest = hashlib.sha256(json.dumps([internal, args], sort_keys=True).encode()).hexdigest()
        with cart.transaction(immediate=True, operation=args.get('action', internal),
                              description=args.get('reason', internal)):
            if operation_id:
                receipt = cart.conn.execute('SELECT * FROM operation_receipts WHERE operation_id=?',
                                            (operation_id,)).fetchone()
                if receipt:
                    if receipt['request_hash'] != digest or receipt['actor'] != cart.actor:
                        raise MemoryToolError('operation_id already belongs to a different request or actor')
                    return json.loads(receipt['result'])
            if 'expected_revision' in args:
                history.require_revision(cart, args['expected_revision'])
            elif internal in ('memory_bury', 'memory_raise', 'memory_link', 'memory_settings'):
                history.require_revision(cart, self.observed_revision)
            before = history.mark(cart.conn)
            result = self._table[internal](args)
            result['ok'] = True
            result['revision'] = history.revision(cart) + int(bool(history.delta(cart.conn, before)))
            if operation_id:
                cart.conn.execute('INSERT INTO operation_receipts VALUES (?,?,?,?)',
                                  (operation_id, digest, cart.actor, json.dumps(result)))
        return result

    def run(self, name: str, args: Optional[dict]) -> dict:
        """Like dispatch, but never raises: errors become text the model reads."""
        try:
            return self.dispatch(name, args)
        except MemoryToolError as exc:
            return {"ok": False, "text": f"MEMORY TOOL ERROR: {exc}"}

    def _spec(self, name: str) -> dict:
        for tool in self.schemas():
            if tool["function"]["name"] == name:
                return tool["function"]["parameters"]
        raise MemoryToolError(f"unknown memory tool '{name}'")

    @staticmethod
    def _signature(spec: dict) -> str:
        required = spec["required"]
        optional = [p for p in spec["properties"] if p not in required]
        return ", ".join(required) + (f"[, {', '.join(optional)}]" if optional else "")

    # -- argument helpers ------------------------------------------------------
    @staticmethod
    def _list(value, what: str) -> List[str]:
        """Accept a list of strings, or one comma-separated string (models do both)."""
        if isinstance(value, str):
            value = [part for part in value.split(",")]
        if not isinstance(value, (list, tuple)) or not all(isinstance(v, str) for v in value):
            raise MemoryToolError(f"{what} must be a list of strings")
        cleaned = [v.strip() for v in value if v.strip()]
        if not cleaned:
            raise MemoryToolError(f"{what} must not be empty")
        return cleaned

    def _importance(self, word: Optional[str]) -> Optional[float]:
        return importance_value(word, float(self.engine.settings()["fog_importance_min"]))

    @staticmethod
    def _strength(word: Optional[str]) -> Optional[float]:
        if word in (None, ""):
            return None
        if word not in STRENGTH_WORDS:
            raise MemoryToolError(f"strength must be one of {list(STRENGTH_WORDS)}")
        return STRENGTH_WORDS[word]

    @staticmethod
    def _switch(args: dict) -> dict:
        days, until = args.get("current_for_days"), args.get("current_until")
        if days is not None and until:
            raise MemoryToolError("give current_for_days or current_until, not both")
        if days is not None:
            if isinstance(days, bool) or not isinstance(days, (int, float)) or days < 0 or days > 36500:
                raise MemoryToolError("current_for_days must be a number of days between 0 and 36500")
            return {"switch_in_days": float(days)}
        if until:
            try:
                date = _dt.date.fromisoformat(str(until)[:10])
            except ValueError:
                raise MemoryToolError("current_until must be a date like 2026-10-31") from None
            return {"switch_at": f"{date.isoformat()}T00:00:00Z"}
        return {}

    # -- the tools -------------------------------------------------------------
    def _recall(self, args: dict) -> dict:
        tags = self._list(args['tags'], 'tags') if args.get('tags') else []
        seeds = self._list(args['seed_note_ids'], 'seed_note_ids') if args.get('seed_note_ids') else []
        result = self.engine.recall(tags, budget_tokens=self.limits.recall_budget_tokens,
                                    max_headers=self.limits.max_headers, seed_note_ids=seeds)
        return {"text": result.render(), "ids": result.ids(),
                "more_available": result.more_available, "seed_note_ids": list(result.seed_note_ids)}

    def _workspace(self, args):
        value = json.loads(args.get('arguments_json', '{}'))
        if not isinstance(value, dict):
            raise MemoryToolError('arguments_json must encode an object')
        result = self.engine.workspace(args['action'], value)
        if args['action'] == 'dashboard':
            from .dashboard import render_text
            result['text'] = render_text(result)
        else:
            result['text'] = json.dumps(result, ensure_ascii=False)
        return result

    def _query(self, args):
        result = self.engine.query(args['sql'], json.loads(args.get('parameters_json', '[]')),
                                   offset=args.get('offset', 0), limit=args.get('limit', 100))
        result['text'] = json.dumps(result, ensure_ascii=False)
        return result

    def _load(self, args: dict) -> dict:
        ids = list(dict.fromkeys(self._list(args["ids"], "ids")))
        if len(ids) > MAX_IDS_PER_LOAD:
            raise MemoryToolError(f"load at most {MAX_IDS_PER_LOAD} memories at a time")
        notes = [self.engine.note(i) for i in ids]          # read first: nothing changes yet
        blocks = [self._render_note(n) for n in notes]
        cost = sum(estimate_tokens(b) for b in blocks)
        if cost > self.limits.load_budget_tokens:
            sizes = ", ".join(f"{n['note_id']}≈{estimate_tokens(b)}" for n, b in zip(notes, blocks))
            raise MemoryToolError(
                f"these memories need about {cost} tokens but the load budget is "
                f"{self.limits.load_budget_tokens}; load fewer ({sizes}). Nothing was loaded.")
        result = {"text": "\n\n".join(blocks), "ids": ids, "tokens": cost}
        limit = self.limits.max_result_chars
        if limit is not None:
            size = len(json.dumps({**result, "ok": True}, ensure_ascii=False))
            if size > limit:
                raise MemoryToolError(
                    f"these memories come to {size} characters, over the {limit}-character tool "
                    f"result limit; load fewer. Nothing was loaded.")
        if not self.read_only:
            if args.get('refresh', True):
                self.engine.load(ids, reinforce_links=args.get('reinforce_links', True))
        return result

    def _discover(self, args):
        clean = dict(args)
        if 'alternatives' in clean:
            clean['alternatives'] = self._list(clean['alternatives'], 'alternatives') if clean['alternatives'] else []
        return self.engine.discover(**clean, max_chars=self.limits.max_result_chars or 12000)

    def _preflight(self, args):
        ids = list(dict.fromkeys(self._list(args['ids'], 'ids')))
        if len(ids) > MAX_IDS_PER_LOAD:
            raise MemoryToolError(f'preflight at most {MAX_IDS_PER_LOAD} memories')
        with self.engine.cart.read_snapshot():
            blocks = [self._render_note(self.engine.note(i)) for i in ids]
            cost = sum(estimate_tokens(b) for b in blocks)
            candidate = {'text': '\n\n'.join(blocks), 'ids': ids, 'tokens': cost,
                         'ok': True, 'revision': self.engine.revision + (0 if self.read_only else 1)}
            size = len(json.dumps(candidate, ensure_ascii=False))
            result = {'ids': ids, 'revision': self.engine.revision,
                      'estimated_tokens': cost, 'result_chars': size,
                      'fits_tool_allowance': cost <= self.limits.load_budget_tokens and
                          (self.limits.max_result_chars is None or size <= self.limits.max_result_chars),
                      'context': 'not measured; requires current host request',
                      'text': 'Size only; no memories refreshed. Narrow or load chosen ids.'}
            if self.preflight_handler is not None:
                result['context'] = self.preflight_handler(ids)
            return result

    def _maintain(self, args):
        clean = {k: v for k, v in args.items() if k != 'operation_id'}
        for key in ('tags', 'source_ids', 'evidence_refs'):
            if key in clean:
                clean[key] = self._list(clean[key], key) if clean[key] else []
        result = self.engine.maintain(**clean)
        result.setdefault('text', 'Memory inspection; historical records are labeled explicitly.')
        # Only complete records are returned. Narrow or fetch a historical note separately.
        from .maintenance import READ_ACTIONS
        if args['action'] in READ_ACTIONS:
            limit = self.limits.max_result_chars or int(self.limits.load_budget_tokens * CHARS_PER_TOKEN)
            if len(json.dumps({**result, 'ok': True}, ensure_ascii=False)) > limit:
                raise MemoryToolError('result exceeds allowance; reduce history limit or use discovery headers')
        return result

    @staticmethod
    def _render_note(note: dict) -> str:
        flags = " · proprietary" if note.get("proprietary") else ""
        return (f"=== MEMORY {note['note_id']}: {note['title']} "
                f"[{note['category']} · {note['profile']} · strength {note['strength']:.2f}{flags}]\n"
                f"{note['body']}\n"
                f"tags: {', '.join(note['tags'])}")

    def _remember(self, args: dict) -> dict:
        tags = self._list(args["tags"], "tags") if args.get("tags") not in (None, "", []) else []
        links = []
        for item in args.get("links") or []:
            if not isinstance(item, dict) or not item.get("to"):
                raise MemoryToolError("each link needs at least {'to': <memory id>}")
            relation = item.get("relation") or "context"
            strength = self._strength(item.get("strength"))
            links.append((item["to"], relation) if strength is None
                         else (item["to"], relation, strength))
        proprietary = args.get("proprietary", False)
        if not isinstance(proprietary, bool):
            raise MemoryToolError("proprietary must be true or false")
        note_id = self.engine.remember(
            str(args["title"]), str(args["body"]), str(args["kind"]), tags,
            category=args.get("category") or "Information",
            importance=self._importance(args.get("importance")),
            links=links, proprietary=proprietary, **self._switch(args))
        note = self.engine.note(note_id)
        return {"text": (f"Remembered {note_id}: {note['title']} "
                         f"({note['profile']}, current until {note['switch_at'][:10]})."),
                "id": note_id}

    def _link(self, args: dict) -> dict:
        relation = args.get("relation") or "context"
        link_id = self.engine.link(args["from_id"], args["to_id"], relation,
                                   self._strength(args.get("strength")))
        return {"text": f"Linked {args['from_id']} and {args['to_id']} ({relation}).",
                "link_id": link_id}

    def _bury(self, args: dict) -> dict:
        self.engine.bury(args["id"])
        return {"text": f"Buried {args['id']}. It stays in memory and can rise again.",
                "id": args["id"]}

    def _raise(self, args: dict) -> dict:
        self.engine.raise_note(args["id"])
        note = self.engine.note(args["id"])
        return {"text": f"Raised {args['id']} (strength now {note['strength']:.2f}).",
                "id": args["id"]}

    def _settings(self, args: dict) -> dict:
        key, value, reason = args.get("key"), args.get("value"), args.get("reason")
        table = {row["key"]: row for row in self.engine.settings_table()}
        if key is None:
            if value is not None or reason:
                raise MemoryToolError("to change a setting give key, value and reason")
            lines = ["YOUR MEMORY SETTINGS (key = value, range; what it does):"]
            for row in table.values():
                lock = "" if row["tunable_by_agent"] else " [fixed]"
                lines.append(f"- {row['key']} = {row['value']:g} ({row['min_value']:g}..."
                             f"{row['max_value']:g}){lock}: {row['description']}")
            return {"text": "\n".join(lines), "settings": {k: r["value"] for k, r in table.items()}}
        if key not in table:
            raise MemoryToolError(f"unknown setting '{key}'; call memory_settings() to list them")
        row = table[key]
        if value is None:
            history = self.engine.setting_history(key)
            lines = [f"{key} = {row['value']:g} ({row['min_value']:g}...{row['max_value']:g}): "
                     f"{row['description']}"]
            lines += [f"  {h['at'][:10]} {h['changed_by']}: "
                      f"{'(new)' if h['old_value'] is None else format(h['old_value'], 'g')}"
                      f" -> {h['new_value']:g}" + (f" ({h['reason']})" if h["reason"] else "")
                      for h in history]
            return {"text": "\n".join(lines), "key": key, "value": row["value"]}
        if not reason or not str(reason).strip():
            raise MemoryToolError("say why you are changing it (reason); it is kept in your journal")
        old = row["value"]
        self.engine.change_setting(key, value, reason=str(reason).strip())
        return {"text": (f"{key}: {old:g} -> {value:g}. Journaled. To undo: "
                         f"memory_settings(key='{key}', value={old:g}, reason='undo')."),
                "key": key, "old_value": old, "new_value": value}

    def _reform(self, args: dict) -> dict:
        keep = tuple(self._list(args["keep"], "keep")) if args.get("keep") else ()
        set_down = []
        for item in args.get("set_down") or []:
            if not isinstance(item, dict) or not item.get("item_id") or not item.get("title"):
                raise MemoryToolError("each set_down entry needs item_id and title")
            set_down.append(SetDown(
                item_id=item["item_id"], title=item["title"],
                kind=item.get("kind") or "offloaded context",
                tags=tuple(self._list(item["tags"], "tags")) if item.get("tags") else (),
                switch_in_days=item.get("current_for_days")))
        reformed = self.reform_handler(Decision(keep=keep, set_down=tuple(set_down)))
        written = list(getattr(reformed, "written_note_ids", []))
        auto = list(getattr(reformed, "auto_filed", []))
        text = (f"Board reformed: {len(keep)} kept, {len(written)} set down into memory"
                + (f" ({len(auto)} you did not mention were filed automatically)" if auto else "")
                + ". Recall them by tag when needed.")
        return {"text": text, "written_ids": written, "auto_filed": auto}
