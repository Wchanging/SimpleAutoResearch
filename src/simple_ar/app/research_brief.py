"""User-facing composition of the reusable research capabilities.

This module is deliberately an application workflow, not a second pipeline
implementation. It owns only the order and handoff policy for a small,
useful path from a topic or local documents to an evidence-backed brief.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from simple_ar.core import (
    ArtifactRef,
    AttemptManifest,
    BudgetState,
    CapabilityRegistry,
    DecisionRecord,
    SessionController,
)
from simple_ar.app.session_roots import new_research_session_root
from simple_ar.research.brief import (
    ResearchBriefRequest,
    ResearchBriefResult,
)
from simple_ar.research.documents.ingest import (
    DocumentBundle,
    DocumentIngestRequest,
)
from simple_ar.research.planning.capability import (
    ResearchPlanRequest,
    ResearchPlanResult,
    search_request_from_plan,
)
from simple_ar.research.registry import register_research_capabilities
from simple_ar.research.sources import (
    SearchProviderRegistry,
    SearchResult,
    default_search_provider_registry,
)


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
        names=("plan", "search", "document_ingest", "research_brief"),
        budget=BudgetState(max_attempts=6, max_no_progress=2),
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
        config=_planning_config(request),
        default_query=request.topic,
        default_max_results=request.max_results,
    )
    plan_capability, _ = controller.execute(
        "plan",
        attempt_id="plan-001",
        next_capability="search",
        request=plan_request,
    )
    _require_completed(plan_capability.status, "plan", request.session_root)
    plan_ref = controller.attempt_output_ref(
        "plan-001", kind="research_plan", schema="research_plan.v1"
    )
    plan = ResearchPlanResult.from_handoff_dict(
        controller.store.read_json(plan_ref)
    )

    provider_registry = search_registry or default_search_provider_registry(
        local_documents=(str(path) for path in request.local_documents)
    )
    search_capability, _ = controller.execute(
        "search",
        attempt_id="search-001",
        inputs=(plan_ref,),
        next_capability="document_ingest",
        request=search_request_from_plan(plan),
        registry=provider_registry,
    )
    _require_completed(
        search_capability.status,
        "search",
        request.session_root,
        allow_partial=True,
    )
    search_ref = controller.attempt_output_ref(
        "search-001", kind="search_result", schema="search_handoff.v1"
    )
    search = SearchResult.from_handoff_dict(controller.store.read_json(search_ref))
    if not search.papers:
        raise ResearchBriefSessionError(
            f"Search produced no usable papers; inspect {request.session_root}."
        )

    document_capability, _ = controller.execute(
        "document_ingest",
        attempt_id="document-001",
        inputs=(search_ref,),
        next_capability="research_brief",
        request=DocumentIngestRequest(
            papers=search.papers,
            source_plan=plan.source_plan,
            cache_dir=request.cache_dir or request.session_root / "cache",
            extraction_dir=request.extraction_dir or request.session_root / "documents",
            max_chunks=request.max_chunks,
        ),
    )
    _require_completed(
        document_capability.status,
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

    brief_capability, _ = controller.execute(
        "research_brief",
        attempt_id="brief-001",
        inputs=(document_ref,),
        next_capability=next_capability,
        request=ResearchBriefRequest(
            topic=request.topic,
            bundle=documents,
            idea_limit=request.idea_limit,
        ),
    )
    if brief_capability.status == "blocked":
        raise ResearchBriefSessionError(
            f"Research brief was blocked; inspect {request.session_root}."
        )
    brief_ref = controller.attempt_output_ref(
        "brief-001", kind="research_brief", schema="research_brief.v1"
    )
    brief = ResearchBriefResult.from_handoff_dict(
        controller.store.read_json(brief_ref),
        bundle=documents,
    )
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


def _require_completed(
    status: str,
    capability: str,
    session_root: Path,
    *,
    allow_partial: bool = False,
) -> None:
    accepted = {"completed", "partial"} if allow_partial else {"completed"}
    if status not in accepted:
        raise ResearchBriefSessionError(
            f"{capability} returned {status}; inspect {session_root}."
        )


__all__ = [
    "ResearchBriefSessionError",
    "ResearchBriefSessionRequest",
    "ResearchBriefSessionResult",
    "new_research_brief_root",
    "run_research_brief_session",
]
