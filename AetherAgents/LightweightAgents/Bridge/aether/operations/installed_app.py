"""Installed Mission Control entry point. Import application modules after root selection."""
from __future__ import annotations

import json
import http.client
import os
import socket
from pathlib import Path
import sys
import tempfile
import threading
import time
import webbrowser
from urllib.parse import urlsplit

from .distribution import direct, layout, verify, write_json
from .distribution_state import (BusyInstallation, atomic_json, exclusive, instance,
                                 maintenance_paths, running)


def unconfigured_profiles():
    """The existing registry requires one entry; this is never a default or admission."""
    return dict(schemaVersion=3, default_profile_id=None, profiles={
        'unconfigured': dict(description='Setup placeholder. Create and admit your deployment in Settings.',
            adapter_type='openai-compatible', endpoint='http://127.0.0.1:9/v1',
            requestedApiIdentifier='not-configured', identifierKind='logical-alias',
            resource_pool='unconfigured', credential_ref=None,
            contextWindow=32768, maxOutputTokens=2048, timeoutSecs=60, maxToolCalls=8,
            dialect='openai', capabilitySource='Unconfigured placeholder; no provider capability claimed',
            contextBudget=dict(workingContextTokens=32768, toolResultReserveTokens=1000,
                safetyMarginTokens=512, minimumInputTokens=1024, maxToolResultChars=3000))})


# The installed Atelier answers at the same address every launch, so bookmarks and open tabs keep working
# (GUI review S3, 10-24). When something already answers there, any free port is used and printed as before.
PREFERRED_PORT = 8484


def preferred_port():
    try:
        with socket.create_connection(('127.0.0.1', PREFERRED_PORT), timeout=0.5):
            return 0
    except OSError:
        return PREFERRED_PORT


def no_legacy_client():
    # Health/status used to probe a legacy model as a side effect. Governed hosts
    # use their own admitted factories; setup/health has no model capability.
    raise ValueError('Choose and admit a deployment through Settings before inference')


def healthy_instance(record):
    """Probe only the saved literal-loopback endpoint; never proxies/redirects."""
    if record.get('status') != 'READY' or not isinstance(record.get('instance_id'), str) or not record['instance_id']:
        return False
    deadline = time.monotonic() + 1
    connection = response = timer = None
    try:
        url = urlsplit(record.get('url', ''))
        if (url.scheme != 'http' or url.hostname != '127.0.0.1' or not url.port
                or url.netloc != f'127.0.0.1:{url.port}' or url.path not in ('', '/')
                or url.query or url.fragment):
            return False
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        connection = http.client.HTTPConnection('127.0.0.1', url.port, timeout=remaining)
        connection.connect()
        connected_socket = connection.sock
        # A socket timeout alone restarts for each arriving byte. Shutdown the
        # captured socket at the one overall deadline, including while an
        # HTTPResponse owns its buffered file after Connection: close.
        def expire():
            try:
                connected_socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        timer = threading.Timer(remaining, expire)
        timer.daemon = True
        timer.start()
        connection.request('GET', '/api/health')
        response = connection.getresponse()
        if response.status != 200:
            return False
        raw = response.read(4097)
        if len(raw) > 4096:
            return False
        value = json.loads(raw)
        return (time.monotonic() < deadline and isinstance(value, dict)
                and value.get('instance_id') == record['instance_id'])
    except (OSError, ValueError, TypeError, http.client.HTTPException):
        return False
    finally:
        if timer is not None:
            timer.cancel()
            timer.join()
        if response is not None:
            response.close()
        if connection is not None:
            connection.close()


