"""Memory Fabric adapter (MOD-12): the Bridge's door to AetherMemory/memory_fabric.

The engine lives at the repo root in AetherMemory/ (standard library only, its own tests).
This adapter is the one place the Bridge finds it, so nothing else touches sys.path.

Outputs:
    memory_fabric()              the engine package (imported on first use)
    open_engine(path, ...)       MemoryEngine.open
    gate_recall(engine, budget)  a recall function for the memory gate (aether.integrity.gate):
                                 tags -> header dicts {id, title, category, one_line, score}.
                                 Recall never refreshes anything: looking is free.
"""
from __future__ import annotations

import sys
from typing import Any, Callable, Dict, List

from aether.paths import REPOSITORY_ROOT

ENGINE_ROOT = REPOSITORY_ROOT / "AetherMemory"


def memory_fabric():
    if str(ENGINE_ROOT) not in sys.path:
        sys.path.insert(0, str(ENGINE_ROOT))
    import memory_fabric as package
    return package


def open_engine(path, **kwargs):
    return memory_fabric().MemoryEngine.open(path, **kwargs)


def open_owner_engine(path, *, owner_id, session_id, **kwargs):
    """Verify the trusted identity BEFORE opening a writable session.

    The host resolves GUID/readable ID using the verified profile, not model input
    or the rebuildable roster alone. No automatic GUID backfill or identity edits.
    """
    with open_engine(path, read_only=True) as view:
        view.bind_session(owner_id, session_id)
    engine = open_engine(path, **kwargs)
    try:
        return engine.bind_session(owner_id, session_id)
    except Exception:
        engine.close()
        raise


def gate_recall(engine, budget_tokens: int = 600) -> Callable[[List[str]], List[Dict[str, Any]]]:
    def recall(tags: List[str]) -> List[Dict[str, Any]]:
        if not tags:
            return []
        result = engine.recall(list(tags), budget_tokens=budget_tokens)
        return [{"id": h.note_id, "title": h.title, "category": h.category, "one_line": h.one_line,
                 "score": h.score, "recurring": h.recurring} for h in result.headers]
    return recall
