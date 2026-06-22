from __future__ import annotations

from enum import Enum


class AgentExecutionMode(str, Enum):
    """How much of the implementation loop an external agent may own."""

    MODEL = "model"
    HANDOFF = "handoff"
    DELEGATED_WORKSPACE = "delegated_workspace"


MODEL_PROVIDERS = {"local", "local_llm", "llm"}
EXTERNAL_HARNESS_PROVIDERS = {"codex", "claude", "claude_code", "opencode", "external_cli", "cli"}


def normalize_agent_mode(value: str | None, *, provider: str = "") -> AgentExecutionMode:
    """Normalize the public agent-mode vocabulary.

    ``agent_mode`` is intentionally the only new V2.6.5 mode switch. Provider,
    model, binary, timeout, resource, execution, and safety options stay in the
    existing config sections.
    """

    normalized_provider = normalize_provider(provider)
    raw = (value or "").strip().lower().replace("-", "_")
    if not raw:
        return AgentExecutionMode.MODEL if normalized_provider in MODEL_PROVIDERS else AgentExecutionMode.HANDOFF
    aliases = {
        "text": AgentExecutionMode.MODEL,
        "text_backend": AgentExecutionMode.MODEL,
        "model": AgentExecutionMode.MODEL,
        "model_backend": AgentExecutionMode.MODEL,
        "proposal": AgentExecutionMode.HANDOFF,
        "handoff": AgentExecutionMode.HANDOFF,
        "handoff_backend": AgentExecutionMode.HANDOFF,
        "workspace": AgentExecutionMode.DELEGATED_WORKSPACE,
        "delegated": AgentExecutionMode.DELEGATED_WORKSPACE,
        "delegated_workspace": AgentExecutionMode.DELEGATED_WORKSPACE,
        "delegated_workspace_backend": AgentExecutionMode.DELEGATED_WORKSPACE,
    }
    try:
        return aliases[raw]
    except KeyError as exc:
        valid = ", ".join(mode.value for mode in AgentExecutionMode)
        raise ValueError(f"Unsupported agent_mode `{value}`. Expected one of: {valid}.") from exc


def normalize_provider(value: str | None) -> str:
    return (value or "local").strip().lower().replace("-", "_") or "local"


def validate_agent_mode_for_provider(mode: AgentExecutionMode, *, provider: str) -> None:
    """Fail early when a config asks for a mode this project cannot honor yet."""

    normalized_provider = normalize_provider(provider)
    if mode == AgentExecutionMode.MODEL and normalized_provider in EXTERNAL_HARNESS_PROVIDERS:
        raise ValueError(
            "`agent_mode = \"model\"` is only supported by local/local_llm providers for now. "
            "Use `agent_mode = \"handoff\"` for Codex/Claude/OpenCode CLI backends, or wait "
            "for the delegated workspace backend."
        )
    if mode == AgentExecutionMode.DELEGATED_WORKSPACE:
        raise NotImplementedError(
            "`agent_mode = \"delegated_workspace\"` is recognized as the strong external-agent "
            "harness path, but it is not executable yet. Use `agent_mode = \"handoff\"` until "
            "the delegated workspace runner is implemented."
        )
