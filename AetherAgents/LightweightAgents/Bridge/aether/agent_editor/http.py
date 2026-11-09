"""Own fixed editor assets and stateless template authoring routes.

Never open agent homes or expose full-edition lifecycle/publication in Atelier.
"""
from pathlib import Path
import socket
import time
from urllib.parse import urlparse
from aether.backends.gui_assets import PUBLIC_EDITOR_ASSETS

from . import service

WEB = Path(__file__).parent / 'web'
ASSETS = {
    '': ('index.html', 'text/html; charset=utf-8'),
    'css/studio.css': ('css/studio.css', 'text/css; charset=utf-8'),
    'js/studio.js': ('js/studio.js', 'text/javascript; charset=utf-8'),
    'js/bridge.js': ('js/bridge.js', 'text/javascript; charset=utf-8'),
    'lifecycle.html': ('lifecycle.html', 'text/html; charset=utf-8'),
    'js/lifecycle.js': ('js/lifecycle.js', 'text/javascript; charset=utf-8'),
    'config/studio-config.json': ('config/studio-config.json', 'application/json'),
    **{f'fixtures/{name}.json': (f'fixtures/{name}.json', 'application/json') for name in
       ('sample_lw_template', 'sample_ul_template', 'empty_traits_template', 'custom_traits_template')},
}


def _send(handler, body, content_type, *, report=None):
    handler.send_response(200)
    handler.send_header('Content-Type', content_type)
    handler.send_header('Content-Length', str(len(body)))
    handler.send_header('Cache-Control', 'no-store')
    handler.send_header('X-Content-Type-Options', 'nosniff')
    handler.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
    if report:
        handler.send_header('Content-Disposition', f'attachment; filename="agent-template-{report["draft_sha256"][:12]}.zip"')
        handler.send_header('X-Draft-SHA256', report['draft_sha256'])
    handler.end_headers()
    handler.wfile.write(body)


def _body(handler, limit=service.MAX_DRAFT_BYTES):
    lengths = handler.headers.get_all('Content-Length') or []
    if handler.headers.get('Transfer-Encoding') or len(lengths) != 1:
        raise service.DraftError('Provide one Content-Length and no transfer encoding.')
    length = lengths[0]
    if not length.isascii() or not length.isdigit() or len(length) > 8:
        raise service.DraftError('Invalid draft length.')
    length = int(length)
    if not 0 < length <= limit:
        raise service.DraftError(f'Request must be between 1 and {limit} bytes.')
    if handler.headers.get('Content-Type', '').split(';')[0].strip() != 'application/json':
        raise service.DraftError('Send the draft as application/json.')
    previous = handler.connection.gettimeout()
    deadline = time.monotonic() + 5
    try:
        chunks = []
        while length:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise service.DraftError('Draft upload timed out.')
            handler.connection.settimeout(remaining)
            chunk = handler.rfile.read1(min(length, 65536))
            if not chunk:
                raise service.DraftError('Draft upload was incomplete.')
            chunks.append(chunk)
            length -= len(chunk)
        return b''.join(chunks)
    except (socket.timeout, OSError) as exc:
        raise service.DraftError('Draft upload was interrupted.') from exc
    finally:
        handler.connection.settimeout(previous)


def handle_agent_editor(handler, method, state=None):
    path = urlparse(handler.path).path
    public = (state or {}).get('edition') == 'atelier'
    if public and path.startswith('/api/agent-editor/') and path not in (
            '/api/agent-editor/validate', '/api/agent-editor/export'):
        handler.send_json({'error': 'Future Feature: this editor action is not available in this edition.'}, 404)
        return True
    if path in ('/api/agent-editor/consent-policy', '/api/agent-editor/consent-offer'):
        from .consent_http import handle_offer
        return handle_offer(handler, method, path, _body)
    if path in ('/api/agent-editor/projects', '/api/agent-editor/publication-preview',
                '/api/agent-editor/publication-confirm', '/api/agent-editor/publication-inspect'):
        from .publication_http import handle_publication
        return handle_publication(handler, method, path, state or {}, _body)
    if path == '/agent-editor':
        if method == 'GET':
            handler.send_response(302)
            handler.send_header('Location', '/agent-editor/')
            handler.send_header('Content-Length', '0')
            handler.end_headers()
            return True
        return False
    if method == 'GET' and path.startswith('/agent-editor/'):
        asset = (PUBLIC_EDITOR_ASSETS if public else ASSETS).get(path[len('/agent-editor/'):])
        if asset is None:
            handler.send_json({'error': 'Editor asset not found.'}, 404)
        else:
            try:
                _send(handler, (WEB/asset[0]).read_bytes(), asset[1])
            except OSError:
                handler.send_json({'error': 'Editor resources are unavailable in this installation.'}, 503)
        return True
    if method != 'POST' or path not in ('/api/agent-editor/validate', '/api/agent-editor/export'):
        return False
    try:
        raw = _body(handler)
        if path.endswith('/validate'):
            report, _ = service.validate(raw)
            handler.send_json(report, 200 if report['status']=='TEMPLATE_VERIFIED' else 422)
        else:
            report, archive = service.export(raw)
            if archive is None:
                handler.send_json(report, 422)
            else:
                _send(handler, archive, 'application/zip', report=report)
    except service.DraftError as exc:
        handler.send_json({'error': str(exc)}, 400)
    return True
