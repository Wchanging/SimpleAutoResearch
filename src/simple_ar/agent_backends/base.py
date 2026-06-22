from __future__ import annotations

from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, Field


class AgentRunRequest(BaseModel):
    """Input to an agent backend invocation."""

    provider: str
    run_dir: Path
    handoff_dir: Path
    workspace_dir: Path | None = None
    timeout_sec: int = 600
    profile: str = ""
    env_allowlist: tuple[str, ...] = ()
    metadata: dict[str, object] = Field(default_factory=dict)


class AgentRunResult(BaseModel):
    """Structured result from an agent backend before SimpleAR validation."""

    provider: str
    status: str
    message: str = ""
    elapsed_sec: float = 0.0
    stdout: str = ""
    stderr: str = ""
    raw_output: str = ""
    artifacts: list[str] = Field(default_factory=list)
    patch_path: str = ""
    generated_files_dir: str = ""
    review_path: str = ""
    result_path: str = ""
    error: str = ""
    metadata: dict[str, object] = Field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status in {"ok", "passed"}


class AgentBackend(Protocol):
    """Minimal protocol for local, fake, and external CLI agent backends."""

    @property
    def name(self) -> str: ...

    def run(self, request: AgentRunRequest) -> AgentRunResult:
        """Run the backend and return untrusted outputs for ingestion."""
        ...
