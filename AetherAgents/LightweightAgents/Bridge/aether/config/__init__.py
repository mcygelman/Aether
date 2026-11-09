"""Configuration (MOD-04).

Every setting in one store, each change journaled with who, when, why and the old value, so any change can be undone.

Contract: InstallationSettings (the catalog of installation settings: get, set, remove, list, history,
undo; CLI python -m aether.config) over ConfigJournal; safe settings files (strict JSON, writer lock);
deployment context budgets; the token-count policy; change notices.
Depends on: integrity.
Owner: Operator assigns.

The public interface is __all__: the submodules and names listed there. Everything
else in this package is internal. Submodules load on first use (PEP 562), so
importing this package has no side effects.
"""
from importlib import import_module as _import_module

_SUBMODULES = ('context_configuration', 'journal', 'model_settings_store', 'notices', 'settings', 'solace_model_config', 'token_count_policy')
_NAMES = {'ConfigJournal': 'journal', 'Change': 'journal', 'ConfigJournalError': 'journal',
          'InstallationSettings': 'settings', 'SettingsError': 'settings',
          'ChangeNotice': 'notices', 'journal_notices': 'notices'}

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
