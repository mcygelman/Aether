"""Verify migration, delegation ceilings and restart-persistent synthetic grants.

Never read installed accounts/keys or contact an external model provider.
"""
import json
import os
import sqlite3
import subprocess
import sys
import threading
from copy import deepcopy

import pytest
from aether.atelier.operator_access import OperatorAccess, password_record, can
from aether.atelier import operator_roles, key_assignments
from aether.keys.vault import Vault, VaultError, KEY_NAME
from aether.keys.runtime_keys import RuntimeKeys, as_operator, inherited, _policies

PASSWORD='Synthetic account phrase 2026!'
KEY='synthetic-api-key-accounts-batch-only'


def register(access,name):
    return access.register(dict(username=name,name=name.title(),password=PASSWORD,confirmation=PASSWORD))


def update(access,admin,user,**extra):
    row=access.actor(user['id'])
    body=dict(row,permissions=row['direct_permissions'],revision=access.read()['revision'])
    body.update(extra)
    return access.update(admin,body)


def test_migrate_exact_ids_hashes_roles_history_and_one_time_import(tmp_path):
    root=tmp_path/'operators';root.mkdir()
    record=password_record(PASSWORD)
    original=dict(version=1,revision=29,accounts=[dict(id='a'*32,username='owner',name='Owner',role='admin',active=True,permissions=[],password=record,legacy_chats=True),dict(id='b'*32,username='reader',name='Reader',role='user',active=True,permissions=['manage_teach'],password=record,legacy_chats=False)])
    raw=json.dumps(original).encode();(root/'accounts.json').write_bytes(raw)
    access=OperatorAccess(root);doc=access.read()
    assert doc['revision']==29
    for old,new in zip(original['accounts'],doc['accounts']):assert all(new[k]==v for k,v in old.items())
    assert (root/'accounts.legacy-v1.json').read_bytes()==raw==(root/'accounts.json').read_bytes()
    admin,_=access.login(dict(username='owner',password=PASSWORD))
    assert access.chat_root(admin,tmp_path/'chats')==tmp_path/'chats'
    update(access,admin,doc['accounts'][1],permissions=['manage_guides'])
    assert OperatorAccess(root).read()['accounts'][1]['permissions']==['manage_guides']
    with sqlite3.connect(access.path) as db:
        assert db.execute('SELECT count(*) FROM accounts').fetchone()[0]==2
        assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    assert PASSWORD.encode() not in access.path.read_bytes()


def test_import_failure_rolls_back_and_can_retry(tmp_path):
    root=tmp_path/'operators';root.mkdir()
    (root/'accounts.json').write_text('{bad json')
    access=OperatorAccess(root)
    with pytest.raises(ValueError):access.read()
    (root/'accounts.json').write_text(json.dumps(dict(revision=0,accounts=[])))
    assert access.read()['accounts']==[]


def test_additive_roles_fresh_sessions_revocation_and_delegation_ceiling(tmp_path):
    access=OperatorAccess(tmp_path);admin,_=register(access,'owner');user,token=register(access,'reader')
    result=operator_roles.change(access,admin,dict(revision=access.read()['revision'],action='save',name='Editors',permissions=['manage_teach']))
    rid=result['roles'][0]['id']
    update(access,admin,user,roles=[rid],permissions=['manage_guides'])
    assert set(access.user(token)['permissions'])=={'manage_teach','manage_guides'}
    operator_roles.change(access,admin,dict(revision=access.read()['revision'],action='save',id=rid,name='Editors',permissions=[]))
    assert access.user(token)['permissions']==['manage_guides']
    with pytest.raises(ValueError,match='Remove'):
        operator_roles.change(access,admin,dict(revision=access.read()['revision'],action='delete',id=rid))
    update(access,admin,user,permissions=['manage_accounts','manage_roles'])
    manager=access.user(token)
    with pytest.raises(PermissionError):update(access,manager,user,role='admin')
    with pytest.raises(PermissionError):update(access,manager,admin,active=False)
    with pytest.raises(PermissionError):operator_roles.change(access,manager,dict(revision=access.read()['revision'],name='Escalation',permissions=['manage_api_keys']))
    with pytest.raises(PermissionError):access.set_password(manager,dict(revision=access.read()['revision'],id=admin['id'],password=PASSWORD,confirmation=PASSWORD))


def test_transactions_rollback_and_concurrent_stale_revision(tmp_path):
    one=OperatorAccess(tmp_path);admin,_=register(one,'owner');two=OperatorAccess(tmp_path)
    old=deepcopy(one.read())
    with pytest.raises(RuntimeError):
        with one.edit(old['revision']) as doc:
            doc['accounts'][0]['name']='Never committed'
            raise RuntimeError()
    assert two.read()==old
    with one.edit(old['revision']) as doc:doc['accounts'][0]['name']='Committed'
    with pytest.raises(ValueError,match='another window'):
        with two.edit(old['revision']):pass


