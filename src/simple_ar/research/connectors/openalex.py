from __future__ import annotations

from simple_ar.literature.openalex_client import OpenAlexSearchClient
from simple_ar.research.sources.base import SearchQuery, SearchResponse


class OpenAlexConnector:
    """Research source connector backed by the existing OpenAlex client."""

    source_name = "openalex"

    def __init__(self, client: OpenAlexSearchClient | None = None) -> None:
        self._client = client or OpenAlexSearchClient()

    def search(self, request: SearchQuery) -> SearchResponse:
        """Search OpenAlex and return a source-agnostic response."""
        papers = self._client.search(request.query, max_results=request.max_results)
        return SearchResponse(
            source=self.source_name,
            query=request.query,
            papers=papers,
            status="ok",
        )
