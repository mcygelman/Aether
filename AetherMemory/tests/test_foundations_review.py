"""Regression cases from the independent foundations reviews; disposable minds."""
import json
import sqlite3

import pytest

from memory_fabric import MemoryEngine
from memory_fabric import history, records, lifecycle, migrations
from memory_fabric.cartridge import fingerprint, content_fingerprint
from memory_fabric.errors import MemoryFabricError


def test_query_then_write_close_and_sealed_reopen(tmp_path):
    path = str(tmp_path / 'mind.sqlite')
    with MemoryEngine.create(path, 'owner') as e:
        assert e.query('SELECT 1')['rows'] == [[1]]
        with pytest.raises(MemoryFabricError):
            e.query('DELETE FROM notes')
        note = e.remember('After query', 'Still writable', 'project_fact')
    with MemoryEngine.open(path, read_only=True) as e:
        assert e.open_report.sealed and e.note(note)['body'] == 'Still writable'


def test_seed_only_tool_recall_and_partial_thread_edit(engine):
    tid = engine.workspace('create', dict(record_type='threads', title='Thread', body='History'))['id']
    assert engine.tools().run('memory_recall', {'seed_note_ids': [tid]})['ok']
    root = records.thread_edit(engine.cart, tid, note_id=tid)['element_id']
    child = records.thread_edit(engine.cart, tid, note_id=tid, parent_element_id=root, position=4, archived=True)['element_id']
    records.thread_edit(engine.cart, tid, element_id=child, position=7)
    row = engine.cart.conn.execute('SELECT * FROM thread_elements WHERE element_id=?', (child,)).fetchone()
    assert row['parent_element_id'] == root and row['archived_at'] and row['position'] == 7
    records.thread_edit(engine.cart, tid, element_id=child, archived=False)
    row = engine.cart.conn.execute('SELECT * FROM thread_elements WHERE element_id=?', (child,)).fetchone()
    assert row['parent_element_id'] == root and row['position'] == 7 and not row['archived_at']
    records.thread_edit(engine.cart, tid, element_id=child, parent_element_id=None)
    assert engine.cart.conn.execute('SELECT parent_element_id FROM thread_elements WHERE element_id=?', (child,)).fetchone()[0] is None


def test_authored_receipt_prefix_is_still_content(engine):
    records.create(engine.cart, 'projects', title='P', body='B', operation_id='lifecycle:authored')
    before = content_fingerprint(engine.cart.conn)
    # A receipt-only change remains in the reviewed content, despite its name.
    with engine.cart.transaction():
        engine.cart.conn.execute("UPDATE operation_receipts SET result=? WHERE operation_id='lifecycle:authored'", ('{}',))
    assert content_fingerprint(engine.cart.conn) != before


def test_duplicate_terminal_names_original_event(engine):
    engine.begin_session('work')
    scaffold = records.create(engine.cart, 'scaffolds', title='S', body='B')['id']
    run = engine.workspace('scaffold_start', dict(scaffold_id=scaffold, operation_id='start'))['event']['event_id']
    end = engine.workspace('scaffold_end', dict(run_id=run, outcome='completed', operation_id='end'))
    with pytest.raises(MemoryFabricError, match=end['event']['event_id']):
        engine.workspace('scaffold_end', dict(run_id=run, outcome='failed', operation_id='different'))
    with pytest.raises(MemoryFabricError, match='stable operation_id'):
        engine.workspace('project_work', dict(project_id='missing'))


def schema3(path):
    with MemoryEngine.create(str(path), 'owner', seed_resources=False) as e:
        note = e.remember('Original', 'Unchanged', 'project_fact')
    from memory_fabric.foundations_schema import AUTHORED_TABLES
    with sqlite3.connect(path) as c:
        for table in ('session_events', *reversed(AUTHORED_TABLES)):
            c.execute('DROP TABLE ' + table)
        c.execute("UPDATE meta SET value='3' WHERE key='schema_version'")
        c.execute("UPDATE meta SET value=? WHERE key='seal'", (fingerprint(c),))
    return note


def test_real_migration_seeds_before_read_only_capture(tmp_path):
    path = tmp_path / 'old.sqlite'
    note = schema3(path)
    report = migrations.migrate(str(path))
    with MemoryEngine.open(str(path), read_only=True) as e:
        assert e.open_report.sealed
        captured = content_fingerprint(e.cart.conn)
        assert e.note(note)['body'] == 'Unchanged'
        assert len(e.workspace('list', {'record_type': 'guides'})['records']) == 1
        assert not history.reconstruct(e.cart, 0)['guides']
    with MemoryEngine.open(str(path)) as e:
        assert content_fingerprint(e.cart.conn) == captured
    assert report.backup_path


def test_resource_failure_rolls_back_entire_migration(tmp_path, monkeypatch):
    from memory_fabric import workspace
    path = tmp_path / 'old.sqlite'
    schema3(path)
    def fail(cart):
        raise OSError('resource seeding failed')
    monkeypatch.setattr(workspace, 'seed', fail)
    with pytest.raises(OSError, match='resource seeding failed'):
        migrations.migrate(str(path))
    assert migrations.cartridge_version(str(path)) == 3
    with sqlite3.connect(path) as c:
        assert not c.execute("SELECT 1 FROM sqlite_master WHERE name='projects'").fetchone()
        assert fingerprint(c) == c.execute("SELECT value FROM meta WHERE key='seal'").fetchone()[0]


def test_viewer_rejects_foreign_host_and_distinguishes_bad_requests(tmp_path):
    from http.client import HTTPConnection
    from threading import Thread
    from memory_fabric.dashboard_server import make_server
    path = str(tmp_path / 'viewer.sqlite')
    with MemoryEngine.create(path, 'owner'):
        pass
    server = make_server(path)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        for route, host, expected in [('/', 'foreign.example', 403),
                ('/catalog?offset=invalid', None, 400), ('/catalog?type=invalid', None, 400),
                ('/note?id=missing', None, 404), ('/dashboard.json', None, 200)]:
            conn = HTTPConnection('127.0.0.1', server.server_port)
            try:
                conn.request('GET', route, headers={'Host': host} if host else {})
                response = conn.getresponse()
                assert response.status == expected
                response.read()
            finally:
                conn.close()
    finally:
        server.shutdown()
        thread.join()
        server.server_close()
