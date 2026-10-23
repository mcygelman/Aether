"""Fixed-file settings routes shared by the standalone editor and Mission Control."""
from __future__ import annotations
from aether.paths import BRIDGE_ROOT

import json
import socket
import threading
import time
from pathlib import Path

from aether.config.context_configuration import ContextConfigurationError
from aether.credentials.redaction import redact  # Error text only; settings documents are never redacted.
from aether.config.model_settings_store import StaleSettings, SettingsAuditError, parse_json
from aether.models.settings_store import preview
from aether.models.model_budget_presets import BudgetPresetStore

PAGE = BRIDGE_ROOT / 'model_context_settings.html'
MISSION_SETTINGS_PATH = '/settings/models-context/'
# G9 lane D: deployment admission for all campaign roles (the Runtime's deployment_admission.ACTIONS).
DEPLOYMENT_PREFIX = 'api/deployment/'
DEPLOYMENT_ACTIONS = ('observe', 'inspect', 'review', 'confirm', 'recover', 'hosts-review', 'hosts-confirm',
                      'renew-review', 'renew-confirm', 'reidentify-review', 'reidentify-confirm',
                      'heavyweight-review', 'heavyweight-confirm')

def with_token_defaults(value):
    from aether.config.token_count_policy import load_default
    try:return dict(value,tokenCountPolicyDefaults=load_default().to_mapping())
    except (OSError,ValueError,TypeError) as exc:
        # Defaults are optional display metadata for explicit-policy/legacy profiles.
        # In particular, do not misreport a committed profile save as a failure.
        return dict(value,tokenCountPolicyDefaults=None,tokenCountPolicyDefaultsError=redact(str(exc)))


class _CountingReader:
    """The request stream, counting the bytes handed to readers, so a response can tell how much of
    the declared body is still unread. Everything else is the wrapped reader's."""

    def __init__(self, raw):
        self._raw = raw
        self.consumed = 0

    def _count(self, data):
        self.consumed += len(data)
        return data

    def read(self, *args):
        return self._count(self._raw.read(*args))

    def read1(self, *args):
        return self._count(self._raw.read1(*args))

    def readline(self, *args):
        return self._count(self._raw.readline(*args))

    def readinto(self, buffer):
        n = self._raw.readinto(buffer)
        self.consumed += n or 0
        return n

    def __iter__(self):
        return iter(self.readline, b'')

    def __getattr__(self, name):
        return getattr(self._raw, name)


# Disposition of a request body still unread when the response is written (S4, Praxis 10-11).
# Answering, then closing, while the client is still sending lets Windows abort the connection
# (WinError 10053): the client never sees the answer. A declared body within the cap is read and
# discarded first, against a short deadline. Ambiguous framing (Transfer-Encoding, a missing or
# malformed Content-Length on POST/PUT/PATCH, a length over the cap, a stalled or short body) is
# never awaited: the answer goes out with Connection: close, writes are half-closed, and the
# connection is read briefly and discarded so the client can receive the answer before it closes.
UNREAD_BODY_CAP = 1024 * 1024
UNREAD_BODY_SECONDS = 2.0
LINGER_SECONDS = 5.0
LINGER_BYTES = 64 * 1024 * 1024
_BODY_METHODS = ('POST', 'PUT', 'PATCH')


