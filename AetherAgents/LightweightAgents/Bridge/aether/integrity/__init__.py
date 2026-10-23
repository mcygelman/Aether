"""Integrity (MOD-09).

What protects agents and the work: SANCTUM seals, sealbox encryption, canonical hashing, checks, evidence records and the memory gate.

Contract: Seal and verify (sanctum, sealbox, canon); integrity checks; append-only evidence records; memory gate (decision 47). The canonical context atom and manifest contract (shared by every module that assembles context).
Depends on: nothing.
Owner: Operator assigns.

The public interface is __all__: the submodules and names listed there. Everything
else in this package is internal. Submodules load on first use (PEP 562), so
importing this package has no side effects.
"""
from importlib import import_module as _import_module

_SUBMODULES = ('ack', 'canon', 'context_atom_contract', 'digests', 'exact_decision_http', 'fabrication_check', 'failure_evidence', 'failure_inventory', 'gate', 'sanctum', 'sealbox')
_NAMES = {'MemoryGate': 'gate', 'GateDecision': 'gate', 'SealboxError': 'sealbox', 'canon_bytes': 'canon', 'sha256_hex': 'canon', 'json_digest': 'digests', 'require_sha256': 'digests', 'is_blank_reply': 'ack', 'is_bare_ack_echo': 'ack'}

__all__ = sorted(set(_SUBMODULES) | set(_NAMES))


def __getattr__(name):
    if name in _SUBMODULES:
        return _import_module(f"{__name__}.{name}")
    if name in _NAMES:
        return getattr(_import_module(f"{__name__}.{_NAMES[name]}"), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return __all__
