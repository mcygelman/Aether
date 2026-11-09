"""Mailboxes: a mind anywhere joins the atelier through a directory it can reach.

The master and an assistant never touch the daybook directly. Each has a mailbox (a directory; on a share it would
be another machine). The postmaster delivers the commission letter and every later note to the mailboxes it
addresses, and collects what they wrote back into the daybook through the same `Atelier.post`, so the record, the
facts, the questions, the meter and the four stops are the same for a mind across the network as for one in this
process. A note carrying a credential bounces back to its author with the reason; delivery is idempotent across a
restart. No model, no provider, no key file.

Run: py -3.10 -B -m pytest tests/test_atelier_mailbox_offline.py -q -p no:cacheprovider --basetemp build/s/fb-mailbox
"""
from __future__ import annotations

import pytest

from aether.atelier import Atelier, AtelierStop, Budget, Postmaster
from aether.mailbox import Mailbox

KEY = 'MailboxPlantedSyntheticKey2b7e41d0'
OPERATOR = dict(id='michel', kind='operator', name='Michel')
LEAD = dict(id='harbor-steward', kind='lead', name='Harbor Steward')
GUIDE = dict(id='Harbor Guide', kind='member', name='Harbor Guide')
KINDS = {'harbor-steward': 'lead', 'Harbor Guide': 'member'}


def test_a_commission_runs_through_mailboxes(tmp_path):
    atelier = Atelier(tmp_path, 'commission-x', secrets=(KEY,))
    share = tmp_path / 'share'                       # the mailboxes root: on a share, another machine reads it
    master, guide = Mailbox(share, LEAD), Mailbox(share, GUIDE)
    post = Postmaster(atelier, share)
    atelier.open(OPERATOR, 'Deliver the settlement report function with tests. Budget: 10 model calls.')
    delivered = post.deliver(KINDS)
    # The commission letter is to the whole commission; the atelier's question for a plan is to the master only.
    assert delivered == {'Harbor Guide': 1, 'harbor-steward': 2}
    assert [n['sender']['kind'] for n in master.inbox()] == ['operator', 'host']
    assert master.inbox()[1]['facts']['question']['about'] == 'plan'
    # The master answers from its mailbox; the postmaster carries it into the daybook; the facts follow.
    for note in master.inbox():
        master.mark_read(note['id'])
    master.send('Sprint 1: implementation\n- implement daily_report with tests (Harbor Guide)\nI will hire Harbor Guide as implementer.',
                cost=dict(model_calls=1, seconds=12))
    result = post.exchange(KINDS)
    assert len(result['posted']) == 1 and not result['bounced']
    state = atelier.facts()
    assert state['plan']['sprints'][0]['tasks'][0]['who'] == 'Harbor Guide' and state['roster'][0]['name'] == 'Harbor Guide'
    assert master.inbox() == []                                              # its own note is not delivered back to it
    assert [n['sender']['kind'] for n in guide.inbox()] == ['operator', 'lead']  # the assistant saw the letter and the plan
    atelier.post(OPERATOR, 'go')
    post.deliver(KINDS)
    texts = [n['text'] for n in guide.inbox()]
    assert 'go' in texts and any(t.startswith('Sprint 1 is approved') for t in texts)   # the word, and the atelier's relay of it
    # The assistant delivers a file through its outbox; the daybook stores it by hash.
    guide.send('Implemented. The deliverable is `implementation/reports.py`.',
               files={'implementation/reports.py': b'def daily_report(rows, start, end):\n    return []\n'},
               cost=dict(model_calls=2, seconds=30))
    result = post.exchange(KINDS)
    assert len(result['posted']) == 1
    deliverable = atelier.facts()['deliverables'][0]
    assert deliverable['name'] == 'implementation/reports.py' and atelier.daybook.read_file(deliverable['sha256']).startswith(b'def daily_report')
    assert master.inbox()[-1]['files'][0]['sha256'] == deliverable['sha256']  # the master received the note and the file reference
    # A note with a credential bounces back to its author, with the reason; said again, it goes through.
    guide.send('Here is the key: ' + KEY)
    result = post.exchange(KINDS)
    assert result['bounced'] == [dict(participant='Harbor Guide', id=result['bounced'][0]['id'], reason='secrets')]
    assert guide.bounces()[0]['bounce']['reason'] == 'secrets' and KEY not in atelier.daybook.path.read_text(encoding='utf-8')
    assert atelier.daybook.read()[-1]['kind'] == 'withheld'
    guide.send('Said again without the secret.')
    assert len(post.exchange(KINDS)['posted']) == 1
    # Restart: a new postmaster on the same roots delivers nothing twice.
    again = Postmaster(Atelier(tmp_path, 'commission-x', secrets=(KEY,)), share)
    before = len(Mailbox(share, GUIDE).inbox(unread_only=False))
    assert again.deliver(KINDS) == {'Harbor Guide': 0, 'harbor-steward': 0}
    assert len(Mailbox(share, GUIDE).inbox(unread_only=False)) == before
    assert atelier.daybook.verify()['ok'] and atelier.budget_left()['model_calls_used'] == 3


def test_money_stops_the_atelier_and_the_note_is_recorded_not_lost(tmp_path):
    atelier = Atelier(tmp_path, 'commission-m', budget=Budget(model_calls=1))
    share = tmp_path / 'share'
    guide, post = Mailbox(share, GUIDE), Postmaster(atelier, share)
    atelier.open(OPERATOR, 'Deliver something. Budget: 1 model call.')
    guide.send('First.', cost=dict(model_calls=1, seconds=3))
    with pytest.raises(AtelierStop) as stop:
        post.collect()
    assert stop.value.reason == 'money' and guide.bounces() == []
    assert len(list((guide.root / 'sent').glob('*.json'))) == 1                 # the note was recorded, then the stop
    rows = atelier.daybook.read()
    assert rows[-2]['text'] == 'First.' and rows[-1]['facts'] == dict(stop='money')   # the record says why
