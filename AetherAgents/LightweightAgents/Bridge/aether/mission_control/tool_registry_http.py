"""Tool registry page (Bridge Overhaul M2, FR-03): see, withdraw, approve and undo tools from Mission Control.

The registry itself is aether.toolbelt.registry; this module only shows it and passes an
operator's decisions to it. Every change names who and why, and is journaled there.

Inputs:  GET  /projects/tools                     the page
         GET  /api/tool-registry                  every tool: built-ins and every record
         GET  /api/tool-registry/history?name=    journaled changes, newest first (all tools when no name)
         POST /api/tool-registry/approve          {"name", "by", "reason"}
         POST /api/tool-registry/withdraw         {"name", "by", "reason"}
         POST /api/tool-registry/undo             {"change_id", "by", "reason"}
Outputs: JSON, or the page. 400: a blank by or reason, or a malformed body; 403: not this
         server's Host, or (for a POST) not this server's own Origin; 409: the registry refused
         (its message), or no registry is configured.
Never:   changes a tool without who and why; accepts a POST that does not come from this
         server's own page; answers a path outside its family (returns False).
"""
from __future__ import annotations

import json
from urllib.parse import parse_qs, urlparse

from aether.config.journal import ConfigJournalError
from aether.paths import WEB_DIR
from aether.toolbelt.registry import APPROVED, BUILTIN_TOOLS, UNAPPROVED, WITHDRAWN, approval_token, evaluate

PAGE = '/projects/tools'
API = '/api/tool-registry'
PAGE_FILE = WEB_DIR / 'tool_registry.html'
MAX_BYTES = 64 * 1024
DIGEST_CHARS = 12
_PREFIX = 'tool:'


class _BadRequest(ValueError):
    pass


def _local(handler, method) -> bool:
    """Host must be this server; a POST must also carry this server's own Origin."""
    origin = f'http://127.0.0.1:{handler.server.server_port}'
    if handler.headers.get('Host') != origin.removeprefix('http://'):
        return False
    if handler.headers.get('Sec-Fetch-Site') == 'cross-site':
        return False
    sent = handler.headers.get('Origin')
    return sent == origin if method != 'GET' else sent in (None, origin)


def _schema(name, record):
    schema = (record or {}).get('schema')
    if isinstance(schema, dict) and schema.get('function', {}).get('name') == name:
        return schema
    return {'type': 'function', 'function': {'name': name}}


def _state_label(name, value, removed):
    """What a journaled value meant for this tool (removed means: back to its default)."""
    if removed or not isinstance(value, dict):
        return APPROVED if name in BUILTIN_TOOLS else 'unregistered'
    return evaluate(_schema(name, value), value,
                    policy=value.get('policy', 'tool-authority-1')).state


def _change_view(change):
    name = change.key[len(_PREFIX):] if change.key.startswith(_PREFIX) else change.key
    return dict(id=change.id, at=change.at, by=change.by, name=name, reason=change.reason,
                undoes=change.undoes,
                before=_state_label(name, change.old, not change.existed),
                after=_state_label(name, change.new, change.removed))


def list_tools(registry) -> dict:
    records = registry.records()
    last = {}
    for change in registry.history(None, limit=1_000_000):   # newest first
        last.setdefault(change.key, change)
    tools = []
    for name in sorted(BUILTIN_TOOLS | set(records)):
        record = records.get(name) or {}
        current = registry.state(_schema(name, record))
        pending, bound = record.get('pending_digest'), record.get('digest')
        change = last.get(_PREFIX + name)
        tools.append(dict(
            name=name, state=current.state, source=current.source,
            builtin=name in BUILTIN_TOOLS,
            definition=record.get('schema'), source_definition=record.get('source_definition'), approval_token=approval_token(record),
            digest=(pending or bound or '')[:DIGEST_CHARS] or None,
            schema_changed=bool(bound and (bound != pending or
                record.get('definition_digest') != record.get('pending_definition_digest') or
                record.get('source_definition_digest') != record.get('pending_source_definition_digest'))),
            last_change=None if change is None else dict(
                id=change.id, by=change.by, reason=change.reason, at=change.at, undoes=change.undoes)))
    return dict(tools=tools, states=[APPROVED, UNAPPROVED, WITHDRAWN])


def _history(registry, query) -> dict:
    params = parse_qs(query)
    name = (params.get('name') or [''])[0].strip() or None
    try:
        limit = int((params.get('limit') or ['50'])[0])
    except ValueError:
        raise _BadRequest('limit must be a whole number')
    limit = max(1, min(limit, 500))
    return dict(name=name, changes=[_change_view(c) for c in registry.history(name, limit=limit)])


