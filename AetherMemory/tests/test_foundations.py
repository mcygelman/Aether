"""Schema 4 vertical slice: history, retries, lifecycle, graph and both renderers."""
import hashlib
import json
import sqlite3
import threading
from urllib.request import build_opener, ProxyHandler

import pytest

from memory_fabric import MemoryEngine
from memory_fabric import dashboard, history, lifecycle, migrations, records, schema
from memory_fabric.dashboard_server import make_server
from memory_fabric.errors import MemoryFabricError


def create(engine, record_type, title='A record', **kw):
    return engine.workspace('create', dict(record_type=record_type, title=title, body='Authored content', **kw))['id']


def test_atomic_facet_retry_and_multifacet_history(engine):
    prior = engine.revision
    args = dict(record_type='projects', title='A project', body='Purpose', operation_id='p1', tags=['work'])
    a = engine.workspace('create', args)
    assert engine.workspace('create', args) == a
    assert engine.revision == prior + 1
    engine.workspace('create', dict(record_type='threads', note_id=a['id'], operation_id='t1'))
    assert engine.workspace('inspect', dict(record_type='threads', record_id=a['id']))['note']['body'] == 'Purpose'
    assert history.reconstruct(engine.cart, prior)['projects'] == []
    before = engine.revision
    with pytest.raises((MemoryFabricError, sqlite3.IntegrityError)):
        create(engine, 'projects', fields={'external_project_id': 'bad'})
    assert engine.revision == before
    with pytest.raises(MemoryFabricError):
        engine.workspace('create', dict(args, body='Changed retry'))


def test_host_events_do_not_stale_owner_tools(engine):
    from memory_fabric.cartridge import fingerprint, content_fingerprint
    a = engine.remember('A', 'A', 'project_fact')
    b = engine.remember('B', 'B', 'project_fact')
    tools = engine.tools()
    prior = engine.revision
    seal, content = fingerprint(engine.cart.conn), content_fingerprint(engine.cart.conn)
    sid = engine.begin_session('logical-1')
    assert engine.begin_session('logical-1') == sid
    assert engine.revision == prior
    assert fingerprint(engine.cart.conn) != seal
    assert content_fingerprint(engine.cart.conn) == content
    assert tools.run('memory_link', {'from_id': a, 'to_id': b})['ok']


def test_scaffold_content_attempts_and_closure_are_independent(engine):
    sid = engine.begin_session('one')
    first = create(engine, 'scaffolds', 'First')
    other = create(engine, 'scaffolds', 'Other')
    request = dict(scaffold_id=first, operation_id='attempt-1')
    start = engine.workspace('scaffold_start', request)
    run = start['event']['run_id']
    end = engine.workspace('scaffold_end', dict(run_id=run, outcome='completed', operation_id='end-1'))
    assert engine.workspace('scaffold_start', request) == start
    assert engine.workspace('scaffold_end', dict(run_id=run, outcome='completed', operation_id='end-1')) == end
    before = lifecycle.scaffold_fingerprint(engine.cart, first)['sha256']
    engine.load([first])
    engine.remember('Unrelated', 'No changes to instructions', 'scratch')
    assert lifecycle.scaffold_fingerprint(engine.cart, first)['sha256'] == before
    assert lifecycle.scaffold_status(engine.cart)['executed']['scaffold_id'] == first
    engine.workspace('revise', dict(record_type='scaffolds', record_id=first, note={'body': 'Revised instructions'}))
    assert lifecycle.scaffold_fingerprint(engine.cart, first)['sha256'] != before
    assert engine.workspace('scaffold_start', request) == start  # lost start reply, later content edit
    with pytest.raises(MemoryFabricError):
        lifecycle.append(engine.cart, 'closed', sid, source_system='host', source_event_id='close', closure_id='c')
    status = lifecycle.scaffold_status(engine.cart)
    assert status['executed']['current_content_differs']
    assert status['unexecuted']['scaffold_id'] in (first, other)


def test_closure_unknown_failure_recovery_and_predecessor(engine):
    old = engine.begin_session('old')
    new = engine.begin_session('new', previous_session_id=old)
    assert lifecycle.previous_closure(engine.cart, new)['value']['state'] == 'UNKNOWN'
    lifecycle.append(engine.cart, 'closure_failed', old, source_system='host', source_event_id='failed',
                     closure_id='c', summary='cartridge filing failed')
    assert lifecycle.previous_closure(engine.cart, new)['value']['state'] == 'FAILED'
    lifecycle.append(engine.cart, 'closed', old, source_system='host', source_event_id='closed',
                     closure_id='c', verified=True, evidence_ref='retained-state', evidence_sha256='a' * 64)
    result = lifecycle.previous_closure(engine.cart, new)['value']
    assert result['state'] == 'PASS' and result['recovered']
    unknown = engine.begin_session('no-lineage')
    assert lifecycle.previous_closure(engine.cart, unknown)['status'] == 'unknown'


