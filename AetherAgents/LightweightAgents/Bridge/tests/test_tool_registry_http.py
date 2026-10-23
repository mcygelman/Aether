"""Bridge Overhaul M2 (MOD-08): the tool registry page and its local API."""
import json
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aether.backends.model_settings_http import SettingsHTTPMixin  # noqa: E402
from aether.mission_control.tool_registry_http import PAGE_FILE, handle_tool_registry  # noqa: E402
from aether.toolbelt.registry import (APPROVED, BUILTIN_TOOLS, UNAPPROVED, WITHDRAWN,  # noqa: E402
                                      ToolRegistry)

LOOKUP = {"type": "function", "function": {
    "name": "weather_lookup", "description": "Look up the weather (external).",
    "parameters": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}}}
WHO = dict(by="michel", reason="read-only week")


def serve(registry):
    class Handler(SettingsHTTPMixin, BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            return

        def do_GET(self):
            if not handle_tool_registry(self, "GET", registry):
                self.settings_send({"error": "not found"}, 404)

        def do_POST(self):
            if not handle_tool_registry(self, "POST", registry):
                self.settings_send({"error": "not found"}, 404)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


@pytest.fixture
def registry(tmp_path):
    return ToolRegistry(tmp_path / "control" / "tool_registry.json")


@pytest.fixture
def client(registry):
    httpd = serve(registry)
    yield Client(httpd.server_port)
    httpd.shutdown()
    httpd.server_close()


class Client:
    def __init__(self, port):
        self.base = f"http://127.0.0.1:{port}"

    def call(self, path, body=None, *, origin="own", raw=False):
        headers = {}
        if origin == "own":
            headers["Origin"] = self.base
        elif origin is not None:
            headers["Origin"] = origin
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(self.base + path, data=data, method="POST" if body is not None else "GET",
                          headers=headers)
        try:
            with urlopen(request, timeout=10) as response:
                payload = response.read()
                return response.status, payload if raw else json.loads(payload)
        except HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def tools(self):
        status, value = self.call("/api/tool-registry")
        assert status == 200
        return {t["name"]: t for t in value["tools"]}


def test_the_builtins_are_listed_as_approved(client):
    tools = client.tools()
    assert set(tools) == set(BUILTIN_TOOLS)
    for name in BUILTIN_TOOLS:
        assert tools[name]["state"] == APPROVED
        assert tools[name]["source"] == "builtin" and tools[name]["builtin"] is True
        assert tools[name]["last_change"] is None


def test_an_announced_tool_is_listed_unapproved_with_a_short_digest(client, registry):
    registry.register(LOOKUP, source="mcp:weather")
    tool = client.tools()["weather_lookup"]
    assert tool["state"] == UNAPPROVED and tool["source"] == "mcp:weather"
    assert len(tool["digest"]) == 12 and tool["last_change"]["reason"] == "announced by mcp:weather"


def test_a_withdraw_is_journaled_and_appears_in_the_list_and_history(client, registry):
    status, value = client.call("/api/tool-registry/withdraw", dict(name="write_file", **WHO))
    assert status == 200
    change = value["change"]
    assert change["name"] == "write_file" and change["before"] == APPROVED and change["after"] == WITHDRAWN
    # journaled by the registry itself
    journaled = registry.history("write_file")
    assert [c.id for c in journaled] == [change["id"]] and journaled[0].by == "michel"
    assert registry.records()["write_file"]["state"] == WITHDRAWN
    listed = client.tools()["write_file"]
    assert listed["state"] == WITHDRAWN
    assert listed["last_change"]["by"] == "michel" and listed["last_change"]["reason"] == "read-only week"
    assert listed["last_change"]["at"]
    status, history = client.call("/api/tool-registry/history?name=write_file")
    assert status == 200 and [c["id"] for c in history["changes"]] == [change["id"]]
    assert history["changes"][0]["reason"] == "read-only week"
    status, everything = client.call("/api/tool-registry/history")
    assert status == 200 and change["id"] in [c["id"] for c in everything["changes"]]


@pytest.mark.parametrize("missing", ["by", "reason"])
@pytest.mark.parametrize("blank", [None, "", "   "])
def test_a_missing_or_blank_by_or_reason_gets_400(client, registry, missing, blank):
    body = dict(name="write_file", **WHO)
    if blank is None:
        del body[missing]
    else:
        body[missing] = blank
    status, value = client.call("/api/tool-registry/withdraw", body)
    assert status == 400 and value["error"]
    status, value = client.call("/api/tool-registry/undo", {**body, "change_id": 1, "name": None})
    assert status == 400
    assert registry.history() == [] and not registry.path.exists()


@pytest.mark.parametrize("origin", [None, "http://evil.example", "null", "http://localhost:1"])
@pytest.mark.parametrize("action", ["approve", "withdraw", "undo"])
def test_a_foreign_or_missing_origin_on_a_post_gets_403(client, registry, origin, action):
    status, value = client.call("/api/tool-registry/" + action,
                                dict(name="write_file", change_id=1, **WHO), origin=origin)
    assert status == 403 and value["error"]
    assert registry.history() == []


def test_a_foreign_host_gets_403_and_a_foreign_origin_on_a_get_gets_403(client):
    request = Request(client.base + "/api/tool-registry", headers={"Host": "evil.example"})
    with pytest.raises(HTTPError) as caught:
        urlopen(request, timeout=10)
    assert caught.value.code == 403
    assert client.call("/api/tool-registry", origin="http://evil.example")[0] == 403
    assert client.call("/api/tool-registry", origin=None)[0] == 200


def test_undo_restores_the_tool(client, registry):
    _, value = client.call("/api/tool-registry/withdraw", dict(name="run_command", **WHO))
    assert client.tools()["run_command"]["state"] == WITHDRAWN
    status, undone = client.call("/api/tool-registry/undo",
                                 dict(change_id=value["change"]["id"], by="michel", reason="back to normal",
                                      approval_token=client.tools()['run_command']['approval_token']))
    assert status == 200
    assert undone["change"]["undoes"] == value["change"]["id"] and undone["change"]["after"] == APPROVED
    assert client.tools()["run_command"]["state"] == APPROVED
    assert "run_command" not in registry.records()
    # undoing it twice is refused by the registry: 409 with its message
    status, again = client.call("/api/tool-registry/undo",
                                dict(change_id=value["change"]["id"], by="michel", reason="again",
                                     approval_token=client.tools()['run_command']['approval_token']))
    assert status == 409 and "already undone" in again["error"]


def test_an_unannounced_approve_gets_409_with_the_registry_message(client, registry):
    status, value = client.call("/api/tool-registry/approve", dict(name="weather_lookup", **WHO))
    assert status == 409 and value["error"] == "'weather_lookup' has not been announced; nothing to approve"
    status, value = client.call("/api/tool-registry/approve", dict(name="read_file", **WHO))
    assert status == 409 and "already approved" in value["error"]
    assert registry.history() == []


def test_an_announced_tool_can_be_approved_from_the_page_api(client, registry):
    registry.register(LOOKUP, source="mcp:weather")
    status, value = client.call("/api/tool-registry/approve", dict(name="weather_lookup",
        approval_token=client.tools()['weather_lookup']['approval_token'], **WHO))
    assert status == 200 and value["change"]["after"] == APPROVED
    assert client.tools()["weather_lookup"]["state"] == APPROVED
    assert registry.state(LOOKUP).state == APPROVED


def test_without_a_registry_the_api_answers_409_not_configured(tmp_path):
    httpd = serve(None)
    try:
        c = Client(httpd.server_port)
        status, value = c.call("/api/tool-registry")
        assert status == 409 and "not configured" in value["error"]
        assert c.call("/api/tool-registry/withdraw", dict(name="write_file", **WHO))[0] == 409
        assert c.call("/projects/tools", raw=True)[0] == 200
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_other_paths_are_not_answered(client):
    assert client.call("/api/tool-registryx")[0] == 404          # falls through to the test server
    assert client.call("/api/tool-registry/other")[1]["error"] == "Unknown tool registry resource"


def test_the_page_is_served_and_has_no_external_src_or_href(client):
    status, page = client.call("/projects/tools", raw=True)
    assert status == 200 and page == PAGE_FILE.read_bytes()
    html = page.decode("utf-8")
    refs = re.findall(r"""(?:src|href|action)\s*=\s*["']([^"']*)["']""", html, re.I)
    assert refs and all(r.startswith("/") and not r.startswith("//") for r in refs)
    assert not re.search(r"(?:https?:)?//[a-z0-9.-]+\.[a-z]", html.replace(".href", ""), re.I)
    assert "@import" not in html and "url(" not in html
    assert 'role="alert"' in html and 'aria-live' in html
    assert 'id="by"' in html and 'id="reason"' in html and html.count("required") >= 2
    for url in re.findall(r"""['"](/api/[^'"?]*)""", html):
        assert url.startswith("/api/tool-registry")
