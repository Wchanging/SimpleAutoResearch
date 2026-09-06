from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from simple_ar.core import CapabilityRegistry, SessionController
from simple_ar.literature.cache import put_cache
from simple_ar.literature.models import Paper
from simple_ar.research.contracts import QueryPlan, ResearchQuestion
from simple_ar.research.sources import (
    SearchProviderRegistry,
    SearchRequest,
    SearchSelectionPolicy,
    SearchResult,
    run_search_capability,
    select_search_result,
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

    def test_stop_after_papers_bounds_provider_queries(self) -> None:
        calls: list[str] = []

        class Connector:
            source_name = "fixture"

            def search(self, request: SearchQuery) -> SearchResponse:
                calls.append(request.query)
                return SearchResponse(
                    source=self.source_name,
                    query=request.query,
                    papers=[
                        Paper(
                            id=f"paper-{request.query}",
                            title=f"Paper for {request.query}",
                            authors=[],
                            abstract="bounded search fixture",
                            url=f"https://example.test/{request.query}",
                            source=self.source_name,
                        )
                    ],
                )

        result = search_sources(
            SearchRequest(
                queries=("first", "second", "third"),
                providers=("fixture",),
                stop_after_papers=2,
            ),
            registry=SearchProviderRegistry({"fixture": Connector}),
        )

        self.assertEqual(calls, ["first", "second"])
        self.assertEqual(len(result.papers), 2)
        self.assertEqual(result.status, "completed")

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

    def test_optional_cache_recovers_provider_failure_without_hiding_it(self) -> None:
        paper = Paper(
            id="cached-paper",
            title="Cached reliable agents",
            authors=[],
            abstract="Cached metadata for a bounded search.",
            url="https://example.test/cached",
            source="fixture",
        )

        class FailingConnector:
            source_name = "fixture"

            def search(self, request: SearchQuery) -> SearchResponse:
                raise RuntimeError("provider unavailable")

        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp) / "literature"
            put_cache(
                "query",
                "fixture",
                10,
                [paper.to_row()],
                cache_dir=cache_dir,
            )
            result = search_sources(
                SearchRequest(
                    queries=("query",),
                    providers=("fixture",),
                    cache_dir=cache_dir,
                ),
                registry=SearchProviderRegistry({"fixture": FailingConnector}),
            )

        self.assertEqual(result.status, "partial")
        self.assertEqual(result.responses[0].status, "cached")
        self.assertEqual(result.responses[0].papers[0].id, "cached-paper")
        self.assertIn("provider unavailable", result.responses[0].message)

    def test_optional_cache_persists_successful_metadata(self) -> None:
        paper = Paper(
            id="fresh-paper",
            title="Fresh reliable agents",
            authors=[],
            abstract="Fresh metadata.",
            url="https://example.test/fresh",
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
            cache_dir = Path(tmp) / "literature"
            result = search_sources(
                SearchRequest(
                    queries=("query",),
                    providers=("fixture",),
                    cache_dir=cache_dir,
                ),
                registry=SearchProviderRegistry({"fixture": Connector}),
            )
            cached = search_sources(
                SearchRequest(
                    queries=("query",),
                    providers=("fixture",),
                    cache_dir=cache_dir,
                ),
                registry=SearchProviderRegistry(
                    {"fixture": lambda: (_ for _ in ()).throw(RuntimeError("offline"))}
                ),
            )

        self.assertEqual(result.status, "completed")
        self.assertEqual(cached.status, "partial")
        self.assertEqual(cached.responses[0].status, "cached")
        self.assertEqual(cached.papers[0].id, "fresh-paper")

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

    def test_selection_policy_persists_deduplication_and_coverage(self) -> None:
        first = Paper(
            id="paper-1",
            title="Reliable agent method",
            authors=[],
            abstract="A method improves benchmark accuracy.",
            url="https://example.test/one",
            source="fixture",
        )
        duplicate = Paper(
            id="paper-duplicate",
            title="Reliable agent method",
            authors=[],
            abstract="A method improves accuracy.",
            url="https://example.test/two",
            source="fixture",
        )
        second = Paper(
            id="paper-2",
            title="Reliable agent benchmark",
            authors=[],
            abstract="A benchmark measures accuracy.",
            url="https://example.test/three",
            source="fixture",
        )

        class Connector:
            source_name = "fixture"

            def search(self, request: SearchQuery) -> SearchResponse:
                papers = [first, duplicate] if request.query == "method" else [second]
                return SearchResponse(
                    source=self.source_name,
                    query=request.query,
                    papers=papers,
                )

        raw = search_sources(
            SearchRequest(
                queries=("method", "benchmark"),
                providers=("fixture",),
            ),
            registry=SearchProviderRegistry({"fixture": Connector}),
        )
        selected = select_search_result(
            raw,
            policy=SearchSelectionPolicy(
                topic="reliable agents",
                questions=(
                    ResearchQuestion(
                        question_id="RQ1",
                        question="What method is used?",
                        facet="method",
                    ),
                    ResearchQuestion(
                        question_id="RQ2",
                        question="How is it evaluated?",
                        facet="benchmark",
                    ),
                ),
                query_plan=QueryPlan(
                    topic="reliable agents",
                    seed_queries=["method", "benchmark"],
                    queries=["method", "benchmark"],
                    query_specs=[
                        {"query": "method", "facet": "method"},
                        {"query": "benchmark", "facet": "benchmark"},
                    ],
                    required_facets=["method", "benchmark"],
                ),
                max_documents=2,
            ),
        )

        self.assertEqual(len(raw.papers), 3)
        self.assertEqual([paper.id for paper in selected.selected_papers], ["paper-1", "paper-2"])
        self.assertEqual(selected.coverage_report["status"], "covered")
        self.assertTrue(any(row["reason"] == "duplicate_lower_score" for row in selected.selection_rows))
        restored = SearchResult.from_handoff_dict(selected.to_handoff_dict())
        self.assertEqual([paper.id for paper in restored.selected_papers], ["paper-1", "paper-2"])
        self.assertEqual(restored.coverage_report["covered_facets"], ["benchmark", "method"])

    def test_explicit_empty_selection_is_not_widened_to_raw_papers(self) -> None:
        raw = Paper(
            id="paper-raw",
            title="Out-of-scope paper",
            authors=[],
            abstract="A paper intentionally rejected by the selection policy.",
            url="https://example.test/raw",
            source="fixture",
            source_id="raw",
        )
        result = SearchResult(
            status="completed",
            responses=(
                SearchResponse(
                    source="fixture",
                    query="topic",
                    papers=[raw],
                ),
            ),
            papers=(raw,),
            selected_papers=(),
            selection_rows=(
                {"paper_id": raw.id, "decision": "drop", "reason": "out of scope"},
            ),
            coverage_report={"status": "partial"},
        )

        restored = SearchResult.from_handoff_dict(result.to_handoff_dict())

        self.assertEqual([paper.id for paper in restored.papers], [raw.id])
        self.assertEqual(restored.selected_papers, ())

    def test_request_rejects_empty_queries_providers_and_limits(self) -> None:
        with self.assertRaises(ValueError):
            SearchRequest(queries=(), providers=("fixture",))
        with self.assertRaises(ValueError):
            SearchRequest(queries=("query",), providers=())
        with self.assertRaises(ValueError):
            SearchRequest(queries=("query",), providers=("fixture",), max_results_per_query=0)


if __name__ == "__main__":
    unittest.main()
