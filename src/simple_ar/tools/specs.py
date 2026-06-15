from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class PermissionLevel(str, Enum):
    """Common permission categories for local tools and external backends."""

    READ_ONLY = "read_only"
    PLAN = "plan"
    WRITE_PATCH = "write_patch"
    EXECUTION = "execution"
    NETWORK = "network"
    SECRET = "secret"


class RiskLevel(str, Enum):
    """Risk level used by previews, approval gates, and traces."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class CommonToolSpec(BaseModel):
    """Tool contract shared by local, OpenAI-style, and MCP-style adapters."""

    model_config = ConfigDict(use_enum_values=True)

    schema_version: str = "common_tool_spec.v1"
    name: str
    description: str
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    permission_level: PermissionLevel = PermissionLevel.READ_ONLY
    risk_level: RiskLevel = RiskLevel.LOW
    domain: str = "core"
    artifact_outputs: list[str] = Field(default_factory=list)
    max_calls: int = 8

    def parameters_schema(self) -> dict[str, Any]:
        """Return an object JSON schema suitable for tool-call APIs."""
        if self.input_schema:
            return self.input_schema
        return {"type": "object", "properties": {}, "additionalProperties": False}


class ToolCall(BaseModel):
    """One tool invocation request."""

    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    caller: str = "simple_ar"
    trace_id: str = ""


class ToolResult(BaseModel):
    """Common result returned by all local tool gateways."""

    tool_name: str
    status: Literal["ok", "not_found", "blocked", "error"] = "ok"
    data: dict[str, Any] = Field(default_factory=dict)
    summary: str = ""
    error: str = ""
    artifacts: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == "ok"
