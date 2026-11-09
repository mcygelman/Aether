"""Own specialist technical cartridges and owner-authored learning; never import project history or change identity.

Paths and lessons are ordinary Memory Fabric notes. Revision, history, linking and
sealing use the existing engine. Guidance is not an admission decision.
"""
import json
from pathlib import Path
import uuid

from aether.integrity.sealbox import atomic_write_bytes
from . import mind_memory

MODE = 'technical-v1'
GUIDANCE = '''Your authored personality remains fixed; your technical understanding can grow.
Notice, reflect, distill, remember, retrieve and revise when useful. You decide what
to learn. No approver, required citation, test, confidence threshold or lesson quota.
Use technical_learning for reusable knowledge, methods, pitfalls and qualifications.
Use paths as an evolving subject map: create, rename, move and cross-link them yourself.
Keep personal, proprietary and project-specific details in project continuity.
For example, keep a customer's failing invoice and unfinished patch in project_checkpoint;
distill a general lesson about testing rounding boundaries without names, data or paths.
An editor can retain a method for checkpointing a long review, without the manuscript.
References and peer feedback can inform your judgment; neither grants permission to learn.
file_memory lesson/warning writes technical memory; fact/todo writes project memory.
memory_workspace and memory_query refer to project continuity. recall_memory/load_memory
refer to technical memory. Closures, decisions and action logs stay with this project.
Use project_checkpoint during work so interrupted turns can resume. Closing words do
not automatically become technical lessons. Choose your own reflection timing.
technical_learning(action=schema) describes the technical tools; action=browse shows paths.
'''


def active(home):
    return (mind_memory.pointer(home) or {}).get('mode') == MODE


def create(home, owner, name, specialty='Technical practice'):
    """Create only a fresh technical store. No history, role identity or project tags are copied."""
    from aether.memory.fabric import memory_fabric
    home=Path(home); folder=home/'memory'; folder.mkdir(parents=True,exist_ok=True)
    cart=folder/'technical.sqlite'
    if not cart.exists():
        with memory_fabric().MemoryEngine.create(str(cart),owner,name=name,seed_resources=False) as engine:
            engine.bind_session(owner,'technical-orientation')
            root=engine.remember(specialty,json.dumps(dict(type='path',parent=None,related=[])),
                                 'standing_lesson',['technical-path'])
            for title in ['Methods','Pitfalls and qualifications']:
                engine.remember(title,json.dumps(dict(type='path',parent=root,related=[])),
                                'standing_lesson',['technical-path'],links=[(root,'context')])
    else:
        with memory_fabric().MemoryEngine.open(str(cart),read_only=True) as engine:
            if engine.agent_id!=owner: raise ValueError('Technical cartridge owner mismatch.')
    atomic_write_bytes(folder/'.cartridge.json',json.dumps(dict(path='technical.sqlite',owner=owner,mode=MODE)).encode())
    return mind_memory.pointer(home)


def transition(home, *, restore=False):
    """Explicit reversible installation compatibility; never imports historical project memories."""
    from .participants import Participant
    p=Participant.load(home)
    if 'specialist' not in p.tags: raise ValueError('Choose a catalog specialist.')
    folder=p.home/'memory'; receipt=folder/'technical-transition.json'; pointer=folder/'.cartridge.json'
    if restore:
        if not receipt.exists(): raise ValueError('No previous pointer to restore.')
        atomic_write_bytes(pointer,json.loads(receipt.read_text())['previous_pointer'].encode())
        return dict(mode='legacy',retained_technical_cartridge=True)
    if active(home): return dict(mode=MODE,already=True)
    if not receipt.exists():
        atomic_write_bytes(receipt,json.dumps(dict(previous_pointer=pointer.read_bytes().decode('utf-8'))).encode())
    create(home,p.id,p.name)
    return dict(mode=MODE,legacy_preserved=True,history_imported=False)


def _open(home, write=False):
    from aether.memory.fabric import open_engine, open_owner_engine
    pointer=mind_memory.pointer(home)
    if not pointer or pointer.get('mode')!=MODE: raise ValueError('Technical memory is not active.')
    if write:
        return open_owner_engine(pointer['path'],owner_id=pointer['owner'],session_id='technical-learning')
    engine=open_engine(pointer['path'],read_only=True)
    try: return engine.bind_session(pointer['owner'],'technical-read')
    except Exception:
        engine.close(); raise


def _rows(engine):
    rows=[]; offset=0
    while True:
        page=engine.query("SELECT note_id,title,body FROM notes WHERE note_id IN (SELECT note_id FROM tags WHERE tag IN ('technical-lesson','technical-path')) ORDER BY created_at,note_id",offset=offset,limit=500)
        for nid,title,body in page['rows']:
            try: data=json.loads(body)
            except ValueError: data=dict(type='lesson',body=body)
            rows.append(dict(data,id=nid,title=title))
        if page['next_offset'] is None: return rows
        offset=page['next_offset']


def browse(home, query=''):
    with _open(home) as engine:
        rows=_rows(engine)
        return dict(mode=MODE,revision=engine.revision,paths=[r for r in rows if r['type']=='path'],
                    lessons=[r for r in rows if r['type']=='lesson' and query.lower() in json.dumps(r).lower()],
                    project_context='Checkpoints, decisions and action history are stored separately with each project.')


