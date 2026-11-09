"""Projects: the layer above roadmaps.

Each project is one folder directly under the repository's ``Projects/`` folder:

    Projects/<id>/project.json   id, name, description, status
    Projects/<id>/roadmaps.json  the project's roadmaps and sprints (a Roadmap Tracker store)

A roadmap belongs to the project whose store holds it, so no roadmap can be in
two projects and there is no separate membership list to drift. A folder
without project.json is not a project and is skipped.
"""
from __future__ import annotations

import json
import os
import re
import stat
import tempfile
import threading
import time
import unicodedata
from pathlib import Path
from urllib.parse import unquote, urlparse

from aether.roadmaps.tracker import tracker_http
from aether.roadmaps.tracker.tracker_store import ROADMAP_STATUSES, DataValidationError, RoadmapStore, migrate_to_v2

PROJECT_FILE = 'project.json'
ROADMAP_FILE = 'roadmaps.json'
FIELDS = ('id', 'name', 'description', 'status')
EDITABLE = ('name', 'description', 'status')
STATUSES = ROADMAP_STATUSES
ID_PATTERN = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}')
RESERVED = {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)), *(f'LPT{i}' for i in range(1, 10))}
LIMITS = {'name': 120, 'description': 500}  # the Roadmap Tracker's own editor limits
MAX_BYTES = 64 * 1024
API = '/api/project-folders'
UI = '/project-folders/'


class ProjectNotFound(LookupError):
    pass


class ProjectExists(ValueError):
    pass


def valid_id(value) -> bool:
    return isinstance(value, str) and ID_PATTERN.fullmatch(value) is not None and value.upper() not in RESERVED


def id_for(name: str) -> str:
    """Folder-safe id from a project name: 'Café Noir!' becomes 'Cafe-Noir'."""
    ascii_name = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode('ascii')
    return re.sub(r'[^A-Za-z0-9_-]+', '-', ascii_name).strip('-_')[:64].strip('-_')


def _field_errors(record: dict) -> list[str]:
    errors = []
    for field, limit in LIMITS.items():
        if field in record:
            value = record[field]
            if not isinstance(value, str) or not value.strip():
                errors.append(f'{field} must be non-empty text')
            elif len(value.strip()) > limit:
                errors.append(f'{field} must be at most {limit} characters')
    if 'status' in record and record['status'] not in STATUSES:
        errors.append('status must be one of: ' + ', '.join(STATUSES))
    return errors


def validate_project(record, folder: str | None = None) -> list[str]:
    """Every problem with a project record; an empty list means it is valid."""
    if not isinstance(record, dict):
        return ['project.json must be a JSON object']
    errors = []
    extra = sorted(set(record) - set(FIELDS))
    missing = [field for field in FIELDS if field not in record]
    if extra:
        errors.append('unexpected fields: ' + ', '.join(extra))
    if missing:
        errors.append('missing fields: ' + ', '.join(missing))
    if 'id' in record:
        if not valid_id(record['id']):
            errors.append('id must be 1-64 letters, digits, "-" or "_", starting with a letter or digit')
        elif folder is not None and record['id'] != folder:
            errors.append(f'id {record["id"]!r} must match its folder name {folder!r}')
    return errors + _field_errors(record)


def editable_fields(body) -> dict:
    """The editable part of a project, validated and trimmed."""
    if not isinstance(body, dict) or set(body) != set(EDITABLE):
        raise ValueError('Send exactly: name, description, status')
    errors = _field_errors(body)
    if errors:
        raise ValueError('; '.join(errors))
    return dict(name=body['name'].strip(), description=body['description'].strip(), status=body['status'])


def _direct(path: Path) -> None:
    metadata = path.lstat()
    if stat.S_ISLNK(metadata.st_mode) or getattr(metadata, 'st_file_attributes', 0) & 0x400:
        raise ValueError('Linked folders and files are not supported; use a direct folder')


def _read_bounded(path: Path) -> bytes:
    with path.open('rb') as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError(f'{path.name} is larger than {MAX_BYTES} bytes')
    return raw


