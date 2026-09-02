"""Small end-to-end research session composition.

This is an explicit, bounded application path from local literature to one
declared experiment and its analysis. It does not generate code or invent a
new research direction; optional model assistance only selects among grounded
directions already present in the synthesis handoff.
"""

from __future__ import annotations

from dataclasses import dataclass, field
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
from simple_ar.core.transitions import TransitionDecision, TransitionPolicy, TransitionRequest
from simple_ar.experiment.code_task_bridge import CodeTaskExperimentSpec
from simple_ar.experiment.execution.backend import ExecutionBackend, RunRequest
from simple_ar.research.documents.ingest import DocumentBundle
from simple_ar.research.evidence.reader import ReadResult
from simple_ar.research.analysis import AnalysisHandoff
from simple_ar.research.design import ResearchDesignRequest, ResearchDesignResult
from simple_ar.research.experiment import ExperimentRequest
from simple_ar.research.planning.capability import ResearchPlanResult
from simple_ar.research.registry import register_research_capabilities
from simple_ar.research.sources import SearchProviderRegistry, SearchResult
from simple_ar.research.synthesis import SynthesisResult
from simple_ar.research.brief import ResearchBriefResult
from simple_ar.research.decisions import (
    transition_request_from_analysis,
)
from simple_ar.result_analysis.schema import AnalysisResult

from .research_brief import (
    ResearchBriefSessionRequest,
    _new_controller,
    _run_research_brief_steps,
)
from .research_experiment import (
    _run_experiment_steps,
    build_experiment_analysis_context,
)


_RECOVERY_ATTEMPT_COUNT = 2
_REPORT_ATTEMPT_COUNT = 2


@dataclass(frozen=True, slots=True)
class ResearchSessionRequest:
    """Inputs for one bounded literature-to-experiment session."""

    brief: ResearchBriefSessionRequest
    command: tuple[str, ...]
    cwd: Path
    timeout_sec: int = 300
    result_schema: Mapping[str, Any] = field(default_factory=dict)
    label: str = "research-session"
    env: Mapping[str, str] | None = None
    code_task_spec: CodeTaskExperimentSpec | None = None
    code_task_model: str | None = None
    baseline_policy: str = "auto"
    baseline_metrics_file: Path | None = None

    def __post_init__(self) -> None:
        if self.code_task_spec is None and (
            not self.command or any(not str(item).strip() for item in self.command)
        ):
            raise ValueError("ResearchSessionRequest.command cannot be empty.")
        if self.code_task_spec is not None and self.command:
            raise ValueError(
                "Provide either command or code_task_spec, not both."
            )
        if self.timeout_sec < 1:
            raise ValueError("ResearchSessionRequest.timeout_sec must be positive.")
        if not self.label.strip():
            raise ValueError("ResearchSessionRequest.label cannot be empty.")
        object.__setattr__(self, "cwd", Path(self.cwd))
        object.__setattr__(self, "command", tuple(str(item) for item in self.command))
        object.__setattr__(self, "result_schema", dict(self.result_schema))
        object.__setattr__(
            self,
            "env",
            dict(self.env) if self.env is not None else None,
        )
        if self.baseline_metrics_file is not None:
            object.__setattr__(
                self,
                "baseline_metrics_file",
                Path(self.baseline_metrics_file),
            )


