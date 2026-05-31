from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from simple_ar.metrics import parse_metric_lines


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

    def to_json(self) -> dict[str, Any]:
        """Convert the result into a JSON-serializable dictionary."""
        return {
            "returncode": self.returncode,
            "timed_out": self.timed_out,
            "metrics": dict(self.metrics),
            "command": list(self.command),
        }


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
    try:
        completed = subprocess.run(
            command,
            cwd=script_path.parent,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            check=False,
        )
        return ExperimentRunResult(
            returncode=completed.returncode,
            timed_out=False,
            stdout=completed.stdout,
            stderr=completed.stderr,
            metrics=parse_metric_lines(completed.stdout),
            command=command,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = _output_text(exc.stdout)
        stderr = _output_text(exc.stderr)
        if stderr:
            stderr += "\n"
        stderr += f"Timed out after {timeout_sec} seconds."
        return ExperimentRunResult(
            returncode=None,
            timed_out=True,
            stdout=stdout,
            stderr=stderr,
            metrics=parse_metric_lines(stdout),
            command=command,
        )


def _output_text(value: bytes | str | None) -> str:
    """Normalize subprocess timeout output into text."""
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value
