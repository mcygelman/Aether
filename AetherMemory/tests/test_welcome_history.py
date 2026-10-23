"""Welcome excerpts stay small while the complete journal stays inspectable."""
import hashlib
import threading
from urllib.request import build_opener, ProxyHandler
from memory_fabric import MemoryEngine, lifecycle
from memory_fabric.dashboard import render_text, render_html
from memory_fabric.dashboard_server import make_server


def test_large_history_has_exact_counts_recent_references_and_full_read(engine):
    opened = [engine.begin_session(str(i)) for i in range(200)]
    current = engine.begin_session('current')
    before = engine.revision
    value = lifecycle.previous_closure(engine.cart, current)['value']
    assert value['candidate_count'] == value['unresolved_count'] == 200
    assert value['candidates'] == value['unresolved_sessions'] == opened[-3:][::-1]
    result = engine.dashboard(session_id=current)
    text = render_text(result)
    assert opened[0] not in text and opened[-1] in text
    assert len(text) < 7000  # Regression against 36K; not a product token budget.
    assert '200 sessions without a recorded close' in render_html(result)
    assert '/history' in render_html(result)
    route = value['history_read']
    first = engine.query(route['sql'], limit=100)
    second = engine.query(route['sql'], offset=first['next_offset'], limit=100)
    third = engine.query(route['sql'], offset=second['next_offset'], limit=100)
    assert len(first['rows'] + second['rows'] + third['rows']) == 201
    assert third['next_offset'] is None and engine.revision == before
    engine.begin_session('known', previous_session_id=opened[-1])
    known = lifecycle.previous_closure(engine.cart, engine.logical_session_id)['value']
    assert known['session_id'] == opened[-1] and known['unresolved_count'] == 201
    assert len(known['unresolved_sessions']) == 3


def test_operator_can_page_full_history_without_writes(tmp_path):
    path = tmp_path/'mind.sqlite'
    with MemoryEngine.create(str(path), 'owner') as e:
        opened = [e.begin_session(str(i)) for i in range(55)]
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    server = make_server(str(path))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        opener = build_opener(ProxyHandler({}))
        url = f'http://127.0.0.1:{server.server_port}'
        with opener.open(url+'/history') as response:
            page = response.read().decode()
            assert opened[-1] in page and opened[0] not in page and '/history?offset=50' in page
        with opener.open(url+'/history?offset=50') as response:
            page = response.read().decode()
            assert opened[0] in page and 'Next page' not in page
    finally:
        server.shutdown()
        thread.join()
        server.server_close()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
