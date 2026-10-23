"""The Atelier edition of Mission Control (v0.1, Michel 10-22): the public surfaces, without the Runtime.

server.py stays the private full edition, unchanged: it is built around the lightweight-mission manager and
hosts every pre-Atelier flow. This server composes only the public handlers, behind the same gateway guard
with gateway.PUBLIC_ROUTES, and imports nothing from aether.runtime (test_v01_public_boundary checks that).

Inputs:  the Bridge folder; optional registry, runtime-profiles, minds-folder, agent-folder and atelier paths.
Outputs: serve(...) -> a ThreadingHTTPServer on 127.0.0.1; main(argv) for `aether_bridge serve --edition atelier`.
         GET / is the Atelier page with the public navigation; GET /api/health answers {"bridge": "ready", ...}.
Never:   imports the Runtime, the mission manager or a private route; adds a gate. Anything undeclared is 404.
"""
from __future__ import annotations

import argparse
import json
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from aether.paths import BRIDGE_ROOT, REPOSITORY_ROOT, WEB_DIR
from aether.agent_editor.http import handle_agent_editor
from aether.atelier.atelier_http import handle_atelier, root_of as atelier_root
from aether.atelier.workplace import Workplace
from aether.atelier.workplace_http import handle_workplace
from aether.backends.model_settings_http import MISSION_SETTINGS_PATH, SettingsHTTPMixin
from aether.campaign.campaign_http import handle_public_campaign_state
from aether.credentials.redaction import redact
from aether.memory.agent_view import AgentDirectory, discover
from aether.memory.agent_view_http import handle_agent_view
from aether.mission_control import gateway
from aether.mission_control.tool_registry_http import handle_tool_registry
from aether.models.settings_store import SettingsStore
from aether.roadmaps.operators import reader as operator_reader, ensure_local_operator
from aether.roadmaps.project_folders import ProjectFolders, handle_project_folders
from aether.roadmaps.project_roadmaps import ProjectRoadmaps, handle_project_roadmaps
from aether.roadmaps.project_setup import ProjectSetup, handle_project_setup
from aether.roadmaps.projects import ProjectRegistry
from aether.toolbelt.registry import ToolRegistry

HOST = "127.0.0.1"
PORT = 8484
EDITION = "atelier"
NAV = ('<nav aria-label="Aether" style="font:14px system-ui,sans-serif;padding:.5rem 1rem;'
       'background:#0b1218;border-bottom:1px solid #39505e">'
       '<strong style="color:#e3edf3">Aether</strong> · <a href="/">Atelier</a> · '
       '<a href="/projects/places">Places</a> · <a href="/agents">Agents</a> · '
       '<a href="/projects/campaign">Commission state</a> · <a href="/projects">Projects</a> · '
       '<a href="/projects/tools">Tools</a> · '
       '<a href="/settings/models-context/">Models</a> · <a href="/agent-editor/">Agent Editor</a></nav>')


def projects_page(state: dict) -> bytes:
    """The tracker's projects and the folder projects, each with its tracker link (read-only listing)."""
    from html import escape
    from urllib.parse import quote
    rows = []
    for project in state["registry"].list_projects():
        pid = str(project.get("id", ""))
        rows.append(f'<li><a href="/projects/roadmaps?project_id={quote(pid, safe="")}">{escape(project.get("name") or pid)}</a>'
                    f' <span class="muted">roadmaps</span></li>')
    folders = state["project_folders"].list()
    for project in folders.get("projects", []):
        pid = str(project.get("id", ""))
        rows.append(f'<li><a href="/project-folders/{quote(pid, safe="")}/tracker/">{escape(project.get("name") or pid)}</a>'
                    f' <span class="muted">folder project</span></li>')
    body = ("<ul>" + "".join(rows) + "</ul>") if rows else '<p class="muted">No projects yet.</p>'
    page = ('<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,'
            'initial-scale=1"><title>Projects · Aether</title><style>:root{color-scheme:dark;font-family:system-ui,sans-serif;'
            'background:#101820;color:#e3edf3}body{margin:0}main{max-width:900px;margin:1.5rem auto;padding:0 1rem}'
            'a{color:#a9ddd4}.muted{color:#adc0cb;font-size:.85rem}li{margin:.5rem 0}</style></head><body>' + NAV +
            '<main><h1>Projects</h1>' + body + '<p><a href="/projects/new">Set up a project</a></p></main></body></html>')
    return page.encode("utf-8")


def home_page() -> bytes:
    """The Atelier page itself, with the public navigation above it (the page file is left as it is)."""
    page = (WEB_DIR / "atelier.html").read_text(encoding="utf-8")
    return page.replace("<body>", "<body>" + NAV, 1).encode("utf-8")


class InstallationAgents(AgentDirectory):
    """Agents in the minds folder and beside the homes this machine's place hosts, read again on every listing."""

    def __init__(self, workplace: Workplace, minds_folder=None, extra_roots=()):
        super().__init__([])
        self.workplace, self.minds_folder, self.extra_roots = workplace, minds_folder, list(extra_roots)

    def current_roots(self) -> list:
        found = []
        if self.minds_folder:
            found.append(("Minds", Path(self.minds_folder)))
        config = self.workplace.config() or {}
        for home in config.get("homes") or []:
            found.append(("This place", Path(home).parent))
        if config.get("share"):
            found.append(("Workplace", Path(config["share"]) / "minds"))
        found.extend(self.extra_roots)
        unique, seen = [], set()
        for label, folder in found:
            key = str(Path(folder).resolve()).casefold()
            if key not in seen:
                seen.add(key)
                unique.append((label, folder))
        return unique

    def homes(self) -> list:
        return discover(self.current_roots())


