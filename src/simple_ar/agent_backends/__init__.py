"""External-agent backend contracts and handoff helpers."""

from simple_ar.agent_backends.base import AgentBackend, AgentRunRequest, AgentRunResult
from simple_ar.agent_backends.external_cli import ExternalCliBackend, ExternalCliBackendConfig
from simple_ar.agent_backends.factory import create_agent_backend, is_external_agent_provider
from simple_ar.agent_backends.fake import FakeAgentBackend
from simple_ar.agent_backends.handoff import (
    AgentHandoffPackage,
    build_code_task_handoff,
    build_greenfield_handoff,
    create_agent_handoff,
    ingest_agent_outputs,
)
from simple_ar.agent_backends.local_llm import LocalLlmAgentBackend
from simple_ar.agent_backends.modes import AgentExecutionMode, normalize_agent_mode, validate_agent_mode_for_provider
from simple_ar.agent_backends.policy import AgentPermissionPolicy

__all__ = [
    "AgentBackend",
    "AgentExecutionMode",
    "AgentHandoffPackage",
    "AgentPermissionPolicy",
    "AgentRunRequest",
    "AgentRunResult",
    "ExternalCliBackend",
    "ExternalCliBackendConfig",
    "FakeAgentBackend",
    "LocalLlmAgentBackend",
    "build_code_task_handoff",
    "build_greenfield_handoff",
    "create_agent_handoff",
    "create_agent_backend",
    "ingest_agent_outputs",
    "is_external_agent_provider",
    "normalize_agent_mode",
    "validate_agent_mode_for_provider",
]
