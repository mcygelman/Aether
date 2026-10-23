"""P1: the context guard inside one Atelier turn (Fable, 2026-10-22), offline with a scripted client.

Between turns the context is rebuilt from the daybook; inside one turn up to 40 rounds of 24,000-character results
were unmanaged. The guard counts the request before each call against the deployment's window less the output
reserve and a margin. When the next round would not fit: results from earlier rounds are set aside as stubs, the
result just asked for is cut to what fits with a marker, and only when neither makes room does the turn end with
the mind's note and what it would continue with. With no catalogued window it counts and does nothing.
"""
from __future__ import annotations

import json
import pytest

from aether.atelier import Participant
from aether.atelier.turn import CONTEXT_MARGIN, SET_ASIDE_KEEP, Tools, ContextCapacityError, context_room, request_estimate, think, run_turn


class Scripted:
    """A model client that answers from a list and records every request it was sent."""

    def __init__(self, replies, *, context_window=0, max_tokens=500, prompt_tokens=None):
        self.replies, self.requests = list(replies), []
        self.context_window, self.max_tokens, self.prompt_tokens = context_window, max_tokens, prompt_tokens

    def chat(self, messages, tools=None):
        self.requests.append(dict(messages=[dict(m) for m in messages], tools=tools))
        reply = dict(self.replies.pop(0)) if self.replies else dict(role='assistant', content='(nothing to add)')
        if self.prompt_tokens is not None:
            reply['_usage'] = dict(prompt_tokens=self.prompt_tokens(messages), completion_tokens=20)
        return reply


def read_call(paths, call_id='c1'):
    return dict(role='assistant', content=None,
                tool_calls=[dict(id=call_id, type='function', function=dict(name='read_files', arguments=json.dumps(dict(paths=paths))))])


def _mind(tmp_path):
    root = tmp_path / 'work'
    root.mkdir()
    (root / 'big.txt').write_text('x' * 12_000, encoding='utf-8')          # about 3,000 tokens
    (root / 'small.txt').write_text('y' * 400, encoding='utf-8')
    p = Participant.author(tmp_path / 'home', id='reader', kind='member', name='Reader', system='You read.', mailboxes=tmp_path / 'share')
    return p, root


def _messages(wake_chars=60):
    return [dict(role='system', content='rules ' * 50), dict(role='user', content='THE COMMISSION: read big.txt and small.txt, then report. ' + 'w' * wake_chars)]


def _tool_messages(request):
    return [m for m in request['messages'] if m['role'] == 'tool']


def test_room_is_the_window_less_reserve_and_margin():
    client = Scripted([], context_window=4000, max_tokens=500)
    assert context_room(client) == dict(window=4000, reserve=500, room=4000 - 500 - int(4000 * CONTEXT_MARGIN))
    assert context_room(Scripted([])) is None                              # no catalogued window: no guard
    assert request_estimate([dict(role='user', content='a' * 400)], None) == 101 + 4
    assert request_estimate([dict(role='user', content='a' * 400)], None, known=(1, 777)) == 777


def test_no_window_means_the_guard_counts_and_does_nothing(tmp_path):
    p, root = _mind(tmp_path)
    client = Scripted([read_call(['big.txt']), read_call(['big.txt'], 'c2'), dict(role='assistant', content='Report: read it twice.')])
    text, cost = think(client, _messages(), Tools(p, root))
    assert text == 'Report: read it twice.' and cost['model_calls'] == 3
    assert cost['context'] == dict(window=None, room=None, peak_estimate=cost['context']['peak_estimate'], guard_fired=False, made_room=[])
    assert cost['context']['peak_estimate'] > 6000                            # it counted, and both reads stayed whole
    assert all(len(m['content']) >= 12_000 for m in _tool_messages(client.requests[-1]))   # whole, plus the tool's own header


