"""Own additive named roles and delegation ceilings; never authorize from UI claims."""
from copy import deepcopy
import uuid


def effective(user, doc):
    if user is None:
        return None
    result=deepcopy(user)
    result['direct_permissions']=list(user['permissions'])
    result['permissions']=sorted(set(user['permissions']) | {p for r in doc.get('roles',[]) if r['id'] in user.get('roles',[]) for p in r['permissions']})
    return result


def current_actor(doc, actor, permission):
    from .operator_access import can
    current=effective(next((u for u in doc['accounts'] if u['id']==actor['id']),None),doc)
    if not can(current,permission):
        raise PermissionError('Your account cannot perform this management action.')
    return current


def ceiling(actor, target, doc):
    if actor['role']=='admin':
        return
    target=effective(target,doc)
    if target['role']=='admin' or not set(target['permissions']) <= set(actor['permissions']):
        raise PermissionError('Only an Admin or a manager with all of this access can manage this account.')


def selection(body, doc, actor):
    from .operator_access import PERMISSIONS
    permissions=body.get('permissions',[]);roles=body.get('roles',[])
    if not isinstance(permissions,list) or any(p not in PERMISSIONS for p in permissions):
        raise ValueError('Choose permissions from the available list.')
    if not isinstance(roles,list) or any(r not in {v['id'] for v in doc.get('roles',[])} for r in roles):
        raise ValueError('Choose existing roles.')
    ceiling(actor,dict(role=body.get('role'),permissions=permissions,roles=roles),doc)
    return sorted(set(permissions)),sorted(set(roles))


def change(access, actor, body):
    from .operator_access import PERMISSIONS
    with access.edit(body.get('revision')) as doc:
        current=current_actor(doc,actor,'manage_roles')
        role=next((r for r in doc['roles'] if r['id']==body.get('id')),None)
        if role and current['role']!='admin' and not set(role['permissions']) <= set(current['permissions']):
            raise PermissionError('This role exceeds your management access.')
        if body.get('action')=='delete':
            if not role:raise ValueError('Role not found.')
            if any(role['id'] in u.get('roles',[]) for u in doc['accounts']):
                raise ValueError('Remove this role from its accounts before deleting it.')
            doc['roles'].remove(role)
        else:
            name=body.get('name','');perms=body.get('permissions')
            if not isinstance(name,str) or not 1<=len(name.strip())<=80 or name.strip().casefold() in ('admin','user'):
                raise ValueError('Use a custom role name of 1–80 characters; Admin and User are reserved.')
            if any(r is not role and r['name'].casefold()==name.strip().casefold() for r in doc['roles']):
                raise ValueError('That role name is already used.')
            if not isinstance(perms,list) or any(p not in PERMISSIONS for p in perms):raise ValueError('Choose known permissions.')
            if current['role']!='admin' and not set(perms)<=set(current['permissions']):raise PermissionError('A role cannot grant access you do not hold.')
            if body.get('id') and not role:raise ValueError('Role not found.')
            if role is None:
                role=dict(id=uuid.uuid4().hex);doc['roles'].append(role)
            role.update(name=name.strip(),permissions=sorted(set(perms)))
    return access.listing()
