"""Journaled payload replacement; bound user data is never disposable."""
from contextlib import nullcontext
import json
from pathlib import Path
from .distribution_state import (atomic_json, exclusive, maintenance_paths,
                                 check_owned_tree, remove_owned_tree)

PHASES = {'preparing', 'switching', 'checking', 'retiring', 'complete'}


def _read(path):
    from .distribution import direct
    return json.loads(direct(path).read_bytes())


def _installation(app):
    from .distribution import SCHEMA
    try:
        value = _read(app / 'installation.json')
    except (FileNotFoundError, NotADirectoryError, json.JSONDecodeError) as error:
        raise ValueError(f'Not an Aether installation: {app}. Choose the installed application folder; existing files were preserved.') from error
    if not isinstance(value, dict) or value.get('schema') != SCHEMA:
        raise ValueError(f'Not an Aether installation: {app}. No supported installation binding; existing files were preserved.')
    return value


def _bound(app, config):
    if _installation(app) != config:
        raise ValueError('Existing installation has a different data binding; refusing adoption')


def _same_install(app, config):
    from .distribution import verify
    verify(app)
    _bound(app, config)


def payloads(app):
    paths = maintenance_paths(app)
    return [app, *(paths[k] for k in ('stage', 'previous', 'removing', 'retiring'))]


def _retained_separate(app, config):
    for disposable in payloads(app):
        for key in ('data', 'credentials'):
            retained = Path(config[key])
            if retained.is_relative_to(disposable) or disposable.is_relative_to(retained):
                raise ValueError('Retained data overlaps a maintenance payload')


def project_guard(app, data):
    """Read bound registry before moving/removing payloads; name blockers."""
    from .distribution import direct
    registry = direct(data / 'projects.json')
    if not registry.exists():
        # Startup folders/instance metadata precede the first registry save.
        # Its durable writer lock is registry-specific evidence, unlike those
        # startup artifacts. Never infer registry loss from launch alone.
        if direct(data / 'projects.json.lock').exists():
            raise ValueError(f'Project registry is missing with prior registry-save evidence: {registry}. '
                             'Restore projects.json before installing or uninstalling; application files were preserved.')
        return dict(status='missing' if data.exists() else 'data-folder-missing', projects=[])
    document = _read(registry)
    rows = document.get('projects') if isinstance(document, dict) else None
    if not isinstance(rows, list):
        raise ValueError(f'Invalid project registry; preserve and repair before maintenance: {registry}')
    checked = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get('root'), str):
            raise ValueError(f'Invalid project root in registry: {registry}')
        root = direct(row['root'])
        name = row.get('name') or row.get('id') or str(root)
        if any(root.is_relative_to(p) or p.is_relative_to(root) for p in payloads(app)):
            raise ValueError(f'Project "{name}" at {root} overlaps an application maintenance payload. '
                             'Move the project and update its registry binding before maintenance.')
        checked.append(dict(name=name, root=str(root)))
    return dict(status='checked', projects=checked)


def _journal(paths, config):
    if not paths['journal'].exists():
        return False
    value = _read(paths['journal'])
    if value == dict(schema='aether-install-transaction-1', layout=config):
        return True  # Legacy journal owns previous even after partial deletion.
    if (not isinstance(value, dict) or set(value) != {'schema', 'layout', 'phase', 'retiring'}
            or value['schema'] != 'aether-install-transaction-2' or value['layout'] != config
            or value['phase'] not in PHASES or value['retiring'] != str(paths['retiring'])):
        raise ValueError('Interrupted installation has different roots or invalid journal; use its original paths')
    return True


