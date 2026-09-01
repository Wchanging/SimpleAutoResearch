from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from dataclasses import replace

from simple_ar.core import CapabilityResult
from simple_ar.app.research_brief import ResearchBriefSessionRequest
from simple_ar.app.research_report import (
    ResearchReportSessionRequest,
    run_research_report_session,
)
from simple_ar.app.research_session import (
    ResearchSessionContinuationRequest,
    ResearchSessionRequest,
    ResearchSessionError,
    continue_research_session,
    load_research_session_result,
    run_research_session,
)
from simple_ar.experiment.code_task_bridge import (
    CODE_TASK_PROJECT_TEMPLATE,
    CodeTaskExperimentSpec,
)


class ResearchSessionApplicationTests(unittest.TestCase):
    def test_literature_to_execution_and_analysis_stays_in_one_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paper = root / "reliable_agents.md"
            paper.write_text(
                "# Method\n\nValidation improves reliable agent behavior.\n\n"
                "# Results\n\nThe fixture reports accuracy: 0.75.\n",
                encoding="utf-8",
            )

            result = run_research_session(
                ResearchSessionRequest(
                    brief=ResearchBriefSessionRequest(
                        topic="reliable agents",
                        session_root=root / "session",
                        local_documents=(paper,),
                        max_results=2,
                        max_chunks=20,
                    ),
                    command=(sys.executable, "-c", "print('accuracy: 0.75')"),
                    cwd=root,
                    timeout_sec=5,
                    result_schema={
                        "primary_metric": "accuracy",
                        "required_metrics": ["accuracy"],
                        "metric_directions": {"accuracy": "higher"},
                    },
                )
            )

            self.assertEqual(result.status, "ready_for_report")
            self.assertEqual(result.next_capability, "report")
            self.assertEqual(result.recommended_transition.action, "accept")
            self.assertEqual(result.recommended_transition.target, "report")
            self.assertEqual(result.execution["status"], "passed")
            self.assertEqual(result.analysis.status, "passed")

            failed_execution = replace(
                result,
                execution={**result.execution, "status": "failed"},
            )
            self.assertEqual(
                failed_execution.recommended_transition.target,
                "experiment",
            )
            self.assertNotEqual(
                failed_execution.recommended_transition.action,
                "accept",
            )
            self.assertEqual(
                [attempt.capability for attempt in result.attempts],
                [
                    "analysis",
                    "research_brief",
                    "research_design",
                    "document_ingest",
                    "experiment",
                    "plan",
                    "search",
                ],
            )
            self.assertEqual(
                [decision.action for decision in result.decisions],
                ["accept"] * 7,
            )
            self.assertTrue(
                (root / "session" / "attempts" / "brief-001" / "research_brief.json").is_file()
            )
            self.assertTrue(
                (root / "session" / "attempts" / "design-001" / "research_design.json").is_file()
            )
            self.assertIsNotNone(result.design)
            self.assertIsNotNone(result.design_ref)
            self.assertTrue(str(result.execution_ref.path).startswith("attempts/"))
            self.assertTrue(str(result.analysis_ref.path).startswith("attempts/"))
            manifest = json.loads(
                (root / "session" / "session_manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["profile"], "full_research")
            self.assertEqual(manifest["status"], "running")

            restored = load_research_session_result(root / "session")
            self.assertEqual(restored.status, result.status)
            self.assertEqual(restored.brief_ref, result.brief_ref)
            self.assertEqual(restored.design_ref, result.design_ref)
            self.assertEqual(restored.design, result.design)
            self.assertEqual(restored.execution_ref, result.execution_ref)
            self.assertEqual(restored.analysis_ref, result.analysis_ref)
            self.assertEqual(restored.execution, result.execution)
            self.assertEqual(restored.analysis, result.analysis)
            self.assertEqual(restored.attempts, result.attempts)
            self.assertEqual(restored.decisions, result.decisions)

    def test_restore_rejects_a_missing_typed_handoff(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "missing-session"
            with self.assertRaises(ResearchSessionError):
                load_research_session_result(root)

    def test_failed_execution_recommends_experiment_without_retrying(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paper = root / "reliable_agents.md"
            paper.write_text(
                "# Method\n\nValidation improves reliable agent behavior.\n\n"
                "# Results\n\nThe fixture reports accuracy: 0.75.\n",
                encoding="utf-8",
            )

            result = run_research_session(
                ResearchSessionRequest(
                    brief=ResearchBriefSessionRequest(
                        topic="reliable agents",
                        session_root=root / "session",
                        local_documents=(paper,),
                        max_results=2,
                        max_chunks=20,
                    ),
                    command=(
                        sys.executable,
                        "-c",
                        "print('accuracy: unavailable'); raise SystemExit(1)",
                    ),
                    cwd=root,
                    timeout_sec=5,
                    result_schema={
                        "primary_metric": "accuracy",
                        "required_metrics": ["accuracy"],
                        "metric_directions": {"accuracy": "higher"},
                    },
                )
            )

            self.assertNotEqual(result.status, "ready_for_report")
            self.assertEqual(result.recommended_transition.target, "experiment")
            self.assertIn(
                result.recommended_transition.action,
                {"repair", "revise", "block"},
            )
            self.assertEqual(
                [attempt.attempt_id for attempt in result.attempts].count(
                    "experiment-001"
                ),
                1,
            )

    def test_failed_session_can_continue_once_without_rebuilding_literature(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paper = root / "reliable_agents.md"
            paper.write_text(
                "# Method\n\nValidation improves reliable agent behavior.\n\n"
                "# Results\n\nThe fixture reports accuracy: 0.75.\n",
                encoding="utf-8",
            )
            session_root = root / "session"
            schema = {
                "primary_metric": "accuracy",
                "required_metrics": ["accuracy"],
                "metric_directions": {"accuracy": "higher"},
            }
            first = run_research_session(
                ResearchSessionRequest(
                    brief=ResearchBriefSessionRequest(
                        topic="reliable agents",
                        session_root=session_root,
                        local_documents=(paper,),
                        max_results=2,
                        max_chunks=20,
                    ),
                    command=(
                        sys.executable,
                        "-c",
                        "print('accuracy: unavailable'); raise SystemExit(1)",
                    ),
                    cwd=root,
                    timeout_sec=5,
                    result_schema=schema,
                )
            )
            self.assertNotEqual(first.status, "ready_for_report")

            recovered = continue_research_session(
                ResearchSessionContinuationRequest(
                    session_root=session_root,
                    command=(sys.executable, "-c", "print('accuracy: 0.9')"),
                    cwd=root,
                    timeout_sec=5,
                    result_schema={},
                )
            )

            self.assertEqual(recovered.status, "ready_for_report")
            self.assertEqual(recovered.execution_ref.path, "attempts/experiment-002/results.json")
            self.assertEqual(recovered.analysis_ref.path, "attempts/analysis-002/analysis.json")
            self.assertEqual(
                [attempt.attempt_id for attempt in recovered.attempts],
                [
                    "analysis-001",
                    "analysis-002",
                    "brief-001",
                    "design-001",
                    "document-001",
                    "experiment-001",
                    "experiment-002",
                    "plan-001",
                    "search-001",
                ],
            )
            self.assertEqual(
                recovered.recommended_transition.action,
                "accept",
            )
            restored = load_research_session_result(session_root)
            self.assertEqual(restored.execution_ref, recovered.execution_ref)
            self.assertEqual(restored.analysis_ref, recovered.analysis_ref)
            self.assertEqual(restored.execution, recovered.execution)

            report = run_research_report_session(
                ResearchReportSessionRequest(
                    session_root=session_root,
                    title="Reliable agents",
                    sections=(
                        {
                            "section_id": "findings",
                            "heading": "Findings",
                            "draft_markdown": "The recovered run reports accuracy 0.9.",
                        },
                    ),
                    source_refs=(recovered.analysis_ref,),
                    context={
                        "topic": "reliable agents",
                        "report_mode": "experiment",
                    },
                )
            )
            self.assertEqual(report.status, "completed")

    def test_session_can_route_experiment_to_existing_code_task_backend(self) -> None:
        class FakeClient:
            model = "fake-research-model"

            def ask_json(
                self,
                _system: str,
                _user: str,
                *,
                label: str = "",
            ) -> dict[str, object]:
                if label == "research-planner":
                    return {
                        "questions": [
                            {
                                "question": "Which validation method is used?",
                                "facet": "method",
                                "rationale": "Identify the evidence-backed method.",
                                "required": True,
                                "negative_scope": [],
                                "success_criteria": ["Name the validation method."],
                            }
                        ],
                        "queries": ["reliable agents validation"],
                        "required_facets": ["method"],
                        "negative_terms": [],
                        "rationale": "Focus on validation.",
                    }
                if label == "research-synthesis":
                    return {
                        "synthesis_markdown": (
                            "## Synthesis\n\nValidation is the main theme [paper-1]."
                        ),
                        "hypothesis_markdown": (
                            "## Hypothesis\n\nValidation should improve accuracy."
                        ),
                    }
                if label == "research-design":
                    return {
                        "selected_idea_id": "idea-001",
                        "rationale": "The first grounded idea has a measurable metric.",
                    }
                raise AssertionError(f"Unexpected JSON LLM label: {label}")

            def ask(
                self,
                _system: str,
                _user: str,
                *,
                label: str = "",
            ) -> str:
                self.last_analysis_label = label
                return json.dumps(
                    {
                        "summary": {
                            "method": "The configured Code-Task backend was used.",
                            "results": "The candidate produced accuracy 0.8.",
                            "limitations": "The fixture is small.",
                            "reproduction_notes": "The run is persisted in the session.",
                        },
                        "rubric_coverage": [],
                        "claims": [],
                        "analysis_audit": {
                            "unsupported_claims": [],
                            "limitations": [],
                            "notes": [],
                        },
                    }
                )

        result_artifact_exists = False
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paper = root / "reliable_agents.md"
            paper.write_text(
                "# Method\n\nValidation improves reliable agent behavior.\n\n"
                "# Results\n\nThe fixture reports accuracy: 0.75.\n",
                encoding="utf-8",
            )
            project = root / "project"
            project.mkdir()

            def fake_code_task_capability(
                *,
                context: object,
                request: object,
                **kwargs: object,
            ) -> CapabilityResult:
                del request, kwargs
                store = getattr(context, "store")
                result_ref = store.write_json(
                    "results.json",
                    {
                        "schema_version": "canonical_results.2.5",
                        "status": "passed",
                        "returncode": 0,
                        "timed_out": False,
                        "metrics": {"accuracy": 0.8},
                        "result_schema": {
                            "primary_metric": "accuracy",
                            "required_metrics": ["accuracy"],
                            "metric_directions": {"accuracy": "higher"},
                        },
                        "execution": {"status": "passed", "duration_sec": 0.1},
                        "comparisons": [],
                    },
                    kind="experiment_result",
                    schema="canonical_results.2.5",
                    producer="test.code_task",
                )
                return CapabilityResult(
                    status="completed",
                    artifacts=(result_ref,),
                    provenance={"backend": "code_task"},
                )

            with patch(
                "simple_ar.app.research_code_task._run_code_task_capability",
                side_effect=fake_code_task_capability,
            ) as code_task_runner:
                result = run_research_session(
                    ResearchSessionRequest(
                        brief=ResearchBriefSessionRequest(
                            topic="reliable agents",
                            session_root=root / "session",
                            local_documents=(paper,),
                            max_results=2,
                            max_chunks=20,
                            use_llm=True,
                            llm_client=FakeClient(),
                        ),
                        command=(),
                        cwd=root,
                        timeout_sec=5,
                        code_task_spec=CodeTaskExperimentSpec(
                            template=CODE_TASK_PROJECT_TEMPLATE,
                            code_root=project,
                            task_file=None,
                            benchmark_command="python benchmark.py",
                            primary_metric="accuracy",
                            metric_directions={"accuracy": "higher"},
                        ),
                    )
                )
                result_artifact_exists = (
                    result.session_root
                    / "attempts"
                    / "experiment-001"
                    / "results.json"
                ).is_file()

        self.assertEqual(result.status, "ready_for_report")
        self.assertEqual(result.execution["status"], "passed")
        self.assertEqual(result.analysis.status, "passed")
        self.assertEqual(code_task_runner.call_count, 1)
        self.assertEqual(result.decisions[-1].next_capability, "report")
        self.assertTrue(result_artifact_exists)


if __name__ == "__main__":
    unittest.main()
