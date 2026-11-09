"""MVP-09 private, exact-version maintenance backup and staged restore.

No key/provider access, schema migration, path rebinding or effect rollback.
Supported writers are installed Aether plus recorded pooled process owners.
External editors must be stopped; all selected SQLite stores are writer-locked
through one snapshot boundary. Credential material is never opened or copied.
"""
from contextlib import ExitStack, contextmanager, closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
import stat
import uuid

from .distribution import direct, verify, SCHEMA as INSTALL_SCHEMA
from .distribution_state import exclusive, maintenance_paths, atomic_json

SCHEMA = 'aether-private-maintenance-backup-1'
DATA_CONTRACT = 'aether-mvp09-exact-build-no-migration-1'
SQLITE = {'.sqlite', '.sqlite3', '.db'}
SECRET_NAMES = {'.sealbox_key', '.env', 'credentials', 'secrets', 'credential-store', 'tokens'}
# Running state of this computer, not user data: a restored copy never starts hosting by itself.
VOLATILE = {'.aether_control/installed-instance.json', '.aether_control/installed-server.lock',
            '.aether_control/workplace/hosting.json'}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def encoded(value):
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False)+'\n').encode('utf-8')


def relative(value):
    if (not isinstance(value, str) or not value or '\\' in value or ':' in value
            or any(c in value for c in '\x00\r\n') or value.startswith('/')
            or any(p in ('', '.', '..') or p.endswith((' ', '.')) for p in value.split('/'))):
        raise ValueError('Canonical relative archive path required')
    return PurePosixPath(value)


def safe(path):
    path = Path(path)
    if '..' in path.parts:
        raise ValueError('Traversal refused')
    path = direct(path)
    if path.exists():
        info = path.stat()
        if stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
            raise ValueError('Hard-linked backup input refused')
        if not stat.S_ISREG(info.st_mode) and not stat.S_ISDIR(info.st_mode):
            raise ValueError('Regular file or directory required')
    return path


def secret_name(relative_path):
    return any(p.lower() in SECRET_NAMES or p.lower().endswith(('.key', '.pem', '.pfx', '.p12'))
               for p in PurePosixPath(relative_path).parts)


def read_json(path):
    path = safe(path)
    if secret_name(path.name):
        raise ValueError('Credential content is not an input')
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('Duplicate metadata key')
            result[key] = value
        return result
    return json.loads(path.read_bytes(), object_pairs_hook=pairs)


def compatibility(app):
    app = safe(app)
    manifest = verify(app)
    if not re.fullmatch('[0-9a-f]{40,64}', manifest.get('source_commit', '')):
        raise ValueError('Exact source commit required')
    return dict(data_contract=DATA_CONTRACT, installation_schema=INSTALL_SCHEMA,
                bundle_sha256=sha((app/'bundle.json').read_bytes()), source_commit=manifest['source_commit'],
                python=manifest['python'], dependencies=manifest['dependencies'])


def installation(app):
    app = safe(app)
    config = read_json(app/'installation.json')
    if config.get('schema') != INSTALL_SCHEMA or config.get('application') != str(app):
        raise ValueError('Exact installed application binding required')
    for field in ('data', 'credentials'):
        safe(config[field])  # Metadata only; never enumerate the credential root.
    return app, config


def _roots(config, mounts):
    roots = {'data': safe(config['data'])}
    if not isinstance(mounts, dict):
        raise ValueError('Explicit external root mapping required')
    for key, path in mounts.items():
        if key == 'data' or not re.fullmatch('[a-z][a-z0-9_-]{0,31}', key):
            raise ValueError('Unique bounded root IDs required')
        roots[key] = safe(path)
    forbidden = [safe(config['application']), safe(config['credentials'])]
    for key, root in roots.items():
        if not root.is_dir() or any(root.is_relative_to(p) or p.is_relative_to(root) for p in forbidden):
            raise ValueError('Selected data root missing or overlaps application/credentials')
        if any(root.is_relative_to(other) or other.is_relative_to(root) for name, other in roots.items() if name != key):
            raise ValueError('Backup roots must be disjoint; nested stores belong to their containing root')
    registry = roots['data']/'projects.json'
    if registry.exists():
        rows = read_json(registry).get('projects')
        if not isinstance(rows, list):
            raise ValueError('Invalid project registry')
        for row in rows:
            project = safe(row['root'])
            if not project.is_dir() or not any(project.is_relative_to(root) for root in roots.values()):
                raise ValueError('Registered project omitted from maintenance snapshot')
    return roots


def _sqlite_file(path):
    with safe(path).open('rb') as stream:
        return stream.read(16) == b'SQLite format 3\x00'


def _walk_error(error):
    raise error