def _write_json(path: Path, value) -> None:
    """Atomic replace, formatted like the Roadmap Tracker's own store."""
    encoded = (json.dumps(value, indent=2, ensure_ascii=False) + '\n').encode('utf-8')
    descriptor, temporary = tempfile.mkstemp(prefix=f'.{path.name}.', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


# The shipped example project: the Aether MVP roadmap (AetherPublicFacing/Release/build_roadmap_seed.py).
SEED = Path(__file__).resolve().parent / 'seed'
SEEDED_MARKER = '.aether-seeded'


def seed_first_run(root, seed=SEED) -> list[str]:
    """Copy the shipped projects into a data folder's Projects/ once (Michel, 2026-11-02).

    The marker makes it once only: a project the operator later removes or edits is never restored over.
    An existing folder of the same name is never touched."""
    root = Path(root)
    if not seed.is_dir() or (root / SEEDED_MARKER).exists():
        return []
    root.mkdir(parents=True, exist_ok=True)
    _direct(root)
    added = []
    for project in sorted(p for p in seed.iterdir() if p.is_dir()):
        target = root / project.name
        if target.exists() or not valid_id(project.name):
            continue
        target.mkdir()
        for name in (PROJECT_FILE, ROADMAP_FILE):
            if (project / name).is_file():
                (target / name).write_bytes((project / name).read_bytes())
        added.append(project.name)
    (root / SEEDED_MARKER).write_bytes(b'')
    return added


class ProjectFolders:
    """The projects held in one Projects/ folder."""

    def __init__(self, root):
        self.root = Path(root)
        self._lock = threading.RLock()
        self._stores: dict[str, RoadmapStore] = {}

    def _children(self) -> list[Path]:
        if not self.root.exists():
            return []
        _direct(self.root)
        return sorted((entry for entry in self.root.iterdir() if entry.is_dir()), key=lambda entry: entry.name.casefold())

    def _record(self, entry: Path) -> dict:
        _direct(entry)
        path = entry / PROJECT_FILE
        _direct(path)
        try:
            record = json.loads(_read_bounded(path).decode('utf-8'))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f'project.json is not valid UTF-8 JSON: {exc}') from exc
        errors = validate_project(record, entry.name)
        if errors:
            raise ValueError('project.json: ' + '; '.join(errors))
        return record

    def store(self, entry: Path) -> RoadmapStore:
        """One shared store per project, so its in-process lock covers every request.

        Checked on every call, not only at discovery: a roadmaps.json or its lock file that
        is (or later becomes) a link would redirect reads and writes outside the project."""
        _direct(entry)
        path = entry / ROADMAP_FILE
        for candidate in (path, path.with_name(path.name + '.lock')):
            if os.path.lexists(candidate):
                _direct(candidate)
        key = str(path.resolve())
        with self._lock:
            return self._stores.setdefault(key, RoadmapStore(Path(key)))

    def _view(self, entry: Path, record: dict) -> dict:
        view = {field: record[field] for field in FIELDS}
        try:
            data, _ = self.store(entry).read()
            view.update(roadmap_ids=[roadmap['id'] for roadmap in data['roadmaps']], roadmap_error=None)
        except (ValueError, OSError) as exc:
            view.update(roadmap_ids=[], roadmap_error=f'roadmaps.json could not be read: {exc}')
        return view

    def roadmap_rows(self) -> dict:
        """Every roadmap, as a row a mission can file (kind 'roadmap'), keyed '<folder>/<roadmap id>'; read-only.

        Michel, 2026-11-02: the pages say Mission -> Roadmap -> Sprint -> Task; the folder is only storage."""
        rows = {}
        for project in self.list().get('projects', []):
            if not project.get('roadmap_ids'):
                continue
            try:
                entry, _ = self.project(project['id'])
                data, _ = self.store(entry).read()
            except (ValueError, OSError, ProjectNotFound):
                continue
            for roadmap in data['roadmaps']:
                sprints = [s for s in data['sprints'] if s['roadmapId'] == roadmap['id']]
                dates = [roadmap.get('updated', '')] + [s.get('updated', '') for s in sprints]
                key = project['id'] + '/' + roadmap['id']
                rows[key] = dict(id='folder:' + key, key=key, folder=project['id'], roadmap=roadmap['id'],
                                 kind='roadmap', name=roadmap['name'],
                                 brief=roadmap.get('next') or roadmap['description'], stage=roadmap['status'],
                                 done=roadmap['status'] in ('Completed', 'Aborted'), opened_at='',
                                 last_at=max(dates, default=''), sprints=len(sprints),
                                 sprints_closed=sum(s.get('status') == 'Closed' for s in sprints))
        return rows

    def create_roadmap(self, name, description='') -> str:
        """A new, empty roadmap in its own folder; returns its key '<folder>/<roadmap id>'."""
        name = ' '.join(str(name or '').split())
        description = ' '.join(str(description or '').split()) or 'Roadmap for ' + name
        view = self.create(dict(name=name, description=description[:LIMITS['description']], status='Planned'))
        entry, _ = self.project(view['id'])
        rid = 'roadmap-' + view['id'].lower()
        store = self.store(entry)
        data, revision = store.read()
        data = migrate_to_v2(data)
        data['roadmaps'].append(dict(id=rid, name=view['name'], description=view['description'], status='Planned',
                                     updated=time.strftime('%Y-%m-%d')))
        store.write(data, revision)
        return view['id'] + '/' + rid

    def roadmap(self, key) -> tuple[Path, str, str]:
        """The store path, roadmap id and name for a key '<folder>/<roadmap id>'."""
        folder, _, rid = str(key or '').partition('/')
        entry, _ = self.project(folder)
        store = self.store(entry)
        data, _ = store.read()
        found = next((r for r in data['roadmaps'] if r['id'] == rid), None)
        if found is None:
            raise ProjectNotFound('That roadmap is no longer here; choose another')
        return store.path, rid, found['name']

    def project(self, pid) -> tuple[Path, dict]:
        """The folder and record of an existing project, matched exactly by id."""
        if valid_id(pid):
            with self._lock:
                for entry in self._children():
                    if entry.name == pid and (entry / PROJECT_FILE).is_file():
                        return entry, self._record(entry)
        raise ProjectNotFound(f'No project {pid!r} in {self.root}')

    def list(self) -> dict:
        projects, problems = [], []
        with self._lock:
            for entry in self._children():
                if not (entry / PROJECT_FILE).exists():
                    continue
                try:
                    projects.append(self._view(entry, self._record(entry)))
                except (ValueError, OSError) as exc:
                    problems.append(dict(folder=entry.name, error=str(exc)))
        return dict(root=str(self.root), projects=projects, problems=problems)

    def get(self, pid) -> dict:
        entry, record = self.project(pid)
        return self._view(entry, record)

    def create(self, body) -> dict:
        fields = editable_fields(body)
        pid = id_for(fields['name'])
        if not valid_id(pid):
            raise ValueError('Choose a name with letters or digits that is not a reserved Windows name; it also becomes the folder name')
        with self._lock:
            self.root.mkdir(exist_ok=True)
            _direct(self.root)
            clash = next((entry.name for entry in self.root.iterdir() if entry.name.casefold() == pid.casefold()), None)
            if clash is not None:
                raise ProjectExists(f'Projects already has a folder named {clash}; choose another name')
            entry = self.root / pid
            entry.mkdir()
            record = dict(id=pid, **fields)
            # project.json is written last: a half-made folder is never listed as a project.
            _write_json(entry / ROADMAP_FILE, dict(schemaVersion=1, roadmaps=[], sprints=[]))
            _write_json(entry / PROJECT_FILE, record)
            return self._view(entry, record)

    def update(self, pid, body) -> dict:
        fields = editable_fields(body)
        with self._lock:
            entry, record = self.project(pid)
            record = dict(id=record['id'], **fields)
            _write_json(entry / PROJECT_FILE, record)
            return self._view(entry, record)


