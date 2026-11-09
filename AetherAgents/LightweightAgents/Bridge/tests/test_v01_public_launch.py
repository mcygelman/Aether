"""v0.1: the real launch command starts and stops the Atelier edition in a fresh folder.

The folder holds only the files public_surface.json declares, laid out as in the repository: no Runtime, no
runtime_profiles.json, no private defaults, no installed packages. The launcher is the one the Windows
"Start Mission Control" file runs.
"""
import json
import os
import shutil
import socket
import subprocess
import sys
from urllib.error import URLError
from urllib.request import Request, urlopen

import pytest

import v01_public_support as support
from aether.operations import launcher


def signed_in(base):
    """Create the first account (it becomes the Admin) and return an opener carrying its session."""
    account = dict(username="launch-admin", name="Launch Admin", password="launch-test-pass-1",
                   confirmation="launch-test-pass-1")
    request = Request(base + "/api/access/register", data=json.dumps(account).encode("utf-8"),
                      headers={"Content-Type": "application/json", "Origin": base})
    with urlopen(request, timeout=10) as response:
        cookie = response.headers["Set-Cookie"].split(";", 1)[0]
    return lambda path: urlopen(Request(base + path, headers={"Cookie": cookie}), timeout=10)


@pytest.mark.parametrize("argv, environment, edition", [
    ([], {}, "atelier"),
    ([], {"AETHER_EDITION": "full"}, "full"),
    (["--governed"], {"AETHER_EDITION": "atelier"}, "full"),       # a full-edition option implies it
    (["--heavyweight-runs-root", "runs"], {}, "full"),
    ([], {"AETHER_HEAVYWEIGHT_RUNS_ROOT": "runs"}, "atelier"),   # the variable serves the full edition only
    (["--edition", "full"], {"AETHER_HEAVYWEIGHT_RUNS_ROOT": "runs"}, "full"),
])
def test_the_launcher_picks_the_edition(monkeypatch, tmp_path, argv, environment, edition):
    for name in ("AETHER_EDITION", "AETHER_HEAVYWEIGHT_RUNS_ROOT"):
        monkeypatch.delenv(name, raising=False)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    commands = []
    monkeypatch.setattr(launcher, "fetch_json", lambda url, timeout=5.0: None)
    monkeypatch.setattr(launcher, "wait_ready", lambda url, seconds=20.0: None)
    monkeypatch.setattr(launcher.subprocess, "Popen", lambda cmd, **kw: commands.append(cmd))
    monkeypatch.setattr(launcher, "paths", lambda: (tmp_path, tmp_path))
    launcher.main(["start", "--no-browser"] + argv)
    command = commands[0]
    assert command[command.index("--edition") + 1] == edition
    assert ("--heavyweight-runs-root" in command) == (edition == "full" and "runs" in argv + list(environment.values()))


@pytest.mark.parametrize("argv, environment", [
    (["--edition", "atelier", "--governed"], {}),
    ([], {"AETHER_EDITION": "studio"}),
])
def test_the_launcher_refuses_a_contradictory_edition(monkeypatch, argv, environment):
    monkeypatch.delenv("AETHER_EDITION", raising=False)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    with pytest.raises(SystemExit):
        launcher.main(["start", "--no-browser"] + argv)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def fresh_copy(root):
    surface = support.surface()
    for rel in surface["files"]:
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(support.REPOSITORY / rel, target)
    return surface, root / surface["launch"]["cwd"]


def test_the_launch_command_starts_and_stops_the_atelier_edition_in_a_fresh_folder(tmp_path):
    surface, bridge = fresh_copy(tmp_path / "fresh")
    assert not (bridge / "aether" / "runtime").exists()
    assert not (bridge / "runtime_profiles.json").exists() and not (bridge / "minder_defaults.json").exists()
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("AETHER_") and k not in ("DEEPSEEK_API_KEY", "PYTHONPATH", "PYTHONHOME")}
    env["PYTHONIOENCODING"] = "utf-8"
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    launcher = [sys.executable] + surface["launch"]["argv"][1:3]      # python -m aether_bridge.launcher
    command = [sys.executable] + surface["launch"]["argv"][1:] + [
        "--no-browser", "--port", str(port), "--endpoint", "http://127.0.0.1:9/v1"]   # no model server needed
    control = bridge.parent / ".aether_control" / "launcher"
    start = subprocess.run(command, cwd=bridge, env=env, capture_output=True, text=True, encoding="utf-8", timeout=120)
    try:
        log = (control / "mission_control.log").read_text(encoding="utf-8", errors="replace") \
            if (control / "mission_control.log").is_file() else ""
        assert start.returncode == 0, start.stdout + start.stderr + log
        with urlopen(base + "/api/health", timeout=10) as response:
            health = json.load(response)
        assert health["edition"] == "atelier"
        assert json.loads((control / "instance.json").read_text(encoding="utf-8"))["instance_id"] == health["instance_id"]
        with urlopen(base + "/", timeout=10) as response:       # signed out: the sign-in page
            assert response.url.startswith(base + "/login") and b'account-form' in response.read()
        open_page = signed_in(base)
        with open_page("/") as response:                  # signed in: Chat, with the shared navigation
            home = response.read()
            assert b'<title>Chat' in home and b'atelier-nav.js' in home
        with urlopen(base + '/settings/models-context/atelier-nav.js', timeout=10) as response:
            assert b'/agents' in response.read()
        with open_page("/api/agents") as response:
            assert "agents" in json.load(response)
        with open_page("/projects/atelier") as response:
            assert response.status == 200
    finally:
        stop = subprocess.run(launcher + ["stop"], cwd=bridge, env=env, capture_output=True, text=True,
                              encoding="utf-8", timeout=60)
    assert stop.returncode == 0, stop.stdout + stop.stderr
    assert not (control / "instance.json").exists()
    try:
        urlopen(base + "/api/health", timeout=2)
        still_up = True
    except (URLError, OSError):
        still_up = False
    assert not still_up
    assert "Traceback" not in (control / "mission_control.log").read_text(encoding="utf-8", errors="replace")
