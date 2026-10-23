"""Compatibility import for the shared read-only campaign projection."""
import sys
from aether.roadmaps.campaign_projection import state as _implementation
sys.modules[__name__] = _implementation
