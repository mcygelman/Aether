"""Credentials (MOD-03).

API keys and sealbox keys: where they live, how the host reads them, and that they never leak into output or errors.

Contract: Secret lookup for the host only; key backup, restore, rotation and status.
Depends on: integrity, models (credential references are parsed as deployment profiles define them).
Owner: Operator assigns.

The public interface is __all__: the submodules and names listed there. Everything
else in this package is internal. Submodules load on first use (PEP 562), so
importing this package has no side effects.
"""
from importlib import import_module as _import_module

_SUBMODULES = ('key_lifecycle', 'provider_credentials', 'redaction')
_NAMES = {'CredentialError': 'provider_credentials', 'load_deepseek_key': 'provider_credentials', 'KeyLifecycleError': 'key_lifecycle'}

__all__ = sorted(set(_SUBMODULES) | set(_NAMES))


def __getattr__(name):
    if name in _SUBMODULES:
        return _import_module(f"{__name__}.{name}")
    if name in _NAMES:
        return getattr(_import_module(f"{__name__}.{_NAMES[name]}"), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return __all__
