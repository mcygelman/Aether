"""Local workspace records: people, role descriptions, authored artifacts and bug notes.

These records are not agent identities, memory, authentication or execution permissions.
Reads create nothing. Mutations are atomic and revision checked; no provider is called.
"""
from contextlib import contextmanager
from copy import deepcopy
import base64
import binascii
import json
from pathlib import Path
import threading
import time
import uuid
import xml.etree.ElementTree as ET
from urllib.parse import urlparse

from aether.config.model_settings_store import writer_lock
from aether.keys.redaction import redact
from aether.integrity.http_body import json_body
from aether.integrity.sealbox import atomic_write_bytes
from .operator_access_http import workspace_allowed

API = '/api/workspace'
ROLES = [
    ('operator', 'Operator', 'Sets the goal, supplies context and decides what is ready to use.'),
    ('lead', 'Lead', 'Coordinates the project, maintains the roadmap and helps the team move forward.'),
    ('developer', 'Developer', 'Builds the work described in the project and its sprints.'),
    ('reviewer', 'Reviewer', 'Checks the result against the brief and reports concrete issues.'),
    ('librarian', 'Librarian', 'Finds, explains and prepares useful reference material.'),
]
KINDS = {'summary', 'bullets', 'tasks', 'aether', 'teachframe', 'note', 'html', 'svg'}


