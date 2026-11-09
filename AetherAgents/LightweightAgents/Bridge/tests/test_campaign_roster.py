"""Campaign assignments execute locally; homes, other campaigns and model settings are preserved."""
import json
from pathlib import Path
import pytest

from aether.atelier import Atelier, Budget, Participant
from aether.atelier.agent_registry import AgentRegistry
from aether.atelier.campaign_roster import CampaignRoster, assignments
from aether.atelier.place import Place
from aether.atelier.turn import Deployment
from aether.mailbox import Mailbox
from tests.test_atelier_context_guard_offline import Strict

OPERATOR = dict(id='operator', kind='operator', name='Operator')
PROFILE = dict(endpoint='http://offline.invalid', contextWindow=131072, maxOutputTokens=500)


def inventory(path):
    return {str(p.relative_to(path)): p.read_bytes() for p in path.rglob('*') if p.is_file()}


@pytest.fixture
def setup(tmp_path):
    share = tmp_path / 'share'
    homes = [Participant.author(tmp_path / name, id=name, kind='member', name=name.title(),
                               system='You help.', deployment='original', cartridge=False) for name in ('writer', 'extra')]
    for cid in ('one', 'two'):
        Atelier(share, cid, budget=Budget(model_calls=30)).open(OPERATOR, 'Say hello.')
    profiles = tmp_path / 'models.json'
    profiles.write_text(json.dumps(dict(profiles={name: dict(PROFILE, requestedApiIdentifier=name)
                                                for name in ('original', 'fast', 'careful')})))
    registry = AgentRegistry(tmp_path / 'agents.json', legacy_homes=lambda: [h.home for h in homes],
                             campaign_roots=lambda: [share])
    roster = CampaignRoster(lambda: share, registry, profiles, homes=lambda: [homes[0].home])
    return share, homes, profiles, registry, roster


def save(roster, cid='one', aid='writer', role='reviewer', model='careful', **extra):
    return roster.save(dict(commission=cid, revision=roster.view(cid)['revision'], agent_id=aid,
                            role=role, model_profile=model, **extra))


def test_campaign_specific_preservation_and_duplicate_update(setup):
    share, homes, profiles, registry, roster = setup
    before = [inventory(h.home) for h in homes], profiles.read_bytes(), inventory(share)
    assert roster.view('one')['members'][0]['source'] == 'Existing default'
    save(roster)
    save(roster, 'two', role='lead', model='fast')
    one, two = roster.effective('one')[0], roster.effective('two')[0]
    assert (one.kind, one.deployment) == ('reviewer', 'careful')
    assert (two.kind, two.deployment) == ('lead', 'fast')
    save(roster, role='member', model='fast')
    assert len(assignments(share, 'one')['members']) == 1
    assert [inventory(h.home) for h in homes] == before[0] and profiles.read_bytes() == before[1]
    assert all((share / rel).read_bytes() == value for rel, value in before[2].items())
    assert homes[0].kind == 'member' and homes[0].deployment == 'original'
    assert {c['id'] for c in registry.listing()['agents'][1]['campaigns']} == {'one', 'two'}


def test_new_member_only_runs_in_assigned_campaign_and_uses_selected_model(setup, monkeypatch):
    share, homes, profiles, registry, roster = setup
    save(roster, aid='extra', role='lead', model='fast')
    requests = []
    def build(deployment):
        client = Strict([dict(role='assistant', content='Hello.')]*10, context_window=131072, max_tokens=500)
        requests.append((deployment.model, client))
        return client
    monkeypatch.setattr(Deployment, 'client', build)
    place = Place(share, [homes[0].home], profiles_path=profiles, log=lambda _: None)
    place.agent_registry, place.campaign_roster = registry, roster
    result = place.round()
    assert not result['failed']
    assert {(t['commission'], t['mind']) for t in result['turns']} == {('one','writer'), ('one','extra'), ('two','writer')}
    assert {m for m, _ in requests} == {'original', 'fast'}
    fast = next(c for m,c in requests if m == 'fast')
    text = json.dumps(fast.requests)
    assert 'the lead of this commission' in text and 'Extra as lead' in text
    assert place.minds[0].deployment == 'original'
    assert place.delivery_directory('one')[0]['extra'] == 'lead'
    assert 'extra' not in place.delivery_directory('two')[0]


