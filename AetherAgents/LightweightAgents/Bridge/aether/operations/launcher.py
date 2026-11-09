"""Idempotent Windows-friendly launcher for Aether Mission Control."""
from __future__ import annotations
from aether.paths import BRIDGE_ROOT

import argparse
import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path
from aether.backends.legacy_endpoints import legacy_ip_endpoint


def fetch_json(url: str, timeout: float = 5.0) -> dict | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError, urllib.error.URLError):
        return None


def wait_ready(url: str, seconds: float = 20.0) -> dict | None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        health = fetch_json(url)
        if health and health.get("bridge") == "ready":
            return health
        time.sleep(0.2)
    return None


def paths() -> tuple[Path, Path]:
    bridge = BRIDGE_ROOT
    control = bridge.parent / ".aether_control" / "launcher"
    control.mkdir(parents=True, exist_ok=True)
    return bridge, control


def start(args: argparse.Namespace) -> int:
    bridge, control = paths()
    base = f"http://127.0.0.1:{args.port}"
    existing = fetch_json(base + "/api/health")
    if existing:
        print(f"Mission Control is already ready ({existing.get('instance_id', 'unknown instance')}).")
        if not args.no_browser:
            webbrowser.open(base)
        return 0
    models = fetch_json(args.endpoint.rstrip("/") + "/models")
    lm_note = "LM Studio is reachable." if models is not None else "LM Studio is not reachable yet; Mission Control will show recovery guidance."
    log_path = control / "mission_control.log"
    try:
        log = log_path.open("ab", buffering=0)
    except OSError as exc:
        print(f"Cannot open the Mission Control log {log_path}: {exc}. Another Mission Control may "
              "still be running; nothing was started.")
        return 1
    edition = getattr(args, "edition", "atelier")
    cmd = [sys.executable, "-m", "aether_bridge", "serve", "--edition", edition, "--model", args.model,
           "--model-window", str(args.model_window), "--endpoint", args.endpoint, "--port", str(args.port)]
    if args.heavyweight_runs_root:
        cmd.extend(["--heavyweight-runs-root", args.heavyweight_runs_root])
    if getattr(args, "governed", False):
        cmd.append("--governed")
        if getattr(args, "governed_config", None):
            # The server runs with the Bridge as its working directory: pass an absolute path.
            cmd.extend(["--governed-config", str(Path(args.governed_config).resolve())])
    flags = 0
    kwargs: dict = {"cwd": str(bridge), "stdout": log, "stderr": subprocess.STDOUT, "stdin": subprocess.DEVNULL}
    if os.name == "nt":
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
        kwargs["creationflags"] = flags
    else:
        kwargs["start_new_session"] = True
    process = subprocess.Popen(cmd, **kwargs)
    health = wait_ready(base + "/api/health", args.ready_timeout)
    if not health:
        print(f"Mission Control did not become ready. Nothing was hidden; inspect {log_path}")
        return 1
    (control / "instance.json").write_text(json.dumps({"pid": process.pid, "instance_id": health["instance_id"],
                                                        "url": base, "log": str(log_path)}, indent=2) + "\n",
                                                   encoding="utf-8")
    print(f"Mission Control ready. {lm_note} Log: {log_path}")
    if not args.no_browser:
        webbrowser.open(base)
    return 0


STOP_WAIT_SECONDS = 15.0   # how long a stopped instance may keep answering before stop says so


def _process_gone(exc: OSError) -> bool:
    """The recorded process no longer exists. Windows reports a dead PID as WinError 87."""
    return isinstance(exc, ProcessLookupError) or getattr(exc, "winerror", None) == 87


def _wait_down(url: str, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while fetch_json(url, timeout=1.0):
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.2)
    return True


