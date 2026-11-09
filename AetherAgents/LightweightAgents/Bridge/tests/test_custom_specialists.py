"""Verify local custom revisions, pinned fresh homes and learning separation; never touch resident state or providers."""
from copy import deepcopy
import json
from pathlib import Path
import uuid
import pytest
from aether.atelier.custom_templates import CustomTemplates,DEFAULT_CONFIG,convert
from aether.atelier.custom_templates_http import install
from aether.atelier.agent_registry import AgentRegistry
from aether.atelier.participants import Participant
from aether.atelier.turn import Tools
from aether.atelier import specialist_learning as learning

class Settings:
    def read(self):return dict(document=dict(profiles={}))

def fixture(tmp_path):
    draft=json.loads((Path(__file__).parents[1]/'aether/agent_editor/web/fixtures/sample_lw_template.json').read_text(encoding='utf-8'))
    draft['extra_reference']={'retain':'all original fields'}
    store=CustomTemplates(tmp_path/'state/custom-templates.json')
    body=dict(request_id=str(uuid.uuid4()),revision=store.listing()['revision'],title='Local research',draft=draft,config=deepcopy(DEFAULT_CONFIG))
    return store,body

def creation(row):return dict(request_id=str(uuid.uuid4()),id=row['id'],version=row['version'],sha256=row['sha256'],name='Fresh Nova',deployment='unconfigured')

def test_revision_restart_exact_draft_conflict_and_retry(tmp_path):
    store,body=fixture(tmp_path);row=store.save(body,'author-1')
    assert row['draft']==body['draft'] and row['saved_by']=='author-1'
    restarted=CustomTemplates(store.path)
    assert restarted.save(body,'author-1')==row and len(restarted.read()[0]['templates'][row['id']])==1
    changed=deepcopy(body);changed.update(request_id=str(uuid.uuid4()),id=row['id']);changed['draft']['identity']['role']='Changed role'
    with pytest.raises(ValueError,match='changed'):restarted.save(changed,'author-2')
    changed['revision']=restarted.listing()['revision'];second=restarted.save(changed,'author-2')
    assert second['version']==2 and restarted.get(row['id'],1)==row
    with pytest.raises(ValueError,match='different content'):store.save(dict(body,title='different'),'author-1')
    assert restarted.get(row['id'])==second

@pytest.mark.parametrize('patch',[{'tools':['shell']},{'output_dirs':['../escape']},{'output_dirs':['memory']},{'kind':'heavyweight'},{'output_dirs':[]}])
def test_invalid_permissions_have_no_store_side_effects(tmp_path,patch):
    store,body=fixture(tmp_path);body['config'].update(patch)
    with pytest.raises(ValueError):store.save(body,'author')
    assert not store.path.exists()

def test_ultralight_and_malformed_import_refused(tmp_path):
    store,body=fixture(tmp_path);body['draft']['identity']['tier']='ultralight'
    with pytest.raises(ValueError):store.save(body,'author')
    body['draft']=None
    with pytest.raises(ValueError):store.save(body,'author')
    assert not store.path.exists()

def test_pinned_creation_is_fresh_and_existing_agent_survives_new_revision(tmp_path):
    store,body=fixture(tmp_path);row=store.save(body,'author');request=creation(row)
    registry=AgentRegistry(tmp_path/'state/agents.json',default_folder=tmp_path/'agents')
    receipt=install(store,registry,tmp_path/'share',Settings(),request)
    home=Path(receipt['agents'][0]['home']);mind=Participant.load(home)
    assert mind.name=='Fresh Nova' and mind.technical() and receipt['model_calls']==0
    assert not learning.browse(home)['lessons']
    runtime=json.loads((home/'template.json').read_text(encoding='utf-8'))
    assert runtime['source_template']==row and runtime['source_template']['draft']==body['draft']
    before={str(p.relative_to(home)):p.read_bytes() for p in home.rglob('*') if p.is_file()}
    body.update(id=row['id'],request_id=str(uuid.uuid4()),revision=store.listing()['revision']);body['draft']['identity']['role']='New role'
    second=store.save(body,'author')
    assert install(store,registry,tmp_path/'share',Settings(),request)==receipt
    assert before=={str(p.relative_to(home)):p.read_bytes() for p in home.rglob('*') if p.is_file()}
    other=install(store,registry,tmp_path/'share',Settings(),creation(second))
    assert other['agents'][0]['id']!=mind.id and other['agents'][0]['home']!=str(home)
    assert len(registry.listing()['agents'])==2
    with pytest.raises(ValueError,match='exact saved'):install(store,registry,tmp_path/'share',Settings(),dict(request,sha256='bad'))
    with pytest.raises(ValueError,match='different selection'):install(store,registry,tmp_path/'share',Settings(),dict(request,name='Renamed retry'))

