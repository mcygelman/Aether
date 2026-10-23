"""Agent (MOD-13).

What an agent is: identity (GUID and readable ID), profile layout, briefcases and microcases, the hybrid core and home, mounts, session closure; tier templates (Heavy, Light, Workers).

Contract: Load and verify an agent's identity and briefcase; assemble; mount policy; closure records.
Depends on: integrity, memory, knowledge, models, toolbelt.
Owner: Operator assigns.

The public interface is __all__: the submodules and names listed there. Everything
else in this package is internal. Submodules load on first use (PEP 562), so
importing this package has no side effects.
"""
from importlib import import_module as _import_module

_SUBMODULES = ('assembler', 'briefcase', 'closure', 'hybrid_identity', 'hybrid_publication', 'microcase', 'mount_policy')
_NAMES = {'Briefcase': 'briefcase', 'BriefcaseError': 'briefcase'}

__all__ = sorted(set(_SUBMODULES) | set(_NAMES))


def __getattr__(name):
    if name in _SUBMODULES:
        return _import_module(f"{__name__}.{name}")
    if name in _NAMES:
        return getattr(_import_module(f"{__name__}.{_NAMES[name]}"), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return __all__
