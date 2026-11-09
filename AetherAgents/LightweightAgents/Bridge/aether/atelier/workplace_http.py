"""Mission Control's local place controls. Reads never start a place or call a model."""
from urllib.parse import urlparse

from aether.integrity.http_body import json_body
from aether.paths import WEB_DIR

PAGE = '/projects/places'
API = '/api/places'
PATHS = {PAGE, API, API + '/join', API + '/start', API + '/start-here', API + '/stop', API + '/prepare-library',
         API + '/networks', API + '/join-network', API + '/name-network', API + '/choose-folder'}
READS = {PAGE, API, API + '/networks'}


def handle_workplace(handler, method, state):
    path = urlparse(handler.path).path
    if path not in PATHS:
        return False
    if (method == 'GET') != (path in READS):
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
        elif path == API + '/networks':
            from .atelier_http import root_of
            from .workplace import here_name
            local = state.get('atelier_root') or root_of(dict(state, workplace=None))
            handler.settings_send(dict(networks=workplace.networks(local), here=here_name(),
                                       status=workplace.status()))
        elif method == 'GET':
            value = workplace.status()
            if not value['config']:
                # What Start working will use on this computer (GUI review S7); nothing is saved by reading.
                from .atelier_http import root_of
                from .workplace import here_name
                value['suggested'] = dict(share=str(root_of(state)), name=here_name())
            handler.settings_send(value)
        else:
            body = json_body(handler, 64 * 1024, 'Send place settings as JSON')
            if not isinstance(body, dict):
                raise ValueError('Send an object.')
            action = path.rsplit('/', 1)[-1]
            def start_here():
                from .atelier_http import root_of
                settings = state.get('model_settings')
                return workplace.start_here(root_of(state), getattr(settings, 'path', None))
            def join_network():
                settings = state.get('model_settings')
                return workplace.join_network(body.get('folder'), name=body.get('name'), node=body.get('node'),
                                              profiles=getattr(settings, 'path', None))
            def name_network():
                from .workplace import name_network as rename
                return dict(name=rename(body.get('folder'), body.get('name')))
            def choose_network_folder():
                from .agent_registry_http import choose_folder
                return dict(path=choose_folder('Choose the Aether network folder (joining is a separate action)'))
            value = {'join': lambda: workplace.save(body), 'start': workplace.start, 'start-here': start_here,
                     'stop': workplace.stop, 'prepare-library': lambda: workplace.prepare_library(body),
                     'join-network': join_network, 'name-network': name_network,
                     'choose-folder': choose_network_folder}[action]()
            handler.settings_send(value)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        from aether.keys.redaction import redact
        handler.settings_send(dict(error=redact(str(exc))), 409)
    return True
