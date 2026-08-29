from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from simple_ar.core import CapabilityRegistry, SessionController
from simple_ar.research.analysis import analyze_experiment_capability
from simple_ar.research.analysis import compare_experiment_results
from simple_ar.experiment.execution.backend import RunRequest
from simple_ar.research.experiment import (
    ExperimentRequest,
    experiment_request_from_synthesis,
    run_experiment_capability,
    run_and_analyze,
    run_experiment,
)
from simple_ar.result_analysis.schema import AnalysisContext
from simple_ar.research.contracts import ResearchExperimentContract
from simple_ar.research.synthesis import SynthesisResult


class ExperimentCapabilityTests(unittest.TestCase):
    def test_synthesis_handoff_builds_explicit_experiment_request(self) -> None:
        contract = ResearchExperimentContract(
            contract_id="synthesis-contract-001",
            hypothesis="The proposed change improves accuracy.",
            metrics=["accuracy"],
        )
        synthesis = SynthesisResult(
            status="ready",
            gap_summary="# Gap Summary\n\nA fixture gap.",
            ideas=(),
            novelty_checks=(),
            experiment_contract=contract,
        )

        request = experiment_request_from_synthesis(
            synthesis.to_handoff_dict(),
            run=RunRequest(
                command=[sys.executable, "-c", "print('accuracy: 0.75')"],
                cwd=Path.cwd(),
                timeout_sec=5,
            ),
            result_schema={"primary_metric": "accuracy", "direction": "higher"},
            artifacts={"results": "results.json"},
        )

        self.assertIsInstance(request, ExperimentRequest)
        self.assertEqual(
            request.experiment_contract.contract_id
            if isinstance(request.experiment_contract, ResearchExperimentContract)
            else None,
            "synthesis-contract-001",
        )
        self.assertEqual(request.result_schema["primary_metric"], "accuracy")
        self.assertEqual(request.artifacts["results"], "results.json")

    def test_synthesis_handoff_without_contract_is_not_executable(self) -> None:
        synthesis = SynthesisResult(
            status="needs_review",
            gap_summary="",
            ideas=(),
            novelty_checks=(),
        )

        with self.assertRaises(ValueError):
            experiment_request_from_synthesis(
                synthesis,
                run=RunRequest(
                    command=[sys.executable, "-c", "print('accuracy: 0.75')"],
                    cwd=Path.cwd(),
                    timeout_sec=5,
                ),
            )

    def test_execution_returns_canonical_results_without_file_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = run_experiment(
                ExperimentRequest(
                    run=RunRequest(
                        command=[sys.executable, "-c", "print('accuracy: 0.75')"],
                        cwd=Path(tmp),
                        timeout_sec=5,
                        label="fixture",
                    ),
                    result_schema={
                        "primary_metric": "accuracy",
                        "direction": "higher",
                    },
                    artifacts={"stdout": "run/stdout.txt"},
                )
            )

        self.assertEqual(result.status, "passed")
        self.assertEqual(result.run.metrics["accuracy"], 0.75)
        self.assertEqual(result.canonical["primary_metric"], "accuracy")
        self.assertEqual(result.canonical["execution"]["label"], "fixture")
        self.assertEqual(result.canonical["artifacts"]["stdout"], "run/stdout.txt")

    def test_comparison_can_be_carried_by_canonical_execution(self) -> None:
        comparison = compare_experiment_results(
            {"status": "passed", "metrics": {"accuracy": 0.70}},
            {"status": "passed", "metrics": {"accuracy": 0.75}},
            primary_metric="accuracy",
            metric_directions={"accuracy": "higher"},
        )

        with tempfile.TemporaryDirectory() as tmp:
            result = run_experiment(
                ExperimentRequest(
                    run=RunRequest(
                        command=[sys.executable, "-c", "print('accuracy: 0.75')"],
                        cwd=Path(tmp),
                        timeout_sec=5,
                    ),
                    comparisons=(comparison,),
                )
            )

        self.assertEqual(result.canonical["comparisons"][0]["verdict"], "improved")

    def test_research_contract_can_cross_into_execution_without_type_aliasing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contract = ResearchExperimentContract(
                contract_id="research-contract-001",
                hypothesis="A bounded change improves accuracy.",
                motivation_refs=["paper-1#claim-1"],
                baseline="baseline",
                dataset="fixture-data",
                metrics=["accuracy"],
                proposed_change="Change one feature.",
            )
            restored = ResearchExperimentContract.from_row(contract.to_row())
            result = run_experiment(
                ExperimentRequest(
                    run=RunRequest(
                        command=[sys.executable, "-c", "print('accuracy: 0.75')"],
                        cwd=Path(tmp),
                        timeout_sec=5,
                    ),
                    result_schema={"primary_metric": "accuracy", "direction": "higher"},
                    experiment_contract=restored,
                )
            )

        self.assertEqual(result.status, "passed")
        self.assertEqual(
            result.canonical["experiment_contract"]["contract_id"],
            "research-contract-001",
        )

    def test_typed_research_contract_supplies_missing_result_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contract = ResearchExperimentContract(
                contract_id="research-contract-002",
                hypothesis="A bounded change improves accuracy.",
                metrics=["accuracy", "macro_f1"],
            )
            result = run_experiment(
                ExperimentRequest(
                    run=RunRequest(
                        command=[sys.executable, "-c", "print('accuracy: 0.75')"],
                        cwd=Path(tmp),
                        timeout_sec=5,
                    ),
                    experiment_contract=contract,
                )
            )

        self.assertEqual(result.status, "passed")
        self.assertEqual(
            result.canonical["result_schema"],
            {
                "primary_metric": "accuracy",
                "required_metrics": ["accuracy", "macro_f1"],
                "direction": "unknown",
            },
        )

    def test_explicit_result_schema_overrides_typed_contract_metrics(self) -> None:
        contract = ResearchExperimentContract(
            contract_id="research-contract-003",
            hypothesis="A bounded change improves accuracy.",
            metrics=["accuracy", "macro_f1"],
        )

        request = ExperimentRequest(
            run=RunRequest(
                command=[sys.executable, "-c", "print('score: 0.75')"],
                cwd=Path.cwd(),
                timeout_sec=5,
            ),
            result_schema={"primary_metric": "score", "direction": "higher"},
            experiment_contract=contract,
        )

        self.assertEqual(
            request.normalized_result_schema(),
            {"primary_metric": "score", "direction": "higher"},
        )

    def test_timeout_remains_a_normalized_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = run_experiment(
                ExperimentRequest(
                    run=RunRequest(
                        command=[sys.executable, "-c", "import time; time.sleep(2)"],
                        cwd=Path(tmp),
                        timeout_sec=1,
                    )
                )
            )

        self.assertEqual(result.status, "timed_out")
        self.assertTrue(result.canonical["timed_out"])

    def test_run_and_analyze_passes_observed_metrics_to_analysis(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = run_and_analyze(
                ExperimentRequest(
                    run=RunRequest(
                        command=[sys.executable, "-c", "print('accuracy: 0.75')"],
                        cwd=Path(tmp),
                        timeout_sec=5,
                    ),
                    result_schema={"primary_metric": "accuracy", "direction": "higher"},
                ),
                AnalysisContext(
                    task_id="fixture",
                ),
            )

            self.assertEqual(result.status, "passed")
            self.assertEqual(result.analysis.metric_summary["primary_metric"], "accuracy")
            self.assertEqual(result.analysis.metric_summary["metrics"][0]["value"], 0.75)
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_run_and_analyze_keeps_failed_execution_visible(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = run_and_analyze(
                ExperimentRequest(
                    run=RunRequest(
                        command=[sys.executable, "-c", "import sys; sys.exit(2)"],
                        cwd=Path(tmp),
                        timeout_sec=5,
                    )
                ),
                {"task_id": "failed-fixture", "expected_metrics": [{"name": "score"}]},
            )

            self.assertEqual(result.status, "failed")
            self.assertEqual(result.execution.canonical["status"], "failed")
            self.assertIn("score", result.analysis.audit.missing_required_metrics)

    def test_session_adapter_persists_canonical_result_without_fake_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = CapabilityRegistry()
            registry.register("run-experiment", run_experiment_capability)
            controller = SessionController.create(
                tmp,
                session_id="experiment-session",
                topic="experiment adapter",
                registry=registry,
            )

            result, decision = controller.execute(
                "run-experiment",
                attempt_id="attempt-001",
                request=ExperimentRequest(
                    run=RunRequest(
                        command=[sys.executable, "-c", "print('accuracy: 0.75')"],
                        cwd=Path(tmp),
                        timeout_sec=5,
                    ),
                    result_schema={"primary_metric": "accuracy", "direction": "higher"},
                ),
            )

            self.assertEqual(result.status, "completed")
            self.assertEqual(decision.action, "accept")
            refs = controller.attempt_output_refs("attempt-001")
            payload = controller.store.read_json(refs[0])
            self.assertEqual(payload["schema_version"], "2.5")
            self.assertEqual(payload["metrics"]["accuracy"], 0.75)
            self.assertEqual(payload["guard"]["status"], "passed")
            self.assertEqual(payload["diagnosis"]["status"], "passed")
            self.assertEqual(payload["artifacts"]["guard"], "guard_report.json")
            self.assertEqual(payload["artifacts"]["diagnosis"], "diagnosis.json")
            self.assertTrue(
                controller.store.exists("attempts/attempt-001/guard_report.json")
            )
            self.assertTrue(
                controller.store.exists("attempts/attempt-001/diagnosis.json")
            )

    def test_session_adapter_keeps_timeout_as_failed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = CapabilityRegistry()
            registry.register("run-experiment", run_experiment_capability)
            controller = SessionController.create(
                tmp,
                session_id="experiment-timeout",
                topic="experiment timeout",
                registry=registry,
            )

            result, decision = controller.execute(
                "run-experiment",
                attempt_id="attempt-001",
                request=ExperimentRequest(
                    run=RunRequest(
                        command=[sys.executable, "-c", "import time; time.sleep(2)"],
                        cwd=Path(tmp),
                        timeout_sec=1,
                    ),
                ),
            )

            self.assertEqual(result.status, "failed")
            self.assertEqual(decision.failure_kind, "resource")
            self.assertEqual(
                controller.store.read_json(controller.attempt_output_refs("attempt-001")[0])["status"],
                "timed_out",
            )
            self.assertEqual(
                controller.store.read_json(
                    "attempts/attempt-001/guard_report.json"
                )["status"],
                "failed",
            )

    def test_session_adapter_rejects_missing_required_metric(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = CapabilityRegistry()
            registry.register("run-experiment", run_experiment_capability)
            controller = SessionController.create(
                tmp,
                session_id="experiment-missing-metric",
                topic="experiment guard",
                registry=registry,
            )

            result, decision = controller.execute(
                "run-experiment",
                attempt_id="attempt-001",
                request=ExperimentRequest(
                    run=RunRequest(
                        command=[sys.executable, "-c", "print('score: 0.75')"],
                        cwd=Path(tmp),
                        timeout_sec=5,
                    ),
                    result_schema={
                        "primary_metric": "accuracy",
                        "required_metrics": ["accuracy"],
                    },
                ),
            )

            self.assertEqual(result.status, "failed")
            self.assertEqual(decision.failure_kind, "metric")
            payload = controller.store.read_json(
                "attempts/attempt-001/results.json"
            )
            self.assertEqual(payload["status"], "passed")
            self.assertEqual(payload["guard"]["status"], "failed")

    def test_session_adapter_persists_execution_streams_as_declared_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            registry = CapabilityRegistry()
            registry.register("run-experiment", run_experiment_capability)
            controller = SessionController.create(
                root,
                session_id="experiment-streams",
                topic="execution evidence",
                registry=registry,
            )

            result, _ = controller.execute(
                "run-experiment",
                attempt_id="attempt-001",
                request=ExperimentRequest(
                    run=RunRequest(
                        command=[
                            sys.executable,
                            "-c",
                            "import sys; print('out'); print('err', file=sys.stderr); sys.exit(2)",
                        ],
                        cwd=root,
                        timeout_sec=5,
                    ),
                ),
            )

            self.assertEqual(result.status, "failed")
            result_ref = controller.attempt_output_refs("attempt-001")[0]
            payload = controller.store.read_json(result_ref)
            self.assertEqual(payload["artifacts"]["stdout"], "execution/stdout.txt")
            self.assertEqual(payload["artifacts"]["stderr"], "execution/stderr.txt")
            self.assertEqual(
                controller.store.read_text("attempts/attempt-001/execution/stdout.txt"),
                "out\n",
            )
            self.assertEqual(
                controller.store.read_text("attempts/attempt-001/execution/stderr.txt"),
                "err\n",
            )

    def test_execution_and_analysis_adapters_use_explicit_result_handoff(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = CapabilityRegistry()
            registry.register("experiment", run_experiment_capability)
            registry.register("analysis", analyze_experiment_capability)
            controller = SessionController.create(
                tmp,
                session_id="run-analysis-session",
                topic="execution to analysis",
                profile="experiment",
                registry=registry,
            )

            controller.execute(
                "experiment",
                attempt_id="attempt-001",
                next_capability="analysis",
                request=ExperimentRequest(
                    run=RunRequest(
                        command=[sys.executable, "-c", "print('accuracy: 0.75')"],
                        cwd=Path(tmp),
                        timeout_sec=5,
                    ),
                    result_schema={"primary_metric": "accuracy", "direction": "higher"},
                ),
            )
            result_ref = controller.attempt_output_refs("attempt-001")[0]
            result, decision = controller.execute(
                "analysis",
                attempt_id="attempt-002",
                inputs=(result_ref,),
                result_ref=result_ref,
                analysis_context={"task_id": "fixture"},
            )

            self.assertEqual(result.status, "completed")
            self.assertEqual(decision.action, "accept")
            analysis_ref = controller.attempt_output_refs("attempt-002")[0]
            payload = controller.store.read_json(analysis_ref)
            self.assertEqual(payload["schema_version"], "analysis_handoff.v1")
            self.assertEqual(payload["execution_ref"]["path"], "attempts/attempt-001/results.json")
            self.assertEqual(
                payload["analysis"]["metric_summary"]["metrics"][0]["value"],
                0.75,
            )
            self.assertEqual(payload["analysis"]["status"], "passed")


if __name__ == "__main__":
    unittest.main()
