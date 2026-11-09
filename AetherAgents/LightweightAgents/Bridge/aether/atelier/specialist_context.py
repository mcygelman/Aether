"""Own project-local specialist continuity; never copy project context into portable technical memory."""
from contextvars import ContextVar
from dataclasses import replace
import hashlib
from pathlib import Path

from . import mind_memory

_projects=ContextVar('specialist_projects',default={})


def bind(participant,root,commission_id):
    contexts=dict(_projects.get())
    contexts[str(participant.home.resolve())]=(Path(root).resolve(),commission_id)
    _projects.set(contexts)


def home(participant,commission_id=None):
    context=_projects.get().get(str(participant.home.resolve()))
    if not context or (commission_id is not None and context[1]!=commission_id):
        raise ValueError('Project continuity requires an active project context.')
    root,cid=context
    key=hashlib.sha256(participant.id.encode()).hexdigest()[:24]
    target=root/'.aether-specialists'/key
    if not mind_memory.pointer(target): mind_memory.create(target,participant.id,participant.name)
    return target


def local(participant,commission_id=None):
    return replace(participant,home=home(participant,commission_id))


def checkpoint(participant,text,commission_id):
    """A single engine transaction preserves either the old or new checkpoint on interruption."""
    target=home(participant,commission_id)
    with mind_memory._open_write(target,'project-checkpoint') as engine:
        nid=engine.remember('Working checkpoint',text,'project_fact',['checkpoint'],source_ref=commission_id)
        return dict(id=nid,stored='project',text='Checkpoint saved with this project.')


def wake(participant,commission_id):
    target=home(participant,commission_id)
    with mind_memory._open_read(target) as engine:
        page=engine.query("SELECT body FROM notes WHERE note_id IN (SELECT note_id FROM tags WHERE tag='checkpoint') ORDER BY rowid DESC LIMIT 1")
        checkpoint_text=page['rows'][0][0] if page['rows'] else ''
        closing=engine.query("SELECT body FROM notes WHERE note_id IN (SELECT note_id FROM tags WHERE tag='closing-words') ORDER BY rowid DESC LIMIT 1")
        last_closing=closing['rows'][0][0] if closing['rows'] else ''
    p=local(participant,commission_id)
    memory=p.wake_memory([],commission_id) or ''
    participant.last_decisions=getattr(p,'last_decisions',[])
    return ('PROJECT CONTINUITY (separate from technical learning):\n'+checkpoint_text+'\n'+
            memory+'\n'+'\nLAST PROJECT CLOSURE:\n'+last_closing)


def closure(participant,text,commission_id,tags):
    target=home(participant,commission_id)
    # The engine's idempotent closure transaction is the project receipt. No
    # portable-home scaffold or duplicate file tree is needed for a specialist.
    result=mind_memory.file_closing(target,text,commission_id=commission_id,tags=list(tags or []))
    return dict(storage='project',project_cartridge=result['cartridge'],cartridge_note=result['notes'][0][1],
                cartridge_notes=result['notes'],technical_lesson_automatically_filed=False)
