"""Mission Control (MOD-14).

The master GUI. It hosts views of the other modules; the modules own their data and rules.

Contract: HTTP/SSE server, Studio server, launch editor; each module supplies its own routes.
Depends on: roadmaps, runtime, config, agent, backends, integrity, models, toolbelt.
Owner: Operator assigns.

The public interface is __all__: the submodules and names listed there. Everything
else in this package is internal. Submodules load on first use (PEP 562), so
importing this package has no side effects.
"""
from importlib import import_module as _import_module

_SUBMODULES = ('charter_launch', 'configured_mission_launcher', 'gateway', 'mission_launch_editor', 'route_types', 'server', 'studio_server', 'tool_registry_http')
_NAMES = {}

__all__ = sorted(set(_SUBMODULES) | set(_NAMES))


def __getattr__(name):
    if name in _SUBMODULES:
        return _import_module(f"{__name__}.{name}")
    if name in _NAMES:
        return getattr(_import_module(f"{__name__}.{_NAMES[name]}"), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return __all__
