"""Checkpoint operation intent and partial turn evidence in the owning home; never replay an action or publish memory."""
import uuid
from pathlib import Path
from aether.keys.redaction import redact
from . import meter,transcripts
from ._files import write_json,read_json

def resume_messages(sprint,mind,wake,messages):
    """Give the same mind its own completed tool feedback after a failed turn; never replay any action."""
    turns=[read_json(p) for p in sorted((sprint.root/'turns').glob('*.json'))]
    own=[t for t in turns if t.get('mind')==mind]
    if not own:return messages
    last=own[-1]
    if last.get('ok') or last.get('kind')!=wake.kind or last.get('task')!=wake.task:return messages
    if str(last.get('error','')).startswith(('SetupError','ContextCapacityError')):return messages
    for ref in last.get('transcripts',[]):
        path=Path(ref).resolve()
        home=transcripts._home(sprint,mind).resolve()
        if home not in path.parents:continue
        saved=read_json(path)
        if saved.get('mind')!=mind:continue
        history=[{k:v for k,v in m.items() if k in ('role','content','tool_calls','tool_call_id','name','reasoning_content')}
                 for m in saved.get('messages',[])[2:] if not m.get('cut') and m.get('role')!='system']
        return messages+history+[dict(role='user',content='The previous turn was interrupted. These are your own recorded actions and tool results. Continue from them; do not repeat completed work. Correct a pending failed command with retry=true. Existing limits still apply.')]
    return messages

class EvidenceError(RuntimeError):pass

class Journal:
    def __init__(self,sprint,mind):
        self.sprint,self.mind=sprint,mind
        key=meter.current_key()
        name=f'{key[1]:04d}' if key else uuid.uuid4().hex
        self.path=transcripts._home(sprint,mind)/'execution'/sprint.id/(name+'.json')
    def __call__(self,cost,phase,operation=None):
        try:
            write_json(self.path,redact(dict(mind=self.mind,phase=phase,operation=operation,cost=cost)))
        except Exception as exc:
            raise EvidenceError('Cannot persist operation evidence; no further action is permitted.') from exc
