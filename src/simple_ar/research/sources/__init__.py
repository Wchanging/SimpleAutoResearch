"""Source ports and provider implementations.

Keep package initialization dependency-free.  Connectors import
``sources.base`` directly, so eager re-exports here would create a circular
import during connector loading.
"""

__all__ = [
    "SearchProviderRegistry",
    "SearchRequest",
    "SearchResult",
    "SearchStatus",
    "default_search_provider_registry",
    "run_search_capability",
    "search_sources",
]


def __getattr__(name: str):
    """Lazily expose registry helpers without importing connectors eagerly."""
    if name in {"SearchProviderRegistry", "default_search_provider_registry"}:
        from simple_ar.research.sources.registry import (
            SearchProviderRegistry,
            default_search_provider_registry,
        )

        return {
            "SearchProviderRegistry": SearchProviderRegistry,
            "default_search_provider_registry": default_search_provider_registry,
        }[name]
    if name in {
        "SearchRequest",
        "SearchResult",
        "SearchStatus",
        "run_search_capability",
        "search_sources",
    }:
        from simple_ar.research.sources.capability import (
            SearchRequest,
            SearchResult,
            SearchStatus,
            run_search_capability,
            search_sources,
        )

        return {
            "SearchRequest": SearchRequest,
            "SearchResult": SearchResult,
            "SearchStatus": SearchStatus,
            "run_search_capability": run_search_capability,
            "search_sources": search_sources,
        }[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
