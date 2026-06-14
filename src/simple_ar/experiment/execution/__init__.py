"""Execution primitives for experiment and code-task runs."""

from simple_ar.experiment.execution.backend import (
    ExecutionBackend,
    LocalExecutionBackend,
    RunRequest,
    RunResult,
)
from simple_ar.experiment.execution.guards import evaluate_result_guard
from simple_ar.experiment.execution.diagnosis import diagnose_experiment_run, render_diagnosis_markdown
from simple_ar.experiment.execution.results import build_canonical_results

__all__ = [
    "ExecutionBackend",
    "LocalExecutionBackend",
    "RunRequest",
    "RunResult",
    "build_canonical_results",
    "diagnose_experiment_run",
    "evaluate_result_guard",
    "render_diagnosis_markdown",
]
