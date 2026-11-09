"""What an operator still needs before agents can work here: information, never a gate.

GET /api/readiness[?commission=<id>] in the Atelier edition. Read-only: the API key store's status (never a key), the
saved model connections, the agents available on this installation, the campaigns and their teams, and whether this
computer's place is running. The navigation shows it as two chips, a first-use checklist and a campaign note; every
action stays available whatever it says.
"""
from __future__ import annotations

from pathlib import Path

from . import projection

PLACEHOLDER = 'not-configured'     # installed_app.unconfigured_profiles: the setup placeholder reaches no model


def _keys():
    try:
        from aether.keys.vault import get_vault
        status = get_vault().status()
    except Exception:            # noqa: BLE001 - an unreadable store is reported as unknown, never raised
        return dict(supported=False, configured=False, unlocked=False, count=None)
    return dict(supported=bool(status.get('supported')), configured=bool(status.get('configured')),
                unlocked=bool(status.get('unlocked')), count=len(status.get('keys') or ()) if status.get('unlocked') else None)


def _connections(store):
    try:
        profiles = store.read()['document'].get('profiles') or {}
    except Exception:            # noqa: BLE001 - the Model connections page explains an unreadable file
        return [], False
    usable = [p for p in profiles.values() if isinstance(p, dict) and p.get('requestedApiIdentifier') != PLACEHOLDER]
    return usable, any(p.get('credential_ref') is None for p in usable)


def readiness(state, root, commission=None) -> dict:
    keys = _keys()
    connections, keyless = _connections(state['model_settings'])
    try:
        available = sum(a['status'] == 'available' for a in state['agent_registry'].listing()['agents'])
    except Exception:            # noqa: BLE001
        available = 0
    campaigns = projection.commissions(root) if root else []
    roster = state.get('campaign_roster')
    try:
        teamed = bool(roster.usage()) if roster is not None else False
    except Exception:            # noqa: BLE001
        teamed = False
    workplace = state['workplace']
    config = workplace.config()
    place = dict(configured=bool(config), running=workplace.active())
    if config:                 # the network this computer joined, by its name (the Join Network page)
        from .workplace import network_name
        place['network'] = network_name(config['share']) or 'Aether network'
    keys_done = (keys['configured'] and (not keys['unlocked'] or (keys['count'] or 0) > 0)) or keyless
    steps = [
        dict(id='keys', done=keys_done, title='Save an API key', href='/settings/api-keys/',
             text='Create your API password, then add your provider’s key. A model server on this computer needs none.'),
        dict(id='connection', done=bool(connections), title='Add a model connection', href='/settings/models-context/',
             text='Tell Aether how to reach a model; saving checks that it answers.'),
        dict(id='agents', done=available > 0, title='Have agents here', href='/agents',
             text='Create the starter team (a lead, an author and a reviewer) on the Agents page, or add an existing '
                  'agent folder.'),
        dict(id='campaign', done=bool(campaigns), title='Start a project', href='/missions',
             text='Create a mission, then a project in it with a short brief: what to deliver, why, and the budget.'),
        dict(id='team', done=teamed, title='Assign the team', href='/projects/roster',
             text='Choose who works on the project, their role and the model that drives them.'),
        dict(id='running', done=place['running'], title='Join a network', href='/network',
             text='Join this computer to your Aether network so agents take their turns as messages arrive.')]
    answer = dict(keys=keys, connections=len(connections), agents=available, campaigns=len(campaigns), place=place,
                  steps=steps, ready=all(step['done'] for step in steps))
    if commission and root and projection.exists(root, commission) and roster is not None:
        try:
            members = [m for m in roster.view(commission)['members'] if m.get('available')]
        except Exception:        # noqa: BLE001 - an unreadable roster is the roster page's to explain
            members = None
        note = None
        if members is not None and not members:
            note = dict(text='No one is on this project’s team yet.', label='Add the team',
                        href='/projects/roster?commission=' + commission)
        elif members and not place['running']:
            note = dict(text='This computer hasn’t joined a network, so agents work only when you ask them to.',
                        label='Join Network', href='/network')
        answer.update(commission=commission, team=None if members is None else len(members), note=note)
    return answer
