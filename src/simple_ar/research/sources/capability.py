"""Small standalone search capability over registered literature providers.

The capability owns provider invocation and failure normalization. Callers
still supply the query/selection policy, while this boundary can apply that
bounded policy, retain raw provider responses, and optionally persist/recover
metadata through an explicit cache directory. Document ingest and stage
artifact projection remain separate capabilities.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal, Mapping

from simple_ar.core.capabilities import CapabilityContext, CapabilityResult
from simple_ar.literature.cache import get_cached, put_cache
from simple_ar.literature.models import Paper
from simple_ar.research.contracts import QueryPlan, ResearchQuestion
from simple_ar.research.sources.base import SearchQuery, SearchResponse
from simple_ar.research.sources.registry import SearchProviderRegistry


SearchStatus = Literal["completed", "partial", "empty", "failed"]


@dataclass(frozen=True, slots=True)
class SearchRequest:
    """Provider-neutral request for one or more planned search queries."""

    queries: tuple[str, ...]
    providers: tuple[str, ...]
    max_results_per_query: int = 10
    filters: dict[str, object] = field(default_factory=dict)
    cache_dir: Path | None = None
    cache_enabled: bool = True

    def __post_init__(self) -> None:
        queries = tuple(query.strip() for query in self.queries if query.strip())
        providers = tuple(provider.strip() for provider in self.providers if provider.strip())
        if not queries:
            raise ValueError("SearchRequest requires at least one query.")
        if not providers:
            raise ValueError("SearchRequest requires at least one provider.")
        if self.max_results_per_query < 1:
            raise ValueError("max_results_per_query must be positive.")
        object.__setattr__(self, "queries", queries)
        object.__setattr__(self, "providers", providers)
        if self.cache_dir is not None:
            object.__setattr__(self, "cache_dir", Path(self.cache_dir))


@dataclass(frozen=True, slots=True)
class SearchSelectionPolicy:
    """Bounded, deterministic selection policy for one search handoff.

    Provider invocation remains independent from selection.  The canonical
    session supplies this policy after planning so raw provider responses stay
    inspectable while downstream ingest receives one deduplicated, budgeted
    paper set.  This is the small portion of the former search-stage policy
    that is useful to every research entry point.
    """

    topic: str
    questions: tuple[ResearchQuestion, ...]
    query_plan: QueryPlan
    max_documents: int

    def __post_init__(self) -> None:
        if not self.topic.strip():
            raise ValueError("SearchSelectionPolicy.topic cannot be empty.")
        if self.max_documents < 1:
            raise ValueError("SearchSelectionPolicy.max_documents must be positive.")
        object.__setattr__(self, "questions", tuple(self.questions))


@dataclass(frozen=True, slots=True)
class SearchResult:
    """Normalized result for a multi-provider search attempt.

    ``responses`` retains one response per provider/query pair, including
    failures. ``papers`` is the flattened raw provider output in call order;
    ``selected_papers`` is the optional bounded result of an explicit
    ``SearchSelectionPolicy``.
    """

    status: SearchStatus
    responses: tuple[SearchResponse, ...]
    papers: tuple[Paper, ...] = ()
    diagnostics: tuple[str, ...] = ()
    selected_papers: tuple[Paper, ...] = ()
    selection_rows: tuple[dict[str, Any], ...] = ()
    coverage_report: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return a compact, JSON-serializable result summary."""
        return {
            "schema_version": "search_result.v1",
            "status": self.status,
            "paper_count": len(self.papers),
            "selected_paper_count": len(self.selected_papers),
            "response_count": len(self.responses),
            "responses": [
                {
                    "source": response.source,
                    "query": response.query,
                    "status": response.status,
                    "paper_count": len(response.papers),
                    "message": response.message,
                }
                for response in self.responses
            ],
            "selection_count": len(self.selection_rows),
            "coverage_status": str(self.coverage_report.get("status") or "unknown"),
            "diagnostics": list(self.diagnostics),
        }

    def to_handoff_dict(self) -> dict[str, Any]:
        """Return provider output needed by a downstream document capability.

        The status view above intentionally omits paper rows.  This handoff
        keeps one copy of the normalized metadata and only references papers
        by ID inside each provider response, so later stages can distinguish
        usable results from source failures without copying full paper rows.
        """

        return {
            "schema_version": "search_handoff.v1",
            "status": self.status,
            "papers": [paper.to_row() for paper in self.papers],
            "selected_paper_ids": [paper.id for paper in self.selected_papers],
            "responses": [
                {
                    "source": response.source,
                    "query": response.query,
                    "status": response.status,
                    "paper_ids": [paper.id for paper in response.papers],
                    "message": response.message,
                }
                for response in self.responses
            ],
            "selection": [dict(row) for row in self.selection_rows],
            "coverage": dict(self.coverage_report),
            "diagnostics": list(self.diagnostics),
        }

    @classmethod
    def from_handoff_dict(cls, data: Mapping[str, Any]) -> "SearchResult":
        """Restore a persisted ``search_handoff.v1`` without network access."""
        if str(data.get("schema_version") or "") != "search_handoff.v1":
            raise ValueError("Expected a search_handoff.v1 object.")
        status = str(data.get("status") or "")
        if status not in {"completed", "partial", "empty", "failed"}:
            raise ValueError(f"Unsupported search handoff status: {status!r}")

        papers = tuple(
            Paper.from_row(row)
            for row in data.get("papers", [])
            if isinstance(row, Mapping)
        )
        paper_by_id = {paper.id: paper for paper in papers}
        selected_ids = [
            str(item).strip()
            for item in data.get("selected_paper_ids", [])
            if str(item).strip()
        ]
        responses: list[SearchResponse] = []
        diagnostics = [str(item) for item in data.get("diagnostics", [])]
        missing_selected = [paper_id for paper_id in selected_ids if paper_id not in paper_by_id]
        if missing_selected:
            diagnostics.append(
                "Search handoff references missing selected papers: "
                + ", ".join(missing_selected)
            )
        for row in data.get("responses", []):
            if not isinstance(row, Mapping):
                continue
            paper_ids = [str(item) for item in row.get("paper_ids", [])]
            missing = [paper_id for paper_id in paper_ids if paper_id not in paper_by_id]
            if missing:
                diagnostics.append(
                    "Search handoff response references missing papers: "
                    + ", ".join(missing)
                )
            responses.append(
                SearchResponse(
                    source=str(row.get("source") or "unknown"),
                    query=str(row.get("query") or ""),
                    papers=[paper_by_id[paper_id] for paper_id in paper_ids if paper_id in paper_by_id],
                    status=str(row.get("status") or "ok"),
                    message=str(row.get("message") or ""),
                )
            )
        return cls(
            status=status,  # type: ignore[arg-type]
            responses=tuple(responses),
            papers=papers,
            diagnostics=tuple(diagnostics),
            selected_papers=tuple(
                paper_by_id[paper_id]
                for paper_id in selected_ids
                if paper_id in paper_by_id
            )
            if "selected_paper_ids" in data
            else papers,
            selection_rows=tuple(
                dict(row) for row in data.get("selection", []) if isinstance(row, Mapping)
            ),
            coverage_report=(
                dict(data.get("coverage"))
                if isinstance(data.get("coverage"), Mapping)
                else {}
            ),
        )


