"""Exercise private cartridge persistence and context without real users or providers.

Never load agent memories or modify retained operator data.
"""
import json
from pathlib import Path
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.request import Request,urlopen
from urllib.error import HTTPError

from aether.atelier.personal_chat_store import PersonalChatStore
from aether.atelier.personal_chat_http import handle,source_selection
from aether.atelier.chat_briefing import briefing
from aether.atelier.model_chat import ModelChats
from aether.atelier.operator_access import OperatorAccess
from aether.mission_control.atelier_server import make_handler
from test_model_chat import Settings,Reply


class PersonalChatTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='personal-chat-');self.root=Path(self.temp.name)
        self.owner='1'*32;self.reply=Reply()
        self.cartridge=PersonalChatStore(self.root/'chats',self.owner)
        self.store=ModelChats(self.root/'chats',Settings(),cartridge=self.cartridge,factory=lambda p:self.reply)

    def tearDown(self):
        self.store.settle();self.temp.cleanup()

    def note(self,**values):
        body=dict(action='save',revision=self.cartridge.snapshot()['revision'],title='Writing preference',
                  content='Prefer brief concrete answers.',kind='preference',confirmed=True,use_in_chat=True)
        body.update(values);self.cartridge.change(body)
        return self.cartridge.snapshot()['notes'][0]

    def test_sqlite_transcripts_resume_and_export_without_provider_replay_or_folder(self):
        cid=self.store.create()['id']
        self.store.start(cid,dict(text='Discuss planning',model='demo'));self.store.settle()
        self.assertEqual(self.store.read(cid)['job']['status'],'complete')
        self.assertFalse(self.store._path(cid).exists())
        reopened=ModelChats(self.store.root,Settings(),cartridge=PersonalChatStore(self.store.root,self.owner))
        self.assertEqual(len(reopened.read(cid)['messages']),2)
        with self.store._edit(cid) as doc:doc['folder']={'root':'private local path'}
        self.note()
        export=self.cartridge.export()
        self.assertEqual(len(export['chats']),1);self.assertEqual(len(export['notes']),1)
        self.assertNotIn('history',export['chats'][0]['messages'][1]);self.assertNotIn('folder',export['chats'][0])

    def test_legacy_import_once_preserves_bytes_and_new_changes(self):
        legacy=ModelChats(self.root/'legacy',Settings());cid=legacy.create()['id']
        raw=legacy._path(cid).read_bytes()
        cartridge=PersonalChatStore(legacy.root,'2'*32)
        store=ModelChats(legacy.root,Settings(),cartridge=cartridge)
        store.update(cid,'tag',dict(tag='new SQLite tag'))
        PersonalChatStore(legacy.root,'2'*32)
        self.assertEqual(store.read(cid)['tags'],['new SQLite tag'])
        self.assertEqual(legacy._path(cid).read_bytes(),raw)
        self.assertEqual(cartridge.snapshot()['imported_chats'],1)
        with self.assertRaises(ValueError):PersonalChatStore(legacy.root,'3'*32)

    def test_invalid_legacy_import_rolls_back_and_can_retry_without_duplication(self):
        legacy=ModelChats(self.root/'broken',Settings());cid=legacy.create()['id']
        bad=legacy.root/('f'*32+'.json');bad.write_text('{bad')
        with self.assertRaises(ValueError):PersonalChatStore(legacy.root,'4'*32)
        bad.unlink()
        fixed=PersonalChatStore(legacy.root,'4'*32)
        self.assertEqual(fixed.chat_ids(),[cid])

    def test_note_revision_archive_and_recall_controls(self):
        n=self.note();state=self.cartridge.snapshot()
        with self.assertRaises(ValueError):self.cartridge.change(dict(action='archive',id=n['id'],revision=0))
        self.assertEqual(self.cartridge.snapshot(),state)
        self.assertEqual(len(briefing(self.cartridge)['items']),1)
        self.assertEqual(briefing(self.cartridge,{'personal_recall':False})['items'],[])
        self.cartridge.change(dict(action='archive',id=n['id'],revision=state['revision']))
        self.assertEqual(briefing(self.cartridge)['items'],[])
        self.cartridge.change(dict(action='restore',id=n['id'],revision=self.cartridge.snapshot()['revision']))
        self.cartridge.change(dict(action='settings',recall=False,revision=self.cartridge.snapshot()['revision']))
        self.assertFalse(briefing(self.cartridge)['enabled'])

    def test_relevance_budget_and_unconfirmed_source_attribution(self):
        cid=self.store.create()['id'];self.store.start(cid,dict(text='Discuss radio batteries',model='demo'));self.store.settle()
        doc=self.store.read(cid);mid=doc['messages'][-1]['id']
        source=source_selection(self.store,dict(chat_id=cid,messages=[mid]))
        body=dict(action='save',revision=0,title='Battery readiness',kind='knowledge',content='Batteries may require review.',use_in_chat=True,confirmed=False)
        self.cartridge.change(body,source)
        self.assertEqual(briefing(self.cartridge,query='weather')['items'],[])
        context=briefing(self.cartridge,query='batteries')
        self.assertFalse(context['items'][0]['confirmed'])
        self.assertEqual(context['items'][0]['source']['messages'][0]['role'],'assistant')
        self.assertEqual(briefing(self.cartridge,query='batteries',budget=2)['items'],[])
        with self.assertRaises(ValueError):source_selection(self.store,dict(chat_id=cid,messages=['f'*32]))
        for i in range(8):self.note(title='Preference '+str(i))
        self.assertLessEqual(len(briefing(self.cartridge)['items']),6)

    def test_preview_equals_sent_briefing_and_transform_never_includes_personal_knowledge(self):
        self.note();cid=self.store.create()['id']
        preview=handle(self.store,'POST',{},dict(action='briefing',chat=cid,query='Hello',model='demo'))
        self.store.start(cid,dict(text='Hello',model='demo'));self.store.settle()
        self.assertEqual(self.reply.calls[-1][0]['content'],preview['instructions'])
        self.assertEqual(self.store.read(cid)['job']['personal_context'],preview)
        self.store.start(cid,dict(model='demo',transform='summary'),transform=True);self.store.settle()
        self.assertNotIn('Prefer brief concrete answers.',json.dumps(self.reply.calls[-1]))
        self.store.update(cid,'personal-recall',{'enabled':False})
        self.store.start(cid,dict(text='Continue',model='demo'));self.store.settle()
        self.assertNotIn('Prefer brief concrete answers.',self.reply.calls[-1][0]['content'])

    def test_http_account_isolation_and_source_ownership(self):
        access=OperatorAccess(self.root/'accounts')
        def details(name):return dict(username=name,name=name,password='Disposable password 123!',confirmation='Disposable password 123!')
        admin,admin_token=access.register(details('admin'));user,user_token=access.register(details('reader'))
        stores={}
        def chats(actor):
            if actor['id'] not in stores:
                root=access.chat_root(actor,self.root/'http-chats')
                stores[actor['id']]=ModelChats(root,Settings(),cartridge=PersonalChatStore(root,actor['id']))
            return stores[actor['id']]
        private=chats(user);cid=private.create()['id']
        private.cartridge.change(dict(action='save',revision=0,title='Reader secret',content='Private synthetic note',kind='knowledge',confirmed=True,use_in_chat=True))
        server=ThreadingHTTPServer(('127.0.0.1',0),make_handler(dict(operator_access=access,operator_chats=chats)))
        worker=threading.Thread(target=server.serve_forever);worker.start();base='http://127.0.0.1:'+str(server.server_port)
        def call(token=None,body=None,query='',origin=True):
            headers={'Cookie':'aether_operator_'+str(server.server_port)+'='+token} if token else {}
            if body is not None:
                headers['Content-Type']='application/json'
                if origin:headers['Origin']=base
            req=Request(base+'/api/model-chat/cartridge'+query,data=json.dumps(body).encode() if body is not None else None,headers=headers)
            try:
                with urlopen(req) as r:return r.status,json.load(r)
            except HTTPError as e:return e.code,json.load(e)
        try:
            self.assertEqual(call()[0],401)
            self.assertEqual(call(admin_token,query='?export=1')[1]['notes'],[])
            self.assertEqual(call(user_token,query='?owner='+admin['id'])[1]['owner'],user['id'])
            n=call(user_token)[1]['notes'][0]
            self.assertEqual(call(admin_token,dict(action='archive',id=n['id'],revision=0))[0],409)
            self.assertEqual(call(admin_token,dict(action='briefing',chat=cid,model='demo',query=''))[0],409)
            self.assertEqual(call(user_token,dict(action='settings',recall=False,revision=1),origin=False)[0],403)
            self.assertEqual(call(user_token,dict(action='settings',recall=False,revision=1))[0],200)
        finally:
            server.shutdown();server.server_close();worker.join(3)


if __name__=='__main__':unittest.main()