@dataclass(frozen=True, slots=True)
class ResearchSessionContinuationRequest:
    """Inputs for one explicit recovery experiment on an existing session.

    Continuation deliberately accepts a new command from the caller. The
    persisted literature, design handoff, and failed parent remain unchanged;
    the controller creates one isolated child attempt and reuses the existing
    experiment and analysis capabilities.
    """

    session_root: Path
    command: tuple[str, ...]
    cwd: Path
    timeout_sec: int = 300
    result_schema: Mapping[str, Any] = field(default_factory=dict)
    label: str = "research-session-recovery"
    env: Mapping[str, str] | None = None
    parent_attempt_id: str = "experiment-001"
    trigger: str = "explicit_recovery"
    use_llm: bool = False
    llm_client: Any | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not str(self.session_root).strip():
            raise ValueError(
                "ResearchSessionContinuationRequest.session_root is required."
            )
        if not self.command or any(not str(item).strip() for item in self.command):
            raise ValueError("ResearchSessionContinuationRequest.command cannot be empty.")
        if self.timeout_sec < 1:
            raise ValueError(
                "ResearchSessionContinuationRequest.timeout_sec must be positive."
            )
        if not self.label.strip():
            raise ValueError("ResearchSessionContinuationRequest.label cannot be empty.")
        if not self.parent_attempt_id.strip():
            raise ValueError(
                "ResearchSessionContinuationRequest.parent_attempt_id cannot be blank."
            )
        if not self.trigger.strip():
            raise ValueError("ResearchSessionContinuationRequest.trigger cannot be blank.")
        if self.use_llm and self.llm_client is None:
            raise ValueError(
                "ResearchSessionContinuationRequest.llm_client is required when use_llm is true."
            )
        object.__setattr__(self, "session_root", Path(self.session_root))
        object.__setattr__(self, "cwd", Path(self.cwd))
        object.__setattr__(self, "command", tuple(str(item) for item in self.command))
        object.__setattr__(self, "result_schema", dict(self.result_schema))
        object.__setattr__(
            self,
            "env",
            dict(self.env) if self.env is not None else None,
        )


@dataclass(frozen=True, slots=True)
class ResearchSessionResult:
    """Structured outputs from one full small research session."""

    session_root: Path
    plan: ResearchPlanResult
    search: SearchResult
    documents: DocumentBundle
    brief: SynthesisResult
    brief_result: ResearchBriefResult
    brief_ref: ArtifactRef
    execution: Mapping[str, Any]
    analysis: AnalysisResult
    execution_ref: ArtifactRef
    analysis_ref: ArtifactRef
    attempts: tuple[AttemptManifest, ...]
    decisions: tuple[DecisionRecord, ...]
    design: ResearchDesignResult | None = None
    design_ref: ArtifactRef | None = None

    @property
    def status(self) -> str:
        execution_status = str(self.execution.get("status") or "unknown")
        if execution_status == "passed" and self.analysis.status == "passed":
            return "ready_for_report"
        if self.analysis.status == "passed":
            return "partial"
        return self.analysis.status

    @property
    def report_ready(self) -> bool:
        """Whether this session has the evidence required for a formal report."""

        return self.status == "ready_for_report"

    @property
    def next_capability(self) -> str | None:
        """Return the explicit next handoff recorded by the session."""

        return self.decisions[-1].next_capability if self.decisions else None

    @property
    def recommended_transition(self) -> TransitionDecision:
        """Derive one bounded next-step recommendation from the final analysis.

        This is advisory only: it does not create an attempt, rerun a command,
        or replace the controller decision already persisted in the manifest.
        A successful execution and analysis may continue to report; every
        other outcome is offered back to the experiment boundary for an
        explicit caller-owned retry or repair.
        """

        execution_status = str(self.execution.get("status") or "unknown")
        execution_passed = execution_status == "passed"
        analysis_passed = self.analysis.status == "passed"
        target = "report" if execution_passed and analysis_passed else "experiment"
        expected_delta = (
            "assemble an evidence-grounded report from the completed session"
            if target == "report"
            else "repair or redesign the experiment using the recorded analysis"
        )
        if execution_passed:
            request = transition_request_from_analysis(
                self.analysis,
                target=target,
                expected_delta=expected_delta,
            )
        else:
            # Analysis can still be structurally valid when the command did
            # not run successfully. Preserve that runtime failure at the
            # research boundary instead of accepting a misleading handoff.
            execution_signal = f"Execution input status: {execution_status or 'unknown'}."
            request = TransitionRequest(
                source="analysis",
                result_status="failed",
                target=target,
                expected_delta=expected_delta,
                signals=(execution_signal, *self.analysis.status_reasons),
            )
        return TransitionPolicy().decide(request)


class ResearchSessionError(RuntimeError):
    """Raised when the brief is not ready for the requested experiment."""