def search_sources(
    request: SearchRequest,
    *,
    registry: SearchProviderRegistry,
) -> SearchResult:
    """Run the requested provider/query pairs without writing run artifacts.

    Provider construction and calls are isolated per pair. A source failure is
    retained as a failed response instead of being mistaken for an empty
    successful search. Successful responses remain usable when another source
    is unavailable.
    """
    responses: list[SearchResponse] = []
    papers: list[Paper] = []
    diagnostics: list[str] = []

    for provider_name in request.providers:
        for query in request.queries:
            response = _run_provider(
                registry,
                provider_name,
                SearchQuery(
                    query=query,
                    max_results=request.max_results_per_query,
                    filters=dict(request.filters),
                ),
            )
            response = _apply_optional_cache(
                response,
                query=query,
                provider_name=provider_name,
                request=request,
            )
            responses.append(response)
            if _response_succeeded(response):
                papers.extend(response.papers)
            elif response.message:
                diagnostics.append(response.message)

    return SearchResult(
        status=_result_status(responses, papers),
        responses=tuple(responses),
        papers=tuple(papers),
        diagnostics=tuple(diagnostics),
    )


def _apply_optional_cache(
    response: SearchResponse,
    *,
    query: str,
    provider_name: str,
    request: SearchRequest,
) -> SearchResponse:
    """Persist successful metadata and recover cached metadata when enabled.

    Cache use is opt-in through ``cache_dir`` and ``cache_enabled``.  A cache
    miss never changes a provider failure, while a cache hit keeps the live
    error in the response message and marks the result as ``cached`` so a
    report can distinguish it from a fresh provider response.
    """

    if not request.cache_enabled or request.cache_dir is None:
        return response
    if _response_succeeded(response) and response.papers:
        try:
            put_cache(
                query,
                provider_name,
                request.max_results_per_query,
                [paper.to_row() for paper in response.papers],
                cache_dir=request.cache_dir,
            )
        except (OSError, TypeError, ValueError):
            # Search is still valid; cache persistence is an optimization.
            pass
        return response
    if _response_succeeded(response):
        return response
    try:
        cached_rows = get_cached(
            query,
            provider_name,
            request.max_results_per_query,
            cache_dir=request.cache_dir,
        )
    except (OSError, TypeError, ValueError):
        cached_rows = None
    if not cached_rows:
        return response
    cached_papers: list[Paper] = []
    for row in cached_rows:
        if not isinstance(row, Mapping):
            continue
        try:
            cached_papers.append(Paper.from_row(dict(row)))
        except (KeyError, TypeError, ValueError):
            continue
    if not cached_papers:
        return response
    return SearchResponse(
        source=response.source,
        query=response.query,
        papers=cached_papers,
        status="cached",
        message=(
            f"Live provider failed ({response.message or 'unknown error'}); "
            "using cached metadata."
        ),
    )