def _body(handler) -> dict:
    if handler.headers.get('Content-Type', '').split(';')[0].strip() != 'application/json':
        raise _BadRequest('Send application/json')
    try:
        length = int(handler.headers.get('Content-Length', '0'))
    except ValueError:
        length = 0
    if not 0 < length <= MAX_BYTES:
        raise _BadRequest(f'Send a JSON body of at most {MAX_BYTES} bytes')
    try:
        body = json.loads(handler.rfile.read(length).decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _BadRequest(f'The body is not valid UTF-8 JSON: {exc}')
    if not isinstance(body, dict):
        raise _BadRequest('Send a JSON object')
    return body


def _drain(handler) -> None:
    """Read a refused request's body (bounded) before answering. Otherwise Windows may abort the
    connection while the client is still sending, and it sees a reset instead of the refusal.
    A handler with the shared disposition does this in settings_send, with a deadline (S4)."""
    if getattr(handler, 'dispose_request_body', None) is not None:
        return
    try:
        length = int(handler.headers.get('Content-Length', '0'))
    except ValueError:
        return
    if 0 < length <= MAX_BYTES:
        handler.rfile.read(length)


def _text(body, field, message) -> str:
    value = body.get(field)
    if not isinstance(value, str) or not value.strip():
        raise _BadRequest(message)
    return value.strip()


def _post(registry, action, body) -> dict:
    by = _text(body, 'by', 'Say who is making the change (your name)')
    reason = _text(body, 'reason', 'Say why the change is made; it is kept in the journal')
    if action == 'undo':
        change_id = body.get('change_id')
        if isinstance(change_id, str) and change_id.strip().isdigit():
            change_id = int(change_id)
        if isinstance(change_id, bool) or not isinstance(change_id, int):
            raise _BadRequest('Name the change to undo (change_id)')
        change = registry.undo(change_id, by=by, reason=reason, expected_token=body.get('approval_token', ''))
    else:
        name = _text(body, 'name', 'Name the tool (name)')
        if action == 'approve':
            change = registry.approve(name, by=by, reason=reason, expected_token=body.get('approval_token', ''))
        else:
            change = registry.withdraw(name, by=by, reason=reason)
    return dict(change=_change_view(change), **list_tools(registry))


def list_servers(registry, settings) -> dict:
    """Configured MCP servers (the `toolbelt.mcp_servers` setting) and the tools each has announced.
    Read-only: nothing here starts a server. Sources no longer configured are listed too."""
    configured = settings.get('toolbelt.mcp_servers') if settings is not None else []
    records = registry.records()
    by_source: dict = {}
    for name, record in sorted(records.items()):
        source = record.get('source', '')
        if source.startswith('mcp:'):
            by_source.setdefault(source, []).append(dict(name=name, state=registry.state(_schema(name, record)).state))
    servers = []
    for entry in configured or []:
        source = 'mcp:' + entry['name']
        servers.append(dict(name=entry['name'], source=source, command=entry['command'],
                            timeout_seconds=entry['timeout_seconds'], configured=True,
                            status='configured; not started here (starting configured servers needs Michel\'s go)',
                            tools=by_source.pop(source, [])))
    for source, tools in sorted(by_source.items()):
        servers.append(dict(name=source[4:], source=source, configured=False,
                            status='no longer configured; its tools stay in the registry with their history',
                            tools=tools))
    return dict(servers=servers)


def handle_tool_registry(handler, method, registry, settings=None) -> bool:
    """settings: the installation settings (aether.config.settings.InstallationSettings) for the
    servers view; None reads this installation's own."""
    parsed = urlparse(handler.path)
    path = parsed.path
    if path != PAGE and path != API and not path.startswith(API + '/'):
        return False
    try:
        if not _local(handler, method):
            _drain(handler)
            handler.settings_send({'error': 'Use the tool registry page on this Mission Control.'}, 403)
        elif path == PAGE:
            if method == 'GET':
                handler.settings_send(PAGE_FILE.read_bytes(), html=True)
            else:
                handler.settings_send({'error': 'Method unavailable'}, 405)
        elif registry is None:
            handler.settings_send({'error': 'The tool registry is not configured on this host.'}, 409)
        elif path == API:
            if method == 'GET':
                handler.settings_send(list_tools(registry))
            else:
                handler.settings_send({'error': 'Method unavailable'}, 405)
        elif path == API + '/servers':
            if method == 'GET':
                if settings is None:
                    from aether.config.settings import InstallationSettings
                    settings = InstallationSettings()
                handler.settings_send(list_servers(registry, settings))
            else:
                handler.settings_send({'error': 'Method unavailable'}, 405)
        elif path == API + '/history':
            if method == 'GET':
                handler.settings_send(_history(registry, parsed.query))
            else:
                handler.settings_send({'error': 'Method unavailable'}, 405)
        elif path in (API + '/approve', API + '/withdraw', API + '/undo'):
            if method == 'POST':
                handler.settings_send(_post(registry, path.rsplit('/', 1)[-1], _body(handler)))
            else:
                handler.settings_send({'error': 'Method unavailable'}, 405)
        else:
            handler.settings_send({'error': 'Unknown tool registry resource'}, 404)
    except _BadRequest as exc:
        handler.settings_send({'error': str(exc)}, 400)
    except ConfigJournalError as exc:   # ToolRegistryError included: the registry refused
        handler.settings_send({'error': str(exc)}, 409)
    except OSError as exc:
        handler.settings_send({'error': f'The tool registry could not be read or written: {exc}'}, 409)
    return True
