"""Record one sprint outcome through the existing atomic RoadmapStore.

Never infer success, publish a roadmap or change unrelated sprint status. Paths
are installation configuration, never arguments chosen by a model tool call.
"""
from pathlib import Path
import re

from aether.roadmaps.tracker.tracker_store import RoadmapStore, migrate_to_v2, RevisionConflictError
from aether.tasker._files import now


def roadmap_path(sprint, config):
    """Resolve the host binding identically for the wake and outcome writer."""
    root = Path(sprint.data_root).resolve()
    path = Path(config.get('roadmap_path') or
                root / 'projects' / config['project'] / '.aether' / 'roadmaps.json')
    return path if path.is_absolute() else root / path


def record_outcome(sprint, summary, result):
    if result not in ('pass', 'fail') or not isinstance(summary, str) or not summary.strip():
        raise ValueError('give a pass or fail outcome and a nonempty summary')
    config = sprint.record()
    project = config.get('project')
    if not project:
        return {'status': 'not-linked', 'reason': 'this sprint has no project; its outcome remains in sprint.json'}
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,95}', project):
        raise ValueError('project id must contain letters, numbers, underscores or hyphens')
    path = roadmap_path(sprint, config)
    store = RoadmapStore(path)
    rid = config.get('roadmap_id') or project
    stamp = now()[:10]
    evidence = str(sprint.root.resolve())
    for attempt in range(3):
        data, revision = store.read()
        data = migrate_to_v2(data)
        if not any(r['id'] == rid for r in data['roadmaps']):
            data['roadmaps'].append(dict(id=rid, name=project, description='Sprints for project ' + project,
                                         status='Completed'))
        row = next((r for r in data['sprints'] if r['id'] == sprint.id), None)
        description = f'{result.upper()}: {summary}'
        desired = dict(id=sprint.id, roadmapId=rid, name=sprint.id, description=description,
                       status='Closed', owner=sprint.overseer, updated=stamp, evidence=[evidence])
        if row is None:
            desired['sequence'] = max((r.get('sequence', 0) for r in data['sprints'] if r['roadmapId'] == rid), default=0) + 1
            data['sprints'].append(desired)
        else:
            if all(row.get(k) == v for k, v in desired.items()):
                return dict(status='recorded', path=str(path), revision=revision, result=result)
            row.update(desired)
        roadmap = next(r for r in data['roadmaps'] if r['id'] == rid)
        if roadmap['status'] == 'Active' and not any(r['roadmapId'] == rid and r['status'] in ('Open', 'Planned')
                                                     for r in data['sprints']):
            roadmap['status'] = 'Completed'  # Tracker invariant: no unfinished work; not a success judgment.
        try:
            revision = store.write(data, expected_revision=revision)
            return dict(status='recorded', path=str(path), revision=revision, result=result)
        except RevisionConflictError:
            if attempt == 2:
                raise
