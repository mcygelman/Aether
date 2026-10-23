"""WMC-framed project creation. No model, identity, hiring or execution authority."""
from __future__ import annotations
from aether.paths import LEGACY_PACKAGE_DIR, REPOSITORY_ROOT

import hashlib
import json
import os
import stat
from pathlib import Path
import uuid

from aether.roadmaps.projects import Project, ProjectRegistry, ensure_home, timestamp
from aether.config.model_settings_store import parse_json, StaleSettings

SCHEMA = 'aether-project-framing-1.0.0'
REPOSITORY = REPOSITORY_ROOT
CATALOG = REPOSITORY/'AetherLexicon/Format_Web/WMC_Catalog.json'

def digest(raw):
    return hashlib.sha256(raw).hexdigest()

def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(',', ':')).encode('utf-8')

def checked_path(path):
    path = Path(path)
    if not path.is_absolute():
        raise ValueError('Choose an absolute project folder.')
    for part in (path, *path.parents):
        try:
            metadata = part.lstat()
        except (FileNotFoundError, NotADirectoryError):
            continue
        if stat.S_ISLNK(metadata.st_mode) or getattr(metadata, 'st_file_attributes', 0) & 0x400:
            raise ValueError('Linked project folders are not supported; choose a direct folder.')
    return path.resolve()

def read_catalog(path=CATALOG, source_root=REPOSITORY):
    raw = Path(path).read_bytes(); data = parse_json(raw)
    if data.get('schemaVersion') != 'aether-wmc-catalog-1.0.0' or data.get('catalogStatus') != 'canonical':
        raise ValueError('The WMC catalog is not a supported canonical catalog.')
    entries = data.get('entries')
    if not isinstance(entries, list) or not entries:
        raise ValueError('The WMC catalog has no entries.')
    ids = [e['id'] for e in entries]
    if len(set(ids)) != len(ids):
        raise ValueError('The WMC catalog contains duplicate identifiers.')
    defaults = [e for e in entries if e.get('default') is True]
    if len(defaults) != 1 or defaults[0]['id'] != data.get('defaultWmcId'):
        raise ValueError('The WMC catalog must identify one explicit default.')
    available, unavailable = [], []
    for row in entries:
        if row.get('status') != 'canonical' or row.get('publicEligible') is not True:
            unavailable.append(dict(id=row['id'], name=row['name'], reason='Not eligible for this project setup version.'))
            continue
        rel = Path(row['source'])
        root = checked_path(source_root)
        source = checked_path(root/rel)
        if rel.is_absolute() or not source.is_relative_to(root):
            raise ValueError('A WMC source is outside the catalog source root.')
        if digest(source.read_bytes()) != row['sha256']:
            raise ValueError(f"WMC source integrity failed: {row['id']}. Restore the accepted source before creating a project.")
        available.append({k:row[k] for k in ('id','name','description','scope','version','source','sha256')})
    if data['defaultWmcId'] not in {e['id'] for e in available}:
        raise ValueError('The catalog default is not eligible for project creation.')
    return dict(revision=digest(raw), default_wmc_id=data['defaultWmcId'], entries=available, unavailable=unavailable)

# Windows MAX_PATH (lane F, 2026-10-10): the deepest record under a campaign folder (the closure
# identity session log under .aether/planning/minder/native-closure-runs/<run>/identity-closure/...)
# needs about 183 characters; the old managed folder name 'project-<32 hex>' plus the default
# %LOCALAPPDATA%\Aether\Projects reached 268 and failed at closure. Folders get a short name and
# creation refuses a location too deep for the deepest record when long paths are disabled.
DEEPEST_RECORD_CHARS = 190
WINDOWS_MAX_PATH = 260


def managed_folder(request_id):
    """Short managed folder name; the campaign id stays 'project-<32 hex>'."""
    return 'c-' + uuid.UUID(request_id).hex[:12]


def windows_long_paths_enabled():
    if os.name != 'nt':
        return True
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'SYSTEM\CurrentControlSet\Control\FileSystem') as key:
            return winreg.QueryValueEx(key, 'LongPathsEnabled')[0] == 1
    except OSError:
        return False


def require_path_room(root):
    """Refuse a campaign location whose deepest record would exceed Windows' path limit."""
    if os.name == 'nt' and not windows_long_paths_enabled() and len(str(root)) + DEEPEST_RECORD_CHARS >= WINDOWS_MAX_PATH:
        room = WINDOWS_MAX_PATH - DEEPEST_RECORD_CHARS - 1
        raise ValueError(f'This campaign folder path is {len(str(root))} characters; Windows paths are limited to '
                         f'{WINDOWS_MAX_PATH} and Aether\'s deepest record needs up to {DEEPEST_RECORD_CHARS} more. '
                         f'Choose a folder path of at most {room} characters, or enable Windows long paths.')
    return root


