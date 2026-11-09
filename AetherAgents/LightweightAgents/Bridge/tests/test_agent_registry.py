"""Availability controls preserve homes/campaigns and serialize ejection against turn start."""
import json
from pathlib import Path
import threading

import pytest

from aether.atelier.agent_registry import AgentRegistry
from aether.atelier.atelier import Atelier


def home_at(path, aid='writer', name='Writer'):
    path.mkdir(parents=True)
    (path / 'participant.json').write_text(json.dumps(dict(id=aid, name=name, kind='member', deployment='unchanged-model')))
    (path / 'identity').mkdir()
    (path / 'identity/System_0.txt').write_text('Synthetic identity: preserve exactly.')
    (path / 'memory').mkdir()
    (path / 'memory/private-note.txt').write_text('Synthetic memory: preserve exactly.')
    return path


def inventory(path):
    return {str(p.relative_to(path)): p.read_bytes() for p in path.rglob('*') if p.is_file()}


def confirm(registry, action, value):
    p = registry.preview(action, str(value))
    return registry.confirm(**p)


def test_inject_duplicate_eject_reinject_preserve_home_and_campaign(tmp_path):
    home = home_at(tmp_path / 'existing')
    share = tmp_path / 'share'
    room = Atelier(share, 'campaign-a')
    room.post(dict(id='writer', kind='member', name='Writer'), 'Present for this campaign.')
    before_home, before_campaign = inventory(home), inventory(share)
    registry = AgentRegistry(tmp_path / 'local/agents.json', campaign_roots=lambda: [share])
    assert not registry.listing()['agents'] and not registry.path.exists()
    view = registry.preview('inject', str(home))
    assert view['review']['agent']['campaigns'][0]['id'] == 'campaign-a'
    assert not registry.path.exists()
    assert registry.confirm(**view)['agents'][0]['status'] == 'available'
    assert registry.preview('inject', str(home))['review']['already_available']
    assert len(confirm(registry, 'inject', home)['agents']) == 1
    assert confirm(registry, 'eject', 'writer')['agents'][0]['status'] == 'ejected'
    assert not registry.acquire('writer', home)
    restarted = AgentRegistry(registry.path, campaign_roots=lambda: [share])
    assert not restarted.available('writer')
    assert confirm(restarted, 'inject', home)['agents'][0]['status'] == 'available'
    assert inventory(home) == before_home and inventory(share) == before_campaign


def test_ejection_during_turn_blocks_new_turns_can_cancel_and_completes(tmp_path):
    home = home_at(tmp_path / 'agent')
    registry = AgentRegistry(tmp_path / 'registry.json')
    confirm(registry, 'inject', home)
    started, finish = threading.Event(), threading.Event()
    def worker():
        with registry.turn('writer', home) as allowed:
            assert allowed
            started.set()
            assert finish.wait(5)
    thread = threading.Thread(target=worker)
    thread.start()
    try:
        assert started.wait(5)
        assert confirm(registry, 'eject', 'writer')['agents'][0]['status'] == 'ejection_pending'
        assert not registry.acquire('writer', home)
        assert registry.cancel_eject('writer')['agents'][0]['status'] == 'available'
        assert confirm(registry, 'eject', 'writer')['agents'][0]['status'] == 'ejection_pending'
    finally:
        finish.set(); thread.join(5)
    assert registry.listing()['agents'][0]['status'] == 'ejected'
    assert not registry.is_working('writer')
    with pytest.raises(ValueError, match='completed'):
        registry.cancel_eject('writer')


def test_conflicting_identity_and_stale_review_never_overwrite(tmp_path):
    home = home_at(tmp_path / 'one')
    other = home_at(tmp_path / 'two')
    registry = AgentRegistry(tmp_path / 'registry.json')
    confirm(registry, 'inject', home)
    before = registry.path.read_bytes()
    with pytest.raises(ValueError, match='already registered'):
        registry.preview('inject', str(other))
    review = registry.preview('eject', 'writer')
    assert registry.acquire('writer')
    with pytest.raises(ValueError, match='changed'):
        registry.confirm(**review)
    registry.release('writer')
    assert registry.path.read_bytes() == before
    review = registry.preview('eject', 'writer')
    review['review']['agent']['home'] = str(other)
    with pytest.raises(ValueError, match='changed'):
        registry.confirm(**review)
    assert registry.path.read_bytes() == before


def test_failed_write_and_missing_folder_preserve_registration(tmp_path, monkeypatch):
    from aether.atelier import agent_registry
    home = home_at(tmp_path / 'one')
    registry = AgentRegistry(tmp_path / 'registry.json')
    confirm(registry, 'inject', home)
    before = registry.path.read_bytes()
    def fail(*args):
        raise OSError('synthetic disk failure')
    monkeypatch.setattr(agent_registry, 'atomic_write_bytes', fail)
    with pytest.raises(OSError):
        confirm(registry, 'eject', 'writer')
    assert registry.path.read_bytes() == before and registry.available('writer')
    (home / 'participant.json').rename(home / 'participant.saved')
    assert registry.listing()['agents'][0]['status'] == 'unavailable'
    assert not registry.acquire('writer', home)


