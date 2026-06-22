from __future__ import annotations

import fnmatch
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from simple_ar.agent_backends import (
    AgentPermissionPolicy,
    AgentRunRequest,
    build_code_task_handoff,
    create_agent_backend,
    ingest_agent_outputs,
    normalize_agent_mode,
    validate_agent_mode_for_provider,
)
from simple_ar.core.artifacts import write_json
from simple_ar.code_task.editing.editor import (
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

    ``enabled`` stays false by default. When enabled explicitly, the adapter
    launches an external CLI in a run-local handoff directory and ingests only
    proposal artifacts for later SimpleAutoResearch validation.
    """

    provider: str = "codex"
    agent_mode: str = "handoff"
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
    agent_mode: str
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
            "agent_mode": self.agent_mode,
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
    """Editor backend for Codex/Claude/OpenCode style adapters.

    The default remains design-time planning only. When ``enabled=True`` the
    backend launches the selected external adapter inside a run-local handoff
    directory and ingests untrusted proposal artifacts for later validation.
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
        """Record the external-agent plan and optionally run the adapter."""

        plan_path = self.write_invocation_plan(request)
        if self.spec.enabled:
            return self._run_enabled_backend(request, plan_path)
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

    def _run_enabled_backend(self, request: EditRequest, plan_path: Path) -> EditResult:
        workspace_rel = _workspace_rel(request)
        policy = AgentPermissionPolicy(
            allow_file_write=True,
            allow_shell_commands=self.spec.permissions.allow_shell_commands,
            allow_network=self.spec.permissions.allow_network,
            allowed_write_patterns=[
                f"{workspace_rel}/**",
                "proposed_edits.json",
                "patch.diff",
                "review.md",
            ],
            protected_patterns=list(self.spec.permissions.blocked_read_patterns),
            notes=[
                "External code-task adapters must produce proposals only.",
                "Do not apply edits directly; SimpleAutoResearch will validate and apply reviewed proposals.",
                f"Editable project root: {workspace_rel}",
            ],
        )
        package = build_code_task_handoff(
            request.context.run_dir,
            name=f"code-task-{normalize_external_agent_provider(self.spec.provider)}",
            task_text=request.context.task_text,
            permission_policy=policy,
        )
        provider = normalize_external_agent_provider(self.spec.provider)
        agent_mode = normalize_agent_mode(self.spec.agent_mode, provider=provider)
        validate_agent_mode_for_provider(agent_mode, provider=provider)
        backend = create_agent_backend(
            provider,
            enabled=True,
            model=self.spec.model or None,
            timeout_sec=self.spec.permissions.timeout_sec,
            binary=self.spec.binary_path or None,
            extra_args=self.spec.extra_args,
        )
        result = backend.run(
            AgentRunRequest(
                provider=provider,
                run_dir=request.context.run_dir,
                handoff_dir=package.handoff_dir,
                workspace_dir=request.context.workspace_dir,
                timeout_sec=self.spec.permissions.timeout_sec,
                metadata={
                    "mode": "code_task",
                    "agent_mode": agent_mode.value,
                    "invocation_plan": str(plan_path),
                },
            )
        )
        ingestion = ingest_agent_outputs(run_dir=request.context.run_dir, handoff_dir=package.handoff_dir)
        proposal = package.handoff_dir / "proposed_edits.json"
        if not result.ok or not proposal.is_file():
            raise ExternalAgentDisabledError(
                "External agent did not produce a usable proposed_edits.json. "
                f"Status={result.status}; see {result.result_path or package.handoff_dir}."
            )
        return EditResult(
            backend=EXTERNAL_AGENT_BACKEND,
            run_dir=request.context.run_dir,
            proposal_path=proposal,
            mode="external_agent",
            edit_count=0,
            metadata=editor_metadata(
                backend=EXTERNAL_AGENT_BACKEND,
                request=request,
                extra={
                    "provider": provider,
                    "agent_mode": agent_mode.value,
                    "enabled": True,
                    "agent_status": result.status,
                    "handoff_dir": package.handoff_dir.relative_to(request.context.run_dir).as_posix(),
                    "ingestion": ingestion,
                },
            ),
        )


def build_external_agent_invocation_plan(
    request: EditRequest,
    spec: ExternalAgentAdapterSpec,
) -> ExternalAgentInvocationPlan:
    """Return a non-executing plan for a future external agent call."""

    provider = normalize_external_agent_provider(spec.provider)
    agent_mode = normalize_agent_mode(spec.agent_mode, provider=provider)
    binary = spec.binary_path.strip() or _default_binary(provider)
    binary_found = bool(shutil.which(binary)) if not Path(binary).is_absolute() else Path(binary).exists()
    workspace_rel = _workspace_rel(request)
    prompt_path = "code_task/meta/external_agent_prompt.md"
    log_path = "code_task/meta/external_agent_log.txt"
    diff_path = "code_task/meta/external_agent.diff"
    permissions = _permission_dict(spec.permissions, request)
    warnings = _invocation_warnings(spec, binary_found=binary_found)
    return ExternalAgentInvocationPlan(
        backend=EXTERNAL_AGENT_BACKEND,
        provider=provider,
        agent_mode=agent_mode.value,
        enabled=spec.enabled,
        binary=binary,
        binary_found=binary_found,
        cwd=workspace_rel,
        command_preview=_command_preview(
            provider=provider,
            binary=binary,
            model=spec.model,
            extra_args=spec.extra_args,
            permissions=spec.permissions,
            prompt_path=prompt_path,
            workspace_rel=workspace_rel,
        ),
        prompt_path=prompt_path,
        log_path=log_path,
        diff_path=diff_path,
        permissions=permissions,
        blocked_read_patterns=_blocked_patterns(spec.permissions, request),
        warnings=warnings,
        status="disabled" if not spec.enabled else "enabled_requires_review",
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
    agent_mode: str = "handoff",
) -> dict[str, Any]:
    """Return normalized metadata for reserved external-agent artifacts."""

    return editor_metadata(
        backend=EXTERNAL_AGENT_BACKEND,
        request=request,
        extra={
            "provider": normalize_external_agent_provider(provider),
            "agent_mode": normalize_agent_mode(agent_mode, provider=provider).value,
            "enabled": False,
            "execution": "handoff_adapter",
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
    workspace_rel: str,
) -> tuple[str, ...]:
    prompt = f"<prompt-from:{prompt_path}>"
    if provider == "codex":
        cmd = [binary, "exec", "--sandbox", "workspace-write", "-C", workspace_rel]
        if model:
            cmd.extend(["-m", model])
        cmd.append(prompt)
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
            workspace_rel,
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
        "writable_root": _workspace_rel(request),
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
        "External agent proposals must pass SimpleAutoResearch review, validation, and benchmark gates.",
    ]
    if spec.enabled:
        warnings.append("Spec requested enabled=true; execution is allowed only inside the handoff boundary.")
    if not binary_found:
        warnings.append(f"Agent binary was not found: {_default_binary(normalize_external_agent_provider(spec.provider)) if not spec.binary_path else spec.binary_path}")
    if spec.permissions.allow_shell_commands:
        warnings.append("Shell command execution would require explicit user approval.")
    if spec.permissions.allow_network:
        warnings.append("Network access would require explicit user approval.")
    return tuple(warnings)


def _workspace_rel(request: EditRequest) -> str:
    """Return the run-relative editable project root for external-agent contracts."""
    try:
        return request.context.workspace_dir.resolve().relative_to(request.context.run_dir.resolve()).as_posix()
    except ValueError:
        return str(request.context.workspace_dir)