def managed_root():
    configured = os.environ.get('AETHER_PROJECTS_HOME')
    if configured:
        return checked_path(configured)
    base = os.environ.get('LOCALAPPDATA')
    return checked_path(Path(base)/'Aether'/'Projects' if base else Path.home()/'.local'/'share'/'Aether'/'Projects')

class ProjectSetup:
    def __init__(self, registry, *, catalog=CATALOG, source_root=REPOSITORY, managed=None, installed_roots=None):
        self.registry = registry
        self.catalog_path = Path(catalog)
        self.source_root = Path(source_root)
        self.managed = checked_path(managed) if managed is not None else managed_root()
        self.installed_roots = installed_roots

    def _installed_location(self, root):
        if self.installed_roots is None:
            return root
        roots = self.installed_roots
        protected = [roots['application'], roots['credentials'], *roots['maintenance']]
        for value in protected:
            other = checked_path(value)
            if root.is_relative_to(other) or other.is_relative_to(root):
                raise ValueError(f'Project folder overlaps installed application or credentials: {other}')
        data = checked_path(roots['data'])
        if root.is_relative_to(data) or data.is_relative_to(root):
            if not any(root != parent and root.is_relative_to(parent)
                       for parent in (data/'p', data/'Projects')):
                raise ValueError('Choose a campaign folder below data/p or data/Projects, or a separate external folder.')
        return root

    def options(self):
        return dict(catalog=read_catalog(self.catalog_path, self.source_root), managed_root=str(self.managed))

    def create(self, request):
        if not isinstance(request, dict) or set(request) != {'request_id','name','summary','root','wmc_id','catalog_revision','wmc_accepted'}:
            raise ValueError('The project form is incomplete or unsupported. Reload the setup page.')
        request_id = str(uuid.UUID(request['request_id']))
        for key in ('name','summary','root','wmc_id','catalog_revision'):
            if not isinstance(request[key], str):
                raise ValueError(f'{key} must be text.')
        if not request['name'].strip() or not request['summary'].strip():
            raise ValueError('Enter a project name and a summary of its purpose and intended outcome.')
        if request['wmc_accepted'] is not True:
            raise ValueError('Accept the selected world model before creating the project.')
        fingerprint = digest(encoded(request))
        registry = self.registry
        with registry._lock:
            registry.load()
            for existing in registry.projects:
                if existing.framing and existing.framing.get('creation_request_id') == request_id:
                    if existing.framing.get('creation_request_sha256') != fingerprint:
                        raise ValueError('This creation request was already used for different content. Start a new project form.')
                    self._installed_location(checked_path(existing.root_path))
                    return dict(project=existing.to_dict(), active=registry.active, replayed=True, selected=existing.id==registry.active)
            from .planning_contract import refuse_brief_request_reuse
            refuse_brief_request_reuse(registry.projects,request_id)
            catalog = read_catalog(self.catalog_path, self.source_root)
            if request['catalog_revision'] != catalog['revision']:
                raise ValueError('The world model catalog changed. Reload and review the selection again.')
            wmc = next((e for e in catalog['entries'] if e['id']==request['wmc_id']), None)
            if wmc is None:
                raise ValueError('Select an eligible world model from the current catalog.')
            pid = 'project-'+uuid.UUID(request_id).hex
            if any(p.id==pid for p in registry.projects):
                raise ValueError('That project identifier is already in use. Start a new project form.')
            root = require_path_room(self._installed_location(checked_path(request['root']) if request['root']
                                     else checked_path(self.managed/managed_folder(request_id))))
            for existing in registry.projects:
                other = existing.root_path
                if root==other or root.is_relative_to(other) or other.is_relative_to(root):
                    raise ValueError('That folder overlaps an existing project. Choose a separate folder.')
            intent_path = checked_path(root/'.aether'/'creation-intent.json')
            if root.exists() and not root.is_dir():
                raise ValueError('The project location must be a folder.')
            intent = None
            if root.exists() and any(root.iterdir()):
                if not intent_path.is_file():
                    raise ValueError('The folder is not empty. Existing content will not be adopted or overwritten.')
                intent = parse_json(intent_path.read_bytes())
                if (intent.get('request_sha256') != fingerprint or intent.get('registry') != str(registry.registry_path)):
                    raise ValueError('That folder belongs to a different creation request; its contents were preserved.')
                # Refuse unexpected partial contents; never delete or silently adopt them.
                allowed_dirs = {root/'.aether', *(root/'.aether'/n for n in ('staff','journal','missions','board','reflections'))}
                pending = list(root.iterdir())
                while pending:
                    p = pending.pop(); checked_path(p)
                    if p==intent_path: continue
                    if not p.is_dir() or p not in allowed_dirs:
                        raise ValueError('The pending project folder changed. Inspect it before completing creation.')
                    pending.extend(p.iterdir())
            now = intent['project']['created_at'] if intent else timestamp()
            if not isinstance(now, str) or not now:
                raise ValueError('Pending creation timestamp is invalid.')
            project = Project(pid, request['name'].strip(), str(root), now, framing=dict(
                    schema_version=SCHEMA, summary=request['summary'].strip(), state='WMC_SELECTED', updated_at=now,
                    wmc={**wmc,'catalog_sha256':catalog['revision']}, wmc_history=[dict(action='selected',wmc_id=wmc['id'],at=now)],
                    heavyweight=None, proposals=[], approvals=[], staff=[], sprints=[], evidence=[],
                    next_action='Select an eligible heavyweight. Governed hiring is the next product integration step.',
                    creation_request_id=request_id, creation_request_sha256=fingerprint,
                    events=[dict(id=request_id,prior='DRAFT_PROJECT',state='WMC_SELECTED',at=now,
                        actor='local-ui-request',authority='Project context creation only',reason='Explicit WMC selection accepted')]))
            if intent:
                if intent['project'] != project.to_dict():
                    raise ValueError('Pending project creation evidence changed; inspect it before continuing.')
            else:
                root.mkdir(parents=True, exist_ok=True)
                if any(root.iterdir()):
                    raise ValueError('The project folder changed before creation. Existing contents were preserved.')
                (root/'.aether').mkdir()
                with intent_path.open('xb') as stream:
                    stream.write(encoded(dict(registry=str(registry.registry_path),request_sha256=fingerprint,project=project.to_dict())))
                    stream.flush(); os.fsync(stream.fileno())
            checked_path(root); checked_path(intent_path)
            ensure_home(project)
            candidate = ProjectRegistry(registry.lightweight_dir, registry.registry_path)
            candidate.load()
            if candidate._raw != registry._raw:
                raise ValueError('Another project change occurred. Retry this same form to inspect its saved outcome.')
            candidate.projects.append(project)
            # Creation never changes the active project or binds a manager.
            candidate.save()
            registry.load()
            return dict(project=project.to_dict(),active=registry.active,replayed=False,selected=False)

    def request_may_have_started(self, request):
        """Keep retries bound only when an actual commit or intent may exist."""
        try:
            request_id = str(uuid.UUID(request['request_id']))
            self.registry.load()
            if any(p.framing and p.framing.get('creation_request_id')==request_id for p in self.registry.projects):
                return True
            root = checked_path(request['root']) if request.get('root') else checked_path(self.managed/managed_folder(request_id))
            return (root/'.aether'/'creation-intent.json').exists()
        except (ValueError, KeyError, TypeError):
            return False
        except OSError:
            return True

