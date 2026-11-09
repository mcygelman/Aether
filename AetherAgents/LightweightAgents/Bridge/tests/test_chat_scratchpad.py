"""Verify account scratchpad isolation and actual tool dispatch on disposable data.

Never use real accounts, credentials, external providers or agent memories.
"""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import threading
import unittest

from aether.atelier.chat_scratchpad import ChatScratchpad
from aether.atelier.chat_files import ChatFiles
from aether.atelier.model_chat import ModelChats
from aether.atelier.personal_chat_store import PersonalChatStore
from test_model_chat import Settings


class Reply:
    def __init__(self): self.calls=[]
    def chat(self, messages, **kwargs):
        self.calls.append((deepcopy(messages),deepcopy(kwargs)))
        if kwargs.get('tools') and messages[-1]['role']=='user':
            return dict(content='',tool_calls=[dict(id='listing',type='function',function=dict(name='list_files',arguments='{}'))])
        return dict(content='Folder checked.' if kwargs.get('tools') else 'File access is off.')


class ScratchpadTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='scratchpad-check-');self.root=Path(self.temp.name)
        self.control=self.root/'.aether_control';self.control.mkdir()
        self.stores=[];self.reply=Reply()
        self.a=self.make('a'*32);self.b=self.make('b'*32)

    def tearDown(self):
        for store in self.stores:store.settle()
        self.temp.cleanup()

    def make(self,owner):
        root=self.control/'chats'/owner
        store=ModelChats(root,Settings(),cartridge=PersonalChatStore(root,owner),
            scratchpad=ChatScratchpad(self.control/'chat_scratchpads',owner),
            protected_roots=lambda:[self.control],factory=lambda p:self.reply)
        self.stores.append(store);return store

    def test_shared_across_own_chats_and_restart_but_never_peers(self):
        a=self.a.create();b=self.b.create();path=Path(a['folder']['root'])/'note.txt';path.write_text('private A')
        again=self.a.create();reopened=self.make('a'*32)
        self.assertEqual(reopened.workspace(reopened.read(again['id'])).read('note.txt')['text'],'private A')
        self.assertEqual(self.b.workspace(b).listing()['entries'],[])
        for peer in [str(path.parent),str(self.control/'chat_scratchpads'),str(self.control)]:
            with self.assertRaises(ValueError):self.b.file_action(b['id'],'attach',dict(path=peer))
        with self.assertRaises(ValueError):self.b.file_action(b['id'],'read',dict(path=str(path)))
        with self.assertRaises(ValueError):self.b.read(a['id'])

    def test_explicit_folder_and_off_survive_restart_and_scratchpad_return(self):
        cid=self.a.create()['id'];external=self.root/'chosen';external.mkdir()
        self.a.file_action(cid,'attach',dict(path=str(external)))
        reopened=self.make('a'*32)
        self.assertEqual(reopened.read(cid)['folder']['root'],str(external))
        reopened.file_action(cid,'detach',{})
        self.assertIsNone(self.make('a'*32).read(cid)['folder'])
        restored=reopened.file_action(cid,'scratchpad',{})
        self.assertEqual(restored['folder']['kind'],'scratchpad');self.assertFalse(restored['folder']['editable'])

    def test_legacy_chat_without_folder_gets_default_without_losing_messages(self):
        cid=self.a.create()['id']
        with self.a._edit(cid) as doc:
            doc.pop('folder');doc.pop('folder_mode');doc['title']='Retained title'
            doc['messages']=[dict(id='m',role='user',content='retained')]
        restored=self.make('a'*32).read(cid)
        self.assertEqual(restored['folder']['kind'],'scratchpad')
        self.assertEqual(restored['messages'][0]['content'],'retained')

    def test_absolute_paths_canonicalize_and_escapes_remain_blocked(self):
        doc=self.a.create();space=self.a.workspace(doc);path=space.root/'a.txt';path.write_text('hello')
        self.assertEqual(space.read(str(path))['path'],'a.txt')
        self.assertEqual(space.listing(str(space.root))['path'],'')
        for value in [str(space.root.parent/'peer.txt'),str(space.root/'..'/'peer.txt'),str(space.root/'a.txt:stream'),'.hidden.txt','memory/a.txt']:
            with self.assertRaises(ValueError,msg=value):space.read(value)
        # Normal folder access still cannot enter application storage.
        with self.assertRaises(ValueError):ChatFiles(doc['folder'],self.root/'cache',[self.control])

    def test_creation_needs_edit_enable_then_review_and_rechecks_revision(self):
        doc=self.a.create();cid=doc['id']
        args=dict(path='new.txt',content='new content',base_sha256='new')
        with self.assertRaises(ValueError):self.a.file_tool(cid,doc['folder']['revision'],'propose_file_edit',args,threading.Event())
        doc=self.a.file_action(cid,'permissions',dict(editable=True,retrieval=False))
        result,_=self.a.file_tool(cid,doc['folder']['revision'],'propose_file_edit',args,threading.Event())
        target=Path(doc['folder']['root'])/'new.txt';self.assertFalse(target.exists())
        self.a.file_action(cid,'apply',dict(proposal=result['proposal']))
        self.assertEqual(target.read_text(),'new content')
        self.a.file_action(cid,'detach',{})
        with self.assertRaises(ValueError):self.a.file_tool(cid,doc['folder']['revision'],'read_file',dict(path='new.txt'),threading.Event())

    def test_current_briefing_and_real_tool_payload_agree_after_folder_changes(self):
        cid=self.a.create()['id']
        with self.a._edit(cid) as doc:doc['messages']=[dict(id='old',role='assistant',content='I have no tools.')]
        self.a.start(cid,dict(model='demo',text='Do you have folder access?'));self.a.settle()
        doc=self.a.read(cid);self.assertEqual(doc['job']['status'],'complete')
        messages,kwargs=self.reply.calls[0]
        names=[t['function']['name'] for t in kwargs['tools']]
        self.assertEqual(names,doc['job']['personal_context']['tools'])
        self.assertIn('supersedes earlier claims',messages[0]['content'])
        self.assertEqual(doc['job']['file_activity'][0]['tool'],'list_files')
        self.a.file_action(cid,'detach',{});self.reply.calls=[]
        self.a.start(cid,dict(model='demo',text='Is file access off?'));self.a.settle()
        self.assertNotIn('tools',self.reply.calls[0][1])
        self.assertIn('No folder tools',self.reply.calls[0][0][0]['content'])
        self.a.file_action(cid,'scratchpad',{});self.reply.calls=[]
        self.a.start(cid,dict(model='demo',text='Check my folder'));self.a.settle()
        self.assertIn('tools',self.reply.calls[0][1])

    def test_deleted_chats_and_same_username_new_id_do_not_reassign_files(self):
        old=self.a.create();(Path(old['folder']['root'])/'kept.txt').write_text('retained')
        self.a.update(old['id'],'delete',{})
        self.assertTrue((Path(old['folder']['root'])/'kept.txt').exists())
        fresh=self.make('c'*32);self.assertEqual(fresh.workspace(fresh.create()).listing()['entries'],[])

    def test_scratchpad_links_are_rejected_before_use(self):
        import os
        doc=self.a.create();path=Path(doc['folder']['root']);outside=self.root/'outside';outside.mkdir()
        try:os.symlink(outside,path/'alias',target_is_directory=True)
        except OSError:self.skipTest('Host cannot create symlinks without privilege')
        with self.assertRaises(ValueError):self.a.workspace(doc).listing('alias')
