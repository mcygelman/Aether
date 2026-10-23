"""The atelier page's API and the two projections, offline (Opus, 10-18, the atelier page lane).

What this proves: the operator opens a commission by posting its letter, approves each sprint by posting "go", and
accepts by posting "accepted", through the real route handler (headers, bounded JSON, origin check), with no preview,
no confirm and no form; a wake runs the turn runner it was given and its notes land in the same thread; a failed turn
is written down; files travel by hash and come back byte for byte; read since a note returns only what is newer; and
the campaign state and receipt routes, at their old URLs, answer from the daybook and facts alone.

What it does not prove: a model's turn (the runner is scripted here; `turn.run_turn` is the turn-runner lane) or the
browser (tests/test_atelier_page_ui.js covers the page). The commission is scripted below in the shape of Fable's
Project A; when tests/test_room_daybook.py provides a fixture daybook, `_scripted_turns` can be replaced by it.

Run: py -3.10 -B -m pytest tests/test_atelier_http_offline.py -q -p no:cacheprovider --basetemp build/s/po-atelier
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
from email.message import Message
from types import SimpleNamespace

import pytest

from aether.atelier import atelier_http, projection
from aether.campaign.campaign_http import handle_public_campaign_state

PORT = 8765
ORIGIN = f'http://127.0.0.1:{PORT}'
KEY = 'AtelierPlantedSyntheticKey7f3a19c2'      # synthetic; must never enter the record
LEAD = dict(id='harbor-steward', kind='lead', name='Harbor Steward')
GUIDE = dict(id='Harbor Guide', kind='member', name='Harbor Guide')
MINDER = dict(id='Minder', kind='reviewer', name='Minder')
REPORTS = b'def daily_report(rows, start, end):\n    return [r for r in rows if start <= r["at"] < end]\n'


class Handler:
    """Just enough of BaseHTTPRequestHandler for the route handlers: headers, a body, and what was sent."""

    def __init__(self, method, path, body=None, origin=ORIGIN):
        self.path, self.server = path, SimpleNamespace(server_port=PORT)
        raw = b'' if body is None else json.dumps(body).encode('utf-8')
        self.headers = Message()
        self.headers['Host'] = f'127.0.0.1:{PORT}'
        if origin:
            self.headers['Origin'] = origin
        if body is not None:
            self.headers['Content-Type'] = 'application/json'
            self.headers['Content-Length'] = str(len(raw))
        self.rfile, self.wfile, self.connection = io.BytesIO(raw), io.BytesIO(), None
        self.sent, self.status, self.raw_headers = None, None, {}

    def settings_send(self, value, status=200, *, html=False):
        self.sent, self.status = value, status

    def send_response(self, status):
        self.status = status

    def send_header(self, name, value):
        self.raw_headers[name] = value

    def end_headers(self):
        pass


def call(state, method, path, body=None, origin=ORIGIN):
    handler = Handler(method, path, body, origin)
    assert atelier_http.handle_atelier(handler, method, state) is True
    return handler.status, handler.sent, handler


def campaign(state, path):
    handler = Handler('GET', path, origin=None)
    assert handle_public_campaign_state(handler, 'GET', atelier_http.root_of(state)) is True
    return handler.status, handler.sent


def _scripted_turns():
    """The participants' turns, as a turn runner would post them; one per wake, in order."""
    turns = {
        'harbor-steward': [lambda a: a.post(LEAD, 'Here is the plan.\nSprint 1: implementation\n- implement daily_report with tests (Harbor Guide)\n'
                                                  'Sprint 2: review\n- independent review (Minder)\n'
                                                  'I will hire Harbor Guide as implementer and Minder as reviewer.',
                                            cost=dict(model_calls=1, seconds=20))],
        'Harbor Guide': [lambda a: a.post(GUIDE, 'Implemented. The deliverable is `implementation/reports.py`.',
                                          files=[a.daybook.store_file('implementation/reports.py', REPORTS, GUIDE)],
                                          cost=dict(model_calls=2, seconds=50),
                                          facts=dict(memory=dict(cited=[dict(sentence='F2 stays as delivered',
                                                                             rests_on=[dict(id='n-1', decided_by='Operator', decided_at='2026-10-18', subject='f2', words='F2 stays as delivered')])]))),
                         lambda a: a.closure(GUIDE, 'I implemented the half-open window. I learned to read CONVENTIONS first.')],
        'Minder': [lambda a: a.post(MINDER, 'requirement 1: half-open window - PASS, the bound is exclusive.',
                                    cost=dict(model_calls=1, seconds=30)),
                   lambda a: a.closure(MINDER, 'I reviewed the window. Next time I carry the convention check forward.')],
    }

    def run_turn(atelier, participant, since):
        queue = turns.get(participant['id'])
        if not queue:
            raise RuntimeError('no scripted turn left for ' + participant['id'])
        queue.pop(0)(atelier)
        atelier.mark_read(participant)
    return run_turn