def inventory(roots):
    files, dirs, excluded = {}, [], []
    for key, root in sorted(roots.items()):
        safe(root)
        for folder, directories, names in os.walk(root, followlinks=False, onerror=_walk_error):
            parent = Path(folder)
            for name in list(directories):
                path = parent/name
                rel = path.relative_to(root).as_posix()
                if secret_name(rel):
                    excluded.append(dict(path=key+'/'+rel, reason='credential-content-not-read'))
                    directories.remove(name)
                else:
                    safe(path); dirs.append(key+'/'+rel)
            for name in sorted(names):
                path = parent/name
                rel = path.relative_to(root).as_posix()
                item = key+'/'+rel
                if secret_name(rel):
                    excluded.append(dict(path=item, reason='credential-content-not-read'))
                    continue
                safe(path)
                if rel in VOLATILE or name.endswith('.lock') or name.endswith(('-wal', '-shm', '-journal')):
                    if name.endswith(('-wal', '-shm', '-journal')):
                        base = Path(str(path).rsplit('-', 1)[0])
                        if not base.is_file() or not _sqlite_file(base):
                            raise ValueError('Orphan SQLite sidecar requires repair before backup')
                    excluded.append(dict(path=item, reason='volatile-lock-or-sqlite-snapshot-sidecar'))
                    continue
                files[item] = path
    return files, sorted(dirs), sorted(excluded, key=lambda x: x['path'])


MAX_PATH = 259      # Windows without long-path support; the daybook's content-addressed files alone are 64 characters


def _fits(base, names, what):
    """Refuse a destination whose deepest record path would not fit, before anything is created."""
    longest = max((len(str(base/relative(name))) for name in names), default=len(str(base)))
    if longest > MAX_PATH:
        raise ValueError(f'{what} is too deep: the longest record path would be {longest} characters and Windows allows {MAX_PATH}; '
                         'choose a shorter destination folder')


def _logical(db):
    result = hashlib.sha256()
    for line in db.iterdump():
        result.update(line.encode('utf-8')+b'\n')
    return result.hexdigest()


def _withhold_tokens(name, db):
    """Blank data-source service tokens in a backup copy; the original store is never written.

    VACUUM rebuilds the copy so no protected value survives in free pages. Returns the
    ids of sources whose token was withheld, or None for any other store.
    """
    if relative(name).parts[-2:] != ('data_sources', 'sources.sqlite3'):
        return None
    withheld = []
    for rid, body in db.execute("SELECT id, body FROM records WHERE kind='source' ORDER BY id").fetchall():
        value = json.loads(body)
        if value.get('secret'):
            value['secret'] = ''
            db.execute('UPDATE records SET body=? WHERE id=?', (json.dumps(value), rid))
            withheld.append(rid)
    db.commit()
    if withheld:
        db.execute('VACUUM')
    return withheld


def _owner_mapping(files, owners):
    if not isinstance(owners, dict):
        raise ValueError('Explicit owner GUID -> identity-file mapping required')
    expected = {}
    for guid, path in owners.items():
        if str(uuid.UUID(guid)) != guid:
            raise ValueError('Canonical owner GUID required')
        relative(path)
        if path not in files or not path.endswith('/identity/AGENT_ID.json'):
            raise ValueError('Owner mapping must name a selected AGENT_ID.json')
        identity = read_json(files[path])
        if identity.get('guid') != guid or not identity.get('readable_id'):
            raise ValueError('Owner mapping differs from original identity')
        expected[guid] = dict(identity=path, readable_id=identity['readable_id'], identity_sha256=sha(files[path].read_bytes()))
    for name in files:
        if (name.endswith('/identity/System_0.txt') and name.removesuffix('System_0.txt')+'AGENT_ID.json' not in files
                and name.removesuffix('identity/System_0.txt')+'participant.json' not in files):   # an Atelier home: see _memory_coverage
            raise ValueError('Legacy profile without a GUID owner mapping is outside this backup contract')
    found = [name for name in files if name.endswith('/identity/AGENT_ID.json')]
    if len(set(owners.values())) != len(owners) or set(owners.values()) != set(found):
        raise ValueError('Every selected member identity needs exactly one owner mapping')
    return expected