def run_research_session(
    request: ResearchSessionRequest,
    *,
    search_registry: SearchProviderRegistry | None = None,
    backend: ExecutionBackend | None = None,
) -> ResearchSessionResult:
    """Run literature, design handoff, one execution, and result analysis."""

    request.brief.session_root.mkdir(parents=True, exist_ok=True)
    controller = _new_controller(
        request.brief.session_root,
        topic=request.brief.topic,
        profile="full_research",
        names=(
            "plan",
            "search",
            "document_ingest",
            "read",
            "synthesize",
            "research_design",
            "experiment",
            "analysis",
        ),
        budget=BudgetState(max_attempts=10, max_no_progress=3),
    )
    code_task_request_cls: Any | None = None
    if request.code_task_spec is not None:
        from .research_code_task import (
            _CodeTaskCapabilityRequest,
            _run_code_task_capability,
        )

        code_task_request_cls = _CodeTaskCapabilityRequest
        if not request.brief.use_llm:
            raise ResearchSessionError(
                "Code-Task-backed research sessions require an LLM-enabled brief."
            )
        controller.registry.register(
            "experiment",
            _run_code_task_capability,
            replace=True,
        )
    steps = _run_research_brief_steps(
        request.brief,
        controller,
        search_registry=search_registry,
        next_capability="research_design",
    )
    if steps.brief.status != "ready":
        raise ResearchSessionError(
            f"Research brief status is {steps.brief.status!r}; inspect {request.brief.session_root}."
        )
    if steps.brief.synthesis is None or steps.brief.synthesis.experiment_contract is None:
        raise ResearchSessionError(
            f"Research brief has no experiment contract; inspect {request.brief.session_root}."
        )

    result_schema = _effective_result_schema(request)
    design_result, _ = controller.execute(
        "research_design",
        attempt_id="design-001",
        inputs=(steps.brief_ref,),
        next_capability="experiment",
        request=ResearchDesignRequest(
            synthesis=steps.brief.synthesis,
            topic=request.brief.topic,
            execution_schema=result_schema,
            use_llm=request.brief.use_llm,
            llm_client=request.brief.llm_client,
        ),
    )
    if design_result.status in {"failed", "blocked"}:
        details = "; ".join(
            item for item in design_result.diagnostics if item.strip()
        )
        raise ResearchSessionError(
            f"research_design capability returned {design_result.status!r}"
            + (f": {details}" if details else ".")
        )
    try:
        design_ref = controller.attempt_output_ref(
            "design-001",
            kind="research_design",
            schema="research_design.v1",
        )
    except (KeyError, OSError, ValueError) as exc:
        raise ResearchSessionError(
            "research_design capability did not provide its typed output: "
            f"{exc}"
        ) from exc
    design_payload = controller.store.read_json(design_ref)
    if not isinstance(design_payload, Mapping):
        raise ResearchSessionError(
            "Research design output is not a JSON object; inspect "
            f"{request.brief.session_root}."
        )
    design = ResearchDesignResult.from_handoff_dict(design_payload)
    if design.status != "ready" or design.contract is None:
        raise ResearchSessionError(
            "Research design is not executable: "
            + "; ".join(design.diagnostics or ("no diagnostic was recorded",))
        )

    source_path = request.brief.session_root / steps.brief_ref.path
    experiment_request: Any | None = None
    experiment_inputs: tuple[ArtifactRef, ...] | None = None
    if request.code_task_spec is not None:
        assert code_task_request_cls is not None
        model = request.code_task_model or str(
            getattr(request.brief.llm_client, "model", "")
        ).strip()
        experiment_request = code_task_request_cls(
            spec=request.code_task_spec,
            model=model or None,
            use_llm=True,
            timeout_sec=request.timeout_sec,
            baseline_policy=request.baseline_policy,
            baseline_metrics_file=request.baseline_metrics_file,
        )
        experiment_inputs = (steps.brief_ref, design_ref)
    experiment = _run_experiment_steps(
        controller,
        source_ref=steps.brief_ref,
        synthesis=steps.brief.synthesis,
        design=design,
        design_ref=design_ref,
        run_request=RunRequest(
            command=list(request.command),
            cwd=request.cwd,
            timeout_sec=request.timeout_sec,
            label=request.label,
            env=dict(request.env) if request.env is not None else None,
        ),
        result_schema=result_schema,
        analysis_context=build_experiment_analysis_context(
            topic=request.brief.topic,
            task_id=request.brief.session_root.name,
            source_file=source_path,
            synthesis=steps.brief.synthesis,
            result_schema=result_schema,
            contract=design.contract,
        ),
        analysis_next_capability="report",
        backend=backend,
        analysis_use_llm=request.brief.use_llm,
        analysis_client=request.brief.llm_client,
        experiment_request=experiment_request,
        experiment_inputs=experiment_inputs,
    )
    return ResearchSessionResult(
        session_root=request.brief.session_root,
        plan=steps.plan,
        search=steps.search,
        documents=steps.documents,
        brief=steps.brief.synthesis,
        brief_result=steps.brief,
        brief_ref=steps.brief_ref,
        execution=experiment.execution,
        analysis=experiment.analysis,
        execution_ref=experiment.execution_ref,
        analysis_ref=experiment.analysis_ref,
        attempts=controller.list_attempts(),
        decisions=tuple(controller.manifest.decisions),
        design=design,
        design_ref=design_ref,
    )