@pytest.fixture
def state(tmp_path):
    return dict(atelier_root=tmp_path / 'atelier', atelier_turn=_scripted_turns(), atelier_secrets=lambda: (KEY,))


def wake(state, commission, participant):
    code, value, _ = call(state, 'POST', '/api/atelier/wake', dict(commission=commission, participant=participant))
    assert code == 202 and value['started'], value
    atelier_http.settle()


def test_the_operator_runs_a_commission_with_three_kinds_of_note(state):
    code, value, _ = call(state, 'POST', '/api/atelier/open',
                          dict(text='Deliver a daily settlement report for the Harbor Ledger: half-open UTC window, with tests. '
                                    'Why: the night audit needs it. What helps: CONVENTIONS.md. Budget: 20 model calls.',
                               files=[dict(name='CONVENTIONS.md', content_base64=base64.b64encode(b'# Windows are half-open\n').decode())]))
    assert code == 200 and 'stop' not in value
    commission = value['commission_id']
    assert value['note']['files'][0]['name'] == 'CONVENTIONS.md'
    code, listing, _ = call(state, 'GET', '/api/atelier')
    assert [c['id'] for c in listing['commissions']] == [commission] and listing['runner'] is True

    code, view, _ = call(state, 'GET', f'/api/atelier/read?commission={commission}')
    assert view['facts']['campaign']['budget_model_calls'] == 20 and view['budget']['model_calls_limit'] == 20
    assert [n['kind'] for n in view['notes']][:2] == ['message', 'host']         # the atelier asked the master for a plan (and invited the librarian)
    assert any(p['kind'] == 'lead' for p in view['people'])

    wake(state, commission, LEAD)
    code, view, _ = call(state, 'GET', f'/api/atelier/read?commission={commission}')
    assert view['facts']['plan']['awaiting_approval'] == 1 and '"go"' in view['hint']
    assert {p['id'] for p in view['people']} >= {'harbor-steward', 'Harbor Guide', 'Minder'}
    seen = view['last_id']

    assert call(state, 'POST', '/api/atelier/post', dict(commission=commission, text='go'))[0] == 200      # sprint 1
    code, newer, _ = call(state, 'GET', f'/api/atelier/read?commission={commission}&since={seen}')
    assert newer['since_known'] and newer['notes'][0]['text'] == 'go' and newer['notes'][0]['sender']['kind'] == 'operator'
    wake(state, commission, GUIDE)
    code, guide_view, _ = call(state, 'GET', f'/api/atelier/read?commission={commission}')
    assert any(n['kind'] == 'host' and 'rests on' in n['text'] for n in guide_view['notes'])
    assert call(state, 'POST', '/api/atelier/post', dict(commission=commission, text='go'))[0] == 200      # sprint 2
    wake(state, commission, MINDER)
    code, value, _ = call(state, 'POST', '/api/atelier/post', dict(commission=commission, text='Accepted.'))
    assert code == 200
    wake(state, commission, GUIDE)
    wake(state, commission, MINDER)

    code, view, _ = call(state, 'GET', f'/api/atelier/read?commission={commission}')
    facts = view['facts']
    assert [a['sprint'] for a in facts['approvals']] == [1, 2] and facts['acceptance']['text'] == 'Accepted.'
    assert facts['deliverables'][0]['sha256'] == hashlib.sha256(REPORTS).hexdigest()
    assert [v['verdict'] for v in facts['verdicts']] == ['PASS'] and len(facts['closures']) == 2
    assert view['chain']['ok'] and view['numbers']['approvals_per_sprint'] == 1.0 and view['numbers']['refusals_per_hundred'] == 0.0
    assert view['budget']['model_calls_used'] == 4

    # Files by hash, byte for byte, as a download.
    sha = facts['deliverables'][0]['sha256']
    code, _, handler = call(state, 'GET', f'/api/atelier/file?commission={commission}&sha256={sha}')
    assert code == 200 and handler.wfile.getvalue() == REPORTS
    assert handler.raw_headers['Content-Disposition'] == 'attachment; filename="reports.py"'
    assert handler.raw_headers['X-Content-Type-Options'] == 'nosniff'

    # The campaign state page, at its old URL, is a view of the daybook and facts.
    code, page_state = campaign(state, f'/api/campaign-state?id={commission}')
    assert code == 200 and page_state['source'] == 'atelier' and page_state['stage'] == 'Close'
    assert {s['path'] for s in page_state['sources']} == {'daybook.jsonl', 'facts.json'}
    daybook = atelier_http.root_of(state) / 'daybooks' / commission / 'daybook.jsonl'
    assert dict((s['path'], s['sha256']) for s in page_state['sources'])['daybook.jsonl'] == hashlib.sha256(daybook.read_bytes()).hexdigest()
    assert page_state['numbers'] == view['numbers'] and page_state['actions'][0]['href'] == f'/projects/atelier?commission={commission}'
    assert campaign(state, '/api/campaign-state')[1]['campaign_id'] == commission           # no id: the latest commission

    # The receipt page, at its old URL: every note with its hash, each file with its download.
    code, receipt = campaign(state, f'/api/campaign-receipts?id={commission}')
    rows = [json.loads(line) for line in daybook.read_text(encoding='utf-8').splitlines()]
    assert code == 200 and receipt['verification']['ok'] and [r['sha256'] for r in receipt['receipts']] == [r['sha256'] for r in rows]
    assert {f['sha256'] for f in receipt['files']} == {sha, hashlib.sha256(b'# Windows are half-open\n').hexdigest()}
    assert all(c['href'].startswith('/api/atelier/file?') for r in receipt['receipts'] for c in r['captured'] if c['field'] != 'previous note')