@pytest.fixture
def keys(tmp_path):
    if os.name!='nt':pytest.skip('Windows DPAPI')
    access=OperatorAccess(tmp_path/'operators');admin,token=register(access,'owner');user,user_token=register(access,'reader')
    vault=Vault(tmp_path/'credentials');runtime=RuntimeKeys(vault,access)
    vault.create(PASSWORD,PASSWORD,KEY)
    key_assignments.lease(access,token,vault,grant=True)
    yield access,admin,token,user,user_token,vault,runtime
    vault.lock();_policies.pop(str(vault.root.resolve()),None)


def grant(fixture,keys):
    access,admin,token,user,_,vault,runtime=fixture
    key_assignments.change(access,admin,dict(action='assign',id=user['id'],keys=keys,revision=access.read()['revision']),runtime,key_assignments.lease(access,token,vault))


def test_assignment_requires_own_unlock_and_never_discloses_secret(keys):
    access,admin,token,user,user_token,vault,runtime=keys
    assert not key_assignments.lease(access,user_token,vault)
    with pytest.raises(PermissionError):key_assignments.change(access,admin,dict(action='assign',id=user['id'],keys=[KEY_NAME],revision=access.read()['revision']),runtime,False)
    grant(keys,[KEY_NAME])
    assert KEY not in json.dumps(key_assignments.listing(access,user,runtime,False))
    assert KEY.encode() not in runtime.path.read_bytes() and PASSWORD.encode() not in runtime.path.read_bytes()
    with as_operator(user['id']):assert runtime.resolve(KEY_NAME)==KEY
    with as_operator(admin['id']):
        with pytest.raises(VaultError):runtime.resolve(KEY_NAME)
    with pytest.raises(VaultError):runtime.resolve(KEY_NAME)


def test_restart_process_works_while_management_locked(keys):
    access,admin,token,user,user_token,vault,runtime=keys;grant(keys,[KEY_NAME]);vault.lock()
    code='''import sys
from aether.atelier.operator_access import OperatorAccess
from aether.keys.vault import Vault, KEY_NAME
from aether.keys.runtime_keys import RuntimeKeys, as_operator
v=Vault(sys.argv[1]);r=RuntimeKeys(v,OperatorAccess(sys.argv[2]))
assert not v.status()['unlocked']
with as_operator(sys.argv[3]): assert r.resolve(KEY_NAME)==sys.argv[4]
print('RESTART_ASSIGNED_USAGE_OK')
'''
    # Synthetic key only; no real credentials are used in arguments or fixtures.
    result=subprocess.run([sys.executable,'-B','-c',code,str(vault.root),str(access.root),user['id'],KEY],capture_output=True,text=True,check=True)
    assert result.stdout.strip()=='RESTART_ASSIGNED_USAGE_OK'


def test_revocation_suspend_disable_rotation_and_inherited_worker(keys):
    access,admin,token,user,user_token,vault,runtime=keys;grant(keys,[KEY_NAME])
    outputs=[]
    with as_operator(user['id']):worker=threading.Thread(target=inherited(lambda:outputs.append(runtime.resolve(KEY_NAME))))
    worker.start();worker.join();assert outputs==[KEY]
    vault.replace_key(KEY+'-rotated');key_assignments.lease(access,token,vault,grant=True)
    with as_operator(user['id']):assert runtime.resolve(KEY_NAME)==KEY+'-rotated'
    key_assignments.change(access,admin,dict(action='suspend',suspended=True,revision=access.read()['revision']),runtime,True)
    with as_operator(user['id']):
        with pytest.raises(VaultError):runtime.resolve(KEY_NAME)
    key_assignments.change(access,admin,dict(action='suspend',suspended=False,revision=access.read()['revision']),runtime,True)
    grant(keys,[])
    with as_operator(user['id']):
        with pytest.raises(VaultError):runtime.resolve(KEY_NAME)
    grant(keys,[KEY_NAME]);update(access,admin,user,active=False)
    with as_operator(user['id']):
        with pytest.raises(VaultError):runtime.resolve(KEY_NAME)


def test_stale_runtime_copy_and_deleted_key_fail_closed(keys):
    access,admin,token,user,user_token,vault,runtime=keys;grant(keys,[KEY_NAME])
    original=runtime.path.read_bytes();vault.delete(KEY_NAME)
    with as_operator(user['id']):
        with pytest.raises(VaultError):runtime.resolve(KEY_NAME)
    runtime.path.write_bytes(original)
    with as_operator(user['id']):
        with pytest.raises(VaultError,match='changed'):runtime.resolve(KEY_NAME)
