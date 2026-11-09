"""Verify ungated specialist learning and project separation with disposable stores; never use resident memory or models."""
import json
from pathlib import Path
import os
import subprocess
import sys
import pytest

from aether.atelier import specialist_learning as learning, specialist_context as context
from aether.atelier.participants import Participant
from aether.atelier.turn import Tools, think
from aether.atelier import mind_memory
from aether.memory.fabric import memory_fabric


def specialist(tmp_path,owner='editor'):
    p=Participant.author(tmp_path/owner,id=owner,kind='member',name=owner,system='Fixed personality.',cartridge=False)
    p.tags=['specialist'];p.allowed_tools=['file_memory','recall_memory','load_memory','memory_query','memory_workspace','log_action','recall_log'];p.save()
    learning.create(p.home,p.id,p.name,'Editing' if owner=='editor' else 'Software testing')
    return p


@pytest.mark.parametrize('owner',['editor','coder','reviewer'])
def test_direct_learning_revision_paths_and_later_project_recall(tmp_path,owner):
    p=specialist(tmp_path,owner);tools=Tools(p,tmp_path/'SecretCustomer',commission_id='CONFIDENTIAL-project')
    identity=(p.home/'identity/System_0.txt').read_bytes()
    path=learning.call(p.home,'path',dict(title='Review methods'))['id']
    other=learning.call(p.home,'path',dict(title='Checkpointing',parent=path))['id']
    args=dict(title='Checkpoint long reviews',body='Record the next review step before interruption.',path=other,operation_id='file-one')
    note=json.loads(tools.call('technical_learning',dict(action='lesson',arguments_json=json.dumps(args))))
    assert note['id'] and note==learning.call(p.home,'lesson',args)
    learning.call(p.home,'revise',dict(id=note['id'],body='Keep a checkpoint before interruption and verify it on resume.',qualifications='Useful for long reviews.'))
    learning.call(p.home,'revise',dict(id=other,title='Recovery checkpoints',parent=None,related=[path]))
    with pytest.raises(ValueError,match='ancestor'):
        learning.call(p.home,'revise',dict(id=other,parent=other))
    restarted=Participant.load(p.home);Tools(restarted,tmp_path/'OtherCustomer',commission_id='later-project')
    assert note['id'] in [h['id'] for h in restarted.recall(['checkpoint'])]
    assert 'verify it on resume' in restarted.load_memory([note['id']])[0]['body']
    result=learning.browse(p.home)
    assert next(x for x in result['paths'] if x['id']==other)['parent'] is None
    assert next(x for x in result['paths'] if x['id']==other)['related']==[path]
    assert learning.call(p.home,'history')['changes']
    with learning._open(p.home) as engine:
        dump='\n'.join(engine.cart.conn.iterdump())
    assert 'CONFIDENTIAL-project' not in dump and 'SecretCustomer' not in dump and 'later-project' not in dump
    assert (p.home/'identity/System_0.txt').read_bytes()==identity
    class Model:
        def chat(self,messages,tools=None):
            names=[t['function']['name'] for t in tools]
            assert 'technical_learning' in names and 'project_checkpoint' in names
            return dict(content='Done.')
    assert think(Model(),[],tools,max_calls=2)[1]['model_calls']==1


def test_project_routes_never_mutate_technical_store(tmp_path):
    p=specialist(tmp_path);tools=Tools(p,tmp_path/'project',commission_id='secret-project')
    before=Path(p.cartridge()['path']).read_bytes()
    checkpoint=json.loads(tools.call('project_checkpoint',dict(text='PRIVATE unfinished invoice patch')))
    assert checkpoint['stored']=='project'
    assert p.file_now('fact','PRIVATE fact','PRIVATE fact body',['secret-project'],commission_id='secret-project')['id']
    assert p.file_now('todo','PRIVATE task','PRIVATE task body',[],commission_id='secret-project')['id']
    p.file_decision('PRIVATE decision',subject='PRIVATE subject',scope='secret-project',decided_by='Customer',source_ref='PRIVATE-note',commission_id='secret-project')
    p.log([dict(kind='noted',pointer='PRIVATE-path',tags=['PRIVATE'],line='PRIVATE action')],commission_id='secret-project')
    assert p.log_recent()[0]['line']=='PRIVATE action'
    assert p.log_search(['PRIVATE'])
    result=p.file_closure('What I learned\n- PRIVATE closure lesson',commission_id='secret-project',tags=['PRIVATE'])
    assert result['storage']=='project' and not result['technical_lesson_automatically_filed']
    # Actual record filing, including reviewers and outcomes, is routed to the local adapter.
    facts=dict(verdicts=[dict(by=dict(id='reviewer',kind='reviewer',name='Private Reviewer'),subject='PRIVATE item',verdict='PASS',why='PRIVATE check')],acceptance=dict(by=dict(id='operator',name='Private Operator')))
    record=p.file_record(facts,'secret-project')
    assert record['reviews'] and record['outcome']
    assert 'PRIVATE' in tools.call('memory_query',dict(sql='SELECT body FROM notes'))
    assert 'schema_version' in tools.call('memory_workspace',dict(action='schema'))
    assert 'PRIVATE unfinished' in p.wake_memory([],commission_id='secret-project')
    assert not p.continuity()['carry']
    assert Path(p.cartridge()['path']).read_bytes()==before
    assert not list((p.home/'memory/archive/experiences').glob('*.md'))
    # Different project, same specialist: no accidental carry from the previous project.
    Tools(p,tmp_path/'next-project',commission_id='next-project')
    assert 'PRIVATE' not in p.wake_memory([],commission_id='next-project')


