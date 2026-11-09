"""Authentication and authorization at the public Atelier dispatch boundary."""
from http.cookies import SimpleCookie, CookieError
import json
from urllib.parse import quote, urlparse

from aether.integrity.http_body import json_body
from .operator_access import can, PERMISSIONS, SESSION_SECONDS

API = '/api/access'


def cookie_name(handler):
    # Cookies have no port isolation; keep concurrent local installations separate.
    return 'aether_operator_' + str(handler.server.server_port)


def token_of(handler):
    try:
        cookie = SimpleCookie(handler.headers.get('Cookie', ''))
        item = cookie.get(cookie_name(handler))
        return item.value if item else ''
    except CookieError:
        return ''


def answer(handler, value, status=200, token=None):
    raw = json.dumps(value, ensure_ascii=False).encode()
    linger = handler.dispose_request_body()
    handler.send_response(status)
    handler.send_header('Content-Type', 'application/json; charset=utf-8')
    handler.send_header('Content-Length', str(len(raw)))
    handler.send_header('Cache-Control', 'no-store')
    handler.send_header('X-Content-Type-Options', 'nosniff')
    if token is not None:
        handler.send_header('Set-Cookie', f'{cookie_name(handler)}={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age={SESSION_SECONDS if token else 0}')
    handler.send_header('Connection', 'close')
    handler.close_connection = True
    handler.end_headers()
    handler.wfile.write(raw)
    if linger:
        handler.linger_close()


def workspace_allowed(actor, body, doc):
    if can(actor, 'admin'):
        return True
    action = body.get('action')
    if action in ('guide_save', 'guide_archive'):
        return can(actor, 'manage_guides')
    if action == 'dashboard_save':
        return can(actor, 'manage_dashboards')
    if action in ('halt', 'resume', 'installation_save'):
        return can(actor, 'manage_settings')
    if action in ('artifact_save', 'artifact_archive') and can(actor, 'manage_teach'):
        existing = next((a for a in doc['artifacts'] if a['id'] == body.get('id')), None)
        if body.get('id') and (not existing or existing['kind'] != 'teachframe'):
            return False
        return (action == 'artifact_archive' and existing is not None or
                action == 'artifact_save' and body.get('kind') == 'teachframe')
    return False


def mutation_permission(path):
    if path.startswith('/api/tasker/'):
        return 'manage_missions'
    if path in ('/api/data-sources/settings', '/api/data-sources/discover'):
        return 'manage_data_sources'
    if path.startswith('/api/data-sources/'):
        return 'use_data_sources'
    if path.startswith('/api/model-chat/'):
        return 'chat'
    if path == '/api/workspace':
        return 'workspace'  # The parsed action and existing record are checked by its handler.
    if path.startswith('/settings/api-keys/'):
        return 'key_access'
    if path.startswith('/settings/'):
        return 'manage_settings'
    if path.startswith(('/api/agent-availability/', '/api/agent-catalog/')) or path in (
            '/api/agent-editor/validate', '/api/agent-editor/export'):
        return 'manage_agents'
    if path.startswith('/api/places/'):
        return 'manage_settings'
    if path in ('/api/campaigns', '/api/campaign-roster') or path.startswith('/api/atelier/'):
        return 'manage_missions'
    return 'admin'


