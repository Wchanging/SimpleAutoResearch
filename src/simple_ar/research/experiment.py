"""Small experiment execution boundary for research capabilities.

The code-task workflow remains the strong implementation backend.  This
module only composes the existing execution port with canonical result
normalization so another backend can be substituted without changing the
downstream result shape.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from simple_ar.core.capabilities import CapabilityContext, CapabilityResult
from simple_ar.experiment.execution.backend import (
    ExecutionBackend,
    LocalExecutionBackend,
    RunRequest,
    RunResult,
)
from simple_ar.experiment.execution.diagnosis import (
    compact_diagnosis,
    diagnose_experiment_run,
    render_diagnosis_markdown,
)
from simple_ar.experiment.execution.guards import evaluate_result_guard
from simple_ar.experiment.execution.results import build_canonical_results
from simple_ar.result_analysis.schema import AnalysisContext, AnalysisResult

from .analysis import (
    AnalysisRequest,
    _merge_result_schema_into_analysis_context,
    analyze_results,
)
from .contracts import ResearchExperimentContract
from .synthesis import SynthesisResult


@dataclass(frozen=True, slots=True)
class ExperimentRequest:
    """One executable experiment plus optional result provenance."""

    run: RunRequest
    result_schema: Mapping[str, Any] = field(default_factory=dict)
    experiment_contract: ResearchExperimentContract | Mapping[str, Any] | None = None
    artifacts: Mapping[str, Any] = field(default_factory=dict)
    comparisons: tuple[Mapping[str, Any], ...] = ()
    verdicts: tuple[Mapping[str, Any], ...] = ()
    guard: Mapping[str, Any] | None = None

    def normalized_experiment_contract(self) -> Mapping[str, Any] | None:
        """Return a mapping for canonical results without aliasing contracts."""

        if isinstance(self.experiment_contract, ResearchExperimentContract):
            return self.experiment_contract.to_row()
        return self.experiment_contract

    def normalized_result_schema(self) -> Mapping[str, Any]:
        """Return the explicit schema or a minimal schema from a typed contract.

        A research contract names the metrics that should test its hypothesis,
        but it does not know the execution command or metric direction.  When
        an explicit execution schema is absent, expose those metric names to
        downstream analysis and leave direction unknown.  Historical mapping
        inputs keep their previous empty-schema behavior.
        """

        schema = dict(self.result_schema)
        if schema or not isinstance(self.experiment_contract, ResearchExperimentContract):
            return schema
        metrics = tuple(
            dict.fromkeys(
                str(metric).strip()
                for metric in self.experiment_contract.metrics
                if str(metric).strip()
            )
        )
        if not metrics:
            return schema
        return {
            "primary_metric": metrics[0],
            "required_metrics": list(metrics),
            "direction": "unknown",
        }


def experiment_request_from_synthesis(
    synthesis: SynthesisResult | Mapping[str, Any],
    *,
    run: RunRequest,
    result_schema: Mapping[str, Any] | None = None,
    artifacts: Mapping[str, Any] | None = None,
    comparisons: tuple[Mapping[str, Any], ...] = (),
    verdicts: tuple[Mapping[str, Any], ...] = (),
    guard: Mapping[str, Any] | None = None,
) -> ExperimentRequest:
    """Build an experiment request from an explicit synthesis handoff.

    The adapter only transfers the research-level experiment contract.  It
    does not approve a ``needs_review`` synthesis result, choose a command,
    execute it, retry it, or select a transition; those remain caller-owned
    decisions.  A mapping must be the persisted ``synthesis_result.v1``
    handoff and is restored through the same typed loader used by the session
    layer.
    """

    if isinstance(synthesis, SynthesisResult):
        restored = synthesis
    elif isinstance(synthesis, Mapping):
        restored = SynthesisResult.from_handoff_dict(synthesis)
    else:
        raise TypeError("synthesis must be a SynthesisResult or handoff mapping")
    if restored.experiment_contract is None:
        raise ValueError("Synthesis handoff does not contain an experiment contract.")

    return ExperimentRequest(
        run=run,
        result_schema=dict(result_schema or {}),
        experiment_contract=restored.experiment_contract,
        artifacts=dict(artifacts or {}),
        comparisons=tuple(comparisons),
        verdicts=tuple(verdicts),
        guard=dict(guard) if guard is not None else None,
    )


@dataclass(frozen=True, slots=True)
class ExperimentResult:
    """Execution output and its canonical, reportable representation."""

    run: RunResult
    canonical: dict[str, Any]

    @property
    def status(self) -> str:
        return str(self.canonical.get("status") or self.run.status)

    def to_dict(self) -> dict[str, Any]:
        """Return the canonical results object without duplicating output."""
        return dict(self.canonical)


@dataclass(frozen=True, slots=True)
class ExperimentEvaluation:
    """One execution paired with the analysis of its observed output."""

    execution: ExperimentResult
    analysis: AnalysisResult

    @property
    def status(self) -> str:
        """Preserve execution status as the outcome of the composition."""
        return self.execution.status

    def to_dict(self) -> dict[str, Any]:
        """Return a compact, JSON-ready view of both boundary results."""
        return {
            "schema_version": "experiment_evaluation.v1",
            "status": self.status,
            "execution": self.execution.to_dict(),
            "analysis": self.analysis.model_dump(mode="json"),
        }


def run_experiment(
    request: ExperimentRequest,
    *,
    backend: ExecutionBackend | None = None,
) -> ExperimentResult:
    """Execute one request and normalize its result without writing files."""
    selected_backend = backend or LocalExecutionBackend()
    run_result = selected_backend.run(request.run)
    canonical = build_canonical_results(
        run_result,
        result_schema=request.normalized_result_schema(),
        experiment_contract=request.normalized_experiment_contract(),
        artifacts=request.artifacts,
        comparisons=list(request.comparisons),
        verdicts=list(request.verdicts),
        guard=request.guard,
    )
    return ExperimentResult(run=run_result, canonical=canonical)


def run_experiment_capability(
    *,
    context: CapabilityContext,
    request: ExperimentRequest,
    backend: ExecutionBackend | None = None,
) -> CapabilityResult:
    """Expose one experiment execution through the session boundary.

    Registration stays caller-owned. The adapter persists the existing
    canonical result shape as ``results.json`` plus the captured execution
    streams as declared attempt-local artifacts. It maps every non-passed
    execution to a failed capability result; it never turns a timeout into a
    successful experiment and never retries implicitly.
    """
    result = run_experiment(request, backend=backend)
    stdout_ref = context.store.write_text(
        "execution/stdout.txt",
        result.run.stdout,
        kind="execution_log",
        schema="text.v1",
        producer="research.experiment",
    )
    stderr_ref = context.store.write_text(
        "execution/stderr.txt",
        result.run.stderr,
        kind="execution_log",
        schema="text.v1",
        producer="research.experiment",
    )
    canonical = result.to_dict()
    artifact_paths = dict(canonical.get("artifacts") or {})
    artifact_paths["stdout"] = stdout_ref.path
    artifact_paths["stderr"] = stderr_ref.path
    artifact_paths["guard"] = "guard_report.json"
    artifact_paths["diagnosis"] = "diagnosis.json"
    artifact_paths["diagnosis_markdown"] = "diagnosis.md"
    canonical["artifacts"] = artifact_paths
    guard = (
        dict(request.guard)
        if request.guard is not None
        else evaluate_result_guard(
            canonical,
            result_schema=request.normalized_result_schema(),
        )
    )
    canonical["guard"] = guard
    diagnosis = diagnose_experiment_run(
        results=canonical,
        guard_report=guard,
        result_schema=request.normalized_result_schema(),
        stdout_tail=result.run.stdout,
        stderr_tail=result.run.stderr,
    )
    canonical["diagnosis"] = compact_diagnosis(diagnosis)
    output = context.store.write_json(
        "results.json",
        canonical,
        kind="experiment_result",
        schema="canonical_results.2.5",
        producer="research.experiment",
    )
    guard_ref = context.store.write_json(
        "guard_report.json",
        guard,
        kind="validation",
        schema="experiment_guard.v1",
        producer="research.experiment",
    )
    diagnosis_ref = context.store.write_json(
        "diagnosis.json",
        diagnosis,
        kind="diagnosis",
        schema="experiment_diagnosis.v1",
        producer="research.experiment",
    )
    diagnosis_markdown_ref = context.store.write_text(
        "diagnosis.md",
        render_diagnosis_markdown(diagnosis),
        kind="diagnosis",
        schema="markdown.v1",
        producer="research.experiment",
    )
    diagnostics: list[str] = []
    execution_status = result.status
    guard_failed = str(guard.get("status") or "").strip().lower() == "failed"
    if execution_status != "passed":
        if result.status == "timed_out":
            diagnostics.append("Experiment execution timed out.")
        else:
            diagnostics.append(f"Experiment execution status: {result.status}.")
        if result.run.returncode is not None:
            diagnostics.append(f"Process exited with code {result.run.returncode}.")
    elif guard_failed:
        diagnostics.append("Experiment result guard failed.")
    if guard_failed:
        diagnostics.extend(
            str(issue.get("message") or issue.get("code") or "Guard check failed.")
            for issue in guard.get("issues", [])
            if isinstance(issue, Mapping) and str(issue.get("severity") or "").lower() == "error"
        )
    return CapabilityResult(
        status="completed" if execution_status == "passed" and not guard_failed else "failed",
        artifacts=(
            output,
            stdout_ref,
            stderr_ref,
            guard_ref,
            diagnosis_ref,
            diagnosis_markdown_ref,
        ),
        diagnostics=tuple(diagnostics),
        usage={
            "duration_sec": result.run.duration_sec,
            "metric_count": len(result.run.metrics),
        },
        provenance={
            "capability": "experiment",
            "backend": result.run.backend,
            "result_schema": str(result.canonical.get("schema_version", "")),
        },
    )


def run_and_analyze(
    request: ExperimentRequest,
    analysis_context: AnalysisContext | Mapping[str, Any],
    *,
    backend: ExecutionBackend | None = None,
    client: Any | None = None,
    use_llm: bool = False,
    output_dir: Path | None = None,
    label: str = "experiment-analysis",
) -> ExperimentEvaluation:
    """Run an experiment and analyze the observed result in one composition.

    The observed metrics and canonical execution record are added to a copied
    analysis context.  The function does not retry, repair, choose a research
    transition, or write files unless ``output_dir`` is explicitly supplied.
    """

    execution = run_experiment(request, backend=backend)
    context = (
        analysis_context
        if isinstance(analysis_context, AnalysisContext)
        else AnalysisContext.model_validate(analysis_context)
    )
    context = _merge_result_schema_into_analysis_context(
        context,
        request.normalized_result_schema(),
    )
    metrics = dict(context.metrics)
    metrics.update(
        {
            str(name): float(value)
            for name, value in execution.run.metrics.items()
            if isinstance(value, (int, float)) and not isinstance(value, bool)
        }
    )
    project_results = dict(context.project_results)
    project_results["execution_result"] = execution.canonical
    analysis = analyze_results(
        AnalysisRequest(
            context=context.model_copy(update={"metrics": metrics, "project_results": project_results}),
            use_llm=use_llm,
            output_dir=output_dir,
            label=label,
        ),
        client=client,
    )
    return ExperimentEvaluation(execution=execution, analysis=analysis)


__all__ = [
    "ExperimentRequest",
    "experiment_request_from_synthesis",
    "ExperimentResult",
    "ExperimentEvaluation",
    "run_experiment",
    "run_experiment_capability",
    "run_and_analyze",
]
