"""Own authenticated source/report HTTP actions; never accept caller-supplied operator identities."""
from urllib.parse import urlparse, parse_qs
from aether.integrity.http_body import json_body
from aether.atelier.operator_access import can
from .results import export

API = '/api/data-sources'


def handle(handler, method, state):
    parsed = urlparse(handler.path)
    if parsed.path != API and not parsed.path.startswith(API + '/'):
        return False
    service = state['data_sources']
    aid = handler.operator['id']
    route = parsed.path[len(API):]
    query = parse_qs(parsed.query)
    try:
        if method == 'GET':
            if not route:
                manager = query.get('manage') == ['1']
                model = query.get('model') == ['1']
                result = service.store.listing(aid, manager, model)
                if manager:
                    doc = service.store.access.read()
                    result['roles'] = [dict(id='admin', name='Admin'), dict(id='user', name='User')] + doc['roles']
                else:
                    result['reports'] = service.store.private(aid, 'report') if not model and can(service.store.access.actor(aid), 'use_data_sources') else []
            elif route == '/runs':
                result = service.store.private(aid, 'run', query.get('id', [None])[0])
                if not query.get('id'):
                    result = [{k: r[k] for k in ('id', 'source_name', 'tool', 'at')} for r in result[:50]]
            elif route == '/jobs':
                result = service.job(aid, query.get('id', [''])[0])
            elif route == '/export':
                run = service.store.private(aid, 'run', query.get('id', [''])[0])
                raw, mime, suffix = export(run, query.get('format', ['csv'])[0])
                handler.send_response(200)
                handler.send_header('Content-Type', mime)
                handler.send_header('Content-Length', str(len(raw)))
                handler.send_header('Content-Disposition', 'attachment; filename="report-' + run['id'] + '.' + suffix + '"')
                handler.send_header('Cache-Control', 'no-store')
                handler.send_header('X-Content-Type-Options', 'nosniff')
                handler.end_headers()
                handler.wfile.write(raw)
                return True
            else:
                handler.settings_send(dict(error='Unknown data-source route.'), 404)
                return True
        elif method == 'POST':
            body = json_body(handler, 128000, 'Send data-source details as JSON.')
            if not isinstance(body, dict):
                raise ValueError('Send a JSON object.')
            if route == '/settings':
                result = service.store.update(aid, body)
            elif route == '/discover':
                result = service.discover(aid, body.get('source'))
            elif route == '/run':
                result = service.start(aid, body)
            elif route == '/stop':
                result = service.job(aid, body.get('id'), True)
            elif route == '/save-report':
                result = service.store.save_report(aid, body.get('run'), body.get('title'), body.get('description', ''), body.get('layout'))
            elif route == '/run-report':
                report = service.store.private(aid, 'report', body.get('id'))
                result = service.start(aid, dict(source=report['source_id'], tool=report['tool'],
                    arguments=body.get('arguments', report['arguments']), expected=report['tool_fingerprint'], layout=report.get('layout')))
            elif route == '/remove-report':
                service.store.remove_private(aid, 'report', body.get('id'))
                result = dict(removed=True)
            else:
                handler.settings_send(dict(error='Unknown data-source action.'), 404)
                return True
        else:
            handler.settings_send(dict(error='Method unavailable.'), 405)
            return True
        handler.settings_send(result)
    except PermissionError as exc:
        handler.settings_send(dict(error=str(exc)), 403)
    except (ValueError, TypeError, KeyError) as exc:
        handler.settings_send(dict(error=str(exc)), 409)
    return True
