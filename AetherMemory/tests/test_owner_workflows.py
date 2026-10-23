"""Owner-facing schema/dispatch, history reconstruction, crash and recovery proofs."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from memory_fabric import MemoryEngine, schema
from memory_fabric import history, fabric
from memory_fabric.cartridge import fingerprint
from memory_fabric.errors import MemoryFabricError
from memory_fabric.recovery import restore_backup
from memory_fabric.tools import ToolLimits


def change(engine, action, **args):
    return engine.tools().dispatch('memory_maintain', dict(
        action=action, expected_revision=engine.revision, reason='owner decision', **args))


def test_owner_workflow_reconstruction_and_reversal(engine, clock):
    engine.bind_session('grace', 'session-one')
    tools = engine.tools(ToolLimits(max_result_chars=20000))
    a = tools.dispatch('memory_remember', dict(title='Pending release', body='not committed',
        kind='project fact', tags=['pending'], operation_id='create'))['id']
    b = engine.remember('Evidence', 'operator closed milestone', 'lesson')
    engine.link(a, b)
    before = engine.revision
    original = engine.maintain(action='inspect', id=a)
    result = change(engine, 'revise', id=a, title='Closed release', body='committed and closed', tags=['closed'])
    assert engine.recall(['pending']).ids() == []
    assert engine.recall(['closed']).ids()[0] == a
    old = tools.dispatch('memory_maintain', dict(action='reconstruct', id=a, at_revision=before))
    assert old['note']['body'] == 'not committed' and len(old['links']) == 1
    log = engine.maintain(action='history')['changes'][-1]
    assert log['actor'] == 'grace' and log['session'] == 'session-one'
    assert 'not committed' not in json.dumps(log)
    assert 'not committed' in json.dumps(history.payload(engine.cart, log['revision']))
    change(engine, 'unlink', id=a, to_id=b)
    assert not engine.maintain(action='inspect', id=a)['note']['links']
    change(engine, 'restore', id=a, at_revision=before)
    now = engine.maintain(action='inspect', id=a)
    assert now['note'] == original['note'] and now['revision'] > result['revision']


def test_properties_refresh_and_control_of_reinforcement(engine, clock):
    a = engine.remember('Commitment', 'still needed', 'todo')
    b = engine.remember('Unrelated', 'comparison', 'fact')
    engine.link(a, b, strength=.4)
    tools = engine.tools()
    rev = engine.revision
    tools.dispatch('memory_load', {'ids': [a,b], 'refresh': False})
    assert engine.revision == rev
    tools.dispatch('memory_load', {'ids': [a,b], 'reinforce_links': False})
    assert engine.cart.conn.execute('SELECT reinforce_count FROM links').fetchone()[0] == 0
    change(engine, 'properties', id=a, importance='high', current_for_days=120, delta=.1, fade_days=20)
    clock.advance(50)
    change(engine, 'refresh', id=a)
    assert engine.note(a)['strength'] >= .85
    change(engine, 'link', id=a, to_id=b, strength='weak')
    assert engine.cart.conn.execute('SELECT strength FROM links').fetchone()[0] == .4


def test_merge_retains_prior_pattern_and_requires_deliberate_body(engine):
    a = engine.remember('Current', 'new evidence', 'lesson')
    b = engine.remember('Earlier', 'old account', 'lesson')
    c = engine.remember('Related', 'context', 'lesson')
    engine.link(b,c)
    prior = engine.revision
    change(engine, 'merge', id=a, source_ids=[b], body='owner reconciliation')
    assert engine.note(b)['strength'] == .1
    assert b not in engine.recall(['Earlier']).ids()
    links = engine.maintain(action='inspect', id=a)['note']['links']
    assert any(c in (l['a'],l['b']) for l in links)
    state = history.reconstruct(engine.cart, prior)
    assert state['links'][0]['a'] == b
    assert next(n for n in state['notes'] if n['note_id']==b)['body'] == 'old account'
    change(engine, 'restore', id=b, at_revision=prior)
    assert engine.note(b)['strength'] > .1


def test_stale_second_session_and_exact_retry(tmp_path):
    path = str(tmp_path/'mind.sqlite')
    with MemoryEngine.create(path, 'owner') as first:
        a = first.remember('Fact', 'v1', 'fact')
        with MemoryEngine.open(path) as second:
            revision = second.revision
            args = dict(action='revise', id=a, body='v2', reason='evidence',
                        expected_revision=revision, operation_id='write-v2')
            receipt = first.tools().dispatch('memory_maintain', args)
            error = second.tools().run('memory_maintain', {**args, 'body':'stale','operation_id':'other'})
            assert not error['ok'] and 'stale revision' in error['text']
            assert second.tools().dispatch('memory_maintain', args) == receipt
            assert second.note(a)['body'] == 'v2'
            assert second.revision == receipt['revision']
            assert not second.tools().run('memory_maintain', {**args, 'body':'different'})['ok']


@pytest.mark.parametrize('failure', ['link', 'history'])
def test_failure_rolls_back_content_relationships_and_history(engine, monkeypatch, failure):
    b = engine.remember('Existing', 'preserve', 'fact')
    before = fingerprint(engine.cart.conn)
    if failure == 'link':
        def fail(*a, **kw):
            raise OSError('injected relationship write failure')
        monkeypatch.setattr(fabric, 'link', fail)
    else:
        original = history.record
        def fail(*a, **kw):
            original(*a, **kw)
            raise OSError('injected failure after history insert')
        monkeypatch.setattr(history, 'record', fail)
    with pytest.raises(OSError):
        engine.remember('Must roll back', 'new', 'fact', links=[(b,'context')])
    assert fingerprint(engine.cart.conn) == before


def test_reformation_io_failure_is_atomic(engine, monkeypatch):
    from memory_fabric import BoardItem, Decision, SetDown
    before = fingerprint(engine.cart.conn)
    original = fabric.remember
    calls = []
    def failing(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise OSError('disk failure on second item')
        return original(*args, **kwargs)
    monkeypatch.setattr(fabric, 'remember', failing)
    with pytest.raises(OSError):
        engine.reform([BoardItem('a','one'),BoardItem('b','two')], Decision((),()), 1000)
    assert len(calls) == 2 and fingerprint(engine.cart.conn) == before


def test_replace_rows_reconstruct_original_card(engine):
    engine.cart.set_card_value('scan_line','first')
    rev = engine.revision
    engine.cart.set_card_value('scan_line','second')
    assert next(r for r in history.reconstruct(engine.cart,rev)['card'] if r['key']=='scan_line')['value']=='first'


def test_outside_review_attribution_and_separate_response(engine):
    review = engine.add_review('reviewer', 'peer', 'needs better evidence', 3)
    original = engine.reviews()
    change(engine, 'respond', review_id=review, body='new evidence attached', evidence_refs=['outcome:42'])
    assert engine.reviews() == original
    response = engine.maintain(action='reviews',review_id=review)['responses'][0]
    assert response['author']=='grace' and response['review_id']==review
    other = engine.remember('Outside', 'review content', 'fact', written_by='reviewer')
    with pytest.raises(MemoryFabricError, match='outside-authored'):
        change(engine,'revise',id=other,body='erase criticism')


def test_discovery_partial_body_alternate_words_history_and_budget(engine):
    note = engine.remember('Incident 42', 'Credentials expired during deployment', 'fact', ['auth'])
    change(engine,'revise',id=note,body='Access token renewed successfully')
    before = fingerprint(engine.cart.conn)
    assert not engine.discover('credential')['candidates']
    historical = engine.discover('credential',include_history=True)['candidates']
    assert historical[0]['historical'] and historical[0]['id']==note
    assert engine.discover('renew')['candidates'][0]['id']==note
    assert engine.discover('password',alternatives=['access token'])['candidates'][0]['id']==note
    assert 'not semantic' in engine.discover('renew')['method']
    assert fingerprint(engine.cart.conn)==before
    change(engine,'bury',id=note)
    excluded = engine.discover('access')
    assert not excluded['candidates'] and excluded['excluded_buried']==1
    assert engine.discover('access',include_buried=True)['candidates'][0]['buried']
    for i in range(10):
        engine.remember('Similar '+str(i),'body'*80,'fact')
    page = engine.discover('',limit=10,max_chars=1200)
    assert len(json.dumps(page,ensure_ascii=False)) <= 1200 and page['more_available']
    next_page = engine.discover('',limit=10,offset=page['next_offset'],max_chars=1200)
    assert not {n['id'] for n in page['candidates']} & {n['id'] for n in next_page['candidates']}


def test_reference_tools_leave_bytes_intact_and_expose_no_mutations(tmp_path):
    path=tmp_path/'reference.sqlite'
    with MemoryEngine.create(str(path),'other') as engine:
        note=engine.remember('Reference','body','fact')
    before=path.read_bytes()
    with MemoryEngine.open(str(path),read_only=True) as engine:
        tools=engine.tools(prefix='reference')
        assert tools.dispatch('reference_discover',{'query':'body'})['candidates']
        assert tools.dispatch('reference_maintain',{'action':'inspect','id':note})['note']
        assert not tools.run('reference_maintain',dict(action='bury',id=note,expected_revision=1,reason='no'))['ok']
    assert path.read_bytes()==before


def test_model_preflight_reports_large_size_without_refresh(engine):
    note=engine.remember('Large', 'body '*4000, 'fact')
    result=engine.tools(ToolLimits(max_result_chars=1000)).dispatch('memory_preflight', {'ids':[note]})
    assert not result['fits_tool_allowance'] and result['result_chars'] > 1000
    assert result['estimated_tokens'] > 3000 and result['context'].startswith('not measured')
    assert len(json.dumps(result)) < 1000 and engine.note(note)['pull_count']==0


def test_profile_tuning_and_removed_tags_remain_discoverable(engine):
    note=engine.remember('Title', 'same body', 'fact', tags=['old cue'])
    change(engine,'organize',id=note,tags=['new cue'])
    assert not engine.discover('old cue',match='all')['candidates']
    candidates=engine.discover('old cue',include_history=True,match='all')['candidates']
    assert candidates and candidates[0]['historical']
    old=engine.maintain(action='reconstruct',id=note,at_revision=candidates[0]['revision'])
    assert 'old cue' in old['tags']
    change(engine,'tune_profile',kind='project fact',profile_values={'slow_rate':0.0})
    assert engine.cart.profile('project_fact')['slow_rate']==0.0
    assert engine.tools().dispatch('memory_maintain',{'action':'profiles'})['profiles']


def test_actual_migration_failure_restores_schema_and_keeps_backup(tmp_path,monkeypatch):
    from memory_fabric import migrations
    path=str(tmp_path/'failure.sqlite')
    with MemoryEngine.create(path,'owner') as engine:
        engine.remember('Keep','body','fact')
    conn=sqlite3.connect(path)
    before=fingerprint(conn)
    conn.close()
    def fail(conn):
        conn.execute('CREATE TABLE must_rollback (x TEXT)')
        conn.execute("UPDATE notes SET body='must roll back'")
        raise OSError('migration interrupted')
    v=schema.SCHEMA_VERSION  # a failing stand-in step to the next version
    monkeypatch.setattr(schema,'SCHEMA_VERSION',v+1)
    monkeypatch.setitem(migrations.MIGRATIONS,v,fail)
    with pytest.raises(OSError):
        migrations.migrate(path,target=v+1)
    conn=sqlite3.connect(path)
    try:
        assert fingerprint(conn)==before
        assert conn.execute("SELECT name FROM sqlite_master WHERE name='must_rollback'").fetchone() is None
    finally:
        conn.close()
    assert (tmp_path/f'failure.schema{v}.bak.sqlite').exists()


@pytest.mark.parametrize('crash_after_commit', [False, True])
def test_process_loss_and_retry_receipt(tmp_path, crash_after_commit):
    path=tmp_path/'live.sqlite'
    with MemoryEngine.create(str(path),'owner') as engine:
        engine.remember('Original','retained','fact')
    script='''
import os, sys
from memory_fabric import MemoryEngine, history
e=MemoryEngine.open(sys.argv[1]).bind_session('owner','run')
if sys.argv[2]=='False':
    record=history.record
    def crash(*a,**kw):
        record(*a,**kw)
        os._exit(31)
    history.record=crash
e.tools().dispatch('memory_remember',dict(title='New',body='saved',kind='fact',operation_id='call-1'))
os._exit(32)
'''
    child=subprocess.run([sys.executable,'-B','-c',script,str(path),str(crash_after_commit)], timeout=15)
    assert child.returncode == (32 if crash_after_commit else 31)
    with MemoryEngine.open(str(path)) as recovered:
        assert recovered.open_report.status=='unclean'
        assert recovered.cart.conn.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert recovered.cart.conn.execute('SELECT count(*) FROM notes').fetchone()[0]==(4 if crash_after_commit else 3)
        receipt=recovered.tools().dispatch('memory_remember',dict(title='New',body='saved',kind='fact',operation_id='call-1'))
        assert recovered.cart.conn.execute('SELECT count(*) FROM notes').fetchone()[0]==4
        assert recovered.revision==4


def test_backup_restore_preserves_history_and_reports_recovery_point(tmp_path):
    path, backup, restored = [str(tmp_path/n) for n in ('source.sqlite','backup.sqlite','restored.sqlite')]
    with MemoryEngine.create(path,'owner') as engine:
        note=engine.remember('Fact','before','fact')
        change(engine,'revise',id=note,body='at backup')
        at=engine.revision
        engine.backup(backup)
        change(engine,'revise',id=note,body='after backup')
    # Simulated damage to the live synthetic copy. The backup remains separate.
    Path(path).write_bytes(b'damaged synthetic live file')
    result=restore_backup(backup,restored,expected_owner='owner')
    assert result['revision']==at
    with MemoryEngine.open(restored) as engine:
        assert engine.note(note)['body']=='at backup'
        assert engine.maintain(action='reconstruct',id=note,at_revision=3)['note']['body']=='before'
    with pytest.raises(MemoryFabricError):
        restore_backup(backup,restored,expected_owner='owner')
    with pytest.raises(MemoryFabricError,match='owner'):
        restore_backup(backup,str(tmp_path/'wrong.sqlite'),expected_owner='other')


def test_schema1_migration_preserves_source_backup_and_reconstructs(tmp_path):
    from memory_fabric.migrations import migrate
    path=str(tmp_path/'old.sqlite')
    with MemoryEngine.create(path,'owner') as engine:
        note=engine.remember('Legacy','v1 content','fact')
    conn=sqlite3.connect(path)
    with conn:
        from memory_fabric.foundations_schema import AUTHORED_TABLES
        for table in ('session_events', *reversed(AUTHORED_TABLES)):
            conn.execute('DROP TABLE ' + table)
        for table in ('memory_changes','history_data','operation_receipts','review_responses'):
            conn.execute('DROP TABLE '+table)
        conn.execute("UPDATE meta SET value='1' WHERE key='schema_version'")
        conn.execute("UPDATE meta SET value=? WHERE key='seal'",(fingerprint(conn),))
    conn.close()
    conn=sqlite3.connect(path)
    before=fingerprint(conn)
    conn.close()
    report=migrate(path)
    conn=sqlite3.connect(report.backup_path)
    assert fingerprint(conn)==before  # SQLite backup preserves data, not header change counters.
    conn.close()
    with MemoryEngine.open(path) as engine:
        assert engine.open_report.sealed
        change(engine,'revise',id=note,body='new')
        assert engine.maintain(action='reconstruct',id=note,at_revision=0)['note']['body']=='v1 content'


def test_history_keeps_floats_exact_and_reconstructs_before_a_link(engine):
    # Claude review 2026-10-07 (D-1): json_object printed REAL with 15 digits, so
    # 0.30000000000000004 came back as 0.3 and whole-row matching raised ValueError.
    a = engine.remember('A', 'x', 'lesson')
    b = engine.remember('B', 'y', 'lesson')
    before = engine.revision
    engine.link(a, b, strength=0.1 + 0.2)
    linked = engine.revision
    assert history.reconstruct(engine.cart, before)['links'] == []
    change(engine, 'unlink', id=a, to_id=b)
    assert [l['strength'] for l in history.reconstruct(engine.cart, linked)['links']] == [0.1 + 0.2]
    change(engine, 'restore', id=a, at_revision=linked)
    assert engine.cart.conn.execute('SELECT strength FROM links').fetchone()[0] == 0.1 + 0.2
