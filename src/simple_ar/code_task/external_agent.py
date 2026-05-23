from __future__ import annotations

import fnmatch
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from simple_ar.artifacts import write_json
from simple_ar.code_task.editor import (
    ApplyEditRequest,
    ApplyEditResult,
    EditRequest,
    EditResult,
    editor_metadata,
)


EXTERNAL_AGENT_BACKEND = "external_agent"
SUPPORTED_EXTERNAL_AGENT_PROVIDERS = ("codex", "claude_code", "opencode")
DEFAULT_BLOCKED_READ_PATTERNS = (
    ".env",
    ".env.*",
    "**/.env",
    "**/.env.*",
    "**/*secret*",
    "**/*credential*",
    "**/*token*",
    "**/*apikey*",
    "**/*api_key*",
    "**/*.pem",
    "**/*.key",
    "**/id_rsa",
    "**/id_ed25519",
    "$HOME/**",
    "~/**",
    "%USERPROFILE%/**",
)


class ExternalAgentDisabledError(RuntimeError):
    """Raised when the reserved external-agent backend is invoked."""


class ExternalAgentConfigError(ValueError):
    """Raised when an external-agent adapter spec is invalid."""


@dataclass(frozen=True)
class ExternalAgentPermissionPolicy:
    """Permission policy for future Codex/Claude/OpenCode editor adapters.

    The policy is intentionally conservative. External agents may eventually
    edit files in the isolated workspace, but SimpleAutoResearch should still
    own diff capture, path checks, validation, benchmarks, and reporting.
    """

    blocked_read_patterns: tuple[str, ...] = DEFAULT_BLOCKED_READ_PATTERNS
    allow_shell_commands: bool = False
    allow_network: bool = False
    allow_dependency_install: bool = False
    allow_write_outside_workspace: bool = False
    require_review: bool = True
    capture_diff: bool = True
    timeout_sec: int = 600
    max_turns: int = 20


@dataclass(frozen=True)
class ExternalAgentAdapterSpec:
    """Configuration draft for an external editor backend.

    ``enabled`` stays false by default. V2.2 can build and record invocation
    plans, but it does not launch external coding agents yet.
    """

    provider: str = "codex"
    binary_path: str = ""
    model: str = ""
    extra_args: tuple[str, ...] = ()
    enabled: bool = False
    permissions: ExternalAgentPermissionPolicy = field(
        default_factory=ExternalAgentPermissionPolicy
    )


@dataclass(frozen=True)
class ExternalAgentInvocationPlan:
    """Reviewable plan for a future external-agent invocation."""

    backend: str
    provider: str
    enabled: bool
    binary: str
    binary_found: bool
    cwd: str
    command_preview: tuple[str, ...]
    prompt_path: str
    log_path: str
    diff_path: str
    permissions: dict[str, Any]
    blocked_read_patterns: tuple[str, ...]
    warnings: tuple[str, ...]
    status: str

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""

        return {
            "backend": self.backend,
            "provider": self.provider,
            "enabled": self.enabled,
            "binary": self.binary,
            "binary_found": self.binary_found,
            "cwd": self.cwd,
            "command_preview": list(self.command_preview),
            "prompt_path": self.prompt_path,
            "log_path": self.log_path,
            "diff_path": self.diff_path,
            "permissions": dict(self.permissions),
            "blocked_read_patterns": list(self.blocked_read_patterns),
            "warnings": list(self.warnings),
            "status": self.status,
        }