def handle_project_setup(handler, method, setup):
    route = handler.path.split('?', 1)[0]
    if route not in ('/projects/new','/api/project-setup/options','/api/project-setup/create'):
        return False
    origin = f'http://127.0.0.1:{handler.server.server_port}'
    request = None
    try:
        if handler.headers.get('Host') != origin.removeprefix('http://') or (method != 'GET' and handler.headers.get('Origin') != origin):
            handler.settings_send({'error':'Use this local project setup page.'},403)
        elif method=='GET' and route=='/projects/new':
            handler.settings_send((LEGACY_PACKAGE_DIR/'web'/'project_setup.html').read_bytes(),html=True)
        elif method=='GET' and route=='/api/project-setup/options':
            handler.settings_send(setup.options())
        elif method=='POST' and route=='/api/project-setup/create':
            length = int(handler.headers.get('Content-Length','0'))
            if handler.headers.get('Content-Type','').split(';')[0]!='application/json' or not 0 < length <= 1024*1024:
                raise ValueError('Submit a supported JSON project form (up to 1 MiB).')
            request = parse_json(handler.rfile.read(length))
            handler.settings_send(setup.create(request),201)
        else:
            handler.settings_send({'error':'This method is unavailable for project setup.'},405)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        message = 'Another project save is in progress. Retry this form after it finishes.' if isinstance(exc,StaleSettings) else str(exc)
        handler.settings_send({'error':message,'retry_same_request':setup.request_may_have_started(request) if isinstance(request,dict) else False},400)
    return True


MAX_DOCUMENT = 1024 * 1024


def read_document(path):
    """A bounded setup JSON document (1 MiB), never a key file, never through a linked folder.

    Moved here from aether.operations.public_setup on 2026-10-07 (MOD-15), next to the
    checked_path it relies on.
    """
    path = Path(path)
    if any(p.lower() == '.sealbox_key' or p.lower().endswith('.key') for p in path.parts):
        raise ValueError('Key paths are not setup inputs')
    path = checked_path(path)
    with path.open('rb') as stream:
        raw = stream.read(MAX_DOCUMENT + 1)
    if len(raw) > MAX_DOCUMENT:
        raise ValueError('Setup document exceeds 1 MiB')
    return parse_json(raw)
