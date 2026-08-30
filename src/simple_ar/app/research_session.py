"""Small end-to-end research session composition.

This is an explicit, bounded application path from local literature to one
declared experiment and its analysis. It does not generate code or invent a
research decision; those remain inputs for later workflow layers.
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
from simple_ar.experiment.execution.backend import ExecutionBackend, RunRequest
from simple_ar.research.documents.ingest import DocumentBundle
from simple_ar.research.analysis import AnalysisHandoff
from simple_ar.research.planning.capability import ResearchPlanResult
from simple_ar.research.sources import SearchProviderRegistry, SearchResult
from simple_ar.research.synthesis import SynthesisResult
from simple_ar.research.brief import ResearchBriefResult
from simple_ar.result_analysis.schema import AnalysisContext, AnalysisResult

from .research_brief import (
    ResearchBriefSessionRequest,
    _new_controller,
    _run_research_brief_steps,
)
from .research_experiment import (
    _run_experiment_steps,
    build_experiment_analysis_context,
)


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

    def __post_init__(self) -> None:
        if not self.command or any(not str(item).strip() for item in self.command):
            raise ValueError("ResearchSessionRequest.command cannot be empty.")
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

    @property
    def status(self) -> str:
        execution_status = str(self.execution.get("status") or "unknown")
        if execution_status == "passed" and self.analysis.status == "passed":
            return "ready_for_report"
        if self.analysis.status == "passed":
            return "partial"
        return self.analysis.status

    @property
    def next_capability(self) -> str | None:
        """Return the explicit next handoff recorded by the session."""

        return self.decisions[-1].next_capability if self.decisions else None


class ResearchSessionError(RuntimeError):
    """Raised when the brief is not ready for the requested experiment."""


def run_research_session(
    request: ResearchSessionRequest,
    *,
    search_registry: SearchProviderRegistry | None = None,
    backend: ExecutionBackend | None = None,
) -> ResearchSessionResult:
    """Run literature, synthesis, one execution, and result analysis."""

    request.brief.session_root.mkdir(parents=True, exist_ok=True)
    controller = _new_controller(
        request.brief.session_root,
        topic=request.brief.topic,
        profile="full_research",
        names=(
            "plan",
            "search",
            "document_ingest",
            "research_brief",
            "experiment",
            "analysis",
        ),
        budget=BudgetState(max_attempts=8, max_no_progress=3),
    )
    steps = _run_research_brief_steps(
        request.brief,
        controller,
        search_registry=search_registry,
        next_capability="experiment",
    )
    if steps.brief.status != "ready":
        raise ResearchSessionError(
            f"Research brief status is {steps.brief.status!r}; inspect {request.brief.session_root}."
        )
    if steps.brief.synthesis is None or steps.brief.synthesis.experiment_contract is None:
        raise ResearchSessionError(
            f"Research brief has no experiment contract; inspect {request.brief.session_root}."
        )

    source_path = request.brief.session_root / steps.brief_ref.path
    experiment = _run_experiment_steps(
        controller,
        source_ref=steps.brief_ref,
        synthesis=steps.brief.synthesis,
        run_request=RunRequest(
            command=list(request.command),
            cwd=request.cwd,
            timeout_sec=request.timeout_sec,
            label=request.label,
            env=dict(request.env) if request.env is not None else None,
        ),
        result_schema=request.result_schema,
        analysis_context=build_experiment_analysis_context(
            topic=request.brief.topic,
            task_id=request.brief.session_root.name,
            source_file=source_path,
            synthesis=steps.brief.synthesis,
            result_schema=request.result_schema,
        ),
        analysis_next_capability="report",
        backend=backend,
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
    )


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

        execution_ref = controller.attempt_output_ref(
            "experiment-001",
            kind="experiment_result",
            schema="canonical_results.2.5",
        )
        execution_payload = controller.store.read_json(execution_ref)
        if not isinstance(execution_payload, Mapping):
            raise ValueError("Experiment result must be a JSON object.")

        analysis_ref = controller.attempt_output_ref(
            "analysis-001",
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
    )


__all__ = [
    "ResearchSessionError",
    "ResearchSessionRequest",
    "ResearchSessionResult",
    "load_research_session_result",
    "run_research_session",
]