def _memory_coverage(roots, files, ownership, databases):
    """Installed memory uses data/minds; explicit external minds use the minds mount.

    Pointer paths and owner IDs are checked without opening an engine, decrypting
    anything, or updating seals. Byte/logical hashes preserve the original engine
    state; the engine's normal seal verification still runs when later opened.
    """
    minds = roots.get('minds', roots['data']/'minds')
    settings = roots['data']/'.aether_control/installation_settings.json'
    if settings.exists():
        configured = read_json(settings).get('memory.minds_folder')
        if configured:
            minds = safe(configured)
    cartridge_owners = {}
    for name, path in files.items():
        if not name.endswith('/memory/cartridge.json'):
            continue
        profile = name.removesuffix('memory/cartridge.json')
        entry = next(((guid, item) for guid, item in ownership.items()
                      if item['identity'] == profile+'identity/AGENT_ID.json'), None)
        if entry is None:
            raise ValueError('Cartridge pointer has no selected GUID owner')
        guid, item = entry
        pointer = read_json(path)
        identity = read_json(files[item['identity']])
        if pointer.get('owner') not in {guid, item['readable_id'], *identity.get('aliases', [])}:
            raise ValueError('Cartridge pointer owner differs from selected identity')
        names = [pointer.get('home'), *pointer.get('mounts', [])]
        for filename in names:
            relative(filename)
            if len(PurePosixPath(filename).parts) != 1:
                raise ValueError('Cartridge pointer must name a file inside its minds root')
            target = safe(minds/filename)
            if target not in files.values():
                raise ValueError('Referenced cartridge omitted; include the exact minds root')
            key = next(k for k,v in files.items() if v == target)
            db = databases.get(key)
            if db is None:
                raise ValueError('Memory cartridge must be a supported SQLite store')
            meta = dict(db.execute('SELECT key,value FROM meta'))
            if filename == pointer['home'] and meta.get('agent_id') != pointer['owner']:
                raise ValueError('Cartridge owner differs from the selected home pointer')
            cartridge_owners[key] = dict(agent_id=meta['agent_id'], schema_version=meta['schema_version'])
    # Atelier homes (v0.1): participant.json beside identity/, and memory/.cartridge.json naming the
    # cartridge by a path relative to the pointer (mind.sqlite inside the home) or absolute (a cartridge kept elsewhere,
    # which then needs its root selected). The owner is the participant id; the cartridge's meta says the same.
    for name, path in files.items():
        if not name.endswith('/memory/.cartridge.json'):
            continue
        home = name.removesuffix('memory/.cartridge.json')
        record_name = home+'participant.json'
        pointer = read_json(path)
        if record_name in files:
            participant = read_json(files[record_name])
        elif _project_store_owner(home, pointer):
            # A specialist's project continuity (specialist_context): no participant record by design; its folder
            # name is derived from the owner, so the owner is still checked rather than taken from the pointer alone.
            participant = dict(id=pointer['owner'])
        else:
            raise ValueError('Atelier cartridge pointer has no participant record beside it')
        target = Path(str(pointer.get('path') or 'mind.sqlite'))
        target = safe(target if target.is_absolute() else path.parent/target)
        key = next((k for k, v in files.items() if v == target), None)
        if key is None:
            raise ValueError('Referenced Atelier cartridge omitted; include the root that holds it')
        db = databases.get(key)
        if db is None:
            raise ValueError('Memory cartridge must be a supported SQLite store')
        meta = dict(db.execute('SELECT key,value FROM meta'))
        if meta.get('agent_id') != participant.get('id'):
            raise ValueError('Atelier cartridge owner differs from its participant record')
        cartridge_owners[key] = dict(agent_id=meta['agent_id'], schema_version=meta['schema_version'])
    return cartridge_owners


def _project_store_owner(home, pointer):
    """True for <project>/.aether-specialists/<sha256(owner)[:24]>/, the specialist project-checkpoint layout."""
    parts = PurePosixPath(home).parts
    owner = pointer.get('owner')
    return (len(parts) >= 2 and parts[-2] == '.aether-specialists' and isinstance(owner, str) and bool(owner)
            and parts[-1] == hashlib.sha256(owner.encode()).hexdigest()[:24])


def _dependencies(value):
    if not isinstance(value, list):
        raise ValueError('Explicit credential-provider dependency list required, even if empty')
    for item in value:
        if (set(item) != {'provider', 'reference', 'purpose'}
                or item['provider'] not in {'environment', 'windows-credential-manager', 'external-key-provider', 'file-provider'}
                or any(not isinstance(v, str) or not v.strip() or len(v) > 160 for v in item.values())):
            raise ValueError('Only provider/reference/purpose metadata is supported; never supply secret values')
    return value


