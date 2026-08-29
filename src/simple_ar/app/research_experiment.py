"""User-facing composition from a research direction to an analyzed run.

The application layer owns only the handoff order and its small policy. The
experiment backend, result normalization, and result-analysis service remain
the existing replaceable implementations.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any, Mapping

from simple_ar.core import (
    ArtifactRef,
    AttemptManifest,
    BudgetState,
    CapabilityRegistry,
    DecisionRecord,
    SessionController,
)
from simple_ar.experiment.execution.backend import ExecutionBackend, RunRequest
from simple_ar.research.analysis import AnalysisHandoff
from simple_ar.research.experiment import ExperimentRequest
from simple_ar.research.registry import register_research_capabilities
from simple_ar.research.synthesis import SynthesisResult
from simple_ar.result_analysis.schema import AnalysisContext, AnalysisResult


@dataclass(frozen=True, slots=True)
class ResearchExperimentSessionRequest:
    """Inputs for one bounded synthesis-to-execution session."""

    topic: str
    session_root: Path
    synthesis_file: Path
    command: tuple[str, ...]
    cwd: Path
    timeout_sec: int = 300
    result_schema: Mapping[str, Any] = field(default_factory=dict)
    label: str = "research-experiment"
    env: Mapping[str, str] | None = None

    def __post_init__(self) -> None:
        if not self.topic.strip():
            raise ValueError("ResearchExperimentSessionRequest.topic cannot be empty.")
        if not self.command or any(not str(item).strip() for item in self.command):
            raise ValueError("ResearchExperimentSessionRequest.command cannot be empty.")
        if self.timeout_sec < 1:
            raise ValueError("ResearchExperimentSessionRequest.timeout_sec must be positive.")
        if not self.label.strip():
            raise ValueError("ResearchExperimentSessionRequest.label cannot be empty.")
        object.__setattr__(self, "session_root", Path(self.session_root))
        object.__setattr__(self, "synthesis_file", Path(self.synthesis_file))
        object.__setattr__(self, "cwd", Path(self.cwd))
        object.__setattr__(self, "command", tuple(str(item) for item in self.command))
        object.__setattr__(self, "result_schema", dict(self.result_schema))
        object.__setattr__(
            self,
            "env",
            dict(self.env) if self.env is not None else None,
        )


@dataclass(frozen=True, slots=True)
class ResearchExperimentSessionResult:
    """Persisted execution and analysis outputs from one session."""

    session_root: Path
    synthesis: SynthesisResult
    execution: Mapping[str, Any]
    analysis: AnalysisResult
    source_ref: ArtifactRef
    execution_ref: ArtifactRef
    analysis_ref: ArtifactRef
    attempts: tuple[AttemptManifest, ...]
    decisions: tuple[DecisionRecord, ...]

    @property
    def status(self) -> str:
        execution_status = str(self.execution.get("status") or "unknown")
        if execution_status == "passed" and self.analysis.status == "passed":
            return "completed"
        if self.analysis.status == "passed":
            return "partial"
        return self.analysis.status

    @property
    def execution_path(self) -> Path:
        return self.session_root / self.execution_ref.path

    @property
    def analysis_path(self) -> Path:
        return self.session_root / self.analysis_ref.path


class ResearchExperimentSessionError(RuntimeError):
    """Raised when a direction handoff cannot safely reach execution."""


@dataclass(frozen=True, slots=True)
class _ExperimentSteps:
    """Internal execution-to-analysis handoff within an existing controller."""

    execution: dict[str, Any]
    analysis: AnalysisResult
    execution_ref: ArtifactRef
    analysis_ref: ArtifactRef


def run_research_experiment_session(
    request: ResearchExperimentSessionRequest,
    *,
    backend: ExecutionBackend | None = None,
) -> ResearchExperimentSessionResult:
    """Execute and analyze one explicit synthesis handoff.

    A failed execution is still passed to analysis so its diagnostics remain
    evidence. This function does not retry, repair, choose a better result, or
    turn a below-target metric into a success.
    """

    synthesis = _load_synthesis(request.synthesis_file)
    if synthesis.status != "ready":
        raise ResearchExperimentSessionError(
            f"Synthesis handoff status is {synthesis.status!r}; review it before execution."
        )
    if synthesis.experiment_contract is None:
        raise ResearchExperimentSessionError(
            "Synthesis handoff has no experiment contract; inspect the source handoff."
        )

    request.session_root.mkdir(parents=True, exist_ok=True)
    registry = CapabilityRegistry()
    register_research_capabilities(registry, names=("experiment", "analysis"))
    controller = SessionController.create(
        request.session_root,
        session_id=request.session_root.name,
        topic=request.topic,
        profile="experiment",
        registry=registry,
        budget=BudgetState(max_attempts=3, max_no_progress=2),
    )

    source_ref = controller.store.write_json(
        "inputs/synthesis.json",
        {
            "schema_version": "experiment_input.v1",
            "topic": request.topic,
            "source_file": str(request.synthesis_file),
            "synthesis": synthesis.to_handoff_dict(),
        },
        kind="synthesis_input",
        schema="experiment_input.v1",
        producer="research.experiment.session",
    )
    steps = _run_experiment_steps(
        controller,
        source_ref=source_ref,
        synthesis=synthesis,
        run_request=RunRequest(
            command=list(request.command),
            cwd=request.cwd,
            timeout_sec=request.timeout_sec,
            label=request.label,
            env=dict(request.env) if request.env is not None else None,
        ),
        result_schema=request.result_schema,
        analysis_context=build_experiment_analysis_context(
            topic=request.topic,
            task_id=request.session_root.name,
            source_file=request.synthesis_file,
            synthesis=synthesis,
            result_schema=request.result_schema,
        ),
        analysis_next_capability=None,
        backend=backend,
    )
    return ResearchExperimentSessionResult(
        session_root=request.session_root,
        synthesis=synthesis,
        execution=steps.execution,
        analysis=steps.analysis,
        source_ref=source_ref,
        execution_ref=steps.execution_ref,
        analysis_ref=steps.analysis_ref,
        attempts=controller.list_attempts(),
        decisions=tuple(controller.manifest.decisions),
    )


def _run_experiment_steps(
    controller: SessionController,
    *,
    source_ref: ArtifactRef,
    synthesis: SynthesisResult,
    run_request: RunRequest,
    result_schema: Mapping[str, Any],
    analysis_context: AnalysisContext,
    analysis_next_capability: str | None,
    backend: ExecutionBackend | None,
) -> _ExperimentSteps:
    """Run experiment and analysis attempts in a caller-owned session."""

    experiment_request = ExperimentRequest(
        run=run_request,
        result_schema=result_schema,
        experiment_contract=synthesis.experiment_contract,
    )
    controller.execute(
        "experiment",
        attempt_id="experiment-001",
        inputs=(source_ref,),
        next_capability="analysis",
        request=experiment_request,
        backend=backend,
    )
    execution_ref = controller.attempt_output_ref(
        "experiment-001",
        kind="experiment_result",
        schema="canonical_results.2.5",
    )
    execution = controller.store.read_json(execution_ref)
    if not isinstance(execution, Mapping):
        raise ResearchExperimentSessionError(
            f"Execution output is not a JSON object; inspect {controller.store.root}."
        )

    controller.execute(
        "analysis",
        attempt_id="analysis-001",
        inputs=(execution_ref,),
        next_capability=analysis_next_capability,
        result_ref=execution_ref,
        analysis_context=analysis_context,
    )
    analysis_ref = controller.attempt_output_ref(
        "analysis-001",
        kind="analysis_result",
        schema="analysis_handoff.v1",
    )
    analysis_payload = controller.store.read_json(analysis_ref)
    if not isinstance(analysis_payload, Mapping):
        raise ResearchExperimentSessionError(
            f"Analysis output is not a JSON object; inspect {controller.store.root}."
        )
    return _ExperimentSteps(
        execution=dict(execution),
        analysis=AnalysisHandoff.from_handoff_dict(analysis_payload).analysis,
        execution_ref=execution_ref,
        analysis_ref=analysis_ref,
    )


def _load_synthesis(path: Path) -> SynthesisResult:
    if not path.is_file():
        raise ResearchExperimentSessionError(f"Synthesis handoff not found: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ResearchExperimentSessionError(
            f"Could not read synthesis handoff {path}: {exc}"
        ) from exc
    if not isinstance(payload, Mapping):
        raise ResearchExperimentSessionError("Synthesis handoff must be a JSON object.")
    if payload.get("schema_version") == "research_brief.v1":
        payload = payload.get("synthesis")
    if not isinstance(payload, Mapping):
        raise ResearchExperimentSessionError(
            "Research brief handoff has no synthesis object."
        )
    try:
        return SynthesisResult.from_handoff_dict(payload)
    except (TypeError, ValueError, KeyError) as exc:
        raise ResearchExperimentSessionError(
            f"Invalid synthesis handoff {path}: {exc}"
        ) from exc


def build_experiment_analysis_context(
    *,
    topic: str,
    task_id: str,
    source_file: Path,
    synthesis: SynthesisResult,
    result_schema: Mapping[str, Any],
) -> AnalysisContext:
    contract = synthesis.experiment_contract
    assert contract is not None
    schema = dict(result_schema)
    required = schema.get("required_metrics")
    required_names = [
        str(item).strip()
        for item in required
        if str(item).strip()
    ] if isinstance(required, (list, tuple)) else []
    primary = str(schema.get("primary_metric") or "").strip()
    metric_names = list(dict.fromkeys(([primary] if primary else []) + required_names))
    directions = schema.get("metric_directions")
    directions = directions if isinstance(directions, Mapping) else {}
    expected_metrics = [
        {
            "name": name,
            "direction": _normalize_direction(
                directions.get(name) or schema.get("direction")
            ),
        }
        for name in metric_names
    ]
    return AnalysisContext(
        task_id=task_id,
        title=topic,
        hypotheses=[
            {
                "id": contract.contract_id or "hypothesis-1",
                "statement": contract.hypothesis,
            }
        ],
        expected_metrics=expected_metrics,
        metric_directions={
            name: _normalize_direction(directions.get(name) or schema.get("direction"))
            for name in metric_names
        },
        task_contract={"experiment_contract": contract.to_row()},
        metadata={"synthesis_file": str(source_file)},
    )


def _normalize_direction(value: object) -> str:
    text = str(value or "unknown").strip().lower()
    if text in {"higher", "higher_is_better", "maximize", "max"}:
        return "higher"
    if text in {"lower", "lower_is_better", "minimize", "min"}:
        return "lower"
    if text in {"resource", "ignore", "unknown"}:
        return text
    return "unknown"


__all__ = [
    "ResearchExperimentSessionError",
    "ResearchExperimentSessionRequest",
    "ResearchExperimentSessionResult",
    "build_experiment_analysis_context",
    "run_research_experiment_session",
]
