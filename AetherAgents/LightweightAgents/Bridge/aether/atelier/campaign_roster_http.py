"""Campaign roster page and local-only assignment API; saving never starts a turn."""
from urllib.parse import parse_qs, urlparse
from aether.integrity.http_body import json_body
from aether.paths import WEB_DIR

PAGE = '/projects/roster'
API = '/api/campaign-roster'


def handle_campaign_roster(handler, method, state):
    parsed = urlparse(handler.path)
    if parsed.path not in (PAGE, API):
        return False
    origin = f'http://127.0.0.1:{handler.server.server_port}'
    if handler.headers.get('Host') != origin.removeprefix('http://') or (method != 'GET' and handler.headers.get('Origin') != origin):
        handler.settings_send(dict(error='Use this local Aether page.'), 403)
        return True
    try:
        store = state['campaign_roster']
        if method == 'GET' and parsed.path == PAGE:
            handler.settings_send((WEB_DIR / 'campaign_roster.html').read_bytes(), html=True)
        elif method == 'GET' and parsed.path == API:
            query = parse_qs(parsed.query)
            handler.settings_send(store.staff() if query.get('staff') == ['1'] else store.view((query.get('commission') or [None])[0]))
        elif method == 'POST' and parsed.path == API:
            body = json_body(handler, 32768, 'Send the roster assignment as JSON.')
            if not isinstance(body, dict):
                raise ValueError('Send an object.')
            handler.settings_send(store.save(body))
        else:
            handler.settings_send(dict(error='Not found'), 404)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        from aether.keys.redaction import redact
        handler.settings_send(dict(error=redact(str(exc))), 409)
    return True