@contextmanager
def boundary(app, mounts):
    app, config = installation(app)
    paths = maintenance_paths(app)
    if paths['journal'].exists() or paths['removing'].exists():
        raise ValueError('Finish existing installation maintenance first')
    with ExitStack() as stack:
        stack.enter_context(exclusive(paths['lock']))
        stack.enter_context(exclusive(safe(Path(config['data'])/'.aether_control/installed-server.lock')))
        roots = _roots(config, mounts)
        files, dirs, excluded = inventory(roots)
        databases = {}
        obligations = []
        # All source DB writer reservations precede any copy, across all roots.
        for name, path in sorted(files.items()):
            if not _sqlite_file(path):
                if path.suffix.lower() in SQLITE:
                    raise ValueError('Expected SQLite store is corrupt; preserve it and restore to separate staging')
                continue
            db = sqlite3.connect(path.as_uri()+'?mode=rw', uri=True, timeout=.5, isolation_level=None)
            stack.callback(db.close)
            db.execute('BEGIN IMMEDIATE')
            stack.callback(db.rollback)
            reader = sqlite3.connect(path.as_uri()+'?mode=ro', uri=True, timeout=.5, isolation_level=None)
            stack.callback(reader.close)
            reader.execute('BEGIN')
            if reader.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
                raise ValueError('SQLite integrity failed before backup')
            databases[name] = reader
            if path.name == 'runtime-attempts.sqlite3':
                tables = {r[0] for r in reader.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                row = reader.execute('SELECT raw FROM events ORDER BY revision DESC LIMIT 1').fetchone()
                event = json.loads(row[0]) if row else None
                active = None
                if 'pool_events' in tables:
                    row = reader.execute('SELECT raw FROM pool_events ORDER BY revision DESC LIMIT 1').fetchone()
                    active = json.loads(row[0])['state']['active'] if row else None
                if active:
                    from aether.runtime.orchestration.process_instance import ProcessInstance, observe
                    for identity in active['senders'].values():
                        if observe(ProcessInstance.from_document(identity)).state not in {'gone', 'reused_pid'}:
                            raise ValueError('Recorded process may still write; maintenance snapshot held')
                unresolved = event and event['status'] not in {'normal_completed', 'reconciled'}
                if unresolved and not active:
                    raise ValueError('Legacy unresolved coordinator has no verifiable process roster; backup held')
                if active or unresolved:
                    obligations.append(dict(file=name, event_sha256=sha(encoded(event)),
                                            pool_claim=active, disposition='PRESERVED_NOT_RELEASED'))
        yield app, config, roots, files, dirs, excluded, databases, obligations


def backup(app, destination, *, mounts, owners, credential_dependencies, _review_selection=None):
    destination = safe(destination)
    if destination.exists():
        raise ValueError('Backup destination must be new')
    deps = _dependencies(credential_dependencies)
    with boundary(app, mounts) as (app, config, roots, files, dirs, excluded, dbs, obligations):
        # Trusted maintenance command rechecks its exact reviewed metadata/scope
        # under the original OS/SQLite boundary, before any destination is made.
        if _review_selection is not None:
            _review_selection()
        if any(destination.is_relative_to(p) or p.is_relative_to(destination) for p in [app, *roots.values(), safe(config['credentials'])]):
            raise ValueError('Backup must be separate from every source and credential root')
        version = compatibility(app)
        _fits(destination/'payload', files, 'Backup destination')
        ownership = _owner_mapping(files, owners)
        cartridge_owners = _memory_coverage(roots, files, ownership, dbs)
        if not deps and any(e['reason'] == 'credential-content-not-read' for e in excluded):
            raise ValueError('Excluded protected material requires a named credential-provider recovery dependency')
        if not deps and any(path.read_bytes().startswith(b'SEALBOX-1') for path in files.values() if path.suffix.lower() not in SQLITE):
            raise ValueError('Protected data requires an external credential-provider recovery dependency')
        before = {name: _logical(dbs[name]) if name in dbs else sha(path.read_bytes()) for name, path in files.items()}
        destination.mkdir(parents=True)
        for name in ['payload/'+key for key in roots]+['payload/'+p for p in dirs]:
            safe(destination/relative(name)).mkdir(parents=True, exist_ok=True)
        saved = {}
        for name, source in files.items():
            target = safe(destination/'payload'/relative(name))
            target.parent.mkdir(parents=True, exist_ok=True)
            withheld = None
            if name in dbs:
                with closing(sqlite3.connect(target)) as sink:
                    dbs[name].backup(sink)
                    withheld = _withhold_tokens(name, sink)
                    payload = _logical(sink) if withheld else None
            else:
                with target.open('xb') as sink:
                    sink.write(source.read_bytes()); sink.flush(); os.fsync(sink.fileno())
            saved[name] = dict(sha256=sha(target.read_bytes()), bytes=target.stat().st_size,
                               source_sha256=before[name], kind='sqlite' if name in dbs else 'file')
            if withheld:
                # source_sha256 still identifies the original for switch; the copy is checked against its own digest.
                saved[name].update(payload_sha256=payload, withheld_tokens=withheld)
        after_files, after_dirs, after_excluded = inventory(roots)
        after = {name: _logical(dbs[name]) if name in dbs else sha(path.read_bytes()) for name, path in after_files.items()}
        if before != after or dirs != after_dirs or excluded != after_excluded:
            raise ValueError('Data changed during maintenance; incomplete backup retained without manifest')
        manifest = dict(schema=SCHEMA, backup_id=str(uuid.uuid4()), created=datetime.now(timezone.utc).isoformat(),
            compatibility=version, installation=config, roots={k:str(v) for k,v in roots.items()}, directories=dirs,
            files=saved, owners=ownership, cartridge_owners=cartridge_owners, excluded=excluded, obligations=obligations,
            credential_dependencies=deps, secrets_included=False,
            recovery_dependency_status='EXTERNAL_PROVIDER_REQUIRED' if deps else 'NO_CREDENTIAL_DEPENDENCY_DECLARED',
            restore_policy='EXACT_BUILD_ORIGINAL_PATHS_NO_MIGRATION_NO_RETRY_NO_REFUND')
        atomic_json(destination/'backup.json', manifest)  # Last publication is the completion boundary.
    return dict(backup=str(destination), manifest_sha256=sha((destination/'backup.json').read_bytes()),
                files=len(saved), obligations=len(obligations), credential_dependencies=deps)


def verify_backup(archive, *, expected_manifest_sha256, app):
    archive = safe(archive)
    if sha((archive/'backup.json').read_bytes()) != expected_manifest_sha256:
        raise ValueError('Independently retained backup manifest hash differs')
    manifest = read_json(archive/'backup.json')
    if (manifest['schema'] != SCHEMA or manifest['compatibility'] != compatibility(app)
            or manifest['secrets_included'] is not False
            or manifest['restore_policy'] != 'EXACT_BUILD_ORIGINAL_PATHS_NO_MIGRATION_NO_RETRY_NO_REFUND'):
        raise ValueError('Only the exact supported build/data contract may restore; no migration')
    if not isinstance(manifest['roots'], dict) or 'data' not in manifest['roots']:
        raise ValueError('Original roots missing')
    expected_files = manifest['files']
    actual = {}
    for folder, directories, names in os.walk(safe(archive/'payload'), followlinks=False, onerror=_walk_error):
        for name in directories:
            safe(Path(folder)/name)
        for name in names:
            path = safe(Path(folder)/name)
            rel = path.relative_to(archive/'payload').as_posix()
            relative(rel)
            if secret_name(rel):
                raise ValueError('Credential material cannot be a backup payload')
            actual[rel] = dict(sha256=sha(path.read_bytes()), bytes=path.stat().st_size)
    if set(actual) != set(expected_files):
        raise ValueError('Backup contains missing or extra payload files')
    for name, info in expected_files.items():
        parts = relative(name).parts
        if parts[0] not in manifest['roots'] or actual[name] != {k:info[k] for k in ('sha256', 'bytes')}:
            raise ValueError('Backup payload digest/size/root differs')
        if info['kind'] == 'sqlite':
            with closing(sqlite3.connect((archive/'payload'/name).as_uri()+'?mode=ro', uri=True)) as db:
                if (db.execute('PRAGMA quick_check').fetchall() != [('ok',)]
                        or _logical(db) != info.get('payload_sha256', info['source_sha256'])):
                    raise ValueError('Restored SQLite integrity or logical contents differ')
                if 'withheld_tokens' in info and any(json.loads(body).get('secret') for body, in
                                                     db.execute("SELECT body FROM records WHERE kind='source'")):
                    raise ValueError('A withheld data-source token is present in the backup')
    for name, expected_owner in manifest['cartridge_owners'].items():
        if name not in expected_files or expected_files[name]['kind'] != 'sqlite':
            raise ValueError('Cartridge owner map points outside the snapshot')
        with closing(sqlite3.connect((archive/'payload'/name).as_uri()+'?mode=ro', uri=True)) as db:
            meta = dict(db.execute('SELECT key,value FROM meta'))
            if expected_owner != dict(agent_id=meta['agent_id'], schema_version=meta['schema_version']):
                raise ValueError('Restored cartridge ownership differs')
    paths = {name: archive/'payload'/name for name in expected_files}
    if _owner_mapping(paths, {g:v['identity'] for g,v in manifest['owners'].items()}) != manifest['owners']:
        raise ValueError('Backup owner map differs')
    return manifest


def restore(app, archive, target, *, expected_manifest_sha256):
    """Restore to a separate new target. Never rewrite original absolute bindings."""
    manifest = verify_backup(archive, expected_manifest_sha256=expected_manifest_sha256, app=app)
    target, archive = safe(target), safe(archive)
    if target.exists() or any(target.is_relative_to(p) or p.is_relative_to(target)
            for p in [archive, safe(app), safe(manifest['installation']['credentials']),
                      *(safe(p) for p in manifest['roots'].values())]):
        raise ValueError('Restore target must be new and separate from archive/application/original roots')
    _fits(target, manifest['files'], 'Restore target')
    target.mkdir(parents=True)
    for name in list(manifest['roots'])+manifest['directories']:
        safe(target/relative(name)).mkdir(parents=True, exist_ok=True)
    for name, info in manifest['files'].items():
        output = safe(target/relative(name)); output.parent.mkdir(parents=True, exist_ok=True)
        with output.open('xb') as stream:
            raw = safe(archive/'payload'/relative(name)).read_bytes()
            if sha(raw) != info['sha256']:
                raise ValueError('Backup changed during restore')
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
    receipt = dict(schema='aether-staged-restore-1', archive=str(archive), manifest_sha256=expected_manifest_sha256,
                   target=str(target), status='VERIFIED_STAGING_NOT_ACTIVE', manifest=manifest)
    _verify_stage(target, receipt)
    atomic_json(target/'restore.json', receipt)
    return receipt


def _verify_stage(target, receipt):
    manifest = receipt['manifest']
    for key in manifest['roots']:
        root = safe(target/key)
        if not root.is_dir():
            raise ValueError('Staged root missing')
    files, dirs, excluded = inventory({key: target/key for key in manifest['roots']})
    if excluded:
        raise ValueError('Unmanifested excluded material added to staging')
    if set(files) != set(manifest['files']) or dirs != manifest['directories']:
        raise ValueError('Staged inventory differs')
    for name, path in files.items():
        if sha(path.read_bytes()) != manifest['files'][name]['sha256']:
            raise ValueError('Staged content differs')


def switch(app, target):
    """Activate verified staging at original paths, preserving every prior root.

    Current roots must exactly match the snapshot: later/corrupt records refuse
    automatic rollback, leaving staging for inspection. Missing original roots
    are not silently recreated. This deliberately bounded switch is not a general
    disaster-recovery or cross-version migration authority.
    """
    target = safe(target); receipt = read_json(target/'restore.json')
    if any(Path(p).drive.casefold() != target.drive.casefold() for p in receipt['manifest']['roots'].values()):
        raise ValueError('Switch requires staging and all original roots on the same volume; separate staging remains available')
    if receipt['status'] != 'VERIFIED_STAGING_NOT_ACTIVE' or receipt['target'] != str(target):
        raise ValueError('Exact staged restore required')
    manifest = verify_backup(receipt['archive'], expected_manifest_sha256=receipt['manifest_sha256'], app=app)
    if manifest != receipt['manifest']:
        raise ValueError('Staging receipt differs from original backup')
    mounts = {k:Path(v) for k,v in manifest['roots'].items() if k != 'data'}
    with boundary(app, mounts) as (app, config, roots, files, dirs, excluded, dbs, obligations):
        _verify_stage(target, receipt)
        current = {name: _logical(dbs[name]) if name in dbs else sha(path.read_bytes()) for name,path in files.items()}
        original = {name:info['source_sha256'] for name,info in manifest['files'].items()}
        if (config != manifest['installation'] or {k:str(v) for k,v in roots.items()} != manifest['roots']
                or current != original or dirs != manifest['directories'] or excluded != manifest['excluded']):
            raise ValueError('Current data differs; preserve later/corrupt history and use reviewed reconciliation, not rollback')
        # Database handles must close before Windows renames directories. The
        # installed maintenance and server locks remain held by the outer switch
        # below; this read phase itself performs no destructive operation.
    return _switch_verified(app, target, receipt)


def _switch_verified(app, target, receipt):
    # Re-enter the maintenance lock and revalidate the complete state before the
    # move. File/DB verification happens within these outer lifetime locks.
    app, config = installation(app)
    with exclusive(maintenance_paths(app)['lock']), exclusive(safe(Path(config['data'])/'.aether_control/installed-server.lock')):
        manifest = receipt['manifest']
        roots = {k:safe(v) for k,v in manifest['roots'].items()}
        _verify_stage(target, receipt)
        files, dirs, excluded = inventory(roots)
        current = {}
        for name,path in files.items():
            if manifest['files'].get(name, {}).get('kind') == 'sqlite':
                with closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True, timeout=.5)) as db:
                    current[name] = _logical(db)
            else:
                current[name] = sha(path.read_bytes())
        if (current != {n:i['source_sha256'] for n,i in manifest['files'].items()}
                or dirs != manifest['directories'] or excluded != manifest['excluded']):
            raise ValueError('Original changed before switch; no paths were switched')
        journal = maintenance_paths(app)['journal']
        state = dict(schema='aether-backup-switch-1', target=str(target), original=manifest['roots'],
                     receipt_sha256=sha(encoded({k:v for k,v in receipt.items() if k != 'status'})), moved=[])
        previous = safe(target/'previous'); previous.mkdir(exist_ok=False)
        atomic_json(journal, state)
        # The data lock is INSIDE its root, so Windows cannot rename it while open.
        # Deliberately stop at a durable prepared switch; finish_switch obtains
        # only the app lifetime lock and performs the retained-root swaps.
    return finish_switch(app, target)


