"""Source ports and provider implementations.

Keep package initialization dependency-free.  Connectors import
``sources.base`` directly, so eager re-exports here would create a circular
import during connector loading.
"""

__all__ = ["SearchProviderRegistry", "default_search_provider_registry"]


def __getattr__(name: str):
    """Lazily expose registry helpers without importing connectors eagerly."""
    if name in __all__:
        from simple_ar.research.sources.registry import (
            SearchProviderRegistry,
            default_search_provider_registry,
        )

        return {
            "SearchProviderRegistry": SearchProviderRegistry,
            "default_search_provider_registry": default_search_provider_registry,
        }[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
