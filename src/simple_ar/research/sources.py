from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from simple_ar.literature.models import Paper


@dataclass(frozen=True)
class SearchQuery:
    """A source-agnostic literature search request."""

    query: str
    max_results: int = 10
    filters: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class SearchResponse:
    """A source-agnostic literature search response."""

    source: str
    query: str
    papers: list[Paper]
    status: str = "ok"
    message: str = ""


class LiteratureConnector(Protocol):
    """Protocol implemented by metadata and document source connectors."""

    source_name: str

    def search(self, request: SearchQuery) -> SearchResponse:
        """Search a literature source and return normalized papers."""
        ...
