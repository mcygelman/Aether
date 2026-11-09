"""Operations (MOD-11).

Installing, launching, checking and maintaining an installation: launcher, preflight, public setup, the CLI, backups, and the live acceptance gates.

Contract: launcher start/stop; release preflight; public setup; CLI entry points; live gates (operator-run). The top layer: it composes every module below it, and no module imports it (test_module_operations).
Depends on: config, credentials, backends, agent, integrity, mission_control, models, roadmaps, runtime, toolbelt.
Owner: Operator assigns.

The public interface is __all__: the submodules and names listed there. Everything
else in this package is internal. Submodules load on first use (PEP 562), so
importing this package has no side effects.
"""
from importlib import import_module as _import_module

_SUBMODULES = ('ara09_offline_proof', 'cli', 'distribution', 'heavyweight_long_run', 'heavyweight_recovery_gate', 'heavyweight_studio_gate', 'heavyweight_twenty_minute_recovery_gate', 'heavyweight_two_hour_gate', 'heavyweight_write_gate', 'launcher', 'local_autonomy_pilot', 'public_setup', 'release_preflight', 'runtime_profile_gate')
_NAMES = {}

# An export that leaves some submodules out (the public repository) lists only those it contains.
_HERE = __import__("pathlib").Path(__file__).parent
_PRESENT = lambda m: (_HERE / (m.replace(".", "/") + ".py")).is_file() or (_HERE / m.replace(".", "/") / "__init__.py").is_file()
_SUBMODULES = tuple(m for m in _SUBMODULES if _PRESENT(m))
_NAMES = {name: module for name, module in _NAMES.items() if _PRESENT(module)}

__all__ = sorted(set(_SUBMODULES) | set(_NAMES))


def __getattr__(name):
    if name in _SUBMODULES:
        return _import_module(f"{__name__}.{name}")
    if name in _NAMES:
        return getattr(_import_module(f"{__name__}.{_NAMES[name]}"), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return __all__
