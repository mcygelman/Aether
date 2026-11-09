"""Admin backup coverage, destination checks, offline commands and staged-restore reconnection reports.

Reads metadata only, through the existing maintenance discovery and preservation verification. Coverage is an
inventory, never a backup; commands are text for the offline Maintenance.cmd boundary, never executed here.
Never: open the credential root or decrypt anything, copy or write data, search a disk beyond registered or
explicitly mounted locations, stop work, rebind paths, activate a restore, resume a job or contact a provider.
"""
from contextlib import closing
import hashlib
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import shutil
import sqlite3

from . import distribution_maintenance as maintenance
from . import distribution_preservation as preservation

REFUSALS = (ValueError, OSError, KeyError, TypeError)
CONTROL = '.aether_control'
RECEIPT_FAMILIES = ('specialist-installs', 'custom-specialist-installs')
MAX_REFERENCES = 400
MAX_JSON = 1024 * 1024


def _inside(path, roots):
    return next((key for key, root in roots.items() if Path(path) == root or Path(path).is_relative_to(root)), None)


def _location(path, roots, *, optional=True):
    """Where one recorded location stands against the selected roots: included, outside or not present."""
    path = Path(path)
    root = _inside(path, roots)
    if root is not None:
        return dict(path=str(path), status='included' if path.exists() else 'not-yet-created', root=root)
    if not path.exists():
        return dict(path=str(path), status='not-yet-created' if optional else 'missing',
                    action='The recorded folder was not found. Reconnect its drive, or remove the record that names it.')
    return dict(path=str(path), status='outside', action='Add it to the plan with --mount (included in the commands below).')


def _mount_id(path):
    return 'extra-'+hashlib.sha256(str(path).encode('utf-8')).hexdigest()[:12]


def _json(path):
    try:
        return maintenance.metadata(path)[0]
    except REFUSALS:
        return None