def visual_attachment(value):
    """One portable illustration, rendered only in the browser's image context."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError('Choose an image attachment.')
    media = value.get('media_type')
    if media not in ('image/png', 'image/jpeg', 'image/webp', 'image/gif', 'image/svg+xml'):
        raise ValueError('Choose a PNG, JPEG, WebP, GIF or SVG image.')
    data = value.get('data')
    if not isinstance(data, str) or len(data) > 699052:
        raise ValueError('Choose an image no larger than 512 KiB.')
    try:
        raw = base64.b64decode(data, validate=True)
    except (ValueError, binascii.Error):
        raise ValueError('The image attachment is not valid base64.') from None
    if not raw or len(raw) > 512 * 1024:
        raise ValueError('Choose an image no larger than 512 KiB.')
    signatures = {
        'image/png': raw.startswith(b'\x89PNG\r\n\x1a\n'),
        'image/jpeg': raw.startswith(b'\xff\xd8\xff'),
        'image/gif': raw[:6] in (b'GIF87a', b'GIF89a'),
        'image/webp': raw[:4] == b'RIFF' and raw[8:12] == b'WEBP',
    }
    if media == 'image/svg+xml':
        try:
            text = raw.decode('utf-8-sig')
            if '<!doctype' in text.lower() or '<!entity' in text.lower():
                raise ValueError('SVG declarations are not supported.')
            root = ET.fromstring(text)
            if root.tag not in ('svg', '{http://www.w3.org/2000/svg}svg'):
                raise ValueError('Choose an SVG document.')
        except (UnicodeError, ET.ParseError):
            raise ValueError('Choose a valid UTF-8 SVG image.') from None
    elif not signatures[media]:
        raise ValueError('The image contents do not match the selected format.')
    return dict(name=field(value, 'name', 160, True), media_type=media, data=data,
                alt=field(value, 'alt', 500))


def now():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def field(body, key, limit, required=False):
    value = body.get(key, '')
    if not isinstance(value, str) or len(value) > limit:
        raise ValueError(f'{key.replace("_", " ").capitalize()} must be text up to {limit:,} characters.')
    if required and not value.strip():
        raise ValueError(f'Enter {key.replace("_", " ")}.')
    return value.strip() if key != 'content' else value


class WorkspaceStore:
    def __init__(self, root):
        self.root = Path(root)
        self.path = self.root / 'workspace.json'
        self.lock = threading.RLock()

    def read(self):
        with self.lock:
            if not self.path.exists():
                return dict(version=1, revision=0, halted=False, people=[], artifacts=[], guides=[], bugs=[], events=[],
                            roles=[dict(id=k, name=n, description=d, builtin=True) for k, n, d in ROLES])
            with self.path.open('rb') as stream:
                raw = stream.read(16 * 1024 * 1024 + 1)
            if len(raw) > 16 * 1024 * 1024:
                raise ValueError('The workspace record is too large to open.')
            doc = json.loads(raw)
            doc.setdefault('guides', [])
            return doc

    def halted(self):
        return bool(self.read().get('halted'))

    @contextmanager
    def edit(self, revision):
        with self.lock:
            self.root.mkdir(parents=True, exist_ok=True)
            with writer_lock(self.root / '.workspace.lock'):
                doc = self.read()
                if type(revision) is not int or revision != doc['revision']:
                    raise ValueError('This workspace changed in another window. Refresh before saving; your text is kept.')
                yield doc
                doc['revision'] += 1
                raw = (json.dumps(doc, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
                if len(raw) > 16 * 1024 * 1024:
                    raise ValueError('The workspace has reached its storage limit. Export and archive older material.')
                atomic_write_bytes(self.path, raw)

    def act(self, body, *, project_ids=None):
        action = body.get('action')
        with self.edit(body.get('revision')) as doc:
            if action in ('halt', 'resume'):
                doc['halted'] = action == 'halt'
                label = 'New work stopped' if doc['halted'] else 'New work enabled'
            elif action == 'installation_save':
                name = field(body, 'name', 120, True)
                if any(ord(c) < 32 or ord(c) == 127 for c in name):
                    raise ValueError('Use a single-line installation name.')
                identity = doc.setdefault('installation', dict(id=str(uuid.uuid4()), created_at=now()))
                identity.update(name=name, updated_at=now())
                label = 'Installation name saved'
            elif action == 'dashboard_save':
                area = field(body, 'area', 40, True)
                if area not in ('Missions','Roadmaps','Sprints','AI Staff','Teach Frames','Creations'):
                    raise ValueError('Choose a dashboard.')
                doc.setdefault('dashboards', {})[area] = dict(title=field(body,'title',120,True), notes=field(body,'notes',4000))
                label = 'Dashboard updated'
            elif action in ('person_save', 'role_save', 'artifact_save', 'guide_save', 'bug_save'):
                collection = {'person_save':'people', 'role_save':'roles', 'artifact_save':'artifacts', 'guide_save':'guides', 'bug_save':'bugs'}[action]
                record_id = body.get('id')
                record = next((x for x in doc[collection] if x['id'] == record_id), None) if record_id else None
                if record_id and record is None:
                    raise ValueError('This record was not found. Refresh the page.')
                if record is None:
                    record = dict(id=uuid.uuid4().hex, created_at=now(), archived=False)
                    doc[collection].append(record)
                if action == 'person_save':
                    role = field(body, 'role', 64, True)
                    if role not in {r['id'] for r in doc['roles']}:
                        raise ValueError('Choose a role from the role directory.')
                    record.update(name=field(body,'name',120,True), role=role,
                                  contact=field(body,'contact',250), notes=field(body,'notes',4000))
                    label = 'Staff record saved'
                elif action == 'role_save':
                    record.update(name=field(body,'name',80,True), description=field(body,'description',4000,True))
                    label = 'Role description saved'
                elif action == 'guide_save':
                    tags = body.get('tags', [])
                    if not isinstance(tags,list) or len(tags)>20 or any(not isinstance(t,str) or len(t)>60 for t in tags):
                        raise ValueError('Use up to 20 tags of 60 characters each.')
                    stage = body.get('stage', record.get('stage','draft'))
                    if stage not in ('draft','ready'):
                        raise ValueError('Choose Draft or Ready to use.')
                    record.update(kind='guide', title=field(body,'title',160,True),
                                  content=field(body,'content',250000,True), summary=field(body,'summary',1000),
                                  category=field(body,'category',120), source=field(body,'source',2000),
                                  tags=list(dict.fromkeys(t.strip() for t in tags if t.strip())), stage=stage)
                    label = 'Guide saved'
                elif action == 'artifact_save':
                    if 'project_id' in body:
                        project_id = body['project_id']
                        if not isinstance(project_id, str) or len(project_id) > 160:
                            raise ValueError('Choose a project or No project.')
                        if project_id and project_id != record.get('project_id') and (
                                project_ids is None or project_id not in project_ids):
                            raise ValueError('That project is unavailable. Refresh and choose a current project.')
                        if project_id:
                            record['project_id'] = project_id
                        else:
                            record.pop('project_id', None)
                    kind = field(body,'kind',32,True)
                    if kind not in KINDS:
                        raise ValueError('Choose an artifact type.')
                    if 'visual' in body:
                        visual = visual_attachment(body['visual'])
                        if visual is None:
                            record.pop('visual', None)
                        else:
                            record['visual'] = visual
                    tags = body.get('tags', [])
                    if not isinstance(tags,list) or len(tags)>20 or any(not isinstance(t,str) or len(t)>60 for t in tags):
                        raise ValueError('Use up to 20 tags of 60 characters each.')
                    record.update(title=field(body,'title',160,True), kind=kind,
                                  content=field(body,'content',250000,True), source=field(body,'source',2000),
                                  tags=list(dict.fromkeys(t.strip() for t in tags if t.strip())), draft=True)
                    if kind == 'teachframe':
                        teaching = body.get('teaching', record.get('teaching', {}))
                        if not isinstance(teaching, dict):
                            raise ValueError('Teaching details must be an object.')
                        record['teaching'] = {key:field(teaching,key,limit) for key,limit in
                                             [('school',120),('audience',500),('when_to_use',3000),('prerequisites',1500)]}
                    stage = body.get('stage', record.get('stage','draft'))
                    if stage not in ('draft','ready'):
                        raise ValueError('Choose Draft or Ready to share.')
                    record.update(stage=stage,draft=stage!='ready')
                    label = 'Artifact saved'
                else:
                    record.update(title=field(body,'title',160,True), area=field(body,'area',80),
                                  content=redact(field(body,'content',20000,True)))
                    record.setdefault('status','open')
                    label = 'Bug note saved'
                record['updated_at'] = now()
            elif action in ('person_archive','artifact_archive','artifact_delete','guide_archive','bug_status','role_delete'):
                collection={'person_archive':'people','artifact_archive':'artifacts','artifact_delete':'artifacts','guide_archive':'guides','bug_status':'bugs','role_delete':'roles'}[action]
                record=next((x for x in doc[collection] if x['id']==body.get('id')),None)
                if record is None:
                    raise ValueError('This record was not found.')
                if action=='bug_status':
                    if body.get('status') not in ('open','resolved'):
                        raise ValueError('Choose Open or Resolved.')
                    record['status']=body['status'];label='Bug status changed'
                elif action=='role_delete':
                    if record.get('builtin'):
                        raise ValueError('The standard roles can be described but not removed.')
                    if any(p.get('role')==record['id'] for p in doc['people']):
                        raise ValueError('Move people to another role before removing this one.')
                    doc['roles'].remove(record);label='Role removed'
                elif action=='artifact_delete':
                    # Michel, 11-05: a creation saved by mistake can be removed for good (Archive keeps it instead).
                    doc['artifacts'].remove(record);label='Creation deleted'
                else:
                    if type(body.get('archived')) is not bool:
                        raise ValueError('Choose Archive or Restore.')
                    record['archived']=body['archived'];label='Record archived' if record['archived'] else 'Record restored'
                record['updated_at']=now()
            else:
                raise ValueError('Unknown workspace action.')
            doc['events'].append(dict(id=uuid.uuid4().hex, at=now(), action=action, text=label))
            doc['events']=doc['events'][-500:]
        return self.read()


def handle_workspace(handler, method, state):
    path = urlparse(handler.path).path
    if path not in (API, API + '/installation'):
        return False
    if path == API + '/installation':
        if method != 'GET':
            handler.settings_send(dict(error='Method not allowed'),405)
        else:
            handler.settings_send(dict(installation=state['workspace_store'].read().get('installation')))
        return True
    if method != 'GET' and handler.headers.get('Origin') != f'http://127.0.0.1:{handler.server.server_port}':
        handler.settings_send(dict(error='Use the local workspace page.'),403)
        return True
    try:
        store=state['workspace_store']
        if method=='GET':
            value=store.read()
        elif method=='POST':
            body=json_body(handler,1024*1024,'Send a workspace action as JSON.')
            if not isinstance(body,dict):
                raise ValueError('Send an object.')
            if state.get('operator_access') and not workspace_allowed(handler.operator, body, store.read()):
                handler.settings_send(dict(error='Your operator role does not allow this change.'),403)
                return True
            project_ids = None
            if body.get('action') == 'artifact_save' and body.get('project_id') and state.get('campaigns'):
                catalogue = state['campaigns'].listing()
                project_ids = {p['id'] for p in catalogue.get('unfiled', [])}
                project_ids.update(p['id'] for c in catalogue.get('campaigns', []) for p in c['projects'])
            value=store.act(body, project_ids=project_ids)
            if body.get('action')=='halt':
                # Persist the stop before requesting cooperative shutdown. Already-sent requests may finish.
                errors=[]
                try:
                    if state.get('tasker_projects'):state['tasker_projects'].stop_all()
                except Exception as exc:
                    errors.append(redact(str(exc)))
                try:
                    for chats in state.get('all_chat_stores', lambda: [state['model_chats']])():
                        chats.stop_all()
                except Exception as exc:
                    errors.append(redact(str(exc)))
                try:
                    if state.get('workplace'):state['workplace'].stop()
                except Exception as exc:
                    errors.append(redact(str(exc)))
                value['stop_errors']=errors
        else:
            handler.settings_send(dict(error='Method not allowed'),405)
            return True
        value=deepcopy(value)
        try:
            value['operators']=state['model_settings'].registered_operators()
        except Exception:
            value['operators']=[]
        handler.settings_send(value)
    except (ValueError,OSError,KeyError,TypeError) as exc:
        handler.settings_send(dict(error=redact(str(exc))),409)
    return True
