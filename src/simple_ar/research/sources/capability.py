"""Small standalone search capability over registered literature providers.

The capability owns provider invocation and failure normalization only. Query
planning, candidate selection, caching, document ingest, and stage artifact
projection remain policies of their existing callers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Mapping

from simple_ar.core.capabilities import CapabilityContext, CapabilityResult
from simple_ar.literature.models import Paper
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


@dataclass(frozen=True, slots=True)
class SearchResult:
    """Normalized result for a multi-provider search attempt.

    ``responses`` retains one response per provider/query pair, including
    failures. ``papers`` is the flattened provider output in call order; the
    existing retrieval policy remains responsible for deduplication and
    selection.
    """

    status: SearchStatus
    responses: tuple[SearchResponse, ...]
    papers: tuple[Paper, ...] = ()
    diagnostics: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """Return a compact, JSON-serializable result summary."""
        return {
            "schema_version": "search_result.v1",
            "status": self.status,
            "paper_count": len(self.papers),
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
        responses: list[SearchResponse] = []
        diagnostics = [str(item) for item in data.get("diagnostics", [])]
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


def run_search_capability(
    *,
    context: CapabilityContext,
    request: SearchRequest,
    registry: SearchProviderRegistry,
) -> CapabilityResult:
    """Persist one explicit search handoff for a controller-managed attempt.

    Provider failures remain visible in the handoff and partial results remain
    usable.  The adapter does not deduplicate, download, retry, or silently
    turn an empty/failed search into a successful result.
    """

    result = search_sources(request, registry=registry)
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
        },
        provenance={
            "capability": "search",
            "result_schema": "search_handoff.v1",
        },
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
        return "partial" if any(not _response_succeeded(response) for response in responses) else "completed"
    return "partial" if any(not _response_succeeded(response) for response in responses) else "empty"


__all__ = [
    "SearchRequest",
    "SearchResult",
    "SearchStatus",
    "run_search_capability",
    "search_sources",
]
