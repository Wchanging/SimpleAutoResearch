from __future__ import annotations

from simple_ar.literature.semantic_scholar_client import SemanticScholarSearchClient
from simple_ar.research.sources.base import SearchQuery, SearchResponse


class SemanticScholarConnector:
    """Research source connector backed by Semantic Scholar Graph API."""

    source_name = "semantic_scholar"

    def __init__(self, client: SemanticScholarSearchClient | None = None) -> None:
        self._client = client or SemanticScholarSearchClient()

    def search(self, request: SearchQuery) -> SearchResponse:
        """Search Semantic Scholar and return a source-agnostic response."""
        papers = self._client.search(request.query, max_results=request.max_results)
        return SearchResponse(
            source=self.source_name,
            query=request.query,
            papers=papers,
            status="ok",
        )
