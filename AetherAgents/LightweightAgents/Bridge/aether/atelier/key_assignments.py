"""Own account key-grant management and session-scoped management unlocks.

Never return secrets or treat one manager's unlocked session as another's approval.
"""
import hashlib
from .operator_roles import current_actor
from .operator_access import can


def lease(access, token, vault, *, grant=False, clear=False):
    session=access.sessions.get(hashlib.sha256(token.encode()).hexdigest())
    if not session:return False
    if clear:session.pop('key_management',None)
    state=vault.status()
    if grant:session['key_management']=state.get('revision')
    return bool(state['unlocked'] and session.get('key_management')==state.get('revision'))


def listing(access, actor, runtime, unlocked):
    doc=access.read()
    management=can(access.actor(actor['id']),'manage_key_assignments')
    result=dict(revision=doc['revision'],suspended=doc['suspended'],unlocked=unlocked,
                can_assign=management,keys=runtime.visible(actor['id']),assignments={})
    if management and unlocked:
        result['keys']=[{k:v[k] for k in ('id','label','provider')} for v in runtime.vault.status()['keys']]
        result['assignments']={u['id']:u.get('keys',[]) for u in doc['accounts']}
    return result


def change(access, actor, body, runtime, unlocked):
    if not unlocked:raise PermissionError('Unlock API management in this signed-in session before changing key assignments.')
    with access.edit(body.get('revision')) as doc:
        current_actor(doc,actor,'manage_key_assignments')
        if body.get('action')=='suspend':
            if type(body.get('suspended')) is not bool:raise ValueError('Choose whether API use is suspended.')
            doc['suspended']=body['suspended']
        elif body.get('action')=='assign':
            user=next((u for u in doc['accounts'] if u['id']==body.get('id')),None)
            keys=body.get('keys');known={k['id'] for k in runtime.vault.status()['keys']}
            if user is None:raise ValueError('Account not found.')
            if not isinstance(keys,list) or any(k not in known for k in keys):raise ValueError('Choose available API keys.')
            user['keys']=sorted(set(keys))
        else:raise ValueError('Choose an assignment action.')
    return listing(access,actor,runtime,unlocked)
