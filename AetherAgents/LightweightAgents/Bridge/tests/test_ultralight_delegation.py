"""Verify bounded child dispatch and shared turn accounting; never contact live providers or resident memories."""
import json
from types import SimpleNamespace

import pytest

from aether.agent.microcase import parse
from aether.atelier.ultralight_delegation import Delegation, configured, _slots
from aether.atelier.turn import Tools, think, Deployment

ARGS = dict(role='checker', task='Check the total.', evidence='ledger: two plus two equals four.')


class Child:
    def __init__(self, mode='ok'):
        self.mode, self.requests = mode, []

    def chat(self, messages, tools=None):
        self.requests.append((messages, tools))
        assert tools is None and len(messages) == 2
        if self.mode == 'error':
            raise RuntimeError('secret-provider-key')
        mc = parse(messages[0]['content'].split('=== END MICROCASE ===')[0] + '=== END MICROCASE ===\n')
        text = f'AGENT: {mc.header.agent}\nSEAL: {mc.header.seal}\nSTATUS: COMPLETE\n[RESULT] Consistent.\n[SOURCE] ledger'
        if self.mode == 'seal':
            text = 'No acknowledgement'
        if self.mode == 'large':
            text += 'x' * 9000
        return dict(content=text, tool_calls=[dict(function=dict(name='write_file'))] if self.mode == 'tools' else [],
                    _usage=dict(prompt_tokens=31, completion_tokens=7))


class Parent:
    def __init__(self, tool_count=1):
        self.requests, self.tool_count = [], tool_count

    def chat(self, messages, tools=None):
        self.requests.append((messages, tools))
        if len(self.requests) == 1 and tools:
            return dict(content='', tool_calls=[dict(id=str(i), function=dict(name='delegate_ultralight', arguments=json.dumps(ARGS)))
                                               for i in range(self.tool_count)], _usage=dict(prompt_tokens=10, completion_tokens=2))
        return dict(content='Reviewed the helper result.', _usage=dict(prompt_tokens=10, completion_tokens=2))


@pytest.mark.parametrize('kind', ['lead', 'member'])
def test_shared_parent_runner_budget_and_isolation(tmp_path, kind):
    child = Child()
    service = Delegation(lambda: child)
    tools = Tools(SimpleNamespace(kind=kind, technical=lambda: False), tmp_path, delegation=service)
    parent = Parent(4)
    text, cost = think(parent, [dict(role='system', content='PRIVATE PARENT IDENTITY')], tools, max_calls=4)
    assert text == 'Reviewed the helper result.'
    assert cost['model_calls'] == 4 and cost['prompt_tokens'] == 82 and cost['completion_tokens'] == 18
    assert len(child.requests) == 2 and len(parent.requests) == 2
    assert [r['model_calls'] for r in cost['ultralights']] == [1, 1, 0, 0]
    assert 'PRIVATE PARENT' not in json.dumps(child.requests)
    assert not list(tmp_path.iterdir()) and not tools.written
    assert all(r['accepted'] is False for r in cost['ultralights'])


def test_reserves_final_parent_reply(tmp_path):
    child = Child()
    tools = Tools(SimpleNamespace(kind='member', technical=lambda: False), tmp_path, delegation=Delegation(lambda: child))
    _, cost = think(Parent(), [], tools, max_calls=2)
    assert cost['model_calls'] == 2 and not child.requests


@pytest.mark.parametrize('mode,status', [('ok', 'COMPLETE'), ('seal', 'BLOCKED'), ('tools', 'BLOCKED'), ('error', 'BLOCKED'), ('large', 'PARTIAL')])
def test_response_boundaries(mode, status):
    service = Delegation(lambda: Child(mode))
    result = json.loads(service.call(ARGS, remaining_calls=3))
    assert result['status'] == status and service.usage['model_calls'] == 1
    assert 'secret-provider-key' not in json.dumps(result)
    assert len(result.get('content', '')) <= 8000


