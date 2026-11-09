"""Create starter team: the Agents page's one-click way to have agents.

The same helper PUBLIC_SETUP.md documents (acceptance/runner.py prepare_minds): it authors the supplied public lead,
author and reviewer in a NEW folder under the installation's agents folder, with fresh cartridges, and makes no model
call. Each new home is then registered through the Agents page's own add path (preview, confirm). Nothing existing is
moved, copied or changed; an identity already here is reported, never duplicated.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from aether.paths import REPOSITORY_ROOT

ROLES = ('lead', 'author', 'reviewer')
PLACEHOLDER = 'not-configured'


def _runner():
    path = REPOSITORY_ROOT / 'AetherPublicFacing/Release/acceptance/runner.py'
    spec = importlib.util.spec_from_file_location('aether_acceptance_runner', path)
    if spec is None or not path.is_file():
        raise ValueError('The starter team templates are not in this installation.')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _connection(settings):
    """The first saved connection that reaches a model, as the new agents' home default; else the placeholder."""
    try:
        profiles = settings.read()['document'].get('profiles') or {}
    except Exception:            # noqa: BLE001 - the placeholder default is always valid; campaigns choose models anyway
        return 'unconfigured'
    usable = sorted(name for name, p in profiles.items() if isinstance(p, dict) and p.get('requestedApiIdentifier') != PLACEHOLDER)
    return usable[0] if usable else 'unconfigured'


def create_starter_team(registry, share, settings) -> dict:
    with registry.lock:
        return _create_starter_team(registry, share, settings)


def _create_starter_team(registry, share, settings) -> dict:
    if not registry.default_folder:
        raise ValueError('This installation has no agents folder for a starter team.')
    runner = _runner()
    ids = {json.loads((runner.ACCEPTANCE_DIR / 'minds' / role / 'participant.json').read_bytes())['id'] for role in ROLES}
    present = sorted(ids & {a['id'] for a in registry.listing()['agents']})
    if present:
        raise ValueError('The starter team is already on this computer (' + ', '.join(present) + '). Its agents are listed below.')
    root = Path(registry.default_folder)
    destination, n = root / 'starter-team', 2
    while destination.exists():
        destination, n = root / f'starter-team-{n}', n + 1
    made = runner.prepare_minds(destination, Path(share), _connection(settings), list(ROLES))
    for home in made['homes']:
        review = registry.preview('inject', home['home'])
        registry.confirm(review['review'], review['token'])
    return dict(registry.listing(), created=dict(folder=str(destination), agents=[h['id'] for h in made['homes']],
                                                 deployment=made['deployment'], model_calls=made['model_calls']))