def coverage(app, extra_mounts=None):
    """Categorized coverage of current Aether data for an Admin. An inventory is not a completed backup."""
    app, config = preservation.installation(app)
    data, credentials = preservation.safe(config['data']), preservation.safe(config['credentials'])
    control = data/CONTROL
    extras = dict(extra_mounts or {})
    refusal = None
    try:
        _, _, roots, mounts, owners, _, _, files, _, excluded = maintenance.discover(app, extras)
    except REFUSALS as exc:
        refusal, roots, mounts, owners, files, excluded = str(exc), {'data': data}, {}, {}, {}, []
    categories = []

    def add(key, label, purpose, locations, note=None):
        categories.append(dict(id=key, label=label, purpose=purpose, locations=locations, note=note))

    def stored(*names):
        return [_location(control/name, roots) for name in names]

    add('accounts', 'Accounts, roles and key grants',
        'Sign-in accounts, Admin/User access, named roles and which API keys each account may use. Passwords are kept only '
        'as one-way hashes; API key values are not part of this store.', stored('operators'))
    add('chats', 'Private Chat', 'Each account’s private conversations and notes.', stored('model_chats'))
    add('scratchpads', 'Chat scratchpads', 'Each account’s default Chat folder.', stored('chat_scratchpads'))
    add('templates', 'Custom templates and creation receipts',
        'Every saved revision of each custom template, and the receipts that make a repeated Create request safe.',
        stored('custom-templates.json', *RECEIPT_FAMILIES))
    registry = control/'agents.json'
    homes, technical, unsupported = [], [], []
    try:
        registered = maintenance.registered_homes(data)
    except REFUSALS as exc:
        registered, refusal = [], refusal or str(exc)
    for home in registered:
        row = _location(home, roots, optional=False)
        if home.is_dir() and not maintenance.supported_home(home):
            row.update(status='excluded', action='This older agent profile has no GUID identity, so this backup format '
                       'cannot keep its ownership. Keep its own copy, or open it once in Agents to update its records.')
            unsupported.append(str(home))
        homes.append(row)
        pointer = _json(home/'memory/.cartridge.json') if home.is_dir() else None
        if pointer and pointer.get('mode') == 'technical-v1':
            technical.append(_location(home/'memory'/str(pointer.get('path') or 'technical.sqlite'), roots, optional=False))
    for name, path in sorted(files.items()):
        # A cartridge moved elsewhere keeps an absolute pointer; its folder must be selected too.
        pointer = _json(path) if name.endswith('/memory/.cartridge.json') else None
        if pointer and isinstance(pointer.get('path'), str) and PureWindowsPath(pointer['path']).is_absolute():
            row = _location(Path(pointer['path']).parent, roots, optional=False)
            homes.append(dict(row, note='Memory cartridge kept outside its home ('+name.split('/memory/')[0]+')'))
    add('agents', 'Agent homes', 'Identity, orientation and memory of each agent registered in Agents, including '
        'specialists created here and agents added from other folders.',
        [_location(data/'minds', roots), *homes])
    add('technical', 'Technical cartridges', 'Each specialist’s own technical learning: lessons, methods and paths.',
        technical, note=None if technical else 'No specialist has a technical cartridge yet.')
    projects = []
    rows = (_json(data/'projects.json') or {}).get('projects') or []
    for row in rows:
        if isinstance(row, dict) and isinstance(row.get('root'), str):
            projects.append(_location(row['root'], roots, optional=False))
    add('projects', 'Projects', 'Registered project folders, mission and project records, and project files.',
        [*projects, _location(data/'atelier', roots), _location(data/'Projects', roots), _location(data/'projects.json', roots)])
    stores = sorted({name.split('/.aether-specialists/')[0]+'/.aether-specialists' for name in files if '/.aether-specialists/' in name})
    add('checkpoints', 'Project checkpoints', 'Each specialist’s continuity for a specific project, kept with that project '
        'and separate from its technical cartridge.',
        [dict(path=str(Path(roots[name.split('/')[0]], *name.split('/')[1:])), status='included', root=name.split('/')[0])
         for name in stores],
        note=None if stores else 'No project checkpoint has been saved yet.')
    add('sources', 'Data sources and saved reports', 'MCP service definitions, role access, saved report definitions and '
        'retained results. Service tokens are left out of every backup: after a restore, re-enter each one in Data '
        'Sources.', stored('data_sources'))
    add('workspace', 'Workspace records', 'Creations, Teach Frames, guides, dashboards and installation settings.',
        stored('workspace', 'installation_settings.json', 'tool_registry.json'))
    add('models', 'Model connections', 'Saved providers, servers and models. Each names its API key; the key value is '
        'kept in the API key store.', [_location(data/'b/runtime_profiles.json', roots)])
    network = (_json(control/'workplace/connection.json') or {})
    shared = []
    if network.get('share') and _inside(Path(network['share']), roots) is not None:
        # This computer's own network folder, inside a backed-up root: it is in the backup (rehearsal 4: the page said
        # both "Included" and "Left out" for the same folder).
        shared = [dict(_location(network['share'], roots), note='The network folder is inside a backed-up folder, so it '
                       'is included. Other computers working in it should leave the network before the backup too.')]
    elif network.get('share'):
        shared = [dict(path=str(network['share']), status='excluded', action='A network folder is written by other computers '
                       'and is not paused by closing this one. Back it up on the computer that hosts it.')]
    add('network', 'Aether network', 'This computer’s network settings, and the shared network folder when it is inside a '
        'backed-up folder.', [*stored('workplace'), *shared])
    secret = [row['path'] for row in excluded if row['reason'] == 'credential-content-not-read']
    add('credentials', 'API key store and credentials', 'Never read or copied. Recover API keys from their provider or the '
        'vault you keep, then unlock or re-enter them on API Keys. Until then, key grants stay recorded but cannot be used.',
        [dict(path=str(credentials), status='excluded', action='Keep your API keys and vault password separately.'),
         *(dict(path=name, status='excluded', action='Credential-named file: never read. List where it can be recovered.')
           for name in secret)])
    outside = {}
    for category in categories:
        for row in category['locations']:
            if row['status'] == 'outside':
                outside.setdefault(_mount_id(row['path']), row['path'])
    for row in (r for c in categories for r in c['locations']):
        if row['status'] == 'outside':
            row['mount'] = _mount_id(row['path'])
    sizes = sum(p.stat().st_size for p in files.values())
    longest = max((len(PurePosixPath(n).as_posix()) for n in files), default=0)
    attention = bool(refusal or outside or unsupported or any(r['status'] == 'missing' for c in categories for r in c['locations']))
    return dict(schema='aether-backup-coverage-1', status='REFUSED' if refusal else 'NEEDS_ATTENTION' if attention else 'READY_TO_PLAN',
                refusal=refusal, inventory_is_not_a_backup=True, app=str(app), data=str(data),
                roots={k: str(v) for k, v in sorted(roots.items())}, mounts=dict(mounts), suggested_mounts=outside,
                owners=len(owners), files=len(files), bytes=sizes, longest_relative_path=longest,
                credential_dependencies_required=bool(secret), categories=categories,
                boundary='Backup runs offline: let active turns finish, leave each Aether network, close Mission Control '
                         'and external editors, then run Maintenance.cmd. Locked or changing data is refused, never forced.')