def preview_replacement(app, target):
    """Exact preview for damaged/missing/later data: any activation stays held.

    This records byte inventories, not claims that external effects are undone.
    Secret files are named as exclusions and never read. All available original
    roots are preserved by rename; missing originals remain an explicit unknown.
    """
    app, config = installation(app)
    target = safe(target)
    receipt = read_json(target/'restore.json')
    manifest = verify_backup(receipt['archive'], expected_manifest_sha256=receipt['manifest_sha256'], app=app)
    if any(Path(p).drive.casefold() != target.drive.casefold() for p in manifest['roots'].values()):
        raise ValueError('Switch requires staging and all original roots on the same volume')
    if receipt['manifest'] != manifest or receipt['status'] != 'VERIFIED_STAGING_NOT_ACTIVE':
        raise ValueError('Exact verified staging required')
    if manifest['installation'] != config:
        raise ValueError('Restore belongs to another original installation')
    with exclusive(maintenance_paths(app)['lock']):
        if maintenance_paths(app)['journal'].exists():
            raise ValueError('Finish the existing maintenance transaction first')
        _verify_stage(target, receipt)
        roots = {k:safe(v) for k,v in manifest['roots'].items()}
        present = {k:v for k,v in roots.items() if v.exists()}
        files, dirs, excluded = inventory(present)
        current = {name:sha(path.read_bytes()) for name,path in files.items()}
        # Inspect known pool owners in readable DBs, without interpreting a
        # corrupt database as proof of exit. Uninspectable stores remain unknown
        # in this preview and cannot authorize normal launch after replacement.
        processes = []
        from aether.runtime.orchestration.process_instance import ProcessInstance, observe
        for name,path in files.items():
            if path.name != 'runtime-attempts.sqlite3':
                continue
            try:
                with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=.5)) as db:
                    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                    row = db.execute('SELECT raw FROM pool_events ORDER BY revision DESC LIMIT 1').fetchone() if 'pool_events' in tables else None
                    active = json.loads(row[0])['state']['active'] if row else None
                    if active:
                        for role, identity in active['senders'].items():
                            state = observe(ProcessInstance.from_document(identity)).state
                            if state not in {'gone','reused_pid'}:
                                raise ValueError('Original process exit is unproved; do not replace its data')
                            processes.append(dict(file=name,role=role,state=state))
            except sqlite3.DatabaseError:
                processes.append(dict(file=name,state='UNKNOWN_CORRUPT_STORE'))
        return dict(schema='aether-held-restore-review-1', target=str(target),
            manifest_sha256=receipt['manifest_sha256'], compatibility=manifest['compatibility'],
            current_files=current, current_directories=dirs, exclusions=excluded,
            missing_roots=sorted(set(roots)-set(present)), process_observations=processes,
            consequence='RESTORE_HELD_NO_DISPATCH_NO_RETRY_NO_REFUND; originals and later history retained for reconciliation')


