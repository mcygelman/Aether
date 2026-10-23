"""Backup and restore of Atelier data through the existing preservation contract (v0.1, Fable, 2026-10-22).

An Atelier home (participant.json, memory/.cartridge.json, a schema-4 mind.sqlite) and a share (daybook, files store,
mailboxes) under the installation's data root are backed up with the existing maintenance backup, verified, restored
to a new root, and the Atelier is run from the restored root. A cartridge kept outside the home must have its root
selected or the backup refuses. A destination too deep for the daybook's 64-character file names is refused up front.
Synthetic installation only; no resident data. Short folder names, because the scoped runner's scratch path is long.
"""
from __future__ import annotations

import hashlib
import json
import shutil

import pytest

from aether.atelier import Atelier, Budget, Participant, mind_memory
from aether.atelier.place import Place
from aether.atelier.postmaster import Postmaster
from aether.mailbox import Mailbox
from aether.operations import distribution_preservation as backup
from tests.test_atelier_context_guard_offline import Strict
from tests.v01_preservation_support import installation  # noqa: F401  (the synthetic installation fixture)

OPERATOR = dict(id='operator', kind='operator', name='Operator')
CID = 'la'


def _atelier_data(inst, *, cartridge_elsewhere=None):
    """A mind and a share with real content under the installation's data root."""
    data = inst['data']
    share = data / 'at'
    mind = Participant.author(data / 'homes' / 'reader', id='reader', kind='member', name='Reader', system='You read.', mailboxes=share)
    if cartridge_elsewhere is not None:
        cartridge_elsewhere.mkdir(parents=True, exist_ok=True)
        shutil.move(str(mind.home / 'memory' / 'mind.sqlite'), str(cartridge_elsewhere / 'reader.sqlite'))
        (mind.home / 'memory' / '.cartridge.json').write_text(json.dumps(dict(path=str(cartridge_elsewhere / 'reader.sqlite'), owner='reader')), encoding='utf-8')
    atelier = Atelier(share, CID, budget=Budget(model_calls=10))
    atelier.open(OPERATOR, 'Read the ledger and report. Budget: 10 model calls.')
    box = Mailbox(share, mind.record())                       # the mailbox exists before the postmaster delivers
    post = Postmaster(atelier, share)
    post.exchange({mind.id: mind.kind}, {mind.id: mind.name})
    box.send('The ledger has 12 lines.', files={'report.txt': b'12 lines\n'}, cost=dict(model_calls=1, prompt_tokens=50, completion_tokens=10))
    post.collect([mind.id])
    for note in box.inbox(commission=CID):
        box.mark_read(note['id'])
    atelier.mark_read(mind.record())
    with mind_memory._open_read(mind.home) as engine:
        revision = engine.revision
    return dict(share=share, mind=mind, atelier=atelier, revision=revision,
                daybook_sha=hashlib.sha256((share / 'daybooks' / CID / 'daybook.jsonl').read_bytes()).hexdigest())


def _backup(inst, destination):
    return backup.backup(inst['app'], destination, mounts=inst['mounts'], owners=inst['owners'],
                         credential_dependencies=inst['credential_dependencies'])


def test_backup_covers_home_cartridge_daybook_and_mail(installation, tmp_path):
    _atelier_data(installation)
    result = _backup(installation, tmp_path / 'bk')
    manifest = backup.read_json(tmp_path / 'bk' / 'backup.json')
    files = manifest['files']
    assert files['data/homes/reader/memory/mind.sqlite']['kind'] == 'sqlite'
    assert manifest['cartridge_owners']['data/homes/reader/memory/mind.sqlite'] == dict(agent_id='reader', schema_version='4')
    for name in ('data/homes/reader/participant.json', 'data/homes/reader/identity/System_0.txt', 'data/homes/reader/memory/.cartridge.json',
                 f'data/at/daybooks/{CID}/daybook.jsonl', f'data/at/daybooks/{CID}/read-marks.json', 'data/at/mailboxes/reader/delivered.json'):
        assert name in files, name
    assert any(name.startswith(f'data/at/daybooks/{CID}/files/') for name in files)        # the stored report.txt
    assert any(name.startswith('data/at/mailboxes/reader/sent/') for name in files)
    backup.verify_backup(tmp_path / 'bk', expected_manifest_sha256=result['manifest_sha256'], app=installation['app'])


