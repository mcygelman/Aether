"""Interruption and restart on the real Atelier path (v0.1).

A separate place process dies at an exact point of a turn (aether/atelier/faults.py, no cleanup) and is started again.
What must hold: accepted work, costs, the daybook chain and the files stay; nothing is answered or posted twice; a lost
turn is recorded as uncertain, not as success; no acknowledgment or clearance is needed to go on. The closure filing
point is exercised in-process. Scripted provider over HTTP through the production adapter; disposable homes only.
"""
from __future__ import annotations

import json
import sys

import pytest

from aether.atelier import Atelier, Budget, Participant
from aether.atelier.faults import EXIT_CODE
from aether.mailbox import Mailbox
from tools.v01_recovery.interrupted_place import run_case

OPERATOR = dict(id='operator', kind='operator', name='Operator')


@pytest.fixture(scope='module')
def cases(tmp_path_factory):
    """Every subprocess case once; the tests below read the reports."""
    out = {}
    for point in ('none', 'after_think', 'after_send', 'after_marks', 'after_collect_post'):
        out[point] = run_case(point, tmp_path_factory.mktemp('rec_' + point), python=sys.executable)
    return out


def test_baseline_turn_completes_and_a_second_start_has_nothing_to_do(cases):
    r = cases['none']
    assert r['first_exit'] == 0 and r['restart_exit'] == 0
    before, after = r['after_interruption'], r['after_restart']
    assert before['reader_notes'] == 1 and before['provider_posts'] == 2 and before['recorded_model_calls'] == 2
    assert after == dict(before, place_stopped=True) or after['reader_notes'] == 1 and after['provider_posts'] == 2
    assert 'outputs/result.md' in after['files'] and after['daybook_chain'] and after['interrupted_records'] == 0


def test_killed_after_thinking_is_recorded_as_uncertain_and_the_mind_is_woken_again(cases):
    r = cases['after_think']
    assert r['first_exit'] == EXIT_CODE and r['restart_exit'] == 0
    before, after = r['after_interruption'], r['after_restart']
    assert before['reader_notes'] == 0 and before['provider_posts'] == 2          # the model answered; nothing was recorded
    assert before['place_working'] and before['place_working']['participant'] == 'reader'
    assert before['inbox_unread'] == 1 and 'outputs/result.md' in before['files']  # the effect happened; the mail is unread
    assert after['interrupted_records'] == 1                                      # said in the daybook, as record, with what is uncertain
    assert after['reader_notes'] == 1 and after['provider_posts'] == 4             # woken again: one note, the lost call is not replayed as a note
    assert after['recorded_model_calls'] == 2 and after['inbox_unread'] == 0 and after['daybook_chain']


def test_killed_after_sending_is_not_answered_twice(cases):
    r = cases['after_send']
    assert r['first_exit'] == EXIT_CODE and r['restart_exit'] == 0
    before, after = r['after_interruption'], r['after_restart']
    assert before['outbox'] == 1 and before['inbox_unread'] == 1 and before['reader_notes'] == 0
    assert after['provider_posts'] == before['provider_posts'] == 2             # no second turn
    assert after['reader_notes'] == 1 and after['outbox'] == 0 and after['sent'] == 1 and after['inbox_unread'] == 0
    assert after['interrupted_records'] == 1 and after['daybook_chain']


def test_killed_after_marks_is_collected_without_a_turn(cases):
    r = cases['after_marks']
    assert r['first_exit'] == EXIT_CODE and r['restart_exit'] == 0
    before, after = r['after_interruption'], r['after_restart']
    assert before['outbox'] == 1 and before['inbox_unread'] == 0
    assert after['reader_notes'] == 1 and after['provider_posts'] == 2 and after['outbox'] == 0 and after['daybook_chain']