def serve_installed(app, *, governed, port=0, browser=True, stop_on_stdin=False):
    app = direct(app)
    config = json.loads(direct(app / 'installation.json').read_bytes())
    checked = layout(config['application'], config['data'], config['credentials'],
                     explicit_campaign_folders=config['campaign_folders'] == 'explicit')
    if checked != config or Path(config['application']) != app:
        raise ValueError('Installation binding changed; use its installed launcher')
    data = direct(config['data'])
    if json.loads(direct(data / 'layout.json').read_bytes()) != config or not governed:
        raise ValueError('A bound data folder and --governed are required')
    try:
        with exclusive(maintenance_paths(app)['lock']):
            held = direct(data/'.aether_control/restore-reconciliation.json')
            if held.exists():
                if _edition(app) != 'atelier':
                    raise ValueError('Restored data is held for reconciliation; original and backup obligations remain unadjudicated')
                # The Atelier has no dispatched jobs to adjudicate: say what happened and start (First Principle 1).
                print(restore_notice(held), file=sys.stderr, flush=True)
            if maintenance_paths(app)['journal'].exists():
                raise ValueError('Installation or restore maintenance is incomplete; finish its recorded transaction before launch')
            return _serve_installed(app, governed=governed, port=port,
                                    browser=browser, stop_on_stdin=stop_on_stdin)
    except BusyInstallation:
        current = running(data)
        if current and current.get('application') == str(app) and healthy_instance(current):
            value = dict(status='ALREADY_RUNNING', url=current.get('url'),
                         instance_id=current.get('instance_id'), data=str(data), advisory=True)
            print(json.dumps(value) if stop_on_stdin else
                  'Aether is already running at ' + str(value['url']), flush=True)
            return 0
        if current and current.get('status') == 'READY':
            raise BusyInstallation('Saved instance is stale or its health could not be confirmed; '
                                   'Aether is starting or maintenance is in progress. Try again shortly.')
        raise BusyInstallation('Aether is starting or maintenance is in progress; try again shortly')


def _edition(app):
    try:
        return json.loads((Path(app) / 'bundle.json').read_bytes()).get('edition', 'full')
    except (OSError, ValueError):
        return 'full'


def restore_notice(path) -> str:
    """What the operator should know after originals were replaced from a backup; a signpost, never a stop."""
    try:
        hold = json.loads(Path(path).read_bytes())
    except (OSError, ValueError):
        hold = {}
    return ('Note: this data was replaced from a backup' + (f" by {hold['operator']}" if hold.get('operator') else '')
            + (f" ({hold['reason']})" if hold.get('reason') else '') + '. Work done after that backup is not in it; '
            + (f"the replaced originals are kept in {hold['original_roots']}. " if hold.get('original_roots') else '')
            + 'Check projects and agents before joining the network.')