def test_cartridge_kept_elsewhere_needs_its_root(installation, tmp_path):
    external = tmp_path / 'minds'
    _atelier_data(installation, cartridge_elsewhere=external)
    with pytest.raises(ValueError, match='Referenced Atelier cartridge omitted'):
        _backup(installation, tmp_path / 'bk0')
    inst = dict(installation, mounts=dict(installation['mounts'], minds=external))
    result = _backup(inst, tmp_path / 'bk')
    manifest = backup.read_json(tmp_path / 'bk' / 'backup.json')
    assert manifest['cartridge_owners']['minds/reader.sqlite'] == dict(agent_id='reader', schema_version='4')
    backup.verify_backup(tmp_path / 'bk', expected_manifest_sha256=result['manifest_sha256'], app=inst['app'])


def test_pointer_whose_owner_differs_is_refused(installation, tmp_path):
    fx = _atelier_data(installation)
    record = fx['mind'].home / 'participant.json'
    record.write_text(record.read_text(encoding='utf-8').replace('"id": "reader"', '"id": "someone-else"'), encoding='utf-8')
    with pytest.raises(ValueError, match='owner differs from its participant record'):
        _backup(installation, tmp_path / 'bk')


def test_too_deep_a_destination_is_refused_before_anything_is_made(installation, tmp_path):
    _atelier_data(installation)
    deep = tmp_path / ('d' * max(1, 250 - len(str(tmp_path))))
    with pytest.raises(ValueError, match='too deep'):
        _backup(installation, deep)
    assert not deep.exists()


def test_restore_to_new_root_and_run_the_atelier_there(installation, tmp_path):
    fx = _atelier_data(installation)
    result = _backup(installation, tmp_path / 'bk')
    receipt = backup.restore(installation['app'], tmp_path / 'bk', tmp_path / 'rs', expected_manifest_sha256=result['manifest_sha256'])
    assert receipt['status'] == 'VERIFIED_STAGING_NOT_ACTIVE'
    share = tmp_path / 'rs' / 'data' / 'at'
    home = tmp_path / 'rs' / 'data' / 'homes' / 'reader'
    # The restored copy is whole and consistent before anything runs on it.
    restored = Atelier(share, CID)
    assert restored.daybook.verify()['ok']
    note = next(r for r in restored.daybook.read() if r['kind'] == 'message' and r['sender']['id'] == 'reader')
    assert restored.daybook.read_file(note['files'][0]['sha256']) == b'12 lines\n'
    mind = Participant.load(home)
    assert mind.home == home and mind.cartridge()['path'] == str(home / 'memory' / 'mind.sqlite')     # relocated with the home
    with mind_memory._open_read(home) as engine:
        assert engine.revision == fx['revision'] and engine.open_report.status == 'sealed'
    # The Atelier runs from the new root with no path from the old one: the operator speaks, the mind answers.
    restored.post(OPERATOR, 'Now count the USD lines.')
    client = Strict([dict(role='assistant', content='Nine USD lines.')], context_window=131072, max_tokens=500)
    place = Place(share, [home], name='restored', client=client, log=lambda *_: None)
    report = place.round()
    assert len(report['turns']) == 1 and not report['failed']
    rows = restored.daybook.read()
    assert rows[-1]['sender']['id'] == 'reader' and 'Nine USD lines.' in rows[-1]['text'] and restored.daybook.verify()['ok']
    with mind_memory._open_read(home) as engine:
        assert engine.revision >= fx['revision']
    # The originals are untouched.
    original = fx['share'] / 'daybooks' / CID / 'daybook.jsonl'
    assert hashlib.sha256(original.read_bytes()).hexdigest() == fx['daybook_sha']