def test_legacy_homes_and_memory_owner_registered_without_memory_read(tmp_path):
    home = home_at(tmp_path / 'existing')
    registry = AgentRegistry(tmp_path / 'registry.json', legacy_homes=lambda: [home])
    assert registry.listing()['agents'][0]['status'] == 'available' and not registry.path.exists()
    confirm(registry, 'eject', 'writer')
    assert not registry.available('writer')  # Discovery cannot re-inject an ejected agent.
    heavy = tmp_path / 'heavy'
    (heavy / 'memory').mkdir(parents=True)
    (heavy / 'memory/.cartridge.json').write_text(json.dumps(dict(owner='heavy', path='missing.sqlite')))
    assert confirm(registry, 'inject', heavy)['agents'][0]['id'] == 'heavy'
    assert not (heavy / 'memory/missing.sqlite').exists()


def test_place_skips_ejected_agent_and_reinject_restores_existing_model(tmp_path, monkeypatch):
    from tests.test_atelier_place_profile_reload_offline import _place, OPERATOR
    place, room, box, profiles, made, logs = _place(tmp_path, monkeypatch)
    registry = AgentRegistry(tmp_path / 'registry.json', legacy_homes=lambda: [m.home for m in place.minds])
    place.agent_registry = registry
    confirm(registry, 'eject', 'writer')
    assert not place.round()['turns'] and not made
    assert place.record()['minds'] == []
    confirm(registry, 'inject', place.minds[0].home)
    assert len(place.round()['turns']) == 1 and len(made) == 1
    assert place.minds[0].deployment == 'research'


def test_manual_wake_respects_ejection_and_finishes_reserved_turn(tmp_path):
    from aether.atelier import atelier_http
    home = home_at(tmp_path / 'agent')
    registry = AgentRegistry(tmp_path / 'registry.json')
    confirm(registry, 'inject', home)
    started, finish = threading.Event(), threading.Event()
    def run(*args):
        started.set()
        assert finish.wait(5)
    state = dict(atelier_root=str(tmp_path / 'share'), agent_registry=registry, atelier_turn=run)
    cid = atelier_http.open_commission(state, dict(text='Synthetic campaign.'))['commission_id']
    body = dict(commission=cid, participant=dict(id='writer', name='Writer', kind='member'))
    try:
        assert atelier_http.wake(state, body)[0] == 202
        assert started.wait(5)
        assert confirm(registry, 'eject', 'writer')['agents'][0]['status'] == 'ejection_pending'
    finally:
        finish.set(); atelier_http.settle(5)
    assert atelier_http.wake(state, body)[0] == 409
    assert registry.listing()['agents'][0]['status'] == 'ejected'


def test_http_page_preview_confirmation_origin_and_picker(tmp_path, monkeypatch):
    from http.server import ThreadingHTTPServer
    from aether.mission_control.atelier_server import make_handler
    from aether.atelier import agent_registry_http
    from tests.test_api_vault import running, request
    home = home_at(tmp_path / 'agent')
    registry = AgentRegistry(tmp_path / 'registry.json')
    monkeypatch.setattr(agent_registry_http, 'choose_folder', lambda: str(home))
    state = dict(agent_registry=registry, agent_directory=None)
    with running(ThreadingHTTPServer(('127.0.0.1', 0), make_handler(state))) as base:
        code, raw, headers = request(base, '/agents')
        assert code == 200 and b'Add existing agent' in raw and headers['Cache-Control'] == 'no-store'
        assert request(base, '/agents/insights')[0] == 200
        assert json.loads(request(base, '/api/agent-availability/choose-folder', {})[1])['path'] == str(home)
        body = dict(action='inject', value=str(home))
        assert request(base, '/api/agent-availability/preview', body, origin='https://hostile.invalid')[0] == 403
        assert not registry.path.exists()
        code, raw, _ = request(base, '/api/agent-availability/preview', body)
        assert code == 200
        review = json.loads(raw)
        assert request(base, '/api/agent-availability/confirm', review, origin='https://hostile.invalid')[0] == 403
        assert not registry.path.exists()
        assert request(base, '/api/agent-availability/confirm', review)[0] == 200
        assert json.loads(request(base, '/api/agent-availability')[1])['agents'][0]['status'] == 'available'


def test_default_and_legacy_folders_discover_in_place_and_respect_ejection(tmp_path):
    from aether.mission_control.atelier_server import InstallationAgents
    from aether.atelier.workplace import Workplace
    data = tmp_path / 'data'
    homes = [home_at(data/'minds'/'new', 'new'), home_at(data/'homes'/'old', 'old')]
    homes += [home_at(data/'demo-homes'/role, role, role.title()) for role in ('lead', 'author', 'reviewer')]
    before = [inventory(h) for h in homes]
    workplace = Workplace(data/'.aether_control/workplace/connection.json')
    directory = InstallationAgents(workplace, data/'minds', [('Existing agents',data/'homes'),('Demo agents',data/'demo-homes')])
    registry = AgentRegistry(data/'.aether_control/agents.json', legacy_homes=directory.legacy_homes,
                             default_folder=data/'minds')
    assert {r['id'] for r in registry.listing()['agents']} == {'new','old','lead','author','reviewer'}
    assert registry.listing()['default_folder'] == str((data/'minds').resolve())
    assert not registry.path.exists()
    confirm(registry, 'eject', 'lead')
    assert next(r for r in registry.listing()['agents'] if r['id']=='lead')['status'] == 'ejected'
    assert [inventory(h) for h in homes] == before
    assert not workplace.path.exists()
