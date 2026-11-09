"""Projects and Roadmaps (MOD-02).

Projects, their roadmaps and sprints, the task board, and approved-plan publication into the tracker.

Contract: RoadmapStore (read/write with revision check and validation), validate_data, project folders,
the planning and plan-ruling history contracts, roadmap publication and evidence, the tracker HTTP surface,
and operators.reader(projects.json), the registered-operator lookup injected into Model Catalog stores.
Depends on: integrity, config, agent, models.
Known debt: plan_ruling_contract validates findings evidence with three Runtime review validators;
the fix is to move those validators into Integrity's contracts (MOD-15 work).
Owner: Operator assigns.

The public interface is __all__: the submodules and names listed there. Everything
else in this package is internal. Submodules load on first use (PEP 562), so
importing this package has no side effects.
"""
from importlib import import_module as _import_module

_SUBMODULES = ('board', 'operators', 'plan_ruling_contract', 'plan_rulings_http', 'planning_contract', 'project_engagement', 'project_engagement_contract', 'project_folders', 'project_planning', 'project_roadmaps', 'project_setup', 'projects', 'roadmap_evidence', 'roadmap_evidence_http', 'roadmap_publication', 'roadmap_publication_http', 'tracker')
_NAMES = {'RoadmapStore': 'tracker.tracker_store', 'validate_data': 'tracker.tracker_store', 'migrate_to_v2': 'tracker.tracker_store', 'DataValidationError': 'tracker.tracker_store', 'RevisionConflictError': 'tracker.tracker_store'}

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
