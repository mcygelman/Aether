"""Offline UI-to-place integration, including remote-role delivery and real library loan bytes."""
import json
import time
from pathlib import Path

import pytest

from aether.atelier import atelier_http, workplace_http
from aether.atelier.atelier import Atelier
from aether.atelier.place import Place
from aether.atelier.participants import Participant
from aether.atelier.postmaster import Postmaster
from aether.atelier.turn import Tools
from aether.atelier.workplace import Workplace
from aether.library import Library
from aether.mailbox import Mailbox
from test_atelier_http_offline import Handler, call


def make(tmp_path):
    w = Workplace(tmp_path / 'local' / 'connection.json')
    w.save(dict(share=str(tmp_path / 'share'), name='machine2'))
    return w


def test_join_persists_and_read_does_not_create_a_place(tmp_path):
    w = make(tmp_path)
    assert Workplace(w.path).config() == w.config()
    state = {'workplace': w}
    assert atelier_http.root_of(state) == tmp_path / 'share'
    assert not w.status()['running']
    assert not (tmp_path / 'share' / 'places').exists()
    for name in ('../bad', 'a/b', 'a.b'):
        with pytest.raises(ValueError):
            w.save(dict(share=str(tmp_path / 'share'), name=name))


def test_http_origin_and_machine_local_connection(tmp_path):
    w = make(tmp_path)
    state = {'workplace': w}
    handler = Handler('POST', '/api/places/start', {}, origin='http://elsewhere')
    assert workplace_http.handle_workplace(handler, 'POST', state)
    assert handler.status == 403 and not w.active()
    handler = Handler('GET', '/api/places')
    assert workplace_http.handle_workplace(handler, 'GET', state)
    assert handler.status == 200 and handler.sent['config']['name'] == 'machine2'
    code, opened, _ = call(state, 'POST', '/api/atelier/open', {'text': 'A library request. Budget: 12 model calls.'})
    assert code == 200
    cid = opened['commission_id']
    assert (tmp_path / 'share' / 'daybooks' / cid / 'daybook.jsonl').exists()
    code, value, _ = call(state, 'POST', '/api/atelier/wake', {'commission': cid, 'participant': {'id': 'librarian', 'kind': 'librarian'}})
    assert code == 200 and value['managed'] and not value['started']


def test_prepare_is_repeatable_and_preserves_existing_home(tmp_path):
    w = make(tmp_path)
    body = dict(endpoint='http://127.0.0.1:1235/v1', model='offline-fixture')
    w.prepare_library(body)
    home = Path(w.config()['homes'][0])
    original = (home / 'identity/System_0.txt').read_bytes()
    w.prepare_library(body)
    assert original == (home / 'identity/System_0.txt').read_bytes()
    with pytest.raises(ValueError):
        w.prepare_library(dict(body, model='changed'))
    assert original == (home / 'identity/System_0.txt').read_bytes()
    assert Library(tmp_path / 'share', 'lunch-check').verify()['ok']


def test_start_duplicate_stop_and_shutdown(tmp_path):
    w = make(tmp_path)
    try:
        assert w.start()['running']
        worker = w.worker
        assert w.start()['running'] and w.worker is worker
        with pytest.raises(ValueError):
            w.save(w.config())
        for _ in range(100):
            if w.status()['places']:
                break
            time.sleep(.01)
        second = Workplace(w.path)
        with pytest.raises(ValueError, match='already running'):
            second.start()
        w.stop()
    finally:
        assert w.close(10)
    assert not w.status()['running']
    assert w.status()['places'][0]['stopped']