def run_search_capability(
    *,
    context: CapabilityContext,
    request: SearchRequest,
    registry: SearchProviderRegistry,
    selection_policy: SearchSelectionPolicy | None = None,
) -> CapabilityResult:
    """Persist one explicit search handoff for a controller-managed attempt.

    Provider failures remain visible in the handoff and partial results remain
    usable.  The adapter does not deduplicate, download, retry, or silently
    turn an empty/failed search into a successful result.
    """

    result = search_sources(request, registry=registry)
    if selection_policy is not None:
        result = select_search_result(result, policy=selection_policy)
    diagnostics = list(result.diagnostics)
    if result.status == "empty":
        diagnostics.append("Search returned no papers.")
    output = context.store.write_json(
        "search_result.json",
        result.to_handoff_dict(),
        kind="search_result",
        schema="search_handoff.v1",
        producer="research.search",
    )
    capability_status = {
        "completed": "completed",
        "partial": "partial",
        "empty": "partial",
        "failed": "failed",
    }[result.status]
    return CapabilityResult(
        status=capability_status,  # type: ignore[arg-type]
        artifacts=(output,),
        diagnostics=tuple(diagnostics),
        usage={
            "query_count": len(request.queries),
            "provider_count": len(request.providers),
            "response_count": len(result.responses),
            "paper_count": len(result.papers),
            "selected_paper_count": len(result.selected_papers),
            "selection_count": len(result.selection_rows),
        },
        provenance={
            "capability": "search",
            "result_schema": "search_handoff.v1",
        },
    )


