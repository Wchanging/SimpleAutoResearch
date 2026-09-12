"""Execution backend protocol and local subprocess implementation."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
import os
from pathlib import Path
from typing import Any, Protocol

from simple_ar.experiment.metrics import parse_metric_lines
from simple_ar.core.process import ProcessSpec, run_process
from simple_ar.core.budget import BudgetLedger


class ExecutionError(RuntimeError):
    """Raised when an execution request is invalid before process launch."""


@dataclass(frozen=True, slots=True)
class RunRequest:
    command: list[str]
    cwd: Path
    timeout_sec: int
    label: str = "experiment"
    env: dict[str, str] | None = None
    output_dir: Path | None = None
    session_id: str = ""
    attempt_id: str = ""


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
    process_record: dict[str, Any] = field(default_factory=dict)

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
    budget_ledger: BudgetLedger | None = field(default=None, repr=False)

    def run(self, request: RunRequest) -> RunResult:
        if request.timeout_sec < 1:
            raise ExecutionError("timeout_sec must be at least 1")
        if not request.cwd.is_dir():
            raise ExecutionError(f"Working directory not found: {request.cwd}")
        if not request.command:
            raise ExecutionError("Execution command is empty")

        spec = ProcessSpec(
            argv=request.command, cwd=request.cwd, timeout_sec=request.timeout_sec,
            env=request.env, output_dir=request.output_dir,
            session_id=request.session_id, attempt_id=request.attempt_id,
        )
        if spec.output_dir:
            invocation_dir = (spec.output_dir / spec.invocation_id).absolute()
            environment = dict(os.environ if request.env is None else request.env)
            environment["SIMPLE_AR_OUTPUT_DIR"] = str(invocation_dir / "outputs")
            spec = replace(spec, output_dir=invocation_dir, env=environment)
        result = run_process(spec, budget_ledger=self.budget_ledger)
        timed_out = result.stop_reason == "timeout"
        stderr = result.stderr
        if timed_out:
            stderr = stderr.rstrip() + f"\nTimed out after {request.timeout_sec} seconds."
        return RunResult(
            returncode=None if timed_out else result.returncode,
            timed_out=timed_out, stdout=result.stdout, stderr=stderr,
            metrics=parse_metric_lines(result.stdout),
            command=list(request.command), cwd=str(request.cwd),
            duration_sec=result.duration_sec, process_record=result.record,
            backend=self.name, label=request.label,
        )
