"""Own authenticated specialist API dispatch; never install unvalidated contracts or caller filesystem paths."""
from urllib.parse import urlparse, parse_qs
from pathlib import Path
from aether.integrity.http_body import json_body
from . import specialist_catalog, specialist_install

API='/api/agent-catalog'

def handle_specialists(handler, method, state):
    path=urlparse(handler.path).path
    if path!=API and not path.startswith(API+'/'): return False
    origin=f'http://127.0.0.1:{handler.server.server_port}'
    if handler.headers.get('Host')!=origin.removeprefix('http://') or (method!='GET' and handler.headers.get('Origin')!=origin):
        handler.settings_send(dict(error='Use this local Aether page.'),403);return True
    try:
        if path.startswith(API+'/custom'):
            from .custom_templates_http import handle_custom
            return handle_custom(handler,method,state)
        if path.startswith(API+'/learning'):
            from .specialist_learning_http import handle_learning
            return handle_learning(handler,method,state)
        if method=='GET' and path==API:
            doc=specialist_catalog.load()
            handler.settings_send(dict(doc,templates=[specialist_catalog.template(r['id'],doc) for r in doc['templates']],
                connections=specialist_install.connections(state['model_settings']),default_folder=state['agent_registry'].default_folder))
        elif method=='POST' and path==API+'/install':
            from .atelier_http import root_of
            body=json_body(handler,16384,'Send the catalog selection as JSON.')
            if not isinstance(body,dict):raise ValueError('Send an object.')
            handler.settings_send(specialist_install.install(state['agent_registry'],root_of(state),state['model_settings'],body))
        else:handler.settings_send(dict(error='Not found'),404)
    except (OSError,ValueError,KeyError,TypeError) as exc:
        from aether.keys.redaction import redact
        handler.settings_send(dict(error=redact(str(exc))),409)
    return True