def test_killed_after_the_daybook_append_is_not_posted_twice(cases):
    r = cases['after_collect_post']
    assert r['first_exit'] == EXIT_CODE and r['restart_exit'] == 0
    before, after = r['after_interruption'], r['after_restart']
    assert before['reader_notes'] == 1 and before['outbox'] == 1                 # appended, outbox copy not yet moved
    assert after['reader_notes'] == 1 and after['outbox'] == 0 and after['sent'] == 1 and after['daybook_chain']
    assert after['provider_posts'] == 2                                         # no second turn, no second note
    assert after['interrupted_records'] == 1                                    # the place died in the turn's own collect: recorded,
    assert after['host_rows'] >= before['host_rows']                            # and the record says the note existed (note_in_outbox)


def _accepted_commission(tmp_path):
    share = tmp_path / 'share'
    mind = Participant.author(tmp_path / 'home', id='solo', kind='lead', name='Solo', system='You lead alone.', mailboxes=share)
    atelier = Atelier(share, 'lone-master', budget=Budget(model_calls=10))
    atelier.open(OPERATOR, 'Write a haiku about ledgers. Budget: 10 model calls.')
    atelier.post(mind.record(), 'Ledgers close at dusk / every line a settled sum / the window half-open.')
    atelier.post(OPERATOR, 'accepted')
    atelier.closure(mind.record(), 'What I did: a haiku. What I learned: brevity. Carry: say the window. Skip: nothing.')
    return share, mind, atelier


def test_closure_filing_interrupted_after_the_cartridge_write_is_filed_once(tmp_path, monkeypatch):
    from aether.atelier import faults
    from aether.atelier.place import Place
    share, mind, atelier = _accepted_commission(tmp_path)
    place = Place(share, [mind.home], name='closer', client=object(), log=lambda *_: None)

    class Died(BaseException):
        pass

    def die(name):
        if name == 'after_closure_filed':
            raise Died()
    monkeypatch.setattr(faults, 'point', die)
    report = dict(commissions={}, turns=[], failed=[], skipped=[])
    with pytest.raises(Died):
        place.file_closures(atelier, 'lone-master', report)
    assert atelier.facts()['closures'][0]['filed'] is False                     # the atelier never recorded the filing
    monkeypatch.setattr(faults, 'point', lambda name: None)
    place.file_closures(atelier, 'lone-master', report)
    state = atelier.facts()
    assert state['closures'][0]['filed'] is True and len(state['closures']) == 1
    from aether.atelier import mind_memory
    with mind_memory._open_read(mind.home) as engine:
        titles = [r[0] for r in engine.cart.conn.execute("SELECT title FROM notes WHERE title LIKE 'Closing words%'")]
    assert titles == ['Closing words, lone-master:']                            # filed once; the mind's own words, as written
    assert Place.resting(atelier, 'solo')


def test_a_loaded_home_is_where_it_is_found(tmp_path):
    mind = Participant.author(tmp_path / 'original', id='mover', kind='member', name='Mover', system='You move.', mailboxes=tmp_path / 'share')
    import shutil
    shutil.copytree(tmp_path / 'original', tmp_path / 'restored' / 'mover')
    loaded = Participant.load(tmp_path / 'restored' / 'mover')
    assert loaded.home == tmp_path / 'restored' / 'mover' and loaded.id == 'mover'
    assert json.loads((tmp_path / 'restored' / 'mover' / 'participant.json').read_text(encoding='utf-8'))['home'] == str(tmp_path / 'original')
    assert loaded.cartridge()['path'].startswith(str(tmp_path / 'restored'))     # the pointer is relative, so it moved too


def test_read_marks_are_written_whole(tmp_path):
    share = tmp_path / 'share'
    atelier = Atelier(share, 'marks')
    atelier.open(OPERATOR, 'Hello.')
    atelier.mark_read(dict(id='reader', kind='member'))
    marks = atelier.daybook.root / 'read-marks.json'
    assert json.loads(marks.read_text(encoding='utf-8'))['reader'] and not marks.with_suffix('.json.tmp').exists()
    assert Mailbox(share, dict(id='reader', kind='member')) is not None
