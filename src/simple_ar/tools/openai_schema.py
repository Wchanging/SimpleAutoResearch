from __future__ import annotations

from simple_ar.tools.registry import ToolRegistry, default_tool_registry


def export_openai_tool_schemas(
    registry: ToolRegistry | None = None,
    *,
    read_only_only: bool = True,
) -> list[dict[str, object]]:
    """Export registered tools in OpenAI-compatible function-tool format."""
    registry = registry or default_tool_registry()
    tools: list[dict[str, object]] = []
    for spec in registry.list_specs():
        if read_only_only and spec.permission_level not in {"read_only", "plan"}:
            continue
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": spec.name,
                    "description": spec.description,
                    "parameters": spec.parameters_schema(),
                },
            }
        )
    return tools
