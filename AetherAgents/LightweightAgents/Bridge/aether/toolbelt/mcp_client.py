"""MCP client (MOD-08, Bridge Overhaul M2, FR-03): an external MCP server's tools join a belt UNAPPROVED.

The Bridge talks to an MCP server over JSON-RPC 2.0: `initialize`, the `notifications/initialized`
notification, then `tools/list` (following `nextCursor`). Each MCP tool (name, description,
inputSchema) becomes the OpenAI function schema the belt uses, under a namespaced name
`<server>__<tool>` (characters outside A-Z a-z 0-9 _ - become `_`; a name over the 64 characters
a function name may have is shortened and given a digest suffix). Every tool is attached with
`belt.attach(schema, handler, source="mcp:<server>")`, so the shared tool registry decides what
runs: an announced tool is UNAPPROVED until an operator approves it, and a re-list that shows a
changed schema suspends the approval (the registry's rule; this module only re-announces).

The transport is injected: any callable `send(message) -> response`. A response may be a dict,
or JSON text or bytes (then its size is checked before it is parsed); a notification's answer is
ignored. `StdioTransport` runs a server as a subprocess; it is never the default and never used
in tests. The client runs each exchange against the caller's deadline; a server that misses it
is disconnected (`close()` when the transport has one) and must be attached again.

Inputs:  a server name; a transport; `timeout` in seconds (no default: it comes from the
         caller's configuration); `max_response_bytes` (a generous runaway cap, raisable).
Outputs: McpServer (attach(belt) -> AttachReport, re-callable to re-list), attach_server(...),
         McpClient (initialize, list_tools, call_tool), mcp_tool_schema, namespaced_name,
         read_message (stdio framing), StdioTransport, mcp_servers_setting (a settings
         validator) and attach_configured(belt, servers, transport_factory).
         A tool's handler returns the result text (or {"content": [...]} when not all text),
         and {"error": ...} on an MCP error, a tool error result, a timeout or a bad answer.
Never:   approves a tool; takes a built-in's name or another source's tool name; offers or runs
         a tool the registry has not approved (the belt asks the registry on every offer and
         call); accepts an answer it cannot verify (wrong id, wrong shape, over the cap);
         starts a subprocess unless a caller builds a StdioTransport.
"""
from __future__ import annotations

import hashlib
import itertools
import json
import math
import os
import queue
import re
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from aether.toolbelt.registry import BUILTIN_TOOLS, WITHDRAWN, ToolRegistryError
from aether.toolbelt.toolbelt import ToolError

PROTOCOL_VERSION = "2024-11-05"
CLIENT_INFO = {"name": "aether-bridge", "version": "m2"}
SEPARATOR = "__"
NAME_LIMIT = 64                                  # OpenAI's function-name rule, not a policy limit
DEFAULT_MAX_RESPONSE_BYTES = 16 * 1024 * 1024    # catches a runaway server; raise it in configuration
RUNAWAY_LIST_PAGES = 10_000                      # a cursor loop, not a real listing
_UNSAFE = re.compile(r"[^A-Za-z0-9_-]")


class McpError(ToolError):
    """Anything that stops an MCP exchange. A ToolError, so belt callers already handle it."""


class McpProtocolError(McpError):
    """The server's answer is not one this client can verify."""


class McpTimeout(McpError):
    pass


class McpRemoteError(McpError):
    """A JSON-RPC error object from the server."""

    def __init__(self, code: int, message: str, data: Any = None):
        super().__init__(f"MCP error {code}: {message}")
        self.code, self.remote_message, self.data = code, message, data


# -- names and schemas --------------------------------------------------------------------------

def _sanitize(text: str) -> str:
    return _UNSAFE.sub("_", text.strip())


def namespaced_name(prefix: str, tool: str) -> str:
    """`<prefix>__<tool>`, sanitized and at most NAME_LIMIT characters. An empty prefix means no
    namespace (an operator's explicit choice; built-in names are still refused)."""
    clean_tool = _sanitize(tool)
    if not clean_tool.strip("_-"):
        raise McpProtocolError(f"tool name {tool!r} has no usable characters")
    name = f"{_sanitize(prefix)}{SEPARATOR}{clean_tool}" if prefix else clean_tool
    if len(name) > NAME_LIMIT:
        tag = hashlib.sha256(f"{prefix}{SEPARATOR}{tool}".encode("utf-8")).hexdigest()[:8]
        name = f"{name[:NAME_LIMIT - len(tag) - 1]}_{tag}"
    return name


