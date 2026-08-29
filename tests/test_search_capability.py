from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from simple_ar.core import CapabilityRegistry, SessionController
from simple_ar.literature.models import Paper
from simple_ar.research.sources import (
    SearchProviderRegistry,
    SearchRequest,
    SearchResult,
    run_search_capability,
    search_sources,
)
from simple_ar.research.sources.base import SearchQuery, SearchResponse


class SearchCapabilityTests(unittest.TestCase):
    def test_successful_multi_provider_search_preserves_call_order(self) -> None:
        calls: list[tuple[str, str, int]] = []

        class Connector:
            def __init__(self, name: str) -> None:
                self.source_name = name

            def search(self, request: SearchQuery) -> SearchResponse:
                calls.append((self.source_name, request.query, request.max_results))
                return SearchResponse(
                    source=self.source_name,
                    query=request.query,
                    papers=[
                        Paper(
                            id=f"{self.source_name}-paper",
                            title="Fixture paper",
                            authors=[],
                            abstract="fixture",
                            url="https://example.test/paper",
                            source=self.source_name,
                        )
                    ],
                )

        result = search_sources(
            SearchRequest(
                queries=("first", "second"),
                providers=("one", "two"),
                max_results_per_query=3,
            ),
            registry=SearchProviderRegistry(
                {
                    "one": lambda: Connector("one"),
                    "two": lambda: Connector("two"),
                }
            ),
        )

        self.assertEqual(result.status, "completed")
        self.assertEqual(len(result.responses), 4)
        self.assertEqual(len(result.papers), 4)
        self.assertEqual(
            calls,
            [("one", "first", 3), ("one", "second", 3), ("two", "first", 3), ("two", "second", 3)],
        )

    def test_partial_failure_is_not_reported_as_empty_success(self) -> None:
        class FailingConnector:
            source_name = "broken"

            def search(self, request: SearchQuery) -> SearchResponse:
                raise RuntimeError("service unavailable")

        class WorkingConnector:
            source_name = "working"

            def search(self, request: SearchQuery) -> SearchResponse:
                return SearchResponse(
                    source=self.source_name,
                    query=request.query,
                    papers=[],
                )

        result = search_sources(
            SearchRequest(queries=("query",), providers=("broken", "working")),
            registry=SearchProviderRegistry(
                {"broken": FailingConnector, "working": WorkingConnector}
            ),
        )

        self.assertEqual(result.status, "partial")
        self.assertEqual(result.responses[0].status, "failed")
        self.assertEqual(len(result.diagnostics), 1)

    def test_all_provider_failures_are_failed(self) -> None:
        registry = SearchProviderRegistry({"missing": lambda: (_ for _ in ()).throw(KeyError("client"))})

        result = search_sources(
            SearchRequest(queries=("query",), providers=("missing",)),
            registry=registry,
        )

        self.assertEqual(result.status, "failed")
        self.assertFalse(result.papers)
        self.assertIn("KeyError", result.diagnostics[0])

    def test_all_successful_empty_responses_are_empty(self) -> None:
        class EmptyConnector:
            source_name = "empty"

            def search(self, request: SearchQuery) -> SearchResponse:
                return SearchResponse(source=self.source_name, query=request.query, papers=[])

        result = search_sources(
            SearchRequest(queries=("query",), providers=("empty",)),
            registry=SearchProviderRegistry({"empty": EmptyConnector}),
        )

        self.assertEqual(result.status, "empty")
        self.assertEqual(result.to_dict()["schema_version"], "search_result.v1")

    def test_search_capability_persists_full_handoff_without_duplicate_rows(self) -> None:
        paper = Paper(
            id="paper-1",
            title="Fixture paper",
            authors=[],
            abstract="A fixture result.",
            url="https://example.test/paper",
            source="fixture",
        )

        class Connector:
            source_name = "fixture"

            def search(self, request: SearchQuery) -> SearchResponse:
                return SearchResponse(
                    source=self.source_name,
                    query=request.query,
                    papers=[paper],
                )

        with tempfile.TemporaryDirectory() as tmp:
            registry = CapabilityRegistry()
            registry.register("search", run_search_capability)
            controller = SessionController.create(
                Path(tmp),
                session_id="search-handoff",
                topic="fixture search",
                registry=registry,
            )
            result, decision = controller.execute(
                "search",
                attempt_id="attempt-001",
                request=SearchRequest(queries=("fixture",), providers=("fixture",)),
                registry=SearchProviderRegistry({"fixture": Connector}),
            )

            self.assertEqual(result.status, "completed")
            self.assertEqual(decision.action, "accept")
            handoff = controller.store.read_json(
                "attempts/attempt-001/search_result.json"
            )
            self.assertEqual(handoff["schema_version"], "search_handoff.v1")
            self.assertEqual([row["id"] for row in handoff["papers"]], ["paper-1"])
            self.assertEqual(handoff["responses"][0]["paper_ids"], ["paper-1"])
            self.assertEqual(handoff["responses"][0]["message"], "")
            restored = SearchResult.from_handoff_dict(handoff)
            self.assertEqual(restored.status, "completed")
            self.assertEqual(restored.papers[0].id, "paper-1")
            self.assertEqual(restored.responses[0].papers[0].title, "Fixture paper")

    def test_search_handoff_reports_broken_paper_reference(self) -> None:
        restored = SearchResult.from_handoff_dict(
            {
                "schema_version": "search_handoff.v1",
                "status": "partial",
                "papers": [],
                "responses": [
                    {
                        "source": "fixture",
                        "query": "query",
                        "status": "ok",
                        "paper_ids": ["missing-paper"],
                    }
                ],
            }
        )

        self.assertEqual(restored.status, "partial")
        self.assertFalse(restored.responses[0].papers)
        self.assertIn("missing-paper", restored.diagnostics[0])

    def test_request_rejects_empty_queries_providers_and_limits(self) -> None:
        with self.assertRaises(ValueError):
            SearchRequest(queries=(), providers=("fixture",))
        with self.assertRaises(ValueError):
            SearchRequest(queries=("query",), providers=())
        with self.assertRaises(ValueError):
            SearchRequest(queries=("query",), providers=("fixture",), max_results_per_query=0)


if __name__ == "__main__":
    unittest.main()
