from __future__ import annotations

from simple_ar.tools.registry import ToolRegistry, default_tool_registry


def export_mcp_tool_schemas(
    registry: ToolRegistry | None = None,
    *,
    read_only_only: bool = True,
) -> list[dict[str, object]]:
    """Export registered tools in MCP-style tool definition format."""
    registry = registry or default_tool_registry()
    tools: list[dict[str, object]] = []
    for spec in registry.list_specs():
        if read_only_only and spec.permission_level not in {"read_only", "plan"}:
            continue
        tools.append(
            {
                "name": spec.name,
                "description": spec.description,
                "inputSchema": spec.parameters_schema(),
                "annotations": {
                    "domain": spec.domain,
                    "permission_level": spec.permission_level,
                    "risk_level": spec.risk_level,
                },
            }
        )
    return tools
