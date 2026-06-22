from __future__ import annotations

from pydantic import BaseModel, Field

from simple_ar.tools.permissions import ToolPermissionPolicy
from simple_ar.tools.specs import PermissionLevel, RiskLevel


class AgentPermissionPolicy(BaseModel):
    """Permission policy serialized into every external-agent handoff."""

    schema_version: str = "agent_permission_policy.v1"
    allow_file_write: bool = False
    allow_shell_commands: bool = False
    allow_network: bool = False
    allow_secret_access: bool = False
    max_runtime_sec: int = 600
    allowed_tools: list[str] = Field(default_factory=list)
    blocked_tools: list[str] = Field(default_factory=list)
    env_allowlist: list[str] = Field(default_factory=list)
    allowed_write_patterns: list[str] = Field(default_factory=list)
    protected_patterns: list[str] = Field(default_factory=lambda: [".env", ".git/**", "**/__pycache__/**"])
    notes: list[str] = Field(default_factory=list)

    def tool_policy(self) -> ToolPermissionPolicy:
        permissions = {PermissionLevel.READ_ONLY, PermissionLevel.PLAN}
        max_risk = RiskLevel.MEDIUM
        if self.allow_file_write:
            permissions.add(PermissionLevel.WRITE_PATCH)
            max_risk = RiskLevel.HIGH
        if self.allow_shell_commands:
            permissions.add(PermissionLevel.EXECUTION)
            max_risk = RiskLevel.CRITICAL
        if self.allow_network:
            permissions.add(PermissionLevel.NETWORK)
            max_risk = RiskLevel.CRITICAL
        return ToolPermissionPolicy(
            allowed_permissions=permissions,
            allowed_tools=set(self.allowed_tools),
            blocked_tools=set(self.blocked_tools),
            max_risk_level=max_risk,
        )

    @classmethod
    def read_only(cls) -> "AgentPermissionPolicy":
        return cls(
            notes=[
                "Default policy is read-only. The agent may inspect listed artifacts and draft plans, but must not write project files or run shell commands.",
            ]
        )
