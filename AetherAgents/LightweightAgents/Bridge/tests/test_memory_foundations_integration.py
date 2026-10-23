"""Offline arrival/work/closure journeys. Every mind and profile is disposable."""
import json

from aether.atelier import mind_memory
from aether.atelier.participants import Participant
from aether.memory import foundations
from aether.memory.fabric import memory_fabric


def mind(tmp_path):
    return Participant.author(tmp_path / 'm', id='guide', kind='member', name='Guide',
        system='You are Guide.', roots={'outputs': 'write'}, mailboxes=tmp_path / 'mail', cartridge=True)


def cards(home, commission):
    return {e['name']: e for e in mind_memory.workspace_call(home, commission, 'dashboard')['elements']}


def test_atelier_closure_retries_and_carry_survive_new_session(tmp_path):
    p = mind(tmp_path)
    sid = mind_memory.begin_work(p.home, 'one')
    assert mind_memory.begin_work(p.home, 'one') == sid
    args = dict(record_type='projects', title='Project A', body='Purpose', operation_id='p')
    project = mind_memory.workspace_call(p.home, 'one', 'create', args)['id']
    mind_memory.workspace_call(p.home, 'one', 'project_work', dict(project_id=project, operation_id='worked'))
    text = 'I finished the local changes. Next: inspect the integration evidence.'
    result = p.file_closure(text, commission_id='one')
    assert result['lifecycle']['event']['kind'] == 'closed'
    with mind_memory._open_read(p.home) as e:
        revision = e.revision
    assert p.file_closure(text, commission_id='one')['lifecycle'] == result['lifecycle']
    with mind_memory._open_read(p.home) as e:
        assert e.revision == revision
    second = mind_memory.begin_work(p.home, 'one')
    assert second != sid
    # Retrying the old closure cannot close this new activation.
    assert p.file_closure(text, commission_id='one')['lifecycle']['event']['session_id'] == sid
    with mind_memory._open_read(p.home) as e:
        assert e.query("SELECT count(*) FROM session_events WHERE session_id=? AND kind='closed'", [second])['rows'] == [[0]]
    (p.home / 'workspace/SESSION_CURRENT.txt').write_text('A later unrelated carry', encoding='utf-8')
    view = cards(p.home, 'one')
    assert view['previous_closure']['value']['state'] == 'PASS'
    assert view['next_self_note']['value']['text'] == text
    assert view['last_project']['value']['project_id'] == project
    with mind_memory._open_write(p.home, 'edit') as e:
        e.maintain(action='revise', id=result['cartridge_note'], body='Changed later', reason='Test historical carry', expected_revision=e.revision)
    assert cards(p.home, 'one')['next_self_note']['value']['text'] == text


def test_failed_filing_is_not_a_pass_and_retry_recovers(tmp_path, monkeypatch):
    p = mind(tmp_path)
    sid = mind_memory.begin_work(p.home, 'one')
    original = mind_memory.file_closing
    def fail(*a, **kw):
        raise OSError('synthetic cartridge failure')
    monkeypatch.setattr(mind_memory, 'file_closing', fail)
    failed = p.file_closure('My authored words', commission_id='one')
    assert failed['lifecycle']['event']['kind'] == 'closure_failed'
    with mind_memory._open_read(p.home) as e:
        assert e.query("SELECT count(*) FROM session_events WHERE kind='closed'")['rows'] == [[0]]
    monkeypatch.setattr(mind_memory, 'file_closing', original)
    assert p.file_closure('My authored words', commission_id='one')['lifecycle']['event']['kind'] == 'closed'
    mind_memory.begin_work(p.home, 'two')
    previous = cards(p.home, 'two')['previous_closure']['value']
    assert previous['session_id'] == sid and previous['state'] == 'PASS' and previous['recovered']


def test_read_only_welcome_does_not_open_a_work_session(tmp_path):
    p = mind(tmp_path)
    assert cards(p.home, 'preview')['previous_closure']['status'] == 'empty'
    with mind_memory._open_read(p.home) as e:
        assert e.query('SELECT count(*) FROM session_events')['rows'] == [[0]]


def test_host_requires_actual_closed_evidence(tmp_path):
    path = tmp_path / 'state.json'
    path.write_text(json.dumps(dict(status='FAILED', closure_id='c')), encoding='utf-8')
    import pytest
    with memory_fabric().MemoryEngine.create(':memory:', 'owner') as e:
        sid = foundations.begin(e, 'one', source_system='test')
        with pytest.raises(ValueError, match='does not establish'):
            foundations.publish(e, sid, 'c', path)
        assert e.query("SELECT count(*) FROM session_events WHERE kind='closed'")['rows'] == [[0]]
