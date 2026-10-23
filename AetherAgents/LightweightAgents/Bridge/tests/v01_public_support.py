"""Support for the v0.1 public-boundary tests: a Runtime blocker, a use recorder, and the public scenario.

A child Python process gets a sitecustomize that makes every `aether.runtime` import fail. With
AETHER_V01_RECORD set, it also records each `aether`, `aether_bridge` and `memory_fabric` module imported
and each repository file opened, so the public surface is measured from use, not guessed.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

BRIDGE = Path(__file__).resolve().parents[1]
REPOSITORY = BRIDGE.parents[2]
SURFACE = REPOSITORY / "AetherPublicFacing" / "Release" / "public_surface.json"

SITECUSTOMIZE = textwrap.dedent('''
    import atexit, json, os, sys

    class _BlockRuntime:
        def find_spec(self, name, path=None, target=None):
            if (name == "aether.runtime" or name.startswith("aether.runtime.") or name in
                    ("aether.mission_control.server", "aether.mission_control.studio_server")):
                raise ImportError("BLOCKED v0.1 public boundary: " + name)
            return None

    sys.meta_path.insert(0, _BlockRuntime())
    _record = os.environ.get("AETHER_V01_RECORD")
    if _record:
        _root = os.path.normcase(os.path.abspath(os.environ["AETHER_V01_REPOSITORY"]))
        _opened = set()

        def _hook(event, args):
            if event == "open" and args and isinstance(args[0], (str, bytes, os.PathLike)):
                try:
                    path = os.path.normcase(os.path.abspath(os.fsdecode(args[0])))
                except Exception:
                    return
                if path.startswith(_root):
                    _opened.add(path)

        sys.addaudithook(_hook)

        def _write():
            modules = sorted(m for m in list(sys.modules)
                             if m.split(".")[0] in ("aether", "aether_bridge", "memory_fabric"))
            files = sorted({os.path.normcase(os.path.abspath(getattr(sys.modules[m], "__file__", "") or ""))
                            for m in modules if getattr(sys.modules.get(m), "__file__", None)})
            with open(_record, "a", encoding="utf-8") as out:
                out.write(json.dumps({"modules": modules, "files": files, "opened": sorted(_opened)}) + "\\n")

        atexit.register(_write)
''')


def blocker_dir(tmp: Path) -> Path:
    folder = Path(tmp) / "v01_blocker"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "sitecustomize.py").write_text(SITECUSTOMIZE, encoding="utf-8")
    return folder


def run_blocked(tmp: Path, code: str, *, record: Path | None = None, cwd: Path = BRIDGE, timeout: int = 300,
                extra_env: dict | None = None) -> subprocess.CompletedProcess:
    """Run `code` in a child Python with the Runtime blocked (and optionally recorded)."""
    env = {k: v for k, v in os.environ.items() if k not in ("AETHER_API_KEY", "DEEPSEEK_API_KEY", "PYTHONHOME")}
    env["PYTHONPATH"] = os.pathsep.join([str(blocker_dir(tmp)), str(BRIDGE)])
    env["PYTHONIOENCODING"] = "utf-8"
    if record is not None:
        env["AETHER_V01_RECORD"] = str(record)
        env["AETHER_V01_REPOSITORY"] = str(REPOSITORY)
    env.update(extra_env or {})
    return subprocess.run([sys.executable, "-X", "utf8", "-c", code], cwd=str(cwd), env=env, capture_output=True,
                          text=True, encoding="utf-8", timeout=timeout)



def import_failures(tmp: Path, modules, *, extra_path=()) -> dict:
    """Import each module in a child Python with the Runtime blocked; return {module: error} and loaded Runtime."""
    code = "\n".join([
        "import importlib, json, sys",
        f"sys.path[:0] = {[str(p) for p in extra_path] + [str(REPOSITORY / 'AetherMemory')]!r}",
        f"names = {list(modules)!r}",
        "failed = {}",
        "for name in names:",
        "    try:",
        "        importlib.import_module(name)",
        "    except BaseException as exc:",
        "        failed[name] = f'{type(exc).__name__}: {exc}'[:300]",
        "loaded = sorted(m for m in sys.modules if m == 'aether.runtime' or m.startswith('aether.runtime.'))",
        "print(json.dumps({'failed': failed, 'runtime_modules': loaded}))",
    ]) + "\n"
    done = run_blocked(tmp, code)
    assert done.returncode == 0, done.stderr[-4000:]
    return json.loads(done.stdout.strip().splitlines()[-1])


def run_scenario(tmp: Path, record: Path | None = None) -> subprocess.CompletedProcess:
    """Run the public scenario against a real Atelier-edition server in `tmp`, Runtime blocked."""
    code = f"import sys\nsys.argv = ['scenario', {str(Path(tmp) / 'work')!r}]\n" + SCENARIO
    return run_blocked(tmp, code, record=record)

# The public actions: a real Atelier-edition server on a disposable layout, every public surface touched,
# private and invalid requests answered plainly, and no Runtime module imported at the end.
SCENARIO = textwrap.dedent('''
    import json, sys, tempfile, threading
    from http.client import HTTPConnection
    from pathlib import Path
    sys.path.insert(0, str(Path.cwd() / "tests"))
    from aether.mission_control import atelier_server

    work = Path(sys.argv[1])
    (work / "Bridge").mkdir(parents=True, exist_ok=True)
    minds = work / "minds"
    import importlib.util
    spec = importlib.util.spec_from_file_location("agent_dashboards_tool", Path.cwd() / "tools" / "agent_dashboards.py")
    tool = importlib.util.module_from_spec(spec); spec.loader.exec_module(tool)
    tool.build_demo(minds)
    httpd = atelier_server.serve(work / "Bridge", port=0, registry_path=work / "projects.json",
                                 project_folders_root=work / "Projects", atelier_root_path=work / "atelier",
                                 minds_folder=minds, runtime_profiles_path=work / "runtime_profiles.json")
    thread = threading.Thread(target=httpd.serve_forever, daemon=True); thread.start()
    port = httpd.server_port
    origin = f"http://127.0.0.1:{port}"
    results = {}

    def call(method, path, body=None, host=None, headers=None):
        conn = HTTPConnection("127.0.0.1", port, timeout=60)
        h = {"Host": host or f"127.0.0.1:{port}"}
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            h.update({"Content-Type": "application/json", "Origin": origin})
        h.update(headers or {})
        conn.request(method, path, body=data, headers=h)
        response = conn.getresponse(); raw = response.read(); conn.close()
        results[f"{method} {path}" + (f" host={host}" if host else "") + (" xsite" if headers else "")] = response.status
        return response.status, raw

    status, raw = call("GET", "/")
    assert status == 200 and b'href="/agents"' in raw and b"Atelier" in raw
    assert json.loads(call("GET", "/api/health")[1])["edition"] == "atelier"
    for page in ("/projects/atelier", "/projects/places", "/agents", "/projects/campaign", "/projects/campaign-receipts",
                 "/projects", "/projects/tools", "/settings/models-context/", "/agent-editor/", "/projects/new"):
        assert call("GET", page)[0] == 200, page
    opened = json.loads(call("POST", "/api/atelier/open", {"text": "Write a one-line readme. Budget: 4 model calls.",
                                                           "request_kind": "project"})[1])
    cid = opened["commission_id"]
    assert call("GET", "/api/atelier/read?commission=" + cid)[0] == 200
    assert call("GET", "/api/campaign-state?id=" + cid)[0] == 200
    assert call("GET", "/api/campaign-receipts?id=" + cid)[0] == 200
    assert call("GET", "/api/campaign-state?id=no-such-commission")[0] == 404
    agents = json.loads(call("GET", "/api/agents")[1])["agents"]
    assert {a["key"] for a in agents} >= {"Wren", "Atlas", "Juniper"}
    assert call("GET", "/api/agents/dashboard?agent=Wren")[0] == 200
    assert call("GET", "/api/agents/memory?agent=Wren")[0] == 200
    assert call("GET", "/api/agents/dashboard?agent=Nobody")[0] == 404
    assert call("GET", "/api/tool-registry")[0] == 200
    assert call("GET", "/api/project-folders")[0] == 200
    assert call("POST", "/api/agent-editor/validate", {"not": "a draft"})[0] in (400, 422)
    for private in ("/projects/overseer", "/api/missions", "/projects/closure-dialogue", "/projects/campaign-journey",
                    "/api/campaign-candidate", "/projects/minder", "/projects/staffing", "/chartered-project",
                    "/api/agent-editor/consent-policy", "/api/heavyweight-runs", "/projects/plan-ruling"):
        status, raw = call("GET", private)
        assert status == 404 and b"full edition" in raw, private
    assert b"full edition" not in call("GET", "/api/no-such-route")[1]
    assert call("GET", "/api/health", host="evil.example:80")[0] == 403
    assert call("POST", "/api/atelier/open", {"text": "x"}, headers={"Origin": "http://evil.example"})[0] == 403
    httpd.shutdown(); thread.join(10); httpd.server_close()
    loaded = sorted(m for m in sys.modules if m == "aether.runtime" or m.startswith("aether.runtime."))
    print(json.dumps({"results": results, "runtime_modules": loaded}))
''')


def surface() -> dict:
    return json.loads(SURFACE.read_text(encoding="utf-8"))