def test_inflight_snapshot_then_next_turn_and_profile_recovery(setup, monkeypatch):
    share, homes, profiles, registry, roster = setup
    save(roster, role='lead', model='fast')
    place = Place(share, [homes[0].home], profiles_path=profiles, log=lambda _: None)
    place.agent_registry, place.campaign_roster = registry, roster
    original = place.campaign_minds('one')[0]
    models = []
    monkeypatch.setattr(Deployment, 'client', lambda dep: models.append(dep.model) or object())
    client = place.client_for(original)
    save(roster, role='reviewer', model='careful')
    assert original.kind == 'lead' and original.deployment == 'fast'
    next_turn = place.campaign_minds('one')[0]
    assert next_turn.kind == 'reviewer' and next_turn.deployment == 'careful'
    next_client = place.client_for(next_turn)
    valid = profiles.read_bytes()
    profiles.write_text('{')
    assert place.client_for(next_turn) is next_client
    assert place.client_for(original) is client
    profiles.write_bytes(valid)
    assert place.client_for(next_turn) is next_client
    assert models == ['fast', 'careful']


def test_live_edit_during_turn_is_picked_up_before_next_agent(setup, monkeypatch):
    share, homes, profiles, registry, roster = setup
    save(roster, aid='extra', model='fast')
    seen = []
    place = Place(share, [homes[0].home], client=object(), log=lambda _: None)
    place.agent_registry, place.campaign_roster = registry, roster
    def turn(mind, atelier, *args, **kwargs):
        seen.append((atelier.campaign_id, mind.id, mind.kind, mind.deployment))
        if atelier.campaign_id == 'one' and mind.id == 'writer':
            save(roster, aid='extra', role='lead', model='careful')
    monkeypatch.setattr('aether.atelier.place.run_turn', turn)
    result = place.round()
    assert not result['failed']
    assert ('one', 'extra', 'lead', 'careful') in seen


def test_unavailable_agent_preserves_assignment_without_default_fallback(setup):
    share, homes, profiles, registry, roster = setup
    save(roster)
    review = registry.preview('eject', 'writer')
    assert [c['id'] for c in review['review']['agent']['campaigns']] == ['one']
    registry.confirm(review['review'], review['token'])
    assert roster.effective('one') == []
    assert roster.view('one')['members'][0]['available'] is False
    with pytest.raises(ValueError, match='unavailable'):
        save(roster)
    assert assignments(share, 'one')['members']['writer']['model_profile'] == 'careful'
    review = registry.preview('inject', str(homes[0].home))
    registry.confirm(review['review'], review['token'])
    assert roster.effective('one')[0].deployment == 'careful'


@pytest.mark.parametrize('change', [dict(role='invented'), dict(model_profile='missing'), dict(agent_id='absent'),
                                    dict(commission='../outside'), dict(revision='old')])
def test_invalid_and_stale_saves_preserve_roster(setup, change):
    share, homes, profiles, registry, roster = setup
    save(roster)
    path = share / 'daybooks/one/roster.json'
    before = path.read_bytes()
    body = dict(commission='one', revision=roster.view('one')['revision'], agent_id='writer', role='lead', model_profile='fast')
    body.update(change)
    with pytest.raises((ValueError, OSError)):
        roster.save(body)
    assert path.read_bytes() == before


def test_conflicting_tab_save_and_restart(setup):
    share, homes, profiles, registry, roster = setup
    old = roster.view('one')['revision']
    save(roster)
    with pytest.raises(ValueError, match='changed'):
        roster.save(dict(commission='one', revision=old, agent_id='extra', role='lead', model_profile='fast'))
    fresh = CampaignRoster(lambda: share, registry, profiles)
    assert fresh.effective('one', [homes[0]])[0].deployment == 'careful'


def test_claims_include_added_agents_and_skip_remote_owner(setup, monkeypatch):
    share, homes, profiles, registry, roster = setup
    save(roster, aid='extra')
    place = Place(share, [], client=object(), log=lambda _: None)
    place.agent_registry, place.campaign_roster = registry, roster
    monkeypatch.setattr(place, 'others', lambda: [dict(name='elsewhere', alive=True, minds=['extra'], participants=[])])
    assert place.claimed_elsewhere() == {'extra': 'elsewhere'}
    assert place.record()['minds'] == ['extra']
    result = place.round()
    assert not result['turns'] and result['skipped'][0]['hosted_by'] == 'elsewhere'


def test_profiles_return_only_labels_not_credentials(setup):
    _, _, profiles, _, roster = setup
    raw=json.loads(profiles.read_bytes())
    raw['profiles']['fast']['credential_ref']={'kind':'vault','name':'synthetic-private-ref'}
    profiles.write_text(json.dumps(raw))
    assert 'synthetic-private-ref' not in json.dumps(roster.view('one'))


def test_staff_directory_separates_current_assignments_and_unreadable_projects(setup):
    share, homes, profiles, registry, roster = setup
    save(roster, role='lead', model='fast')
    save(roster, 'two', aid='extra', role='reviewer', model='careful')
    before = inventory(share), [inventory(h.home) for h in homes]
    projects = {p['id']: p for p in roster.staff()['projects']}
    assert projects['one']['revision'] == roster.view('one')['revision']
    assert projects['one']['members'][0]['model_profile'] == 'fast'
    assert any(m['agent_id'] == 'extra' and m['role'] == 'reviewer' for m in projects['two']['members'])
    assert inventory(share) == before[0] and [inventory(h.home) for h in homes] == before[1]
    (share/'daybooks/two/roster.json').write_text('{',encoding='utf-8')
    projects = {p['id']: p for p in roster.staff()['projects']}
    assert projects['two']['error'] and projects['one']['members']


