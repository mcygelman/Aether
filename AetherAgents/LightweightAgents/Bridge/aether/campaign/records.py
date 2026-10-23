"""Compatibility import for the shared read-only campaign projection."""
import sys
from aether.roadmaps.campaign_projection import records as _implementation
sys.modules[__name__] = _implementation
