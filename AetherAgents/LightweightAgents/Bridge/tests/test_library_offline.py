"""MOD-20 Aether Library, offline: file, search, lend, deposit, decline, verify, tamper reported (Fable, 2026-10-18)."""
from pathlib import Path

import pytest

from aether.library import Library, LibraryError

GUIDE = dict(id='harbor-guide', kind='member', name='Harbor Guide')
LIBRARIAN = dict(id='librarian', kind='librarian', name='Librarian')


def test_a_collection_is_filed_searched_lent_and_verified(tmp_path):
    lib = Library(tmp_path, 'teachframes', title='TeachFrames')
    lib.add('tf-attachment', title='Attachment preserved through motion', kind='teachframe',
            summary='A part placed with a transform and animated through the same channel loses its placement.',
            files={'TEACHFRAME_Attachment.md': b'# attachment\nplace on an outer group, animate an inner one\n'},
            tags={'svg', 'animation'}, by=LIBRARIAN)
    lib.add('svgcat-m01', title='Groktest.html (Grok)', kind='example', summary='Body hovers above the rail; legs rotate about the canvas.',
            files={'Groktest.html': b'<svg></svg>'}, tags={'svg-cat'}, related=['tf-attachment'], by=LIBRARIAN)
    hits = lib.search('legs rotate rail svg')
    assert [h['id'] for h in hits][:1] == ['svgcat-m01'] and hits[0]['score'] > 0
    assert [h['id'] for h in lib.search('placement transform', kind='teachframe')] == ['tf-attachment']
    assert lib.search('anything', tags=['nope']) == []
    lent = lib.lend('tf-attachment', GUIDE, tmp_path / 'work' / 'c1' / 'library')
    assert (Path(lent['into']) / 'TEACHFRAME_Attachment.md').read_bytes().startswith(b'# attachment')
    assert lib.entry('tf-attachment')['lends'] == 1 and lib.entry('tf-attachment')['lent_to'][0]['to']['id'] == 'harbor-guide'
    assert lib.read('svgcat-m01') == b'<svg></svg>'
    kinds = [r['kind'] for r in lib.ledger()]
    assert kinds == ['created', 'filed', 'filed', 'lent']
    assert lib.verify()['ok'] is True
    with pytest.raises(LibraryError):
        lib.add('tf-attachment', title='again', kind='teachframe')
    with pytest.raises(LibraryError):
        lib.add('x', title='x', kind='not-a-kind')


def test_a_deposit_waits_until_the_librarian_files_or_declines(tmp_path):
    lib = Library(tmp_path, 'teachframes')
    d1 = lib.deposit(GUIDE, {'critique.md': b'the legs float\n'}, 'my critique of M04', commission='harbor-ledger-c')
    d2 = lib.deposit(GUIDE, {'junk.bin': b'\x00\x00'}, 'not sure what this is')
    assert [d['id'] for d in lib.deposits()] == sorted([d1['id'], d2['id']])
    filed = lib.file(d1['id'], item_id='svgcat-critique-m04', title='Critique of M04', kind='critique',
                     summary='The legs float above the rail.', tags={'svg-cat'}, by=LIBRARIAN)
    assert filed['source']['deposit'] == d1['id'] and filed['source']['commission'] == 'harbor-ledger-c'
    assert (lib.items_dir / 'svgcat-critique-m04' / 'critique.md').read_bytes() == b'the legs float\n'
    declined = lib.decline(d2['id'], 'two zero bytes is not an item', by=LIBRARIAN)
    assert Path(declined['at']).exists() and lib.deposits() == []
    kinds = [r['kind'] for r in lib.ledger()]
    assert kinds == ['created', 'deposited', 'deposited', 'filed', 'declined']
    assert lib.verify()['ok'] is True
    assert Library.all_under(tmp_path) == ['teachframes']


def test_tampering_is_reported_not_blocked(tmp_path):
    lib = Library(tmp_path, 'c')
    lib.add('one', title='One', kind='note', files={'a.txt': b'a'}, by=LIBRARIAN)
    (lib.items_dir / 'one' / 'a.txt').write_bytes(b'b')
    report = lib.verify()
    assert report['ok'] is False and report['files_changed_or_missing'] == [dict(item='one', file='a.txt')]
    lines = lib.ledger_path.read_text(encoding='utf-8').splitlines()
    lines[0] = lines[0].replace('"created"', '"edited"')
    lib.ledger_path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    report = lib.verify()
    assert report['first_problem'] == dict(n=1, what='chain')
    assert lib.search('one')                       # the shelf still answers