def replace_under_hold(app, target, *, expected_preview_sha256, operator, reason):
    """Apply an exact reviewed file replacement; NEVER adjudicate generations.

    The installation cannot launch until the separate restore reconciliation is
    completed. No API here clears that hold or merges/discards later obligations.
    """
    if not isinstance(operator,str) or not operator.strip() or not isinstance(reason,str) or not reason.strip():
        raise ValueError('Explicit operator and reason required for data selection')
    app, config = installation(app)
    target = safe(target)
    preview = preview_replacement(app,target)
    if sha(encoded(preview)) != expected_preview_sha256:
        raise ValueError('Replacement preview changed; review current originals again')
    paths = maintenance_paths(app)
    with exclusive(paths['lock']):
        # The app lock excludes launch. Re-read files under this same retained
        # lock before publishing a switch journal; out-of-band editors are not
        # supported maintenance writers.
        receipt = read_json(target/'restore.json')
        roots = {k:safe(v) for k,v in receipt['manifest']['roots'].items()}
        present = {k:v for k,v in roots.items() if v.exists()}
        files, dirs, excluded = inventory(present)
        if ({name:sha(path.read_bytes()) for name,path in files.items()} != preview['current_files']
                or dirs != preview['current_directories'] or excluded != preview['exclusions']
                or sorted(set(roots)-set(present)) != preview['missing_roots']):
            raise ValueError('Original data changed after replacement preview')
        _verify_stage(target,receipt)
        previous = safe(target/'previous'); previous.mkdir(exist_ok=False)
        state = dict(schema='aether-backup-switch-1', target=str(target), original=receipt['manifest']['roots'],
            receipt_sha256=sha(encoded({k:v for k,v in receipt.items() if k != 'status'})), moved=[],
            missing_roots=preview['missing_roots'], restore_hold=dict(schema='aether-restore-reconciliation-hold-1',
                operator=operator, reason=reason, preview=preview, original_roots=str(previous),
                obligations='ALL_ORIGINAL_AND_BACKUP_OBLIGATIONS_REMAIN_UNADJUDICATED'))
        atomic_json(paths['journal'],state)
    return finish_switch(app,target)


