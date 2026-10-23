"""Knowledge (MOD-10).

The Lexicon, TeachFrames, the Learning Arc and School: guidance agents check out when a task needs it.

Contract: TeachFrame parsing and cards; School checkout and deposit. (First Principles reminder delivery moved to the Runtime on 2026-10-08: aether.runtime.orchestration.workforce_guidance.)
Depends on: integrity, models.
Owner: Operator assigns.

The public interface is __all__: the submodules and names listed there. Everything
else in this package is internal. Submodules load on first use (PEP 562), so
importing this package has no side effects.
"""
from importlib import import_module as _import_module

_SUBMODULES = ('deposit', 'school', 'teachframe')
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
