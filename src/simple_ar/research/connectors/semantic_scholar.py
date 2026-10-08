from __future__ import annotations

from simple_ar.literature.semantic_scholar_client import SemanticScholarSearchClient
from simple_ar.research.sources.base import SearchQuery, SearchResponse, temporal_scope


class SemanticScholarConnector:
    """Research source connector backed by Semantic Scholar Graph API."""

    source_name = "semantic_scholar"

    def __init__(self, client: SemanticScholarSearchClient | None = None) -> None:
        self._client = client or SemanticScholarSearchClient()

    def search(self, request: SearchQuery) -> SearchResponse:
        """Search Semantic Scholar and return a source-agnostic response."""
        scope = temporal_scope(request.filters)
        options = {"year_range": (scope["start_year"], scope["end_year"])} if scope else {}
        papers = self._client.search(request.query, max_results=request.max_results, **options)
        return SearchResponse(
            source=self.source_name,
            query=request.query,
            papers=papers,
            status="ok",
        )