def _serve_installed(app, *, governed, port=0, browser=True, stop_on_stdin=False):
    app = direct(app)
    manifest = verify(app)
    config = json.loads((app / 'installation.json').read_bytes())
    if config.get('campaign_folders') not in ('explicit', 'managed'):
        raise ValueError('Unsupported installation data layout')
    checked = layout(config['application'], config['data'], config['credentials'],
                     explicit_campaign_folders=config['campaign_folders'] == 'explicit')
    if checked != config or Path(config['application']) != app:
        raise ValueError('Installation binding changed; use its installed launcher')
    data = direct(config['data'])
    if json.loads((data / 'layout.json').read_bytes()) != config:
        raise ValueError('User data does not belong to this installation layout')
    if not governed:
        raise ValueError('This installed preview requires --governed')
    # distribution.layout imports project_setup for its public constants. Set the
    # root contract at the entry point BEFORE that first application import.
    if os.environ.get('AETHER_INSTALL_DATA_ROOT') != str(data):
        raise ValueError('Run the installed aether.py entry point in a fresh process')
    bridge = data / 'b'
    scratch = data / 'scratch'
    for folder in (bridge, scratch, data / 'minds', data / '.aether_control'):
        folder.mkdir(parents=True, exist_ok=True)
    os.environ['TEMP'] = os.environ['TMP'] = str(scratch)
    tempfile.tempdir = str(scratch)
    os.environ['AETHER_MINDS_DIR'] = str(data / 'minds')
    from aether.models.runtime_profiles import validate_registry
    with instance(data) as (ownership, metadata):
        profiles = bridge / 'runtime_profiles.json'
        if not profiles.exists():
            initial = unconfigured_profiles()
            validate_registry(initial, budget_aware=True, deployment_aware=True)
            write_json(profiles, initial)
        setup = dict(managed=data / 'p', installed_roots=dict(
            application=str(app), data=str(data), credentials=config['credentials'],
            maintenance=[str(p) for p in maintenance_paths(app).values()]))
        from aether.roadmaps.project_folders import SEED, seed_first_run
        # The shipped example roadmap and its mission, once; never over the operator's projects or missions.
        if seed_first_run(data / 'Projects'):
            from aether.atelier.campaigns import seed_missions
            seed_missions(data / 'atelier', SEED / 'missions.json')
        # First run only: Meridian and the product staff (Praxis's setup; it adds only what is missing). An existing
        # roster is the operator's; Staff → AI offers the newer standard staff instead.
        if not (data / 'staff/roster.json').exists() and not (data / 'minds/meridian').exists():
            from aether.overseer.meridian_setup import prepare
            try:
                prepare(data)
            except (OSError, ValueError, RuntimeError) as exc:
                print(f'Meridian and the standard staff were not set up: {exc}', flush=True)
        first_admin = False
        if manifest.get('edition') == 'atelier':
            # First launch with no account at all: the default Admin, the same on every install (Michel, 2026-11-07).
            # A carried-over profile already has its accounts, so nothing is added there.
            from aether.atelier.operator_access import OperatorAccess
            first_admin = bool(OperatorAccess(data / '.aether_control' / 'operators').seed_default_admin())
            from aether.mission_control.atelier_server import serve
            def atelier_on(chosen):
                return serve(bridge, port=chosen, registry_path=data / 'projects.json',
                    runtime_profiles_path=profiles, minds_folder=data / 'minds',
                    agent_roots=[('Existing agents', data / 'homes'), ('Demo agents', data / 'demo-homes')],
                    project_folders_root=data / 'Projects', atelier_root_path=data / 'atelier',
                    project_setup_options=setup)
            chosen = port or preferred_port()
            try:
                server = atelier_on(chosen)
            except OSError:
                if port:
                    raise
                server = atelier_on(0)     # 8484 was taken between the check and the bind: any free port
        else:
            from aether.mission_control.server import serve
            server = serve(bridge, [], 'not-configured', 32768, endpoint='http://127.0.0.1:9/v1',
                port=port, governed=True, client_factory=no_legacy_client,
                registry_path=data / 'projects.json', runtime_profiles_path=profiles,
                project_setup_options=setup, project_folders_root=data / 'Projects',
                project_engagement_options=dict(catalog=app / 'code/heavyweight_catalog.json', source_root=data))
        ready = dict(status='READY', url=f'http://127.0.0.1:{server.server_port}',
            instance_id=server.state['instance_id'], governed=True, application=str(app), data=str(data),
            executable=sys.executable, campaign_folders=config['campaign_folders'],
            stale_instance_recovered=ownership['stale_instance_recovered'])
        ready['edition'] = manifest.get('edition', 'full')
        if first_admin:
            from aether.atelier.operator_access import DEFAULT_ADMIN
            ready['default_admin'] = DEFAULT_ADMIN['username']
        try:
            atomic_json(metadata, dict(ownership, **ready))
            if stop_on_stdin:
                print(json.dumps(ready), flush=True)
            else:
                print(f"Aether is running at {ready['url']}\nPress Ctrl+C here to stop.", flush=True)
                if first_admin:
                    print(f"First sign-in: username {DEFAULT_ADMIN['username']}, password {DEFAULT_ADMIN['password']}. "
                          'A welcome screen then asks for your own password and your API key.', flush=True)
            if browser:
                webbrowser.open(ready['url'])
            if stop_on_stdin:
                def wait_for_close():
                    sys.stdin.readline()
                    server.shutdown()
                threading.Thread(target=wait_for_close, daemon=True).start()
            server.serve_forever(poll_interval=0.1)
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
        return 0