class ExternalAgentEditorBackend:
    """Reserved editor backend for Codex/Claude/OpenCode style adapters.

    The backend currently performs design-time planning only. It never launches
    an external process in V2.2, which keeps the default code-task path stable
    while giving future adapters a concrete contract to implement.
    """

    name = EXTERNAL_AGENT_BACKEND

    def __init__(self, spec: ExternalAgentAdapterSpec | None = None) -> None:
        self.spec = spec or ExternalAgentAdapterSpec()

    def build_invocation_plan(self, request: EditRequest) -> ExternalAgentInvocationPlan:
        """Build a reviewable external-agent invocation plan."""

        return build_external_agent_invocation_plan(request, self.spec)

    def write_invocation_plan(self, request: EditRequest) -> Path:
        """Write the planned invocation artifact without running the agent."""

        plan = self.build_invocation_plan(request)
        path = request.context.meta_dir / "external_agent_invocation_plan.json"
        write_json(path, plan.to_dict())
        return path

    def propose(self, request: EditRequest) -> EditResult:
        """Record the external-agent plan, then refuse execution in V2.2."""

        plan_path = self.write_invocation_plan(request)
        raise ExternalAgentDisabledError(
            "The external_agent editor backend is designed but not enabled. "
            f"Invocation plan written to {plan_path}."
        )

    def apply(self, request: ApplyEditRequest) -> ApplyEditResult:
        raise ExternalAgentDisabledError(
            "The external_agent editor backend cannot apply edits directly. "
            "Future adapters must return a captured diff/proposal that passes "
            "SimpleAutoResearch review, validation, and benchmark gates."
        )


def build_external_agent_invocation_plan(
    request: EditRequest,
    spec: ExternalAgentAdapterSpec,
) -> ExternalAgentInvocationPlan:
    """Return a non-executing plan for a future external agent call."""

    provider = normalize_external_agent_provider(spec.provider)
    binary = spec.binary_path.strip() or _default_binary(provider)
    binary_found = bool(shutil.which(binary)) if not Path(binary).is_absolute() else Path(binary).exists()
    prompt_path = "code_task/meta/external_agent_prompt.md"
    log_path = "code_task/meta/external_agent_log.txt"
    diff_path = "code_task/meta/external_agent.diff"
    permissions = _permission_dict(spec.permissions, request)
    warnings = _invocation_warnings(spec, binary_found=binary_found)
    return ExternalAgentInvocationPlan(
        backend=EXTERNAL_AGENT_BACKEND,
        provider=provider,
        enabled=spec.enabled,
        binary=binary,
        binary_found=binary_found,
        cwd="code_task/workspace",
        command_preview=_command_preview(
            provider=provider,
            binary=binary,
            model=spec.model,
            extra_args=spec.extra_args,
            permissions=spec.permissions,
            prompt_path=prompt_path,
        ),
        prompt_path=prompt_path,
        log_path=log_path,
        diff_path=diff_path,
        permissions=permissions,
        blocked_read_patterns=_blocked_patterns(spec.permissions, request),
        warnings=warnings,
        status="disabled" if not spec.enabled else "planned_not_executable",
    )


def normalize_external_agent_provider(provider: str) -> str:
    """Normalize provider aliases used in configs and docs."""

    value = (provider or "").strip().lower().replace("-", "_")
    aliases = {
        "claude": "claude_code",
        "claude_cli": "claude_code",
        "claude_code": "claude_code",
        "codex": "codex",
        "openai_codex": "codex",
        "opencode": "opencode",
        "open_code": "opencode",
    }
    normalized = aliases.get(value)
    if normalized not in SUPPORTED_EXTERNAL_AGENT_PROVIDERS:
        raise ExternalAgentConfigError(
            "Unsupported external agent provider. Expected one of: "
            + ", ".join(SUPPORTED_EXTERNAL_AGENT_PROVIDERS)
        )
    return normalized


def is_blocked_external_agent_read_path(
    path: str | Path,
    *,
    blocked_patterns: tuple[str, ...] = DEFAULT_BLOCKED_READ_PATTERNS,
) -> bool:
    """Return true when a path must not be exposed to an external agent."""

    text = str(path).replace("\\", "/").strip()
    if not text:
        return False
    lowered = text.lower()
    if lowered.startswith(("~/", "$home/", "%userprofile%/")):
        return True
    name = Path(text).name.lower()
    for pattern in blocked_patterns:
        normalized = pattern.replace("\\", "/").lower()
        if normalized in {"$home/**", "~/**", "%userprofile%/**"}:
            continue
        if fnmatch.fnmatch(lowered, normalized):
            return True
        if fnmatch.fnmatch(name, normalized):
            return True
        if not normalized.startswith("**/") and fnmatch.fnmatch(lowered, f"**/{normalized}"):
            return True
    return False


