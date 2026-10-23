"""A working mind keeps its client when its deployment profile cannot be read (Fable, 2026-10-22 night).

The place re-reads runtime_profiles.json each pass so that an edit to the same profile takes effect without a
restart. A profile renamed under a working mind, or a file caught half-written, must not turn that mind into a
failure every cycle with mail: the client already built keeps working, the place says so once, and the reload
resumes when the file is readable again. A mind that never had a client still fails plainly.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from aether.atelier import Atelier, Budget, Participant
from aether.atelier.place import Place
from aether.atelier.turn import Deployment
from aether.mailbox import Mailbox
from tests.test_atelier_context_guard_offline import Strict

OPERATOR = dict(id='operator', kind='operator', name='Operator')
PROFILE = dict(model='offline', endpoint='http://offline.invalid', contextWindow=131072, maxOutputTokens=500)


def _place(tmp_path, monkeypatch, deployment='research'):
    share = tmp_path / 'share'
    mind = Participant.author(tmp_path / 'home', id='writer', kind='member', name='Writer', system='You write.',
                              mailboxes=share, deployment=deployment)
    atelier = Atelier(share, 'profile-reload', budget=Budget(model_calls=10))
    atelier.open(OPERATOR, 'Say hello.')
    (share / 'work' / atelier.campaign_id).mkdir(parents=True)
    profiles_path = tmp_path / 'profiles.json'
    profiles_path.write_text(json.dumps(dict(profiles=dict(research=PROFILE))), encoding='utf-8')
    made = []

    def build(deployment):
        client = Strict([dict(role='assistant', content='Hello.')] * 6, context_window=deployment.context_window,
                        max_tokens=deployment.max_tokens)
        made.append(client)
        return client
    monkeypatch.setattr(Deployment, 'client', build)
    logs = []
    place = Place(share, [mind.home], name='profile-test', profiles_path=profiles_path, log=logs.append)
    return place, atelier, Mailbox(share, mind.record()), profiles_path, made, logs


def _failures(atelier):
    return [r for r in atelier.daybook.read() if r['kind'] == 'turn_failed']


@pytest.mark.parametrize('damage', ['renamed', 'half_written', 'missing'])
def test_a_working_mind_keeps_its_client_while_its_profile_cannot_be_read(tmp_path, monkeypatch, damage):
    place, atelier, box, profiles_path, made, logs = _place(tmp_path, monkeypatch)
    assert len(place.round()['turns']) == 1 and len(made) == 1
    if damage == 'renamed':
        profiles_path.write_text(json.dumps(dict(profiles=dict(research_v2=PROFILE))), encoding='utf-8')
    elif damage == 'half_written':
        profiles_path.write_text('{"profiles": {"research": {"model": "off', encoding='utf-8')
    else:
        profiles_path.unlink()
    for _ in range(2):
        atelier.post(OPERATOR, 'Say it again.')
        report = place.round()
        assert len(report['turns']) == 1 and not report['failed']          # the cached client kept working
    assert len(made) == 1 and not _failures(atelier)
    assert not box.inbox(commission=atelier.campaign_id)                   # nothing was mailed to the mind
    assert sum('cannot be reloaded right now' in line for line in logs) == 1     # said once, not every pass
    profiles_path.write_text(json.dumps(dict(profiles=dict(research=dict(PROFILE, contextWindow=65536)))), encoding='utf-8')
    atelier.post(OPERATOR, 'And once more.')
    assert len(place.round()['turns']) == 1
    assert len(made) == 2 and made[1].context_window == 65536                # the same-profile reload resumed
    assert sum('usable again' in line for line in logs) == 1


@pytest.mark.parametrize('damage', ['invalid_number', 'missing_endpoint', 'client_construction'])
def test_failed_candidate_keeps_client_and_profile_until_a_successful_reload(tmp_path, monkeypatch, damage):
    place, atelier, box, profiles_path, made, logs = _place(tmp_path, monkeypatch)
    assert len(place.round()['turns']) == 1
    cached = made[0]
    candidate = dict(PROFILE, contextWindow=65536)
    if damage == 'invalid_number':
        candidate['maxOutputTokens'] = 'temporarily incomplete'
    elif damage == 'missing_endpoint':
        candidate.pop('endpoint')
    else:
        build = Deployment.client
        def broken(deployment):
            if deployment.context_window == 65536:
                raise ValueError('candidate cannot be constructed')
            return build(deployment)
        monkeypatch.setattr(Deployment, 'client', broken)
    profiles_path.write_text(json.dumps(dict(profiles=dict(research=candidate))), encoding='utf-8')
    for _ in range(2):
        atelier.post(OPERATOR, 'Continue working.')
        report = place.round()
        assert len(report['turns']) == 1 and not report['failed']
        assert place._clients['research'] is cached
        assert place._client_profiles['research'] == PROFILE
    assert not _failures(atelier) and not box.inbox(commission=atelier.campaign_id)
    assert len(made) == 1 and len(cached.requests) == 3
    assert sum('cannot be reloaded right now' in line for line in logs) == 1
    assert not any('usable again' in line for line in logs)
    profiles_path.write_text(json.dumps(dict(profiles=dict(research=dict(PROFILE, contextWindow=32768)))), encoding='utf-8')
    atelier.post(OPERATOR, 'Continue with the updated settings.')
    report = place.round()
    assert len(report['turns']) == 1 and not report['failed']
    assert len(made) == 2 and made[1].context_window == 32768
    assert place._clients['research'] is made[1] and place._client_profiles['research']['contextWindow'] == 32768
    assert sum('usable again' in line for line in logs) == 1


def test_profile_changed_after_read_cannot_mix_or_invalidate_the_snapshot(tmp_path, monkeypatch):
    place, atelier, box, profiles_path, made, logs = _place(tmp_path, monkeypatch)
    place.round()
    profiles_path.write_text(json.dumps(dict(profiles=dict(research=dict(PROFILE, contextWindow=65536)))), encoding='utf-8')
    read_text = Path.read_text
    reads = []
    def edited_after_read(path, *args, **kwargs):
        text = read_text(path, *args, **kwargs)
        if path == profiles_path:
            reads.append(text)
            if len(reads) == 1:
                path.write_text('{"profiles":', encoding='utf-8')
        return text
    monkeypatch.setattr(Path, 'read_text', edited_after_read)
    atelier.post(OPERATOR, 'Use the changed profile.')
    report = place.round()
    assert len(report['turns']) == 1 and not report['failed']
    assert len(reads) == 1 and len(made) == 2 and made[1].context_window == 65536
    for _ in range(2):
        atelier.post(OPERATOR, 'Continue while the file is being edited.')
        report = place.round()
        assert len(report['turns']) == 1 and not report['failed']
    assert len(reads) == 3 and len(made) == 2
    assert not _failures(atelier) and not box.inbox(commission=atelier.campaign_id)
    assert sum('cannot be reloaded right now' in line for line in logs) == 1
    profiles_path.write_text(json.dumps(dict(profiles=dict(research=dict(PROFILE, contextWindow=32768)))), encoding='utf-8')
    atelier.post(OPERATOR, 'Continue after the edit.')
    assert len(place.round()['turns']) == 1
    assert len(reads) == 4 and len(made) == 3 and made[2].context_window == 32768
    assert sum('usable again' in line for line in logs) == 1


@pytest.mark.parametrize('initial_error', ['missing_profile', 'invalid_number'])
def test_a_mind_that_never_had_a_client_still_fails_plainly(tmp_path, monkeypatch, initial_error):
    deployment = 'nowhere' if initial_error == 'missing_profile' else 'research'
    place, atelier, box, profiles_path, made, logs = _place(tmp_path, monkeypatch, deployment=deployment)
    if initial_error == 'invalid_number':
        profiles_path.write_text(json.dumps(dict(profiles=dict(research=dict(PROFILE, maxOutputTokens='invalid')))), encoding='utf-8')
    report = place.round()
    expected = 'nowhere' if initial_error == 'missing_profile' else 'invalid literal'
    assert not report['turns'] and len(report['failed']) == 1 and expected in report['failed'][0]['error']
    assert not made and len(_failures(atelier)) == 1
    assert not place._clients and not place._client_profiles
