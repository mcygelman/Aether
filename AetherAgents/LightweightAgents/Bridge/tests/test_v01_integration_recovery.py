"""Integration corrections: uncertainty is exact and simultaneous marks do not collide."""
import json
from pathlib import Path

import pytest

from aether.atelier import Atelier, Participant
from aether.atelier.place import Place
from aether.mailbox import Mailbox


@pytest.mark.parametrize('matching', [False, True])
def test_recovery_only_claims_the_interrupted_turns_reply(tmp_path, matching):
    share = tmp_path/'share'
    mind = Participant.author(tmp_path/'home', id='reader', kind='member', name='Reader',
                              system='Read carefully.', mailboxes=share)
    atelier = Atelier(share, 'current')
    atelier.open(dict(id='operator', kind='operator'), 'A task.')
    box = Mailbox(share, mind.record())
    box.send('A completed note.', in_reply_to='current-note' if matching else 'another-commission-note')
    place = Place(share, [mind.home], name='test', client=object(), log=lambda *_: None)
    place.record_path.write_text(json.dumps(dict(pid=-1, heartbeat='old', working=dict(
        commission='current', participant=mind.id, in_reply_to='current-note'))), encoding='utf-8')
    assert place.recover_interrupted()
    record = atelier.daybook.read()[-1]['facts']['interrupted']
    assert record['note_in_outbox'] is matching
    assert record['uncertain'] == ([] if matching else ['model_call', 'files', 'cost'])
    assert not place.recover_interrupted()


def test_interleaved_read_mark_writes_have_distinct_temporary_files(tmp_path, monkeypatch):
    from aether.atelier import atelier as implementation
    a = Atelier(tmp_path, 'marks')
    a.open(dict(id='operator', kind='operator'), 'A task.')
    b = Atelier(tmp_path, 'marks')
    replace = implementation.os.replace
    paths = []
    def interleave(source, destination):
        paths.append(Path(source))
        if len(paths) == 1:
            b.mark_read(dict(id='second', kind='member'))
        replace(source, destination)
    monkeypatch.setattr(implementation.os, 'replace', interleave)
    a.mark_read(dict(id='first', kind='member'))
    assert len(set(paths)) == 2 and all(not p.exists() for p in paths)
    assert json.loads((a.daybook.root/'read-marks.json').read_bytes())['first']