def history(engine,args):
    result=engine.maintain(action='history',**args)
    for change in result['changes']:
        payload=json.loads(engine.query('SELECT payload FROM history_data WHERE history_id=?',[change['history_id']])['rows'][0][0])
        notes=payload.get('notes',{})
        def describe(row):
            try: content=json.loads(row['body'])
            except (ValueError,TypeError): return None
            if not isinstance(content,dict) or content.get('type') not in ('lesson','path'): return None
            return dict(content,id=row['note_id'],title=row['title'])
        change['before']=[d for r in notes.get('before',[]) if (d:=describe(r))]
        change['after']=[d for r in notes.get('after',[]) if (d:=describe(r))]
    return result


def call(home, action, args=None):
    args=dict(args or {})
    if action=='schema':
        return dict(actions={
            'browse':'query (optional); all paths and matching lessons, with revision',
            'lesson':'title, body; optional path (path id), related (ids), tags, qualifications, references (public citations), supersedes (lesson id), operation_id (reuse for retries)',
            'path':'title; optional parent (path id), related (ids), operation_id',
            'revise':'id, reason; optional title, body, path, parent, related, qualifications, references, tags, expected_revision, operation_id. Retains history.',
            'history':'after (revision cursor), limit (default 20)',
            'inspect':'id; includes current note and links',
            'query':'sql, parameters (optional), offset, limit; read-only technical cartridge query'},guidance=GUIDANCE)
    if action=='browse': return browse(home,str(args.get('query','')))
    writing=action in ('lesson','path','revise')
    with _open(home,writing) as engine:
        if action=='query': return engine.query(args['sql'],args.get('parameters',[]),offset=args.get('offset',0),limit=args.get('limit',50))
        if action=='history': return history(engine,args)
        if action=='inspect': return engine.maintain(action=action,**args)
        if not writing: raise ValueError('Choose an action from technical_learning schema.')
        from memory_fabric import records
        operation=args.pop('operation_id',None) or 'technical-'+uuid.uuid4().hex
        def write():
            rows={r['id']:r for r in _rows(engine)}
            prior=rows.get(args.get('id')) if action=='revise' else None
            if action=='revise' and prior is None: raise ValueError('Choose an existing technical note.')
            data={k:v for k,v in (prior or dict(type=action)).items() if k not in ('id','title')}
            for key in ('body','path','parent','related','qualifications','references','supersedes'):
                if key in args: data[key]=args[key]
            for field in ('path','parent'):
                target=data.get(field)
                if target and (target not in rows or rows[target]['type']!='path'): raise ValueError(field+' must name a technical path.')
            # Cycles make a hierarchy unreadable; this is structural integrity, not lesson admission.
            if data['type']=='path' and prior:
                cursor=data.get('parent'); seen={prior['id']}
                while cursor:
                    if cursor in seen: raise ValueError('A path cannot be its own ancestor; use a cross-link.')
                    seen.add(cursor); cursor=rows[cursor].get('parent')
            for target in [*(data.get('related') or []),*([data['supersedes']] if data.get('supersedes') else [])]:
                if target not in rows: raise ValueError('Related notes must belong to this technical cartridge.')
                if prior and target==prior['id']: raise ValueError('A note cannot link to itself.')
            title=args.get('title',prior['title'] if prior else '')
            tags=['technical-'+data['type'],*(args.get('tags') if 'tags' in args else (engine.note(prior['id'])['tags'] if prior else []))]
            body=json.dumps(data,ensure_ascii=False)
            if prior:
                engine.maintain(action='revise',id=prior['id'],title=title,body=body,tags=tags,
                                reason=args.get('reason') or 'Technical understanding revised',
                                expected_revision=args.get('expected_revision',engine.revision))
                nid=prior['id']
            else:
                nid=engine.remember(title,body,'standing_lesson',tags)
            def associations(record):
                return ({(record[key],'context') for key in ('path','parent') if record.get(key)} |
                        {(target,'conceptual') for target in record.get('related',[])} |
                        ({(record['supersedes'],'derived')} if record.get('supersedes') else set()))
            before=associations(prior or {}); after=associations(data)
            for target,relation in before-after:
                engine.maintain(action='unlink',id=nid,to_id=target,relation=relation,
                                reason='Technical path or association revised',expected_revision=engine.revision)
            for target,relation in after-before:
                engine.link(nid,target,relation)
            return dict(id=nid,title=title,kind='technical-'+data['type'])
        result=records.mutate(engine.cart,'specialist_technical',dict(action=action,arguments=args),write,operation_id=operation)
        return dict(result,revision=engine.revision)


def recall(home,tags,limit=8):
    # This reader uses recall/discovery without persisting project search terms.
    with _open(home): pass  # Verify the cartridge owner before generic read-only recall.
    return mind_memory.recall(home,tags,limit=limit)


def load(home,ids):
    with _open(home,True) as engine:
        return [dict(id=n['note_id'],title=n['title'],body=n['body'],kind=n['profile'],tags=n.get('tags')) for n in engine.load(ids)]