UNSAFE = re.compile(r'["%\x00-\x1f<>|?*]')


def _path(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'Enter the {label}.')
    text = value.strip()
    windows = PureWindowsPath(text)
    if not windows.is_absolute() or '..' in windows.parts:
        raise ValueError(f'Use the full path of the {label}, such as D:\\Aether backups\\2026-10-27.')
    if UNSAFE.search(text) or ':' in text[2:]:
        raise ValueError(f'The {label} contains a character Command Prompt cannot pass safely (" % : < > | ? * or a '
                         'control character). Choose or rename a folder without it.')
    if any(part.endswith((' ', '.')) for part in windows.parts[1:]):
        raise ValueError(f'A folder name in the {label} ends with a space or a dot. Windows cannot use it reliably.')
    return Path(text)


def _quote(path):
    return '"'+str(path)+'"'


def commands(app, destination, plan, mounts, dependencies=None, staging=None):
    """Command Prompt text for the existing offline workflow; every path is quoted and pre-validated."""
    tool = _quote(Path(app)/'Maintenance.cmd')
    prepare = [tool, 'prepare-backup', '--destination', _quote(destination), '--plan', _quote(plan)]
    for key, path in sorted(mounts.items()):
        prepare += ['--mount', _quote(f'{key}={path}')]
    if dependencies:
        prepare += ['--credential-dependencies', _quote(dependencies)]
    steps = [dict(title='Prepare the plan', command=' '.join(prepare),
                  note='Lists exactly what will be saved. Nothing is copied yet. Keep the plan private; note its plan_sha256.'),
             dict(title='Make the backup', command=f'{tool} backup --plan {_quote(plan)} --plan-sha256 PLAN_SHA256',
                  note='Replace PLAN_SHA256 with the value from step 1. Note the manifest_sha256 it prints and keep it '
                       'apart from the backup.'),
             dict(title='Verify the backup', command=f'{tool} verify-backup --archive {_quote(destination)} --manifest-sha256 MANIFEST_SHA256',
                  note='Replace MANIFEST_SHA256 with the value from step 2. Expect VERIFIED_BACKUP_NOT_ACTIVE.')]
    if staging:
        steps += [dict(title='Restore to a new staging folder',
                       command=f'{tool} restore --archive {_quote(destination)} --manifest-sha256 MANIFEST_SHA256 --target {_quote(staging)}',
                       note='Verifies the backup again and copies it into a new folder. Nothing becomes active.'),
                  dict(title='Read the reconnection report', command=f'{tool} recovery-report --target {_quote(staging)}',
                       note='Shows what is restored, what still names original folders and what must be reconnected.')]
    return steps


