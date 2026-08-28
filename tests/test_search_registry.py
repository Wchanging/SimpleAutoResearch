from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from simple_ar.core.artifacts import read_jsonl, write_text
from simple_ar.core.pipeline import Context
from simple_ar.core.stages import Stage
from simple_ar.literature.models import Paper
from simple_ar.pipeline_stages.research import execute_search
from simple_ar.research.sources import SearchProviderRegistry, default_search_provider_registry
from simple_ar.research.sources.base import SearchQuery, SearchResponse


TEST_ROOT = Path(__file__).resolve().parents[1] / ".tmp_tests"


class SearchProviderRegistryTests(unittest.TestCase):
    def test_registry_registers_and_resolves_lazy_connectors(self) -> None:
        calls: list[str] = []

        class Connector:
            source_name = "fixture"

            def search(self, request: SearchQuery) -> SearchResponse:
                return SearchResponse(
                    source=self.source_name,
                    query=request.query,
                    papers=[],
                )

        registry = SearchProviderRegistry()
        registry.register(" fixture ", lambda: calls.append("created") or Connector())

        self.assertEqual(registry.names(), ("fixture",))
        self.assertTrue(registry.has("fixture"))
        self.assertEqual(calls, [])
        self.assertEqual(registry.resolve("fixture").source_name, "fixture")
        self.assertEqual(calls, ["created"])

    def test_registry_rejects_duplicate_unknown_and_invalid_providers(self) -> None:
        registry = SearchProviderRegistry({"fixture": lambda: object()})

        with self.assertRaises(ValueError):
            registry.register("fixture", lambda: object())
        with self.assertRaises(TypeError):
            registry.resolve("fixture")
        with self.assertRaisesRegex(KeyError, "Unknown search provider"):
            registry.resolve("missing")

        class MissingSourceName:
            def search(self, request: SearchQuery) -> SearchResponse:
                return SearchResponse(source="fixture", query=request.query, papers=[])

        registry.register("missing-name", MissingSourceName)
        with self.assertRaisesRegex(TypeError, "source_name"):
            registry.resolve("missing-name")

    def test_factory_errors_are_not_misreported_as_unknown_provider(self) -> None:
        def failing_factory():
            raise KeyError("client")

        registry = SearchProviderRegistry({"fixture": failing_factory})

        with self.assertRaisesRegex(KeyError, "client"):
            registry.resolve("fixture")

    def test_default_registry_exposes_only_builtin_provider_names(self) -> None:
        registry = default_search_provider_registry()

        self.assertEqual(
            registry.names(),
            ("arxiv", "local_files", "openalex", "semantic_scholar"),
        )

    def test_execute_search_accepts_a_replacement_provider_registry(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            run_dir = root / "run"
            write_text(run_dir / "01-plan" / "problem.md", "# Problem\nStudy a fixture source.\n")
            ctx = Context(
                run_dir,
                "fixture topic",
                config={
                    "research_sources": ["custom_source"],
                    "max_papers": 1,
                    "allow_fixture_fallback": False,
                },
                current_stage=Stage.SEARCH,
            )

            class ReplacementConnector:
                source_name = "custom_source"

                def search(self, request: SearchQuery) -> SearchResponse:
                    return SearchResponse(
                        source=self.source_name,
                        query=request.query,
                        papers=[
                            Paper(
                                id="replacement-paper",
                                title="Replacement Paper",
                                authors=[],
                                abstract="A fixture result from a replacement connector.",
                                url="https://example.test/replacement",
                                source=self.source_name,
                                source_id="replacement-paper",
                            )
                        ],
                    )

            registry = SearchProviderRegistry({"custom_source": ReplacementConnector})
            with patch("simple_ar.pipeline_stages.research.put_cache", return_value=None):
                execute_search(ctx, provider_registry=registry)

            papers = read_jsonl(ctx.run_dir / "02-search" / "papers.jsonl")
            self.assertEqual([paper["id"] for paper in papers], ["replacement-paper"])


if __name__ == "__main__":
    unittest.main()
