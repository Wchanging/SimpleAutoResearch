from __future__ import annotations

from simple_ar.tools.specs import CommonToolSpec, PermissionLevel, RiskLevel


def default_code_task_tool_specs() -> list[CommonToolSpec]:
    """Return read-only tool specs for code-task memory and code lookup."""
    return [
        CommonToolSpec(
            name="read_code_task_memory",
            description="Read the compact code-task memory summary and JSON state for continuity across plans, edits, validation, and repairs.",
            input_schema=_object_schema({}),
            output_schema=_object_schema({}),
            permission_level=PermissionLevel.READ_ONLY,
            risk_level=RiskLevel.LOW,
            domain="code_task",
            max_calls=8,
        ),
        CommonToolSpec(
            name="list_code_task_files",
            description="List indexed files in the isolated code-task workspace, with summaries and role tags.",
            input_schema=_object_schema(
                {
                    "role": {
                        "type": "string",
                        "description": "Optional role/access filter such as editable, read_only_evidence, test, config, or python.",
                    },
                    "extensions": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional file extensions, for example ['.py', '.toml'].",
                    },
                    "max_files": {"type": "integer", "minimum": 1, "maximum": 300, "default": 120},
                }
            ),
            output_schema=_object_schema({}),
            permission_level=PermissionLevel.READ_ONLY,
            risk_level=RiskLevel.LOW,
            domain="code_task",
            max_calls=12,
        ),
        CommonToolSpec(
            name="search_code_task_code",
            description="Search text inside code-task workspace files and return bounded line previews.",
            input_schema=_object_schema(
                {
                    "query": {"type": "string", "description": "Required search string."},
                    "extensions": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional extension filter. Defaults to common source/config/docs files.",
                    },
                    "case_sensitive": {"type": "boolean", "default": False},
                    "max_matches": {"type": "integer", "minimum": 1, "maximum": 100, "default": 30},
                },
                required=["query"],
            ),
            output_schema=_object_schema({}),
            permission_level=PermissionLevel.READ_ONLY,
            risk_level=RiskLevel.LOW,
            domain="code_task",
            max_calls=16,
        ),
        CommonToolSpec(
            name="read_code_task_file_range",
            description="Read a bounded line range from a code-task workspace file.",
            input_schema=_object_schema(
                {
                    "path": {"type": "string", "description": "Workspace-relative file path."},
                    "start_line": {"type": "integer", "minimum": 1, "default": 1},
                    "max_lines": {"type": "integer", "minimum": 1, "maximum": 200, "default": 80},
                },
                required=["path"],
            ),
            output_schema=_object_schema({}),
            permission_level=PermissionLevel.READ_ONLY,
            risk_level=RiskLevel.LOW,
            domain="code_task",
            max_calls=20,
        ),
        CommonToolSpec(
            name="find_code_task_symbol",
            description="Find functions, classes, or methods from the code-task repo map.",
            input_schema=_object_schema(
                {
                    "query": {"type": "string", "description": "Symbol name or substring."},
                    "kind": {"type": "string", "description": "Optional kind filter: function, class, or method."},
                    "case_sensitive": {"type": "boolean", "default": False},
                    "max_matches": {"type": "integer", "minimum": 1, "maximum": 80, "default": 25},
                },
                required=["query"],
            ),
            output_schema=_object_schema({}),
            permission_level=PermissionLevel.READ_ONLY,
            risk_level=RiskLevel.LOW,
            domain="code_task",
            max_calls=12,
        ),
        CommonToolSpec(
            name="find_code_task_related_files",
            description="Return files most likely related to a query by combining locate results, repo-map summaries, imports, symbols, and path tokens.",
            input_schema=_object_schema(
                {
                    "query": {"type": "string", "description": "Task, feature, error, or file-related query."},
                    "max_files": {"type": "integer", "minimum": 1, "maximum": 80, "default": 20},
                },
                required=["query"],
            ),
            output_schema=_object_schema({}),
            permission_level=PermissionLevel.READ_ONLY,
            risk_level=RiskLevel.LOW,
            domain="code_task",
            max_calls=10,
        ),
        CommonToolSpec(
            name="list_code_task_recent_edits",
            description="List recent code-task edit, validation, review, and repair memory entries.",
            input_schema=_object_schema(
                {
                    "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
                    "include_reviews": {"type": "boolean", "default": True},
                    "include_repairs": {"type": "boolean", "default": True},
                }
            ),
            output_schema=_object_schema({}),
            permission_level=PermissionLevel.READ_ONLY,
            risk_level=RiskLevel.LOW,
            domain="code_task",
            max_calls=8,
        ),
    ]


def _object_schema(properties: dict[str, object], *, required: list[str] | None = None) -> dict[str, object]:
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": False,
    }
