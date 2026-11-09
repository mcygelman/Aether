"""Own bounded MCP client exchanges over stdio and Streamable HTTP.

Never interpret SQL, follow redirects with credentials, install servers, or infer tool permissions.
"""
import json
import threading
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler, ProxyHandler
from aether.toolbelt.mcp_client import McpClient, McpError, StdioTransport

VERSIONS = ('2025-11-25', '2025-06-18', '2025-03-26', '2024-11-05')
MAX_BYTES = 4 * 1024 * 1024


def endpoint(url):
    value = urlsplit(url)
    if value.username or value.password or value.query or value.fragment or not value.hostname:
        raise ValueError('Use an MCP endpoint without credentials, query parameters or fragments.')
    if value.scheme != 'https' and not (value.scheme == 'http' and value.hostname in ('127.0.0.1', 'localhost', '::1')):
        raise ValueError('Use HTTPS for a remote service, or HTTP on localhost.')
    return url


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('The MCP endpoint redirected. Configure its final address.')


class HttpTransport:
    def __init__(self, url, token, timeout):
        self.url, self.token, self.timeout = endpoint(url), token, timeout
        self.session = None
        self.version = VERSIONS[0]
        self.response = None
        self.closed = False
        self.opener = build_opener(NoRedirect(), ProxyHandler({}))

    def __call__(self, message):
        if self.closed:
            raise ValueError('MCP connection is closed.')
        headers = {'Content-Type': 'application/json', 'Accept': 'application/json, text/event-stream'}
        if message['method'] != 'initialize':
            headers['MCP-Protocol-Version'] = self.version
        if self.session:
            headers['Mcp-Session-Id'] = self.session
        if self.token:
            headers['Authorization'] = 'Bearer ' + self.token
        request = Request(self.url, json.dumps(message).encode(), headers, method='POST')
        try:
            response = self.opener.open(request, timeout=self.timeout)
        except HTTPError as exc:
            code = exc.code
            exc.close()
            raise ValueError('MCP service returned HTTP ' + str(code) + '. Check its address and authentication.') from None
        self.response = response
        try:
            if self.closed:
                raise ValueError('MCP connection is closed.')
            if message['method'] == 'initialize':
                self.session = response.headers.get('Mcp-Session-Id')
            if 'id' not in message:
                return None
            content_type = response.headers.get_content_type()
            if content_type == 'application/json':
                raw = response.read(MAX_BYTES + 1)
                if len(raw) > MAX_BYTES:
                    raise ValueError('MCP response exceeds the supported size. Request less data.')
                return json.loads(raw)
            if content_type != 'text/event-stream':
                raise ValueError('MCP service must return JSON or an event stream.')
            size, parts = 0, []
            while not self.closed:
                line = response.readline(MAX_BYTES + 1)
                size += len(line)
                if size > MAX_BYTES:
                    raise ValueError('MCP event stream exceeds the supported size.')
                if not line or line.strip() == b'':
                    if parts:
                        data = json.loads('\n'.join(parts))
                        parts = []
                        if data.get('id') == message['id']:
                            return data
                    if not line:
                        break
                elif line.startswith(b'data:'):
                    parts.append(line[5:].decode('utf-8').strip())
            raise ValueError('MCP stream ended without the requested result.')
        finally:
            response.close()
            self.response = None

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.response is not None:
            # Closing a response being read can wait for its socket timeout. Do not
            # block the caller's cancellation deadline on the reader's IO lock.
            threading.Thread(target=self.response.close, daemon=True, name='aether-mcp-close').start()
        if self.session:
            headers = {'Mcp-Session-Id': self.session, 'MCP-Protocol-Version': self.version}
            if self.token:
                headers['Authorization'] = 'Bearer ' + self.token
            def release():
                try:
                    with self.opener.open(Request(self.url, headers=headers, method='DELETE'), timeout=min(2, self.timeout)):
                        pass
                except Exception:
                    pass  # Some servers do not support explicit session termination.
            threading.Thread(target=release, daemon=True, name='aether-mcp-session-end').start()


def exchange(source, secret, operation, cancelled=lambda: False):
    """Open a fresh session; do not retry a tool call with an uncertain outcome."""
    transport = (HttpTransport(source['url'], secret, source['timeout']) if source['transport'] == 'http'
                 else StdioTransport(source['command'], cwd=source.get('cwd') or None, max_message_bytes=MAX_BYTES))
    client = McpClient(transport, timeout=source['timeout'], max_response_bytes=MAX_BYTES, label='Data source')
    output = []
    done = threading.Event()

    def work():
        try:
            init = client.request('initialize', dict(protocolVersion=VERSIONS[0], capabilities={},
                                                    clientInfo=dict(name='aether-data-sources', version='1')))
            if not isinstance(init, dict) or init.get('protocolVersion') not in VERSIONS:
                raise ValueError('The MCP service selected an unsupported protocol version.')
            if not isinstance(init.get('capabilities', {}).get('tools'), dict):
                raise ValueError('This MCP service does not advertise tools.')
            if isinstance(transport, HttpTransport):
                transport.version = init['protocolVersion']
            client.notify('notifications/initialized')
            output.append((True, operation(client)))
        except Exception as exc:
            output.append((False, exc))
        finally:
            done.set()

    worker = threading.Thread(target=work, daemon=True, name='aether-source-mcp')
    try:
        if cancelled():
            raise ValueError('Report stopped before the source request.')
        worker.start()
        deadline = time.monotonic() + source['timeout']
        while not done.wait(0.05):
            if cancelled():
                raise ValueError('Report stopped. The service may finish its database request; no result will be published.')
            if time.monotonic() >= deadline:
                raise ValueError('MCP request timed out. Its outcome is unknown; it was not retried.')
        if cancelled():
            raise ValueError('Report stopped; no result was published.')
        if not output[0][0]:
            raise output[0][1]
        return output[0][1]
    finally:
        client.close()
        if worker.is_alive():
            worker.join(timeout=0.25)
