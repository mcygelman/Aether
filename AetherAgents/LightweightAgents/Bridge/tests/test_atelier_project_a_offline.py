"""Project A in the atelier, offline, with scripted participants (the atelier's spine).

What this proves: a campaign can be run as a conversation and nothing else. The operator opens the atelier by talking;
the lead plans by talking; members deliver files; the reviewer says PASS or FAIL in prose; the operator says "go"
once per sprint and "accepted" once; everyone writes a closing message in their own words; the host records it all
with a hash chain, derives the facts, asks when it needs something, meters the budget, withholds a credential, and
reports the three numbers. A restart re-reads the record and finds the same facts. Project B's lead wakes to its own
closing words from Project A.

What it does not prove: that a model did the thinking (participants are scripted here; the turn runner is tested
separately), or that memory was filed into a container (the host records that filing happened; filing is the turn
runner's job). No model, no provider, no key file.

Run: py -3.10 -B -m pytest tests/test_atelier_project_a_offline.py -q -p no:cacheprovider --basetemp build/s/fb-atelier
"""
from __future__ import annotations

import json

import pytest

from aether.atelier import Atelier, AtelierStop, Budget, Daybook, DaybookError, rebuild, questions

KEY = 'AtelierPlantedSyntheticKey7f3a19c2'   # synthetic; must never enter the record
OPERATOR = dict(id='michel', kind='operator', name='Michel')
LEAD = dict(id='harbor-steward', kind='lead', name='Harbor Steward')
GUIDE = dict(id='Harbor Guide', kind='member', name='Harbor Guide')
MINDER = dict(id='Minder', kind='reviewer', name='Minder')

REPORTS_V1 = b'def daily_report(rows, start, end):\n    return [r for r in rows if start <= r["at"] <= end]\n'
REPORTS_V2 = b'def daily_report(rows, start, end):\n    return [r for r in rows if start <= r["at"] < end]\n'


def hosts_questions(atelier, to):
    return [m for m in atelier.daybook.read() if m['kind'] == 'host' and m.get('to', {}).get('id') == to and m['facts'].get('question')]


def test_project_a_as_a_conversation(tmp_path):
    atelier = Atelier(tmp_path, 'campaign-a', secrets=(KEY,))
    # -- The operator opens the atelier by talking. That message is the authorization. --------------------------
    atelier.open(OPERATOR, 'Deliver a daily settlement report function for the Harbor Ledger: half-open UTC window, '
                        'USD only, exact minor units, with tests and an operator guide. Budget: 20 model calls.')
    state = atelier.facts()
    assert state['campaign']['opened_by']['id'] == 'michel' and state['campaign']['budget_model_calls'] == 20
    assert questions(state) == [] and [m['facts']['question']['about'] for m in hosts_questions(atelier, 'lead')] == ['plan']   # asked, once
    # -- The lead plans by talking, and asks for people. ------------------------------------------------------
    atelier.post(LEAD, 'Here is how I want to do this.\n'
                    'Sprint 1: grounding\n- read the conventions and write a contract note (Harbor Guide)\n'
                    'Sprint 2: implementation\n- implement daily_report with tests (Harbor Guide)\n'
                    'Sprint 3: review\n- independent review of the implementation (Minder)\n'
                    'I will hire Harbor Guide as implementer and Minder as reviewer. Budget looks fine.',
              cost=dict(model_calls=1, seconds=20))
    state = atelier.facts()
    assert [s['number'] for s in state['plan']['sprints']] == [1, 2, 3]
    assert state['plan']['sprints'][1]['tasks'][0]['who'] == 'Harbor Guide'
    assert [(h['name'], h['role']) for h in state['roster']] == [('Harbor Guide', 'implementer'), ('Minder', 'reviewer')]
    assert state['plan']['awaiting_approval'] == 1 and hosts_questions(atelier, 'operator')
    # -- One approval for the plan: the operator says "go" once (First Principles review, O1). ---------------
    atelier.post(OPERATOR, 'go')
    state = atelier.facts()
    assert [a['sprint'] for a in state['approvals']] == [1, 2, 3] and state['plan']['awaiting_approval'] is None
    # -- A member works and delivers a file; the host hashes it. ---------------------------------------------
    note = atelier.daybook.store_file('grounding/CONTRACT_NOTE.md', b'# Contract\nHalf-open window [start, end).\n', GUIDE)
    atelier.post(GUIDE, 'Read CONVENTIONS.md. The window is half-open. The deliverable is `grounding/CONTRACT_NOTE.md`.',
              files=[note], cost=dict(model_calls=1, seconds=30))
    v1 = atelier.daybook.store_file('implementation/reports.py', REPORTS_V1, GUIDE)
    atelier.post(GUIDE, 'Implemented. The deliverable is `implementation/reports.py`; tests pass locally.',
              files=[v1], cost=dict(model_calls=2, seconds=55))
    # -- The reviewer says what is wrong, in prose; the member answers with a new version. --------------------
    atelier.post(MINDER, 'Review of reports.py.\n'
                      'requirement 1: validation of rows - PASS, every clause is traced to a line\n'
                      'requirement 2: half-open window - FAIL: the upper bound is inclusive, CONTRACT_NOTE says [start, end)\n',
              cost=dict(model_calls=1, seconds=40))
    v2 = atelier.daybook.store_file('implementation/reports.py', REPORTS_V2, GUIDE)
    atelier.post(GUIDE, 'Agreed, fixed the bound. The deliverable is `implementation/reports.py` (new version).',
              files=[v2], cost=dict(model_calls=1, seconds=25))
    atelier.post(MINDER, 'requirement 2: half-open window - PASS now.', cost=dict(model_calls=1, seconds=15))
    state = atelier.facts()
    assert [(v['subject'], v['verdict']) for v in state['verdicts']] == [
        ('validation of rows', 'PASS'), ('half-open window', 'FAIL'), ('half-open window', 'PASS')]
    assert [d['sha256'] for d in state['deliverables'] if d['name'] == 'implementation/reports.py'] == [v1['sha256'], v2['sha256']]
    assert atelier.daybook.read_file(v2['sha256']) == REPORTS_V2
    assert state['plan']['awaiting_approval'] is None and len(state['approvals']) == 3
    # -- The operator accepts, once. The host asks each hired mind for its closing words. ----------------------
    atelier.post(OPERATOR, 'Accepted. Good work.')
    state = atelier.facts()
    assert state['acceptance']['by']['id'] == 'michel'
    assert {m['to']['id'] for m in hosts_questions(atelier, 'Harbor Guide') + hosts_questions(atelier, 'Minder')} == {'Harbor Guide', 'Minder'}
    atelier.closure(LEAD, 'We delivered the report function with a reviewed half-open window. What I learned: state the '
                       'window convention in the contract note before anyone codes; the reviewer caught it, not me.')
    atelier.closure(GUIDE, 'I implemented daily_report and fixed the bound after review. Carry: check [start, end) first.')
    atelier.closure(MINDER, 'I failed one requirement and passed its correction. Lesson: cite the contract line in every verdict.')
    atelier.filed(LEAD, 'cartridge public-demo-owner', dict(note_ids=['n1']))
    state = atelier.facts()
    assert len(state['closures']) == 3 and state['closures'][0]['filed'] is True and state['closures'][1]['filed'] is False
    assert questions(state) == []                                               # nothing left to ask
    # -- The record is exact; the three numbers come from it. -------------------------------------------------
    assert atelier.daybook.verify()['ok']
    numbers = atelier.numbers()
    assert numbers['approvals_per_sprint'] == 0.33 and numbers['refusals_per_hundred'] == 0.0      # one "go", three sprints
    assert numbers['thinking_seconds'] == 185.0 and numbers['calls'] >= 9
    left = atelier.budget_left(state)
    assert left['model_calls_used'] == 7 and left['model_calls_limit'] == 20
    # -- Restart: a new host on the same root sees the same facts. ----------------------------------------------
    again = Atelier(tmp_path, 'campaign-a')
    assert again.facts() == state and again.daybook.verify()['ok']
    assert json.loads((again.daybook.root / 'facts.json').read_text(encoding='utf-8'))['acceptance'] == state['acceptance']
    # -- Project B: the lead wakes to its own closing words from Project A. ----------------------------------
    b = Atelier(tmp_path, 'campaign-b')
    b.open(OPERATOR, 'Deliver a weekly variant of the settlement report, EUR as well as USD. Budget: 20 model calls.')
    context = b.wake_context(LEAD)
    assert context['prior_closures'][0]['campaign_id'] == 'campaign-a'
    assert 'state the window convention' in context['prior_closures'][0]['text']
    assert [q['about'] for q in context['questions_for_me']] == ['plan'] and context['unread_total'] == 2
    b.mark_read(LEAD)
    assert b.wake_context(LEAD)['unread_total'] == 0


