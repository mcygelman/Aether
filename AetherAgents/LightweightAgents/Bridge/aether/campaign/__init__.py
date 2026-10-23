"""Campaign (MOD-17).

A campaign is one piece of work run through Mission Control, from objective to accepted
deliverable (MVP-01 contract, section 0). Today's code calls it a "project" (the registry
projects.json, its framing record and its <root>/.aether stores); this module names it campaign
and does not rename that code. A Project stays the parent of roadmaps.

Contract: campaign_state(records) -> {stage, condition, reason, actions, sources}, a pure
projection with the contract's precedence, now also listing the open obligations (G7: Closed only
when closure is recorded and none is open); VOCABULARY, the one mapping from every status literal
to a condition (an unmapped literal is UNRECOGNIZED, never guessed); load_campaign_records(root,
campaign_id), a read-only loader of the durable records (native host stores and generation
attempts included; links never followed); handle_campaign_state, the
GET /api/campaign-state handler Mission Control dispatches to (this module never imports
Mission Control).
Depends on: roadmaps, integrity, runtime. Pure record projection lives in roadmaps.campaign_projection; runtime never imports this higher-level campaign package.
Owner: Claude (Opus 5.5).

The public interface is __all__: the submodules and names listed there. Everything
else in this package is internal. Submodules load on first use (PEP 562), so
importing this package has no side effects.
"""
from importlib import import_module as _import_module

_SUBMODULES = ('campaign_http', 'records', 'receipts', 'state', 'vocabulary')
_NAMES = {'campaign_state': 'state', 'execution_status': 'state', 'load_campaign_records': 'records',
          'campaign_receipts': 'receipts', 'load_campaign_receipts': 'receipts',
          'CampaignNotFound': 'records', 'CampaignRegistryUnavailable': 'records', 'handle_campaign_state': 'campaign_http',
          'VOCABULARY': 'vocabulary', 'CONDITIONS': 'vocabulary', 'PRECEDENCE': 'vocabulary',
          'STAGES': 'vocabulary', 'UNRECOGNIZED': 'vocabulary', 'classify': 'vocabulary'}

__all__ = sorted(set(_SUBMODULES) | set(_NAMES))


def __getattr__(name):
    if name in _SUBMODULES:
        return _import_module(f"{__name__}.{name}")
    if name in _NAMES:
        return getattr(_import_module(f"{__name__}.{_NAMES[name]}"), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return __all__
