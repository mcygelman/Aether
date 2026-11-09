"""Campaigns: named containers of projects, as the operator organises the work.

The words are Agile, on the page: a campaign holds projects; a project is one atelier commission (its brief, team and
conversation); its plan is the project's roadmap, made of sprints and tasks. The code keeps "commission" for the project.

campaigns.json sits beside the daybooks, in the folder this computer's network uses, so every computer in the network
sees the same campaigns. It names campaigns and their projects only: deleting a campaign never touches a project's
records, and a project in no campaign is listed as unfiled.

Routes (Atelier edition):
  GET  /api/campaigns                         every campaign with its projects, plus the unfiled projects
  POST /api/campaigns {action, ...}            create {name, description}; update {id, name?, description?};
                                               delete {id}; restore {campaign}; assign {project, campaign|null};
                                               name_project {project, name}

A campaign may also file roadmaps kept in project folders (Projects/<folder>/roadmaps.json) in its `folders` list;
each roadmap lists as a row of kind 'roadmap' with id 'folder:<folder>/<roadmap id>', assign takes that id, and
new_roadmap {campaign, name, description} creates an empty roadmap in the campaign (Michel, 2026-11-02: the pages
say Mission -> Roadmap -> Sprint -> Task).
"""
from __future__ import annotations

import json
import re
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

from aether.integrity.http_body import json_body
from aether.config.model_settings_store import writer_lock

from . import facts as facts_module
from . import projection
from .atelier import Atelier

API = '/api/campaigns'
FILE = 'campaigns.json'
NAME_LIMIT = 120
TEXT_LIMIT = 2000
ROLE = {'Plan': 'Planning', 'Sprint': 'In progress', 'Review': 'In review', 'Close': 'Closing', 'Closed': 'Done'}

_lock = threading.RLock()


def _now():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def _name(value, what='name'):
    text = ' '.join(str(value or '').split())
    if not text:
        raise ValueError(f'Give the {what}.')
    return text[:NAME_LIMIT]


def _slug(name):
    base = re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')[:40] or 'campaign'
    return f'{base}-{uuid.uuid4().hex[:6]}'


def _title(letter):
    first = next((line.strip(' #*-') for line in str(letter or '').splitlines() if line.strip(' #*-')), '')
    return first[:90] + ('…' if len(first) > 90 else '') if first else 'Untitled project'


FOLDER_PREFIX = 'folder:'       # a project folder's roadmap filed in a campaign (campaign['folders'])
FOLDER_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$')


def seed_missions(root, seed) -> list:
    """Add the shipped missions to campaigns.json when absent (called once, with the first-run project seed)."""
    seed = Path(seed)
    if not seed.is_file():
        return []
    campaigns = Campaigns(lambda: Path(root))
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    added = []
    with _lock, writer_lock(root / '.campaigns.lock'):
        doc = campaigns._read()
        for mission in json.loads(seed.read_text(encoding='utf-8')):
            if any(c['id'] == mission['id'] for c in doc['campaigns']):
                continue
            doc['campaigns'].append(dict(id=mission['id'], name=_name(mission['name'], 'campaign a name'),
                                         description=str(mission.get('description') or '')[:TEXT_LIMIT], projects=[],
                                         folders=[f for f in mission.get('folders', []) if FOLDER_RE.match(f)],
                                         created_at=_now(), updated_at=_now()))
            added.append(mission['id'])
        if added:
            campaigns._write(doc)
    return added


