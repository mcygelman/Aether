"""S00073 launcher primitives stay idempotent and readable."""
from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aether_bridge.launcher import fetch_json, wait_ready  # noqa: E402
from aether_bridge.model import LMStudio  # noqa: E402


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        return

    def do_GET(self):
        body = json.dumps({"bridge": "ready", "instance_id": "fixture"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def test_health_probe_and_readiness_are_bounded():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}"
        assert fetch_json(url)["instance_id"] == "fixture"
        assert wait_ready(url, 1)["bridge"] == "ready"
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_unreachable_probe_returns_none_without_throwing():
    assert fetch_json("http://127.0.0.1:1", timeout=0.05) is None


def test_lmstudio_probe_uses_models_without_inference():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = LMStudio(endpoint=f"http://127.0.0.1:{server.server_address[1]}")
        assert client.probe(timeout_secs=0.5) is True
    finally:
        server.shutdown()
        thread.join(timeout=2)


# ---- stop and restart refuse honestly (Windows check, 2026-10-07) ----
import pytest  # noqa: E402
from aether.operations import launcher  # noqa: E402


class _Gone(OSError):
    winerror = 87          # how Windows reports a PID that no longer exists


class _Denied(OSError):
    winerror = 5


@pytest.fixture
def control(tmp_path, monkeypatch):
    monkeypatch.setattr(launcher, "paths", lambda: (tmp_path, tmp_path))
    record = {"pid": 4242, "instance_id": "mc-1", "url": "http://127.0.0.1:1", "log": "x"}
    (tmp_path / "instance.json").write_text(json.dumps(record), encoding="utf-8")
    return tmp_path


def args(**extra):
    return type("Args", (), {"stop_wait": 0.3, **extra})()


def kill_raising(exc):
    def kill(pid, sig):
        raise exc
    return kill


def test_restart_never_starts_after_a_failed_stop(monkeypatch):
    started = []
    monkeypatch.setattr(launcher, "stop", lambda a: 1)
    monkeypatch.setattr(launcher, "start", lambda a: started.append(a) or 0)
    assert launcher.main(["restart", "--no-browser"]) == 1
    assert started == []


def test_a_dead_pid_with_nothing_answering_clears_the_stale_record(control, monkeypatch):
    monkeypatch.setattr(launcher, "fetch_json", lambda url, timeout=5.0: None)
    monkeypatch.setattr(launcher.os, "kill", kill_raising(_Gone(22, "The parameter is incorrect")))
    assert launcher.stop(args()) == 0
    assert not (control / "instance.json").exists()


def test_a_dead_pid_while_the_instance_still_answers_refuses_and_keeps_the_record(control, monkeypatch):
    monkeypatch.setattr(launcher, "fetch_json", lambda url, timeout=5.0: {"bridge": "ready", "instance_id": "mc-1"})
    monkeypatch.setattr(launcher.os, "kill", kill_raising(_Gone(22, "The parameter is incorrect")))
    assert launcher.stop(args()) == 1
    assert (control / "instance.json").exists()


def test_a_refused_kill_refuses(control, monkeypatch):
    monkeypatch.setattr(launcher, "fetch_json", lambda url, timeout=5.0: {"bridge": "ready", "instance_id": "mc-1"})
    monkeypatch.setattr(launcher.os, "kill", kill_raising(_Denied(13, "Access is denied")))
    assert launcher.stop(args()) == 1
    assert (control / "instance.json").exists()


def test_stop_waits_until_the_instance_stops_answering(control, monkeypatch):
    answers = {"n": 0}
    def health(url, timeout=5.0):
        answers["n"] += 1
        return {"bridge": "ready", "instance_id": "mc-1"} if answers["n"] <= 3 else None
    monkeypatch.setattr(launcher, "fetch_json", health)
    monkeypatch.setattr(launcher.os, "kill", lambda pid, sig: None)
    assert launcher.stop(args(stop_wait=5)) == 0
    assert not (control / "instance.json").exists()


def test_stop_reports_an_instance_that_keeps_answering(control, monkeypatch):
    monkeypatch.setattr(launcher, "fetch_json", lambda url, timeout=5.0: {"bridge": "ready", "instance_id": "mc-1"})
    monkeypatch.setattr(launcher.os, "kill", lambda pid, sig: None)
    assert launcher.stop(args()) == 1
    assert (control / "instance.json").exists()


def test_start_reports_an_unopenable_log_without_starting(tmp_path, monkeypatch):
    monkeypatch.setattr(launcher, "paths", lambda: (tmp_path, tmp_path))
    monkeypatch.setattr(launcher, "fetch_json", lambda url, timeout=5.0: None)
    (tmp_path / "mission_control.log").mkdir()          # cannot be opened as a file
    def no_popen(*a, **k):
        raise AssertionError("must not start")
    monkeypatch.setattr(launcher.subprocess, "Popen", no_popen)
    assert launcher.main(["start", "--no-browser"]) == 1


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"PASS {fn.__name__}")
    print(f"{len(fns)}/{len(fns)} green")
