"""Paths box: the one place that knows where the Bridge and the repository live.

Code that moved out of aether_bridge used to compute folders from its own file
location (``Path(__file__).parents[1]`` and so on). Those numbers changed meaning
when the file moved, so every moved file asks here instead. When the Bridge itself
moves (Bridge Overhaul M6), only this file changes.

Outputs:
    BRIDGE_ROOT         the Bridge folder (aether_bridge/, aether/, tests/, fixtures/ ...)
    LEGACY_PACKAGE_DIR  aether_bridge/ (its web/ pages and default data files still live there)
    WEB_DIR             aether_bridge/web/, the Mission Control pages
    LIGHTWEIGHT_ROOT    AetherAgents/LightweightAgents, or the installed user-data root
    REPOSITORY_ROOT     the repository root, or the installed immutable resource root
"""
import os
from pathlib import Path

BRIDGE_ROOT = Path(__file__).resolve().parents[1]
LEGACY_PACKAGE_DIR = BRIDGE_ROOT / "aether_bridge"
WEB_DIR = LEGACY_PACKAGE_DIR / "web"


def _installed_root(name, fallback):
    value = os.environ.get(name)
    if not value:
        return fallback
    path = Path(value)
    if not path.is_absolute() or not path.is_dir():
        raise ValueError(f'{name} must name an existing absolute installation folder')
    return path.resolve()


# The installed entry point sets these before importing application modules.
# Source launches retain their original paths. Code/web resources stay immutable;
# installation settings and tool approvals follow the explicit data root.
LIGHTWEIGHT_ROOT = _installed_root('AETHER_INSTALL_DATA_ROOT', BRIDGE_ROOT.parent)
REPOSITORY_ROOT = _installed_root('AETHER_INSTALL_RESOURCES_ROOT', BRIDGE_ROOT.parents[min(2, len(BRIDGE_ROOT.parents) - 1)])

__all__ = ["BRIDGE_ROOT", "LEGACY_PACKAGE_DIR", "WEB_DIR", "LIGHTWEIGHT_ROOT", "REPOSITORY_ROOT"]