def check_backup(app, destination, plan, extra_mounts=None, dependencies=None, staging=None):
    """Validate destination/plan/staging paths against the current scope and return the offline commands."""
    app, config = preservation.installation(app)
    problems, warnings = [], []
    try:
        destination, plan = _path(destination, 'backup folder'), _path(plan, 'plan file')
        dependencies = _path(dependencies, 'credential dependency list') if dependencies else None
        staging = _path(staging, 'staging folder') if staging else None
        mounts = {k: str(_path(v, 'additional folder')) for k, v in dict(extra_mounts or {}).items()}
    except ValueError as exc:
        return dict(ok=False, problems=[str(exc)], warnings=[], steps=[])
    try:
        _, _, roots, _, _, _, _, files, _, _ = maintenance.discover(app, mounts)
    except REFUSALS as exc:
        return dict(ok=False, problems=['The current scope cannot be planned: '+str(exc)], warnings=[], steps=[])
    fixed = {'application': app, 'credentials': preservation.safe(config['credentials']), **roots}
    for label, path in (('Backup folder', destination), ('Plan file', plan), ('Staging folder', staging)):
        if path is None:
            continue
        if path.exists():
            problems.append(f'{label} already exists. Choose a new name; existing data is never replaced.')
        clash = next((k for k, root in fixed.items() if path == root or path.is_relative_to(root) or root.is_relative_to(path)), None)
        if clash:
            problems.append(f'{label} overlaps the {clash} location. Choose a folder outside it.')
    pairs = [(destination, plan, 'The plan file must be outside the backup folder.')]
    if staging:
        pairs += [(destination, staging, 'The staging folder must be separate from the backup folder.'),
                  (staging, plan, 'The plan file must be outside the staging folder.')]
    for outer, inner, message in pairs:
        if inner == outer or inner.is_relative_to(outer) or outer.is_relative_to(inner):
            problems.append(message)
    longest = max((len(str(destination/'payload'/n)) for n in files), default=0)
    if longest > preservation.MAX_PATH:
        problems.append(f'The backup folder path is too long: its deepest file would need {longest} characters and Windows '
                        f'allows {preservation.MAX_PATH}. Choose a shorter folder, such as D:\\AB\\1.')
    if staging:
        deepest = max((len(str(staging/n)) for n in files), default=0)
        if deepest > preservation.MAX_PATH:
            problems.append(f'The staging folder path is too long ({deepest} characters for the deepest file).')
    anchor = next((p for p in (destination, *destination.parents) if p.exists()), None)
    if anchor is None or not anchor.is_dir():
        problems.append('The backup folder’s drive or parent folder was not found.')
    else:
        need = sum(p.stat().st_size for p in files.values())
        free = shutil.disk_usage(anchor).free
        if free < need * 1.1 + 64 * 1024 * 1024:
            problems.append(f'Not enough free space: about {need:,} bytes to save and {free:,} bytes free.')
    if dependencies and not dependencies.is_file():
        warnings.append('The credential dependency list does not exist yet. Save it at that path before step 1.')
    return dict(ok=not problems, problems=problems, warnings=warnings, files=len(files),
                steps=commands(app, destination, plan, mounts, dependencies, staging) if not problems else [])


ABSOLUTE = re.compile(r'^(?:[A-Za-z]:[\\/]|\\\\[^\\]+\\)')     # Windows drive or UNC paths; web routes are not folders


def _strings(value, pointer=''):
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _strings(item, f'{pointer}/{key}')
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _strings(item, f'{pointer}/{index}')
    elif isinstance(value, str) and ABSOLUTE.match(value) and len(value) < 1024:
        yield pointer or '/', value


