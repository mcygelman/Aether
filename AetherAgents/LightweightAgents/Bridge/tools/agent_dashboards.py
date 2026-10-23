"""Standalone agent viewer: game-style agent cards, Welcome Dashboards and memory, read-only.

The same handler Mission Control will mount (aether.memory.agent_view_http). Until then:

    py -3.10 tools/agent_dashboards.py --root path/to/public-homes
    py -3.10 tools/agent_dashboards.py --demo

--root names a folder whose subfolders are agent homes (repeat it; LABEL=PATH sets the card's
source label). --demo writes three disposable example minds under build/agent_dashboards_demo
and shows them; it never touches a resident home. The server listens on 127.0.0.1 only,
answers GET only, and refuses requests that do not name this server as their Host.
"""
from __future__ import annotations

import argparse
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BRIDGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BRIDGE))

from aether.memory.agent_view import AgentDirectory  # noqa: E402
from aether.memory.agent_view_http import handle_agent_view, PAGE  # noqa: E402


def make_server(directory, port=0):
    class Handler(BaseHTTPRequestHandler):
        def settings_send(self, value, status=200, *, html=False):
            body = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', 'text/html; charset=utf-8' if html else 'application/json; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _local(self):
            port = self.server.server_port
            return self.headers.get('Host') in (f'127.0.0.1:{port}', f'localhost:{port}')

        def do_GET(self):
            if not self._local():
                self.settings_send({'error': 'Open this viewer from this computer only.'}, 403)
                return
            if self.path in ('/', ''):
                self.send_response(302)
                self.send_header('Location', PAGE)
                self.end_headers()
                return
            if not handle_agent_view(self, 'GET', directory):
                self.settings_send({'error': 'Not found'}, 404)

        def do_POST(self):
            self.settings_send({'error': 'This viewer is read-only.'}, 405)

        def log_message(self, *_):
            pass
    return ThreadingHTTPServer(('127.0.0.1', port), Handler)


def build_demo(folder: Path) -> Path:
    """Three fictional minds that show the card and dashboard states. Disposable."""
    from aether.memory.fabric import memory_fabric
    from aether.memory.foundations import begin, publish
    mf = memory_fabric()
    folder = Path(folder)
    if folder.exists():
        return folder
    folder.mkdir(parents=True)

    def mind(name, agent_id):
        home = folder / name
        (home / 'memory').mkdir(parents=True)
        (home / 'memory/.cartridge.json').write_text(json.dumps({'path': 'mind.sqlite'}), encoding='utf-8')
        return home, mf.MemoryEngine.create(str(home / 'memory/mind.sqlite'), agent_id, name=name)

    def close(engine, home, sid, cid, carry):
        state = home / f'{cid}.closure.json'
        state.write_text(json.dumps({'status': 'CLOSED', 'closure_id': cid}), encoding='utf-8')
        publish(engine, sid, cid, state, carry_text=carry)

    # Wren: ranked, its own avatar and title, a project, one plan done and one waiting.
    home, wren = mind('Wren', 'wren-demo')
    with wren:
        wren.set_scan_line('Keeps the long thread of a project')
        wren.set_avatar("<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'>"
                        "<rect width='100' height='100' rx='18' fill='#1f3a44'/>"
                        "<circle cx='50' cy='52' r='30' fill='none' stroke='#9fe6da' stroke-width='5'/>"
                        "<path d='M28 58 Q50 22 72 58' fill='none' stroke='#e8c260' stroke-width='6' stroke-linecap='round'/>"
                        "<circle cx='50' cy='64' r='6' fill='#e8c260'/></svg>",
                        'A golden arc over a teal ring, like a thread held up to the light', True)
        for n in range(7):
            wren.record_outcome('code review', f'demo-review-{n}', 'success', 2.0, verified_by='Linus')
        wren.add_review('Linus', 'reviewer', 'Catches the edge cases others skip, and says why.', rating=4.5)
        lesson = wren.remember('Ledger totals need the rounding rule', 'Round once, at the end; per-line rounding drifts.',
                               'standing_lesson', ['ledger', 'rounding'])
        first = begin(wren, 'demo-1', source_system='demo')
        project = wren.workspace('create', {'record_type': 'projects', 'title': 'Harbor Ledger',
                                            'body': 'Monthly statements for the harbor office.', 'links': [(lesson, 'context')],
                                            'operation_id': 'project'})['id']
        wren.workspace('project_work', {'project_id': project, 'operation_id': 'focus'})
        done = wren.workspace('create', {'record_type': 'scaffolds', 'title': 'Reconcile March statements',
                                         'body': 'Match each statement line to the bank export; list differences.',
                                         'links': [(project, 'context')], 'operation_id': 'plan-1'})['id']
        run = wren.workspace('scaffold_start', {'scaffold_id': done, 'operation_id': 'run-1'})['event']['run_id']
        wren.workspace('scaffold_end', {'run_id': run, 'outcome': 'completed', 'operation_id': 'run-1-end'})
        wren.workspace('create', {'record_type': 'scaffolds', 'title': 'Draft the April operator guide',
                                  'body': 'Write the guide after the April statements close.',
                                  'links': [(project, 'context')], 'operation_id': 'plan-2'})
        close(wren, home, first, 'demo-close-1',
              'Next self: March is reconciled. Start April from the rounding lesson, then draft the guide.')
        begin(wren, 'demo-1', source_system='demo', new_work=True)

    # Atlas: no avatar or title yet, unranked, an earlier session that never closed.
    home, atlas = mind('Atlas', 'atlas-demo')
    with atlas:
        atlas.remember('Shared folder paths differ by machine', 'Use the path each machine sees.', 'project_fact', ['places'])
        begin(atlas, 'lost-run', source_system='studio')
        good = begin(atlas, 'good-run', source_system='studio')
        close(atlas, home, good, 'demo-close-2', 'Next self: the share works from both machines.')
        begin(atlas, 'today', source_system='studio')

    # Juniper: a fresh mind, only the supplied Guide and TeachFrame.
    mind('Juniper', 'juniper-demo')[1].close()
    return folder


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--root', action='append', default=[], help='folder of agent homes, or LABEL=PATH')
    parser.add_argument('--demo', action='store_true', help='also show three disposable example minds')
    parser.add_argument('--demo-folder', default=str(BRIDGE / 'build' / 'agent_dashboards_demo'))
    parser.add_argument('--port', type=int, default=8491)
    args = parser.parse_args(argv)
    roots = []
    for item in args.root:
        label, _, path = item.partition('=') if '=' in item else (Path(item).name, '', item)
        roots.append((label, Path(path)))
    if args.demo:
        roots.append(('Demo', build_demo(Path(args.demo_folder))))
    if not roots:
        parser.error('give at least one --root, or --demo')
    server = make_server(AgentDirectory(roots), args.port)
    print(f'Agent viewer: http://127.0.0.1:{server.server_port}{PAGE}  (Ctrl+C stops it)', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