def test_retry_after_registration_failure_and_interrupted_authoring(tmp_path,monkeypatch):
    store,body=fixture(tmp_path);row=store.save(body,'author');request=creation(row)
    registry=AgentRegistry(tmp_path/'state/agents.json',default_folder=tmp_path/'agents');confirm=registry.confirm
    monkeypatch.setattr(registry,'confirm',lambda *a:(_ for _ in ()).throw(ValueError('interrupted registration')))
    with pytest.raises(ValueError):install(store,registry,tmp_path/'share',Settings(),request)
    home=next((tmp_path/'agents/specialists').iterdir());identity=(home/'identity/System_0.txt').read_bytes()
    monkeypatch.setattr(registry,'confirm',confirm)
    assert install(store,registry,tmp_path/'share',Settings(),request)['complete']
    assert identity==(home/'identity/System_0.txt').read_bytes()
    request=creation(row);monkeypatch.setattr(Participant,'author',lambda *a,**k:(_ for _ in ()).throw(ValueError('interrupted authoring')))
    with pytest.raises(ValueError):install(store,registry,tmp_path/'share',Settings(),request)
    with pytest.raises(ValueError,match='Interrupted authoring retained'):install(store,registry,tmp_path/'share',Settings(),request)

def test_custom_runtime_tools_and_project_technical_separation(tmp_path):
    store,body=fixture(tmp_path);row=store.save(body,'author');registry=AgentRegistry(tmp_path/'state/agents.json',default_folder=tmp_path/'agents')
    receipt=install(store,registry,tmp_path/'share',Settings(),creation(row));mind=Participant.load(Path(receipt['agents'][0]['home']))
    tools=Tools(mind,tmp_path/'project',commission_id='private-one');identity=mind.system_text()
    assert 'not allowed' in tools.call('run_tests',{'path':'.'})
    assert 'may write' in tools.call('write_file',{'path':'outside/x','content':'bad'})
    tools.call('write_file',{'path':'deliverables/report.txt','content':'Synthetic result'})
    before=Path(mind.cartridge()['path']).read_bytes()
    assert json.loads(tools.call('project_checkpoint',{'text':'PRIVATE customer progress'}))['stored']=='project'
    assert before==Path(mind.cartridge()['path']).read_bytes()
    lesson=json.loads(tools.call('technical_learning',dict(action='lesson',arguments_json=json.dumps(dict(title='Review source dates',body='Check publication dates before comparing versions.')))))
    assert lesson['id'] and mind.system_text()==identity
    restarted=Participant.load(mind.home);Tools(restarted,tmp_path/'later',commission_id='later')
    assert 'PRIVATE' not in restarted.wake_memory([],commission_id='later')
    assert lesson['id'] in [n['id'] for n in restarted.recall(['dates'])]

def test_reference_is_retained_but_not_executable_and_revision_tampering_refused(tmp_path):
    store,body=fixture(tmp_path);body['draft']['extra_reference']['untrusted']='EXECUTE_SENTINEL'
    contract=convert(body['draft'],body['config']);assert 'EXECUTE_SENTINEL' not in contract['system']
    row=store.save(body,'author');doc,_=store.read();doc['templates'][row['id']][0]['draft']['identity']['role']='tampered'
    store.path.write_text(json.dumps(doc),encoding='utf-8')
    with pytest.raises(ValueError,match='integrity'):store.get(row['id'])
