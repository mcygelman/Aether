"""Local Models & Context screen. Fixed registry per process, no generic file API."""
import argparse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from aether.models.settings_store import SettingsStore
from aether.roadmaps.operators import reader as operator_reader
from aether.backends.model_settings_http import SettingsHTTPMixin


def bind(store, port=0):
    class Handler(SettingsHTTPMixin, BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.handle_settings('GET', store)

        def do_POST(self):
            self.handle_settings('POST', store)

    server = ThreadingHTTPServer(('127.0.0.1',port),Handler)
    server.daemon_threads = True
    return server


def main():
    parser=argparse.ArgumentParser(description='Aether Models & Context settings')
    parser.add_argument('--registry',default=None,help='Existing runtime profile file; fixed for this settings session')
    parser.add_argument('--no-browser',action='store_true')
    args=parser.parse_args()
    store=SettingsStore(args.registry) if args.registry else SettingsStore()
    store.operators=operator_reader(store.operator_registry_path)   # attribution for saves (item 9)
    server=bind(store)
    url=f'http://127.0.0.1:{server.server_port}/'
    print(f'Models & Context: {url}\nProfile file: {store.path}',flush=True)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__=='__main__':
    main()
