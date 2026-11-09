"""Own the native project HTTP adapter behind the existing local/operator guards.

Never interpret model prose, bypass native verification, or accept filesystem roots.
"""
from urllib.parse import parse_qs, urlparse
from aether.integrity.http_body import json_body
from aether.keys.redaction import redact
from aether.atelier.operator_access import can


def handle(handler, method, state):
    url = urlparse(handler.path)
    if not url.path.startswith('/api/tasker/'):
        return False
    if not can(getattr(handler, 'operator', None), 'manage_missions'):
        handler.send_json(dict(error='Your operator role cannot manage projects.'), 403)
        return True
    service = state['tasker_projects']
    try:
        query = {k: v[0] for k, v in parse_qs(url.query).items()}
        if method == 'GET':
            if url.path == '/api/tasker/options':
                value = service.options()
            elif url.path == '/api/tasker/plan':
                value = service.plan()
            elif url.path == '/api/tasker/team':
                value = service.team()
            elif url.path == '/api/tasker/school':
                value = service.school()
            elif url.path == '/api/tasker/school-frame':
                value = service.school_frame(query.get('name'))
            elif url.path == '/api/tasker/projects':
                value = dict(projects=service.listing())
            elif url.path == '/api/tasker/project':
                value = service.read(query.get('project'))
            elif url.path == '/api/tasker/output':
                raw = service.output(query.get('project'), query.get('task'), query.get('name'))
                handler.send_response(200)
                handler.send_header('Content-Type', 'application/octet-stream')
                handler.send_header('Content-Disposition', 'attachment')
                handler.send_header('Content-Length', str(len(raw)))
                handler.send_header('X-Content-Type-Options', 'nosniff')
                handler.end_headers()
                handler.wfile.write(raw)
                return True
            else:
                raise ValueError('Unknown native project read')
        else:
            body = json_body(handler, 256 * 1024, 'Send a native project action as JSON')
            if not isinstance(body, dict):
                raise ValueError('Send a project object')
            action = url.path.rsplit('/', 1)[-1]
            if action == 'create':
                value = service.create(body)
            elif action == 'lead':
                value = service.create_lead(body)
            elif action == 'staff':
                value = service.add_standard_staff()
            elif action == 'accept-anyway':
                # Michel, 2026-11-07: an Admin may proceed over a failed check, with a reason, recorded as that.
                if (handler.operator or {}).get('role') != 'admin':
                    raise PermissionError('Only an Admin can accept a sprint over a failed check.')
                if set(body) - {'project', 'reason'}:
                    raise ValueError('Send a project ID and your reason')
                value = service.accept_anyway(body.get('project'), body.get('reason'), handler.operator)
            elif action in ('specialist-preview', 'specialist'):
                author = handler.operator.get('name') or handler.operator.get('username') or 'the operator'
                value = (service.specialist_preview if action == 'specialist-preview' else service.create_specialist)(body, author)
            else:
                if set(body) - {'project', 'reviews', 'caps'}:
                    raise ValueError('Send a project ID, not a filesystem path')
                project = body.get('project')
                if action == 'start': value = service.start(project, handler.operator['id'])
                elif action == 'stop': value = service.stop(project)
                elif action == 'resume': value = service.resume(project)
                elif action == 'continue': value = service.continue_interrupted(project, handler.operator['id'])
                elif action == 'budget': value = service.raise_budget(project, body.get('caps'))
                elif action == 'accept':
                    reviews = body.get('reviews')
                    if reviews is not None:
                        if not isinstance(reviews, dict) or any(not isinstance(v, dict) for v in reviews.values()):
                            raise ValueError('Reviews must be a map of bound decisions')
                        reviews = {k: dict(v, reviewer=handler.operator['id']) for k, v in reviews.items()}
                    value = service.accept(project, reviews)
                else: raise ValueError('Unknown native project action')
        handler.send_json(value)
    except PermissionError as exc:
        handler.send_json(dict(error=str(exc)), 403)
    except (ValueError, OSError, KeyError, TypeError, RuntimeError) as exc:
        handler.send_json(dict(error=redact(str(exc))), 409)
    return True