def test_closing_hint_follows_filing_and_revised_closing_words(state):
    _, opened, _ = call(state, 'POST', '/api/atelier/open', dict(text='Lend the connection guide.'))
    cid = opened['commission_id']
    librarian = dict(id='lunch-librarian', kind='librarian', name='Lunch Librarian')
    room = atelier_http.atelier(state, cid)
    room.post(librarian, 'The guide is attached.')
    call(state, 'POST', '/api/atelier/post', dict(commission=cid, text='Accepted.'))

    def check(stage, hint):
        before = room.daybook.path.read_bytes()
        code, view, _ = call(state, 'GET', f'/api/atelier/read?commission={cid}')
        assert code == 200 and view['hint'] == hint
        assert campaign(state, f'/api/campaign-state?id={cid}')[1]['stage'] == stage
        assert room.daybook.path.read_bytes() == before   # status reads never file or rewrite a closure

    waiting = 'Accepted; closing notes are being written and filed.'
    finished = 'Accepted; every closing note is filed.'
    check('Close', waiting)
    room.closure(librarian, 'I learned to inspect the shelf item before lending it.')
    check('Close', waiting)
    room.filed(librarian, 'the librarian home')
    check('Closed', finished)
    room.closure(librarian, 'I learned to verify the delivered bytes as well as the shelf item.')
    check('Close', waiting)
    room.filed(librarian, 'the librarian home')
    check('Closed', finished)


