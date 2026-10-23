"""Aether modules: each area of responsibility is a box with a defined interface.

Every module is a subpackage (``aether.<module>``) whose ``__init__`` is its public
interface. Code that moved here from ``aether_bridge`` keeps its old import path
through an alias, so nothing that imported it breaks. The register of modules,
their owners, states and files is ``aether/modules/registry.json``; run
``python -m aether.modules`` from the Bridge folder for the current register.

Design: AetherPlanAndAnalysis/Specifications/Modules/00_MODULE_MAP.md
"""
