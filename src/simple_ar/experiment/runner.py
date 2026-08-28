from __future__ import annotations

import sys
from pathlib import Path

from simple_ar.experiment.execution.backend import LocalExecutionBackend, RunRequest, RunResult


class ExperimentRunError(RuntimeError):
    """Raised when an experiment script cannot be launched."""


ExperimentRunResult = RunResult


def run_experiment(script_path: Path, *, timeout_sec: int = 30) -> RunResult:
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
    return result


__all__ = ["ExperimentRunError", "ExperimentRunResult", "run_experiment"]
