"""Serve the Admin backup-coverage preview, path checks and reconnection report for this installed copy.

Every route answers only a signed-in Admin, enforced here on the server for GET and POST alike.
Never: run a backup, restore or switch from a live server, read the credential root, accept an application
path from the request, or return private locations to an account that is not an Admin.
"""
from pathlib import Path

from aether.atelier.operator_access import can
from aether.atelier.operator_access_http import answer
from aether.integrity.http_body import json_body
from aether.operations import distribution_preservation as preservation, maintenance_coverage
from aether.paths import BRIDGE_ROOT, LIGHTWEIGHT_ROOT

API = '/api/maintenance/'
NOT_INSTALLED = dict(status='NOT_INSTALLED', message='This Mission Control runs from a source folder. Backup and '
                     'restore apply to an installed copy: use its Maintenance.cmd.')


def installed_app():
    """The installed application folder this server runs from, or None for a source launch."""
    app = BRIDGE_ROOT.parent
    if not (app / 'installation.json').is_file():
        return None
    _, config = preservation.installation(app)
    return app if Path(config['data']).resolve() == Path(LIGHTWEIGHT_ROOT).resolve() else None


def _mounts(value):
    if value is None:
        return {}
    if not isinstance(value, dict) or len(value) > 40:
        raise ValueError('Send additional folders as a mapping of up to 40 entries.')
    return value


def handle(handler, method, state):
    path = handler.path.split('?', 1)[0]
    if not path.startswith(API):
        return False
    if not can(getattr(handler, 'operator', None), 'admin'):
        answer(handler, dict(error='Only an Admin can see backup coverage and recovery details.'), 403)
        return True
    route = path[len(API):]
    try:
        app = state.get('maintenance_app') or installed_app()
        if app is None:
            answer(handler, NOT_INSTALLED)
        elif method == 'GET' and route == 'coverage':
            answer(handler, maintenance_coverage.coverage(app))
        elif method == 'POST' and route in ('check', 'recovery-report'):
            body = json_body(handler, 16384, 'Send the folder choices as JSON.')
            if not isinstance(body, dict):
                raise ValueError('Send the folder choices as an object.')
            if route == 'check':
                answer(handler, maintenance_coverage.check_backup(
                    app, body.get('destination'), body.get('plan'), _mounts(body.get('mounts')),
                    body.get('dependencies') or None, body.get('staging') or None))
            else:
                target = maintenance_coverage._path(body.get('target'), 'staging folder')
                answer(handler, maintenance_coverage.recovery_report(app, target))
        else:
            answer(handler, dict(error='Maintenance route not found.'), 404)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        answer(handler, dict(error=str(exc)), 409)
    return True
