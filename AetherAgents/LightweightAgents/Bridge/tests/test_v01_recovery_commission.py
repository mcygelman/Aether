"""A fresh schema-4 commission with a lone master, through the real place loop (v0.1).

One authored mind, a new cartridge at the current schema, one commission: the master delivers, the operator accepts,
the atelier asks for closing words, the master writes them, they are filed into its own cartridge as written, the
closure is published into its lifecycle journal at its next working activation, and the next commission's Welcome
shows that closure. Nothing is written for the mind by the host; no acknowledgment is needed anywhere.
"""
from __future__ import annotations

import json

from aether.atelier import Atelier, Budget, Participant, mind_memory
from aether.atelier.place import Place
from tests.test_atelier_context_guard_offline import Strict

OPERATOR = dict(id='operator', kind='operator', name='Operator')
CLOSING = ('What I did: wrote the haiku and named the file. What I learned: a half-open window reads cleaner. '
           'What my future self should carry: name the deliverable in backticks. What my next self should skip: a second draft.')


def _write(call_id, path, content):
    return dict(role='assistant', content='', tool_calls=[dict(id=call_id, type='function', function=dict(
        name='write_file', arguments=json.dumps(dict(path=path, content=content))))])


def test_lone_master_commission_closes_in_its_own_words_and_the_next_welcome_shows_it(tmp_path):
    share = tmp_path / 'share'
    master = Participant.author(tmp_path / 'home', id='solo', kind='lead', name='Solo', system='You are Solo, the lead, working alone.',
                                mailboxes=share, roots={'outputs': 'write'})
    with mind_memory._open_read(master.home) as engine:
        assert dict(engine.cart.conn.execute('SELECT key,value FROM meta'))['schema_version'] == '4'     # fresh, current schema
    client = Strict([_write('w1', 'outputs/haiku.md', 'Ledgers close at dusk\n'),
                     dict(role='assistant', content='Done. The deliverable is `outputs/haiku.md`.'),
                     dict(role='assistant', content=CLOSING)], context_window=131072, max_tokens=500)
    atelier = Atelier(share, 'haiku-a', budget=Budget(model_calls=10))
    atelier.open(OPERATOR, 'Write a haiku about ledgers into outputs/haiku.md. Budget: 10 model calls.')
    logs = []
    place = Place(share, [master.home], name='solo-place', client=client, log=logs.append)

    first = place.round()
    assert len(first['turns']) == 1 and not first['failed']
    state = atelier.facts()
    assert state['deliverables'] and state['deliverables'][0]['name'] == 'outputs/haiku.md' and not state['closures']
    atelier.post(OPERATOR, 'accepted')

    second = place.round()                       # the atelier asks for closing words; the master answers; the place files them
    assert len(second['turns']) == 1 and not second['failed'] and second.get('filed')
    state = atelier.facts()
    assert state['acceptance'] and len(state['closures']) == 1
    closure = state['closures'][0]
    assert closure['participant']['id'] == 'solo' and closure['text'] == CLOSING and closure['filed'] is True
    assert Place.resting(atelier, 'solo') and len(client.requests) == 3

    # The mind's own words, in its own cartridge, under its own name; nothing written for it by the host.
    with mind_memory._open_read(master.home) as engine:
        rows = engine.cart.conn.execute("SELECT title, written_by, body FROM notes WHERE title LIKE 'Closing words%'").fetchall()
        assert len(rows) == 1 and rows[0][1] == 'solo' and rows[0][2] == CLOSING
        opened = engine.cart.conn.execute("SELECT count(*) FROM session_events WHERE kind='opened'").fetchone()[0]
        closed = engine.cart.conn.execute("SELECT count(*) FROM session_events WHERE kind='closed'").fetchone()[0]
    assert opened == 1 and closed == 1                     # the filing published the closure into the lifecycle journal, once
    receipts = list((master.home / 'logs' / 'atelier_closure').glob('*.json'))
    assert len(receipts) == 1 and json.loads(receipts[0].read_text(encoding='utf-8'))['status'] == 'CLOSED'

    # A third round does nothing: the master rests until the operator speaks again.
    third = place.round()
    assert not third['turns'] and not third['failed'] and len(client.requests) == 3

    # The next commission: a new working session opens, no history is rewritten, and the Welcome shows the verified
    # closure and the mind's own carry (the predecessor of a new commission is not assumed; it is named as unknown).
    b = Atelier(share, 'haiku-b', budget=Budget(model_calls=10))
    b.open(OPERATOR, 'Now a haiku about windows. Budget: 10 model calls.')
    mind_memory.begin_work(master.home, 'haiku-b')
    with mind_memory._open_read(master.home) as engine:
        kinds = [r[0] for r in engine.cart.conn.execute('SELECT kind FROM session_events ORDER BY event_seq')]
    assert kinds.count('opened') == 2 and kinds.count('closed') == 1
    welcome = mind_memory.welcome(master.home, 'haiku-b')
    assert 'PASS' in welcome and 'What my future self should carry' in welcome and 'Closing words' in welcome