def test_thread_spine_branch_choices_cycles_and_repeat_notes(engine):
    tid = create(engine, 'threads')
    note = engine.remember('Evidence', 'One note can recur', 'standing_lesson')
    root = engine.workspace('thread_edit', dict(thread_id=tid, note_id=note))['element_id']
    leaves = [engine.workspace('thread_edit', dict(thread_id=tid, note_id=note, parent_element_id=root))['element_id'] for _ in range(2)]
    assert len(engine.workspace('thread_read', dict(thread_id=tid, spine=True))['branch_choices']) == 2
    spine = engine.workspace('thread_read', dict(thread_id=tid, spine=True, leaf_element_id=leaves[0]))
    assert [r['element_id'] for r in spine['elements']] == [root, leaves[0]]
    with pytest.raises(MemoryFabricError):
        engine.workspace('thread_edit', dict(thread_id=tid, element_id=root, parent_element_id=leaves[0]))


def test_explicit_buried_seed_and_cross_project_association(engine):
    a, b = create(engine, 'projects', 'A'), create(engine, 'projects', 'B')
    lesson = engine.remember('Lesson', 'Shared', 'core_knowledge')
    engine.link(a, lesson, strength=.95)
    engine.link(lesson, b, strength=.95)
    engine.bury(a)
    before = engine.revision
    result = engine.recall([], seed_note_ids=[a])
    assert lesson in result.ids() and b in result.ids() and a not in result.ids()
    assert result.seed_note_ids == (a,)
    assert engine.revision == before


def test_dashboard_default_customization_unknown_provider_and_read_only(engine):
    before = engine.revision
    assert engine.workspace('layout')['origin'] == 'builtin'
    snap = engine.dashboard()
    assert engine.revision == before
    names = {e['name'] for e in snap['elements']}
    assert {'last_executed_scaffold', 'last_unexecuted_scaffold', 'guides', 'teachframes'} <= names
    elements = engine.workspace('layout')['elements'] + [dict(name='future', source_kind='provider', provider_key='future-provider')]
    engine.workspace('dashboard_edit', dict(elements=elements, operation_id='layout1'))
    assert engine.workspace('layout')['origin'] == 'stored'
    before = engine.revision
    snap = engine.dashboard()
    assert snap['elements'][-1]['status'] == 'unavailable'
    assert engine.revision == before
    rendered = dashboard.render_html(snap)
    assert 'Last Executed Scaffold' in rendered


def test_queries_are_real_reads_and_cannot_write(engine):
    catalog = engine.workspace('schema')
    assert 'projects' in catalog['tables']
    assert engine.workspace('schema', {'table': 'projects'})['foreign_keys'][0]['table'] == 'notes'
    assert engine.query('WITH x AS (SELECT title FROM notes) SELECT * FROM x', limit=1)['next_offset'] == 1
    before = engine.revision
    for sql in ['DELETE FROM notes', 'PRAGMA user_version=42', "ATTACH DATABASE ':memory:' AS other", 'CREATE TEMP TABLE leak(x)', "SELECT load_extension('x')"]:
        with pytest.raises(MemoryFabricError):
            engine.query(sql)
    assert engine.revision == before
    assert engine.query('SELECT count(*) FROM guides')['rows'] == [[1]]


def test_workspace_write_overflow_never_reports_committed_work_as_failure(engine):
    from memory_fabric.belt import MemoryBelt
    from test_belt_and_hydration import FakeBridgeBelt
    engine.begin_session('one')
    scaffold = create(engine, 'scaffolds', source_ref='file:' + 'x' * 1000, fields={'source_version': 'v1'})
    belt = MemoryBelt(FakeBridgeBelt(max_result_chars=200), engine.tools())
    result = belt.dispatch('memory_workspace', {'action': 'scaffold_start', 'arguments_json': json.dumps({
        'scaffold_id': scaffold, 'operation_id': 'large-event'})})
    assert result['ok'] and 'error' not in result and len(json.dumps(result)) <= 200
    assert engine.query("SELECT count(*) FROM session_events WHERE kind='scaffold_started'")['rows'] == [[1]]