def continue_research_session(
    request: ResearchSessionContinuationRequest,
    *,
    backend: ExecutionBackend | None = None,
) -> ResearchSessionResult:
    """Run one explicit recovery experiment without rebuilding literature.

    The persisted session is the source of truth. Recovery is allowed only
    when its latest analysis recommends the experiment boundary, and it is
    limited to the fixed ``experiment-002``/``analysis-002`` child pair. The
    caller supplies the revised command; no LLM or scheduler selects it.
    """

    base = load_research_session_result(request.session_root)
    recommendation = base.recommended_transition
    if recommendation.action == "block":
        raise ResearchSessionError(
            "Research session recovery is blocked by its persisted budget or decision history."
        )
    if recommendation.target != "experiment":
        raise ResearchSessionError(
            "Research session does not recommend an experiment recovery; "
            f"got {recommendation.action!r} -> {recommendation.target!r}."
        )
    if base.design is None or base.design.contract is None or base.design_ref is None:
        raise ResearchSessionError(
            "Research session has no executable design handoff for recovery."
        )

    registry = CapabilityRegistry()
    register_research_capabilities(
        registry,
        names=("research_design", "experiment", "analysis"),
    )
    controller = SessionController.load(
        request.session_root,
        registry=registry,
    )
    attempts = controller.list_attempts()
    parent = next(
        (item for item in attempts if item.attempt_id == request.parent_attempt_id),
        None,
    )
    if parent is None:
        raise ResearchSessionError(
            f"Recovery parent attempt not found: {request.parent_attempt_id}."
        )
    if parent.capability != "experiment":
        raise ResearchSessionError(
            f"Recovery parent must be an experiment attempt, got {parent.capability!r}."
        )
    existing_ids = {item.attempt_id for item in attempts}
    conflicts = sorted(existing_ids & {"experiment-002", "analysis-002"})
    if conflicts:
        raise ResearchSessionError(
            "This session already contains a recovery attempt: "
            + ", ".join(conflicts)
            + ". Start a new session for another recovery branch."
        )

    # The original budget reserves the normal report pair. An explicit
    # recovery needs two additional attempts while keeping that report pair.
    budget = controller.manifest.budget
    required_attempts = (
        budget.attempts + _RECOVERY_ATTEMPT_COUNT + _REPORT_ATTEMPT_COUNT
    )
    if budget.max_attempts < required_attempts:
        budget.max_attempts = required_attempts
        controller.save()

    result_schema = _merge_recovery_result_schema(
        base.execution,
        request.result_schema,
    )
    source_path = request.session_root / base.brief_ref.path
    steps = _run_experiment_steps(
        controller,
        source_ref=base.brief_ref,
        synthesis=base.brief,
        design=base.design,
        design_ref=base.design_ref,
        run_request=RunRequest(
            command=list(request.command),
            cwd=request.cwd,
            timeout_sec=request.timeout_sec,
            label=request.label,
            env=dict(request.env) if request.env is not None else None,
        ),
        result_schema=result_schema,
        analysis_context=build_experiment_analysis_context(
            topic=base.plan.query_plan.topic,
            task_id=request.session_root.name,
            source_file=source_path,
            synthesis=base.brief,
            result_schema=result_schema,
            contract=base.design.contract,
        ),
        analysis_next_capability="report",
        backend=backend,
        analysis_use_llm=request.use_llm,
        analysis_client=request.llm_client,
        experiment_attempt_id="experiment-002",
        analysis_attempt_id="analysis-002",
        experiment_parent_attempt_id=parent.attempt_id,
        experiment_trigger=request.trigger,
    )
    return ResearchSessionResult(
        session_root=request.session_root,
        plan=base.plan,
        search=base.search,
        documents=base.documents,
        brief=base.brief,
        brief_result=base.brief_result,
        brief_ref=base.brief_ref,
        execution=steps.execution,
        analysis=steps.analysis,
        execution_ref=steps.execution_ref,
        analysis_ref=steps.analysis_ref,
        attempts=controller.list_attempts(),
        decisions=tuple(controller.manifest.decisions),
        design=base.design,
        design_ref=base.design_ref,
    )