def finish_switch(app, target):
    """Resume our exact journal after process interruption; never discard originals."""
    app = safe(app); target = safe(target)
    paths = maintenance_paths(app)
    with exclusive(paths['lock']):
        state = read_json(paths['journal']); receipt = read_json(target/'restore.json')
        if (state.get('schema') != 'aether-backup-switch-1' or state['target'] != str(target)
                or state['receipt_sha256'] != sha(encoded({k:v for k,v in receipt.items() if k != 'status'})) or state['original'] != receipt['manifest']['roots']):
            raise ValueError('Exact backup switch journal required')
        # Validate every not-yet-moved staged root again after interruption.
        for key in state['original']:
            staged = safe(target/key)
            if not staged.exists():
                continue
            files, dirs, excluded = inventory({key: staged})
            expected = {n:i for n,i in receipt['manifest']['files'].items() if n.startswith(key+'/')}
            if (excluded or set(files) != set(expected)
                    or dirs != [d for d in receipt['manifest']['directories'] if d.startswith(key+'/')]
                    or any(sha(p.read_bytes()) != expected[n]['sha256'] for n,p in files.items())):
                raise ValueError('Interrupted staging changed; maintenance hold retained')
        for key, value in state['original'].items():
            source, staged, previous = safe(value), safe(target/key), safe(target/'previous'/key)
            if previous.exists():
                if staged.exists() and not source.exists():
                    staged.rename(source)
                elif staged.exists() or not source.is_dir():
                    raise ValueError('Ambiguous interrupted switch; originals preserved')
            elif key in state.get('missing_roots', []):
                if source.exists() and not staged.exists():
                    pass  # This exact missing-root restore was already moved.
                elif not source.exists() and staged.is_dir():
                    staged.rename(source)
                else:
                    raise ValueError('Missing-root restore changed; retain the maintenance hold')
            else:
                if not source.is_dir() or not staged.is_dir():
                    raise ValueError('Original or staged root missing; refuse replacement')
                source.rename(previous)
                staged.rename(source)
            if key not in state['moved']:
                state['moved'].append(key); atomic_json(paths['journal'], state)
        for name, info in receipt['manifest']['files'].items():
            key, rel = name.split('/', 1)
            if sha(safe(Path(state['original'][key])/relative(rel)).read_bytes()) != info['sha256']:
                raise ValueError('Switched bytes differ; maintenance hold retained')
        if state.get('restore_hold'):
            control = safe(Path(state['original']['data'])/'.aether_control')
            control.mkdir(exist_ok=True)
            atomic_json(control/'restore-reconciliation.json', state['restore_hold'])
        receipt['status'] = ('RESTORED_HELD_FOR_RECONCILIATION' if state.get('restore_hold') else 'SWITCHED_ORIGINALS_PRESERVED')
        atomic_json(target/'restore.json', receipt)
        paths['journal'].unlink()
        return dict(status=receipt['status'], previous=str(target/'previous'), obligations='PRESERVED_NOT_RELEASED',
                    credential_dependencies=receipt['manifest']['credential_dependencies'])


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='operation', required=True)
    create = commands.add_parser('backup')
    create.add_argument('--app', required=True, type=Path)
    create.add_argument('--destination', required=True, type=Path)
    create.add_argument('--selection', required=True, type=Path,
                        help='JSON with mounts, owners and credential_dependencies; metadata only')
    stage = commands.add_parser('restore')
    stage.add_argument('--app', required=True, type=Path)
    stage.add_argument('--archive', required=True, type=Path)
    stage.add_argument('--target', required=True, type=Path)
    stage.add_argument('--manifest-sha256', required=True)
    preview = commands.add_parser('preview-replacement')
    preview.add_argument('--app',required=True,type=Path)
    preview.add_argument('--target',required=True,type=Path)
    replace = commands.add_parser('replace-under-hold')
    replace.add_argument('--app',required=True,type=Path)
    replace.add_argument('--target',required=True,type=Path)
    replace.add_argument('--preview-sha256',required=True)
    replace.add_argument('--operator',required=True)
    replace.add_argument('--reason',required=True)
    for operation in ('switch', 'finish-switch'):
        command = commands.add_parser(operation)
        command.add_argument('--app', required=True, type=Path)
        command.add_argument('--target', required=True, type=Path)
    args = parser.parse_args(argv)
    if args.operation == 'backup':
        selected = read_json(args.selection)
        if set(selected) != {'mounts', 'owners', 'credential_dependencies'}:
            raise ValueError('Explicit selection with exactly mounts, owners and credential_dependencies required')
        result = backup(args.app, args.destination, **selected)
    elif args.operation == 'restore':
        result = restore(args.app, args.archive, args.target, expected_manifest_sha256=args.manifest_sha256)
    elif args.operation == 'preview-replacement':
        preview = preview_replacement(args.app,args.target)
        result = dict(preview=preview,sha256=sha(encoded(preview)))
    elif args.operation == 'replace-under-hold':
        result = replace_under_hold(args.app,args.target,expected_preview_sha256=args.preview_sha256,operator=args.operator,reason=args.reason)
    else:
        result = (switch if args.operation == 'switch' else finish_switch)(args.app, args.target)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