def install(bundle, destination, data, credentials, *, explicit_campaign_folders=False):
    from .distribution import direct, verify, layout, copy_file, write_json
    bundle = direct(bundle)
    manifest = verify(bundle)
    config = layout(destination, data, credentials, explicit_campaign_folders=explicit_campaign_folders)
    app, data, credentials = (Path(config[k]) for k in ('application', 'data', 'credentials'))
    paths = maintenance_paths(app)
    for path in (app, *paths.values()):
        direct(path)
        if bundle.is_relative_to(path) or path.is_relative_to(bundle):
            raise ValueError('Installer source must be separate from maintenance destinations')
    _retained_separate(app, config)

    def preflight():
        if paths['removing'].exists():
            raise ValueError('Uninstall is incomplete; rerun uninstall before installing')
        recovering = _journal(paths, config)
        if not recovering and any(paths[k].exists() for k in ('stage', 'previous', 'retiring')):
            raise ValueError('Unowned staging/previous/retiring folder; refusing to remove it')
        if app.exists():
            _bound(app, config)  # Corrupt payload is repairable; a changed binding is not.
        if data.exists():
            if (data / 'layout.json').exists():
                if _read(data / 'layout.json') != config:
                    raise ValueError('User data belongs to a different layout')
            elif not recovering or any(p.name != 'layout.json.pending' for p in data.iterdir()):
                raise ValueError('Existing unbound user data is preserved; refusing adoption')
        elif app.exists() or any(paths[k].exists() for k in ('stage', 'previous', 'retiring')):
            # A staged/switched payload can only follow creation of the bound
            # home. A journal alone can precede it in a fresh install, so that
            # case must remain recoverable without inventing an old home.
            raise ValueError('Installed user data is missing; restore the original data folder before retrying. '
                             'Refusing to replace it with an empty home.')
        if credentials.exists() and not data.exists():
            raise ValueError('Existing credentials cannot be adopted into a fresh installation')
        project_guard(app, data)
        for path in payloads(app):
            check_owned_tree(path, app.parent)
        return recovering

    def phase(name):
        atomic_json(paths['journal'], dict(schema='aether-install-transaction-2', layout=config,
                                          phase=name, retiring=str(paths['retiring'])))

    def retire_previous():
        # Persist ownership before rename; partial retirement needs no hashes.
        phase('retiring')
        remove_owned_tree(paths['retiring'], app.parent)
        if paths['previous'].exists():
            paths['previous'].rename(paths['retiring'])
            remove_owned_tree(paths['retiring'], app.parent)

    def restore_previous():
        # Under both locks and after all read-only preflight guards. A complete
        # old payload must survive a second failure during staging. Restore it
        # also after a failed switch check left a damaged live replacement.
        if not paths['previous'].exists():
            return
        try:
            _same_install(paths['previous'], config)
        except (OSError, ValueError):
            return  # Legacy partial previous remains journal-owned, not bootable.
        if app.exists():
            try:
                _same_install(app, config)
                return
            except (OSError, ValueError):
                remove_owned_tree(paths['retiring'], app.parent)
                app.rename(paths['retiring'])
        paths['previous'].rename(app)

    preflight()  # Refusals/mistypes create no stray locks or parent folders.
    with exclusive(paths['lock']):
        recovering = preflight()
        # Restore before even a journal rewrite can fail with ENOSPC. The
        # existing matching journal already owns this rename. Never restore
        # against an absent/unbound home, and never outside both OS locks.
        if paths['previous'].exists() and (data / 'layout.json').exists():
            with exclusive(data / '.aether_control/installed-server.lock'):
                restore_previous()
        phase('preparing')
        data.mkdir(parents=True, exist_ok=True)
        if not (data / 'layout.json').exists():
            atomic_json(data / 'layout.json', config)
        with exclusive(data / '.aether_control/installed-server.lock'):
            remove_owned_tree(paths['stage'], app.parent)
            phase('preparing')
            paths['stage'].mkdir()
            for relative in manifest['files']:
                copy_file(bundle / relative, paths['stage'] / relative)
            copy_file(bundle / 'bundle.json', paths['stage'] / 'bundle.json')
            verify(paths['stage'])
            write_json(paths['stage'] / 'installation.json', config)
            # Never retire an intact fallback until replacement staging passes.
            retire_previous()
            project_guard(app, data)
            check_owned_tree(app, app.parent)
            phase('switching')
            if app.exists():
                app.rename(paths['previous'])
            paths['stage'].rename(app)
            phase('checking')
            _same_install(app, config)
            retire_previous()
            phase('complete')
            paths['journal'].unlink()
    return dict(installed=str(app), launcher=str(app / 'Aether.cmd'), data=str(data),
                credentials=str(credentials), provider_calls=0, recovered=recovering)


def uninstall(destination):
    """Run from extracted bundle. Missing data is reported, never recreated."""
    from .distribution import direct, layout
    import sys
    app = direct(destination)
    if Path(sys.executable).resolve().is_relative_to(app):
        raise ValueError('Close Aether and run Uninstall.cmd from the extracted bundle; user data will be kept')
    paths = maintenance_paths(app)

    def preflight():
        for path in paths.values():
            direct(path)
        if paths['journal'].exists():
            raise ValueError('Installation is incomplete; rerun Install.cmd before uninstalling')
        if app.exists():
            if paths['removing'].exists():
                raise ValueError('Both live and removing payloads exist; refusing ambiguous removal')
            config = _installation(app)
        elif paths['uninstall'].exists():
            receipt = _read(paths['uninstall'])
            if receipt.get('schema') != 'aether-uninstall-receipt-1' or receipt.get('status') not in ('REMOVING', 'REMOVED_DATA_PRESERVED'):
                raise ValueError('Invalid uninstall receipt')
            config = receipt['layout']
        else:
            raise ValueError('No bound installation to remove')
        checked = layout(config['application'], config['data'], config['credentials'],
                         explicit_campaign_folders=config['campaign_folders'] == 'explicit')
        if checked != config or Path(config['application']) != app:
            raise ValueError('Invalid uninstall binding')
        data = direct(config['data'])
        if data.exists() and _read(data / 'layout.json') != config:
            raise ValueError('Uninstall data binding changed; refusing')
        _retained_separate(app, config)
        checked_projects = project_guard(app, data)
        for path in (app, paths['removing']):
            check_owned_tree(path, app.parent)
        return config, data, checked_projects

    preflight()
    with exclusive(paths['lock']):
        config, data, checked_projects = preflight()
        with exclusive(data / '.aether_control/installed-server.lock') if data.exists() else nullcontext():
            receipt = dict(schema='aether-uninstall-receipt-1', status='REMOVING', layout=config,
                removed=[str(app)], kept=[dict(path=str(data), present=data.exists(),
                    contents='All user data (registry, campaigns, memories, settings, control records, logs)' if data.exists()
                             else 'Data folder already missing; not recreated'),
                    dict(path=config['credentials'], present=Path(config['credentials']).exists(),
                         contents='Entire reserved credential folder (if present); environment credentials unchanged')],
                project_registry=checked_projects, receipt=str(paths['uninstall']))
            atomic_json(paths['uninstall'], receipt)
            if app.exists():
                app.rename(paths['removing'])
            remove_owned_tree(paths['removing'], app.parent)
            receipt['status'] = 'REMOVED_DATA_PRESERVED'
            atomic_json(paths['uninstall'], receipt)
            return receipt
