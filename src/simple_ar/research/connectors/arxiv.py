from __future__ import annotations

from simple_ar.literature.arxiv_client import ArxivSearchClient
from simple_ar.research.sources.base import SearchQuery, SearchResponse


class ArxivConnector:
    """Research source connector backed by the existing arXiv client."""

    source_name = "arxiv"

    def __init__(self, client: ArxivSearchClient | None = None) -> None:
        self._client = client or ArxivSearchClient()

    def search(self, request: SearchQuery) -> SearchResponse:
        """Search arXiv and return a source-agnostic response."""
        papers = self._client.search(request.query, max_results=request.max_results)
        return SearchResponse(
            source=self.source_name,
            query=request.query,
            papers=papers,
            status="ok",
        )
