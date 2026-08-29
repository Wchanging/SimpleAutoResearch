from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from simple_ar.core import CapabilityRegistry, SessionController
from simple_ar.core.capabilities import ArtifactStore, AttemptManifest, CapabilityContext
from simple_ar.literature.models import Paper
from simple_ar.research import (
    ResearchPlanRequest,
    ResearchPlanResult,
    search_request_from_plan,
)
from simple_ar.research.sources import SearchProviderRegistry
from simple_ar.research.sources.base import SearchQuery, SearchResponse
from simple_ar.research.planning.capability import (
    build_research_plan,
    run_research_plan_capability,
)


class PlanningCapabilityTests(unittest.TestCase):
    def test_plan_reuses_existing_deterministic_planners(self) -> None:
        result = build_research_plan(
            ResearchPlanRequest(
                topic="reliable coding agents",
                problem_markdown="# Problem\nStudy bounded agent evaluation.",
                config={
                    "research_required_facets": ["method", "benchmark"],
                    "research_sources": ["fixture"],
                    "research_queries": ["reliable coding agents"],
                },
                default_query="coding agents",
                default_max_results=4,
            )
        )

        self.assertTrue(result.questions)
        self.assertEqual(result.query_plan.planner, "deterministic")
        self.assertEqual(result.source_plan.sources, ["fixture"])
        self.assertEqual(result.source_plan.max_results_per_query, 4)
        self.assertIn("benchmark", result.query_plan.required_facets)

    def test_plan_handoff_round_trips_without_llm_or_network(self) -> None:
        result = build_research_plan(ResearchPlanRequest(topic="agent evaluation"))

        restored = ResearchPlanResult.from_handoff_dict(result.to_handoff_dict())

        self.assertEqual(restored.query_plan.to_row(), result.query_plan.to_row())
        self.assertEqual(restored.source_plan.to_row(), result.source_plan.to_row())
        self.assertEqual(
            [question.to_row() for question in restored.questions],
            [question.to_row() for question in result.questions],
        )

    def test_plan_adapts_to_search_without_invoking_a_provider(self) -> None:
        result = build_research_plan(
            ResearchPlanRequest(
                topic="agent evaluation",
                config={"research_sources": ["fixture"]},
            )
        )

        request = search_request_from_plan(result)

        self.assertEqual(request.providers, ("fixture",))
        self.assertEqual(request.queries, tuple(result.query_plan.queries))
        self.assertEqual(
            request.max_results_per_query,
            result.source_plan.max_results_per_query,
        )

    def test_capability_persists_research_plan_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context = CapabilityContext(
                store=ArtifactStore(Path(tmp)),
                attempt=AttemptManifest(attempt_id="plan-001", capability="plan"),
            )

            result = run_research_plan_capability(
                context=context,
                request=ResearchPlanRequest(topic="agent evaluation"),
            )

            self.assertEqual(result.status, "completed")
            self.assertEqual(result.artifacts[0].path, "research_plan.json")
            payload = context.store.read_json(result.artifacts[0])
            self.assertEqual(payload["schema_version"], "research_plan.v1")
            self.assertEqual(payload["planner"], "deterministic")

    def test_plan_handoff_can_drive_search_in_a_session_fixture(self) -> None:
        class Connector:
            source_name = "fixture"

            def search(self, request: SearchQuery) -> SearchResponse:
                return SearchResponse(
                    source=self.source_name,
                    query=request.query,
                    papers=[
                        Paper(
                            id="fixture-paper",
                            title="Fixture evidence",
                            authors=[],
                            abstract="A bounded fixture result.",
                            url="https://example.test/fixture-paper",
                            source="fixture",
                        )
                    ],
                )

        with tempfile.TemporaryDirectory() as tmp:
            registry = CapabilityRegistry()
            from simple_ar.research import register_research_capabilities

            register_research_capabilities(registry, names=("plan", "search"))
            controller = SessionController.create(
                Path(tmp),
                session_id="plan-search-flow",
                topic="fixture research",
                profile="research_brief",
                registry=registry,
                budget=None,
            )

            plan_result, plan_decision = controller.execute(
                "plan",
                attempt_id="plan-001",
                next_capability="search",
                request=ResearchPlanRequest(
                    topic="fixture research",
                    config={"research_sources": ["fixture"]},
                ),
            )
            self.assertEqual(plan_result.status, "completed")
            self.assertEqual(plan_decision.action, "accept")

            plan_ref = controller.attempt_output_ref(
                "plan-001",
                kind="research_plan",
                schema="research_plan.v1",
            )
            restored = ResearchPlanResult.from_handoff_dict(
                controller.store.read_json(plan_ref)
            )
            search_result, search_decision = controller.execute(
                "search",
                attempt_id="search-001",
                inputs=(plan_ref,),
                request=search_request_from_plan(restored),
                registry=SearchProviderRegistry({"fixture": Connector}),
            )

            self.assertEqual(search_result.status, "completed")
            self.assertEqual(search_decision.action, "accept")
            payload = controller.store.read_json(
                controller.attempt_output_ref(
                    "search-001",
                    kind="search_result",
                    schema="search_handoff.v1",
                )
            )
            self.assertEqual(payload["papers"][0]["id"], "fixture-paper")


if __name__ == "__main__":
    unittest.main()
