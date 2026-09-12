from __future__ import annotations

import json
import tempfile
import unittest
import contextlib
import io
from pathlib import Path
from unittest.mock import patch

from simple_ar.core import CapabilityResult
from simple_ar.app.research_brief import (
    ResearchBriefSessionError,
    ResearchBriefSessionRequest,
    run_research_brief_session,
)
from simple_ar.cli import main


class ResearchBriefApplicationTests(unittest.TestCase):
    def test_failed_plan_surfaces_capability_diagnostic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            def register_failed_plan(registry: object, *, names: object = ()) -> None:
                del names
                registry.register(
                    "plan",
                    lambda **_: CapabilityResult(
                        status="failed",
                        diagnostics=("planner backend unavailable",),
                    ),
                )

            with patch(
                "simple_ar.app.research_application.register_research_capabilities",
                side_effect=register_failed_plan,
            ):
                with self.assertRaisesRegex(
                    ResearchBriefSessionError,
                    "planner backend unavailable",
                ):
                    run_research_brief_session(
                        ResearchBriefSessionRequest(
                            topic="reliable agents",
                            session_root=root / "session",
                            local_documents=(root / "paper.md",),
                        )
                    )

    def test_local_document_session_propagates_explicit_llm_mode(self) -> None:
        class FakeClient:
            model = "fake-brief-model"

            def ask_json(self, system: str, user: str, *, label: str = "", **kwargs: object) -> dict[str, object]:
                del kwargs
                if label == "research-planner":
                    return {
                        "questions": [
                            {
                                "question": "What validation method is described?",
                                "facet": "method",
                                "rationale": "Read the method evidence.",
                                "required": True,
                                "negative_scope": [],
                                "success_criteria": ["Identify the method."],
                            }
                        ],
                        "queries": ["reliable agents validation"],
                        "required_facets": ["method"],
                        "negative_terms": [],
                        "rationale": "Focus on validation.",
                    }
                return {
                    "synthesis_markdown": "## Synthesis\n\nValidation is the main theme [paper-1].",
                    "hypothesis_markdown": "## Hypothesis\n\nValidation should improve reliability.",
                }

            def ask_json_many(
                self,
                requests: list[object],
                *,
                max_workers: int,
            ) -> list[dict[str, object]]:
                del max_workers
                return [
                    {
                        "paper_id": str(getattr(request, "label", "")),
                        "one_sentence_summary": "Validation is the main theme.",
                        "problem": "Reliable agent behavior.",
                        "method": "Validation.",
                        "limitation": "Small fixture.",
                        "relation_to_topic": "Directly relevant.",
                        "confidence": "medium",
                    }
                    for request in requests
                ]

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paper = root / "reliable_agents.md"
            paper.write_text(
                "# Method\n\nValidation improves reliable agent behavior.\n\n"
                "# Results\n\nThe fixture reports accuracy: 0.75.\n",
                encoding="utf-8",
            )

            result = run_research_brief_session(
                ResearchBriefSessionRequest(
                    topic="reliable agents",
                    session_root=root / "session",
                    local_documents=(paper,),
                    max_results=2,
                    max_chunks=20,
                    use_llm=True,
                    llm_client=FakeClient(),
                )
            )

        self.assertEqual(result.plan.query_plan.planner, "llm")
        self.assertEqual(result.brief.synthesis.generation_mode, "llm")
        self.assertTrue(result.brief.synthesis.synthesis_markdown)

    def test_local_document_session_persists_each_handoff(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paper = root / "reliable_agents.md"
            paper.write_text(
                "# Method\n\nValidation improves reliable agent behavior.\n\n"
                "# Results\n\nThe fixture reports accuracy: 0.75.\n",
                encoding="utf-8",
            )

            result = run_research_brief_session(
                ResearchBriefSessionRequest(
                    topic="reliable agents",
                    session_root=root / "session",
                    local_documents=(paper,),
                    queries=("reliability validation",),
                    providers=("local_files",),
                    max_results=2,
                    max_chunks=20,
                    llm_client=object(),  # use_llm=False must not invoke this object.
                )
            )

            self.assertIn(result.status, {"ready", "partial", "needs_review"})
            self.assertIn("reliability validation", result.plan.query_plan.queries)
            self.assertEqual(result.plan.source_plan.sources, ["local_files"])
            self.assertTrue(result.brief_path.is_file())
            self.assertEqual(len(result.attempts), 5)
            self.assertEqual(
                [attempt.capability for attempt in result.attempts],
                ["document_ingest", "plan", "read", "search", "synthesize"],
            )
            manifest = json.loads(
                (root / "session" / "session_manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["status"], "completed")
            self.assertEqual(manifest["schema_version"], "session_manifest.v2")
            for name in ("plan", "search", "documents", "read", "synthesis", "summary"):
                self.assertTrue((result.session_root / manifest["state_refs"][name]["path"]).is_file())
            self.assertEqual(result.brief_ref.path, manifest["state_refs"]["synthesis"]["path"])

    def test_cli_runs_the_same_local_document_workflow(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paper = root / "brief_source.txt"
            paper.write_text(
                "Reliable agents use validation. The benchmark reports accuracy: 0.75.\n",
                encoding="utf-8",
            )
            output = io.StringIO()
            with contextlib.chdir(root), contextlib.redirect_stdout(output):
                main(
                    [
                        "research-brief",
                        "--topic",
                        "reliable agents",
                        "--local-document",
                        str(paper),
                        "--output-root",
                        "runs",
                    ]
                )

            self.assertIn("Research brief session:", output.getvalue())
            self.assertIn("Status:", output.getvalue())
            sessions = list((root / "runs").iterdir())
            self.assertEqual(len(sessions), 1)
            manifest = json.loads((sessions[0] / "session_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["schema_version"], "session_manifest.v2")
            for name in ("read", "synthesis", "summary"):
                self.assertTrue((sessions[0] / manifest["state_refs"][name]["path"]).is_file())


if __name__ == "__main__":
    unittest.main()