def test_a_credential_is_withheld_and_counted(tmp_path):
    atelier = Atelier(tmp_path, 'campaign-s', secrets=(KEY,))
    atelier.open(OPERATOR, 'Deliver something small. Budget: 5 model calls.')
    with pytest.raises(AtelierStop) as stop:
        atelier.post(LEAD, 'Here is the key you asked for: ' + KEY)
    assert stop.value.reason == 'secrets'
    rows = atelier.daybook.read()
    assert rows[-1]['kind'] == 'withheld' and KEY not in atelier.daybook.path.read_text(encoding='utf-8')
    assert atelier.facts()['withheld'] == 1 and atelier.numbers()['refusals_per_hundred'] > 0
    atelier.post(LEAD, 'Said again without the secret.')                      # the work continues


def test_budget_is_a_stop_for_money_only(tmp_path):
    atelier = Atelier(tmp_path, 'campaign-m', budget=Budget(model_calls=2))
    atelier.open(OPERATOR, 'Deliver something. Budget: 2 model calls.')
    atelier.post(LEAD, 'Sprint 1: one\n- do it (Harbor Guide)', cost=dict(model_calls=1, seconds=5))
    with pytest.raises(AtelierStop) as stop:
        atelier.post(GUIDE, 'Done.', cost=dict(model_calls=1, seconds=5))
    assert stop.value.reason == 'money'
    last = atelier.daybook.read()[-1]
    assert last['kind'] == 'host' and last['facts'] == dict(stop='money')
    assert atelier.daybook.verify()['ok']                                       # the record stayed whole


def test_the_daybook_refuses_nothing_about_shape_and_reports_tampering(tmp_path):
    daybook = Daybook(tmp_path, 'campaign-c')
    first = daybook.append(LEAD, 'anything at all, in any shape {{{ not json')
    daybook.append(GUIDE, '', files=[daybook.store_file('a.txt', b'x', GUIDE)])
    assert daybook.verify()['ok'] and daybook.read(since_id=first['id'])[0]['sender']['id'] == 'Harbor Guide'
    with pytest.raises(DaybookError):
        daybook.store_file('../escape.txt', b'x', GUIDE)
    lines = daybook.path.read_text(encoding='utf-8').splitlines()
    lines[0] = lines[0].replace('anything at all', 'something else')
    daybook.path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    report = daybook.verify()
    assert report['ok'] is False and report['first_problem'] == 'message 1 breaks the chain'   # reported, not blocked
    assert rebuild(daybook)['messages'] == 2
