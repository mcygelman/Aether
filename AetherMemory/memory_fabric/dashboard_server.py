"""Local operator viewer. Each refresh opens a fresh read-only snapshot."""
from __future__ import annotations

import json
import html
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from .dashboard import render_html, STYLE, _note_link
from .engine import MemoryEngine
from .errors import MemoryFabricError


def make_server(path, *, port=0, session_id=None, name='welcome'):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.headers.get('Host', '').lower() not in (
                    f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'):
                self.send_error(403, 'Use the localhost viewer address')
                return
            parsed = urlparse(self.path)
            route, params = parsed.path, parse_qs(parsed.query)
            if route not in ('/', '/dashboard.json', '/note', '/catalog', '/history'):
                self.send_error(404)
                return
            try:
                with MemoryEngine.open(path, read_only=True) as engine:
                    if route == '/history':
                        from .lifecycle import HISTORY_SQL
                        found = engine.query(HISTORY_SQL, offset=int(params.get('offset', ['0'])[0]), limit=50)
                        content = '<!doctype html><html lang="en"><meta charset="utf-8"><title>Session history</title><style>' + STYLE + '</style>'
                        content += '<p><a href="/">Welcome Dashboard</a></p><h1>Session history</h1><p>Newest first. Missing closure information is advisory.</p>'
                        for row in found['rows']:
                            event = dict(zip(found['columns'], row))
                            label = f"{event['occurred_at']} — {event['kind']} — {event['session_id']}"
                            content += '<details><summary>' + html.escape(label) + '</summary><pre>' + \
                                html.escape(json.dumps(event, ensure_ascii=False, indent=2)) + '</pre></details>'
                        if found['next_offset'] is not None:
                            content += '<a href="/history?offset=' + str(found['next_offset']) + '">Next page</a>'
                        content += '</html>'
                    elif route == '/catalog':
                        kind = params.get('type', ['projects'])[0]
                        offset = int(params.get('offset', ['0'])[0])
                        found = engine.workspace('list', dict(record_type=kind, offset=offset, limit=50))
                        from .foundations_schema import FACETS
                        key = FACETS[kind][0]
                        content = '<!doctype html><html lang="en"><meta charset="utf-8"><title>Memory catalog</title><style>' + STYLE + '</style>'
                        content += '<p><a href="/">Welcome Dashboard</a></p><h1>' + html.escape(kind.title()) + '</h1><ul>'
                        content += ''.join('<li>' + _note_link(r[key], r['title']) + '</li>' for r in found['records']) + '</ul>'
                        content += '<p>' + str(found['total']) + ' records</p>'
                        if found['next_offset'] is not None:
                            content += '<a href="/catalog?type=' + kind + '&offset=' + str(found['next_offset']) + '">Next page</a>'
                        content += '</html>'
                    elif route == '/note':
                        nid = params.get('id', [''])[0]
                        if params.get('revision'):
                            note = engine.maintain(action='reconstruct', id=nid, at_revision=int(params['revision'][0]))['note']
                        else:
                            note = engine.note(nid)
                        content = '<!doctype html><html lang="en"><meta charset="utf-8"><title>Memory note</title><style>' + STYLE + '</style>'
                        content += '<p><a href="/">Welcome Dashboard</a></p><h1>' + html.escape(note['title']) + '</h1><article class="prose">'
                        content += html.escape(note['body']) + '</article></html>'
                    else:
                        result = engine.dashboard(name, session_id=params.get('session_id', [session_id])[0])
                        content = json.dumps(result, ensure_ascii=False) if route.endswith('.json') else render_html(result, refresh_url='/')
                body = content.encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type', 'application/json; charset=utf-8' if route.endswith('.json') else 'text/html; charset=utf-8')
                self.send_header('Cache-Control', 'no-store')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except MemoryFabricError as exc:
                self.send_error(404 if 'unknown note' in str(exc).lower() else 400,
                                'Requested memory was not found' if 'unknown note' in str(exc).lower() else 'Invalid memory request')
            except (KeyError, ValueError, TypeError):
                self.send_error(400, 'Invalid dashboard request')
            except Exception as exc:
                self.send_error(503, 'Dashboard unavailable: ' + type(exc).__name__)

        def log_message(self, *_):
            pass
    return ThreadingHTTPServer(('127.0.0.1', port), Handler)


def main(args):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('cartridge')
    parser.add_argument('--port', type=int, default=0)
    parser.add_argument('--session-id')
    parser.add_argument('--name', default='welcome')
    options = parser.parse_args(args)
    server = make_server(options.cartridge, port=options.port, session_id=options.session_id, name=options.name)
    print(f'Welcome Dashboard: http://127.0.0.1:{server.server_port}/', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