class SettingsHTTPMixin:
    """Use with BaseHTTPRequestHandler; returns False for routes it does not own."""

    def setup(self):
        super().setup()
        self.rfile = _CountingReader(self.rfile)

    def parse_request(self):
        parsed = super().parse_request()
        # Where this request's body starts (keep-alive reuses one handler for several requests).
        self._body_start = getattr(self.rfile, 'consumed', None)
        self._body_disposed = False
        return parsed

    def dispose_request_body(self):
        """Call before writing a response. Reads and discards a bounded, still-unread declared body
        (short deadline). Returns True when the framing is ambiguous or the body did not arrive:
        the caller then answers with Connection: close and calls linger_close() after writing.
        A handler without stream tracking (test doubles) is left exactly as before: False."""
        start = getattr(self, '_body_start', None)
        sock = getattr(self, 'connection', None)
        if start is None or sock is None or getattr(self, '_body_disposed', True):
            return False
        self._body_disposed = True
        headers = self.headers
        lengths = headers.get_all('Content-Length') or []
        if headers.get('Transfer-Encoding') is not None or len(set(v.strip() for v in lengths)) > 1:
            return self._must_close()
        if not lengths:
            return self._must_close() if self.command in _BODY_METHODS else False
        declared = lengths[0].strip()
        # Any length with more digits than the cap's is over the cap; never convert it (H1: Python's
        # int() refuses over 4300 digits, which would lose the refusal itself).
        if not declared.isdigit() or not declared.isascii() or len(declared.lstrip('0')) > len(str(UNREAD_BODY_CAP)):
            return self._must_close()
        remaining = int(declared) - (self.rfile.consumed - start)
        if remaining <= 0:
            return False
        if remaining > UNREAD_BODY_CAP:
            return self._must_close()
        deadline = time.monotonic() + UNREAD_BODY_SECONDS
        previous = sock.gettimeout()
        try:
            while remaining > 0:
                left = deadline - time.monotonic()
                if left <= 0:
                    return self._must_close()
                sock.settimeout(left)
                chunk = self.rfile.read1(min(remaining, 65536))
                if not chunk:
                    return self._must_close()
                remaining -= len(chunk)
        except OSError:   # includes socket.timeout: the stream is unusable; never wait again
            return self._must_close()
        finally:
            try:
                sock.settimeout(previous)
            except OSError:
                pass
        return False

    def _must_close(self):
        self.close_connection = True
        return True

    def linger_close(self):
        """After an answer with Connection: close: half-close writes, then read and discard briefly
        (bounded in time and bytes) so the client receives the answer instead of a reset."""
        self.close_connection = True
        sock = self.connection
        try:
            self.wfile.flush()
            sock.shutdown(socket.SHUT_WR)
        except OSError:
            return
        deadline = time.monotonic() + LINGER_SECONDS
        discarded = 0
        try:
            while discarded < LINGER_BYTES:
                left = deadline - time.monotonic()
                if left <= 0:
                    return
                sock.settimeout(left)
                chunk = sock.recv(min(65536, LINGER_BYTES - discarded))   # H2: never past the byte bound
                if not chunk:
                    return
                discarded += len(chunk)
        except OSError:
            return

    def settings_send(self, value, status=200, *, html=False):
        body = value if html else json.dumps(value, ensure_ascii=False).encode('utf-8')
        linger = self.dispose_request_body()
        self.send_response(status)
        self.send_header('Content-Type', 'text/html; charset=utf-8' if html else 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'")
        # Refused requests can have unread bodies. Do not let them become another
        # request on Mission Control's HTTP/1.1 connection.
        self.send_header('Connection', 'close')
        self.close_connection = True
        self.end_headers()
        self.wfile.write(body)
        if linger:
            self.linger_close()

    def runtime_admission(self):
        """The server's injected admission store (governed Mission Control only), or None."""
        state = getattr(getattr(self, 'server', None), 'state', None)
        return state.get('runtime_admission') if isinstance(state, dict) else None

    def server_state(self, name):
        """One injected governed service from the server state (governed Mission Control only), or None."""
        state = getattr(getattr(self, 'server', None), 'state', None)
        return state.get(name) if isinstance(state, dict) else None

    def handle_settings(self, method, store, *, prefix='/', embedded=False):
        if not self.path.startswith(prefix) and self.path != prefix.rstrip('/'):
            return False
        origin = f'http://127.0.0.1:{self.server.server_port}'
        if self.headers.get('Host') != origin.removeprefix('http://'):
            self.settings_send({'error': 'Unrecognized local host'}, 403)
            return True
        if method != 'GET' and self.headers.get('Origin') != origin:
            # settings_send disposes of the refused body (bounded, with a deadline) before answering:
            # answering while the client is still sending lets Windows reset the connection.
            self.settings_send({'error': 'Use the local settings page'}, 403)
            return True
        # The trailing slash gives both hosts the same relative API URLs.
        if method == 'GET' and prefix != '/' and self.path == prefix.rstrip('/'):
            self.send_response(303)
            self.send_header('Location', prefix)
            self.send_header('Content-Length', '0')
            self.end_headers()
            return True
        route = self.path[len(prefix):]
        try:
            if method == 'GET':
                if route == '':
                    page = PAGE.read_text(encoding='utf-8').replace(
                        'const settingsHost = {embedded:false}; // SETTINGS_HOST',
                        'const settingsHost = ' + json.dumps({'embedded': embedded}) + ';',
                    )
                    page=page.replace('let tokenPolicyDefaults = null; // TOKEN_POLICY_DEFAULTS',
                        'let tokenPolicyDefaults = '+json.dumps(with_token_defaults({})['tokenCountPolicyDefaults'])+';')
                    self.settings_send(page.encode('utf-8'), html=True)
                elif route == 'api/settings':
                    try:
                        value = store.read()
                    except (OSError, ValueError, TypeError) as exc:
                        # M2 (MVP-08 T01): an unreadable registry is identified, never hidden behind a bare error;
                        # the page offers the attributed repair (settings_repair) that retains the corrupt bytes.
                        from aether.backends.settings_repair import inspect
                        self.settings_send(dict(inspect(store), registry_unreadable=True, error=redact(str(exc))), 409)
                        return True
                    self.settings_send(with_token_defaults(value))
                elif route == 'api/registry':
                    from aether.backends.settings_repair import inspect
                    self.settings_send(inspect(store))
                elif route == 'api/presets':
                    self.settings_send(BudgetPresetStore(store.path).read())
                elif route == 'api/operators':
                    self.settings_send({'operators': store.registered_operators()})
                elif route == 'api/deployment-adapters':
                    from aether.backends.deployment_probe import adapter_status
                    self.settings_send(adapter_status())
                else:
                    self.settings_send({'error': 'Not found'}, 404)
                return True
            if method != 'POST':
                self.settings_send({'error': 'Method not allowed'}, 405)
                return True
            if route == 'api/close' and not embedded:
                self.settings_send({'closed': True})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return True
            deployment_action = route[len(DEPLOYMENT_PREFIX):] if route.startswith(DEPLOYMENT_PREFIX) else None
            if deployment_action not in DEPLOYMENT_ACTIONS and route not in (
                    'api/preview', 'api/save', 'api/migrate', 'api/presets', 'api/probe', 'api/observe', 'api/admit', 'api/repair'):
                self.settings_send({'error': 'Not found'}, 404)
                return True
            if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                self.settings_send({'error': 'JSON required'}, 415)
                return True
            length = int(self.headers.get('Content-Length', '0'))
            if self.headers.get('Transfer-Encoding') or not 0 < length <= 1024*1024:
                self.settings_send({'error': 'Invalid settings request size'}, 413)
                return True
            payload = parse_json(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ContextConfigurationError('Settings request must be an object')
            if route in ('api/save', 'api/migrate', 'api/presets', 'api/repair'):
                if 'operator_id' not in payload or 'operator' in payload:
                    raise ContextConfigurationError('Settings writes require operator_id, not an operator label')
            if route == 'api/preview':
                result = preview(payload.get('document'), payload.get('profile'),
                                 payload.get('request_tokens'), payload.get('measurement', 'estimate'))
            elif route == 'api/presets':
                result = BudgetPresetStore(store.path, operator_registry_path=store.operator_registry_path,
                                           operators=store.operators).save(
                    payload.get('document'), payload.get('revision'), operator=payload['operator_id'])
            elif route == 'api/probe':
                from aether.backends.deployment_probe import probe_saved
                if set(payload) != {'profile_id', 'revision', 'timeout_seconds'}:
                    raise ContextConfigurationError('Probe requires profile_id, revision and timeout_seconds only')
                result = probe_saved(store, payload['profile_id'], payload['revision'], payload['timeout_seconds'])
            elif deployment_action is not None:
                # G9 lane D: admit one deployment for ALL campaign roles (observe, review, confirm, inspect,
                # recover, host configurations, renew, re-identify). Mission Control composed with --governed
                # supplies the Runtime's DeploymentAdmission; this module never imports the Runtime.
                deployment = self.server_state('deployment_admission')
                if deployment is None:
                    self.settings_send({'error': 'Deployment admission needs Mission Control started with --governed.'}, 409)
                    return True
                result = deployment.handle(store, deployment_action, payload)
            elif route in ('api/observe', 'api/admit'):
                # G9: observe the runtime identity (no inference, load or tokenization) and admit a
                # host. Mission Control composed with --governed supplies the admission store; this
                # module never imports the Runtime, which writes the evidence and host files.
                admission = self.runtime_admission()
                if admission is None:
                    self.settings_send({'error': 'Runtime observation and admission need Mission Control '
                                                 'started with --governed.'}, 409)
                    return True
                if route == 'api/admit':
                    result = admission.admit(store, payload)
                else:
                    from aether.backends.runtime_observation import observe_saved
                    if set(payload) != {'profile_id', 'revision', 'timeout_seconds'}:
                        raise ContextConfigurationError('Observe requires profile_id, revision and timeout_seconds only')
                    result = admission.record_observation(observe_saved(
                        store, payload['profile_id'], payload['revision'], payload['timeout_seconds']))
            elif route == 'api/repair':
                from aether.backends.settings_repair import repair
                if set(payload) != {'operator_id', 'corrupt_sha256', 'restore_kind', 'restore_revision'}:
                    raise ContextConfigurationError('Repair requires operator_id, corrupt_sha256, restore_kind and restore_revision only')
                result = repair(store, operator_id=payload['operator_id'], corrupt_sha256=payload['corrupt_sha256'],
                                restore=(payload['restore_kind'], payload['restore_revision']))
                result = with_token_defaults(result)
            elif route == 'api/migrate':
                result = store.save(payload.get('document'), payload.get('revision'), migrate=True,
                                    operator=payload['operator_id'])
            else:
                result = store.save(payload.get('document'), payload.get('revision'), operator=payload['operator_id'])
            if route in ('api/save', 'api/migrate', 'api/repair'):
                # Retain the confirmed bytes as a known-good copy for a later repair (M2); a failure to retain is
                # reported with the saved result, never a failed save.
                from aether.backends.settings_repair import retain_known_good
                try:
                    result['known_good_retained'] = retain_known_good(store)
                except (OSError, ValueError) as exc:
                    result['known_good_retained'] = None
                    result['known_good_error'] = redact(str(exc))
            self.settings_send(with_token_defaults(result) if route in ('api/save','api/migrate') else result)
        except (StaleSettings, FileExistsError) as exc:
            self.settings_send({'error': redact(str(exc))}, 409)
        except SettingsAuditError as exc:
            self.settings_send({'error': redact(str(exc)), 'replacement_performed': exc.replaced,
                                'attempted_revision': exc.new_revision, 'automatic_retry': False}, 503)
        except (OSError, ValueError, TypeError) as exc:
            # Observe/admit failures can carry provider text: registered secrets never reach the page.
            self.settings_send({'error': redact(str(exc))}, 400)
        return True