def test_a_large_window_never_fires(tmp_path):
    p, root = _mind(tmp_path)
    client = Scripted([read_call(['big.txt']), dict(role='assistant', content='Report: fine.')], context_window=131_072, max_tokens=8192)
    text, cost = think(client, _messages(), Tools(p, root))
    assert text == 'Report: fine.' and cost['context']['guard_fired'] is False and cost['context']['made_room'] == []
    assert cost['context']['room'] == 131_072 - 8192 - int(131_072 * CONTEXT_MARGIN)


def test_the_newest_result_is_cut_to_fit_and_set_aside_once_read(tmp_path):
    p, root = _mind(tmp_path)
    # Wake and schemas plus big.txt exceed the margin-adjusted room on round 2.
    client = Scripted([read_call(['big.txt']), read_call(['small.txt'], 'c2'), dict(role='assistant', content='Report: both read.')],
                      context_window=5000, max_tokens=500)
    text, cost = think(client, _messages(), Tools(p, root))
    assert text == 'Report: both read.' and cost['model_calls'] == 3 and cost['context']['guard_fired'] is False
    made = cost['context']['made_room']
    assert [m['kind'] for m in made] == ['cut', 'set_aside'] and all(m['tool_call_id'] == 'c1' for m in made)
    # Round 2: the result the mind asked for, cut to what fits, with the marker; the mind kept its tools.
    second = client.requests[1]
    (cut,) = _tool_messages(second)
    assert cut['content'].startswith('=== big.txt ===\nxxxx') and '[cut here at ' in cut['content'] and 're-read next turn from there' in cut['content']
    assert SET_ASIDE_KEEP < made[0]['kept'] < 12_000 and second['tools'] is not None
    assert request_estimate(second['messages'], second['tools']) <= cost['context']['room']
    assert not any(k.startswith('_') for m in second['messages'] for k in m)   # host marks never leave the host
    # Round 3: the mind has read it; it is a stub now, and small.txt stays whole.
    third = client.requests[2]
    stub, small = _tool_messages(third)
    assert stub['content'].startswith(f"[{made[0]['characters']} characters set aside to make room in your context") and 'xxxx' in stub['content']
    assert made[1]['characters'] == made[0]['characters'] >= 12_000 and len(small['content']) >= 400
    assert request_estimate(third['messages'], third['tools']) <= cost['context']['room']


def test_the_turn_ends_with_the_note_when_room_cannot_be_made(tmp_path):
    p, root = _mind(tmp_path)
    # The first request fits; the assistant's long reply leaves room only for a note.
    first = read_call(['small.txt'])
    first['content'] = 'reasoning ' * 1100
    client = Scripted([first, dict(role='assistant', content='Report so far: small.txt is the index; next turn, read big.txt.')],
                      context_window=5000, max_tokens=500)
    text, cost = think(client, _messages(), Tools(p, root))
    assert text.startswith('Report so far') and cost['model_calls'] == 2
    guard = cost['context']
    assert guard['guard_fired'] is True and guard['fired_at_call'] == 2 and guard['room'] == context_room(client)['room']
    final = client.requests[-1]
    assert final['tools'] is None                                               # the note is asked for without tools
    asked = final['messages'][-1]['content']
    assert asked.startswith('Your context is nearly full (about ') and 'what you would continue with next turn' in asked
    assert 'starts fresh from the daybook' in asked
    assert request_estimate(final['messages'], None) <= guard['room']          # the note call itself fits


