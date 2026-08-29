from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from simple_ar.core.capabilities import (
    ArtifactRef,
    ArtifactStore,
    AttemptManifest,
    CapabilityContext,
)
from simple_ar.research.analysis import (
    AnalysisRequest,
    AnalysisHandoff,
    analyze_results,
    analyze_experiment_capability,
    compare_experiment_results,
)
from simple_ar.result_analysis.schema import AnalysisContext


class AnalysisCapabilityTests(unittest.TestCase):
    def test_analysis_handoff_round_trips_without_execution(self) -> None:
        handoff = AnalysisHandoff(
            execution_ref=ArtifactRef(
                "attempts/run-001/results.json",
                kind="experiment_result",
                schema="canonical_results.2.5",
            ),
            execution_status="passed",
            analysis=analyze_results(
                AnalysisRequest(
                    context={
                        "task_id": "fixture",
                        "project_results": {
                            "execution_result": {
                                "status": "passed",
                                "metrics": {"score": 0.8},
                            }
                        },
                        "metrics": {"score": 0.8},
                    }
                )
            ),
        )

        restored = AnalysisHandoff.from_handoff_dict(handoff.to_handoff_dict())

        self.assertEqual(restored.execution_ref.path, "attempts/run-001/results.json")
        self.assertEqual(restored.execution_status, "passed")
        self.assertEqual(restored.analysis.status, "passed")

    def test_analysis_handoff_rejects_missing_execution_status(self) -> None:
        handoff = AnalysisHandoff(
            execution_ref=ArtifactRef(
                "attempts/run-001/results.json",
                kind="experiment_result",
                schema="canonical_results.2.5",
            ),
            execution_status="passed",
            analysis=analyze_results(
                AnalysisRequest(context={"task_id": "fixture"})
            ),
        ).to_handoff_dict()
        handoff.pop("execution_status")

        with self.assertRaisesRegex(ValueError, "execution_status"):
            AnalysisHandoff.from_handoff_dict(handoff)

    def test_analysis_is_deterministic_and_does_not_write_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = analyze_results(
                AnalysisRequest(
                    context=AnalysisContext(
                        task_id="fixture",
                        title="Offline fixture",
                        research_question="Does the candidate improve accuracy?",
                        expected_metrics=[{"name": "accuracy", "direction": "higher"}],
                        metrics={"accuracy": 0.75},
                    )
                )
            )

            self.assertEqual(result.metric_summary["primary_metric"], "accuracy")
            self.assertFalse(result.audit.llm_used)
            self.assertEqual(result.status, "incomplete")
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_analysis_persistence_is_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "analysis"
            analyze_results(
                AnalysisRequest(
                    context={"task_id": "fixture", "metrics": {"score": 1.0}},
                    output_dir=output_dir,
                )
            )

            self.assertTrue((output_dir / "metric_summary.json").is_file())
            self.assertTrue((output_dir / "analysis_audit.json").is_file())
            self.assertTrue((output_dir / "analysis_status.json").is_file())

    def test_analysis_status_requires_an_explicit_execution_record(self) -> None:
        result = analyze_results(
            AnalysisRequest(
                context=AnalysisContext(
                    task_id="no-execution",
                    metrics={"score": 0.8},
                )
            )
        )

        self.assertEqual(result.status, "incomplete")
        self.assertIn("No canonical execution", result.status_reasons[0])

    def test_analysis_capability_preserves_incomplete_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ArtifactStore(Path(tmp))
            result_ref = store.write_json(
                "input/results.json",
                {"status": "passed", "metrics": {"score": 0.8}},
            )
            capability = analyze_experiment_capability(
                context=CapabilityContext(
                    store=store,
                    attempt=AttemptManifest(attempt_id="analysis-001"),
                    inputs=(result_ref,),
                ),
                result_ref=result_ref,
                analysis_context={
                    "task_id": "missing-required-evidence",
                    "expected_metrics": [
                        {"name": "score"},
                        {"name": "f1"},
                    ],
                },
            )

            self.assertEqual(capability.status, "partial")
            self.assertTrue(any("Required metrics are missing" in item for item in capability.diagnostics))

    def test_analysis_status_preserves_execution_and_evidence_state(self) -> None:
        cases = (
            ("passed", "passed"),
            ("failed", "failed"),
            ("timed_out", "failed"),
            ("blocked_by_validation", "blocked"),
        )
        for execution_status, expected in cases:
            with self.subTest(execution_status=execution_status):
                result = analyze_results(
                    AnalysisRequest(
                        context=AnalysisContext(
                            task_id=execution_status,
                            expected_metrics=[{"name": "score", "direction": "higher"}],
                            metrics={"score": 0.8},
                            project_results={
                                "execution_result": {
                                    "status": execution_status,
                                    "metrics": {"score": 0.8},
                                }
                            },
                        )
                    )
                )
                self.assertEqual(result.status, expected)

    def test_analysis_status_marks_guard_or_required_metric_deficiency_incomplete(self) -> None:
        for execution_result in (
            {
                "status": "passed",
                "metrics": {"score": 0.8},
                "guard": {"status": "failed"},
            },
            {
                "status": "passed",
                "metrics": {"score": 0.8},
            },
        ):
            with self.subTest(execution_result=execution_result):
                result = analyze_results(
                    AnalysisRequest(
                        context=AnalysisContext(
                            task_id="incomplete",
                            expected_metrics=[
                                {"name": "score", "direction": "higher"},
                                {"name": "f1", "direction": "higher"},
                            ],
                            metrics={"score": 0.8},
                            project_results={"execution_result": execution_result},
                        )
                    )
                )
                self.assertEqual(result.status, "incomplete")

    def test_analysis_status_uses_only_explicit_regression_for_metric_below_target(self) -> None:
        result = analyze_results(
            AnalysisRequest(
                context=AnalysisContext(
                    task_id="regressed",
                    expected_metrics=[{"name": "score", "direction": "higher"}],
                    metrics={"score": 0.8},
                    project_results={
                        "execution_result": {
                            "status": "passed",
                            "metrics": {"score": 0.8},
                            "comparisons": [{"verdict": "regressed"}],
                        }
                    },
                )
            )
        )

        self.assertEqual(result.status, "metric_below_target")
        self.assertIn("explicit experiment comparison", result.status_reasons[0])

    def test_empty_label_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            AnalysisRequest(context={}, label=" ")

    def test_experiment_comparison_uses_primary_metric_direction(self) -> None:
        comparison = compare_experiment_results(
            {
                "status": "passed",
                "metrics": {"accuracy": 0.70, "runtime_sec": 2.0},
                "primary_metric": "accuracy",
            },
            {
                "status": "passed",
                "metrics": {"accuracy": 0.75, "runtime_sec": 1.5},
                "primary_metric": "accuracy",
            },
            metric_directions={
                "accuracy": "higher_is_better",
                "runtime_sec": "resource",
            },
        )

        self.assertEqual(comparison["schema_version"], "experiment_comparison.v1")
        self.assertEqual(comparison["status"], "ready")
        self.assertEqual(comparison["verdict"], "improved")
        self.assertAlmostEqual(comparison["deltas"]["accuracy"], 0.05)
        accuracy = next(row for row in comparison["metrics"] if row["name"] == "accuracy")
        self.assertTrue(accuracy["is_primary"])
        self.assertEqual(accuracy["interpretation"], "improved")

    def test_experiment_comparison_reads_embedded_schema_and_handles_failure(self) -> None:
        baseline = {
            "status": "failed",
            "metrics": {},
            "result_schema": {
                "primary_metric": "score",
                "direction": "higher",
            },
        }
        candidate = {
            "status": "passed",
            "metrics": {"score": 0.4},
            "result_schema": {
                "primary_metric": "score",
                "direction": "higher",
            },
        }

        comparison = compare_experiment_results(baseline, candidate)

        self.assertEqual(comparison["verdict"], "improved")
        self.assertEqual(comparison["metric_config"]["primary_metric"], "score")
        self.assertEqual(comparison["metric_config"]["metric_directions"]["score"], "higher")

    def test_experiment_comparison_is_inconclusive_without_directional_evidence(self) -> None:
        comparison = compare_experiment_results(
            {"status": "passed", "metrics": {"runtime_sec": 2.0}},
            {"status": "passed", "metrics": {"runtime_sec": 1.0}},
            primary_metric="runtime_sec",
            metric_directions={"runtime_sec": "resource"},
        )

        self.assertEqual(comparison["verdict"], "inconclusive")
        self.assertIn("no directional", comparison["reasons"][0])

    def test_experiment_comparison_reports_missing_primary_metric(self) -> None:
        comparison = compare_experiment_results(
            {"status": "passed", "metrics": {"accuracy": 0.7}},
            {"status": "passed", "metrics": {"accuracy": 0.8}},
            primary_metric="f1",
            metric_directions={"f1": "higher"},
        )

        self.assertEqual(comparison["verdict"], "inconclusive")
        self.assertIn("was not shared", comparison["reasons"][0])


if __name__ == "__main__":
    unittest.main()
