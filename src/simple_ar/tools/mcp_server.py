"""Minimal stdio MCP server for run-local SimpleAutoResearch tools."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

from simple_ar.tools.gateway import CommonToolGateway
from simple_ar.tools.mcp_schema import export_mcp_tool_schemas
from simple_ar.tools.registry import default_tool_registry
from simple_ar.tools.specs import ToolCall


JSONRPC_VERSION = "2.0"
DEFAULT_PROTOCOL_VERSION = "2024-11-05"


@dataclass(slots=True)
class MCPServerConfig:
    run_dir: Path
    debug_payloads: bool = False
    read_only_only: bool = True


class SimpleARMCPStdioServer:
    """Small MCP stdio server exposing existing read-only run tools.

    This intentionally implements only the MCP methods needed for a practical
    tool-using external-agent workflow: initialize, tools/list, tools/call, and
    ping. It does not expose write/execution tools by default.
    """

    def __init__(self, config: MCPServerConfig) -> None:
        self.config = config
        self.registry = default_tool_registry(include_report=False, include_experiment=True)
        self.gateway = CommonToolGateway(
            config.run_dir,
            registry=self.registry,
            debug_payloads=config.debug_payloads,
        )

    def serve(self, stdin: BinaryIO | None = None, stdout: BinaryIO | None = None) -> None:
        stdin = stdin or sys.stdin.buffer
        stdout = stdout or sys.stdout.buffer
        while True:
            message = _read_mcp_message(stdin)
            if message is None:
                return
            response = self.handle_message(message)
            if response is not None:
                _write_mcp_message(stdout, response)

    def handle_message(self, message: dict[str, Any]) -> dict[str, Any] | None:
        request_id = message.get("id")
        method = str(message.get("method") or "")
        params = message.get("params")
        params = params if isinstance(params, dict) else {}
        if request_id is None and method.startswith("notifications/"):
            return None
        try:
            if method == "initialize":
                result = self._initialize(params)
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {
                    "tools": export_mcp_tool_schemas(
                        self.registry,
                        read_only_only=self.config.read_only_only,
                    )
                }
            elif method == "tools/call":
                result = self._tools_call(params)
            else:
                return _jsonrpc_error(request_id, -32601, f"Unsupported MCP method: {method}")
        except Exception as exc:  # pragma: no cover - defensive boundary for external clients
            return _jsonrpc_error(request_id, -32000, str(exc))
        return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "result": result}

    def _initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        protocol = str(params.get("protocolVersion") or DEFAULT_PROTOCOL_VERSION)
        return {
            "protocolVersion": protocol,
            "serverInfo": {"name": "simple-autoresearch", "version": "0.1.0"},
            "capabilities": {"tools": {"listChanged": False}},
        }

    def _tools_call(self, params: dict[str, Any]) -> dict[str, Any]:
        name = str(params.get("name") or "")
        arguments = params.get("arguments")
        arguments = arguments if isinstance(arguments, dict) else {}
        result = self.gateway.call(ToolCall(tool_name=name, arguments=arguments, caller="mcp"))
        payload = result.model_dump(mode="json")
        text = json.dumps(payload, indent=2, ensure_ascii=False)
        return {
            "content": [{"type": "text", "text": text}],
            "isError": result.status not in {"ok", "not_found"},
            "structuredContent": payload,
        }


def _read_mcp_message(stdin: BinaryIO) -> dict[str, Any] | None:
    first = stdin.readline()
    if not first:
        return None
    # Helpful for local smoke tests: accept one JSON object per line.
    if first.lstrip().startswith(b"{"):
        return json.loads(first.decode("utf-8"))
    headers: dict[str, str] = {}
    line = first
    while line and line not in {b"\r\n", b"\n"}:
        key, _, value = line.decode("ascii", errors="replace").partition(":")
        headers[key.strip().lower()] = value.strip()
        line = stdin.readline()
    length = int(headers.get("content-length", "0"))
    if length <= 0:
        return None
    body = stdin.read(length)
    return json.loads(body.decode("utf-8"))


def _write_mcp_message(stdout: BinaryIO, message: dict[str, Any]) -> None:
    body = json.dumps(message, ensure_ascii=False).encode("utf-8")
    header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
    stdout.write(header + body)
    stdout.flush()


def _jsonrpc_error(request_id: object, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": JSONRPC_VERSION,
        "id": request_id,
        "error": {"code": code, "message": message},
    }