def _merge_recovery_result_schema(
    execution: Mapping[str, Any],
    override: Mapping[str, Any],
) -> dict[str, Any]:
    """Reuse the parent schema unless the caller explicitly adjusts it."""

    persisted = execution.get("result_schema")
    schema = dict(persisted) if isinstance(persisted, Mapping) else {}
    for key, value in override.items():
        if key == "required_metrics" and isinstance(value, (list, tuple)):
            existing = schema.get(key)
            names = list(existing) if isinstance(existing, (list, tuple)) else []
            schema[key] = list(dict.fromkeys(names + list(value)))
        elif key == "metric_directions" and isinstance(value, Mapping):
            existing = schema.get(key)
            directions = dict(existing) if isinstance(existing, Mapping) else {}
            directions.update(value)
            schema[key] = directions
        elif value not in (None, "", []):
            schema[key] = value
    return schema


def _effective_result_schema(request: ResearchSessionRequest) -> dict[str, Any]:
    """Align analysis with the backend that will actually execute the task."""

    schema = dict(request.result_schema)
    if request.code_task_spec is None:
        return schema

    configured = request.code_task_spec.result_schema()
    for key, value in configured.items():
        if key == "required_metrics" and isinstance(value, list):
            existing = schema.get("required_metrics")
            names = list(existing) if isinstance(existing, (list, tuple)) else []
            schema[key] = list(dict.fromkeys(names + value))
        elif key == "metric_directions" and isinstance(value, Mapping):
            existing = schema.get(key)
            directions = dict(existing) if isinstance(existing, Mapping) else {}
            directions.update(value)
            schema[key] = directions
        else:
            schema[key] = value
    return schema


def _attempt_sequence(attempt_id: str) -> tuple[int, str]:
    suffix = attempt_id.rsplit("-", 1)[-1]
    return (int(suffix), attempt_id) if suffix.isdigit() else (-1, attempt_id)


def _latest_attempt_id(
    attempts: tuple[AttemptManifest, ...],
    *,
    capability: str,
    parent_attempt_id: str | None = None,
) -> str:
    matches = tuple(
        item
        for item in attempts
        if item.capability == capability
        and (
            parent_attempt_id is None
            or item.parent_attempt == parent_attempt_id
        )
    )
    if not matches:
        raise KeyError(f"No {capability!r} attempt is available.")
    return max(matches, key=lambda item: _attempt_sequence(item.attempt_id)).attempt_id


