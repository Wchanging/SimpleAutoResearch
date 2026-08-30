"""CLI helpers for common tools and MCP stdio serving."""

from __future__ import annotations

import json
from pathlib import Path

from simple_ar.core.console import print_line
from simple_ar.tools.gateway import CommonToolGateway
from simple_ar.tools.mcp_schema import export_mcp_tool_schemas
from simple_ar.tools.mcp_server import MCPServerConfig, SimpleARMCPStdioServer
from simple_ar.tools.openai_schema import export_openai_tool_schemas
from simple_ar.tools.specs import ToolCall


def print_tool_schema(*, schema_format: str, output: str | None = None) -> None:
    schema = export_mcp_tool_schemas() if schema_format == "mcp" else export_openai_tool_schemas()
    text = json.dumps(schema, indent=2, ensure_ascii=False)
    if output:
        Path(output).write_text(text + "\n", encoding="utf-8")
        print_line(f"Wrote {schema_format} tool schema: {output}")
    else:
        print_line(text)


def call_tool(
    run_dir: Path,
    tool_name: str,
    *,
    args_json: str = "{}",
    args_file: str | None = None,
    debug_payloads: bool = False,
) -> None:
    if args_file:
        args_json = Path(args_file).read_text(encoding="utf-8-sig")
    try:
        arguments = json.loads(args_json or "{}")
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid --args-json: {exc}") from exc
    if not isinstance(arguments, dict):
        raise SystemExit("--args-json must decode to a JSON object.")
    gateway = CommonToolGateway(run_dir, debug_payloads=debug_payloads)
    result = gateway.call(ToolCall(tool_name=tool_name, arguments=arguments, caller="cli"))
    print_line(result.model_dump_json(indent=2))


def serve_mcp(run_dir: Path, *, debug_payloads: bool = False) -> None:
    server = SimpleARMCPStdioServer(MCPServerConfig(run_dir=run_dir, debug_payloads=debug_payloads))
    server.serve()
