from __future__ import annotations

from simple_ar.literature.openalex_client import OpenAlexSearchClient
from simple_ar.research.sources.base import SearchQuery, SearchResponse, temporal_scope


class OpenAlexConnector:
    """Research source connector backed by the existing OpenAlex client."""

    source_name = "openalex"

    def __init__(self, client: OpenAlexSearchClient | None = None) -> None:
        self._client = client or OpenAlexSearchClient()

    def search(self, request: SearchQuery) -> SearchResponse:
        """Search OpenAlex and return a source-agnostic response."""
        scope = temporal_scope(request.filters)
        options = {"year_range": (scope["start_year"], scope["end_year"])} if scope else {}
        papers = self._client.search(request.query, max_results=request.max_results, **options)
        return SearchResponse(
            source=self.source_name,
            query=request.query,
            papers=papers,
            status="ok",
        )