def test_the_providers_own_count_corrects_the_estimate(tmp_path):
    p, root = _mind(tmp_path)
    # A tokenizer that spends twice the estimate: the provider's prompt_tokens say so after each call, and the guard
    # believes the provider for the messages it counted, estimating only what came after.
    def heavy(messages):
        return sum(len(m.get('content') or '') // 2 for m in messages)
    client = Scripted([read_call(['small.txt']), read_call(['small.txt'], 'c2'), dict(role='assistant', content='Report: small twice.')],
                      context_window=5000, max_tokens=500, prompt_tokens=heavy)
    text, cost = think(client, _messages(), Tools(p, root))
    assert text == 'Report: small twice.' and cost['prompt_tokens'] == sum(heavy(r['messages']) for r in client.requests)
    assert cost['context']['peak_estimate'] >= heavy(client.requests[1]['messages'])


class Strict(Scripted):
    """Reject oversized requests at the provider boundary, including note-only calls."""
    factor = 1
    def chat(self, messages, tools=None):
        assert request_estimate(messages, tools) * self.factor <= context_room(self)['room']
        return super().chat(messages, tools)


def test_oversized_wake_never_reaches_provider(tmp_path):
    p, root = _mind(tmp_path)
    client = Strict([], context_window=5000, max_tokens=500)
    with pytest.raises(ContextCapacityError) as err:
        think(client, _messages(wake_chars=24000), Tools(p, root))
    assert client.requests == [] and err.value.cost['model_calls'] == 0
    assert err.value.transcript[1]['content'] == _messages(wake_chars=24000)[1]['content']


def test_first_tool_request_can_fall_back_to_fitting_note(tmp_path):
    p, root = _mind(tmp_path)
    client = Strict([dict(role='assistant', content='Next turn: read big.txt.')], context_window=5000, max_tokens=500)
    text, cost = think(client, _messages(wake_chars=12000), Tools(p, root))
    assert text.startswith('Next turn') and cost['model_calls'] == 1
    assert client.requests[0]['tools'] is None


@pytest.mark.parametrize('max_calls', [2, None])
def test_final_note_also_compacts_at_call_or_round_limit(tmp_path, monkeypatch, max_calls):
    import aether.atelier.turn as turn
    monkeypatch.setattr(turn, 'MAX_TOOL_ROUNDS', 1)
    p, root = _mind(tmp_path)
    (root / 'big.txt').write_text('x' * 22000, encoding='utf-8')
    client = Strict([read_call(['big.txt']), dict(role='assistant', content='Next: continue reading.')], context_window=5000, max_tokens=500)
    text, cost = think(client, _messages(), Tools(p, root), max_calls=max_calls)
    assert cost['model_calls'] == 2 and text.startswith('Next:')
    assert client.requests[-1]['tools'] is None
    assert cost['context']['made_room']


def test_irreducible_final_request_retains_full_work_and_cost(tmp_path):
    p, root = _mind(tmp_path)
    reply = read_call(['big.txt']); reply['content'] = 'reasoning ' * 2500
    client = Strict([reply], context_window=5000, max_tokens=500, prompt_tokens=lambda m: 1000)
    with pytest.raises(ContextCapacityError) as err:
        think(client, _messages(), Tools(p, root), max_calls=2)
    assert len(client.requests) == 1
    assert err.value.cost['model_calls'] == 1 and err.value.cost['prompt_tokens'] == 1000
    assert any(m.get('content', '').endswith('x' * 12000) for m in err.value.transcript if m['role'] == 'tool')


def test_provider_calibration_survives_compaction(tmp_path):
    p, root = _mind(tmp_path)
    class Heavy(Strict):
        factor = 2
        def chat(self, messages, tools=None):
            reply = super().chat(messages, tools)
            reply['_usage'] = dict(prompt_tokens=2 * request_estimate(messages, tools), completion_tokens=10)
            return reply
    client = Heavy([read_call(['big.txt']), dict(role='assistant', content='Read the first part.')], context_window=5000, max_tokens=500)
    text, cost = think(client, _messages(), Tools(p, root))
    assert text == 'Read the first part.' and cost['context']['made_room']


def test_zero_input_room_sends_nothing(tmp_path):
    p, root = _mind(tmp_path)
    client = Strict([], context_window=500, max_tokens=500)
    with pytest.raises(ContextCapacityError):
        think(client, _messages(), Tools(p, root), max_calls=1)
    assert client.requests == []


def test_capacity_failure_is_host_evidence_not_closure_and_retry_can_work(tmp_path, monkeypatch):
    from aether.atelier import Atelier, Budget
    from aether.mailbox import Mailbox
    from aether.atelier.postmaster import Postmaster
    import aether.atelier.turn as turn
    p, root = _mind(tmp_path)
    share = tmp_path / 'share'
    atelier = Atelier(share, 'capacity-check', budget=Budget(model_calls=10))
    atelier.open(dict(id='operator',kind='operator',name='Operator'), 'Read and report.')
    box = Mailbox(share,p.record())
    post = Postmaster(atelier, share); post.exchange({p.id:p.kind}, {p.id:p.name})
    original = box.inbox(commission=atelier.campaign_id)
    assert original
    monkeypatch.setattr(turn, 'wake_messages', lambda *a: _messages())
    first = read_call(['big.txt']); first['content'] = 'reasoning ' * 2500
    client = Strict([first], context_window=5000, max_tokens=500)
    with pytest.raises(ContextCapacityError) as err:
        run_turn(p, atelier, box, client, root)
    assert err.value.recorded
    assert box.inbox(commission=atelier.campaign_id) == original
    failure = [n for n in atelier.daybook.read() if n['kind']=='turn_failed'][-1]
    assert failure['sender']['kind']=='host' and failure['cost']['model_calls']==1
    saved = failure['facts']['evidence']['transcript']
    transcript=json.loads(atelier.daybook.read_file(saved['sha256']))
    assert any(m['role']=='tool' and 'x'*12000 in m['content'] for m in transcript)
    assert not atelier.facts()['closures']
    # No acknowledgment or clearance is required: a fitting deployment runs the same inbox.
    ready=Strict([dict(role='assistant',content='I can continue.')], context_window=131072,max_tokens=500)
    assert run_turn(p,atelier,box,ready,root)


def _place_capacity(tmp_path, *, oversized=True, client=None):
    from aether.atelier import Atelier, Budget
    from aether.atelier.place import Place
    from aether.mailbox import Mailbox
    share = tmp_path / 'share'
    p = Participant.author(tmp_path / 'home', id='reader', kind='member', name='Reader',
                           system='You read. ' * (3000 if oversized else 1), mailboxes=share)
    atelier = Atelier(share, 'capacity-place', budget=Budget(model_calls=10))
    atelier.open(dict(id='operator', kind='operator', name='Operator'), 'Read big.txt and report.')
    box = Mailbox(share, p.record())
    root = share / 'work' / atelier.campaign_id
    root.mkdir(parents=True)
    (root / 'big.txt').write_text('x' * 12000, encoding='utf-8')
    client = client or Strict([dict(role='assistant', content='Report: I can continue.')], context_window=5000, max_tokens=500)
    logs = []
    place = Place(share, [p.home], name='capacity-test', client=client, log=logs.append)
    return place, atelier, box, client, logs


def test_place_suppresses_unchanged_wake_without_growing_inbox_or_refusals(tmp_path):
    place, atelier, box, client, logs = _place_capacity(tmp_path)
    assert len(place.round()['failed']) == 1
    original = box.inbox(commission=atelier.campaign_id)
    failures = [r for r in atelier.daybook.read() if r['kind'] == 'turn_failed']
    assert len(failures) == 1 and failures[0]['to'] is None
    assert failures[0]['facts']['participant']['id'] == 'reader'
    stored_files = list((atelier.daybook.root / 'files').rglob('*'))
    for _ in range(2):
        report = place.round()
        assert not report['turns'] and not report['failed']
        assert report['skipped'][0]['reason'] == 'context_capacity_unchanged'
    atelier.note('Host bookkeeping must not rearm the same impossible request.')
    assert place.round()['skipped']
    assert box.inbox(commission=atelier.campaign_id) == original and len(original) == 1
    assert [r for r in atelier.daybook.read() if r['kind'] == 'turn_failed'] == failures
    assert list((atelier.daybook.root / 'files').rglob('*')) == stored_files
    assert client.requests == []
    assert atelier.numbers()['capacity_failures'] == 1 and atelier.numbers()['refusals_per_hundred'] == 0
    assert atelier.cost()['model_calls'] == 0 and not atelier.facts()['closures']
    assert sum('wake cannot fit;' in line for line in logs) == 1


@pytest.mark.parametrize('sender_kind', ['operator', 'member'])
def test_new_nonhost_note_retries_once_without_clearance(tmp_path, sender_kind):
    place, atelier, box, client, logs = _place_capacity(tmp_path)
    place.round()
    atelier.post(dict(id='new-speaker', kind=sender_kind, name='Colleague'), 'Here is additional context.')
    assert len(place.round()['failed']) == 1     # changed input is tried even if still too large
    assert place.round()['skipped']
    assert atelier.numbers()['capacity_failures'] == 2 and client.requests == []
    assert sum('retrying automatically' in line for line in logs) == 1


@pytest.mark.parametrize('change', ['window', 'identity'])
def test_changed_capacity_or_smaller_input_completes_without_new_mail(tmp_path, change):
    place, atelier, box, client, logs = _place_capacity(tmp_path)
    place.round()
    if change == 'window':
        client.context_window = 131072
    else:
        (place.minds[0].home / 'identity/System_0.txt').write_text('You read.', encoding='utf-8')
    report = place.round()
    assert len(report['turns']) == 1 and not report['failed']
    assert len(client.requests) == 1 and not box.inbox(commission=atelier.campaign_id)
    assert atelier.numbers()['capacity_failures'] == 1 and atelier.numbers()['refusals_per_hundred'] == 0
    assert not atelier.facts()['closures']


def test_owner_memory_change_retries_without_a_memory_gate(tmp_path):
    from aether.atelier import mind_memory
    place, atelier, box, client, logs = _place_capacity(tmp_path)
    place.round()
    with mind_memory._open_write(place.minds[0].home, 'owner-edit') as engine:
        engine.remember('New context', 'The owner changed their memory.', 'project_fact', ['context'])
    assert len(place.round()['failed']) == 1
    assert place.round()['skipped']
    assert atelier.numbers()['capacity_failures'] == 2


def test_same_profile_edit_reloads_client_and_resumes(tmp_path, monkeypatch):
    from aether.atelier.turn import Deployment
    place, atelier, box, _, logs = _place_capacity(tmp_path)
    place.client = None
    place.profiles_path = tmp_path / 'profiles.json'
    profiles = dict(profiles=dict(research=dict(model='offline', endpoint='http://offline.invalid',
                                               contextWindow=5000, maxOutputTokens=500)))
    place.profiles_path.write_text(json.dumps(profiles), encoding='utf-8')
    made = []
    def build(deployment):
        client = Strict([dict(role='assistant', content='Report: resumed.')],
                        context_window=deployment.context_window, max_tokens=deployment.max_tokens)
        made.append(client)
        return client
    monkeypatch.setattr(Deployment, 'client', build)
    assert place.round()['failed']
    assert place.round()['skipped'] and len(made) == 1
    profiles['profiles']['research']['contextWindow'] = 131072
    place.profiles_path.write_text(json.dumps(profiles), encoding='utf-8')
    assert place.round()['turns'] and len(made) == 2
    assert not made[0].requests and len(made[1].requests) == 1
    assert atelier.numbers()['capacity_failures'] == 1


def test_retry_bookkeeping_failure_cannot_block_recovery(tmp_path, monkeypatch):
    place, atelier, box, client, logs = _place_capacity(tmp_path)
    place.round()
    client.context_window = 131072
    def unavailable(*args):
        raise OSError('retry snapshot temporarily unavailable')
    monkeypatch.setattr(place, '_capacity_inputs', unavailable)
    report = place.round()
    assert len(report['turns']) == 1 and not report['failed'] and len(client.requests) == 1
    assert not box.inbox(commission=atelier.campaign_id)


def test_late_capacity_failure_recovers_on_fresh_turn_same_inputs_and_deployment(tmp_path):
    first = read_call(['big.txt']); first['content'] = 'reasoning ' * 2500
    first['tool_calls'].append(dict(id='write', type='function', function=dict(name='write_file',
        arguments=json.dumps(dict(path='retained.txt', content='Work already done.')))))
    client = Strict([first, dict(role='assistant', content='Report: fresh context fits.')], context_window=5000, max_tokens=500)
    place, atelier, box, client, logs = _place_capacity(tmp_path, oversized=False, client=client)
    assert place.round()['failed'] and len(client.requests) == 1
    failure = [r for r in atelier.daybook.read() if r['kind'] == 'turn_failed'][0]
    transcript = json.loads(atelier.daybook.read_file(failure['facts']['evidence']['transcript']['sha256']))
    assert any(m['role'] == 'tool' and 'x' * 12000 in m['content'] for m in transcript)
    assert failure['facts']['evidence']['actions'] and failure['cost']['model_calls'] == 1
    retained = next(f for f in failure['files'] if f['name'] == 'retained.txt')
    assert atelier.daybook.read_file(retained['sha256']) == b'Work already done.'
    report = place.round()
    assert len(report['turns']) == 1 and not report['failed'] and len(client.requests) == 2
    assert not box.inbox(commission=atelier.campaign_id) and not atelier.facts()['closures']
    assert atelier.numbers()['capacity_failures'] == 1 and atelier.numbers()['refusals_per_hundred'] == 0


def test_only_capacity_failures_are_record_not_mail_including_old_rows(tmp_path):
    from aether.atelier import Atelier
    from aether.atelier.atelier import HOST
    from aether.atelier.postmaster import Postmaster
    from aether.mailbox import Mailbox
    atelier = Atelier(tmp_path, 'metrics')
    assert atelier.numbers()['capacity_failures'] == 0
    p = dict(id='reader', kind='member', name='Reader')
    box = Mailbox(tmp_path, p)
    other = Mailbox(tmp_path, dict(id='other', kind='member'))
    atelier.daybook.append(HOST, 'Legacy capacity record', kind='turn_failed', to=p,
                           facts=dict(evidence=dict(context=dict(window=5000))))
    atelier.turn_failed(p, 'New capacity record', dict(context=dict(window=5000)))
    ordinary = atelier.turn_failed(p, 'Ordinary recoverable error', dict(trace='offline'))
    Postmaster(atelier, tmp_path).deliver({'reader': 'member', 'other': 'member'})
    assert [n['id'] for n in box.inbox(commission='metrics')] == [ordinary['id']]
    assert not other.inbox(commission='metrics')
    assert atelier.numbers()['capacity_failures'] == 2
    assert atelier.numbers()['refusals_per_hundred'] == 100


def test_twenty_percent_margin_absorbs_measured_first_request_undercount(tmp_path):
    import math
    p, root = _mind(tmp_path)
    class Measured(Scripted):
        def chat(self, messages, tools=None):
            actual = math.ceil(request_estimate(messages, tools) * 1.31)
            assert actual + self.max_tokens <= self.context_window
            reply = super().chat(messages, tools)
            reply['_usage'] = dict(prompt_tokens=actual, completion_tokens=10)
            return reply
    client = Measured([dict(role='assistant', content='Fits with output reserved.')], context_window=6000, max_tokens=1024)
    room = context_room(client)['room']
    assert room == 3776 and CONTEXT_MARGIN == 0.20
    # Exercise an uncalibrated request close to the new boundary, with tool schemas.
    from aether.atelier.turn import tool_schemas
    messages = _messages()
    messages[1]['content'] += 'x' * (4 * (room - request_estimate(messages, tool_schemas()) - 1))
    assert room - 2 <= request_estimate(messages, tool_schemas()) <= room
    text, cost = think(client, messages, Tools(p, root))
    assert text == 'Fits with output reserved.' and cost['model_calls'] == 1
