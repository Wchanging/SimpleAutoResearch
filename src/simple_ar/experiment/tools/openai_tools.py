from __future__ import annotations

from typing import Any

from simple_ar.experiment.tools.registry import default_experiment_tool_specs


def export_openai_tool_schemas(*, read_only_only: bool = True) -> list[dict[str, Any]]:
    """Export lightweight OpenAI-compatible tool schemas for future adapters."""

    tools: list[dict[str, Any]] = []
    for spec in default_experiment_tool_specs():
        if read_only_only and spec.permission != "read_only":
            continue
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": spec.name,
                    "description": spec.description,
                    "parameters": spec.input_schema
                    or {
                        "type": "object",
                        "properties": {},
                        "additionalProperties": False,
                    },
                },
            }
        )
    return tools

