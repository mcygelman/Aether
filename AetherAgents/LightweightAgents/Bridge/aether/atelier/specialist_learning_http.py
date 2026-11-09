"""Own specialist learning inspection and explicit legacy transition; never admit lessons or accept filesystem paths."""
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from aether.integrity.http_body import json_body
from . import specialist_learning as learning
from .participants import Participant


def handle_learning(handler,method,state):
    registry=state['agent_registry']; parsed=urlparse(handler.path)
    query=parse_qs(parsed.query)
    if parsed.path not in ('/api/agent-catalog/learning','/api/agent-catalog/learning/history','/api/agent-catalog/learning/transition'):
        handler.settings_send(dict(error='Not found'),404); return True
    with registry.lock:
        entries=registry._entries()
        specialists={}
        for aid,row in entries.items():
            try:
                p=Participant.load(Path(row['home']))
                if 'specialist' in p.tags: specialists[aid]=p
            except (OSError,ValueError,TypeError): pass
        if method=='GET':
            aid=query.get('agent',[''])[0]
            if not aid:
                handler.settings_send(dict(agents=[dict(id=p.id,name=p.name,technical=p.technical()) for p in specialists.values()]))
            else:
                if aid not in specialists: raise ValueError('Choose a registered specialist.')
                p=specialists[aid]
                if not p.technical(): result=dict(mode='legacy',history_imported=False)
                elif parsed.path.endswith('/history'):
                    result=learning.call(p.home,'history',dict(after=int(query.get('after',['0'])[0]),limit=50))
                else: result=learning.browse(p.home,query.get('query',[''])[0])
                handler.settings_send(dict(result,id=p.id,name=p.name,can_restore=(p.home/'memory/technical-transition.json').exists()))
        elif method=='POST' and parsed.path.endswith('/transition'):
            body=json_body(handler,8192,'Send a specialist identity and action.')
            if not isinstance(body,dict): raise ValueError('Send an object.')
            p=specialists.get(body.get('agent'))
            if p is None: raise ValueError('Choose a registered specialist.')
            if body.get('action') not in ('technical','restore'): raise ValueError('Choose technical or restore.')
            if registry.is_working(p.id): raise ValueError('Finish the current turn before changing its storage mode.')
            handler.settings_send(learning.transition(p.home,restore=body['action']=='restore'))
        else: handler.settings_send(dict(error='Not found'),404)
    return True