def mcp_tool_schema(tool: Any, *, prefix: str) -> Dict[str, Any]:
    """One MCP tool entry -> the OpenAI function schema the belt carries."""
    if not isinstance(tool, dict):
        raise McpProtocolError(f"a tools/list entry must be an object, got {type(tool).__name__}")
    mcp_name = tool.get("name")
    if not isinstance(mcp_name, str) or not mcp_name.strip():
        raise McpProtocolError("a tools/list entry has no name")
    description = tool.get("description", "")
    if description is None:
        description = ""
    if not isinstance(description, str):
        raise McpProtocolError(f"tool {mcp_name!r}: description must be text")
    params = tool.get("inputSchema")
    if not isinstance(params, dict):
        raise McpProtocolError(f"tool {mcp_name!r}: inputSchema must be a JSON Schema object")
    if params.get("type", "object") != "object":
        raise McpProtocolError(f"tool {mcp_name!r}: inputSchema type must be 'object', "
                               f"not {params.get('type')!r}")
    params = {"type": "object", **params}
    return {"type": "function", "function": {"name": namespaced_name(prefix, mcp_name),
                                             "description": description, "parameters": params}}


# -- the client ---------------------------------------------------------------------------------

def _positive(value: Any, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or \
            not math.isfinite(value) or value <= 0:
        raise McpError(f"{what} must be a positive number of seconds/bytes, got {value!r}")
    return value


class McpClient:
    def __init__(self, transport: Callable[[Dict[str, Any]], Any], *, timeout: float,
                 max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES, label: str = "mcp"):
        if not callable(transport):
            raise McpError("the transport must be a callable send(message) -> response")
        self.transport = transport
        self.timeout = _positive(timeout, "timeout")
        self.max_response_bytes = int(_positive(max_response_bytes, "max_response_bytes"))
        self.label = label
        self.server_info: Optional[Dict[str, Any]] = None
        self.protocol_version: Optional[str] = None
        self.closed: Optional[str] = None
        self._ids = itertools.count(1)
        self._lock = threading.Lock()

    # One exchange, against the caller's deadline.
    def _exchange(self, message: Dict[str, Any]) -> Any:
        if self.closed:
            raise McpError(f"{self.label}: disconnected ({self.closed}); attach the server again")
        box: Dict[str, Any] = {}

        def work():
            try:
                box["value"] = self.transport(message)
            except BaseException as exc:  # handed to the caller below
                box["error"] = exc

        worker = threading.Thread(target=work, daemon=True, name=f"{self.label}:{message.get('method')}")
        worker.start()
        worker.join(self.timeout)
        if worker.is_alive():
            self.close(f"no answer to {message.get('method')} within {self.timeout:g}s")
            raise McpTimeout(f"{self.label}: no answer to {message.get('method')} within "
                             f"{self.timeout:g}s (the configured timeout); disconnected")
        if "error" in box:
            exc = box["error"]
            if isinstance(exc, McpError):
                raise exc
            raise McpError(f"{self.label}: transport failed during {message.get('method')}: "
                           f"{type(exc).__name__}: {exc}") from exc
        return box.get("value")

    def _decode(self, raw: Any, method: str) -> Any:
        if isinstance(raw, (str, bytes, bytearray)):
            data = raw.encode("utf-8") if isinstance(raw, str) else bytes(raw)
            self._bounded(len(data), method)
            try:
                return json.loads(data.decode("utf-8"))
            except (UnicodeDecodeError, ValueError) as exc:
                raise McpProtocolError(f"{self.label}: the answer to {method} is not JSON: {exc}") from exc
        try:
            size = len(json.dumps(raw, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        except (TypeError, ValueError) as exc:
            raise McpProtocolError(f"{self.label}: the answer to {method} is not JSON data: {exc}") from exc
        self._bounded(size, method)
        return raw

    def _bounded(self, size: int, method: str) -> None:
        if size > self.max_response_bytes:
            raise McpProtocolError(f"{self.label}: the answer to {method} is {size} bytes, over the "
                                   f"max_response_bytes cap of {self.max_response_bytes}; raise the cap "
                                   f"in configuration if this server is trusted to send more")

    def request(self, method: str, params: Optional[Dict[str, Any]] = None) -> Any:
        with self._lock:
            msg_id = next(self._ids)
        message = {"jsonrpc": "2.0", "id": msg_id, "method": method}
        if params is not None:
            message["params"] = params
        response = self._decode(self._exchange(message), method)
        where = f"{self.label}: the answer to {method}"
        if not isinstance(response, dict):
            raise McpProtocolError(f"{where} is not a JSON-RPC object")
        if response.get("jsonrpc") != "2.0":
            raise McpProtocolError(f"{where} is not JSON-RPC 2.0")
        if response.get("id") != msg_id:
            raise McpProtocolError(f"{where} carries id {response.get('id')!r}, expected {msg_id}")
        if "error" in response:
            error = response["error"]
            if not isinstance(error, dict) or not isinstance(error.get("code"), int) \
                    or isinstance(error.get("code"), bool) or not isinstance(error.get("message"), str):
                raise McpProtocolError(f"{where} has a malformed error object")
            raise McpRemoteError(error["code"], error["message"], error.get("data"))
        if "result" not in response:
            raise McpProtocolError(f"{where} has neither result nor error")
        return response["result"]

    def notify(self, method: str, params: Optional[Dict[str, Any]] = None) -> None:
        message = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        self._exchange(message)

    def initialize(self) -> Dict[str, Any]:
        result = self.request("initialize", {"protocolVersion": PROTOCOL_VERSION, "capabilities": {},
                                             "clientInfo": dict(CLIENT_INFO)})
        if not isinstance(result, dict) or not isinstance(result.get("protocolVersion"), str):
            raise McpProtocolError(f"{self.label}: initialize returned no protocolVersion")
        info = result.get("serverInfo")
        self.server_info = info if isinstance(info, dict) else {}
        self.protocol_version = result["protocolVersion"]
        self.notify("notifications/initialized")
        return result

    def list_tools(self) -> List[Any]:
        tools: List[Any] = []
        cursor, seen = None, set()
        for _ in range(RUNAWAY_LIST_PAGES):
            result = self.request("tools/list", {"cursor": cursor} if cursor else None)
            if not isinstance(result, dict) or not isinstance(result.get("tools"), list):
                raise McpProtocolError(f"{self.label}: tools/list returned no tools array")
            tools.extend(result["tools"])
            cursor = result.get("nextCursor")
            if cursor is None:
                return tools
            if not isinstance(cursor, str) or cursor in seen:
                raise McpProtocolError(f"{self.label}: tools/list returned a bad or repeated nextCursor")
            seen.add(cursor)
        raise McpProtocolError(f"{self.label}: tools/list kept paging past {RUNAWAY_LIST_PAGES} pages")

    def call_tool(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        result = self.request("tools/call", {"name": name, "arguments": arguments})
        if not isinstance(result, dict) or not isinstance(result.get("content"), list):
            raise McpProtocolError(f"{self.label}: tools/call for {name!r} returned no content array")
        return result

    def close(self, why: str = "closed") -> None:
        self.closed = self.closed or why
        close = getattr(self.transport, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass


# -- tool results -------------------------------------------------------------------------------

def _text_of(content: List[Any]) -> Optional[str]:
    """The joined text when every item is a text item; None otherwise."""
    if all(isinstance(c, dict) and c.get("type") == "text" and isinstance(c.get("text"), str)
           for c in content):
        return "\n".join(c["text"] for c in content)
    return None


def _handler(client: McpClient, mcp_name: str, source: str):
    def run(args):
        if args is None:
            args = {}
        if not isinstance(args, dict):
            return {"error": f"{source}: arguments must be a JSON object"}
        try:
            result = client.call_tool(mcp_name, args)
        except McpError as exc:
            return {"error": f"{source}: {exc}"}
        content = result["content"]
        text = _text_of(content)
        if result.get("isError") is True:
            detail = text if text is not None else json.dumps(content, ensure_ascii=False)
            return {"error": f"{source}: {mcp_name} failed: {detail or 'no detail given'}"}
        structured = result.get("structuredContent")
        if text is not None and not isinstance(structured, dict):
            return text
        value: Dict[str, Any] = {"content": content}
        if isinstance(structured, dict):
            value["structuredContent"] = structured
        return value
    run.mcp_name = mcp_name
    run.source = source
    return run


# -- attaching a server -------------------------------------------------------------------------

@dataclass
class AttachReport:
    server: str
    source: str
    server_info: Dict[str, Any] = field(default_factory=dict)
    attached: Dict[str, str] = field(default_factory=dict)   # belt name -> registry state
    refused: Dict[str, str] = field(default_factory=dict)    # MCP name (or #index) -> why
    vanished: List[str] = field(default_factory=list)        # registered by this source, no longer listed


class McpServer:
    """One configured MCP server. attach(belt) lists its tools and attaches them; call it again
    to re-list (a changed schema is re-announced, which suspends its approval)."""

    def __init__(self, name: str, transport, *, timeout: float,
                 max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES, prefix: Optional[str] = None):
        if not isinstance(name, str) or not _sanitize(name).strip("_-"):
            raise McpError(f"an MCP server needs a name with usable characters, got {name!r}")
        self.name = name
        self.source = f"mcp:{name}"
        self.prefix = _sanitize(name) if prefix is None else prefix
        self.client = McpClient(transport, timeout=timeout, max_response_bytes=max_response_bytes,
                                label=self.source)

    def attach(self, belt) -> AttachReport:
        if self.client.server_info is None:
            self.client.initialize()
        tools = self.client.list_tools()
        registry = belt.tool_registry()
        records = registry.records()
        report = AttachReport(self.name, self.source, dict(self.client.server_info or {}))
        listed = set()
        for index, tool in enumerate(tools):
            label = tool.get("name") if isinstance(tool, dict) and isinstance(tool.get("name"), str) \
                else f"#{index}"
            try:
                schema = mcp_tool_schema(tool, prefix=self.prefix)
            except McpError as exc:
                report.refused[label] = str(exc)
                continue
            name = schema["function"]["name"]
            listed.add(name)
            if name in BUILTIN_TOOLS:
                report.refused[label] = f"'{name}' is a built-in tool's name; an MCP tool may not take it"
                continue
            if name in report.attached:
                report.refused[label] = f"'{name}' is already taken by another tool of this server"
                continue
            owner = records.get(name, {}).get("source")
            if owner is not None and owner != self.source:
                report.refused[label] = f"'{name}' belongs to {owner}; it is not {self.source}'s to take"
                continue
            try:
                belt.attach(schema, _handler(self.client, tool["name"], self.source), source=self.source,
                            source_definition=tool)
            except (ToolError, ToolRegistryError) as exc:
                report.refused[label] = str(exc)
                continue
            report.attached[name] = registry.state(schema).state
        # The server no longer lists them: they leave this belt, so no call can reach a server that
        # no longer has the tool. Their registry records and history stay; if the server lists one
        # again, it comes back under the registry's usual rules.
        report.vanished = sorted(n for n, r in registry.records().items()
                                 if r.get("source") == self.source and n not in listed
                                 and r.get("state") != WITHDRAWN)
        for name in report.vanished:
            belt.detach(name)
        return report

    def close(self) -> None:
        self.client.close()


def attach_server(belt, server_name: str, transport, *, timeout: float,
                  max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
                  prefix: Optional[str] = None) -> AttachReport:
    """Connect, list and attach in one step. Keep an McpServer instead to re-list later."""
    return McpServer(server_name, transport, timeout=timeout, max_response_bytes=max_response_bytes,
                     prefix=prefix).attach(belt)


# -- stdio framing and transport (never the default; never used in tests) -----------------------

def read_message(stream, max_bytes: int) -> Optional[Any]:
    """One message from a binary stream: a newline-delimited JSON line (the MCP stdio framing)
    or a Content-Length block (what aether's own mcp_server writes). None at end of stream."""
    while True:
        first = stream.readline(max_bytes + 1)
        if not first:
            return None
        if len(first) > max_bytes:
            raise McpProtocolError(f"a message line exceeds the {max_bytes}-byte cap")
        if first.strip():
            break
    if first.lower().startswith(b"content-length:"):
        try:
            length = int(first.split(b":", 1)[1].strip())
        except ValueError as exc:
            raise McpProtocolError(f"bad Content-Length header: {first[:80]!r}") from exc
        if length < 0 or length > max_bytes:
            raise McpProtocolError(f"a message of {length} bytes exceeds the {max_bytes}-byte cap")
        while True:
            line = stream.readline(max_bytes + 1)
            if line in (b"\r\n", b"\n", b""):
                break
        data = stream.read(length)
    else:
        data = first
    try:
        return json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise McpProtocolError(f"a message from the server is not JSON: {exc}") from exc


class StdioTransport:
    """A server run as a subprocess (argv, no shell). Requests are matched to answers by id;
    server notifications are dropped and server requests answered 'method not found'."""

    def __init__(self, argv: List[str], *, cwd: Optional[str] = None,
                 max_message_bytes: int = DEFAULT_MAX_RESPONSE_BYTES):
        if not isinstance(argv, list) or not argv or not all(isinstance(a, str) and a for a in argv):
            raise McpError("a stdio MCP server needs a non-empty argv list of strings")
        self.max_message_bytes = int(_positive(max_message_bytes, "max_message_bytes"))
        self._proc = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                      stderr=subprocess.DEVNULL, shell=False,
                                      creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        self._waiting: Dict[Any, "queue.Queue"] = {}
        self._lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._ended: Optional[str] = None
        threading.Thread(target=self._read_loop, daemon=True, name="mcp-stdio-reader").start()

    def _write(self, message: Dict[str, Any]) -> None:
        data = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
        with self._write_lock:
            self._proc.stdin.write(data)
            self._proc.stdin.flush()

    def _read_loop(self) -> None:
        why = "the server closed its output"
        try:
            while True:
                message = read_message(self._proc.stdout, self.max_message_bytes)
                if message is None:
                    break
                if not isinstance(message, dict):
                    continue
                if "method" in message:
                    if "id" in message:
                        self._write({"jsonrpc": "2.0", "id": message["id"],
                                     "error": {"code": -32601, "message": "not supported by this client"}})
                    continue
                with self._lock:
                    waiter = self._waiting.pop(message.get("id"), None)
                if waiter is not None:
                    waiter.put(message)
        except Exception as exc:  # a protocol violation ends the connection
            why = str(exc)
        self._end(why)

    def _end(self, why: str) -> None:
        with self._lock:
            self._ended = self._ended or why
            waiters, self._waiting = list(self._waiting.values()), {}
        for waiter in waiters:
            waiter.put(None)

    def __call__(self, message: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        waiter: Optional["queue.Queue"] = None
        with self._lock:
            if self._ended:
                raise McpError(f"stdio server ended: {self._ended}")
            if "id" in message:
                waiter = queue.Queue(maxsize=1)
                self._waiting[message["id"]] = waiter
        self._write(message)
        if waiter is None:
            return None
        answer = waiter.get()   # the client's deadline closes us, which wakes this
        if answer is None:
            raise McpError(f"stdio server ended: {self._ended}")
        return answer

    def close(self) -> None:
        self._end("closed by the client")
        try:
            self._proc.stdin.close()
        except Exception:
            pass
        if self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait(timeout=2)


# -- configuration ------------------------------------------------------------------------------

# The settings validator lives in Configuration (the layer below), the same object:
# Configuration validates `toolbelt.mcp_servers` without importing Toolbelt.
from aether.config.settings import mcp_servers as mcp_servers_setting  # noqa: E402


def attach_configured(belt, servers: List[Dict[str, Any]], *,
                      transport_factory: Callable[[Dict[str, Any]], Any]) -> Dict[str, Any]:
    """Attach every configured server (validated by mcp_servers_setting). One server failing does
    not stop the others: its entry is the error text. The caller chooses the transport factory
    (StdioTransport for real servers: `lambda s: StdioTransport(s["command"], cwd=s.get("cwd"))`)."""
    results: Dict[str, Any] = {}
    for entry in servers:
        transport = None
        try:
            transport = transport_factory(entry)
            server = McpServer(entry["name"], transport, timeout=entry["timeout_seconds"],
                               max_response_bytes=entry.get("max_response_bytes", DEFAULT_MAX_RESPONSE_BYTES),
                               prefix=entry.get("prefix"))
            results[entry["name"]] = server.attach(belt)
        except (McpError, ToolError, OSError) as exc:
            close = getattr(transport, "close", None)
            if callable(close):
                close()
            results[entry["name"]] = f"refused: {exc}"
    return results