def guard(handler, method, state):
    access = state.get('operator_access')
    if access is None:
        return False  # Synthetic/legacy embedded handlers; serve() always supplies access.
    path = urlparse(handler.path).path
    token = token_of(handler)
    handler.operator = access.user(token)
    if method != 'GET' and handler.headers.get('Origin') != f'http://127.0.0.1:{handler.server.server_port}':
        answer(handler, dict(error='Use this installation’s own page.'), 403)
        return True
    if path.startswith(API + '/'):
        try:
            route = path[len(API) + 1:]
            if method == 'GET' and route == 'session':
                answer(handler, dict(user=handler.operator, first_account=not access.read()['accounts'], permissions=PERMISSIONS,
                                     welcome=access.welcome_due(handler.operator)))
            elif method == 'POST' and route in ('register', 'login'):
                body = json_body(handler, 8192, 'Send account details as JSON.')
                if not isinstance(body, dict):
                    raise ValueError('Send an account object.')
                user, fresh = access.register(body) if route == 'register' else access.login(body)
                access.logout(token)
                answer(handler, dict(user=user), token=fresh)
            elif not handler.operator:
                answer(handler, dict(error='Sign in to continue.'), 401)
            elif method == 'POST' and route == 'logout':
                access.logout(token)
                answer(handler, dict(signed_out=True), token='')
            elif method == 'POST' and route == 'password':
                body = json_body(handler, 8192, 'Send account details as JSON.')
                if not isinstance(body, dict):
                    raise ValueError('Send an account object.')
                user, fresh = access.change_password(handler.operator, body)
                answer(handler, dict(user=user), token=fresh)
            elif method == 'POST' and route == 'welcome':
                # The one-time welcome of the default Admin (Michel, 2026-11-07): its own password, then its API key.
                body = json_body(handler, 16384, 'Send the welcome as JSON.')
                if not isinstance(body, dict):
                    raise ValueError('Send a welcome object.')
                from . import welcome
                value, fresh = welcome.complete(access, handler.operator, body, state, token)
                answer(handler, value, token=fresh)
            elif route in ('roles','key-assignments') and method in ('GET','POST'):
                from . import operator_roles, key_assignments
                if route == 'roles':
                    if method == 'GET': answer(handler, access.listing())
                    else:
                        body=json_body(handler,16384,'Send role details as JSON.')
                        if not isinstance(body,dict) or type(body.get('revision')) is not int:raise ValueError('Refresh roles before saving.')
                        answer(handler,operator_roles.change(access,handler.operator,body))
                else:
                    runtime=state['runtime_keys']
                    unlocked=key_assignments.lease(access,token,runtime.vault)
                    if method=='GET':answer(handler,key_assignments.listing(access,handler.operator,runtime,unlocked))
                    else:
                        body=json_body(handler,16384,'Send assignments as JSON.')
                        if not isinstance(body,dict) or type(body.get('revision')) is not int:raise ValueError('Refresh assignments before saving.')
                        answer(handler,key_assignments.change(access,handler.operator,body,runtime,unlocked))
            elif route == 'accounts' and method in ('GET', 'POST'):
                if method == 'GET':
                    answer(handler, access.listing())
                elif not can(handler.operator, 'manage_accounts'):
                    answer(handler, dict(error='Only Admins can manage operator accounts.'), 403)
                else:
                    body = json_body(handler, 16384, 'Send account details as JSON.')
                    if not isinstance(body, dict) or type(body.get('revision')) is not int:
                        raise ValueError('Refresh the account directory before saving.')
                    action = body.get('action', 'update')
                    if action not in ('update', 'create', 'set_password', 'delete'):
                        raise ValueError('Choose a supported account action.')
                    operation = {'update': access.update, 'create': access.create_account,
                                 'set_password': access.set_password, 'delete': access.delete_account}[action]
                    answer(handler, operation(handler.operator, body))
            else:
                answer(handler, dict(error='Account route not found.'), 404)
        except PermissionError as exc:
            answer(handler, dict(error=str(exc)), 403)
        except (ValueError, TypeError, KeyError) as exc:
            answer(handler, dict(error=str(exc)), 409)
        return True
    public_asset = path.startswith('/workspace-assets/') or path.endswith(('.js', '.css'))
    if method == 'GET' and (path in ('/login', '/api/health') or public_asset):
        return False
    if not handler.operator:
        if method == 'GET' and '/api/' not in path:
            handler.send_response(302)
            handler.send_header('Location', '/login?next=' + quote(handler.path, safe=''))
            handler.send_header('Cache-Control', 'no-store')
            handler.send_header('Content-Length', '0')
            handler.end_headers()
        else:
            answer(handler, dict(error='Sign in to continue.'), 401)
        return True
    if method != 'GET':
        permission = mutation_permission(path)
        if permission not in ('chat', 'workspace', 'key_access') and not can(handler.operator, permission):
            answer(handler, dict(error='This action requires ' + PERMISSIONS.get(permission, 'Admin') + '.'), 403)
            return True
    return False
