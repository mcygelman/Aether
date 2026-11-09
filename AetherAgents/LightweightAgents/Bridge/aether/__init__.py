"""Aether modules: each area of responsibility is a box with a defined interface.

Every module is a subpackage (``aether.<module>``) whose ``__init__`` is its public
interface. Code that moved here from ``aether_bridge`` keeps its old import path
through an alias, so nothing that imported it breaks. In the full development
repository, ``aether/modules/registry.json`` records each module's owner, state,
files and allowed dependencies, and ``python -m aether.modules check`` enforces
them. That register is not part of the public export; there, each package's
``__all__`` lists only the submodules it contains.
"""
