"""G9 lane F: a local scripted provider for the offline campaign journey.

A real HTTP server on 127.0.0.1 shaped like an openai-compatible / DeepSeek API:
``GET /v1/models`` lists the served model and ``POST /v1/chat/completions`` answers each
campaign role from its own script. Spawned host workers reach it through Aether's DEFAULT
production adapters, unwrapped: nothing in the child is patched.

Roles are recognised from the request itself (``ROLE_MARKERS``: text the role's own prompt
always carries) and checked against the role the journey armed for that launch; a request
that matches no armed role is answered 500 and recorded, so an unexpected caller fails the
journey instead of receiving another role's answer.

Every request is recorded (method, path, role, whether the planted credential was the
Authorization, and the decoded JSON body). The Authorization header itself is NEVER kept:
only the boolean comparison, so the recorder cannot become a leak.

``leaks(root, secret)`` and ``scan_texts(secret, *texts)`` are the key-leak scans (the
approach of test_production_adapters' DeepSeek end-to-end test).
"""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading

MODEL = 'test-research'

# Text each role's request always carries (system or user content). Checked in order; the first
# role whose marker appears in the request is the detected role. Unknown markers -> None.
ROLE_MARKERS = (
    ('planning', ('CONFIRMED GENERATION AUTHORITY',)),
    ('minder', ('Minder',)),
    ('review', ('findings', 'Findings')),
    ('staffing', ('staff', 'Staff')),
    ('contribution', ('deliverable', 'artifacts')),
    ('closure', ('closure', 'Closure')),
)


def request_text(body):
    """Every message's text content in one string (tool calls and non-text parts skipped)."""
    parts = []
    for message in body.get('messages') or []:
        content = message.get('content')
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            parts.extend(p.get('text', '') for p in content if isinstance(p, dict))
    return '\n'.join(parts)


def detect(body, armed=()):
    """The role this request belongs to: the first ARMED role whose markers appear, else the first
    role of ROLE_MARKERS whose markers appear, else None."""
    text = request_text(body)
    ordered = [r for r in ROLE_MARKERS if r[0] in armed] + [r for r in ROLE_MARKERS if r[0] not in armed]
    for role, markers in ordered:
        if any(m in text for m in markers):
            return role
    return None


class JourneyProvider:
    """Scripted provider. ``arm(role, replies)`` queues the answers for one role's launch.

    A reply is a message dict (``content`` and/or ``tool_calls``) or a callable ``body -> dict``.
    ``usage`` may be given as ``_usage`` inside the dict.
    """

    def __init__(self, model=MODEL, *, secret=None, listed=('another-model',)):
        outer = self
        self.model, self.secret, self.listed = model, secret, tuple(listed)
        self.requests = []          # dicts: method, path, role, authorized, body (POST only)
        self.scripts = {}           # role -> [reply, ...]
        self.unexpected = []        # requests nothing was armed for
        self.lock = threading.Lock()

        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def log_message(self, *args):  # Keep stderr clean; requests are recorded instead.
                pass

            def reply(self, value, status=200):
                raw = json.dumps(value).encode()
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def authorized(self):
                value = self.headers.get('Authorization')
                if outer.secret is None:
                    return value is None
                return value == 'Bearer ' + outer.secret

            def do_GET(self):
                with outer.lock:
                    outer.requests.append(dict(method='GET', path=self.path, role=None,
                                               authorized=self.authorized(), body=None))
                if self.path != '/v1/models':
                    return self.reply(dict(error='not scripted'), 404)
                self.reply({'object': 'list', 'data': [dict(id=i, object='model', owned_by='journey', created=1760000000)
                                                       for i in (outer.model, *outer.listed)]})

            def do_POST(self):
                length = int(self.headers.get('Content-Length') or 0)
                body = json.loads(self.rfile.read(length) or b'{}')
                with outer.lock:
                    armed = tuple(r for r, q in outer.scripts.items() if q)
                    role = detect(body, armed)
                    if role not in armed and len(armed) == 1:
                        role = armed[0] if detect(body) is None else role
                    entry = dict(method='POST', path=self.path, role=role, authorized=self.authorized(), body=body)
                    outer.requests.append(entry)
                    queue = outer.scripts.get(role) or []
                    ok = bool(self.path == '/v1/chat/completions' and body.get('model') == outer.model and queue)
                    message = queue.pop(0) if ok else None
                    entry['answered'] = ok
                    if not ok:
                        outer.unexpected.append(entry)
                if message is None:
                    return self.reply(dict(error=dict(message='not scripted for this request')), 500)
                message = dict(message(body) if callable(message) else message)
                usage = message.pop('_usage', dict(prompt_tokens=10, completion_tokens=5, total_tokens=15))
                message.setdefault('content', '')
                finish = 'tool_calls' if message.get('tool_calls') else 'stop'
                self.reply(dict(id=f'journey-{len(outer.requests)}', object='chat.completion', created=1,
                                model=outer.model, usage=usage,
                                choices=[dict(index=0, message=dict(message, role='assistant'), finish_reason=finish)]))

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.endpoint = f'http://127.0.0.1:{self.server.server_port}/v1'
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    # -- scripting -------------------------------------------------------------------------
    def arm(self, role, replies):
        with self.lock:
            self.scripts.setdefault(role, []).extend(replies)

    def remaining(self, role=None):
        with self.lock:
            return {r: len(q) for r, q in self.scripts.items() if q and (role is None or r == role)}

    # -- assertions ------------------------------------------------------------------------
    def posts(self, role=None):
        return [r for r in self.requests if r['method'] == 'POST' and (role is None or r['role'] == role)]

    def gets(self):
        return [r for r in self.requests if r['method'] == 'GET']

    def all_authorized(self):
        return all(r['authorized'] for r in self.requests)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(10)


# -- key-leak scans ------------------------------------------------------------------------

def leaks(root, secret):
    """Every file under `root` holding the secret's bytes (UTF-8, UTF-16 and hex forms)."""
    needles = (secret.encode(), secret.encode('utf-16-le'), secret.encode().hex().encode())
    found = []
    for path in Path(root).rglob('*'):
        try:
            if path.is_file() and any(n in path.read_bytes() for n in needles):
                found.append(str(path))
        except OSError:
            continue
    return found


def scan_texts(secret, *texts):
    """The texts (HTTP answers, logs) that contain the secret."""
    return [t[:200] for t in texts if secret in (t if isinstance(t, str) else json.dumps(t, default=str))]