def test_closing_hint_waits_for_other_participants(state):
    _, opened, _ = call(state, 'POST', '/api/atelier/open', dict(text='Deliver a reviewed report.'))
    cid = opened['commission_id']
    room = atelier_http.atelier(state, cid)
    room.post(LEAD, 'I will hire Harbor Guide as implementer and Minder as reviewer.')
    call(state, 'POST', '/api/atelier/post', dict(commission=cid, text='Accepted.'))
    room.closure(GUIDE, 'I learned to inspect the conventions.')
    room.filed(GUIDE, 'Guide home')
    assert any(q['about'] == 'closure' and q['to'] == MINDER['id']
               for q in room.facts()['questions_asked'])
    _, view, _ = call(state, 'GET', f'/api/atelier/read?commission={cid}')
    assert view['hint'] == 'Accepted; closing notes are being written and filed.'
    assert campaign(state, f'/api/campaign-state?id={cid}')[1]['stage'] == 'Close'
    room.closure(MINDER, 'I learned to check the boundary independently.')
    room.filed(MINDER, 'Minder home')
    _, view, _ = call(state, 'GET', f'/api/atelier/read?commission={cid}')
    assert view['hint'] == 'Accepted; every closing note is filed.'
    assert campaign(state, f'/api/campaign-state?id={cid}')[1]['stage'] == 'Closed'


def test_a_failed_turn_a_withheld_note_and_a_money_stop_are_notes_not_refusals(state):
    _, opened, _ = call(state, 'POST', '/api/atelier/open', dict(text='Deliver a summary. Budget: 1 model calls.'))
    commission = opened['commission_id']
    wake(state, commission, dict(id='nobody', kind='member', name='Nobody'))           # the runner has no turn: it fails
    _, view, _ = call(state, 'GET', f'/api/atelier/read?commission={commission}')
    assert view['notes'][-1]['kind'] == 'turn_failed' and 'no scripted turn' in view['notes'][-1]['text']
    assert view['numbers']['refusals_per_hundred'] > 0

    code, value, _ = call(state, 'POST', '/api/atelier/post', dict(commission=commission, text='the key is ' + KEY))
    assert code == 200 and value['stop'] == 'secrets'
    daybook = atelier_http.root_of(state) / 'daybooks' / commission / 'daybook.jsonl'
    assert KEY not in daybook.read_text(encoding='utf-8')

    wake(state, commission, LEAD)                                                      # 1 model call: the budget
    _, view, _ = call(state, 'GET', f'/api/atelier/read?commission={commission}')
    assert any(n.get('facts', {}).get('stop') == 'money' for n in view['notes'])
    assert campaign(state, f'/api/campaign-state?id={commission}')[1]['condition'] == 'NEEDS_YOU'
    assert view['chain']['ok']                                                          # the record stays whole


def test_the_routes_answer_plainly(state, monkeypatch):
    assert call(state, 'POST', '/api/atelier/open', dict(text='x'), origin='https://elsewhere.example')[0] == 403
    assert call(state, 'GET', '/api/atelier/read?commission=c-missing')[0] == 404
    assert call(state, 'GET', '/api/atelier/read?commission=../../etc')[0] == 404
    assert call(state, 'GET', '/api/atelier/open')[0] == 405
    _, opened, _ = call(state, 'POST', '/api/atelier/open', dict(text='Deliver a note.'))
    commission = opened['commission_id']
    assert call(state, 'GET', f'/api/atelier/file?commission={commission}&sha256=nothex')[0] == 409
    assert call(state, 'GET', f'/api/atelier/file?commission={commission}&sha256={"0" * 64}')[0] == 409
    code, value, _ = call(state, 'POST', '/api/atelier/post', dict(commission=commission, text='', files=[dict(name='../x', content_base64='eA==')]))
    assert code == 409 and 'relative' in value['error']
    monkeypatch.setattr(atelier_http, 'turn_runner', lambda state: None)              # a build without a turn runner
    code, value, _ = call(state, 'POST', '/api/atelier/wake', dict(commission=commission, participant=LEAD))
    assert code == 503 and value['wired'] is False
    assert campaign(state, '/api/campaign-state?id=project-earlier')[0] == 404          # not a commission: never the earlier native records
    assert projection.exists(atelier_http.root_of(state), commission)


