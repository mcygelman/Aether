"""Create fresh heavyweight homes and read their roster; never rewrite a resident identity.

The template is product guidance, not a copy of Praxis or another resident's memory.
SANCTUM reports identity integrity; it does not prevent an otherwise readable wake.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from aether.integrity import canon, sanctum
from aether.tasker._files import file_lock, now, write_json, write_text
from . import memory, seeds

TEMPLATE = Path(__file__).parent / 'templates' / 'SYSTEM_0.txt'
SECTIONS = ('AGENT_HEADER', 'CORE_IDENTITY', 'PRIME_CONTEXT_STACK', 'DYNAMIC_POINTERS',
            'CRITICAL_WORKFLOWS', 'SYMBOLIC_COGNITION', 'ENVIRONMENT', 'FIRST_PRINCIPLES')


def home_path(data_root, agent_id):
    if not isinstance(agent_id, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,95}', agent_id):
        raise ValueError('agent id must contain only letters, numbers, underscores or hyphens')
    root = (Path(data_root) / 'minds').resolve()
    home = (root / agent_id).resolve()
    if home.parent != root:
        raise ValueError('the heavyweight home must be inside the minds folder')
    return home


def create_heavyweight_home(data_root, agent_id, name, template=None, deployment=None):
    home = home_path(data_root, agent_id)
    if not isinstance(name, str) or not name.strip() or any(c in name for c in '\r\n⌟⟫'):
        raise ValueError('give the mind a nonempty single-line name')
    text = Path(template or TEMPLATE).read_text(encoding='utf-8')
    text = text.replace('{{NAME}}', name).replace('{{AGENT_ID}}', agent_id)
    if any('[' + section + ']' not in text for section in SECTIONS) or '⟪SYSTEM_0_END⟫' not in text:
        raise ValueError('the heavyweight template needs the full System 0 sections')
    with file_lock(home.parent / '.heavyweight-create.lock'):
        if home.exists():
            raise FileExistsError(f'{agent_id} already has a home; existing identity and memory are preserved')
        home.mkdir(parents=True)
        write_text(home / 'identity/System_0.txt', text)
        write_json(home / 'SANCTUM_MANIFEST.json', dict(
            format='SANCTUM-1', agent=name, authored=now(),
            authorized_by='Operator requested fresh product heavyweight home', prior_manifest=None,
            protected={'identity/System_0.txt': 'sha256:' + canon.canon_sha(text)}))
        memory.create(home, agent_id, name)
        seeds.plant(home, agent_id, name)
        write_json(home / 'participant.json', dict(id=agent_id, name=name, kind='heavyweight',
                                                  deployment=deployment))
    return home


def list_heavyweights(data_root):
    active = {}
    for path in sorted((Path(data_root) / 'sprints').glob('*/sprint.json')):
        try:
            row = json.loads(path.read_text(encoding='utf-8'))
            # A sprint stopped for good (its terminal receipt; it never resumes) no longer holds its lead (11-07).
            if (path.parent / 'timer/RECOVERY_STOP.json').exists():
                continue
            if row.get('state') not in ('closed-pass', 'closed-fail'):
                active.setdefault(row.get('overseer'), []).append(row.get('id', path.parent.name))
        except (OSError, ValueError):
            continue
    rows = []
    for path in sorted((Path(data_root) / 'minds').glob('*/participant.json')):
        try:
            row = json.loads(path.read_text(encoding='utf-8'))
            if row.get('kind') != 'heavyweight':
                continue
            home = home_path(data_root, row['id'])
            if home != path.parent.resolve():
                continue
            report = sanctum.verify(home)
            rows.append(dict(id=row['id'], name=row.get('name', row['id']), home=str(home),
                             deployment=row.get('deployment'), available=row['id'] not in active,
                             sprints=active.get(row['id'], []), integrity=report.to_dict()))
        except (OSError, ValueError, KeyError):
            continue
    return rows
