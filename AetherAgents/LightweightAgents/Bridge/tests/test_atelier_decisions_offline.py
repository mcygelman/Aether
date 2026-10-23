"""Decision memory in the atelier (Fable's design, built by Opus, 2026-10-19): the operator's ruling filed into every mind,
the lead recording the operator's words, a member told plainly it does not file decisions, the wake's own block, the link
to the warning it overrides, replacement, and what a cited decision rests on, said in the room. No model, no network."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from aether.atelier import Atelier, mind_memory   # noqa: E402
from aether.atelier.participants import Participant   # noqa: E402
from aether.atelier.place import Place   # noqa: E402
from aether.atelier.postmaster import Postmaster   # noqa: E402
from aether.atelier.turn import Tools, run_turn, tool_schemas, wake_messages   # noqa: E402
from aether.mailbox import Mailbox   # noqa: E402

OPERATOR = dict(id='operator', kind='operator', name='Operator')
WARNING = 'In this repo, a cap on amounts is not a cap on totals.'


def _minds(tmp_path):
    share = tmp_path / 'share'
    made = []
    for pid, kind, name in (('harbor-guide', 'member', 'Harbor Guide'), ('harbor-minder', 'reviewer', 'Harbor Minder'),
                            ('harbor-steward', 'lead', 'Harbor Steward')):
        p = Participant.author(tmp_path / 'homes' / pid, id=pid, kind=kind, name=name, system=f'You are {name}.',
                               roots={'inputs': 'read', 'outputs': 'write'}, mailboxes=share)
        p.file_now('warning', WARNING, 'Do not infer a cap on totals.', ['cap'], commission_id='harbor-ledger-a')
        made.append(p)
    return share, made


def _place(share, minds, log=None):
    return Place(share, [m.home for m in minds], name='test-place', log=(log.append if log is not None else (lambda *_: None)))


def _open(share, cid='harbor-ledger-e'):
    atelier = Atelier(share, cid)
    atelier.open(OPERATOR, 'Project E: a month report on the harbour ledger. Budget: 20 model calls.')
    return atelier


def test_the_operators_ruling_is_filed_into_every_mind_with_its_scope(tmp_path):
    share, minds = _minds(tmp_path)
    atelier = _open(share)
    atelier.post(OPERATOR, 'go\nDecision: F2 stays as delivered; daily_report keeps its cap on the total.\nDecided (F1): its own ticket.')
    facts = atelier.facts()
    assert [d['words'][:8] for d in facts['decisions']] == ['F2 stays', 'its own ']
    log = []
    _place(share, minds, log).file_decisions(atelier, 'harbor-ledger-e')
    _place(share, minds).file_decisions(atelier, 'harbor-ledger-e')               # once per source, however often the place looks
    for p in minds:
        rows = mind_memory.decisions(p.home, 'harbor-ledger-e')
        assert sorted(d['subject'] for d in rows) == ['f1', 'f2'] and {d['scope'] for d in rows} == {'project:harbor-ledger'}
        assert all(d['decided_by'] == 'Operator' for d in rows)
        assert mind_memory.decisions(p.home, 'tidewater-a') == []                 # another project: not bound
    assert sum('decision filed' in line for line in log) == 6
    atelier.post(OPERATOR, 'Decision: standing: every report keeps exact integer arithmetic.')
    _place(share, minds).file_decisions(atelier, 'harbor-ledger-e')
    assert [d['scope'] for d in mind_memory.decisions(minds[0].home, 'tidewater-a', ['exact', 'integer'])] == ['standing']


def test_the_wake_shows_the_decisions_whole_above_the_skips_and_the_headers(tmp_path):
    share, minds = _minds(tmp_path)
    atelier = _open(share)
    atelier.post(OPERATOR, 'Decision: F2 stays as delivered; daily_report keeps its cap on the total.')
    _place(share, minds).file_decisions(atelier, 'harbor-ledger-e')
    guide = minds[0]
    facts = atelier.facts()
    context = dict(unread=[], unread_total=0, budget_left={}, prior_closures=[], questions_for_me=[])
    user = wake_messages(guide, context, facts)[1]['content']
    block = user.index('DECISIONS THAT BIND HERE')
    assert block < user.index('YOUR MEMORY (headers')
    assert 'In their words: "F2 stays as delivered; daily_report keeps its cap on the total."' in user
    assert guide.last_decisions and all(i.startswith('n-') for i in guide.last_decisions)


def test_the_ruling_links_to_the_warning_it_overrides_and_a_later_ruling_replaces_it(tmp_path):
    share, minds = _minds(tmp_path)
    atelier = _open(share)
    atelier.post(OPERATOR, "Decision: F2 stays as delivered; daily_report keeps its cap on the day's total.")
    _place(share, minds).file_decisions(atelier, 'harbor-ledger-e')
    guide = minds[0]
    first = mind_memory.decisions(guide.home, 'harbor-ledger-e')[0]['id']
    heads = mind_memory.recall(guide.home, ['cap', 'amounts', 'totals'])
    warning = next(h for h in heads if h['title'] == WARNING)
    assert warning['decisions'] == [first]
    assert f'(a decision applies: {first})' in mind_memory.render_headers(heads)
    atelier.post(OPERATOR, 'Decision: F2 is lifted after all; the total is not capped.')
    _place(share, minds).file_decisions(atelier, 'harbor-ledger-e')
    now = mind_memory.decisions(guide.home, 'harbor-ledger-e')
    assert len(now) == 1 and now[0]['id'] != first and 'lifted' in now[0]['words']
    engine = mind_memory._open_read(guide.home)        # the old ruling is buried, so tag recall no longer raises it;
    try:                                               # when it does surface (discovery, a load), its header says what replaced it
        old = dict(id=first, title='old', kind='decision', strength=0.1, one_line='')
        mind_memory._mark_decisions(engine.cart.conn, [old])
        strength = engine.cart.conn.execute('SELECT strength FROM temp.note_state WHERE note_id = ?', (first,)).fetchone()[0]
    finally:
        engine.close()
    assert old['replaced_by'] == now[0]['id'] and f'(replaced by {now[0]["id"]})' in mind_memory.render_headers([old])
    assert strength < 0.3                              # buried, not deleted


def test_the_lead_records_the_operator_and_a_member_is_told_plainly(tmp_path):
    share, minds = _minds(tmp_path)
    guide, minder, steward = minds
    assert 'file_decision' in [t['function']['name'] for t in tool_schemas(lead=True)]
    assert 'file_decision' not in [t['function']['name'] for t in tool_schemas()]
    member = Tools(guide, tmp_path / 'work', commission_id='harbor-ledger-e')
    assert member.call('file_decision', dict(words='F2 is removed')) == mind_memory.ONLY_THEY_FILE and not member.decisions
    lead = Tools(steward, tmp_path / 'work', commission_id='harbor-ledger-e')
    assert lead.call('file_decision', dict(words='The operator said: F1 goes to its own ticket.', subject='F1')).startswith('recorded')
    atelier = _open(share)
    atelier.post(steward.record(), 'The operator said F1 goes to its own ticket; recorded.', facts=dict(memory=dict(decisions_filed=lead.decisions)))
    atelier.post(guide.record(), 'Decided: the cap goes.', facts=dict(memory=dict(decisions_filed=[dict(words='the cap goes')])))  # not a lead: nothing
    _place(share, minds).file_decisions(atelier, 'harbor-ledger-e')
    rows = mind_memory.decisions(guide.home, 'harbor-ledger-e')
    assert [(d['subject'], d['decided_by']) for d in rows] == [('f1', 'Harbor Steward (lead, recording the operator)')]


class _Cites:
    """A scripted mind whose note and file cite a decision."""
    def __init__(self, text):
        self.text, self.calls = text, 0

    def chat(self, messages, tools=None):
        self.calls += 1
        if self.calls == 1:
            body = json.dumps(dict(path='outputs/REVIEW.md', content='Operator decision: F2 stays as delivered.\n'))
            return dict(role='assistant', content='', tool_calls=[dict(id='w', type='function', function=dict(name='write_file', arguments=body))])
        return dict(role='assistant', content=self.text, _usage=dict(prompt_tokens=10, completion_tokens=5))


def _turn(tmp_path, share, atelier, mind, text):
    post = Postmaster(atelier, share)
    post.exchange({mind.id: mind.kind}, {mind.id: mind.name}, only=[mind.id])
    root = share / 'work' / atelier.campaign_id; (root / 'outputs').mkdir(parents=True, exist_ok=True)
    assert run_turn(mind, atelier, Mailbox(share, mind.record()), _Cites(text), root) is not None
    post.collect([mind.id])
    return atelier.daybook.read()


def test_a_cited_decision_is_said_in_the_room_with_what_it_rests_on(tmp_path):
    share, minds = _minds(tmp_path)
    guide = minds[0]
    atelier = _open(share)
    atelier.post(OPERATOR, "Decision: F2 stays as delivered; daily_report keeps its cap on the day's total.")
    _place(share, [guide]).file_decisions(atelier, 'harbor-ledger-e')
    notes = _turn(tmp_path, share, atelier, guide, 'Done. Operator decision: F2 stays as delivered, so the cap stays.')
    mine = next(n for n in notes if n['sender']['id'] == guide.id)
    host = [n for n in notes if n['kind'] == 'host' and (n.get('facts') or {}).get('cited_decision')]
    assert host and host[0]['n'] == mine['n'] + 1 and host[0]['to']['kind'] == 'reviewer'
    assert 'It rests on [n-' in host[0]['text'] and 'Operator' in host[0]['text']
    assert mine['text'] == 'Done. Operator decision: F2 stays as delivered, so the cap stays.'      # the mind's words, unchanged
    assert (mine.get('facts') or {}).get('memory', {}).get('decisions_shown')
    assert any(r['kind'] == 'cited-decision' and r['pointer'].startswith('n-') for r in guide.log_recent(limit=20))


def test_a_decision_cited_from_nowhere_is_said_to_rest_on_nothing(tmp_path):
    share, minds = _minds(tmp_path)
    guide = minds[0]
    atelier = _open(share)
    notes = _turn(tmp_path, share, atelier, guide, 'Done. Decision (operator, on accepting Project A): remove the check.')
    host = [n for n in notes if n['kind'] == 'host' and (n.get('facts') or {}).get('cited_decision')]
    assert host and all('rests on nothing in the record' in n['text'] for n in host)
    assert any(r['kind'] == 'cited-decision' and r['pointer'] == 'none' for r in guide.log_recent(limit=20))


def test_talk_about_decisions_is_not_a_citation(tmp_path):
    share, minds = _minds(tmp_path)
    said = ['The lead coordinates planning and records operator decisions.', 'Only the lead records operator decisions.']
    assert mind_memory.cited_decisions(minds[0].home, said) == []        # the second-machine run, 10-19: design talk, not a claim