def external_agent_design_metadata(
    request: EditRequest | None = None,
    *,
    provider: str = "codex",
) -> dict[str, Any]:
    """Return normalized metadata for reserved external-agent artifacts."""

    return editor_metadata(
        backend=EXTERNAL_AGENT_BACKEND,
        request=request,
        extra={
            "provider": normalize_external_agent_provider(provider),
            "enabled": False,
            "execution": "reserved_design_only",
        },
    )


def _default_binary(provider: str) -> str:
    if provider == "claude_code":
        return "claude"
    if provider == "opencode":
        return "opencode"
    return "codex"


def _command_preview(
    *,
    provider: str,
    binary: str,
    model: str,
    extra_args: tuple[str, ...],
    permissions: ExternalAgentPermissionPolicy,
    prompt_path: str,
) -> tuple[str, ...]:
    prompt = f"<prompt-from:{prompt_path}>"
    if provider == "codex":
        cmd = [binary, "exec", prompt, "--sandbox", "workspace-write", "--json", "-C", "code_task/workspace"]
        if model:
            cmd.extend(["-m", model])
    elif provider == "claude_code":
        allowed_tools = "Read Edit Write"
        if permissions.allow_shell_commands:
            allowed_tools += " Bash"
        cmd = [
            binary,
            "-p",
            prompt,
            "--output-format",
            "text",
            "--allowed-tools",
            allowed_tools,
            "--add-dir",
            "code_task/workspace",
        ]
        if model:
            cmd.extend(["--model", model])
    else:
        cmd = [binary, "run", "--format", "json", prompt]
        if model:
            cmd.extend(["-m", model])
    cmd.extend(extra_args)
    return tuple(cmd)


def _permission_dict(
    permissions: ExternalAgentPermissionPolicy,
    request: EditRequest,
) -> dict[str, Any]:
    return {
        "writable_root": "code_task/workspace",
        "allow_write_outside_workspace": permissions.allow_write_outside_workspace,
        "allow_shell_commands": permissions.allow_shell_commands or request.safety.allow_command_execution,
        "allow_network": permissions.allow_network or request.safety.allow_network,
        "allow_dependency_install": permissions.allow_dependency_install,
        "require_review": permissions.require_review,
        "capture_diff": permissions.capture_diff,
        "timeout_sec": permissions.timeout_sec,
        "max_turns": permissions.max_turns,
    }


def _blocked_patterns(
    permissions: ExternalAgentPermissionPolicy,
    request: EditRequest,
) -> tuple[str, ...]:
    patterns = tuple(permissions.blocked_read_patterns or DEFAULT_BLOCKED_READ_PATTERNS)
    if request.safety.blocked_read_patterns:
        patterns = tuple(dict.fromkeys((*patterns, *request.safety.blocked_read_patterns)))
    return patterns


def _invocation_warnings(
    spec: ExternalAgentAdapterSpec,
    *,
    binary_found: bool,
) -> tuple[str, ...]:
    warnings: list[str] = [
        "External agent execution is reserved and disabled in this version.",
        "Any future external-agent diff must pass SimpleAutoResearch review, validation, and benchmark gates.",
    ]
    if spec.enabled:
        warnings.append(
            "Spec requested enabled=true, but V2.2 still treats the backend as planned_not_executable."
        )
    if not binary_found:
        warnings.append(f"Agent binary was not found: {_default_binary(normalize_external_agent_provider(spec.provider)) if not spec.binary_path else spec.binary_path}")
    if spec.permissions.allow_shell_commands:
        warnings.append("Shell command execution would require explicit user approval.")
    if spec.permissions.allow_network:
        warnings.append("Network access would require explicit user approval.")
    return tuple(warnings)
