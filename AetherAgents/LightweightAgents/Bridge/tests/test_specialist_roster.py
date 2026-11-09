"""Verify fresh public role installation and enforced limits; never invoke providers or access resident homes."""
import json
from pathlib import Path
from types import SimpleNamespace
import uuid

import pytest
from aether.atelier import specialist_catalog as catalog, specialist_install as installer
from aether.atelier.agent_registry import AgentRegistry, metadata
from aether.atelier.participants import Participant
from aether.atelier.turn import Tools, think
from aether.atelier.operator_access_http import mutation_permission

class Settings:
    def read(self):
        return dict(document=dict(profiles=dict(parent=dict(model='synthetic',endpoint='http://invalid'),ultralight=dict(model='helper',endpoint='http://invalid'))))

def setup(tmp_path, selection=None):
    registry=AgentRegistry(tmp_path/'r/agents.json',default_folder=tmp_path/'a')
    doc=catalog.load()
    body=dict(request_id=str(uuid.uuid4()),revision=doc['revision'],templates=selection or [r['id'] for r in doc['templates']],deployment='unconfigured')
    return registry,body

def test_all_roles_install_with_distinct_fresh_memories_and_retry(tmp_path,monkeypatch):
    from aether.memory.fabric import memory_fabric
    monkeypatch.setenv('AETHER_MINDS_DIR',str(tmp_path/'minds'))
    registry,body=setup(tmp_path)
    old=tmp_path/'a/existing';old.mkdir(parents=True);(old/'private.txt').write_text('Preserve this resident.')
    receipt=installer.install(registry,tmp_path/'share',Settings(),body)
    assert receipt['complete'] and receipt['model_calls']==0 and len(receipt['agents'])==29
    pointers=[]
    for item in receipt['agents']:
        home=Path(item['home']);mind=Participant.load(home);row=catalog.template(item['template'])
        assert metadata(str(home))['weight']=='lightweight'
        assert mind.allowed_tools==row['allowed_tools'] and mind.roots==row['roots']
        assert 'not a continuation' in mind.system_text()
        pointer=mind.cartridge();pointers.append(pointer['path'])
        with memory_fabric().MemoryEngine.open(pointer['path'],read_only=True) as engine:
            assert engine.agent_id==mind.id and engine.open_report.sealed
    assert len(set(pointers))==29 and len(registry.listing()['agents'])==29
    before={str(p):p.read_bytes() for p in (tmp_path/'a').rglob('*') if p.is_file()}
    assert installer.install(registry,tmp_path/'share',Settings(),body)==receipt
    assert before=={str(p):p.read_bytes() for p in (tmp_path/'a').rglob('*') if p.is_file()}
    assert (old/'private.txt').read_text()=='Preserve this resident.'

def test_reviewer_limits_are_enforced_not_just_prompted(tmp_path):
    row=catalog.template('linus')
    mind=Participant('reviewer','reviewer','Reviewer',tmp_path,roots=row['roots'],allowed_tools=row['allowed_tools'])
    tools=Tools(mind,tmp_path)
    assert 'not allowed' in tools.call('run_tests',dict(path='.'))
    assert 'may write' in tools.call('write_file',dict(path='src/change.py',content='bad'))
    assert 'may write' in tools.call('write_file',dict(path='reviews-other/change.py',content='bad'))
    assert 'outside' in tools.call('write_file',dict(path='../outside.txt',content='bad'))
    tools.call('write_file',dict(path='reviews/findings.txt',content='A supported finding'))
    assert (tmp_path/'reviews/findings.txt').read_text()=='A supported finding'
    calls=[]
    class Model:
        def chat(self,messages,tools=None):calls.append(tools);return dict(content='Inspection only.')
    think(Model(),[],tools,max_calls=2)
    assert 'run_tests' not in [t['function']['name'] for t in calls[0]]
    assert not (tmp_path/'src').exists()

@pytest.mark.parametrize('changes',[dict(templates=['missing']),dict(templates=['grace','grace']),dict(revision='stale'),dict(request_id='../x'),dict(deployment='ultralight')])
def test_invalid_request_has_no_home_side_effects(tmp_path,changes):
    registry,body=setup(tmp_path,['grace']);body.update(changes)
    with pytest.raises(ValueError):installer.install(registry,tmp_path/'share',Settings(),body)
    assert not (tmp_path/'a').exists()

def test_registration_retry_preserves_authored_home(tmp_path,monkeypatch):
    registry,body=setup(tmp_path,['grace'])
    original=registry.confirm
    monkeypatch.setattr(registry,'confirm',lambda *a,**k: (_ for _ in ()).throw(ValueError('Synthetic registration failure')))
    with pytest.raises(ValueError):installer.install(registry,tmp_path/'share',Settings(),body)
    home=next((tmp_path/'a/specialists').iterdir());identity=(home/'identity/System_0.txt').read_bytes()
    monkeypatch.setattr(registry,'confirm',original)
    receipt=installer.install(registry,tmp_path/'share',Settings(),body)
    assert receipt['complete'] and len(registry.listing()['agents'])==1
    assert (home/'identity/System_0.txt').read_bytes()==identity
    with pytest.raises(ValueError):installer.install(registry,tmp_path/'share',Settings(),dict(body,templates=['linus']))

def test_interrupted_authoring_never_overwritten(tmp_path,monkeypatch):
    registry,body=setup(tmp_path,['grace'])
    monkeypatch.setattr(Participant,'author',lambda *a,**k: (_ for _ in ()).throw(ValueError('Synthetic interruption')))
    with pytest.raises(ValueError):installer.install(registry,tmp_path/'share',Settings(),body)
    with pytest.raises(ValueError,match='Interrupted authoring retained'):installer.install(registry,tmp_path/'share',Settings(),body)
    assert not registry.listing()['agents'] and len(list((tmp_path/'a/specialists').iterdir()))==1

def test_empty_allowlist_and_legacy_compatibility(tmp_path):
    mind=Participant('x','member','X',tmp_path,allowed_tools=[])
    assert 'not allowed' in Tools(mind,tmp_path).call('write_file',dict(path='x',content='x'))
    mind.allowed_tools=None
    Tools(mind,tmp_path).call('write_file',dict(path='x',content='legacy'))
    assert (tmp_path/'x').read_text()=='legacy'
    assert mutation_permission('/api/agent-catalog/install')=='manage_agents'

def test_role_specific_contracts():
    rows=catalog.load()['templates']
    assert len(rows)==29 and len({r['qualification']['scenario'] for r in rows})==29
    assert {r['agent_class'] for r in rows}=={'lightweight'}
    assert 'run_tests' in catalog.template('test-automation')['allowed_tools']
    assert 'run_tests' not in catalog.template('security-reviewer')['allowed_tools']
    assert 'file_decision' in catalog.template('task-coordinator')['allowed_tools']
    assert all(r['recommendations']['helper_profile']=='ultralight' for r in rows)
