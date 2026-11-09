"""The Atelier edition of Mission Control (v0.1): the public surfaces, without the Runtime.

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
import socket
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from aether_bridge import __version__

from aether.paths import BRIDGE_ROOT, REPOSITORY_ROOT, WEB_DIR, LIGHTWEIGHT_ROOT
from aether.agent_editor.http import handle_agent_editor
from aether.atelier.atelier_http import handle_atelier, root_of as atelier_root
from aether.atelier.workplace import Workplace
from aether.atelier.agent_registry import AgentRegistry
from aether.atelier.campaign_roster import CampaignRoster
from aether.atelier.campaign_roster_http import handle_campaign_roster
from aether.atelier.campaigns import Campaigns, handle_campaigns
from aether.atelier.model_chat import ModelChats, handle_model_chat
from aether.atelier.personal_chat_store import PersonalChatStore
from aether.atelier.chat_scratchpad import ChatScratchpad
from aether.atelier.workspace_store import WorkspaceStore, handle_workspace
from aether.atelier.specialist_http import handle_specialists
from aether.data_sources.http import handle as handle_data_sources
from aether.data_sources.service import SourceService, bound as bound_data_sources
from aether.atelier.operator_access import OperatorAccess
from aether.atelier.operator_access_http import guard as operator_guard
from aether.atelier.agent_registry_http import handle_agent_registry
from aether.atelier.workplace_http import handle_workplace
from aether.atelier.readiness import readiness
from aether.backends.model_settings_http import MISSION_SETTINGS_PATH, SettingsHTTPMixin
from aether.backends.gui_assets import ASSETS
from aether.campaign.campaign_http import handle_public_campaign_state
from aether.keys.redaction import redact
from aether.memory.agent_view import AgentDirectory, discover
from aether.memory.agent_view_http import handle_agent_view
from aether.mission_control import gateway
from aether.mission_control.maintenance_http import handle as handle_maintenance
from aether.mission_control.tasker_http import handle as handle_tasker
from aether.mission_control.tasker_projects import TaskerProjects
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
NAV = ('<nav data-local-nav aria-label="Aether" style="font:14px system-ui,sans-serif;padding:.5rem 1rem;'
       'background:#0b1218;border-bottom:1px solid #39505e">'
       '<strong style="color:#e3edf3">Aether</strong> · <a href="/">Atelier</a> · '
       '<a href="/projects/places">Places</a> · <a href="/agents">Agents</a> · <a href="/projects/roster">Campaign roster</a> · '
       '<a href="/projects/campaign">Commission state</a> · '
       '<a href="/settings/api-keys/">API keys</a> · <a href="/settings/models-context/">Model connections</a></nav>')


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


# The Atelier edition's own pages: model-only Chat at the root, Missions, a project's page, and Join Network. The full
# edition keeps atelier.html and places.html; the code's names (commission, place, workplace) stay underneath.
PAGES = {"/": "chat.html", "/chat": "chat.html", "/missions": "campaigns.html",
         "/projects/tasker": "tasker.html",
         "/login": "operator_login.html", "/workspace": "workspace.html", "/roadmaps": "roadmaps.html", "/projects/atelier": "project.html", "/network": "network.html"}
MOVED = {"/projects/places": "/network"}


def storage_locations(state):
    """Return location metadata without exposing stored contents or moving data."""
    resolve = lambda value: str(Path(value).resolve()) if value else None
    rows = []
    def add(label, purpose, paths, href=None):
        rows.append(dict(label=label, purpose=purpose,
                         paths=[resolve(p) for p in paths if p], href=href))
    add('Data root', 'Local installation data. Selected at installation; relocation is not available here.', [LIGHTWEIGHT_ROOT])
    add('Workspace records', 'Creations, Teach Frames, guides and workspace settings.', [state['workspace_store'].path])
    add('Chat history', 'Private conversation stores. Access to files is chosen separately in each Chat.', [state['model_chats'].root], '/')
    add('Operator accounts', 'The local account directory. Manage accounts in Human Staff.', [state['operator_access'].path], '/workspace?view=humans')
    registry = state['agent_registry']
    add('Agent registry', 'Local availability and registration records.', [registry.path], '/agents?from=settings')
    add('Default minds folder', 'Default home location for this installation’s agents.', [registry.default_folder], '/agents?from=settings')
    add('Agent folder locations', 'Folders where Agents looks for available identities.', registry.folders(), '/agents?from=settings')
    config = state['workplace'].config()
    add('Network settings', 'Local configuration for this computer’s network hosting.', [state['workplace'].path], '/network')
    add('Network folder', 'Configured shared workplace. An unset location is not a network membership.', [config.get('share')], '/network')
    add('Project records', 'The current location used for mission and project records.', [atelier_root(state)], '/missions')
    add('Model connections file', 'Saved models and server settings. Edit connections on Models.', [state['model_settings'].path], '/settings/models-context/')
    if config.get('profiles'):
        add('Network host model file', 'Model connections selected in this computer’s network options.', [config['profiles']], '/network')
    return dict(locations=rows)


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

    def legacy_homes(self):
        homes = [h.home for h in discover(self.current_roots())]
        # A participant record is sufficient for availability; a memory cartridge is optional.
        for _, folder in self.current_roots():
            if folder.is_dir():
                homes.extend(h for h in folder.iterdir() if h.is_dir() and (h / 'participant.json').is_file())
        homes.extend(Path(h) for h in (self.workplace.config() or {}).get('homes', []))
        return list(dict.fromkeys(homes))

    def homes(self) -> list:
        from aether.memory.agent_view import AgentHome, _locate
        homes = discover(self.current_roots())
        known = {h.home.resolve() for h in homes}
        registry = getattr(self.workplace, 'agent_registry', None)
        if registry:
            with registry.lock:
                entries = registry._saved()
            keys = {h.key for h in homes}
            for row in entries.values():
                folder = Path(row['home'])
                if folder.resolve() not in known:
                    key = folder.name
                    while key in keys:
                        key += '-registered'
                    cart, problem = _locate(folder)
                    homes.append(AgentHome(key, folder, 'Registered', cart, problem))
                    known.add(folder.resolve()); keys.add(key)
        return homes


def _minds_folder():
    try:
        from aether.memory.session import minds_folder
        return minds_folder()
    except Exception:    # an unreadable installation setting hides that folder; the page still works
        return None


class AtelierHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def server_bind(self):
        # Windows SO_REUSEADDR permits two listeners on one address; never share an app's port.
        if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
            self.allow_reuse_address = False
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def server_close(self):
        tasker = getattr(self, 'state', {}).get('tasker_projects')
        if tasker is not None:
            tasker.close()
        workplace = getattr(self, "state", {}).get("workplace")
        if workplace is not None and not workplace.close():
            raise RuntimeError("The place is finishing its current turn. Wait for it before closing Mission Control.")
        sources = getattr(self, 'state', {}).get('data_sources')
        if sources is not None:
            sources.stop_all()
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
            from aether.keys.runtime_keys import as_operator
            from aether.atelier.operator_access_http import token_of
            actor = state['operator_access'].user(token_of(self)) if state.get('operator_access') else None
            with as_operator(actor['id'] if actor else None), bound_data_sources(state.get('data_sources')):
                workplace = state.get('workplace')
                if actor and workplace is not None and workplace.pending == actor['id']:
                    workplace.resume(actor['id'])     # the account that joined is back: its hosting resumes (O2)
                return self._dispatch_scoped(method)

        def _dispatch_scoped(self, method: str) -> None:
            try:
                if gateway.refuse(self, method, gateway.PUBLIC_ROUTES):
                    return
                path = urlparse(self.path).path
                if operator_guard(self, method, state):
                    return
                if method == "GET" and path == "/api/workspace/storage":
                    self.send_json(storage_locations(state))
                    return
                if handle_maintenance(self, method, state):
                    return
                if method == "GET" and path == "/workspace" and (parse_qs(urlparse(self.path).query).get("view") or ["roadmaps"])[0] == "roadmaps":
                    self.send_response(302)
                    self.send_header("Location", "/roadmaps")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if method == "GET" and path.startswith("/workspace-assets/"):
                    name = path.removeprefix("/workspace-assets/")
                    if name not in ASSETS:
                        self.not_found()
                    else:
                        self.send_text((WEB_DIR / "workspace_assets" / name).read_text(encoding="utf-8"), ASSETS[name])
                    return
                workspace = state.get("workspace_store")
                if (method == "POST" and workspace and workspace.halted() and path in {
                        "/api/atelier/wake", "/api/places/start", "/api/places/start-here",
                        "/api/places/join-network", "/api/places/prepare-library"}):
                    self.send_json({"error": "New work is stopped. Enable requests in Settings → Emergency Halt first."}, 409)
                    return
                if method == "GET" and path in PAGES:
                    # The workspace previews creations; a sprint's page draws its SVG outputs (both as blob images).
                    self.settings_send((WEB_DIR / PAGES[path]).read_bytes(), html=True,
                                       image_blobs=path in ('/workspace', '/projects/tasker'))
                    return
                if method == "GET" and path in MOVED:
                    self.send_response(302)
                    self.send_header("Location", MOVED[path])
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if handle_workspace(self, method, state):
                    return
                if handle_tasker(self, method, state):
                    return
                if handle_model_chat(self, method, state):
                    return
                if handle_data_sources(self, method, state):
                    return
                if handle_campaigns(self, method, state):
                    return
                if handle_agent_editor(self, method, state):
                    return
                if handle_workplace(self, method, state):
                    return
                if handle_atelier(self, method, state):
                    return
                if handle_campaign_roster(self, method, state):
                    return
                if handle_agent_registry(self, method, state):
                    return
                if handle_specialists(self, method, state):
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
                if method == "GET" and path == "/projects":
                    self.settings_send(projects_page(state), html=True)
                elif method == "GET" and path == "/api/readiness":
                    commission = (parse_qs(urlparse(self.path).query).get("commission") or [None])[0]
                    result = readiness(state, atelier_root(state), commission)
                    if state.get('runtime_keys'):
                        runtime = state['runtime_keys']
                        result['keys'] = dict(supported=True, configured=runtime.vault.path.exists(), assigned=len(runtime.visible(self.operator['id'])), suspended=state['operator_access'].read()['suspended'])
                    self.send_json(result)
                elif method == "GET" and path == "/api/health":
                    self.send_json({"bridge": "ready", "edition": EDITION, "version": __version__,
                                    "release_stage": "prototype", "instance_id": state["instance_id"],
                                    "started_at": state["started_at"]})
                else:
                    self.not_found()
            except ConnectionError:
                # Navigating away can close a pending read. Do not write a second
                # response to the disconnected browser or emit a server traceback.
                self.close_connection = True
            except Exception as exc:     # noqa: BLE001 - report application errors to a connected client
                try:
                    self.send_json({"error": redact(f"{type(exc).__name__}: {exc}")}, 500)
                except ConnectionError:
                    self.close_connection = True

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
          project_setup_options=None, tasker_data_root=None) -> AtelierHTTPServer:
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
    directory = state['agent_directory']
    def campaign_roots():
        default = Path(state.get('atelier_root') or LIGHTWEIGHT_ROOT / 'atelier')
        roots = [default] if default.exists() else []
        config = workplace.config()
        if config.get('share'):
            roots.append(Path(config['share']))
        return roots
    state['agent_registry'] = AgentRegistry(control / 'agents.json', legacy_homes=directory.legacy_homes,
                                            campaign_roots=campaign_roots, default_folder=directory.minds_folder)
    workplace.agent_registry = state['agent_registry']
    state['campaign_roster'] = CampaignRoster(lambda: atelier_root(state), state['agent_registry'], profiles,
                                              homes=lambda: workplace.config().get('homes', []))
    workplace.campaign_roster = state['campaign_roster']
    state['campaigns'] = Campaigns(lambda: atelier_root(state), folders=lambda: state['project_folders'].roadmap_rows(),
                                   create_roadmap=lambda name, description: state['project_folders'].create_roadmap(name, description))
    state['workspace_store'] = WorkspaceStore(control / 'workspace')
    state['tasker_projects'] = TaskerProjects(tasker_data_root or LIGHTWEIGHT_ROOT, profiles,
                                             halted=state['workspace_store'].halted, roadmaps=state['project_folders'])
    workplace.halt_check = state['workspace_store'].halted
    from aether.overseer.chat import Conversations
    def lead_running(agent_id):
        return any(row.get('overseer') == agent_id and row.get('execution') in ('running', 'stopping')
                   for row in state['tasker_projects'].listing())
    state['agent_conversations'] = Conversations(state['tasker_projects'].root, state['agent_registry'], lead_running)
    state['operator_access'] = OperatorAccess(control / 'operators')
    state['data_sources'] = SourceService(control / 'data_sources', state['operator_access'], halt=state['workspace_store'].halted)
    from aether.keys.vault import get_vault
    from aether.keys.runtime_keys import RuntimeKeys
    state['runtime_keys'] = RuntimeKeys(get_vault(bridge_path), state['operator_access'])
    state['model_settings'].operators = lambda: [dict(id=str(uuid.UUID(u['id'])), display_name=u['name'])
        for u in state['operator_access'].listing()['accounts'] if u['active']]
    def chat_protected_roots():
        return [control, bridge_path, *state['agent_registry'].folders(),
                *(workplace.config().get('homes') or [])]
    state['model_chats'] = ModelChats(control / 'model_chats', state['model_settings'], bridge=bridge_path,
                                     halt_check=state['workspace_store'].halted, protected_roots=chat_protected_roots)
    chat_stores = {}
    chat_lock = threading.RLock()
    def connection_allowed(profile, aid):
        current = state['operator_access'].actor(aid)
        if not current: return False
        ref = profile.get('credential_ref')
        if not ref: return True
        if ref.get('kind') != 'vault': return current['role'] == 'admin'
        return state['runtime_keys'].allowed(aid,ref.get('name'))

    def agent_talk(aid):
        # Michel, 2026-11-05: only an Admin talks with a heavyweight in Chat; the role is read again at every use.
        current = state['operator_access'].actor(aid)
        if not current or current.get('role') != 'admin':
            return None
        return state['agent_conversations'], current.get('name') or current.get('username') or 'the Admin'

    def operator_chats(actor):
        with chat_lock:
            if actor['id'] not in chat_stores:
                root = state['operator_access'].chat_root(actor, control / 'model_chats')
                chat_stores[actor['id']] = ModelChats(root, state['model_settings'], bridge=bridge_path,
                                                     data_sources=state['data_sources'], operator_id=actor['id'],
                                                     cartridge=PersonalChatStore(root,actor['id']),
                                                     connection_allowed=lambda p,aid=actor['id']: connection_allowed(p,aid),
                                                     agents=lambda aid=actor['id']: agent_talk(aid),
                                                     scratchpad=ChatScratchpad(control / 'chat_scratchpads',actor['id']),
                                                     halt_check=state['workspace_store'].halted, protected_roots=chat_protected_roots)
            return chat_stores[actor['id']]
    state['operator_chats'] = operator_chats
    state['all_chat_stores'] = lambda: [state['model_chats'], *list(chat_stores.values())]
    httpd = AtelierHTTPServer((HOST, port), make_handler(state))
    httpd.registry = registry
    httpd.state = state
    workplace.resume()      # hosting resumes now, or when the account that joined next signs in (O2)
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