def test_restart_and_interrupted_transaction_preserve_checkpoint(tmp_path):
    p=specialist(tmp_path);project=tmp_path/'project';Tools(p,project,commission_id='one')
    context.checkpoint(p,'Complete checkpoint before crash','one')
    project_cart=Path(mind_memory.pointer(context.home(p))['path'])
    # Kill a separate process in the middle of a transaction; no simulated rollback-only shortcut.
    code='''import sys,os
from aether.memory.fabric import memory_fabric
e=memory_fabric().MemoryEngine.open(sys.argv[1]);e.bind_session('editor','interrupted')
with e.cart.transaction(immediate=True):
 e.remember('Working checkpoint','INCOMPLETE','project_fact',['checkpoint'])
 os._exit(71)
'''
    run=subprocess.run([sys.executable,'-c',code,str(project_cart)],capture_output=True,text=True)
    assert run.returncode==71,run.stderr
    read='''import sys
from pathlib import Path
from aether.atelier.participants import Participant
from aether.atelier.turn import Tools
from aether.atelier.specialist_context import wake
p=Participant.load(Path(sys.argv[1]));Tools(p,Path(sys.argv[2]),commission_id='one');print(wake(p,'one'))
'''
    run=subprocess.run([sys.executable,'-c',read,str(p.home),str(project)],capture_output=True,text=True)
    assert run.returncode==0,run.stderr
    assert 'Complete checkpoint before crash' in run.stdout and 'INCOMPLETE' not in run.stdout


def test_legacy_transition_is_recoverable_and_preserves_original_bytes(tmp_path):
    p=Participant.author(tmp_path/'legacy',id='legacy',kind='member',name='Legacy',system='Personality never changes.')
    p.tags=['specialist'];p.save();p.file_now('fact','Private legacy memory','PRIVATE legacy',[],commission_id='private-project')
    pointer=(p.home/'memory/.cartridge.json').read_bytes();old=Path(p.cartridge()['path']);old_bytes=old.read_bytes()
    identity=(p.home/'identity/System_0.txt').read_bytes()
    learning.transition(p.home)
    assert p.technical() and old.read_bytes()==old_bytes
    assert not learning.browse(p.home)['lessons']
    Tools(p,tmp_path/'work',commission_id='new')
    assert json.loads(Tools(p,tmp_path/'work').call('technical_learning',dict(action='schema')))['actions']
    learning.transition(p.home,restore=True)
    assert not p.technical() and old.read_bytes()==old_bytes
    assert (p.home/'memory/.cartridge.json').read_bytes()==pointer
    assert (p.home/'identity/System_0.txt').read_bytes()==identity
    learning.transition(p.home)
    assert p.technical()


def test_owner_separation_and_unchanged_generic_memory(tmp_path):
    a=specialist(tmp_path,'one');b=specialist(tmp_path,'two')
    note=learning.call(a.home,'lesson',dict(title='Only my lesson',body='My technical method'))['id']
    assert not learning.browse(b.home)['lessons']
    with pytest.raises(Exception): learning.call(b.home,'revise',dict(id=note,body='Wrong owner'))
    raw=json.loads((b.home/'memory/.cartridge.json').read_text());raw['path']=a.cartridge()['path']
    (b.home/'memory/.cartridge.json').write_text(json.dumps(raw))
    with pytest.raises(Exception,match='owner'): learning.call(b.home,'lesson',dict(title='Wrong owner',body='No'))
    generic=Participant.author(tmp_path/'generic',id='generic',kind='member',name='Generic',system='Legacy generic')
    assert not generic.technical()
    assert generic.file_now('lesson','Generic continuity lesson','Existing behavior',[],commission_id='original')['id']
    assert generic.recall(['continuity'])