def _minds_folder():
    try:
        from aether.memory.session import minds_folder
        return minds_folder()
    except Exception:    # an unreadable installation setting hides that folder; the page still works
        return None


class AtelierHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def server_close(self):
        workplace = getattr(self, "state", {}).get("workplace")
        if workplace is not None and not workplace.close():
            raise RuntimeError("The place is finishing its current turn. Wait for it before closing Mission Control.")
        super().server_close()


def make_handler(state: dict):
    class Handler(SettingsHTTPMixin, BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):
            return

        def read_json(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(length).decode("utf-8")) if length else {}

        def send_json(self, data: object, status: int = 200) -> None:
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            linger = self.dispose_request_body()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            if linger:
                self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
            if linger:
                self.linger_close()

        def send_text(self, body: str, content_type: str = "text/html; charset=utf-8", status: int = 200) -> None:
            data = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def not_found(self):
            self.send_json({"error": "not found"}, 404)

        def _dispatch(self, method: str) -> None:
            if gateway.refuse(self, method, gateway.PUBLIC_ROUTES):
                return
            try:
                if handle_agent_editor(self, method, state):
                    return
                if handle_workplace(self, method, state):
                    return
                if handle_atelier(self, method, state):
                    return
                if handle_agent_view(self, method, state.get("agent_directory")):
                    return
                if handle_tool_registry(self, method, state.get("tool_registry")):
                    return
                if handle_public_campaign_state(self, method, atelier_root(state)):
                    return
                if handle_project_folders(self, method, state.get("project_folders")):
                    return
                if handle_project_roadmaps(self, method, state.get("project_roadmaps")):
                    return
                if handle_project_setup(self, method, state["project_setup"]):
                    return
                if self.handle_settings(method, state["model_settings"], prefix=MISSION_SETTINGS_PATH, embedded=True):
                    return
                path = urlparse(self.path).path
                if method == "GET" and path == "/":
                    self.settings_send(home_page(), html=True)
                elif method == "GET" and path == "/projects":
                    self.settings_send(projects_page(state), html=True)
                elif method == "GET" and path == "/api/health":
                    self.send_json({"bridge": "ready", "edition": EDITION, "instance_id": state["instance_id"],
                                    "started_at": state["started_at"]})
                else:
                    self.not_found()
            except Exception as exc:     # noqa: BLE001 - answer with the error, never drop the request
                self.send_json({"error": redact(f"{type(exc).__name__}: {exc}")}, 500)

        def do_GET(self):
            self._dispatch("GET")

        def do_POST(self):
            self._dispatch("POST")

        def do_PUT(self):
            self._dispatch("PUT")

        def do_DELETE(self):
            self._dispatch("DELETE")

    return Handler


def serve(bridge_dir: str | Path = BRIDGE_ROOT, *, port: int = PORT, registry_path=None, runtime_profiles_path=None,
          minds_folder=None, agent_roots=(), project_folders_root=None, atelier_root_path=None,
          project_setup_options=None) -> AtelierHTTPServer:
    bridge_path = Path(bridge_dir).resolve()
    registry = ProjectRegistry.load_or_migrate(bridge_path.parent, registry_path=registry_path)
    ensure_local_operator(registry)
    control = registry.registry_path.parent / ".aether_control"
    workplace = Workplace(control / "workplace" / "connection.json")
    profiles = Path(runtime_profiles_path or bridge_path / "runtime_profiles.json")
    state = {
        "edition": EDITION,
        "workplace": workplace,
        "registry": registry,
        "project_setup": ProjectSetup(registry, **(project_setup_options or {})),
        "project_roadmaps": ProjectRoadmaps(registry),
        "project_folders": ProjectFolders(project_folders_root or REPOSITORY_ROOT / "Projects"),
        "model_settings": SettingsStore(profiles, operator_registry_path=registry.registry_path,
                                        operators=operator_reader(registry.registry_path)),
        "tool_registry": ToolRegistry(control / "tool_registry.json"),   # the default path for the installation
        "agent_directory": InstallationAgents(workplace, minds_folder or _minds_folder(), agent_roots),
        "instance_id": uuid.uuid4().hex,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "operation_lock": threading.Lock(),
    }
    if atelier_root_path is not None:
        state["atelier_root"] = str(atelier_root_path)
    httpd = AtelierHTTPServer((HOST, port), make_handler(state))
    httpd.registry = registry
    httpd.state = state
    return httpd


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="aether_bridge serve --edition atelier")
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--runtime-profiles", default=None, metavar="PATH",
                    help="Models & Context registry (default: Bridge/runtime_profiles.json).")
    ap.add_argument("--minds", default=None, metavar="PATH",
                    help="Folder of agent homes for the Agents page (default: the installation's minds folder).")
    args, _ignored = ap.parse_known_args(argv)   # the full edition's model flags are accepted and unused here
    httpd = serve(BRIDGE_ROOT, port=args.port, runtime_profiles_path=args.runtime_profiles, minds_folder=args.minds)
    print(f"Aether Mission Control (Atelier edition) serving http://{HOST}:{args.port}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