def load_research_session_result(
    session_root: str | Path,
) -> ResearchSessionResult:
    """Restore a standard research-to-analysis session without rerunning it.

    Restoration uses only the session manifest and the declared typed outputs
    of the standard attempts.  It deliberately does not scan for a best
    artifact, infer missing stages, or register executable handlers; callers
    can therefore use the returned result for an explicit report continuation
    after the original process has ended.
    """

    root = Path(session_root)
    try:
        controller = SessionController.load(
            root,
            registry=CapabilityRegistry(),
        )
    except (FileNotFoundError, OSError, TypeError, ValueError) as exc:
        raise ResearchSessionError(
            f"Could not load research session {root}: {exc}"
        ) from exc

    try:
        attempts = controller.list_attempts()
        experiment_attempt_id = _latest_attempt_id(
            attempts,
            capability="experiment",
        )
        analysis_attempt_id = _latest_attempt_id(
            attempts,
            capability="analysis",
            parent_attempt_id=experiment_attempt_id,
        )
        plan_ref = controller.attempt_output_ref(
            "plan-001",
            kind="research_plan",
            schema="research_plan.v1",
        )
        plan = ResearchPlanResult.from_handoff_dict(controller.store.read_json(plan_ref))

        search_ref = controller.attempt_output_ref(
            "search-001",
            kind="search_result",
            schema="search_handoff.v1",
        )
        search = SearchResult.from_handoff_dict(controller.store.read_json(search_ref))

        document_ref = controller.attempt_output_ref(
            "document-001",
            kind="document_bundle",
            schema="document_bundle.v1",
        )
        documents = DocumentBundle.from_handoff_dict(
            controller.store.read_json(document_ref)
        )

        # New sessions expose Read and Synthesis as separate attempts. Keep
        # the old aggregate handoff readable so historical sessions remain
        # usable after this migration.
        if any(item.attempt_id == "read-001" for item in attempts):
            read_ref = controller.attempt_output_ref(
                "read-001",
                kind="read_result",
                schema="read_result.v1",
            )
            read_payload = controller.store.read_json(read_ref)
            if not isinstance(read_payload, Mapping):
                raise ValueError("Read handoff must be a JSON object.")
            read_result = ReadResult.from_handoff_dict(
                read_payload,
                bundle=documents,
            )
            brief_ref = controller.attempt_output_ref(
                "synthesize-001",
                kind="synthesis_result",
                schema="synthesis_result.v1",
            )
            synthesis_payload = controller.store.read_json(brief_ref)
            if not isinstance(synthesis_payload, Mapping):
                raise ValueError("Synthesis handoff must be a JSON object.")
            synthesis = SynthesisResult.from_handoff_dict(synthesis_payload)
            brief_result = ResearchBriefResult.from_parts(read_result, synthesis)
        else:
            brief_ref = controller.attempt_output_ref(
                "brief-001",
                kind="research_brief",
                schema="research_brief.v1",
            )
            brief_payload = controller.store.read_json(brief_ref)
            if not isinstance(brief_payload, Mapping):
                raise ValueError("Research brief handoff must be a JSON object.")
            brief_result = ResearchBriefResult.from_handoff_dict(
                brief_payload,
                bundle=documents,
            )
        if brief_result.synthesis is None:
            raise ValueError("Research brief handoff has no synthesis result.")

        design: ResearchDesignResult | None = None
        design_ref: ArtifactRef | None = None
        if any(item.attempt_id == "design-001" for item in controller.list_attempts()):
            design_ref = controller.attempt_output_ref(
                "design-001",
                kind="research_design",
                schema="research_design.v1",
            )
            design_payload = controller.store.read_json(design_ref)
            if not isinstance(design_payload, Mapping):
                raise ValueError("Research design handoff must be a JSON object.")
            design = ResearchDesignResult.from_handoff_dict(design_payload)
            if design.status != "ready" or design.contract is None:
                raise ValueError("Research design handoff is not executable.")

        execution_ref = controller.attempt_output_ref(
            experiment_attempt_id,
            kind="experiment_result",
            schema="canonical_results.2.5",
        )
        execution_payload = controller.store.read_json(execution_ref)
        if not isinstance(execution_payload, Mapping):
            raise ValueError("Experiment result must be a JSON object.")

        analysis_ref = controller.attempt_output_ref(
            analysis_attempt_id,
            kind="analysis_result",
            schema="analysis_handoff.v1",
        )
        analysis_payload = controller.store.read_json(analysis_ref)
        if not isinstance(analysis_payload, Mapping):
            raise ValueError("Analysis handoff must be a JSON object.")
        analysis = AnalysisHandoff.from_handoff_dict(analysis_payload).analysis
    except (KeyError, OSError, TypeError, ValueError) as exc:
        raise ResearchSessionError(
            f"Research session {root} is missing a usable typed handoff: {exc}"
        ) from exc

    return ResearchSessionResult(
        session_root=root,
        plan=plan,
        search=search,
        documents=documents,
        brief=brief_result.synthesis,
        brief_result=brief_result,
        brief_ref=brief_ref,
        execution=dict(execution_payload),
        analysis=analysis,
        execution_ref=execution_ref,
        analysis_ref=analysis_ref,
        attempts=controller.list_attempts(),
        decisions=tuple(controller.manifest.decisions),
        design=design,
        design_ref=design_ref,
    )


__all__ = [
    "ResearchSessionContinuationRequest",
    "ResearchSessionError",
    "ResearchSessionRequest",
    "ResearchSessionResult",
    "continue_research_session",
    "load_research_session_result",
    "run_research_session",
]
