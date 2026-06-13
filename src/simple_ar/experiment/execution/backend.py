"""Execution backend protocol and local subprocess implementation."""

from __future__ import annotations

import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol

from simple_ar.experiment.metrics import parse_metric_lines


class ExecutionError(RuntimeError):
    """Raised when an execution request is invalid before process launch."""


@dataclass(frozen=True, slots=True)
class RunRequest:
    command: list[str]
    cwd: Path
    timeout_sec: int
    label: str = "experiment"
    env: dict[str, str] | None = None


@dataclass(frozen=True, slots=True)
class RunResult:
    returncode: int | None
    timed_out: bool
    stdout: str
    stderr: str
    metrics: dict[str, float] = field(default_factory=dict)
    command: list[str] = field(default_factory=list)
    cwd: str = ""
    duration_sec: float = 0.0
    backend: str = "local"
    label: str = "experiment"

    @property
    def status(self) -> str:
        if self.timed_out:
            return "timed_out"
        if self.returncode == 0:
            return "passed"
        return "failed"

    def to_json(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status
        return data


class ExecutionBackend(Protocol):
    name: str

    def run(self, request: RunRequest) -> RunResult:
        """Run one execution request and return a normalized result."""


@dataclass(slots=True)
class LocalExecutionBackend:
    name: str = "local"

    def run(self, request: RunRequest) -> RunResult:
        if request.timeout_sec < 1:
            raise ExecutionError("timeout_sec must be at least 1")
        if not request.cwd.is_dir():
            raise ExecutionError(f"Working directory not found: {request.cwd}")
        if not request.command:
            raise ExecutionError("Execution command is empty")

        started = time.monotonic()
        try:
            completed = subprocess.run(
                request.command,
                cwd=request.cwd,
                capture_output=True,
                text=True,
                timeout=request.timeout_sec,
                check=False,
                env=request.env,
            )
            duration = time.monotonic() - started
            return RunResult(
                returncode=completed.returncode,
                timed_out=False,
                stdout=completed.stdout,
                stderr=completed.stderr,
                metrics=parse_metric_lines(completed.stdout),
                command=list(request.command),
                cwd=str(request.cwd),
                duration_sec=duration,
                backend=self.name,
                label=request.label,
            )
        except subprocess.TimeoutExpired as exc:
            duration = time.monotonic() - started
            stdout = _output_text(exc.stdout)
            stderr = _output_text(exc.stderr)
            if stderr:
                stderr += "\n"
            stderr += f"Timed out after {request.timeout_sec} seconds."
            return RunResult(
                returncode=None,
                timed_out=True,
                stdout=stdout,
                stderr=stderr,
                metrics=parse_metric_lines(stdout),
                command=list(request.command),
                cwd=str(request.cwd),
                duration_sec=duration,
                backend=self.name,
                label=request.label,
            )


def _output_text(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value
