"""Own source configuration, role checks and private reporting records.

Never store plaintext service credentials, grant database permissions or copy records to agent cartridges.
"""
from contextlib import contextmanager
from copy import deepcopy
import base64
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import uuid
from aether.atelier.operator_access import can
from aether.config.model_settings_store import utc_now
from .mcp import endpoint


def protected(value, decrypt=False):
    from aether.keys.vault import _dpapi
    if decrypt:
        return _dpapi(base64.b64decode(value, validate=True), b'Aether MCP service v1', decrypt=True).decode('utf-8')
    return base64.b64encode(_dpapi(value.encode('utf-8'), b'Aether MCP service v1')).decode('ascii')


def fingerprint(tool):
    return hashlib.sha256(json.dumps(tool, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def text(value, name, limit, required=False):
    if not isinstance(value, str) or len(value) > limit or (required and not value.strip()):
        raise ValueError('Enter ' + name + ' (up to ' + str(limit) + ' characters).')
    return value.strip()


class SourceStore:
    def __init__(self, root, access, protector=protected):
        self.root, self.access, self.protector = Path(root), access, protector
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / 'sources.sqlite3'
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS records (id TEXT PRIMARY KEY, kind TEXT NOT NULL, owner TEXT NOT NULL, body TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS metadata (id INTEGER PRIMARY KEY CHECK(id=1), revision TEXT NOT NULL)')
            db.execute('INSERT OR IGNORE INTO metadata VALUES (1,?)', (uuid.uuid4().hex,))

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    def actor(self, aid, permission=None):
        user = self.access.actor(aid)
        if not user or self.access.read().get('suspended', False) or (permission and not can(user, permission)):
            raise PermissionError('Your account cannot use this data-source action.')
        return user

    def raw(self, sid):
        with self.db() as db:
            row = db.execute("SELECT body FROM records WHERE id=? AND kind='source'", (sid,)).fetchone()
        if not row:
            raise ValueError('Data source not found.')
        return json.loads(row[0])

    def allowed(self, user, source, model=False):
        return bool(user and can(user, 'use_data_sources') and source['enabled'] and
                    (user['role'] == 'admin' or set(source['roles']) & {user['role'], *user.get('roles', [])}) and
                    (not model or source['allow_model_use']))

    def source(self, aid, sid, model=False):
        user = self.actor(aid, 'use_data_sources')
        source = self.raw(sid)
        if not self.allowed(user, source, model):
            raise PermissionError('This source is unavailable to your role or is not enabled for models.')
        return source

    @staticmethod
    def public(source, manager=False):
        if manager:
            value = deepcopy(source)
            value['token_configured'] = bool(value.pop('secret', ''))
            return value
        return {k: deepcopy(source[k]) for k in ('id', 'name', 'alias', 'description', 'provider', 'guide', 'version', 'tools', 'allow_model_use')}

    def listing(self, aid, manager=False, model=False):
        user = self.actor(aid, 'manage_data_sources' if manager else None)
        with self.db() as db:
            revision = db.execute('SELECT revision FROM metadata WHERE id=1').fetchone()[0]
            rows = [json.loads(r[0]) for r in db.execute("SELECT body FROM records WHERE kind='source' ORDER BY id")]
        return dict(revision=revision, sources=[self.public(s, manager) for s in rows if manager or self.allowed(user, s, model)])

    def update(self, aid, body):
        self.actor(aid, 'manage_data_sources')
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            revision = db.execute('SELECT revision FROM metadata WHERE id=1').fetchone()[0]
            if body.get('revision') != revision:
                raise ValueError('Source settings changed. Refresh before saving.')
            sid = body.get('id') or uuid.uuid4().hex
            row = db.execute("SELECT body FROM records WHERE id=? AND kind='source'", (sid,)).fetchone()
            old = json.loads(row[0]) if row else {}
            if body.get('id') and not old:
                raise ValueError('Data source not found.')
            if body.get('action') == 'delete':
                db.execute("DELETE FROM records WHERE id=? AND kind='source'", (sid,))
            else:
                source = dict(id=sid, version=uuid.uuid4().hex, catalog=old.get('catalog', []), secret=old.get('secret', ''))
                for key, limit in [('name', 100), ('alias', 60), ('description', 500), ('provider', 80), ('guide', 12000)]:
                    source[key] = text(body.get(key, ''), key, limit, key in ('name', 'alias'))
                if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]*', source['alias']):
                    raise ValueError('Use an alias beginning with a letter, followed by letters, numbers, underscores or hyphens.')
                for other, in db.execute("SELECT body FROM records WHERE kind='source' AND id<>?", (sid,)):
                    if json.loads(other)['alias'].casefold() == source['alias'].casefold():
                        raise ValueError('This source alias is already used.')
                source['transport'] = body.get('transport', 'http')
                source['url'] = ''
                source['command'] = []
                source['cwd'] = ''
                if source['transport'] == 'http':
                    source['url'] = endpoint(text(body.get('url', ''), 'MCP endpoint', 2000, True))
                elif source['transport'] == 'stdio':
                    command = body.get('command')
                    if not isinstance(command, list) or not command or len(command) > 40 or any(not isinstance(v, str) or not v or len(v) > 4000 for v in command):
                        raise ValueError('Enter the local service command as a JSON array of arguments.')
                    source['command'] = command
                    source['cwd'] = text(body.get('cwd', ''), 'service folder', 2000)
                    if source['cwd'] and not Path(source['cwd']).is_absolute():
                        raise ValueError('The service folder must be an absolute path.')
                else:
                    raise ValueError('Choose Streamable HTTP or a local stdio service.')
                timeout = body.get('timeout', 30)
                if type(timeout) is not int or not 1 <= timeout <= 300:
                    raise ValueError('Choose a timeout between 1 and 300 seconds.')
                source['timeout'] = timeout
                roles = body.get('roles', [])
                valid_roles = {'admin', 'user', *[r['id'] for r in self.access.read().get('roles', [])]}
                if not isinstance(roles, list) or any(not isinstance(r, str) or r not in valid_roles for r in roles):
                    raise ValueError('Choose existing roles for this source.')
                source['roles'] = sorted(set(roles))
                for key in ('enabled', 'allow_model_use'):
                    if type(body.get(key, False)) is not bool:
                        raise ValueError('Choose the source access switches.')
                    source[key] = body.get(key, False)
                secret = body.get('token', '')
                if not isinstance(secret, str) or len(secret) > 16000 or '\n' in secret or '\r' in secret:
                    raise ValueError('Enter a service bearer token without line breaks.')
                if secret:
                    if not re.fullmatch(r'[A-Za-z0-9._~+/-]+=*', secret):
                        raise ValueError('Enter a bearer token using its original token characters, without quotes or spaces.')
                    source['secret'] = self.protector(secret)
                if body.get('clear_token') or source['transport'] == 'stdio':
                    source['secret'] = ''
                connection_changed = any(source.get(k) != old.get(k) for k in ('transport', 'url', 'command', 'cwd', 'secret'))
                if connection_changed:
                    source['catalog'] = []
                catalog = {t['name']: t for t in source['catalog']}
                tools = body.get('tools', [])
                if not isinstance(tools, list):
                    raise ValueError('Choose discovered reporting tools.')
                source['tools'] = []
                for item in tools:
                    if not isinstance(item, dict) or item.get('name') not in catalog or item.get('kind') not in ('schema', 'query'):
                        raise ValueError('Discover tools for this connection before selecting capabilities.')
                    mapping = item.get('mapping') or {}
                    if not isinstance(mapping, dict) or any(k not in ('rows', 'columns', 'total', 'truncated') or not isinstance(v, str) or len(v) > 200 for k, v in mapping.items()):
                        raise ValueError('Use dotted JSON field paths for result mapping.')
                    tool = catalog[item['name']]
                    source['tools'].append(dict(name=tool['name'], kind=item['kind'], mapping=mapping,
                                                schema=tool['inputSchema'], description=tool.get('description', ''), fingerprint=fingerprint(tool)))
                if len({t['name'] for t in source['tools']}) != len(source['tools']):
                    raise ValueError('Select each tool once.')
                db.execute('INSERT OR REPLACE INTO records VALUES (?,?,?,?)', (sid, 'source', '', json.dumps(source)))
            db.execute('UPDATE metadata SET revision=? WHERE id=1', (uuid.uuid4().hex,))
        return self.listing(aid, True)

    def discovered(self, aid, sid, version, tools):
        self.actor(aid, 'manage_data_sources')
        if not isinstance(tools, list) or len(tools) > 500 or len(json.dumps(tools)) > 500000:
            raise ValueError('The service catalog is too large. Connect a smaller toolset.')
        if any(not isinstance(t, dict) or not isinstance(t.get('name'), str) or not t['name'] or
               not isinstance(t.get('inputSchema'), dict) for t in tools) or len({t['name'] for t in tools}) != len(tools):
            raise ValueError('The MCP service returned an invalid tool catalog.')
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT body FROM records WHERE id=? AND kind='source'", (sid,)).fetchone()
            source = json.loads(row[0]) if row else None
            if not source or source['version'] != version:
                raise ValueError('Source settings changed during discovery. Refresh and try again.')
            source['catalog'] = tools
            source['version'] = uuid.uuid4().hex
            # Discovery never grants newly announced tools or silently updates an authorized schema.
            db.execute('UPDATE records SET body=? WHERE id=?', (json.dumps(source), sid))
            db.execute('UPDATE metadata SET revision=? WHERE id=1', (uuid.uuid4().hex,))
        return self.listing(aid, True)

    def secret(self, source):
        return self.protector(source['secret'], decrypt=True) if source.get('secret') else ''

    def put_private(self, aid, kind, value):
        self.actor(aid, 'use_data_sources')
        self.source(aid, value['source_id'])
        value = dict(deepcopy(value), id=uuid.uuid4().hex, at=utc_now())
        with self.db() as db:
            db.execute('INSERT INTO records VALUES (?,?,?,?)', (value['id'], kind, aid, json.dumps(value)))
        return value

    def private(self, aid, kind, rid=None):
        user = self.actor(aid, 'use_data_sources')
        with self.db() as db:
            rows = db.execute('SELECT body FROM records WHERE kind=? AND owner=?' + (' AND id=?' if rid else '') + ' ORDER BY rowid DESC',
                              (kind, aid, rid) if rid else (kind, aid)).fetchall()
        output = []
        for row, in rows:
            value = json.loads(row)
            if rid and value['id'] != rid:
                continue
            try:
                source = self.raw(value['source_id'])
                if not self.allowed(user, source):
                    continue
            except ValueError:
                continue
            output.append(value)
        if rid:
            if not output:
                raise PermissionError('This report or result is unavailable to your account.')
            return output[0]
        return output

    def remove_private(self, aid, kind, rid):
        self.private(aid, kind, rid)
        with self.db() as db:
            db.execute('DELETE FROM records WHERE kind=? AND owner=? AND id=?', (kind, aid, rid))

    def save_report(self, aid, run_id, title, description='', layout=None):
        run = self.private(aid, 'run', run_id)
        layout = layout or {}
        if not isinstance(layout, dict) or any(k not in ('filter', 'sort_column', 'chart_column', 'ascending') for k in layout):
            raise ValueError('Choose table filter, ordering and chart presentation.')
        for key in ('filter', 'sort_column', 'chart_column'):
            if key in layout:
                text(layout[key], key, 1000)
        if 'ascending' in layout and type(layout['ascending']) is not bool:
            raise ValueError('Choose ascending or descending order.')
        return self.put_private(aid, 'report', dict(source_id=run['source_id'], tool=run['tool'], arguments=run['arguments'],
            source_version=run['source_version'], tool_fingerprint=run['tool_fingerprint'],
            title=text(title, 'report title', 150, True), description=text(description, 'report description', 4000),
            snapshot_id=run['id'], definition_version=1, layout=layout))
