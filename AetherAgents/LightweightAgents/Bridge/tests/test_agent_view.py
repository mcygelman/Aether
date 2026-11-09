"""Operator agent views: cards, Welcome Dashboards and memory, read-only (MOD-12, offline)."""
import hashlib
import importlib.util
import json
import threading
from http.client import HTTPConnection
from pathlib import Path

import pytest

from aether.memory.agent_view import AgentDirectory, AgentViewError, UnknownAgent

TOOL = Path(__file__).resolve().parents[1] / 'tools' / 'agent_dashboards.py'


def tool():
    spec = importlib.util.spec_from_file_location('agent_dashboards_tool', TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def demo(tmp_path):
    folder = tool().build_demo(tmp_path / 'demo')
    return folder, AgentDirectory([('Demo', folder)])


def digests(folder):
    return {p.relative_to(folder).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(folder.rglob('*')) if p.is_file()}


def test_cards_come_from_each_agents_own_record(demo):
    _, directory = demo
    cards = {c['key']: c for c in directory.agents()}
    assert set(cards) == {'Atlas', 'Juniper', 'Wren'}
    wren = cards['Wren']
    assert wren['title'] == 'Keeps the long thread of a project'
    assert wren['avatar_uri'].startswith('data:image/svg+xml;base64,')
    assert wren['rank']['level'] >= 1 and wren['rank']['specialty'] == 'code review'
    assert wren['last_project']['title'] == 'Harbor Ledger'
    atlas = cards['Atlas']
    assert atlas['title'] is None and atlas['avatar_uri'] is None   # nothing invented
    assert atlas['rank'] == {'level': 0, 'label': 'Unranked', 'specialty': None,
                             'basis': 'No outcome verified by someone else yet.'}


def test_current_dashboard_uses_the_latest_session_context(demo):
    _, directory = demo
    wren = {e['name']: e for e in directory.dashboard('Wren')['elements']}
    assert wren['previous_closure']['value']['state'] == 'PASS'
    assert wren['next_self_note']['value']['text'].startswith('Next self: March is reconciled')
    assert wren['last_executed_scaffold']['value']['title'] == 'Reconcile March statements'
    assert wren['last_unexecuted_scaffold']['value']['title'] == 'Draft the April operator guide'
    atlas = {e['name']: e for e in directory.dashboard('Atlas')['elements']}
    assert atlas['previous_closure']['status'] == 'unknown'
    assert atlas['previous_closure']['value']['latest_verified_closure']['state'] == 'PASS'
    assert 'predecessor unknown' in atlas['next_self_note']['title']
    juniper = {e['name']: e for e in directory.dashboard('Juniper')['elements']}
    assert juniper['previous_closure']['status'] == 'empty'
    assert juniper['guides']['value']['total'] == 1
    none = directory.dashboard('Wren', 'none')
    assert none['viewing']['session_id'] is None
    with pytest.raises(AgentViewError, match='not an opening'):
        directory.dashboard('Wren', 'event-nope')
    with pytest.raises(UnknownAgent):
        directory.dashboard('Nobody')


def test_viewing_changes_no_byte_of_any_memory(demo):
    folder, directory = demo
    before = digests(folder)
    directory.agents()
    for key in ('Wren', 'Atlas', 'Juniper'):
        directory.dashboard(key)
        directory.sessions(key)
        directory.history(key)
        assert 'Memory Fabric' in directory.memory_page(key)
    note = directory.dashboard('Wren')
    scaffold = {e['name']: e for e in note['elements']}['last_executed_scaffold']['value']
    assert directory.note('Wren', scaffold['scaffold_id'], scaffold['snapshot_revision'])['title'] == 'Reconcile March statements'
    assert digests(folder) == before


def test_problems_are_reported_on_the_card_not_raised(tmp_path):
    broken = tmp_path / 'agents' / 'Lost'
    (broken / 'memory').mkdir(parents=True)
    (broken / 'memory/.cartridge.json').write_text(json.dumps({'path': 'missing.sqlite'}), encoding='utf-8')
    (tmp_path / 'agents' / 'NotAnAgent').mkdir()
    cards = AgentDirectory([tmp_path / 'agents']).agents()
    assert [c['key'] for c in cards] == ['Lost']
    assert cards[0]['status'] == 'unavailable' and 'not found' in cards[0]['reason']


def test_http_routes_are_get_only_local_and_typed(demo):
    _, directory = demo
    server = tool().make_server(directory)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_port

    def call(path, method='GET', host=None):
        conn = HTTPConnection('127.0.0.1', port, timeout=30)
        try:
            conn.request(method, path, headers={'Host': host or f'127.0.0.1:{port}'})
            response = conn.getresponse()
            policies.append(response.getheader('Content-Security-Policy'))
            return response.status, response.getheader('Content-Type'), response.read()
        finally:
            conn.close()
    policies = []
    try:
        status, kind, body = call('/agents')
        assert status == 200 and kind.startswith('text/html') and b'Agents' in body
        # Avatars are data: URIs; the page policy must let them show (the browser blocked them under default-src).
        assert "img-src 'self' data:" in policies[-1] and "default-src 'self'" in policies[-1]
        status, _, body = call('/api/agents')
        assert status == 200 and len(json.loads(body)['agents']) == 3
        assert call('/api/agents/dashboard?agent=Wren')[0] == 200
        assert call('/api/agents/dashboard?agent=Nobody')[0] == 404
        assert call('/api/agents/dashboard')[0] == 400
        status, _, body = call('/api/agents/history?agent=Wren')
        assert status == 200 and json.loads(body)['events']
        assert call('/api/agents/history?agent=Wren&before=0')[0] == 400
        assert call('/api/agents/history?agent=Wren&before=nope')[0] == 400
        assert call('/api/agents/history?agent=Wren&before=9999999999999999999999999999')[0] == 400
        assert call('/api/agents/history?agent=Nobody')[0] == 404
        assert call('/api/agents/history?agent=Wren', method='POST')[0] == 405
        assert call('/api/agents/note?agent=Wren&id=n-missing')[0] == 409
        status, kind, body = call('/api/agents/memory?agent=Wren')
        assert status == 200 and kind.startswith('text/html')
        assert call('/api/agents', method='POST')[0] == 405
        assert call('/api/agents', host='evil.example:80')[0] == 403
    finally:
        server.shutdown()
        server.server_close()
        thread.join(10)


@pytest.mark.parametrize('content', ['[]', 'null', '42', '"pointer"', '{"path": []}',
                                    '{"path": 5}', '{"path": null}', '{"path": " "}',
                                    '{"path": "bad\\u0000path"}', '{invalid'])
def test_malformed_pointer_does_not_hide_healthy_agents(demo, content):
    folder, directory = demo
    pointer = folder / 'Malformed/memory/.cartridge.json'
    pointer.parent.mkdir(parents=True)
    pointer.write_text(content, encoding='utf-8')
    cards = {c['key']: c for c in directory.agents()}
    assert cards['Malformed']['status'] == 'unavailable'
    assert 'pointer unreadable' in cards['Malformed']['reason']
    assert all(cards[key]['status'] == 'ok' for key in ('Wren', 'Atlas', 'Juniper'))
    assert directory.dashboard('Wren')['viewing']['agent'] == 'Wren'


def test_old_session_is_explicitly_current_projection(demo):
    from aether.memory.fabric import open_engine
    from aether.memory.foundations import begin
    folder, directory = demo
    before = directory.dashboard('Wren')
    sid = before['viewing']['session_id']
    with open_engine(folder / 'Wren/memory/mind.sqlite') as engine:
        begin(engine, 'later-work', source_system='test')
        project = engine.workspace('create', {'record_type': 'projects', 'title': 'Later project',
            'body': 'Created after the selected session.', 'operation_id': 'later-project'})['id']
        engine.workspace('project_work', {'project_id': project, 'operation_id': 'later-focus'})
    after = directory.dashboard('Wren', sid)
    assert 'Live view of its notes' in after['viewing']['basis']
    assert after['viewing']['session_id'] == sid
    assert {e['name']: e for e in after['elements']}['last_project']['value']['title'] == 'Later project'
    assert 'Live view of its notes' in directory.dashboard('Wren')['viewing']['basis']
    assert 'without session context' in directory.dashboard('Wren', 'none')['viewing']['basis']


def test_history_pages_cover_all_events_despite_new_appends(demo):
    from aether.memory.fabric import open_engine
    from aether.memory.foundations import begin
    folder, directory = demo
    path = folder / 'Atlas/memory/mind.sqlite'
    with open_engine(path) as engine:
        for n in range(65):
            begin(engine, f'lost-{n}', source_system='test')
        expected = [r[0] for r in engine.cart.conn.execute('SELECT event_seq FROM session_events ORDER BY event_seq DESC')]
    before_read = digests(folder)
    first = directory.history('Atlas')
    assert len(first['events']) == 50
    assert first['next_before'] is not None
    assert digests(folder) == before_read
    with open_engine(path) as engine:
        begin(engine, 'new-during-pagination', source_system='test')
    before_read = digests(folder)
    rest = directory.history('Atlas', first['next_before'])
    assert rest['next_before'] is None
    assert [e['event_seq'] for e in first['events'] + rest['events']] == expected
    assert digests(folder) == before_read
    assert directory.history('Juniper')['events'] == []
    value = {e['name']: e for e in directory.dashboard('Atlas')['elements']}['previous_closure']['value']
    assert value['unresolved_count'] > 3 and len(value['unresolved_sessions']) == 3
