from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping

from simple_ar.literature.arxiv_client import ArxivSearchClient
from simple_ar.literature.openalex_client import OpenAlexSearchClient
from simple_ar.literature.semantic_scholar_client import SemanticScholarSearchClient
from simple_ar.research.connectors.arxiv import ArxivConnector
from simple_ar.research.connectors.local_files import LocalFileConnector
from simple_ar.research.connectors.openalex import OpenAlexConnector
from simple_ar.research.connectors.semantic_scholar import SemanticScholarConnector
from simple_ar.research.sources.base import LiteratureConnector


ConnectorFactory = Callable[[], LiteratureConnector]


class SearchProviderRegistry:
    """Explicit registry for source-agnostic literature connectors.

    The registry owns provider construction only. Search policy, caching,
    candidate selection, and artifact writing remain in the caller so that a
    connector can be replaced without changing the research workflow.
    """

    def __init__(self, providers: Mapping[str, ConnectorFactory] | None = None) -> None:
        self._providers: dict[str, ConnectorFactory] = {}
        for name, factory in (providers or {}).items():
            self.register(name, factory)

    def register(
        self,
        name: str,
        factory: ConnectorFactory,
        *,
        replace: bool = False,
    ) -> None:
        normalized = name.strip()
        if not normalized:
            raise ValueError("Search provider name cannot be empty.")
        if not callable(factory):
            raise TypeError("Search provider factory must be callable.")
        if normalized in self._providers and not replace:
            raise ValueError(f"Search provider already registered: {normalized}")
        self._providers[normalized] = factory

    def has(self, name: str) -> bool:
        return name.strip() in self._providers

    def resolve(self, name: str) -> LiteratureConnector:
        normalized = name.strip()
        try:
            factory = self._providers[normalized]
        except KeyError as exc:
            raise KeyError(f"Unknown search provider: {normalized}") from exc
        connector = factory()
        if not callable(getattr(connector, "search", None)):
            raise TypeError(
                f"Search provider {normalized} returned {type(connector).__name__}; "
                "expected a LiteratureConnector."
            )
        source_name = getattr(connector, "source_name", None)
        if not isinstance(source_name, str) or not source_name.strip():
            raise TypeError(
                f"Search provider {normalized} returned {type(connector).__name__}; "
                "source_name must be a non-empty string."
            )
        return connector

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._providers))


def default_search_provider_registry(
    *,
    local_documents: Iterable[str] = (),
    arxiv_page_size: int = 10,
    connector_factories: Mapping[str, ConnectorFactory] | None = None,
) -> SearchProviderRegistry:
    """Build the built-in registry without changing provider behavior."""

    documents = tuple(str(path) for path in local_documents)
    factories: dict[str, ConnectorFactory] = {
        "openalex": lambda: OpenAlexConnector(OpenAlexSearchClient()),
        "semantic_scholar": lambda: SemanticScholarConnector(SemanticScholarSearchClient()),
        "arxiv": lambda: ArxivConnector(ArxivSearchClient(page_size=arxiv_page_size)),
        "local_files": lambda: LocalFileConnector(list(documents)),
    }
    factories.update(connector_factories or {})
    return SearchProviderRegistry(factories)