def _local(handler, method) -> bool:
    """Same rule as the tracker: this computer only, and writes only from the page itself."""
    host = handler.headers.get('Host')
    if host not in (f'127.0.0.1:{handler.server.server_port}', f'localhost:{handler.server.server_port}'):
        return False
    origin = handler.headers.get('Origin')
    return origin in (None, 'http://' + host) if method == 'GET' else origin == 'http://' + host


def _body(handler):
    if handler.headers.get('Content-Type', '').split(';')[0].strip() != 'application/json':
        raise ValueError('Send application/json')
    try:
        length = int(handler.headers.get('Content-Length', '0'))
    except ValueError:
        length = 0
    if not 0 < length <= MAX_BYTES:
        raise ValueError(f'Send a JSON body of at most {MAX_BYTES} bytes')
    return json.loads(handler.rfile.read(length).decode('utf-8'))


def _api(handler, method, service, rest):
    if not _local(handler, method):
        handler.settings_send({'error': 'Use the local Mission Control page.'}, 403)
        return
    if service is None:
        handler.settings_send({'error': 'Projects are unavailable on this host'}, 503)
        return
    if rest == '' and method == 'GET':
        handler.settings_send(service.list())
    elif rest == '' and method == 'POST':
        handler.settings_send(service.create(_body(handler)), 201)
    elif rest and '/' not in rest and method == 'GET':
        handler.settings_send(service.get(unquote(rest)))
    elif rest and '/' not in rest and method == 'PUT':
        handler.settings_send(service.update(unquote(rest), _body(handler)))
    elif rest and '/' in rest:
        handler.settings_send({'error': 'Unknown projects resource'}, 404)
    else:
        handler.settings_send({'error': 'Method unavailable'}, 405)


def _tracker(handler, method, service, path):
    pid, _, rest = path[len(UI):].partition('/')
    if not _local(handler, method):
        handler.settings_send({'error': 'Use the local Mission Control page.'}, 403)
        return
    if service is None:
        handler.settings_send({'error': 'Projects are unavailable on this host'}, 503)
        return
    entry, record = service.project(unquote(pid))
    base = UI + record['id'] + '/tracker/'
    if rest == 'tracker' and method == 'GET':
        handler.send_response(303)
        handler.send_header('Location', base)
        handler.send_header('Content-Length', '0')
        handler.end_headers()
    elif not (rest.startswith('tracker/') and tracker_http.handle_tracker(
            handler, method, store=service.store(entry), base=base, scope='Project: ' + record['name'])):
        handler.settings_send({'error': 'Roadmap resource unavailable'}, 404)


def handle_project_folders(handler, method, service) -> bool:
    """Serve /api/project-folders[/<id>] and each project's roadmap view at /project-folders/<id>/tracker/."""
    path = urlparse(handler.path).path
    try:
        if path == API or path.startswith(API + '/'):
            _api(handler, method, service, path[len(API) + 1:])
        elif path.startswith(UI):
            _tracker(handler, method, service, path)
        else:
            return False
    except ProjectNotFound as exc:
        handler.settings_send({'error': str(exc)}, 404)
    except ProjectExists as exc:
        handler.settings_send({'error': str(exc)}, 409)
    except (ValueError, OSError) as exc:
        handler.settings_send({'error': str(exc)}, 400)
    return True