def test_input_stop_capacity_and_concurrency():
    child = Child()
    service = Delegation(lambda: child, stopped=lambda: True)
    assert json.loads(service.call(ARGS, remaining_calls=3))['status'] == 'BLOCKED'
    service = Delegation(lambda: child)
    assert json.loads(service.call(dict(ARGS, evidence='x' * 4501), remaining_calls=3))['status'] == 'BLOCKED'
    _slots.acquire(); _slots.acquire()
    try:
        assert 'busy' in service.call(ARGS, remaining_calls=3)
    finally:
        _slots.release(); _slots.release()
    assert not child.requests


def test_late_output_discarded():
    child = Child()
    service = Delegation(lambda: child, stopped=lambda: bool(child.requests))
    answer = json.loads(service.call(ARGS, remaining_calls=3))
    assert answer['status'] == 'BLOCKED' and 'content' not in answer and answer['model_calls'] == 1


def test_profile_opt_in_and_production_bounds(tmp_path, monkeypatch):
    path = tmp_path / 'profiles.json'
    assert configured(path) is None
    path.write_text(json.dumps(dict(profiles=dict(parent=dict(model='expensive', endpoint='http://invalid')))))
    assert configured(path) is None
    path.write_text(json.dumps(dict(profiles=dict(ultralight=dict(model='small', endpoint='http://invalid', max_tokens=9000, timeoutSecs=900)))))
    seen = []
    monkeypatch.setattr(Deployment, 'client', lambda d: seen.append(d) or Child())
    service = configured(path)
    assert json.loads(service.call(ARGS, remaining_calls=3))['status'] == 'COMPLETE'
    assert seen[0].model == 'small' and seen[0].max_tokens == 1024 and seen[0].timeout_secs == 60


def test_no_tool_advertised_when_unconfigured(tmp_path):
    parent = Parent(0)
    think(parent, [], Tools(SimpleNamespace(kind='member', technical=lambda: False), tmp_path), max_calls=2)
    assert not any(s['function']['name'] == 'delegate_ultralight' for s in parent.requests[0][1])


@pytest.mark.parametrize('weight', ['lightweight', 'heavyweight'])
def test_real_turn_receipt_for_both_home_types(tmp_path, monkeypatch, weight):
    from aether.atelier import Atelier, Budget, Participant
    from aether.atelier.agent_registry import metadata
    from aether.atelier.postmaster import Postmaster
    from aether.atelier.turn import run_turn
    from aether.mailbox import Mailbox
    monkeypatch.setenv('AETHER_MINDS_DIR', str(tmp_path/'minds'))
    share = tmp_path/'share'
    mind = Participant.author(tmp_path/'home', id='synthetic-parent', name='Synthetic Parent', kind='member',
                              system='PRIVATE PARENT', mailboxes=share, cartridge=True)
    if weight == 'heavyweight':
        (mind.home/'participant.json').unlink()
        # CampaignRoster.local_agent uses this same adapter for a pointer-only home.
        mind = Participant(mind.id, 'member', mind.name, mind.home)
    assert metadata(str(mind.home))['weight'] == weight
    atelier = Atelier(share, 'helper-check', budget=Budget(model_calls=3))
    atelier.open(dict(id='operator', kind='operator', name='Operator'), 'Check the evidence and summarize.')
    profiles = tmp_path/'profiles.json'
    profiles.write_text(json.dumps(dict(profiles=dict(ultralight=dict(model='small', endpoint='http://invalid')))))
    child = Child()
    monkeypatch.setattr(Deployment, 'client', lambda _: child)
    box = Mailbox(share, mind.record())
    Postmaster(atelier, share).deliver({mind.id: mind.kind}, {mind.id: mind.name})
    work = share/'work/helper-check'; work.mkdir(parents=True)
    sent = run_turn(mind, atelier, box, Parent(), work, profiles_path=profiles)
    record = json.loads(sent.read_text())
    assert record['cost']['model_calls'] == 3
    assert record['cost']['ultralights'][0]['status'] == 'COMPLETE'
    assert len(child.requests) == 1 and 'PRIVATE PARENT' not in json.dumps(child.requests)
