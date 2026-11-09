"""Agent views over HTTP (MOD-12): the operator's agent cards, Welcome Dashboards and memory.

Read-only GET routes, answered the way Mission Control's modules answer theirs: one
handle_agent_view(handler, method, directory) call that returns False for any other path.
The standalone viewer (tools/agent_dashboards.py) mounts the same function today.

Mission Control already mounts these handlers with its installation AgentDirectory.
The public page is /agents/insights; ?view=memory selects the memory tab. Staff → AI
and Dashboards expose direct entry links. /agents itself belongs to Availability.
The same read-only backend powers both the standalone prototype and these pages.

Inputs:  the handler (it must offer settings_send(value, status, html=) and .path), the method,
         and an agent_view.AgentDirectory (None answers 503 with the reason).
Outputs: PAGE (the viewer page) and, under API: the agent list, one card, sessions, a Welcome
         Dashboard, one note, and the Memory Fabric viewer page.
Never:   answers a method other than GET, writes, or opens a session; a model-authored path or
         reference is never followed.
"""
from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from aether.paths import WEB_DIR

OWNER = 'memory.agent_view_http'
PAGE = '/agents'
API = '/api/agents'
PATHS = {PAGE, '/agents/insights', API, API + '/card', API + '/sessions', API + '/dashboard', API + '/note', API + '/memory', API + '/history'}


def _one(query, name, required=True):
    values = query.get(name, [])
    if not values or values[0] == '':
        if required:
            raise ValueError(f'{name} is required')
        return None
    return values[0]


# The shared page policy plus inline images: an agent's avatar arrives as a data: URI it chose. Nothing remote.
PAGE_POLICY = ("default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
               "img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'")


def _send_page(handler, body: bytes) -> None:
    handler.send_response(200)
    handler.send_header('Content-Type', 'text/html; charset=utf-8')
    handler.send_header('Content-Length', str(len(body)))
    handler.send_header('Cache-Control', 'no-store')
    handler.send_header('X-Content-Type-Options', 'nosniff')
    handler.send_header('Content-Security-Policy', PAGE_POLICY)
    handler.send_header('Connection', 'close')
    handler.close_connection = True
    handler.end_headers()
    handler.wfile.write(body)


def handle_agent_view(handler, method, directory) -> bool:
    from .agent_view import AgentViewError, UnknownAgent, LATEST
    parsed = urlparse(handler.path)
    if parsed.path not in PATHS:
        return False
    if method != 'GET':
        handler.settings_send({'error': 'Agent views are read-only; use GET.'}, 405)
        return True
    if parsed.path in (PAGE, '/agents/insights'):
        _send_page(handler, (WEB_DIR / 'agents.html').read_bytes())
        return True
    if directory is None:
        handler.settings_send({'error': 'No agent folders are configured for this server.'}, 503)
        return True
    query = parse_qs(parsed.query)
    try:
        if parsed.path == API:
            from datetime import datetime, timezone
            value = {'agents': directory.agents(),
                     'retrieved_at': datetime.now(timezone.utc).isoformat(timespec='seconds')}
        else:
            key = _one(query, 'agent')
            if parsed.path == API + '/card':
                value = directory.card(key)
            elif parsed.path == API + '/sessions':
                value = {'sessions': directory.sessions(key)}
            elif parsed.path == API + '/dashboard':
                value = directory.dashboard(key, _one(query, 'session', required=False) or LATEST)
            elif parsed.path == API + '/history':
                before = _one(query, 'before', required=False)
                value = directory.history(key, int(before) if before is not None else None)
            elif parsed.path == API + '/note':
                revision = _one(query, 'revision', required=False)
                value = directory.note(key, _one(query, 'id'), int(revision) if revision is not None else None)
            else:
                page = directory.memory_page(key, include_proprietary=_one(query, 'proprietary', False) == '1')
                handler.settings_send(page.encode('utf-8'), html=True)
                return True
        handler.settings_send(value)
    except UnknownAgent as exc:
        handler.settings_send({'error': str(exc)}, 404)
    except (AgentViewError, ValueError) as exc:
        handler.settings_send({'error': str(exc)}, 400 if isinstance(exc, ValueError) and
                              not isinstance(exc, AgentViewError) else 409)
    except OSError as exc:
        handler.settings_send({'error': f'Agent view unavailable: {type(exc).__name__}'}, 503)
    return True
