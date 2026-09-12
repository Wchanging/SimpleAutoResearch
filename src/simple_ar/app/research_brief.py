"""Compatibility request/result projection for the canonical research application.

The research-brief command requests a literature summary from the same lifecycle
as research-session. No stages, fixed attempt IDs or transition policy live here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from simple_ar.core import ArtifactRef, AttemptManifest, DecisionRecord
from simple_ar.app.session_roots import new_research_session_root
from simple_ar.app.research_application import (
    ResearchApplicationError, ResearchApplicationServices, create_session,
)
from simple_ar.app.research_intake import brief_from_legacy_request
from simple_ar.research.brief import ResearchBriefResult
from simple_ar.research.documents.ingest import DocumentBundle
from simple_ar.research.planning.capability import ResearchPlanResult
from simple_ar.research.sources import SearchProviderRegistry, SearchResult, default_search_provider_registry


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


def new_research_brief_root(output_root: str | Path, topic: str) -> Path:
    """Create a unique session directory below an application output root."""
    return new_research_session_root(output_root, topic)


def run_research_brief_session(
    request: ResearchBriefSessionRequest,
    *,
    search_registry: SearchProviderRegistry | None = None,
) -> ResearchBriefSessionResult:
    """Translate the legacy request and project canonical persisted outputs."""
    services = ResearchApplicationServices(
        llm_client=request.llm_client if request.use_llm else None,
        search_registry=search_registry or default_search_provider_registry(
            local_documents=(str(path) for path in request.local_documents),
        ),
        max_results=request.max_results,
        max_chunks=request.max_chunks,
        idea_limit=request.idea_limit,
        cache_dir=request.cache_dir,
        extraction_dir=request.extraction_dir,
        config=_planning_config(request),
    )
    try:
        app = create_session(brief_from_legacy_request(request), root=request.session_root, services=services)
        view = app.advance(max_actions=services.max_attempts)
    except ResearchApplicationError as exc:
        raise ResearchBriefSessionError(str(exc)) from exc
    if "synthesis" not in view.state_refs:
        raise ResearchBriefSessionError(
            f"{view.status_reason} Inspect {request.session_root}."
        )
    return ResearchBriefSessionResult(
        session_root=request.session_root,
        plan=app._load_plan(),
        search=app._load_search(),
        documents=app._load_documents(),
        brief=ResearchBriefResult.from_parts(app._load_read(), app._load_synthesis()),
        brief_ref=view.state_refs["synthesis"],
        attempts=app.controller.list_attempts(),
        decisions=tuple(app.controller.manifest.decisions),
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


__all__ = [
    "ResearchBriefSessionError",
    "ResearchBriefSessionRequest",
    "ResearchBriefSessionResult",
    "new_research_brief_root",
    "run_research_brief_session",
]
