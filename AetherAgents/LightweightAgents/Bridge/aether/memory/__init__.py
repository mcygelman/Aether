"""Memory (MOD-12).

Each agent's memory in one cartridge, served by the Memory Fabric engine (AetherMemory/memory_fabric). The legacy markdown hydration stays until each agent moves in.

Contract: MemoryEngine owner workflows, discovery and history; WorkingMemory measured
request integration and scoped helper contract; SessionMemory (the pointer switch,
hydration, memory tools, task-start and tool-error triggers, the memory gate; T07: the
cartridge binding for integrity guards and ClosureMemoryWriter for closure memories);
context reformation building blocks (board: catalog and proposals; reform_plan: decision,
new context, archive; document 08); legacy HW-08 loader until migration.
Depends on: integrity, config, backends, models.
Owner: Claude (Opus 5.5).

The public interface is __all__: the submodules and names listed there. Everything
else in this package is internal. Submodules load on first use (PEP 562), so
importing this package has no side effects.
"""
from importlib import import_module as _import_module

_SUBMODULES = ('agent_view', 'agent_view_http', 'board', 'fabric', 'index_insertion', 'memory_loader', 'notices', 'reform_plan', 'reformer', 'session', 'working_context')
_NAMES = {'open_engine': 'fabric', 'open_owner_engine': 'fabric', 'gate_recall': 'fabric',
          'memory_fabric': 'fabric', 'WorkingMemory': 'working_context',
          'SessionMemory': 'session', 'read_pointer': 'session',
          'ClosureMemoryWriter': 'session', 'cartridge_binding': 'session'}

__all__ = sorted(set(_SUBMODULES) | set(_NAMES))


def __getattr__(name):
    if name in _SUBMODULES:
        return _import_module(f"{__name__}.{name}")
    if name in _NAMES:
        return getattr(_import_module(f"{__name__}.{_NAMES[name]}"), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return __all__
