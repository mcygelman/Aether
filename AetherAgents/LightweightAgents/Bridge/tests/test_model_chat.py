"""Quick synthetic checks for model-only chat; never call a provider or open an agent."""
from copy import deepcopy
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from aether.atelier.model_chat import ModelChats, client_for
from aether.mission_control.atelier_server import make_handler


class Settings:
    def read(self):
        return {'document': {'profiles': {'demo': {
            'endpoint': 'http://127.0.0.1:1/v1/chat/completions', 'requestedApiIdentifier': 'demo-model',
            'contextWindow': 32000, 'maxOutputTokens': 1024, 'timeoutSecs': 5,
        }}}}


class Reply:
    def __init__(self):
        self.calls = []

    def chat(self, messages):
        self.calls.append(deepcopy(messages))
        return {'content': 'A useful draft.', 'reasoning_content': 'Synthetic provider replay field.'}


class ChatTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory(prefix='model-chat-')
        self.reply = Reply()
        self.store = ModelChats(Path(self.scratch.name)/'chats', Settings(), factory=lambda p: self.reply)
        self.cid = self.store.create()['id']

    def tearDown(self):
        self.store.settle()
        self.scratch.cleanup()

    def send(self, text='An exploratory idea'):
        self.store.start(self.cid, {'text': text, 'model': 'demo'})
        self.store.settle()
        doc = self.store.read(self.cid)
        self.assertEqual(doc['job']['status'], 'complete', doc['job'])
        return doc

    def test_conversation_is_saved_separately_and_replay_is_not_exported(self):
        first = self.send()
        self.assertNotIn('history', first['messages'][-1])
        self.send('Refine the idea')
        self.assertEqual(self.reply.calls[-1][2]['reasoning_content'], 'Synthetic provider replay field.')
        reopened = ModelChats(self.store.root, Settings()).read(self.cid)
        self.assertEqual(len(reopened['messages']), 4)
        self.assertEqual([p.name for p in Path(self.scratch.name).iterdir()], ['chats'])

    def test_transforms_are_separate_selected_editable_artifacts(self):
        original = self.send()['messages']
        for kind in ('aether', 'teachframe'):
            self.store.start(self.cid, {'model': 'demo', 'transform': kind,
                'messages': [original[0]['id']]}, transform=True)
            self.store.settle()
        doc = self.store.read(self.cid)
        self.assertEqual(doc['messages'], original)
        self.assertEqual([a['kind'] for a in doc['artifacts']], ['aether', 'teachframe'])
        self.assertEqual(json.loads(self.reply.calls[-1][-1]['content']), [{'role': 'user', 'text': original[0]['content']}])
        artifact = doc['artifacts'][0]
        self.store.update(self.cid, 'save-artifact', {'artifact': artifact['id'], 'text': 'Polished version'})
        self.assertEqual(self.store.read(self.cid)['artifacts'][0]['content'], 'Polished version')

    def test_stop_discards_late_response_and_prevents_overlap(self):
        entered, release = threading.Event(), threading.Event()
        class Slow:
            def chat(self, messages):
                entered.set()
                release.wait(3)
                return {'content': 'Late reply'}
        self.store.factory = lambda p: Slow()
        try:
            self.store.start(self.cid, {'model': 'demo', 'text': 'Wait'})
            self.assertTrue(entered.wait(2))
            self.store.update(self.cid, 'stop', {})
            with self.assertRaises(ValueError):
                self.store.start(self.cid, {'model': 'demo', 'text': 'Overlap'})
        finally:
            release.set()
            self.store.settle()
        doc = self.store.read(self.cid)
        self.assertEqual(len(doc['messages']), 1)
        self.assertEqual(doc['job']['status'], 'stopped')

    def test_history_search_finds_words_said_with_the_line_that_said_them(self):
        self.send('Compare   the harbor\nsettlement totals for March')
        other = self.store.create()['id']
        self.assertEqual([h['id'] for h in self.store.search('harbor settlement')], [self.cid])   # spacing and lines ignored
        hit = self.store.search('HARBOR')[0]
        self.assertEqual((hit['role'], hit['snippet']), ('user', 'Compare the harbor settlement totals for March'))
        self.assertEqual(self.store.search('useful draft')[0]['role'], 'assistant')
        self.assertEqual(self.store.search('a'), [])                       # one letter would match nearly everything
        self.assertNotIn(other, [h['id'] for h in self.store.search('March')])

    def test_delete_restore_and_bad_paths(self):
        self.send()
        self.store.update(self.cid, 'delete', {})
        with self.assertRaises(ValueError):
            self.send('Still deleted')
        self.store.update(self.cid, 'restore', {})
        self.send('Restored')
        with self.assertRaises(ValueError):
            self.store.read('../outside')
        client = client_for(Settings().read()['document']['profiles']['demo'])
        self.assertEqual(client.model, 'demo-model')  # Construct only; no transport.

    def test_local_http_boundary_and_landing_page(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler({'model_chats': self.store}))
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        base = 'http://127.0.0.1:'+str(server.server_port)
        try:
            with urlopen(base+'/') as response:
                self.assertIn(b'Choose Model', response.read())
            request = Request(base+'/api/model-chat/new', b'{}', {'Content-Type': 'application/json', 'Origin': base})
            with urlopen(request) as response:
                self.assertEqual(json.load(response)['messages'], [])
            request = Request(base+'/api/model-chat/new', b'{}', {'Content-Type': 'application/json', 'Origin': 'https://elsewhere.invalid'})
            with self.assertRaises(HTTPError) as caught:
                urlopen(request)
            self.assertEqual(caught.exception.code, 403)
        finally:
            server.shutdown()
            server.server_close()
            worker.join(3)


if __name__ == '__main__':
    unittest.main()
