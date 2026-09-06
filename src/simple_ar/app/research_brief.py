"""User-facing composition of the reusable research capabilities.

This module is deliberately an application workflow, not a second pipeline
implementation. It owns only the order and handoff policy for a small,
useful path from a topic or local documents to an evidence-backed brief.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import json
from pathlib import Path
from typing import Any, Mapping

from simple_ar.core import (
    ArtifactRef,
    AttemptManifest,
    BudgetState,
    CapabilityResult,
    CapabilityRegistry,
    DecisionRecord,
    SessionController,
)
from simple_ar.app.session_roots import new_research_session_root
from simple_ar.research.brief import (
    ResearchBriefResult,
    evidence_pack_from_read,
)
from simple_ar.research.documents.ingest import (
    DocumentBundle,
    DocumentIngestRequest,
)
from simple_ar.research.evidence.reader import ReadRequest, ReadResult
from simple_ar.research.planning.capability import (
    ResearchPlanRequest,
    ResearchPlanResult,
    search_request_from_plan,
)
from simple_ar.research.registry import register_research_capabilities
from simple_ar.research.sources import (
    SearchProviderRegistry,
    SearchResult,
    SearchSelectionPolicy,
    default_search_provider_registry,
)
from simple_ar.research.synthesis import SynthesisRequest, SynthesisResult


@dataclass(frozen=True, slots=True)
class ResearchBriefSessionRequest:
    """Inputs for the small topic-to-brief application workflow.

    ``session_root`` is an exact run directory. The CLI creates a unique one
    below its output root; library callers can choose a stable path for a
    fixture, a service job, or an explicit resume policy.
    """

    topic: str
    session_root: Path
    local_documents: tuple[Path, ...] = ()
    queries: tuple[str, ...] = ()
    providers: tuple[str, ...] = ()
    max_results: int = 10
    max_chunks: int | None = 300
    idea_limit: int = 3
    cache_dir: Path | None = None
    extraction_dir: Path | None = None
    config: Mapping[str, object] = field(default_factory=dict)
    use_llm: bool = False
    llm_client: Any | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not self.topic.strip():
            raise ValueError("ResearchBriefSessionRequest.topic cannot be empty.")
        if not str(self.session_root).strip():
            raise ValueError("ResearchBriefSessionRequest.session_root is required.")
        if self.max_results < 1:
            raise ValueError("ResearchBriefSessionRequest.max_results must be positive.")
        if self.max_chunks is not None and self.max_chunks < 1:
            raise ValueError("ResearchBriefSessionRequest.max_chunks must be positive.")
        if self.idea_limit < 1:
            raise ValueError("ResearchBriefSessionRequest.idea_limit must be positive.")
        if self.use_llm and self.llm_client is None:
            raise ValueError(
                "ResearchBriefSessionRequest.llm_client is required when use_llm is true."
            )
        object.__setattr__(self, "session_root", Path(self.session_root))
        object.__setattr__(
            self,
            "local_documents",
            tuple(Path(path) for path in self.local_documents),
        )
        object.__setattr__(
            self,
            "queries",
            tuple(query.strip() for query in self.queries if query.strip()),
        )
        object.__setattr__(
            self,
            "providers",
            tuple(provider.strip() for provider in self.providers if provider.strip()),
        )
        object.__setattr__(self, "config", dict(self.config))


@dataclass(frozen=True, slots=True)
class ResearchBriefSessionResult:
    """Persisted outputs and in-memory values from one brief session."""

    session_root: Path
    plan: ResearchPlanResult
    search: SearchResult
    documents: DocumentBundle
    brief: ResearchBriefResult
    brief_ref: ArtifactRef
    attempts: tuple[AttemptManifest, ...]
    decisions: tuple[DecisionRecord, ...]

    @property
    def status(self) -> str:
        return self.brief.status

    @property
    def brief_path(self) -> Path:
        """Return the final direction handoff (the Synthesis artifact)."""

        return self.session_root / self.brief_ref.path


class ResearchBriefSessionError(RuntimeError):
    """Raised when a session cannot produce a usable downstream handoff."""


@dataclass(frozen=True, slots=True)
class _ResearchBriefSteps:
    """Internal state needed by a larger application composition."""

    controller: SessionController
    plan: ResearchPlanResult
    search: SearchResult
    documents: DocumentBundle
    brief: ResearchBriefResult
    brief_ref: ArtifactRef


def new_research_brief_root(output_root: str | Path, topic: str) -> Path:
    """Create a unique session directory below an application output root."""

    return new_research_session_root(output_root, topic)


def run_research_brief_session(
    request: ResearchBriefSessionRequest,
    *,
    search_registry: SearchProviderRegistry | None = None,
) -> ResearchBriefSessionResult:
    """Run one bounded, explicit topic-to-brief workflow.

    Search and ingest results are retained as separate attempts. The brief
    capability then composes the existing deterministic Read and Synthesis
    implementations, so this function does not duplicate domain logic.
    """

    request.session_root.mkdir(parents=True, exist_ok=True)
    controller = _new_controller(
        request.session_root,
        topic=request.topic,
        profile="research_brief",
        names=("plan", "search", "document_ingest", "read", "synthesize"),
        budget=BudgetState(max_attempts=7, max_no_progress=2),
    )
    steps = _run_research_brief_steps(
        request,
        controller,
        search_registry=search_registry,
    )
    return ResearchBriefSessionResult(
        session_root=request.session_root,
        plan=steps.plan,
        search=steps.search,
        documents=steps.documents,
        brief=steps.brief,
        brief_ref=steps.brief_ref,
        attempts=controller.list_attempts(),
        decisions=tuple(controller.manifest.decisions),
    )


def _new_controller(
    session_root: Path,
    *,
    topic: str,
    profile: str,
    names: tuple[str, ...],
    budget: BudgetState,
) -> SessionController:
    capability_registry = CapabilityRegistry()
    register_research_capabilities(capability_registry, names=names)
    return SessionController.create(
        session_root,
        session_id=session_root.name,
        topic=topic,
        profile=profile,
        registry=capability_registry,
        budget=budget,
    )


def _run_research_brief_steps(
    request: ResearchBriefSessionRequest,
    controller: SessionController,
    *,
    search_registry: SearchProviderRegistry | None = None,
    next_capability: str | None = None,
) -> _ResearchBriefSteps:
    """Run the reusable retrieval-to-brief prefix in one controller."""

    plan_request = ResearchPlanRequest(
        topic=request.topic,
        problem_markdown=_research_problem_markdown(request),
        config=_planning_config(request),
        default_query=request.topic,
        default_max_results=request.max_results,
        use_llm=request.use_llm,
        llm_client=request.llm_client,
    )
    plan_capability, _ = controller.execute(
        "plan",
        attempt_id="plan-001",
        next_capability="search",
        request=plan_request,
    )
    _require_completed(plan_capability, "plan", request.session_root)
    plan_ref = controller.attempt_output_ref(
        "plan-001", kind="research_plan", schema="research_plan.v1"
    )
    plan = ResearchPlanResult.from_handoff_dict(
        controller.store.read_json(plan_ref)
    )

    provider_registry = search_registry or default_search_provider_registry(
        local_documents=(str(path) for path in request.local_documents)
    )
    search_request = search_request_from_plan(plan)
    if request.cache_dir is not None:
        search_request = replace(
            search_request,
            cache_dir=request.cache_dir / "literature",
            cache_enabled=plan.source_plan.cache_enabled,
        )
    search_capability, _ = controller.execute(
        "search",
        attempt_id="search-001",
        inputs=(plan_ref,),
        next_capability="document_ingest",
        request=search_request,
        registry=provider_registry,
        selection_policy=SearchSelectionPolicy(
            topic=request.topic,
            questions=plan.questions,
            query_plan=plan.query_plan,
            max_documents=_search_document_limit(request, plan),
        ),
    )
    _require_completed(
        search_capability,
        "search",
        request.session_root,
        allow_partial=True,
    )
    search_ref = controller.attempt_output_ref(
        "search-001", kind="search_result", schema="search_handoff.v1"
    )
    search = SearchResult.from_handoff_dict(controller.store.read_json(search_ref))
    # ``SearchResult.from_handoff_dict`` already handles old handoffs that do
    # not declare ``selected_paper_ids``. Once the canonical selection fields
    # exist, an empty selection is meaningful and must not be widened back to
    # the raw provider output.
    selected_papers = search.selected_papers
    if not selected_papers:
        raise ResearchBriefSessionError(
            f"Search produced no usable papers; inspect {request.session_root}."
        )

    document_capability, _ = controller.execute(
        "document_ingest",
        attempt_id="document-001",
        inputs=(search_ref,),
        next_capability="read",
        request=DocumentIngestRequest(
            papers=selected_papers,
            source_plan=plan.source_plan,
            cache_dir=request.cache_dir or request.session_root / "cache",
            extraction_dir=request.extraction_dir or request.session_root / "documents",
            max_chunks=request.max_chunks,
        ),
    )
    _require_completed(
        document_capability,
        "document_ingest",
        request.session_root,
        allow_partial=True,
    )
    document_ref = controller.attempt_output_ref(
        "document-001", kind="document_bundle", schema="document_bundle.v1"
    )
    documents = DocumentBundle.from_handoff_dict(
        controller.store.read_json(document_ref)
    )
    if not documents.records:
        raise ResearchBriefSessionError(
            f"Document ingest produced no usable documents; inspect {request.session_root}."
        )

    read_capability, _ = controller.execute(
        "read",
        attempt_id="read-001",
        inputs=(document_ref,),
        next_capability="synthesize",
        request=ReadRequest(
            bundle=documents,
            topic=request.topic,
            problem_markdown=_research_problem_markdown(request),
            research_plan_json=json.dumps(
                plan.to_handoff_dict(),
                ensure_ascii=False,
                indent=2,
            ),
            config=request.config,
            use_llm=request.use_llm,
            llm_client=request.llm_client,
        ),
    )
    _require_completed(
        read_capability,
        "read",
        request.session_root,
        allow_partial=True,
    )
    read_ref = controller.attempt_output_ref(
        "read-001", kind="read_result", schema="read_result.v1"
    )
    read_payload = controller.store.read_json(read_ref)
    if not isinstance(read_payload, Mapping):
        raise ResearchBriefSessionError(
            f"Read handoff is not a JSON object; inspect {request.session_root}."
        )
    read = ReadResult.from_handoff_dict(read_payload, bundle=documents)

    synthesis_capability, _ = controller.execute(
        "synthesize",
        attempt_id="synthesize-001",
        inputs=(read_ref,),
        next_capability=next_capability,
        request=SynthesisRequest(
            evidence_pack=evidence_pack_from_read(
                request.topic,
                read,
                coverage=search.coverage_report,
                source_plan=plan.source_plan.to_row(),
                execution_context=_research_execution_context(request),
            ),
            idea_limit=request.idea_limit,
            use_llm=request.use_llm,
            llm_client=request.llm_client,
        ),
    )
    _require_completed(
        synthesis_capability,
        "synthesize",
        request.session_root,
        allow_partial=True,
    )
    synthesis_ref = controller.attempt_output_ref(
        "synthesize-001", kind="synthesis_result", schema="synthesis_result.v1"
    )
    synthesis_payload = controller.store.read_json(synthesis_ref)
    if not isinstance(synthesis_payload, Mapping):
        raise ResearchBriefSessionError(
            f"Synthesis handoff is not a JSON object; inspect {request.session_root}."
        )
    synthesis = SynthesisResult.from_handoff_dict(synthesis_payload)
    brief = ResearchBriefResult.from_parts(read, synthesis)
    # ``brief_ref`` is the historical field name for the final direction
    # handoff. It now points to the explicit Synthesis attempt.
    brief_ref = synthesis_ref
    return _ResearchBriefSteps(
        controller=controller,
        plan=plan,
        search=search,
        documents=documents,
        brief=brief,
        brief_ref=brief_ref,
    )


def _planning_config(request: ResearchBriefSessionRequest) -> dict[str, object]:
    config = dict(request.config)
    if request.queries:
        config["research_queries"] = list(request.queries)
    if request.providers:
        config["research_sources"] = list(request.providers)
    elif request.local_documents:
        config["research_sources"] = ["local_files"]
    if request.local_documents:
        config["research_local_documents"] = [str(path) for path in request.local_documents]
        config.setdefault("research_use_fulltext", True)
        config.setdefault("research_allow_pdf_download", False)
    return config


def _research_execution_context(request: ResearchBriefSessionRequest) -> str:
    """Read the optional hard boundary for a prepared downstream experiment."""

    value = request.config.get("research_execution_context")
    return str(value or "").strip()


def _research_problem_markdown(request: ResearchBriefSessionRequest) -> str:
    """Expose prepared experiment limits to planning and reading prompts."""

    base = (
        f"# Research Problem\n\nStudy `{request.topic}` with the configured "
        "evidence and experiment budget.\n"
    )
    context = _research_execution_context(request)
    if not context:
        return base
    return (
        base
        + "\n## Prepared Experiment Boundary (hard)\n\n"
        + context[:8000]
        + "\n"
    )


def _search_document_limit(
    request: ResearchBriefSessionRequest,
    plan: ResearchPlanResult,
) -> int:
    """Resolve one explicit paper budget for canonical search selection."""

    configured = plan.source_plan.budget.get("max_documents")
    if isinstance(configured, int) and not isinstance(configured, bool) and configured > 0:
        return configured
    return max(1, request.max_results)


def _require_completed(
    result: CapabilityResult,
    capability: str,
    session_root: Path,
    *,
    allow_partial: bool = False,
) -> None:
    accepted = {"completed", "partial"} if allow_partial else {"completed"}
    if result.status not in accepted:
        details = "; ".join(item for item in result.diagnostics if item.strip())
        raise ResearchBriefSessionError(
            f"{capability} returned {result.status!r}"
            + (f": {details}" if details else ".")
            + f" Inspect {session_root}."
        )


__all__ = [
    "ResearchBriefSessionError",
    "ResearchBriefSessionRequest",
    "ResearchBriefSessionResult",
    "new_research_brief_root",
    "run_research_brief_session",
]