def stop(args: argparse.Namespace) -> int:
    """0 only when the launcher-owned instance is no longer answering; otherwise say why."""
    _, control = paths()
    record_path = control / "instance.json"
    if not record_path.is_file():
        print("No launcher-owned Mission Control instance is recorded.")
        return 0
    record = json.loads(record_path.read_text(encoding="utf-8"))
    url = record.get("url", "")
    health = fetch_json(url + "/api/health")
    if health and health.get("instance_id") != record.get("instance_id"):
        print("The port belongs to a different Bridge instance; it was not stopped.")
        return 2
    try:
        os.kill(int(record["pid"]), signal.SIGTERM)
        signalled = True
    except OSError as exc:
        if not _process_gone(exc):
            print(f"Could not stop the launcher-owned process {record['pid']}: {exc}")
            return 1
        if health:
            print(f"Mission Control still answers at {url}, but its recorded process {record['pid']} "
                  "no longer exists, so this launcher cannot stop it. Close it from its own window "
                  "or Task Manager, then start again.")
            return 1
        signalled = False
    if not _wait_down(url + "/api/health", getattr(args, "stop_wait", STOP_WAIT_SECONDS)):
        print(f"Mission Control still answers at {url} after the stop signal; the record is kept.")
        return 1
    record_path.unlink(missing_ok=True)
    print("Launcher-owned Mission Control stopped." if signalled else
          "Launcher-owned Mission Control was no longer running; its stale record was cleared.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="aether_bridge.launcher")
    sub = parser.add_subparsers(dest="action", required=True)
    for name in ("start", "restart"):
        command = sub.add_parser(name)
        command.add_argument("--model", default=os.environ.get("AETHER_MODEL", "qwen3.6-35b-a3b"))
        command.add_argument("--model-window", type=int, default=int(os.environ.get("AETHER_MODEL_WINDOW", "35580")))
        command.add_argument("--endpoint", default=os.environ.get("AETHER_ENDPOINT", legacy_ip_endpoint()))
        command.add_argument("--port", type=int, default=8484)
        command.add_argument("--ready-timeout", type=float, default=20.0)
        command.add_argument("--no-browser", action="store_true")
        command.add_argument("--edition", choices=("atelier", "full"), default=None,
                             help="atelier (default, or AETHER_EDITION): the v0.1 public surfaces; full: the private "
                                  "full edition (implied by --governed or --heavyweight-runs-root).")
        command.add_argument("--heavyweight-runs-root", default=None,
                             help="Full edition only (default: AETHER_HEAVYWEIGHT_RUNS_ROOT).")
        command.add_argument("--governed", action="store_true",
                             help="Start Mission Control with the governed campaign hosts composed (MVP-01 G1).")
        command.add_argument("--governed-config", default=None, metavar="PATH",
                             help="Governed services configuration for --governed "
                                  "(default: Bridge/governed_services.json; optional).")
    sub.add_parser("stop")
    args = parser.parse_args(argv)
    if getattr(args, "governed_config", None) and not args.governed:
        parser.error("--governed-config requires --governed")
    if args.action != "stop":
        full_only = args.governed or args.heavyweight_runs_root
        if args.edition is None:
            args.edition = "full" if full_only else os.environ.get("AETHER_EDITION", "atelier")
        if args.edition not in ("atelier", "full"):
            parser.error(f"AETHER_EDITION must be atelier or full, not {args.edition!r}")
        if args.edition == "atelier" and full_only:
            parser.error("--governed and --heavyweight-runs-root belong to the full edition (--edition full)")
        if args.edition == "full" and not args.heavyweight_runs_root:
            args.heavyweight_runs_root = os.environ.get("AETHER_HEAVYWEIGHT_RUNS_ROOT")
    if args.action == "stop":
        return stop(args)
    if args.action == "restart":
        stopped = stop(args)
        if stopped != 0:
            # A failed stop must never be followed by a start.
            print("Restart refused: the running instance was not stopped, so no second one was started.")
            return stopped
    return start(args)


if __name__ == "__main__":
    raise SystemExit(main())
