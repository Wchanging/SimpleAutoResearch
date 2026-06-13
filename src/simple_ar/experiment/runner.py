from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from simple_ar.experiment.execution.backend import LocalExecutionBackend, RunRequest


class ExperimentRunError(RuntimeError):
    """Raised when an experiment script cannot be launched."""


@dataclass(frozen=True)
class ExperimentRunResult:
    """Captured result from running an experiment subprocess.

    Args:
        returncode: Process return code, or ``None`` when the process timed out.
        timed_out: Whether the process exceeded the configured timeout.
        stdout: Captured standard output.
        stderr: Captured standard error.
        metrics: Parsed numeric metrics from stdout.
        command: Command used to launch the script.
    """

    returncode: int | None
    timed_out: bool
    stdout: str
    stderr: str
    metrics: dict[str, float] = field(default_factory=dict)
    command: list[str] = field(default_factory=list)
    duration_sec: float = 0.0
    backend: str = "local"
    cwd: str = ""

    def to_json(self) -> dict[str, Any]:
        """Convert the result into a JSON-serializable dictionary."""
        return {
            "returncode": self.returncode,
            "timed_out": self.timed_out,
            "metrics": dict(self.metrics),
            "command": list(self.command),
            "duration_sec": self.duration_sec,
            "backend": self.backend,
            "cwd": self.cwd,
            "status": self.status,
        }

    @property
    def status(self) -> str:
        if self.timed_out:
            return "timed_out"
        return "passed" if self.returncode == 0 else "failed"


def run_experiment(script_path: Path, *, timeout_sec: int = 30) -> ExperimentRunResult:
    """Run one generated experiment script in a subprocess.

    Args:
        script_path: Path to ``experiment.py``.
        timeout_sec: Maximum runtime before the process is terminated.

    Returns:
        Captured process result and parsed metrics.

    Raises:
        ExperimentRunError: If the script does not exist or timeout is invalid.
    """
    if timeout_sec < 1:
        raise ExperimentRunError("timeout_sec must be at least 1")
    if not script_path.is_file():
        raise ExperimentRunError(f"Experiment script not found: {script_path}")

    command = [sys.executable, script_path.name]
    result = LocalExecutionBackend().run(
        RunRequest(
            command=command,
            cwd=script_path.parent,
            timeout_sec=timeout_sec,
            label="experiment",
        )
    )
    return ExperimentRunResult(
        returncode=result.returncode,
        timed_out=result.timed_out,
        stdout=result.stdout,
        stderr=result.stderr,
        metrics=dict(result.metrics),
        command=list(result.command),
        duration_sec=result.duration_sec,
        backend=result.backend,
        cwd=result.cwd,
    )


__all__ = ["ExperimentRunError", "ExperimentRunResult", "run_experiment"]