def _readonly(path):
    return closing(sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro', uri=True))


def recovery_report(app, target):
    """What a verified staged restore holds and what reconnecting it would need. Reads; never activates or rebinds."""
    app, config = preservation.installation(app)
    target = preservation.safe(target)
    if not target.is_dir():
        raise ValueError('The staging folder was not found.')
    if not (target/'restore.json').is_file():
        raise ValueError('This folder has no restore.json, so it is not a completed restore (an interrupted restore '
                         'leaves this state). Keep it for inspection and restore again into a new folder.')
    receipt = preservation.read_json(target/'restore.json')
    if receipt.get('schema') != 'aether-staged-restore-1':
        raise ValueError('Choose a folder made by Maintenance.cmd restore; it holds restore.json')
    manifest = preservation.verify_backup(receipt['archive'], expected_manifest_sha256=receipt['manifest_sha256'], app=app)
    if manifest != receipt['manifest']:
        raise ValueError('The staging receipt differs from its backup; keep both for inspection')
    status = receipt['status']
    originals = {k: Path(v) for k, v in manifest['roots'].items()}
    report = dict(schema='aether-reconnection-report-1', target=str(target), status=status, active=False,
                  archive=receipt['archive'], manifest_sha256=receipt['manifest_sha256'],
                  compatibility=manifest['compatibility'], obligations=manifest['obligations'],
                  credential_dependencies=manifest['credential_dependencies'],
                  excluded=manifest['excluded'], same_installation=manifest['installation'] == config)
    if status != 'VERIFIED_STAGING_NOT_ACTIVE':
        report.update(active=status == 'SWITCHED_ORIGINALS_PRESERVED', previous=str(target/'previous'),
                      summary='This staging folder was switched into the original folders. Prior data is kept under '
                              'previous. Read the original installation from Mission Control instead.')
        return report
    preservation._verify_stage(target, receipt)
    staged = {k: target/k for k in originals}
    report['roots'] = [dict(id=k, original=str(v), staged=str(staged[k]), original_present=v.exists())
                       for k, v in sorted(originals.items())]
    references, truncated = [], False
    for name, info in sorted(manifest['files'].items()):
        if not name.endswith('.json') or info['kind'] != 'file' or info['bytes'] > MAX_JSON or preservation.secret_name(name):
            continue
        try:
            value = json.loads((target/name).read_bytes())
        except ValueError:
            continue
        for pointer, text in _strings(value):
            path = Path(text)
            root = _inside(path, originals)
            kind = ('restored' if root else 'application' if _inside(path, {'a': app}) else
                    'credentials' if _inside(path, {'c': Path(config['credentials'])}) else 'external')
            if len(references) >= MAX_REFERENCES:
                truncated = True
                break
            references.append(dict(file=name, field=pointer, path=text, kind=kind, root=root,
                                   staged=str(staged[root]/path.relative_to(originals[root])) if root else None))
    report['references'], report['references_truncated'] = references, truncated
    receipts = []
    for name in sorted(manifest['files']):
        parts = name.split('/')
        if len(parts) == 4 and parts[1] == CONTROL and parts[2] in RECEIPT_FAMILIES and parts[3].endswith('.json'):
            value = json.loads((target/name).read_bytes())
            receipts.append(dict(file=name, complete=bool(value.get('complete')),
                                 agents=[dict(id=a.get('id'), home=a.get('home'), phase=a.get('phase'),
                                              restored=_inside(Path(a.get('home', '')), originals) is not None)
                                         for a in value.get('agents', [])],
                                 retry='Repeating this request returns this receipt and creates no second specialist.'
                                       if value.get('complete') else
                                       'Incomplete: it names the original home folder. Do not repeat the request from a '
                                       'restored copy until that folder has been inspected.'))
    report['receipts'] = receipts
    accounts = target/'data'/CONTROL/'operators/accounts.sqlite3'
    if accounts.is_file():
        from aether.atelier.account_store import AccountStore
        with _readonly(accounts) as db:
            doc = AccountStore.read_db(db)
        report['accounts'] = dict(accounts=len(doc['accounts']), admins=sum(u['role'] == 'admin' for u in doc['accounts']),
                                  roles=[r['name'] for r in doc['roles']],
                                  key_grants=[dict(account=u['username'], keys=u['keys']) for u in doc['accounts'] if u['keys']],
                                  key_status='KEY_VALUES_NOT_IN_BACKUP',
                                  note='Grants are kept. They allow use only when the named keys exist and are unlocked '
                                       'in the API key store of the installation that runs this data.')
    sources = target/'data'/CONTROL/'data_sources/sources.sqlite3'
    withheld = {rid for info in manifest['files'].values() for rid in info.get('withheld_tokens', [])}
    if sources.is_file():
        with _readonly(sources) as db:
            rows = [(kind, json.loads(body)) for kind, body in db.execute('SELECT kind, body FROM records')]
        # A backup made before tokens were withheld may still hold a Windows-protected one.
        report['sources'] = dict(sources=[dict(name=b.get('name'), alias=b.get('alias'), transport=b.get('transport'),
                                               token='WINDOWS_PROTECTED' if b.get('secret') else
                                                     'NOT_IN_BACKUP' if b.get('id') in withheld else 'NONE')
                                          for k, b in rows if k == 'source'],
                                 reports=sum(k == 'report' for k, _ in rows), contacted=False,
                                 note='Backups leave service tokens out. After a switch, re-enter each token marked '
                                      'here in Data Sources before using that source.')
    profiles = _json(target/'data/b/runtime_profiles.json') if (target/'data/b/runtime_profiles.json').is_file() else None
    if isinstance(profiles, dict):
        rows = profiles.get('profiles') if isinstance(profiles.get('profiles'), dict) else {}
        report['models'] = dict(connections=[dict(name=k, model=v.get('requestedApiIdentifier'),
                                                  endpoint=v.get('endpoint') or v.get('base_url'),
                                                  key=(v.get('credential_ref') or {}).get('name'))
                                             for k, v in sorted(rows.items()) if isinstance(v, dict)], contacted=False)
    cartridges = []
    from aether.memory.fabric import open_engine
    for name, owner in sorted(manifest['cartridge_owners'].items()):
        try:
            with open_engine(str(target/name), read_only=True) as engine:
                seal = engine.open_report.status
                matches = engine.agent_id == owner['agent_id']
        except Exception as exc:   # noqa: BLE001 - reported per cartridge; nothing is repaired here
            seal, matches = f'NOT_OPENED: {type(exc).__name__}: {str(exc)[:200]}', False
        cartridges.append(dict(file=name, owner=owner['agent_id'], owner_matches=matches, seal=seal,
                               seal_note=None if seal in ('sealed', 'tampered') else
                               'Not checked read-only; the engine checks it when the owner next opens it for writing.'))
    report['cartridges'] = cartridges
    restored = sum(r['kind'] == 'restored' for r in references)
    report['steps'] = [
        dict(supported=True, title='Return the data to its original folders',
             text='When the original folders are on the same drive and unchanged since the backup, close Mission Control '
                  'and run switch. It keeps the current originals under this folder’s previous folder. Every reference '
                  f'that names an original folder ({restored} found) is correct again after the switch.',
             command=f'{_quote(app/"Maintenance.cmd")} switch --target {_quote(target)}'),
        dict(supported=True, title='Originals damaged, missing or changed later',
             text='Run preview-replacement, read it, then replace-under-hold with its hash, your name and a reason. The '
                  'replaced originals are kept under this folder’s previous folder. Mission Control then starts on the '
                  'backup and says so; work done after the backup is not in it, and nothing is retried or refunded.',
             command=f'{_quote(app/"Maintenance.cmd")} preview-replacement --target {_quote(target)}'),
        dict(supported=True, title='Use a restored network folder and its hosted agents',
             text='In Settings → Aether Networks, choose the restored network folder and hosted agent homes, then join when '
                  'ready. Homes whose cartridge pointer is relative move with the home; an absolute pointer still names '
                  'the original cartridge (listed under saved references). Nothing joins or runs until you do this.'),
        dict(supported=True, title='Reconnect keys and services',
             text='Unlock or re-enter API keys on API Keys; key grants then apply again. Re-enter each data-source service '
                  'token in Data Sources; backups leave them out. Check each model connection before using it.'),
        dict(supported=False, title='Run this installation’s own data from a different folder or computer',
             text='Not available. The installation is bound to its data folder, and its saved records (accounts, Chats, '
                  'Agents registry, templates and receipts) still name the original folders; no supported step rewrites '
                  'them. Use the staging copy for inspection, or return it to the original folders.')]
    report['summary'] = ('Verified staging, not active. Mission Control never reads a staging folder: no job resumes, no '
                         'agent wakes and no provider or service is contacted from it.')
    return report