def select_search_result(
    result: SearchResult,
    *,
    policy: SearchSelectionPolicy,
) -> SearchResult:
    """Apply the canonical retrieval policy while retaining raw responses.

    The former legacy Search stage already had this behavior, but the new
    capability path previously passed every raw paper straight to ingest.  A
    selected paper list and auditable coverage rows now travel in the same
    search handoff; no second stage or hidden workspace scan is introduced.
    """

    from simple_ar.research.evidence.coverage import build_coverage_report
    from simple_ar.research.evidence.retrieval import (
        RetrievalCandidate,
        select_retrieval_candidates,
    )

    query_numbers = {
        query: index
        for index, query in enumerate(policy.query_plan.queries, start=1)
        if query.strip()
    }
    specs = {
        str(row.get("query") or "").strip(): row
        for row in policy.query_plan.query_specs
        if isinstance(row, Mapping) and str(row.get("query") or "").strip()
    }
    candidates: list[RetrievalCandidate] = []
    retrieval_rows: list[dict[str, Any]] = []
    for response_index, response in enumerate(result.responses, start=1):
        query = response.query.strip()
        query_spec = specs.get(query, {})
        query_index = query_numbers.get(query, response_index)
        retrieval_rows.append(
            {
                "schema_version": "retrieval_round.v1",
                "round": 1,
                "query_index": query_index,
                "query": query,
                "source": response.source,
                "status": response.status,
                "returned": len(response.papers),
                "message": response.message,
            }
        )
        if response.status.strip().lower() in {"failed", "error", "blocked"}:
            continue
        facet = str(query_spec.get("facet") or "").strip()
        for paper in response.papers:
            candidates.append(
                RetrievalCandidate(
                    paper=paper,
                    source=response.source,
                    query=query,
                    query_index=query_index,
                    round_index=1,
                    facet=facet,
                    returned_source=response.source,
                )
            )
    selected, selection_rows = select_retrieval_candidates(
        candidates,
        max_documents=policy.max_documents,
        negative_terms=list(policy.query_plan.negative_terms),
        priority_facets=list(policy.query_plan.required_facets),
    )
    coverage = build_coverage_report(
        topic=policy.topic,
        questions=list(policy.questions),
        query_plan=policy.query_plan,
        selection_rows=selection_rows,
        retrieval_rows=retrieval_rows,
        max_documents=policy.max_documents,
        next_query_limit=0,
    )
    return replace(
        result,
        selected_papers=tuple(selected),
        selection_rows=tuple(selection_rows),
        coverage_report=coverage,
    )


def _run_provider(
    registry: SearchProviderRegistry,
    provider_name: str,
    request: SearchQuery,
) -> SearchResponse:
    try:
        connector = registry.resolve(provider_name)
        response = connector.search(request)
        if not isinstance(response, SearchResponse):
            raise TypeError(
                f"provider returned {type(response).__name__}; expected SearchResponse"
            )
        return response
    except Exception as exc:
        message = (
            f"Search provider {provider_name!r} failed for query {request.query!r}: "
            f"{type(exc).__name__}: {exc}"
        )
        return SearchResponse(
            source=provider_name,
            query=request.query,
            papers=[],
            status="failed",
            message=message,
        )


def _response_succeeded(response: SearchResponse) -> bool:
    return response.status.strip().lower() not in {"failed", "error", "blocked"}


def _result_status(
    responses: list[SearchResponse],
    papers: list[Paper],
) -> SearchStatus:
    if not responses or all(not _response_succeeded(response) for response in responses):
        return "failed"
    if papers:
        if any(not _response_succeeded(response) for response in responses):
            return "partial"
        if any(response.status.strip().lower() in {"cached", "partial"} for response in responses):
            return "partial"
        return "completed"
    return "partial" if any(not _response_succeeded(response) for response in responses) else "empty"


__all__ = [
    "SearchRequest",
    "SearchSelectionPolicy",
    "SearchResult",
    "SearchStatus",
    "run_search_capability",
    "select_search_result",
    "search_sources",
]
