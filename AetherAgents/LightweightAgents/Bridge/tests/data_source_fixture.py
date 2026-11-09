"""Own a synthetic MCP test service; never connect a database, provider or operator installation."""
from contextlib import contextmanager
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import sys
import threading

TOOLS = [dict(name='report_officers', description='Synthetic promotion report; no real personnel.',
              inputSchema=dict(type='object', properties=dict(division=dict(type='string')), required=['division']),
              annotations=dict(readOnlyHint=True)),
         dict(name='describe_personnel', description='Synthetic source schema and promotion flag meaning.',
              inputSchema=dict(type='object', properties={}), annotations=dict(readOnlyHint=True))]


class Fixture:
    def __init__(self):
        self.tools = deepcopy(TOOLS)
        self.calls = []
        self.sse = False
        self.fail = False
        self.protocol = None
        self.token = 'synthetic-mcp-token'
        self.entered = threading.Event()
        self.release = threading.Event()
        self.release.set()

    def reply(self, message):
        method = message['method']
        if 'id' not in message:
            return None
        if method == 'initialize':
            result = dict(protocolVersion=self.protocol or message['params']['protocolVersion'], capabilities=dict(tools={}), serverInfo=dict(name='synthetic-personnel', version='1'))
        elif method == 'tools/list':
            result = dict(tools=self.tools)
        elif method == 'tools/call':
            self.calls.append(message['params'])
            self.entered.set()
            self.release.wait(5)
            if self.fail:
                result = dict(isError=True, content=[dict(type='text', text='Synthetic database failure')])
            elif message['params']['name'] == 'describe_personnel':
                result = dict(content=[dict(type='text', text='Synthetic promotion flag means approved for the next promotion cycle.')])
            else:
                division = message['params']['arguments'].get('division', 'All')
                payload = dict(columns=['Officer', 'Division', 'Rank', 'Years'], rows=[['Avery Chen', division, 'Sergeant', 8], ['Jordan Silva', division, 'Lieutenant', 12]], total_rows=2, truncated=False)
                result = dict(structuredContent=payload, content=[dict(type='text', text=json.dumps(payload))])
        else:
            return dict(jsonrpc='2.0', id=message['id'], error=dict(code=-32601, message='Unknown fixture method'))
        return dict(jsonrpc='2.0', id=message['id'], result=result)


@contextmanager
def http_fixture():
    fixture = Fixture()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_DELETE(self):
            self.send_response(204);self.send_header('Content-Length', '0');self.end_headers()

        def do_POST(self):
            if self.headers.get('Authorization') != 'Bearer ' + fixture.token:
                self.send_response(401);self.send_header('Content-Length', '0');self.end_headers();return
            message = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            result = fixture.reply(message)
            if result is None:
                self.send_response(202);self.send_header('Content-Length', '0');self.end_headers();return
            raw = json.dumps(result).encode()
            if fixture.sse:
                raw = b'event: message\ndata: ' + raw + b'\n\n'
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream' if fixture.sse else 'application/json')
            self.send_header('Content-Length', str(len(raw)))
            self.send_header('Mcp-Session-Id', 'synthetic-session')
            self.end_headers()
            try:
                self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    worker = threading.Thread(target=server.serve_forever)
    worker.start()
    fixture.url = 'http://127.0.0.1:' + str(server.server_port) + '/mcp'
    try:
        yield fixture
    finally:
        fixture.release.set()
        server.shutdown();worker.join();server.server_close()


if __name__ == '__main__':
    fixture = Fixture()
    for line in sys.stdin:
        answer = fixture.reply(json.loads(line))
        if answer is not None:
            print(json.dumps(answer), flush=True)
