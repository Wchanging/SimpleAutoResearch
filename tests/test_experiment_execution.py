from __future__ import annotations

import math
import sys
import tempfile
import unittest
from pathlib import Path

from simple_ar.experiment.coding.provider import implement_greenfield_project
from simple_ar.experiment.execution.backend import LocalExecutionBackend, RunRequest
from simple_ar.experiment.execution.guards import evaluate_result_guard
from simple_ar.experiment.execution.repair import repair_generated_project_from_guard
from simple_ar.experiment.execution.results import build_canonical_results
from simple_ar.experiment.rerun import preserve_stage_outputs
from simple_ar.experiment.tools.gateway import LocalExperimentToolGateway
from simple_ar.core.pipeline import Context
from simple_ar.core.stages import Stage
from simple_ar.report.context import build_report_context


TEST_ROOT = Path(__file__).resolve().parents[1] / ".tmp_tests"


class ExperimentExecutionTests(unittest.TestCase):
    def test_local_backend_and_canonical_results_keep_legacy_metric_fields(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            script = root / "run.py"
            script.write_text("print('accuracy: 0.83')\n", encoding="utf-8")

            run = LocalExecutionBackend().run(
                RunRequest(
                    command=[sys.executable, "run.py"],
                    cwd=root,
                    timeout_sec=10,
                    label="unit",
                )
            )
            result = build_canonical_results(
                run,
                result_schema={
                    "primary_metric": "accuracy",
                    "required_metrics": ["accuracy"],
                },
                artifacts={"stdout": "07-run/stdout.txt"},
            )
            guard = evaluate_result_guard(result)

            self.assertEqual(result["schema_version"], "2.5")
            self.assertEqual(result["status"], "passed")
            self.assertEqual(result["returncode"], 0)
            self.assertEqual(result["metrics"]["accuracy"], 0.83)
            self.assertEqual(result["execution"]["backend"], "local")
            self.assertEqual(guard["status"], "passed")

    def test_guard_flags_missing_primary_and_nonfinite_metrics(self) -> None:
        result = {
            "returncode": 0,
            "timed_out": False,
            "metrics": {"loss": math.inf},
            "result_schema": {
                "primary_metric": "accuracy",
                "required_metrics": ["accuracy", "loss"],
            },
        }

        guard = evaluate_result_guard(result)

        self.assertEqual(guard["status"], "failed")
        codes = {issue["code"] for issue in guard["issues"]}
        self.assertIn("missing_primary_metric", codes)
        self.assertIn("nonfinite_metric", codes)

    def test_guard_propagates_code_review_warning(self) -> None:
        result = {
            "returncode": 0,
            "timed_out": False,
            "metrics": {"score": 0.7},
            "result_schema": {
                "primary_metric": "score",
                "required_metrics": ["score"],
            },
            "experiment_contract": {
                "task_kind": "greenfield",
                "success_criteria": ["Emit parseable metrics."],
            },
            "verdicts": [
                {
                    "name": "primary_metric_observed",
                    "metric": "score",
                    "value": 0.7,
                    "verdict": "observed",
                }
            ],
            "code_review": {
                "status": "warning",
                "summary": {"warning_count": 1, "error_count": 0},
                "findings": [{"severity": "warning", "code": "review_risk"}],
            },
        }

        guard = evaluate_result_guard(result)

        self.assertEqual(guard["status"], "warning")
        codes = {issue["code"] for issue in guard["issues"]}
        self.assertIn("code_review_warning", codes)
        self.assertIn("success_criteria_requires_review", codes)

    def test_guard_marks_greenfield_review_recovery_as_warning(self) -> None:
        result = {
            "returncode": 0,
            "timed_out": False,
            "metrics": {"score": 0.7},
            "result_schema": {
                "primary_metric": "score",
                "required_metrics": ["score"],
            },
            "review_failure_recovery": {
                "reason": "llm_project_failed_code_review",
                "recovery_mode": "deterministic_fallback_scaffold",
            },
        }

        guard = evaluate_result_guard(result)

        self.assertEqual(guard["status"], "warning")
        codes = {issue["code"] for issue in guard["issues"]}
        self.assertIn("code_generation_recovered", codes)

    def test_greenfield_provider_writes_reviewed_runnable_project(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            stage_dir = Path(tmp) / "06-code"
            stage_dir.mkdir()
            contract = {
                "contract_id": "exp-test",
                "task_kind": "greenfield",
                "objective": "Create a tiny local experiment.",
            }
            result_schema = {
                "primary_metric": "accuracy",
                "required_metrics": ["accuracy", "macro_f1"],
            }
            resource_plan = {
                "max_files": 6,
                "max_generated_lines": 300,
                "max_runtime_sec": 20,
            }

            result = implement_greenfield_project(
                stage_dir=stage_dir,
                contract=contract,
                result_schema=result_schema,
                resource_plan=resource_plan,
                dependency_plan={"install_allowed": False},
                domain_profile={"expected_entrypoints": ["python main.py"]},
                client=None,
            )

            self.assertTrue(result.project_dir.is_dir())
            self.assertTrue((result.project_dir / "main.py").is_file())
            self.assertTrue(result.experiment_script_path.is_file())
            self.assertTrue(result.architecture_plan_path.is_file())
            self.assertTrue(result.file_plan_path.is_file())
            self.assertIn(result.review_status, {"passed", "warning"})

            run = LocalExecutionBackend().run(
                RunRequest(
                    command=[sys.executable, "experiment.py"],
                    cwd=stage_dir,
                    timeout_sec=10,
                )
            )
            self.assertEqual(run.returncode, 0)
            self.assertIn("accuracy", run.metrics)
            self.assertIn("macro_f1", run.metrics)

    def test_stage_rerun_archives_existing_outputs_by_default(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            run_dir = Path(tmp) / "run"
            stage_dir = run_dir / "07-run"
            stage_dir.mkdir(parents=True)
            (stage_dir / "results.json").write_text('{"old": true}', encoding="utf-8")
            (stage_dir / "stdout.txt").write_text("old stdout\n", encoding="utf-8")
            ctx = Context(run_dir=run_dir, topic="test", current_stage=Stage.RUN, config={})

            archive = preserve_stage_outputs(
                ctx,
                artifact_paths=("results.json", "stdout.txt"),
                reason="unit rerun",
            )

            self.assertIsNotNone(archive)
            assert archive is not None
            self.assertIn("results.json", archive.archived_paths)
            self.assertTrue((archive.archive_dir / "results.json").is_file())
            self.assertTrue((stage_dir / "rerun_archive.json").is_file())

    def test_report_context_exposes_canonical_experiment_evidence(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            run_dir = Path(tmp) / "run"
            (run_dir / "07-run").mkdir(parents=True)
            (run_dir / "06-code").mkdir(parents=True)
            (run_dir / "05-design").mkdir(parents=True)
            (run_dir / "07-run" / "results.json").write_text("{}", encoding="utf-8")
            (run_dir / "07-run" / "guard_report.json").write_text("{}", encoding="utf-8")
            (run_dir / "06-code" / "code_review.json").write_text("{}", encoding="utf-8")
            (run_dir / "05-design" / "resource_plan.json").write_text("{}", encoding="utf-8")
            ctx = Context(run_dir=run_dir, topic="test")
            results = {
                "status": "passed",
                "returncode": 0,
                "timed_out": False,
                "metrics": {"score": 0.75},
                "guard": {"status": "warning", "issues": [{"severity": "warning", "code": "x"}]},
                "code_review": {"status": "warning", "summary": {"warning_count": 1}},
                "resource_plan": {"max_runtime_sec": 30},
                "review_failure_recovery": {
                    "reason": "llm_project_failed_code_review",
                    "recovery_mode": "deterministic_fallback_scaffold",
                },
            }

            report_context = build_report_context(
                ctx,
                report_mode="experiment",
                goal="",
                problem="",
                search_meta={},
                synthesis="",
                hypothesis="",
                plan={},
                results=results,
                paper_rows=[],
                papers=[],
                research_evidence_summary="",
            )

            handles = {handle.handle for handle in report_context.source_handles}
            self.assertIn("artifact:canonical_results", handles)
            self.assertIn("artifact:result_guard", handles)
            self.assertIn("artifact:code_review", handles)
            self.assertIn("artifact:resource_plan", handles)
            self.assertIn("artifact:review_failure_recovery", handles)

    def test_repair_fills_missing_required_metrics_for_generated_project(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            project = Path(tmp) / "generated_project"
            (project / "generated_experiment").mkdir(parents=True)
            (project / "generated_experiment" / "runner.py").write_text(
                "def run_experiment():\n    return {'loss': 0.5}\n",
                encoding="utf-8",
            )
            schema = {"primary_metric": "accuracy", "required_metrics": ["accuracy", "macro_f1"]}

            summary = repair_generated_project_from_guard(
                project_dir=project,
                result_schema=schema,
                guard_report={"issues": [{"code": "missing_primary_metric"}]},
                current_metrics={"loss": 0.5},
                output_path=Path(tmp) / "repair_summary.json",
            )

            self.assertEqual(summary["status"], "patched")
            repaired = (project / "generated_experiment" / "runner.py").read_text(encoding="utf-8")
            self.assertIn("accuracy", repaired)
            self.assertIn("macro_f1", repaired)

    def test_local_experiment_tool_gateway_reads_contract_and_results(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            run_dir = Path(tmp) / "run"
            (run_dir / "05-design").mkdir(parents=True)
            (run_dir / "07-run").mkdir(parents=True)
            (run_dir / "05-design" / "experiment_contract.json").write_text(
                '{"contract_id": "exp-test", "task_kind": "greenfield"}',
                encoding="utf-8",
            )
            (run_dir / "05-design" / "result_schema.json").write_text(
                '{"primary_metric": "accuracy", "required_metrics": ["accuracy"]}',
                encoding="utf-8",
            )
            (run_dir / "07-run" / "results.json").write_text(
                '{"returncode": 0, "timed_out": false, "metrics": {"accuracy": 0.9}}',
                encoding="utf-8",
            )

            gateway = LocalExperimentToolGateway(run_dir)
            contract = gateway.call("read_experiment_contract")
            guard = gateway.call("validate_results_schema")

            self.assertEqual(contract.status, "ok")
            self.assertEqual(contract.data["experiment_contract"]["contract_id"], "exp-test")
            self.assertEqual(guard.status, "ok")
            self.assertEqual(guard.data["guard"]["status"], "passed")


if __name__ == "__main__":
    unittest.main()