def test_turn_and_closure(tmp_path):
    from aether.atelier import Atelier, Budget
    from aether.atelier.place import Place
    from aether.atelier.postmaster import Postmaster
    from aether.atelier.turn import run_turn
    from aether.mailbox import Mailbox
    from types import SimpleNamespace
    p=specialist(tmp_path);p.kind='lead';p.save();share=tmp_path/'share';cid='private-project';root=share/'work'/cid
    atelier=Atelier(share,cid,budget=Budget(model_calls=5))
    atelier.open(dict(id='operator',kind='operator',name='Operator'),'PRIVATE manuscript review')
    place=Place(share,[p.home]);Postmaster(atelier,share).exchange(place.kinds(),place.names())
    class Model:
        calls=0
        def chat(self,messages,tools=None):
            self.calls+=1
            if self.calls==1:
                assert 'No approver' in messages[0]['content']
                assert 'PROJECT CONTINUITY' in messages[1]['content']
                def call(name,args):return dict(id=name,type='function',function=dict(name=name,arguments=json.dumps(args)))
                return dict(content='',tool_calls=[call('project_checkpoint',dict(text='PRIVATE resume at page ten')),
                    call('log_action',dict(line='PRIVATE review started')),
                    call('technical_learning',dict(action='lesson',arguments_json=json.dumps(dict(title='Checkpoint long reviews',body='Record where to resume before interruption.'))))])
            # Side-log is durable before the model makes another call.
            assert any(r['line']=='PRIVATE review started' for r in p.log_recent())
            assert all('Error:' not in m['content'] for m in messages if m['role']=='tool')
            return dict(content='Continuing the review.')
    model=Model()
    assert run_turn(p,atelier,Mailbox(share,p.record()),model,root)
    assert model.calls==2
    assert len([r for r in p.log_recent(limit=30) if r['line']=='PRIVATE review started'])==1
    cart=Path(p.cartridge()['path']);before=cart.read_bytes()
    facts=atelier.facts();facts['closures']=[dict(participant=p.record(),text='PRIVATE closing words')]
    proxy=SimpleNamespace(facts=lambda:facts,daybook=atelier.daybook,filed=lambda *a:None,note=lambda *a,**k:None)
    report=dict(failed=[]);place.file_closures(proxy,cid,report)
    assert not report['failed'] and report['filed'][0]['storage']=='project'
    assert cart.read_bytes()==before
    assert len(learning.browse(p.home)['lessons'])==1


def test_history_links_supersession_and_stale_revision(tmp_path):
    p=specialist(tmp_path);a=learning.call(p.home,'path',dict(title='Methods'))['id'];b=learning.call(p.home,'path',dict(title='Testing'))['id']
    note=learning.call(p.home,'lesson',dict(title='An initial technique',body='First version',path=a))
    learning.call(p.home,'revise',dict(id=note['id'],body='Qualified version',path=b))
    links=learning.call(p.home,'inspect',dict(id=note['id']))['note']['links']
    assert any(b in (l['a'],l['b']) for l in links) and not any(a in (l['a'],l['b']) for l in links)
    with pytest.raises(Exception,match='revision'):
        learning.call(p.home,'revise',dict(id=note['id'],body='Stale edit',expected_revision=note['revision']))
    replacement=learning.call(p.home,'lesson',dict(title='A better technique',body='Improvement',supersedes=note['id']))
    assert replacement['id']!=note['id']
    assert learning.call(p.home,'inspect',dict(id=note['id']))['note']
    history=learning.call(p.home,'history',dict(limit=100))
    assert any(n.get('body')=='First version' for c in history['changes'] for n in c['before'])
    assert any(n.get('body')=='Qualified version' for c in history['changes'] for n in c['after'])


def test_learning_routes_and_storage_permission():
    from aether.mission_control.gateway import PUBLIC_ROUTES, route_for
    from aether.atelier.operator_access_http import mutation_permission
    for path in ('/api/agent-catalog/learning','/api/agent-catalog/learning/history'):
        route=route_for('GET',path,PUBLIC_ROUTES)
        assert route and not route.changes_state
    route=route_for('POST','/api/agent-catalog/learning/transition',PUBLIC_ROUTES)
    assert route and route.changes_state
    assert mutation_permission(route.path)=='manage_agents'
