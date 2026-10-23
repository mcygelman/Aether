"""Toolbelt (MOD-08).

One registry of approved tools for all agents: scoped file tools, the calculator, governed execution, MCP.

Contract: ToolBelt.openai_tools(), ToolBelt.dispatch(name, args), max_result_chars; ToolBelt.attach(schema, handler, source) for external tools; ToolRegistry (Bridge Overhaul M2): approved, unapproved and withdrawn tools, journaled who and why, offered equals runnable (CLI python -m aether.toolbelt.registry).
Depends on: integrity, config.
Owner: Operator assigns.

The public interface is __all__: the submodules and names listed there. Everything
else in this package is internal. Submodules load on first use (PEP 562), so
importing this package has no side effects.
"""
from importlib import import_module as _import_module

_SUBMODULES = ('action_admission', 'calc', 'calc_adapter', 'execution', 'mcp_client', 'mcp_server', 'registry', 'toolbelt')
_NAMES = {'ToolBelt': 'toolbelt', 'ToolError': 'toolbelt', 'ToolRegistry': 'registry'}

__all__ = sorted(set(_SUBMODULES) | set(_NAMES))


def __getattr__(name):
    if name in _SUBMODULES:
        return _import_module(f"{__name__}.{name}")
    if name in _NAMES:
        return getattr(_import_module(f"{__name__}.{_NAMES[name]}"), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return __all__