def test_setup_placeholder_cannot_be_assigned(setup):
    _, _, profiles, _, roster = setup
    doc=json.loads(profiles.read_bytes())
    doc['profiles']['placeholder']=dict(PROFILE,requestedApiIdentifier='not-configured')
    profiles.write_text(json.dumps(doc),encoding='utf-8')
    before=roster.view('one')['revision']
    with pytest.raises(ValueError,match='saved driving model'):save(roster,model='placeholder')
    assert roster.view('one')['revision']==before


def test_manual_wake_resolves_registered_home_and_campaign_model(setup, monkeypatch):
    from aether.atelier import atelier_http
    share, homes, profiles, registry, roster = setup
    save(roster, aid='extra', role='reviewer', model='careful')
    calls=[]
    monkeypatch.setattr(Deployment, 'client', lambda dep: calls.append(dep.model) or object())
    def turn(mind, target, box, client, root, *, stopped=None):
        assert stopped is not None and stopped() is False
        calls.append((mind.id, mind.kind, str(mind.home), target.campaign_id))
    monkeypatch.setattr('aether.atelier.turn.run_turn', turn)
    state=dict(atelier_root=str(share), agent_registry=registry, campaign_roster=roster)
    try:
        code,value=atelier_http.wake(state, dict(commission='one', participant=dict(id='extra',kind='member')))
        assert code==202 and value['participant']['kind']=='reviewer'
    finally:
        atelier_http.settle(5)
    assert calls==['careful', ('extra','reviewer',str(homes[1].home),'one')]


def test_same_profile_name_in_two_files_never_reuses_wrong_client(setup, monkeypatch):
    share, homes, profiles, registry, roster = setup
    different=profiles.with_name('legacy.json')
    different.write_text(json.dumps(dict(profiles=dict(original=dict(PROFILE,requestedApiIdentifier='legacy-model')))))
    place=Place(share,[homes[0].home],profiles_path=different,log=lambda _:None)
    place.agent_registry,place.campaign_roster=registry,roster
    monkeypatch.setattr(Deployment,'client',lambda dep: dep.model)
    assert place.client_for(homes[0])=='legacy-model'
    save(roster,model='original')
    assert place.client_for(place.campaign_minds('one')[0])=='original'
    assert place.client_for(place.campaign_minds('two')[0])=='legacy-model'


def test_new_campaign_starts_empty_while_existing_assignments_are_kept(setup, monkeypatch):
    from aether.atelier import atelier_http
    share, homes, profiles, registry, roster = setup
    state=dict(atelier_root=str(share), campaign_roster=roster)
    cid=atelier_http.open_commission(state,dict(text='A new campaign.'))['commission_id']
    assert roster.view(cid)['members']==[] and roster.effective(cid,homes)==[]
    assert atelier_http.wake(state,dict(commission=cid,participant=homes[0].record()))[0]==409
    assert len(roster.view('one')['members'])==1
    save(roster,cid,aid='extra',role='lead',model='fast')
    roster.initialize_new(cid)  # repeated initialization never replaces a saved choice
    assert [(m.id,m.kind,m.deployment) for m in roster.effective(cid,homes)]==[('extra','lead','fast')]


def test_http_assignment_is_local_only_and_never_runs(setup):
    from io import BytesIO
    from types import SimpleNamespace
    from aether.atelier.campaign_roster_http import handle_campaign_roster
    _, _, _, _, roster = setup
    class Handler:
        path='/api/campaign-roster'
        server=SimpleNamespace(server_port=8123)
        def settings_send(self, value, code=200, **kwargs): self.value,self.code=value,code
    h=Handler()
    body=json.dumps(dict(commission='one',revision=roster.view('one')['revision'],agent_id='writer',role='lead',model_profile='fast')).encode()
    h.headers={'Host':'127.0.0.1:8123','Origin':'http://evil.invalid','Content-Length':str(len(body)),'Content-Type':'application/json'}
    h.rfile=BytesIO(body)
    assert handle_campaign_roster(h,'POST',dict(campaign_roster=roster)) and h.code==403
    from email.message import Message
    headers=Message()
    for key,value in h.headers.items(): headers[key]=value
    h.headers=headers
    h.headers.replace_header('Origin','http://127.0.0.1:8123')
    handle_campaign_roster(h,'POST',dict(campaign_roster=roster))
    assert h.code==200 and h.value['members'][0]['role']=='lead'