def test_supplied_resources_survive_edit_and_reopen(tmp_path):
    path = str(tmp_path / 'mind.sqlite')
    with MemoryEngine.create(path, 'owner') as engine:
        guide = engine.workspace('list', dict(record_type='guides'))['records'][0]['guide_id']
        engine.workspace('revise', dict(record_type='guides', record_id=guide, note={'body': 'My adaptation'}))
    with MemoryEngine.open(path) as engine:
        assert engine.note(guide)['body'] == 'My adaptation'
        assert engine.workspace('list', dict(record_type='guides'))['total'] == 1
    with MemoryEngine.open(path, read_only=True) as engine:
        assert engine.open_report.status == 'sealed'


def test_readonly_http_refresh_uses_same_projection(tmp_path):
    path = str(tmp_path / 'mind.sqlite')
    with MemoryEngine.create(path, 'owner') as engine:
        sid = engine.begin_session('one')
    before = hashlib.sha256(open(path, 'rb').read()).hexdigest()
    server = make_server(path, session_id=sid)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        root = f'http://127.0.0.1:{server.server_port}'
        opener = build_opener(ProxyHandler({}))
        with opener.open(root + '/dashboard.json') as response:
            data = json.load(response)
        with opener.open(root + '/') as response:
            assert 'Last Unexecuted Scaffold' in response.read().decode()
        nid = next(e for e in data['elements'] if e['name'] == 'guides')['value']['records'][0]['guide_id']
        with opener.open(root + '/note?id=' + nid) as response:
            assert 'Aether First Principles' in response.read().decode()
        with MemoryEngine.open(path, read_only=True) as engine:
            assert data['elements'][0]['status'] == engine.dashboard(session_id=sid)['elements'][0]['status']
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    assert hashlib.sha256(open(path, 'rb').read()).hexdigest() == before


def test_schema3_migration_preserves_notes_and_seal(tmp_path):
    from memory_fabric.cartridge import fingerprint
    from memory_fabric.foundations_schema import TABLES
    path = str(tmp_path / 'legacy.sqlite')
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    # Real schema-3 tables; do not seed a current cartridge and fake its version.
    for statement in schema.DDL:
        if statement in __import__('memory_fabric.foundations_schema', fromlist=['DDL']).DDL:
            continue
        conn.execute(statement)
    conn.executemany('INSERT INTO meta VALUES (?,?)', [('schema_version', '3'), ('agent_id', 'owner'), ('open_sessions', '0')])
    conn.execute("INSERT INTO meta VALUES ('seal',?)", (fingerprint(conn),))
    conn.commit()
    conn.close()
    report = migrations.migrate(path)
    assert report.from_version == 3 and report.to_version == 4 and report.backup_path
    with MemoryEngine.open(path) as engine:
        assert set(TABLES) <= {r[0] for r in engine.cart.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert engine.workspace('list', dict(record_type='guides'))['total'] == 1


def test_dashboard_reset_is_explicit_and_reversible_history(engine):
    engine.workspace('dashboard_edit', dict(title='My welcome', operation_id='edit'))
    prior = engine.revision
    engine.workspace('dashboard_edit', dict(reset=True, operation_id='reset'))
    assert engine.dashboard()['origin'] == 'builtin'
    assert history.reconstruct(engine.cart, prior)['dashboards'][0]['title'] == 'My welcome'


def test_external_scaffold_edition_is_part_of_execution_identity(engine):
    engine.begin_session('one')
    sid = create(engine, 'scaffolds', source_ref='file:instructions.txt')
    with pytest.raises(MemoryFabricError, match='edition'):
        engine.workspace('scaffold_start', dict(scaffold_id=sid, operation_id='run'))
    engine.workspace('revise', dict(record_type='scaffolds', record_id=sid, fields={'source_version': 'v1'}))
    start = engine.workspace('scaffold_start', dict(scaffold_id=sid, operation_id='run'))
    engine.workspace('scaffold_end', dict(run_id=start['event']['run_id'], outcome='completed', operation_id='end'))
    assert lifecycle.scaffold_status(engine.cart)['unexecuted'] is None
    engine.workspace('revise', dict(record_type='scaffolds', record_id=sid, fields={'source_version': 'v2'}))
    assert lifecycle.scaffold_status(engine.cart)['unexecuted']['scaffold_id'] == sid
    assert 'body' not in start['event']['details']['source']
