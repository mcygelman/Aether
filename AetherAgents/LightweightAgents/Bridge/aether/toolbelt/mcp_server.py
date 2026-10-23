"""Minimal stdio MCP server for the Aether Bridge read tools.

Surface B is read-only by design: this server exposes list_roots, list_dir,
read_file, glob, and search. The mission runtime remains the only write mount.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

from aether.toolbelt.toolbelt import ToolBelt, ToolError

READ_TOOL_NAMES = ("list_roots", "list_dir", "read_file", "glob", "search")


def read_tool_schemas(belt=None) -> list[dict]:
    if belt is not None:
        return [tool for tool in belt.openai_tools() if tool['function']['name'] in READ_TOOL_NAMES]
    with tempfile.TemporaryDirectory() as td:
        belt = ToolBelt(Path(td))
        return [
            tool for tool in belt.openai_tools()
            if tool["function"]["name"] in READ_TOOL_NAMES
        ]


def mcp_tools(belt=None) -> list[dict]:
    tools = []
    for tool in read_tool_schemas(belt):
        fn = tool["function"]
        tools.append({
            "name": fn["name"],
            "description": fn.get("description", ""),
            "inputSchema": fn["parameters"],
        })
    return tools


def parse_root_spec(spec: str) -> tuple[str, str]:
    name, sep, rest = spec.partition("=")
    if not sep or not name or not rest:
        raise ToolError(f"bad root spec '{spec}' (expected NAME=PATH[:mode])")
    path = rest
    for mode in ("auto", "deny"):
        if rest.endswith(f":{mode}"):
            path = rest[: -(len(mode) + 1)]
            break
    return name, path


def belt_from_roots(root_specs: list[str], log=sys.stderr) -> ToolBelt:
    belt = ToolBelt(Path.cwd() / ".mcp_readonly_journal")
    for spec in root_specs:
        name, path = parse_root_spec(spec)
        belt.register_root(name, path, "deny")
        print(f"aether_bridge mcp-serve: mounted {name}={Path(path).resolve()} as deny/read-only", file=log)
    return belt


def _read_message() -> dict[str, Any] | None:
    first = sys.stdin.buffer.readline()
    if not first:
        return None

    if first.lower().startswith(b"content-length:"):
        length = int(first.split(b":", 1)[1].strip())
        while True:
            line = sys.stdin.buffer.readline()
            if line in (b"\r\n", b"\n", b""):
                break
        data = sys.stdin.buffer.read(length)
        return json.loads(data.decode("utf-8"))

    return json.loads(first.decode("utf-8"))


def _write_message(message: dict[str, Any]) -> None:
    data = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    sys.stdout.buffer.write(f"Content-Length: {len(data)}\r\n\r\n".encode("ascii"))
    sys.stdout.buffer.write(data)
    sys.stdout.buffer.flush()


def _result(msg_id, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _error(msg_id, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def handle_request(message: dict[str, Any], belt: ToolBelt) -> dict[str, Any] | None:
    msg_id = message.get("id")
    method = message.get("method")
    params = message.get("params") or {}

    if msg_id is None:
        return None

    try:
        if method == "initialize":
            return _result(msg_id, {
                "protocolVersion": params.get("protocolVersion", "2024-11-05"),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "aether-projects", "version": "0.1.0-s00065"},
            })
        if method == "ping":
            return _result(msg_id, {})
        if method == "tools/list":
            return _result(msg_id, {"tools": mcp_tools(belt)})
        if method == "tools/call":
            name = params.get("name")
            args = params.get("arguments") or {}
            if name not in READ_TOOL_NAMES:
                raise ToolError(f"tool not exposed on read-only MCP mount: {name}")
            value = belt.dispatch(name, args)
            text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)
            return _result(msg_id, {"content": [{"type": "text", "text": text}], "isError": False})
        return _error(msg_id, -32601, f"method not found: {method}")
    except (ToolError, TypeError, ValueError, OSError) as e:
        return _result(msg_id, {"content": [{"type": "text", "text": str(e)}], "isError": True})


def serve(root_specs: list[str]) -> int:
    belt = belt_from_roots(root_specs)
    while True:
        message = _read_message()
        if message is None:
            return 0
        response = handle_request(message, belt)
        if response is not None:
            _write_message(response)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="aether_bridge mcp-serve")
    ap.add_argument("--root", action="append", default=[], metavar="NAME=PATH[:mode]",
                    help="Project root to expose read-only through MCP.")
    args = ap.parse_args(argv)
    return serve(args.root)


if __name__ == "__main__":
    raise SystemExit(main())
