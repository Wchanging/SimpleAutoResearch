from __future__ import annotations

import math
import sys
import tempfile
import unittest
from pathlib import Path

from simple_ar.code_task import execute_code_task, initialize_code_task
from simple_ar.code_task.generation.architecture import fallback_architecture_plan
from simple_ar.code_task.generation.task_contract import build_greenfield_task_contract
from simple_ar.experiment.contracts import build_experiment_design_package
from simple_ar.experiment.execution.backend import LocalExecutionBackend, RunRequest
from simple_ar.experiment.execution.diagnosis import diagnose_experiment_run, render_diagnosis_markdown
from simple_ar.experiment.execution.guards import evaluate_result_guard
from simple_ar.code_task.generation.generated_project_repair import repair_generated_project_from_guard
from simple_ar.experiment.execution.results import build_canonical_results
from simple_ar.experiment.rerun import preserve_stage_outputs
from simple_ar.experiment.tools.gateway import LocalExperimentToolGateway
from simple_ar.core.artifacts import read_json
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

    def test_diagnosis_summarizes_missing_metrics_and_review_risk(self) -> None:
        result = {
            "returncode": 0,
            "timed_out": False,
            "metrics": {"loss": 0.4},
            "code_review": {
                "status": "warning",
                "findings": [
                    {
                        "code": "duplicate_pipeline",
                        "message": "Two duplicated experiment pipelines can diverge.",
                    }
                ],
            },
        }
        schema = {"primary_metric": "accuracy", "required_metrics": ["accuracy", "macro_f1"]}
        guard = evaluate_result_guard(result, result_schema=schema)

        diagnosis = diagnose_experiment_run(
            results=result,
            guard_report=guard,
            result_schema=schema,
            code_review=result["code_review"],
        )
        rendered = render_diagnosis_markdown(diagnosis)

        self.assertEqual(diagnosis["status"], "failed")
        self.assertEqual(diagnosis["completion"]["missing_metrics"], ["accuracy", "macro_f1"])
        codes = {item["code"] for item in diagnosis["deficiencies"]}
        self.assertIn("missing_primary_metric", codes)
        self.assertIn("duplicated_or_inconsistent_pipeline", codes)
        self.assertTrue(diagnosis["repair"]["local_repair_supported"])
        self.assertIn("Experiment Diagnosis", rendered)

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

    def test_guard_marks_repaired_experiment_as_warning(self) -> None:
        result = {
            "returncode": 0,
            "timed_out": False,
            "metrics": {"accuracy": 0.86},
            "result_schema": {
                "primary_metric": "accuracy",
                "required_metrics": ["accuracy"],
            },
            "repair": {"status": "patched", "strategy": "schema_metric_fallback"},
        }

        guard = evaluate_result_guard(result)

        self.assertEqual(guard["status"], "warning")
        codes = {issue["code"] for issue in guard["issues"]}
        self.assertIn("experiment_repaired", codes)

    def test_greenfield_code_task_writes_reviewed_runnable_project(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            task_file = root / "task.md"
            task_file.write_text(
                "# Task\n\nCreate a tiny local experiment that reports accuracy and macro_f1.\n",
                encoding="utf-8",
            )
            run_dir = root / "greenfield-code-task"
            initialize_code_task(
                run_dir=run_dir,
                code_root=None,
                task_file=task_file,
                kind="greenfield",
                workspace_mode="empty",
                benchmark_command="python generated_project/main.py",
                primary_metric="accuracy",
                metric_directions={"accuracy": "higher_is_better", "macro_f1": "higher_is_better"},
            )

            result = execute_code_task(
                run_dir,
                use_llm=False,
                to_step="run",
                timeout_sec=10,
                max_files=6,
                max_generated_lines=300,
            )

            self.assertEqual(result.stop_reason, "completed")
            project_dir = run_dir / "code_task" / "workspace" / "generated_project"
            self.assertTrue(project_dir.is_dir())
            self.assertTrue((project_dir / "main.py").is_file())
            self.assertTrue((run_dir / "code_task" / "meta" / "architecture_plan.json").is_file())
            self.assertTrue((run_dir / "code_task" / "meta" / "file_plan.json").is_file())
            self.assertTrue((run_dir / "code_task" / "meta" / "review_report.json").is_file())
            metrics = read_json(run_dir / "code_task" / "run" / "patched" / "metrics.json")
            self.assertIn("accuracy", metrics)
            self.assertIn("macro_f1", metrics)

    def test_greenfield_contract_includes_task_file_requirements(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            task_file = Path(tmp) / "task.md"
            task_file.write_text(
                "# Task\n\nTrain a tiny local classifier and report macro_f1.\n",
                encoding="utf-8",
            )

            package = build_experiment_design_package(
                {
                    "task_kind": "greenfield",
                    "task_objective": "Build a lightweight training project.",
                    "task_task_file": str(task_file),
                    "implementation_mode": "generate_project",
                    "evaluation_primary_metric": "macro_f1",
                    "evaluation_required_metrics": ["macro_f1"],
                    "generation_enabled": True,
                },
                topic="",
                hypothesis="",
                template="greenfield_project",
            )

            self.assertIn("Train a tiny local classifier", package.contract.objective)
            self.assertTrue(
                any("macro_f1" in item for item in package.contract.constraints),
                package.contract.constraints,
            )

    def test_greenfield_task_contract_extracts_evidence_plan(self) -> None:
        contract = build_greenfield_task_contract(
            (
                "# Task\n\n"
                "- Hypothesis H1: robust preprocessing improves accuracy on noisy datasets.\n"
                "- Compare standard scaling against no scaling and robust scaling.\n"
                "- Produce artifacts/results.json and artifacts/report.md with per-dataset rows.\n"
                "- Evaluate macro_f1 and accuracy across multiple conditions.\n"
            ),
            benchmark_command="python main.py",
            max_files=8,
            max_generated_lines=1200,
            result_schema={"primary_metric": "accuracy", "required_metrics": ["accuracy", "macro_f1"]},
        )

        evidence = contract["evidence_plan"]
        self.assertIn("accuracy", evidence["required_metrics"])
        self.assertTrue(evidence["hypotheses"])
        self.assertTrue(evidence["required_comparisons"])
        self.assertTrue(any("artifacts/results.json" in item for item in evidence["required_artifacts"]))
        self.assertTrue(any("Hypothesis evidence" in item for item in contract["success_criteria"]))

    def test_medium_greenfield_fallback_architecture_has_real_module_boundaries(self) -> None:
        plan = fallback_architecture_plan(
            contract={"objective": "Build a medium-light classifier experiment."},
            result_schema={
                "primary_metric": "accuracy",
                "required_metrics": ["accuracy", "macro_f1", "condition_count"],
            },
            resource_plan={"max_files": 10, "max_generated_lines": 1800},
            domain_profile={},
        )

        paths = [row["path"] for row in plan["files"]]

        self.assertIn("main.py", paths)
        self.assertIn("generated_experiment/config.py", paths)
        self.assertIn("generated_experiment/inputs.py", paths)
        self.assertIn("generated_experiment/core.py", paths)
        self.assertIn("generated_experiment/metrics.py", paths)
        self.assertIn("generated_experiment/analysis.py", paths)
        self.assertIn("generated_experiment/runner.py", paths)
        self.assertLessEqual(len(paths), 10)

    def test_large_capability_fallback_architecture_preserves_task_surface(self) -> None:
        plan = fallback_architecture_plan(
            contract={
                "objective": "Greenfield analysis workbench",
                "task": (
                    "Build an open-source style project with input loading, preprocessing, analysis, "
                    "metrics, resource detection, self-check, README, artifacts/results.json, "
                    "artifacts/report.md, and condition_results.jsonl."
                ),
            },
            result_schema={
                "primary_metric": "best_score",
                "required_metrics": ["best_score", "accuracy", "macro_f1", "condition_count"],
            },
            resource_plan={"max_files": 16, "max_generated_lines": 6000},
            domain_profile={"task_excerpt": "input processing analysis reporting self-check resource profile"},
        )

        paths = [row["path"] for row in plan["files"]]

        self.assertIn("README.md", paths)
        self.assertIn("generated_experiment/config.py", paths)
        self.assertIn("generated_experiment/inputs.py", paths)
        self.assertIn("generated_experiment/processing.py", paths)
        self.assertIn("generated_experiment/analysis.py", paths)
        self.assertIn("generated_experiment/reporting.py", paths)
        self.assertIn("generated_experiment/validation.py", paths)
        self.assertLessEqual(len(paths), 16)

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
                "diagnosis": {
                    "status": "warning",
                    "summary": "Run has warnings.",
                    "completion": {"missing_metrics": []},
                    "repair": {"local_repair_supported": False},
                    "deficiencies": [{"severity": "major", "code": "code_review_warning"}],
                },
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
            self.assertIn("artifact:experiment_diagnosis", handles)
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
            (project / "main.py").write_text(
                "raise RuntimeError('old broken entrypoint')\n",
                encoding="utf-8",
            )
            schema = {"primary_metric": "accuracy", "required_metrics": ["accuracy", "macro_f1"]}

            summary = repair_generated_project_from_guard(
                project_dir=project,
                result_schema=schema,
                guard_report={"issues": [{"code": "missing_primary_metric"}]},
                diagnosis_report={
                    "status": "failed",
                    "completion": {"missing_metrics": ["accuracy", "macro_f1"]},
                    "deficiencies": [{"code": "missing_primary_metric"}],
                },
                current_metrics={"loss": 0.5},
                output_path=Path(tmp) / "repair_summary.json",
            )

            self.assertEqual(summary["status"], "patched")
            repaired = (project / "generated_experiment" / "runner.py").read_text(encoding="utf-8")
            self.assertIn("accuracy", repaired)
            self.assertIn("macro_f1", repaired)
            namespace: dict[str, object] = {}
            exec(repaired, namespace)
            metrics = namespace["run_experiment"]()  # type: ignore[operator]
            self.assertIsInstance(metrics["accuracy"], float)
            self.assertIsInstance(metrics["macro_f1"], float)
            main = (project / "main.py").read_text(encoding="utf-8")
            self.assertIn("generated_experiment.runner", main)
            self.assertFalse((project / "main.py.before_repair").exists())
            self.assertEqual(summary["snapshot"]["captured_count"], 3)
            snapshot = read_json(Path(summary["snapshot"]["manifest"]))
            self.assertEqual(
                sorted(row["path"] for row in snapshot["files"]),
                ["generated_experiment/__init__.py", "generated_experiment/runner.py", "main.py"],
            )

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
            (run_dir / "07-run" / "diagnosis.json").write_text(
                '{"status": "passed", "summary": "ok"}',
                encoding="utf-8",
            )

            gateway = LocalExperimentToolGateway(run_dir)
            contract = gateway.call("read_experiment_contract")
            guard = gateway.call("validate_results_schema")
            diagnosis = gateway.call("read_experiment_diagnosis")
            failure_view = gateway.call("inspect_execution_failure")

            self.assertEqual(contract.status, "ok")
            self.assertEqual(contract.data["experiment_contract"]["contract_id"], "exp-test")
            self.assertEqual(guard.status, "ok")
            self.assertEqual(guard.data["guard"]["status"], "passed")
            self.assertEqual(diagnosis.status, "ok")
            self.assertEqual(diagnosis.data["diagnosis"]["status"], "passed")
            self.assertEqual(failure_view.data["diagnosis"]["summary"], "ok")

    def test_local_experiment_tool_gateway_searches_generated_code(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            run_dir = Path(tmp) / "run"
            project = run_dir / "06-code" / "generated_project"
            project.mkdir(parents=True)
            (project / "main.py").write_text(
                "def run_experiment():\n"
                "    metrics = {'accuracy': 0.9, 'macro_f1': 0.8}\n"
                "    return metrics\n",
                encoding="utf-8",
            )
            (project / "config.json").write_text('{"seed": 7}\n', encoding="utf-8")
            gateway = LocalExperimentToolGateway(run_dir)

            listing = gateway.call("list_generated_code_files", {"extensions": [".py"]})
            self.assertEqual(listing.status, "ok")
            self.assertEqual(listing.data["files"][0]["path"], "main.py")

            snippet = gateway.call(
                "read_generated_code_file",
                {"path": "main.py", "start_line": 2, "max_lines": 1},
            )
            self.assertEqual(snippet.status, "ok")
            self.assertIn("accuracy", snippet.data["text"])

            matches = gateway.call("search_generated_code", {"query": "macro_f1"})
            self.assertEqual(matches.status, "ok")
            self.assertEqual(matches.data["matches"][0]["line"], 2)

            blocked = gateway.call("read_generated_code_file", {"path": "../secret.txt"})
            self.assertEqual(blocked.status, "error")


if __name__ == "__main__":
    unittest.main()