def test_library_conversation_reply_followup_and_filed_completion(state):
    code, opened, _ = call(state, 'POST', '/api/atelier/open',
                          dict(text='Lend the guide. Budget: 12 model calls.', request_kind='library'))
    assert code == 200
    cid = opened['commission_id']
    room = atelier_http.atelier(state, cid)
    librarian = dict(id='shelf-keeper', kind='librarian', name='Shelf Keeper')

    def read(label):
        before = room.daybook.path.read_bytes()
        _, view, _ = call(state, 'GET', f'/api/atelier/read?commission={cid}')
        assert view['status']['label'] == label
        assert view['facts']['campaign']['request_kind'] == 'library'
        assert not any(p['kind'] == 'lead' for p in view['people'])
        assert not any(q['about'] == 'plan' for q in view['facts']['questions_asked'])
        assert room.daybook.path.read_bytes() == before
        return view

    assert not read('Waiting for reply')['status']['can_accept']
    ref = room.daybook.store_file('guide.txt', b'CONNECTION-CHECK', librarian)
    room.post(librarian, 'Here is the guide.', files=[ref], facts={'request_kind': 'project'})
    assert read('Waiting for you')['status']['can_accept']
    code, _, download = call(state, 'GET', f'/api/atelier/file?commission={cid}&sha256={ref["sha256"]}')
    assert code == 200 and download.wfile.getvalue() == b'CONNECTION-CHECK'
    call(state, 'POST', '/api/atelier/post', dict(commission=cid, text='Please explain the first step.'))
    read('Waiting for reply')
    room.post(librarian, 'Start by checking your connection.')
    read('Waiting for you')
    call(state, 'POST', '/api/atelier/post', dict(commission=cid, text='Accepted.'))
    closing = read('Closing')
    assert any(q['about'] == 'closure' and q['to'] == librarian['id'] for q in closing['facts']['questions_asked'])
    room.closure(librarian, 'I lent the guide and explained its first step.')
    read('Closing')
    room.note('Could not file the closing note: fixture home unavailable.', facts=dict(filing_failed=dict(participant=librarian)))
    read('Filing failed')
    room.filed(librarian, 'fixture home')
    finished = read('Finished')
    assert not finished['status']['can_accept'] and not finished['status']['can_approve']
    for path in ('/api/campaign-state', '/api/campaign-receipts'):
        result = campaign(state, f'{path}?id={cid}')[1]
        assert result['stage'] == 'Closed' and result['condition'] == 'DONE'
        assert not result.get('obligations')


def test_intent_is_explicit_and_invalid_choice_creates_no_conversation(state):
    assert call(state, 'POST', '/api/atelier/open', dict(text='x', request_kind='unknown'))[0] == 409
    assert not projection.commissions(atelier_http.root_of(state))
    _, opened, _ = call(state, 'POST', '/api/atelier/open', dict(text='Use library documents in our team project.'))
    _, view, _ = call(state, 'GET', '/api/atelier/read?commission=' + opened['commission_id'])
    assert view['facts']['campaign']['request_kind'] == 'project'
    assert any(q['about'] == 'plan' for q in view['facts']['questions_asked'])
    assert view['status']['label'] == 'Waiting for reply'


def test_active_turn_failed_turn_and_resolved_stop_are_clear(state):
    _, opened, _ = call(state, 'POST', '/api/atelier/open', dict(text='Deliver a report.'))
    cid = opened['commission_id']
    room = atelier_http.atelier(state, cid)
    active = projection.thread(atelier_http.root_of(state), cid, working=[LEAD['id']])
    assert active['status']['label'] == 'Working' and active['status']['condition'] == 'RUNNING'
    room.turn_failed(LEAD, 'Fixture connection failed.')
    assert projection.thread(atelier_http.root_of(state), cid)['status']['label'] == 'Turn failed'
    room.note('Budget exhausted. Please add model calls.', facts={'stop': 'money'})
    assert projection.thread(atelier_http.root_of(state), cid)['status']['label'] == 'Waiting for you'
    assert campaign(state, f'/api/campaign-state?id={cid}')[1]['condition'] == 'NEEDS_YOU'
    call(state, 'POST', '/api/atelier/post', dict(commission=cid, text='Budget: 20 model calls. Please continue.'))
    assert projection.thread(atelier_http.root_of(state), cid)['status']['label'] == 'Waiting for reply'
    assert campaign(state, f'/api/campaign-state?id={cid}')[1]['condition'] != 'NEEDS_YOU'
