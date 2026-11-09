"""Prepare Meridian's separate Tasker home and the product staff in an explicit data root.

Never modify a resident home, import private memories, start a model or service,
alter the GUI/loop, or replace existing identities, reflections or work state.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from aether.tasker import identity, staff
from aether.tasker._files import file_lock, write_json
from .homes import create_heavyweight_home, list_heavyweights

TEMPLATES = Path(__file__).parent / 'templates'
TEMPLATE = TEMPLATES / 'MERIDIAN_SYSTEM_0.txt'
PROVENANCE = TEMPLATES / 'MERIDIAN_PROVENANCE.json'
SETUP_SCHEMA = 'aether-meridian-tasker-preparation-1'


def _plain(path):
    path = Path(path).absolute()
    for part in (path, *path.parents):
        if part.is_symlink() or (hasattr(part, 'is_junction') and part.is_junction()):
            raise ValueError('Choose a data root without linked path components')
    return path.resolve()


def prepare(data_root):
    """Explicit, repeatable setup using existing native creation/seeding APIs only."""
    root = _plain(data_root)
    if (root / 'identity').exists() or (root / 'memory').exists():
        raise ValueError('Choose an application data root, not an existing agent home')
    origin = json.loads(PROVENANCE.read_text(encoding='utf-8'))
    if hashlib.sha256(TEMPLATE.read_bytes()).hexdigest() != origin['template_sha256']:
        raise ValueError('Meridian template does not match its provenance')
    catalog = json.loads((staff.PRODUCT_STAFF / 'catalog.json').read_text(encoding='utf-8'))
    wanted = {row['staff_id'] for row in catalog['templates']}
    if len(wanted) != len(catalog['templates']) or not wanted:
        raise ValueError('Product staff catalog has missing or duplicate IDs')
    for row in catalog['templates']:
        filename = row['file']
        if Path(filename).name != filename or not filename.startswith('Identity_') or not filename.endswith('.txt'):
            raise ValueError('Unexpected product staff filename')
        raw = (staff.PRODUCT_STAFF / filename).read_bytes()
        if hashlib.sha256(raw).hexdigest() != row['sha256']:
            raise ValueError('Product staff identity does not match the catalog')
        text = raw.decode('utf-8')
        identity.require(text)
        if identity.name_and_role(text)[0].lower() != row['staff_id']:
            raise ValueError('Product staff identity does not match its roster ID')
    home = _plain(root / 'minds/meridian')
    _plain(root / 'staff')
    with file_lock(root / '.meridian-setup.lock'):
        if home.exists():
            marker = home / 'PREPARATION.json'
            if not marker.is_file():
                raise ValueError('Existing meridian home preserved; reconcile its provenance before setup')
            previous = json.loads(marker.read_text(encoding='utf-8'))
            if previous.get('schema') != SETUP_SCHEMA or previous.get('runtime_id') != 'meridian':
                raise ValueError('Existing meridian home preserved; preparation provenance differs')
            participant = json.loads((home / 'participant.json').read_text(encoding='utf-8'))
            if participant.get('id') != 'meridian' or participant.get('kind') != 'heavyweight':
                raise ValueError('Existing meridian participant differs; nothing will be replaced')
        before = staff.roster(root)
        known = {row['id'] for row in before}
        for agent in wanted:
            target = _plain(root / 'staff' / agent)
            if agent not in known and target.exists():
                raise ValueError(f'Unregistered {agent} home preserved; reconcile before seeding')
        created = not home.exists()
        if created:
            create_heavyweight_home(root, 'meridian', 'Meridian', template=TEMPLATE)
            write_json(home / 'PREPARATION.json', dict(
                schema=SETUP_SCHEMA, runtime_id='meridian', source=origin,
                memory_policy='Separate local cartridge; no resident memory imported',
                authorization='Michel requested Meridian preparation for Tasker and local testing, 2026-11-03'))
        missing = sorted(wanted - known)
        added = staff.seed_staff(root, staff.PRODUCT_STAFF, only=missing) if missing else []
        roster = staff.roster(root)
        current = {row['id']: row for row in roster}
        if any(current.get(row['id']) != row for row in before):
            raise RuntimeError('Existing roster changed concurrently; preserve it and reconcile')
        if not wanted <= set(current):
            raise RuntimeError('Catalog seeding incomplete; retained homes must be reconciled')
        return dict(data_root=str(root), meridian_home=str(home), meridian_created=created,
                    lightweights_added=added, lightweight_count=len(roster),
                    heavyweight_roster=list_heavyweights(root), model_calls=0,
                    gui_status='Not changed; Opus must bind this data root to the normal application workflow')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-root', required=True, help='Persistent application data root, separate from resident homes')
    args = parser.parse_args()
    print(json.dumps(prepare(args.data_root), indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