class Campaigns:
    def __init__(self, root_of, folders=None, create_roadmap=None):
        self.root_of = root_of           # a function: the folder this computer's network uses now
        self.folders = folders           # a function: roadmap rows by '<folder>/<roadmap id>' (kind 'roadmap')
        self.create_roadmap = create_roadmap   # a function (name, description) -> '<folder>/<roadmap id>'

    def _folder_rows(self) -> dict:
        try:
            return self.folders() if self.folders else {}
        except (OSError, ValueError):
            return {}

    def path(self) -> Path:
        return Path(self.root_of()) / FILE

    def _read(self) -> dict:
        path = self.path()
        if not path.is_file():
            return dict(campaigns=[], project_names={})
        doc = json.loads(path.read_text(encoding='utf-8'))
        doc.setdefault('campaigns', [])
        doc.setdefault('project_names', {})
        return doc

    def _write(self, doc):
        path = self.path()
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(f'.{FILE}.{uuid.uuid4().hex[:8]}.tmp')
        temp.write_bytes((json.dumps(doc, indent=2, ensure_ascii=False) + '\n').encode('utf-8'))
        temp.replace(path)

    def _projects(self, doc) -> dict:
        root = Path(self.root_of())
        out = {}
        for c in projection.commissions(root):
            try:
                state = facts_module.rebuild(Atelier(root, c['id']).daybook)
                stage = projection.conversation_status(state, [])['stage']
                sprints = state['plan']['sprints']
                approved = {a.get('sprint') for a in state['approvals']}
                kind = state['campaign'].get('request_kind') or 'project'
            except Exception:            # noqa: BLE001 - an unreadable project still lists, by its letter
                stage, sprints, approved, kind = 'Plan', [], set(), 'project'
            if kind == 'chat':
                continue                                  # a chat lives on the Chat page, not in a campaign
            out[c['id']] = dict(id=c['id'], name=doc['project_names'].get(c['id']) or _title(c['letter']),
                                brief=c['letter'], opened_at=c['opened_at'], last_at=c['last_at'],
                                stage=ROLE.get(stage, stage), done=stage == 'Closed', kind=kind,
                                sprints=len(sprints), sprints_approved=sum(s['number'] in approved for s in sprints))
        return out

    def listing(self) -> dict:
        root = Path(self.root_of()).resolve()
        return Campaigns(lambda: root, self.folders, self.create_roadmap)._listing()

    def _listing(self) -> dict:
        with _lock:
            doc = self._read()
            projects = self._projects(doc)
            folders = self._folder_rows()
            filed, campaigns = set(), []
            for c in doc['campaigns']:
                rows = [projects[p] for p in c.get('projects', []) if p in projects]
                rows += [row for row in folders.values() if row['folder'] in c.get('folders', [])]
                filed.update(r['id'] for r in rows)
                campaigns.append(dict(c, projects=rows))
            unfiled = [p for p in [*projects.values(), *folders.values()] if p['id'] not in filed]
            return dict(campaigns=campaigns, unfiled=unfiled, folder=str(self.root_of()))

    def _find(self, doc, cid):
        found = next((c for c in doc['campaigns'] if c['id'] == cid), None)
        if found is None:
            raise ValueError('That campaign is no longer here. Refresh the page.')
        return found

    def act(self, body: dict) -> dict:
        # Pin the network for the entire transaction, even if Join Network changes concurrently.
        root = Path(self.root_of()).resolve()
        root.mkdir(parents=True, exist_ok=True)
        with _lock, writer_lock(root / '.campaigns.lock'):
            return Campaigns(lambda: root, self.folders, self.create_roadmap)._act(body)

    def _act(self, body: dict) -> dict:
        action = body.get('action')
        with _lock:
            doc = self._read()
            answer = {}
            if action == 'create':
                name = _name(body.get('name'), 'campaign a name')
                campaign = dict(id=_slug(name), name=name, description=str(body.get('description') or '')[:TEXT_LIMIT],
                                projects=[], created_at=_now(), updated_at=_now())
                doc['campaigns'].append(campaign)
                answer['created'] = campaign['id']
            elif action == 'update':
                campaign = self._find(doc, body.get('id'))
                if 'name' in body:
                    campaign['name'] = _name(body.get('name'), 'campaign a name')
                if 'description' in body:
                    campaign['description'] = str(body.get('description') or '')[:TEXT_LIMIT]
                campaign['updated_at'] = _now()
            elif action == 'delete':
                campaign = self._find(doc, body.get('id'))
                doc['campaigns'].remove(campaign)
                answer['deleted'] = campaign          # the page offers Undo with it; projects stay as they are
            elif action == 'restore':
                campaign = body.get('campaign')
                if not isinstance(campaign, dict) or not campaign.get('id'):
                    raise ValueError('Nothing to restore.')
                if any(c['id'] == campaign['id'] for c in doc['campaigns']):
                    raise ValueError('That campaign is already back.')
                filed = {p for c in doc['campaigns'] for p in c.get('projects', [])}
                doc['campaigns'].append(dict(id=str(campaign['id']), name=_name(campaign.get('name'), 'campaign a name'),
                                             description=str(campaign.get('description') or '')[:TEXT_LIMIT],
                                             projects=list(dict.fromkeys(p for p in campaign.get('projects') or []
                                                                        if isinstance(p, str) and p not in filed)),
                                             folders=list(dict.fromkeys(f for f in campaign.get('folders') or []
                                                                       if isinstance(f, str) and FOLDER_RE.match(f))),
                                             created_at=campaign.get('created_at') or _now(), updated_at=_now()))
            elif action == 'assign':
                project, target = str(body.get('project') or ''), body.get('campaign')
                # A roadmap is filed by its storage folder ('folder:<folder>[/<roadmap id>]'); an atelier project
                # by commission id.
                field, member = ('folders', project[len(FOLDER_PREFIX):].partition('/')[0]) \
                    if project.startswith(FOLDER_PREFIX) else ('projects', project)
                if not (FOLDER_RE if field == 'folders' else projection.COMMISSION_RE).match(member):
                    raise ValueError('Choose a roadmap.')
                if field == 'folders' and not any(r['folder'] == member for r in self._folder_rows().values()):
                    raise ValueError('That roadmap is no longer here. Refresh the page.')
                if target:
                    self._find(doc, target)
                for c in doc['campaigns']:
                    if member in c.get(field, []):
                        c[field].remove(member)
                    if c['id'] == target:
                        c.setdefault(field, []).append(member)
                        c['updated_at'] = _now()
            elif action == 'new_roadmap':
                campaign = self._find(doc, body.get('campaign'))
                if self.create_roadmap is None:
                    raise ValueError('Roadmaps are unavailable on this host.')
                key = self.create_roadmap(_name(body.get('name'), 'roadmap a name'),
                                          str(body.get('description') or '')[:TEXT_LIMIT])
                campaign.setdefault('folders', []).append(key.partition('/')[0])
                campaign['updated_at'] = _now()
                answer['created_roadmap'] = key
            elif action == 'name_project':
                project = body.get('project')
                if str(project or '').startswith(FOLDER_PREFIX):
                    raise ValueError('A roadmap project is renamed in its roadmap, not here.')
                if not projection.COMMISSION_RE.match(str(project or '')):
                    raise ValueError('Choose a project.')
                doc['project_names'][project] = _name(body.get('name'), 'project a name')
            else:
                raise ValueError('Unknown campaign action.')
            self._write(doc)
        return dict(self.listing(), **answer)

    def campaign_of(self, project) -> dict | None:
        with _lock:
            for c in self._read()['campaigns']:
                if project in c.get('projects', []):
                    return dict(id=c['id'], name=c['name'])
        return None


def handle_campaigns(handler, method, state):
    parsed = urlparse(handler.path)
    if parsed.path != API:
        return False
    origin = f'http://127.0.0.1:{handler.server.server_port}'
    if method != 'GET' and handler.headers.get('Origin') != origin:
        handler.settings_send(dict(error='Use this local Aether page.'), 403)
        return True
    try:
        store = state['campaigns']
        if method == 'GET':
            handler.settings_send(store.listing())
        elif method == 'POST':
            body = json_body(handler, 64 * 1024, 'Send the campaign change as JSON.')
            if not isinstance(body, dict):
                raise ValueError('Send an object.')
            handler.settings_send(store.act(body))
        else:
            handler.settings_send(dict(error='Method not allowed'), 405)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        from aether.keys.redaction import redact
        handler.settings_send(dict(error=redact(str(exc))), 409)
    return True
