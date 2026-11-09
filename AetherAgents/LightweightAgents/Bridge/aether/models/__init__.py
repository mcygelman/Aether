"""Model Catalog (MOD-05).

The model spec sheet: measured context, output limits and presets per model and runtime profile, looked up rather than hand-set.

Contract: ModelCatalog.lookup(model) -> ModelSpec; runtime profiles and their settings store
(SettingsStore, preview); budget presets.
Depends on: config.
Deployment profiles (v3 records and their validation) live here since 2026-10-08, so
validate_registry(deployment_aware=True) no longer imports Backends.
SettingsStore takes its operator lookup at construction (operators=, 10-08 list, item 9);
callers inject the Roadmaps operators.reader, which keeps the whole-registry check. Without
one, saves refuse. No Roadmaps import remains in this package.
Owner: Operator assigns.

The public interface is __all__: the submodules and names listed there. Everything
else in this package is internal. Submodules load on first use (PEP 562), so
importing this package has no side effects.
"""
from importlib import import_module as _import_module

_SUBMODULES = ('budget', 'catalog', 'deployment_profiles', 'model_budget_presets', 'runtime_profiles', 'settings_store')
_NAMES = {'ModelCatalog': 'catalog', 'ModelSpec': 'catalog', 'ModelCatalogError': 'catalog',
          'SettingsStore': 'settings_store'}

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
