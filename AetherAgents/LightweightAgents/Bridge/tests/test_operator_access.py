"""Focused synthetic checks for local operator privileges and request isolation."""
import json
from pathlib import Path
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from aether.atelier.operator_access import OperatorAccess, can
from aether.atelier.operator_access_http import mutation_permission, workspace_allowed
from aether.atelier.workspace_store import WorkspaceStore
from aether.atelier.model_chat import ModelChats
from aether.atelier.campaigns import Campaigns
from aether.mission_control.atelier_server import make_handler
from test_model_chat import Settings, Reply


def details(username):
    return dict(username=username, name=username.title(), password='Synthetic passphrase 123!',
                confirmation='Synthetic passphrase 123!', role='admin', permissions=['manage_settings'])


class OperatorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='operator-')
        self.root = Path(self.temp.name)
        self.access = OperatorAccess(self.root / 'accounts')
        self.admin, self.admin_token = self.access.register(details('owner'))
        self.user, self.user_token = self.access.register(details('reader'))

    def tearDown(self):
        self.temp.cleanup()

    def update(self, user, **changes):
        body = dict(user, revision=self.access.read()['revision'])
        body.update(changes)
        return self.access.update(self.admin, body)

    def test_first_account_admin_later_registration_cannot_elevate(self):
        self.assertEqual(self.admin['role'], 'admin')
        self.assertEqual(self.user['role'], 'user')
        self.assertEqual(self.user['permissions'], [])
        self.assertNotIn('password', self.access.listing()['accounts'][0])
        self.assertNotIn(b'Synthetic passphrase', self.access.path.read_bytes())
        self.assertFalse(can(self.user, 'manage_settings'))

    def test_multiple_admins_last_admin_and_immediate_revocation(self):
        with self.assertRaises(ValueError): self.update(self.admin, role='user')
        self.update(self.user, role='admin')
        self.assertTrue(can(self.access.user(self.user_token), 'manage_settings'))
        self.update(self.user, role='user', permissions=['manage_teach'])
        self.assertFalse(can(self.access.user(self.user_token), 'manage_settings'))
        self.assertTrue(can(self.access.user(self.user_token), 'manage_teach'))
        self.update(self.user, active=False)
        self.assertIsNone(self.access.user(self.user_token))
        self.update(self.user, active=True)
        self.assertIsNone(self.access.user(self.user_token))
        with self.assertRaises(PermissionError):
            self.access.update(self.user, dict(self.user, revision=self.access.read()['revision'], role='admin'))

    def test_permissions_do_not_spill_between_areas_or_artifact_types(self):
        teacher = dict(self.user, permissions=['manage_teach'])
        doc = dict(artifacts=[dict(id='note', kind='note'),dict(id='frame',kind='teachframe')])
        self.assertTrue(workspace_allowed(teacher, dict(action='artifact_save',kind='teachframe'), doc))
        self.assertFalse(workspace_allowed(teacher, dict(action='artifact_save',id='note',kind='teachframe'), doc))
        self.assertFalse(workspace_allowed(teacher, dict(action='artifact_archive',id='note'), doc))
        self.assertFalse(workspace_allowed(teacher, dict(action='artifact_delete',id='frame'), doc))
        self.assertFalse(workspace_allowed(teacher, dict(action='role_save'), doc))
        self.assertFalse(workspace_allowed(teacher, dict(action='dashboard_save'), doc))
        self.assertEqual(mutation_permission('/api/atelier/wake'), 'manage_missions')
        self.assertEqual(mutation_permission('/api/agent-availability/confirm'), 'manage_agents')
        self.assertEqual(mutation_permission('/api/places/start'), 'manage_settings')
        self.assertEqual(mutation_permission('/future/mutation'), 'admin')

    def test_password_change_rotates_sessions_and_chat_roots_are_private(self):
        _, extra = self.access.login(details('reader'))
        before=self.access.path.read_bytes()
        with self.assertRaises(ValueError):
            self.access.change_password(self.user,dict(current_password='Wrong current password',
                password='A replacement phrase 456!',confirmation='A replacement phrase 456!'))
        self.assertEqual(self.access.path.read_bytes(),before)
        self.assertIsNotNone(self.access.user(self.user_token))
        _, fresh = self.access.change_password(self.user, dict(current_password=details('reader')['password'],
            password='A replacement phrase 456!',confirmation='A replacement phrase 456!'))
        self.assertIsNone(self.access.user(extra))
        self.assertIsNone(self.access.user(self.user_token))
        self.assertEqual(self.access.user(fresh)['id'], self.user['id'])
        with self.assertRaises(ValueError):self.access.login(details('reader'))
        self.assertIsNotNone(self.access.user(self.admin_token))
        roots=[self.access.chat_root(a,self.root/'chats') for a in (self.admin,self.user)]
        self.assertEqual(roots[0],self.root/'chats')
        self.assertNotEqual(*roots)

    def test_http_enforces_login_permissions_origin_and_chat_isolation(self):
        workspace=WorkspaceStore(self.root/'workspace');settings=Settings();reply=Reply();stores={}
        def chats(actor):
            if actor['id'] not in stores:
                stores[actor['id']]=ModelChats(self.access.chat_root(actor,self.root/'chats'),settings,factory=lambda p:reply)
            return stores[actor['id']]
        state=dict(operator_access=self.access,operator_chats=chats,workspace_store=workspace,
                   campaigns=Campaigns(lambda:self.root/'atelier'),model_settings=settings)
        server=ThreadingHTTPServer(('127.0.0.1',0),make_handler(state));worker=threading.Thread(target=server.serve_forever);worker.start()
        base='http://127.0.0.1:'+str(server.server_port)
        def request(path,body=None,token=None,origin=True):
            headers={}
            if token:headers['Cookie']='aether_operator_'+str(server.server_port)+'='+token
            if body is not None:
                headers['Content-Type']='application/json'
                if origin:headers['Origin']=base
            req=Request(base+path,None if body is None else json.dumps(body).encode(),headers)
            try:
                with urlopen(req) as r:return r.status,json.load(r)
            except HTTPError as e:return e.code,json.load(e)
        try:
            self.assertEqual(request('/api/workspace')[0],401)
            self.assertEqual(request('/api/access/session')[1]['user'],None)
            for path,body in [('/api/campaigns',dict(action='create',name='Denied')),
                ('/api/agent-availability/starter-team',{}),('/settings/models-context/api/save',{}),
                ('/api/places/start',{}),('/api/workspace',dict(action='halt',revision=0)),
                ('/api/access/accounts',dict(self.user,role='admin',revision=self.access.read()['revision']))]:
                self.assertEqual(request(path,body,self.user_token)[0],403,path)
            status,chat=request('/api/model-chat/new',{},self.user_token);self.assertEqual(status,200)
            self.assertEqual(request('/api/model-chat?id='+chat['id'],token=self.admin_token)[0],409)
            self.assertEqual(request('/api/model-chat/send',dict(id=chat['id'],model='demo',text='Hello'),self.user_token)[0],200)
            chats(self.user).settle()
            self.assertEqual(len(reply.calls),1)
            self.assertEqual(request('/api/model-chat/new',{},self.user_token,False)[0],403)
            self.update(self.user,permissions=['manage_missions'])
            self.assertEqual(request('/api/campaigns',dict(action='create',name='Allowed'),self.user_token)[0],200)
            self.assertEqual(request('/api/workspace',dict(action='halt',revision=0),self.user_token)[0],403)
            reset = dict(action='set_password', id=self.user['id'], revision=self.access.read()['revision'],
                         password='Replacement account phrase!', confirmation='Replacement account phrase!')
            self.assertEqual(request('/api/access/accounts', reset, self.user_token)[0],403)
            self.assertEqual(request('/api/access/accounts', reset, self.admin_token)[0],200)
            self.assertEqual(request('/api/model-chat?id='+chat['id'],token=self.user_token)[0],401)
            _, fresh = self.access.login(dict(username='reader',password=reset['password']))
            self.assertEqual(request('/api/model-chat?id='+chat['id'],token=fresh)[0],200)
            deletion = dict(action='delete',id=self.user['id'],confirmation='reader',revision=self.access.read()['revision'])
            self.assertEqual(request('/api/access/accounts', deletion, fresh)[0],403)
            self.assertEqual(request('/api/access/accounts', deletion, self.admin_token,False)[0],403)
            self.assertEqual(request('/api/access/accounts', dict(deletion,confirmation='wrong'),self.admin_token)[0],409)
            status, directory=request('/api/access/accounts', deletion,self.admin_token)
            self.assertEqual(status,200)
            self.assertNotIn(self.user['id'],[u['id'] for u in directory['accounts']])
            self.assertEqual(request('/api/model-chat?id='+chat['id'],token=fresh)[0],401)
            self.assertTrue(stores[self.user['id']]._path(chat['id']).is_file())
        finally:
            for store in stores.values():store.settle()
            server.shutdown();server.server_close();worker.join(3)

    def test_delete_requires_current_admin_confirmation_and_fresh_revision(self):
        body=dict(id=self.user['id'],confirmation='reader',revision=self.access.read()['revision'])
        before=self.access.path.read_bytes()
        for actor, changes, error in [(self.user,{},PermissionError),
                (self.admin,{'confirmation':'Reader'},ValueError),
                (self.admin,{'revision':body['revision']-1},ValueError),
                (self.admin,{'id':'missing'},ValueError)]:
            with self.assertRaises(error):self.access.delete_account(actor,dict(body,**changes))
            self.assertEqual(self.access.path.read_bytes(),before)
        self.assertIsNotNone(self.access.user(self.user_token))
        self.update(self.user,role='admin')
        stale_admin=self.access.user(self.user_token)
        self.update(self.user,role='user')
        with self.assertRaises(PermissionError):
            self.access.delete_account(stale_admin,dict(body,revision=self.access.read()['revision']))

    def test_delete_retains_private_files_revokes_all_sessions_and_does_not_reassign_history(self):
        _, extra=self.access.login(details('reader'))
        chat_root=self.access.chat_root(self.user,self.root/'chats')
        chat_root.mkdir(parents=True)
        saved=chat_root/'retained.json';saved.write_text('Private historical record')
        self.access.delete_account(self.admin,dict(id=self.user['id'],confirmation='reader',revision=self.access.read()['revision']))
        self.assertIsNone(self.access.user(self.user_token));self.assertIsNone(self.access.user(extra))
        self.assertIsNotNone(self.access.user(self.admin_token))
        with self.assertRaises(ValueError):self.access.login(details('reader'))
        replacement,_=self.access.register(details('reader'))
        self.assertNotEqual(replacement['id'],self.user['id'])
        self.assertNotEqual(self.access.chat_root(replacement,self.root/'chats'),chat_root)
        self.assertEqual(saved.read_text(),'Private historical record')
        self.assertEqual(OperatorAccess(self.access.root).listing(),self.access.listing())

    def test_delete_protects_last_admin_and_self_but_allows_another_admin_to_delete(self):
        def remove(actor,target):
            return self.access.delete_account(actor,dict(id=target['id'],confirmation=target['username'],revision=self.access.read()['revision']))
        with self.assertRaisesRegex(ValueError,'active Admin'):remove(self.admin,self.admin)
        self.update(self.user,role='admin')
        with self.assertRaisesRegex(ValueError,'Another Admin'):remove(self.admin,self.admin)
        remove(self.user,self.admin)
        self.assertIsNone(self.access.user(self.admin_token))
        self.assertTrue(can(self.access.user(self.user_token),'admin'))
        self.assertFalse(self.access.read()['accounts'][0].get('legacy_chats'))


if __name__=='__main__':unittest.main()
