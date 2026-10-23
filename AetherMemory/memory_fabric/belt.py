"""Belt box: add an agent's memory tools to an existing tool belt.

Inputs:  any tool belt with the Aether Bridge contract (an object with
         openai_tools() -> list, dispatch(name, args) -> result, and
         max_result_chars), plus a MemoryTools instance.
Outputs: MemoryBelt, which has the same contract: it lists the inner belt's
         tools followed by the memory tools, routes memory_* calls to memory
         and everything else to the inner belt unchanged.

Why a wrapper and not a change to the Bridge's ToolBelt: the Bridge stays
exactly as it is (its tests, journal and safety rules untouched); turning
memory on for an agent is one line where the belt is built:

    belt = MemoryBelt(belt, mind.tools(ToolLimits.for_context(
        budget.working_context_tokens, belt.max_result_chars)))

Guarantees:
  * Inner tool names win: if the inner belt already has a memory_* tool, the
    wrapper refuses to build (no silent shadowing).
  * Every other attribute (roots, journal, max_result_chars...) is the inner
    belt's, so checks like validate_runner_binding see the same belt.
  * The Bridge stops a run on an oversized tool result, and memory must
    never be the reason. Reads (recall, load) that would be too long become
    an error message; memory_load checks BEFORE pulling anything up when its
    limits carry max_result_chars (ToolLimits.for_context sets it). Writes
    are never reported as failed after they happened: an over-long
    confirmation is shortened instead.
  * Memory refusals come back as {"error": ...}, the shape the Bridge's tool
    loop already gives the model for any failed call.
"""
from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

from .tools import MemoryToolError, MemoryTools


class MemoryBelt:
    def __init__(self, inner: Any, memory: MemoryTools):
        self._inner = inner
        self._memory = memory
        limit = getattr(inner, 'max_result_chars', None)
        if limit is not None:
            current = memory.limits.max_result_chars
            memory.limits = replace(memory.limits, max_result_chars=min(limit, current) if current else limit)
        inner_names = {t["function"]["name"] for t in inner.openai_tools()}
        clash = inner_names & set(memory.enabled)
        if clash:
            raise MemoryToolError(f"inner belt already defines {sorted(clash)}; refusing to shadow")

    @property
    def memory(self) -> MemoryTools:
        return self._memory

    def __getattr__(self, name: str):
        # Only called for attributes MemoryBelt itself lacks: everything else is the inner
        # belt's, private helpers included (the Bridge's Studio calls belt._resolve).
        if name in _OWN:                  # not set yet (during __init__ or copying)
            raise AttributeError(name)
        return getattr(self._inner, name)

    def __setattr__(self, name: str, value):
        # A setting such as belt.max_result_chars = n must reach the belt that enforces it.
        if name in _OWN:
            object.__setattr__(self, name, value)
        else:
            setattr(self._inner, name, value)

    def openai_tools(self) -> list:
        return list(self._inner.openai_tools()) + self._memory.schemas()

    def dispatch(self, name: str, args: dict):
        if name not in self._memory.enabled:
            return self._inner.dispatch(name, args)
        result = self._memory.run(name, args)
        if not result.get("ok", False):
            return {"error": result["text"]}
        limit = getattr(self._inner, "max_result_chars", None)
        if limit is None or _size(result) <= limit:
            return result
        internal = self._memory.internal_name(name)
        from .maintenance import READ_ACTIONS
        writing = internal in WRITE_TOOLS and not (
            internal == 'memory_maintain' and args.get('action') in READ_ACTIONS) and not (
            internal == 'memory_settings' and args.get('value') is None)
        if internal == 'memory_workspace':
            from .workspace import WRITE_ACTIONS
            writing = args.get('action') in WRITE_ACTIONS
        if writing:
            # The write happened. Never report it as failed: shorten the confirmation
            # until its serialised size fits (escaping can double a character's cost).
            short = {k: v for k, v in result.items() if k in ("ok", "id", "link_id")}
            if internal == 'memory_workspace':
                short.update({k: result[k] for k in ('element_id', 'dashboard_id', 'revision') if k in result})
                if result.get('event'):
                    short['event_id'] = result['event']['event_id']
            text = result.get("text", "")
            while text and _size({**short, "text": text}) > limit:
                text = text[: len(text) // 2]
            short["text"] = text
            if _size(short) > limit:          # even ids alone don't fit: the bare fact
                short = {"ok": True}
            return short
        return {"error": f"MEMORY TOOL ERROR: the result would be {_size(result)} characters, over "
                         f"the {limit}-character tool result limit; ask for less (fewer ids, "
                         f"narrower tags, one schema table, or smaller offset/limit pages)."}


_OWN = ("_inner", "_memory")

WRITE_TOOLS = ("memory_remember", "memory_link", "memory_bury", "memory_raise", "memory_reform",
               "memory_settings", "memory_maintain", "memory_load")


def _size(result) -> int:
    return len(json.dumps(result, ensure_ascii=False, default=str))
