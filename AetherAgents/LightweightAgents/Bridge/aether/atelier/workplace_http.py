"""Mission Control's local place controls. Reads never start a place or call a model."""
from urllib.parse import urlparse

from aether.integrity.http_body import json_body
from aether.paths import WEB_DIR

PAGE = '/projects/places'
API = '/api/places'
PATHS = {PAGE, API, API + '/join', API + '/start', API + '/stop', API + '/prepare-library'}


def handle_workplace(handler, method, state):
    path = urlparse(handler.path).path
    if path not in PATHS:
        return False
    if (method == 'GET') != (path in {PAGE, API}):
        handler.settings_send(dict(error='Method not allowed'), 405)
        return True
    if method == 'POST' and handler.headers.get('Origin') != f'http://127.0.0.1:{handler.server.server_port}':
        handler.settings_send(dict(error='Use this Mission Control page to manage its place.'), 403)
        return True
    try:
        workplace = state.get('workplace')
        if workplace is None:
            raise ValueError('This server has no workplace settings configured.')
        if path == PAGE:
            handler.settings_send((WEB_DIR / 'places.html').read_bytes(), html=True)
        elif method == 'GET':
            handler.settings_send(workplace.status())
        else:
            body = json_body(handler, 64 * 1024, 'Send place settings as JSON')
            if not isinstance(body, dict):
                raise ValueError('Send an object.')
            action = path.rsplit('/', 1)[-1]
            value = {'join': lambda: workplace.save(body), 'start': workplace.start,
                     'stop': workplace.stop, 'prepare-library': lambda: workplace.prepare_library(body)}[action]()
            handler.settings_send(value)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        from aether.credentials.redaction import redact
        handler.settings_send(dict(error=redact(str(exc))), 409)
    return True
