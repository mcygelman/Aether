"""Registered operator lookup (Roadmaps, MOD-02): who may sign a settings save.

Inputs: the project registry path (projects.json).
Outputs: registered_operators(path) -> [{'id', 'display_name'}]; reader(path) -> a zero-argument
callable that Mission Control and the settings launchers inject into Model Catalog stores.
Reader never provisions an operator or migrates projects. Public startup calls ensure_local_operator
once to retain or create the local attribution record without an operator ceremony.
"""
from pathlib import Path
import uuid

from aether.config.context_configuration import ContextConfigurationError
from aether.roadmaps.project_engagement_contract import actor_valid
from aether.roadmaps.projects import ProjectRegistry


def registered_operators(registry_path):
    """Read existing identities only; do not provision an operator or migrate projects."""
    try:
        path = Path(registry_path).resolve()
        registry = ProjectRegistry(path.parent, path)
        registry.load()
        actor = registry._extra.get('local_operator')
        actor_valid(actor)
        # This is the present registry format. Future multi-operator lookup belongs here.
        return [{'id': str(uuid.UUID(actor['id'])), 'display_name': actor['display_name']}]
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        raise ContextConfigurationError('Registered operator identity unavailable; load the existing operator record first.') from None


def reader(registry_path):
    """Bind one registry path; each call re-reads and re-validates the whole registry."""
    path = Path(registry_path).resolve()
    return lambda: registered_operators(path)


def ensure_local_operator(registry):
    """Retain or create the local attribution record on first public startup."""
    with registry._lock:
        candidate = ProjectRegistry(registry.lightweight_dir, registry.registry_path)
        candidate.load()
        actor = candidate._extra.get('local_operator')
        if actor is not None:
            actor_valid(actor)
            return actor
        actor = dict(id=str(uuid.uuid4()), display_name='Local Operator', revision=1)
        candidate._extra['local_operator'] = actor
        candidate.save()
        registry.load()
        return actor
