"""The Roadmap Tracker on its own, without Mission Control (local only).

Run from the Bridge folder:  python -m aether.roadmaps.tracker [--data PATH] [--port N] [--no-browser]
By default it edits the canonical tracker, Projects/Aether/roadmaps.json.

This replaces the copy that lived in RoadMaps/Roadmap Tracker/ (now RoadMaps_Defunct/):
the standalone and Mission Control surfaces share one store, one HTTP handler and one page.
"""
from __future__ import annotations

import argparse
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

try:
    from .tracker_http import handle_tracker
    from .tracker_store import RoadmapStore
except ImportError:                     # loaded by file path, with this folder on sys.path
    from tracker_http import handle_tracker
    from tracker_store import RoadmapStore

ROOT = Path(__file__).resolve().parent


def _repository_root() -> Path:
    for parent in ROOT.parents:
        if (parent / "Projects" / "Aether" / "roadmaps.json").is_file():
            return parent
    return ROOT


DATA_FILE = _repository_root() / "Projects" / "Aether" / "roadmaps.json"


class TrackerServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, *, data_file=DATA_FILE, scope=None):
        super().__init__(address, TrackerHandler)
        self.store = RoadmapStore(data_file)
        self.scope = scope or ("Aether program roadmaps" if Path(data_file).resolve() == DATA_FILE.resolve()
                               else "Selected project roadmap store")


class TrackerHandler(BaseHTTPRequestHandler):
    server: TrackerServer

    def log_message(self, format_string: str, *args: object) -> None:
        print(f"[{self.log_date_time_string()}] {format_string % args}")

    def _handle(self, method):
        if not handle_tracker(self, method, store=self.server.store, scope=self.server.scope):
            self.send_error(HTTPStatus.NOT_FOUND)

    def do_GET(self):
        self._handle("GET")

    def do_PUT(self):
        self._handle("PUT")


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the local Roadmap Tracker")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--data", type=Path, default=DATA_FILE, help="Canonical store shared with Mission Control")
    parser.add_argument("--scope-label", help="Visible data-scope label")
    return parser.parse_args(argv)


def main(argv=None) -> None:
    args = parse_args(argv)
    if args.host not in {"127.0.0.1", "localhost"}:
        raise SystemExit("For safety, the tracker only binds to this computer (127.0.0.1).")
    server = TrackerServer(("127.0.0.1", args.port), data_file=args.data, scope=args.scope_label)
    url = f"http://127.0.0.1:{server.server_port}/"
    print(f"Roadmap Tracker is running at {url}")
    print("Press Ctrl+C to stop it.")
    if not args.no_browser:
        threading.Timer(0.5, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping Roadmap Tracker.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