def test_remote_librarian_role_invitation_and_downloadable_loan(tmp_path):
    w = make(tmp_path)
    w.prepare_library(dict(endpoint='http://127.0.0.1:9/v1', model='unused'))
    share = tmp_path / 'share'
    librarian = Participant.load(Path(w.config()['homes'][0]))
    remote = Place(share, [librarian.home], name='machine2')
    remote.heartbeat()
    box = Mailbox(share, librarian.record())
    operator_place = Place(share, [], name='machine1')
    operator_place.heartbeat()
    state = {'workplace': w}
    _, opened, _ = call(state, 'POST', '/api/atelier/open', {'text': 'Lend the library connection guide. Budget: 12 model calls.', 'request_kind': 'library'})
    cid = opened['commission_id']
    a = Atelier(share, cid)
    kinds, names = operator_place.delivery_directory()
    assert kinds[librarian.id] == 'librarian'
    post = Postmaster(a, share)
    spectator = Mailbox(share, dict(id='other-lead', kind='lead'))
    kinds['other-lead'] = 'lead'
    post.deliver(kinds, names)
    assert not spectator.inbox(commission=cid)
    assert any(n.get('to', {}).get('id') == 'librarian' for n in box.inbox(commission=cid) if n.get('to'))
    # Script the mind's tool choices, not the delivery: use the real tools, mailbox, daybook and HTTP download.
    root = share / 'work' / cid
    root.mkdir(parents=True)
    shelf = Library(share, 'lunch-check')
    tools = Tools(librarian, root, library=shelf, commission_id=cid)
    assert 'connection-guide' in tools.call('catalog_search', {'query': 'connection guide'})
    assert 'LUNCH-LIBRARY-01' in tools.call('shelf_read', {'item_id': 'connection-guide'})
    assert tools.call('shelf_lend', {'item_id': 'connection-guide', 'to': 'operator'}).startswith('lent ')
    box.send('The connection guide fits. LUNCH-LIBRARY-01.', files=tools.written, place='machine2')
    post.collect([librarian.id])
    note = next(n for n in a.daybook.read() if n['sender']['id'] == librarian.id)
    assert note['place'] == 'machine2' and note['files']
    ref = note['files'][0]
    _, _, handler = call(state, 'GET', f'/api/atelier/file?commission={cid}&sha256={ref["sha256"]}')
    assert handler.status == 200 and b'LUNCH-LIBRARY-01' in handler.wfile.getvalue()
    assert shelf.entry('connection-guide')['lends'] == 1
    assert shelf.verify()['ok']
    assert any(row['kind'] == 'lent' for row in shelf.ledger())
    assert a.daybook.verify()['ok']


def test_availability_is_separate_and_stale_work_is_not_active(tmp_path):
    w = make(tmp_path)
    state = {'workplace': w}
    _, opened, _ = call(state, 'POST', '/api/atelier/open', {'text': 'Lend a guide.', 'request_kind': 'library'})
    cid = opened['commission_id']
    remote = Place(tmp_path / 'share', [], name='remote')
    remote.current_turn = dict(commission=cid, participant='keeper')
    remote.heartbeat()
    _, view, _ = call(state, 'GET', f'/api/atelier/read?commission={cid}')
    assert view['availability']['running'] is False
    assert view['status']['label'] == 'Working' and view['working'] == ['keeper']
    record = json.loads(remote.record_path.read_text())
    record['heartbeat'] = '2000-01-01T00:00:00+00:00'
    remote.record_path.write_text(json.dumps(record))
    _, view, _ = call(state, 'GET', f'/api/atelier/read?commission={cid}')
    assert not view['working'] and view['status']['label'] == 'Waiting for reply'
    assert not view['availability']['places'][0]['alive']


def test_place_records_work_only_during_turn_and_clears_after_failure(tmp_path, monkeypatch):
    import aether.atelier.place as place_module
    w = make(tmp_path)
    w.prepare_library(dict(endpoint='http://127.0.0.1:9/v1', model='unused'))
    place = Place(tmp_path / 'share', w.config()['homes'], name='machine2', client=object())
    mind = place.minds[0]
    Mailbox(place.share, mind.record())
    _, opened, _ = call({'workplace': w}, 'POST', '/api/atelier/open', {'text': 'Find the guide.', 'request_kind': 'library'})
    cid = opened['commission_id']

    def failed(*args, **kwargs):
        assert place.record()['working'] == dict(commission=cid, participant=mind.id)
        raise RuntimeError('scripted failure, no model called')

    monkeypatch.setattr(place_module, 'run_turn', failed)
    report = place.round()
    assert len(report['failed']) == 1
    assert place.current_turn is None and place.record()['working'] is None


def test_atelier_server_serves_connection_and_shared_thread(tmp_path):
    import threading
    from urllib.request import Request, urlopen
    from aether.mission_control import atelier_server
    w = Workplace(tmp_path / '.aether_control' / 'workplace' / 'connection.json')
    w.save(dict(share=str(tmp_path / 'share'), name='machine2'))
    server = atelier_server.serve(tmp_path / 'Bridge', port=0, registry_path=tmp_path / 'projects.json',
                                  project_folders_root=tmp_path / 'Projects', minds_folder=tmp_path / 'minds',
                                  runtime_profiles_path=tmp_path / 'runtime_profiles.json')
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    base = f'http://127.0.0.1:{server.server_port}'
    try:
        with urlopen(base + '/projects/places') as response:
            assert b'Places &amp; libraries' in response.read()
        with urlopen(base + '/api/places') as response:
            assert json.load(response)['config']['share'] == str(tmp_path / 'share')
        request = Request(base + '/api/atelier/open', data=json.dumps({'text': 'Read the guide. Budget: 12 model calls.'}).encode(),
                          headers={'Origin': base, 'Content-Type': 'application/json'})
        with urlopen(request) as response:
            cid = json.load(response)['commission_id']
        with urlopen(base + '/api/atelier/read?commission=' + cid) as response:
            assert json.load(response)['places_managed'] is True
    finally:
        server.shutdown()
        thread.join(5)
        server.server_close()
