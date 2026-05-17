from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path

from simple_ar.artifacts import read_json, read_jsonl, read_text, write_json, write_text
from simple_ar.cli import main
from simple_ar.code_task import (
    PatchValidationError,
    analyze_code_task_failure,
    apply_patch_edits,
    generate_patch_plan,
    initialize_code_task,
    probe_code_task_environment,
    propose_repair_edits,
    record_plan_decision,
    run_code_task_baseline,
    run_code_task_benchmark,
    validate_code_task,
)


TEST_ROOT = Path(__file__).resolve().parents[1] / ".tmp_tests"


class CodeTaskTests(unittest.TestCase):
    def test_init_copies_workspace_and_indexes_python_ast(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "toy_project"
            task_file = root / "task.md"
            _write_toy_project(code_root)
            write_text(task_file, "# Task\n\nImprove the spam classifier without changing the API.\n")

            run_dir = root / "runs" / "code-task-run"
            result = initialize_code_task(
                run_dir=run_dir,
                code_root=code_root,
                task_file=task_file,
                benchmark_command="python -m unittest discover -s tests",
                max_file_bytes=10_000,
            )

            workspace = result.workspace_dir
            self.assertTrue((workspace / "spam_model.py").is_file())
            self.assertTrue((workspace / "tests" / "test_spam_model.py").is_file())
            self.assertFalse((workspace / ".env").exists())
            self.assertFalse((workspace / ".git" / "config").exists())
            self.assertEqual(
                read_text(code_root / "spam_model.py"),
                read_text(workspace / "spam_model.py"),
            )

            manifest = read_json(run_dir / "manifest.json")
            self.assertEqual(manifest["workflow"], "code_task")
            self.assertEqual(manifest["layout"]["workspace"], "code_task/workspace")
            self.assertEqual(manifest["benchmark"]["executed"], False)
            self.assertEqual(manifest["environment"]["policy"]["mode"], "current")
            self.assertEqual(manifest["environment"]["policy"]["python_executable"], sys.executable)
            self.assertGreaterEqual(manifest["copy"]["skipped_count"], 2)

            index = read_json(result.codebase_index_path)
            self.assertEqual(index["project"]["python_file_count"], 2)
            self.assertEqual(index["project"]["test_file_count"], 1)
            spam_model = _indexed_file(index, "spam_model.py")
            self.assertIn("source", spam_model["role_tags"])
            self.assertEqual(spam_model["python"]["syntax_ok"], True)
            self.assertIn("math", spam_model["python"]["imports"])
            self.assertEqual(
                [item["name"] for item in spam_model["python"]["classes"]],
                ["SpamModel"],
            )
            self.assertEqual(
                [item["name"] for item in spam_model["python"]["functions"]],
                ["predict"],
            )
            self.assertEqual(spam_model["python"]["has_main_guard"], True)

    def test_code_task_init_cli_prints_summary(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "toy_project"
            task_file = root / "task.md"
            output_root = root / "runs"
            _write_toy_project(code_root)
            write_text(task_file, "# Task\n\nImprove tests.\n")

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                main(
                    [
                        "code-task",
                        "init",
                        "--code-root",
                        str(code_root),
                        "--task-file",
                        str(task_file),
                        "--output-root",
                        str(output_root),
                        "--name",
                        "demo-code-task",
                    ]
                )

            output = stdout.getvalue()
            self.assertIn("Code task run:", output)
            self.assertIn("Workspace:", output)
            self.assertIn("Indexed:", output)
            run_dir = next(output_root.iterdir())
            self.assertTrue((run_dir / "code_task" / "workspace" / "spam_model.py").is_file())
            self.assertTrue((run_dir / "code_task" / "meta" / "codebase_index.json").is_file())

            status_stdout = io.StringIO()
            with contextlib.redirect_stdout(status_stdout):
                main(["status", str(run_dir)])

            status_text = status_stdout.getvalue()
            self.assertIn("Workflow: code_task", status_text)
            self.assertIn("python files: 2", status_text)

    def test_probe_code_task_environment_writes_report_and_manifest(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "toy_project"
            task_file = root / "task.md"
            _write_toy_project(code_root)
            write_text(task_file, "# Task\n\nProbe this project.\n")
            run_dir = root / "runs" / "code-task-run"
            initialize_code_task(
                run_dir=run_dir,
                code_root=code_root,
                task_file=task_file,
                benchmark_command="python -m unittest discover -s tests",
            )

            result = probe_code_task_environment(run_dir)

            self.assertTrue(result.report_path.is_file())
            self.assertIn(result.status, {"ok", "warning"})
            report = read_json(result.report_path)
            self.assertEqual(report["schema_version"], 1)
            self.assertEqual(report["project"]["dependency_files"], ["pyproject.toml"])
            self.assertEqual(report["project"]["test_dirs"], ["tests"])
            self.assertTrue(report["tools"]["python"]["available"])
            self.assertEqual(report["execution_policy"]["mode"], "current")
            self.assertIn("available", report["gpu"])

            manifest = read_json(run_dir / "manifest.json")
            self.assertEqual(manifest["layout"]["environment_report"], "code_task/meta/environment_report.json")
            self.assertEqual(manifest["environment"]["report"], "code_task/meta/environment_report.json")
            self.assertEqual(manifest["status"], "environment_probed")
            summary = read_text(run_dir / "code_task" / "summary.md")
            self.assertIn("## Environment", summary)
            self.assertIn("pyproject.toml", summary)

    def test_code_task_probe_cli_prints_environment_summary(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "toy_project"
            task_file = root / "task.md"
            output_root = root / "runs"
            _write_toy_project(code_root)
            write_text(task_file, "# Task\n\nProbe from CLI.\n")

            with contextlib.redirect_stdout(io.StringIO()):
                main(
                    [
                        "code-task",
                        "init",
                        "--code-root",
                        str(code_root),
                        "--task-file",
                        str(task_file),
                        "--output-root",
                        str(output_root),
                    ]
                )
            run_dir = next(output_root.iterdir())

            probe_stdout = io.StringIO()
            with contextlib.redirect_stdout(probe_stdout):
                main(["code-task", "probe", str(run_dir)])

            output = probe_stdout.getvalue()
            self.assertIn("Environment report:", output)
            self.assertIn("Status:", output)
            status_stdout = io.StringIO()
            with contextlib.redirect_stdout(status_stdout):
                main(["status", str(run_dir)])
            self.assertIn("Environment:", status_stdout.getvalue())

    def test_patch_plan_offline_writes_reviewable_plan_and_updates_manifest(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "toy_project"
            task_file = root / "task.md"
            _write_toy_project(code_root)
            write_text(
                task_file,
                "# Task\n\nImprove spam keyword handling and keep the public predict API stable.\n",
            )
            run_dir = root / "runs" / "code-task-run"
            initialize_code_task(
                run_dir=run_dir,
                code_root=code_root,
                task_file=task_file,
                benchmark_command="python -m unittest discover -s tests",
            )

            result = generate_patch_plan(run_dir, use_llm=False)

            self.assertEqual(result.mode, "offline")
            self.assertTrue(result.pending_approval)
            self.assertTrue((run_dir / "code_task" / "patch_plan.md").is_file())
            plan_text = read_text(run_dir / "code_task" / "patch_plan.md")
            self.assertIn("# Patch Plan", plan_text)
            self.assertIn("## Files To Modify", plan_text)
            self.assertIn("spam_model.py", plan_text)
            self.assertIn("python -m unittest discover -s tests", plan_text)

            manifest = read_json(run_dir / "manifest.json")
            self.assertEqual(manifest["status"], "planned")
            self.assertEqual(manifest["plan"]["status"], "pending_approval")
            self.assertEqual(manifest["layout"]["patch_plan"], "code_task/patch_plan.md")

    def test_patch_plan_includes_baseline_and_environment_context(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "metric_project"
            task_file = root / "task.md"
            _write_metric_project(code_root, value="0.50")
            write_text(task_file, "# Task\n\nImprove the printed accuracy metric.\n")
            run_dir = root / "runs" / "code-task-run"
            initialize_code_task(
                run_dir=run_dir,
                code_root=code_root,
                task_file=task_file,
                benchmark_command="python benchmark.py",
            )
            probe_code_task_environment(run_dir)
            run_code_task_baseline(run_dir, timeout_sec=10)

            result = generate_patch_plan(run_dir, use_llm=False)

            self.assertEqual(result.mode, "offline")
            plan_text = read_text(run_dir / "code_task" / "patch_plan.md")
            self.assertIn("## Run Context", plan_text)
            self.assertIn("Baseline metrics", plan_text)
            self.assertIn("`accuracy`=0.5", plan_text)
            manifest = read_json(run_dir / "manifest.json")
            self.assertEqual(manifest["plan"]["context"]["baseline_status"], "passed")
            self.assertEqual(manifest["plan"]["context"]["baseline_metrics"]["accuracy"], 0.5)

    def test_code_task_plan_and_decide_cli(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "toy_project"
            task_file = root / "task.md"
            output_root = root / "runs"
            _write_toy_project(code_root)
            write_text(task_file, "# Task\n\nImprove spam classifier accuracy.\n")

            with contextlib.redirect_stdout(io.StringIO()):
                main(
                    [
                        "code-task",
                        "init",
                        "--code-root",
                        str(code_root),
                        "--task-file",
                        str(task_file),
                        "--output-root",
                        str(output_root),
                    ]
                )
            run_dir = next(output_root.iterdir())

            plan_stdout = io.StringIO()
            with contextlib.redirect_stdout(plan_stdout):
                main(["code-task", "plan", str(run_dir), "--no-llm"])

            self.assertIn("Patch plan:", plan_stdout.getvalue())
            self.assertTrue((run_dir / "code_task" / "patch_plan.md").is_file())

            decide_stdout = io.StringIO()
            with contextlib.redirect_stdout(decide_stdout):
                main(
                    [
                        "code-task",
                        "decide-plan",
                        str(run_dir),
                        "--decision",
                        "approve",
                        "--note",
                        "Looks small enough.",
                    ]
                )

            self.assertIn("Decision: approve", decide_stdout.getvalue())
            decisions = read_jsonl(run_dir / "code_task" / "meta" / "hitl_decisions.jsonl")
            self.assertEqual(decisions[-1]["decision"], "approve")
            self.assertEqual(decisions[-1]["note"], "Looks small enough.")
            manifest = read_json(run_dir / "manifest.json")
            self.assertEqual(manifest["status"], "plan_approved")
            self.assertEqual(manifest["plan"]["status"], "approved")

            status_stdout = io.StringIO()
            with contextlib.redirect_stdout(status_stdout):
                main(["status", str(run_dir)])
            self.assertIn("Plan:", status_stdout.getvalue())
            self.assertIn("status: approved", status_stdout.getvalue())

    def test_apply_edits_requires_approved_plan_and_then_patches_workspace(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "toy_project"
            task_file = root / "task.md"
            _write_toy_project(code_root)
            write_text(task_file, "# Task\n\nAlso detect prize as spam.\n")
            run_dir = root / "runs" / "code-task-run"
            initialize_code_task(run_dir=run_dir, code_root=code_root, task_file=task_file)
            generate_patch_plan(run_dir, use_llm=False)
            proposal_path = _write_valid_edit_proposal(run_dir)

            with self.assertRaises(PermissionError):
                apply_patch_edits(run_dir, edits_file=proposal_path)

            record_plan_decision(run_dir, decision="approve", note="Small targeted edit.")
            result = apply_patch_edits(run_dir, edits_file=proposal_path)

            workspace_model = run_dir / "code_task" / "workspace" / "spam_model.py"
            self.assertIn("'prize'", read_text(workspace_model))
            self.assertNotIn("'prize'", read_text(code_root / "spam_model.py"))
            self.assertEqual(result.changed_files, ("spam_model.py",))
            self.assertTrue((run_dir / "code_task" / "patch.diff").is_file())
            self.assertTrue((run_dir / "code_task" / "meta" / "applied_edits.json").is_file())
            self.assertFalse((run_dir / "code_task" / "meta" / "pre_patch_manifest.json").exists())
            self.assertFalse((run_dir / "code_task" / "meta" / "post_patch_manifest.json").exists())
            applied = read_json(run_dir / "code_task" / "meta" / "applied_edits.json")
            self.assertEqual(applied["changed_files"], ["spam_model.py"])
            self.assertTrue(applied["edits"][0]["old_sha256"])
            self.assertTrue(applied["edits"][0]["new_sha256"])
            diff_text = read_text(run_dir / "code_task" / "patch.diff")
            self.assertIn("+    lowered = text.lower()", diff_text)
            manifest = read_json(run_dir / "manifest.json")
            self.assertEqual(manifest["status"], "patched")
            self.assertEqual(manifest["patch"]["status"], "applied")
            self.assertNotIn("pre_patch_manifest", manifest["patch"])
            self.assertNotIn("post_patch_manifest", manifest["patch"])

    def test_apply_edits_rejects_path_traversal_without_modifying_workspace(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "toy_project"
            task_file = root / "task.md"
            _write_toy_project(code_root)
            write_text(task_file, "# Task\n\nTry an unsafe edit.\n")
            run_dir = root / "runs" / "code-task-run"
            initialize_code_task(run_dir=run_dir, code_root=code_root, task_file=task_file)
            generate_patch_plan(run_dir, use_llm=False)
            record_plan_decision(run_dir, decision="approve")
            proposal_path = root / "bad_edits.json"
            write_json(
                proposal_path,
                {
                    "edits": [
                        {
                            "path": "../outside.py",
                            "old": "x",
                            "new": "y",
                            "reason": "unsafe",
                        }
                    ]
                },
            )
            before = read_text(run_dir / "code_task" / "workspace" / "spam_model.py")

            with self.assertRaises(PatchValidationError):
                apply_patch_edits(run_dir, edits_file=proposal_path)

            after = read_text(run_dir / "code_task" / "workspace" / "spam_model.py")
            self.assertEqual(before, after)
            self.assertFalse((run_dir / "code_task" / "patch.diff").exists())

    def test_code_task_propose_and_apply_cli_with_manual_edits_file(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "toy_project"
            task_file = root / "task.md"
            output_root = root / "runs"
            _write_toy_project(code_root)
            write_text(task_file, "# Task\n\nDetect prize messages as spam.\n")
            with contextlib.redirect_stdout(io.StringIO()):
                main(
                    [
                        "code-task",
                        "init",
                        "--code-root",
                        str(code_root),
                        "--task-file",
                        str(task_file),
                        "--output-root",
                        str(output_root),
                    ]
                )
            run_dir = next(output_root.iterdir())
            with contextlib.redirect_stdout(io.StringIO()):
                main(["code-task", "plan", str(run_dir), "--no-llm"])
                main(["code-task", "decide-plan", str(run_dir), "--decision", "approve"])

            propose_stdout = io.StringIO()
            with contextlib.redirect_stdout(propose_stdout):
                main(["code-task", "propose-edits", str(run_dir), "--no-llm"])
            self.assertIn("Edit count: 0", propose_stdout.getvalue())
            self.assertTrue((run_dir / "code_task" / "meta" / "proposed_edits.json").is_file())

            edits_file = _write_valid_edit_proposal(run_dir, path=root / "manual_edits.json")
            apply_stdout = io.StringIO()
            with contextlib.redirect_stdout(apply_stdout):
                main(["code-task", "apply-edits", str(run_dir), "--edits-file", str(edits_file)])

            self.assertIn("Changed files: 1", apply_stdout.getvalue())
            status_stdout = io.StringIO()
            with contextlib.redirect_stdout(status_stdout):
                main(["status", str(run_dir)])
            self.assertIn("Patch:", status_stdout.getvalue())
            self.assertIn("status: applied", status_stdout.getvalue())

    def test_validate_code_task_reports_warnings_and_strict_errors(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "toy_project"
            task_file = root / "task.md"
            _write_toy_project(code_root)
            write_text(
                code_root / "danger.py",
                "import os\n\n\ndef run():\n    os.system('echo unsafe')\n",
            )
            write_text(task_file, "# Task\n\nValidate risky code.\n")
            run_dir = root / "runs" / "code-task-run"
            initialize_code_task(run_dir=run_dir, code_root=code_root, task_file=task_file)

            result = validate_code_task(run_dir)

            self.assertEqual(result.status, "passed")
            self.assertEqual(result.error_count, 0)
            self.assertGreaterEqual(result.warning_count, 1)
            report = read_json(run_dir / "code_task" / "meta" / "validation_report.json")
            self.assertTrue(any(item["code"] == "risky_call" for item in report["issues"]))

            strict = validate_code_task(run_dir, strict=True)
            self.assertEqual(strict.status, "failed")
            self.assertGreaterEqual(strict.error_count, 1)

    def test_run_code_task_benchmark_captures_outputs_and_updates_status(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "toy_project"
            task_file = root / "task.md"
            _write_toy_project(code_root)
            write_text(task_file, "# Task\n\nRun existing tests.\n")
            run_dir = root / "runs" / "code-task-run"
            initialize_code_task(
                run_dir=run_dir,
                code_root=code_root,
                task_file=task_file,
                benchmark_command="python -m unittest discover -s tests",
            )

            result = run_code_task_benchmark(run_dir, timeout_sec=10)

            self.assertEqual(result.label, "patched")
            self.assertEqual(result.status, "passed")
            self.assertEqual(result.returncode, 0)
            self.assertTrue((run_dir / "code_task" / "run" / "patched" / "execution_report.json").is_file())
            self.assertTrue((run_dir / "code_task" / "run" / "patched" / "stdout.txt").is_file())
            manifest = read_json(run_dir / "manifest.json")
            self.assertEqual(manifest["benchmark"]["last_status"], "passed")
            self.assertEqual(manifest["benchmark"]["latest_label"], "patched")
            self.assertEqual(manifest["benchmark"]["runs"]["patched"]["status"], "passed")
            report = read_json(run_dir / "code_task" / "run" / "patched" / "execution_report.json")
            self.assertEqual(report["environment"]["mode"], "current")
            self.assertEqual(report["command"][0], sys.executable)
            self.assertEqual(manifest["status"], "benchmark_passed")

    def test_run_code_task_baseline_records_pre_patch_result(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "toy_project"
            task_file = root / "task.md"
            _write_toy_project(code_root)
            write_text(task_file, "# Task\n\nCapture baseline.\n")
            run_dir = root / "runs" / "code-task-run"
            initialize_code_task(
                run_dir=run_dir,
                code_root=code_root,
                task_file=task_file,
                benchmark_command="python -m unittest discover -s tests",
            )

            result = run_code_task_baseline(run_dir, timeout_sec=10)

            self.assertEqual(result.label, "baseline")
            self.assertEqual(result.status, "passed")
            self.assertTrue((run_dir / "code_task" / "run" / "baseline" / "execution_report.json").is_file())
            manifest = read_json(run_dir / "manifest.json")
            self.assertEqual(manifest["status"], "baseline_passed")
            self.assertEqual(manifest["benchmark"]["latest_label"], "baseline")
            self.assertEqual(manifest["benchmark"]["runs"]["baseline"]["status"], "passed")
            summary = read_text(run_dir / "code_task" / "summary.md")
            self.assertIn("### Baseline", summary)
            self.assertIn("Environment mode: `current`", summary)

    def test_patched_run_writes_comparison_when_baseline_exists(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "metric_project"
            task_file = root / "task.md"
            _write_metric_project(code_root, value="0.50")
            write_text(task_file, "# Task\n\nImprove the printed accuracy metric.\n")
            run_dir = root / "runs" / "code-task-run"
            initialize_code_task(
                run_dir=run_dir,
                code_root=code_root,
                task_file=task_file,
                benchmark_command="python benchmark.py",
            )

            baseline = run_code_task_baseline(run_dir, timeout_sec=10)
            self.assertEqual(baseline.metrics["accuracy"], 0.5)
            write_text(run_dir / "code_task" / "workspace" / "metric_value.txt", "0.80\n")
            patched = run_code_task_benchmark(run_dir, timeout_sec=10)

            self.assertEqual(patched.metrics["accuracy"], 0.8)
            comparison_path = run_dir / "code_task" / "run" / "comparison.json"
            self.assertTrue(comparison_path.is_file())
            comparison = read_json(comparison_path)
            self.assertEqual(comparison["verdict"], "improved")
            self.assertAlmostEqual(comparison["deltas"]["accuracy"], 0.3)
            manifest = read_json(run_dir / "manifest.json")
            self.assertEqual(manifest["benchmark"]["comparison"]["verdict"], "improved")
            self.assertEqual(manifest["layout"]["comparison"], "code_task/run/comparison.json")
            summary = read_text(run_dir / "code_task" / "summary.md")
            self.assertIn("## Result", summary)
            self.assertIn("Outcome: `improved`", summary)
            self.assertIn("Next step:", summary)
            self.assertIn("### Comparison", summary)
            self.assertIn("Verdict: `improved`", summary)
            self.assertIn("+0.3", summary)

    def test_comparison_uses_configured_direction_for_custom_metric(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "metric_project"
            task_file = root / "task.md"
            _write_metric_project(
                code_root,
                value="10.0",
                metric_name="custom_reward",
            )
            write_text(task_file, "# Task\n\nImprove the custom reward metric.\n")
            run_dir = root / "runs" / "code-task-run"
            initialize_code_task(
                run_dir=run_dir,
                code_root=code_root,
                task_file=task_file,
                benchmark_command="python benchmark.py",
                primary_metric="custom_reward",
                metric_directions={"custom_reward": "higher"},
            )

            baseline = run_code_task_baseline(run_dir, timeout_sec=10)
            self.assertEqual(baseline.metrics["custom_reward"], 10.0)
            write_text(
                run_dir / "code_task" / "workspace" / "metric_value.txt",
                "12.5\n",
            )
            run_code_task_benchmark(run_dir, timeout_sec=10)

            comparison = read_json(run_dir / "code_task" / "run" / "comparison.json")
            self.assertEqual(comparison["verdict"], "improved")
            self.assertEqual(comparison["metric_config"]["primary_metric"], "custom_reward")
            row = comparison["metrics"][0]
            self.assertEqual(row["name"], "custom_reward")
            self.assertEqual(row["direction"], "higher_is_better")
            self.assertEqual(row["direction_source"], "configured")
            self.assertEqual(row["interpretation"], "improved")
            self.assertEqual(row["is_primary"], True)
            summary = read_text(run_dir / "code_task" / "summary.md")
            self.assertIn("Primary metric: `custom_reward` (higher_is_better)", summary)
            self.assertIn("Outcome: `improved`", summary)

            status_stdout = io.StringIO()
            with contextlib.redirect_stdout(status_stdout):
                main(["status", str(run_dir)])
            status = status_stdout.getvalue()
            self.assertIn("- summary:", status)
            self.assertIn("- primary metric: custom_reward", status)
            self.assertIn("- comparison: improved", status)
            self.assertIn("custom_reward=+2.5", status)

    def test_unknown_metric_is_recorded_but_not_overinterpreted(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "metric_project"
            task_file = root / "task.md"
            _write_metric_project(code_root, value="10.0", metric_name="custom_reward")
            write_text(task_file, "# Task\n\nImprove an unknown custom metric.\n")
            run_dir = root / "runs" / "code-task-run"
            initialize_code_task(
                run_dir=run_dir,
                code_root=code_root,
                task_file=task_file,
                benchmark_command="python benchmark.py",
            )

            run_code_task_baseline(run_dir, timeout_sec=10)
            write_text(
                run_dir / "code_task" / "workspace" / "metric_value.txt",
                "12.5\n",
            )
            run_code_task_benchmark(run_dir, timeout_sec=10)

            comparison = read_json(run_dir / "code_task" / "run" / "comparison.json")
            self.assertEqual(comparison["verdict"], "inconclusive")
            self.assertEqual(comparison["deltas"]["custom_reward"], 2.5)
            self.assertEqual(comparison["metrics"][0]["direction"], "unknown")
            self.assertEqual(comparison["metrics"][0]["interpretation"], "changed")

    def test_code_task_init_cli_records_metric_config(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "metric_project"
            task_file = root / "task.md"
            output_root = root / "runs"
            _write_metric_project(code_root, value="0.50", metric_name="macro_f1")
            write_text(task_file, "# Task\n\nImprove macro F1.\n")

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                main(
                    [
                        "code-task",
                        "init",
                        "--code-root",
                        str(code_root),
                        "--task-file",
                        str(task_file),
                        "--output-root",
                        str(output_root),
                        "--benchmark-command",
                        "python benchmark.py",
                        "--primary-metric",
                        "macro_f1",
                        "--metric-direction",
                        "macro_f1=higher",
                        "--metric-direction",
                        "inference_time_ms=resource",
                    ]
                )

            run_dir = next(output_root.iterdir())
            manifest = read_json(run_dir / "manifest.json")
            self.assertEqual(manifest["benchmark"]["primary_metric"], "macro_f1")
            self.assertEqual(
                manifest["benchmark"]["metric_directions"]["macro_f1"],
                "higher_is_better",
            )
            self.assertEqual(
                manifest["benchmark"]["metric_directions"]["inference_time_ms"],
                "resource",
            )
            output = stdout.getvalue()
            self.assertIn("Primary metric: macro_f1", output)
            self.assertIn("Metric directions:", output)

    def test_code_task_init_cli_reads_toml_config(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "metric_project"
            task_file = root / "task.md"
            output_root = root / "configured_runs"
            config_file = root / "code_task.toml"
            _write_metric_project(code_root, value="10.0", metric_name="custom_reward")
            write_text(task_file, "# Task\n\nImprove configured reward.\n")
            write_text(
                config_file,
                (
                    "[code_task]\n"
                    f'code_root = "{code_root.as_posix()}"\n'
                    f'task_file = "{task_file.as_posix()}"\n'
                    f'output_root = "{output_root.as_posix()}"\n'
                    'name = "configured-metric-task"\n'
                    "\n"
                    "[benchmark]\n"
                    'command = "python benchmark.py"\n'
                    'primary_metric = "custom_reward"\n'
                    "\n"
                    "[benchmark.metric_directions]\n"
                    'custom_reward = "higher"\n'
                    'latency_ms = "resource"\n'
                    "\n"
                    "[environment]\n"
                    'mode = "current"\n'
                    "\n"
                    "[safety]\n"
                    "max_file_bytes = 10000\n"
                ),
            )

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                main(["code-task", "init", "--config", str(config_file)])

            run_dir = next(output_root.iterdir())
            manifest = read_json(run_dir / "manifest.json")
            self.assertEqual(manifest["benchmark"]["command"], "python benchmark.py")
            self.assertEqual(manifest["benchmark"]["primary_metric"], "custom_reward")
            self.assertEqual(
                manifest["benchmark"]["metric_directions"]["custom_reward"],
                "higher_is_better",
            )
            self.assertEqual(
                manifest["benchmark"]["metric_directions"]["latency_ms"],
                "resource",
            )
            self.assertEqual(manifest["copy"]["max_file_bytes"], 10000)
            self.assertIn("Config:", stdout.getvalue())

    def test_external_env_mode_records_python_policy_and_uses_it(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "toy_project"
            task_file = root / "task.md"
            _write_toy_project(code_root)
            write_text(task_file, "# Task\n\nRun with an explicit interpreter.\n")
            run_dir = root / "runs" / "code-task-run"
            initialize_code_task(
                run_dir=run_dir,
                code_root=code_root,
                task_file=task_file,
                benchmark_command="python -m unittest discover -s tests",
                env_mode="external",
                python_executable=sys.executable,
            )

            result = probe_code_task_environment(run_dir)
            self.assertIn(result.status, {"ok", "warning"})
            baseline = run_code_task_baseline(run_dir, timeout_sec=10)

            report = read_json(baseline.report_path)
            self.assertEqual(report["environment"]["mode"], "external")
            self.assertEqual(report["environment"]["python_executable"], sys.executable)
            self.assertEqual(report["command"][0], sys.executable)
            manifest = read_json(run_dir / "manifest.json")
            self.assertEqual(manifest["environment"]["policy"]["mode"], "external")
            self.assertEqual(
                manifest["benchmark"]["runs"]["baseline"]["environment"]["mode"],
                "external",
            )

    def test_code_task_cli_can_override_env_mode_for_baseline(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "toy_project"
            task_file = root / "task.md"
            output_root = root / "runs"
            _write_toy_project(code_root)
            write_text(task_file, "# Task\n\nRun CLI baseline with explicit env.\n")
            with contextlib.redirect_stdout(io.StringIO()):
                main(
                    [
                        "code-task",
                        "init",
                        "--code-root",
                        str(code_root),
                        "--task-file",
                        str(task_file),
                        "--output-root",
                        str(output_root),
                        "--benchmark-command",
                        "python -m unittest discover -s tests",
                    ]
                )
            run_dir = next(output_root.iterdir())

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                main(
                    [
                        "code-task",
                        "baseline",
                        str(run_dir),
                        "--timeout",
                        "10",
                        "--env-mode",
                        "external",
                        "--python",
                        sys.executable,
                    ]
                )

            self.assertIn("Status: passed", stdout.getvalue())
            manifest = read_json(run_dir / "manifest.json")
            self.assertEqual(manifest["environment"]["policy"]["mode"], "external")

    def test_analyze_failure_and_offline_repair_proposal(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "toy_project"
            task_file = root / "task.md"
            _write_toy_project(code_root)
            write_text(task_file, "# Task\n\nBreak then diagnose the spam classifier.\n")
            run_dir = root / "runs" / "code-task-run"
            initialize_code_task(
                run_dir=run_dir,
                code_root=code_root,
                task_file=task_file,
                benchmark_command="python -m unittest discover -s tests",
            )
            generate_patch_plan(run_dir, use_llm=False)
            record_plan_decision(run_dir, decision="approve")
            apply_patch_edits(run_dir, edits_file=_write_failing_edit_proposal(run_dir))
            failed = run_code_task_benchmark(run_dir, timeout_sec=10)
            self.assertEqual(failed.status, "failed")

            analysis = analyze_code_task_failure(run_dir)

            self.assertEqual(analysis.status, "needs_repair")
            analysis_text = read_text(analysis.analysis_path)
            self.assertIn("# Failure Analysis", analysis_text)
            self.assertIn("AssertionError", analysis_text)

            repair = propose_repair_edits(run_dir, use_llm=False)
            self.assertEqual(repair.mode, "offline")
            self.assertEqual(repair.edit_count, 0)
            self.assertTrue((repair.repair_dir / "proposed_edits.json").is_file())
            manifest = read_json(run_dir / "manifest.json")
            self.assertEqual(manifest["repair"]["status"], "repair_proposed")

    def test_code_task_validate_run_and_failure_cli(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "toy_project"
            task_file = root / "task.md"
            output_root = root / "runs"
            _write_toy_project(code_root)
            write_text(task_file, "# Task\n\nRun CLI validation and tests.\n")
            with contextlib.redirect_stdout(io.StringIO()):
                main(
                    [
                        "code-task",
                        "init",
                        "--code-root",
                        str(code_root),
                        "--task-file",
                        str(task_file),
                        "--output-root",
                        str(output_root),
                        "--benchmark-command",
                        "python -m unittest discover -s tests",
                    ]
                )
            run_dir = next(output_root.iterdir())

            validate_stdout = io.StringIO()
            with contextlib.redirect_stdout(validate_stdout):
                main(["code-task", "validate", str(run_dir)])
            self.assertIn("Status: passed", validate_stdout.getvalue())

            run_stdout = io.StringIO()
            with contextlib.redirect_stdout(run_stdout):
                main(["code-task", "run", str(run_dir), "--timeout", "10"])
            self.assertIn("Status: passed", run_stdout.getvalue())
            self.assertTrue((run_dir / "code_task" / "run" / "patched" / "execution_report.json").is_file())

            status_stdout = io.StringIO()
            with contextlib.redirect_stdout(status_stdout):
                main(["status", str(run_dir)])
            self.assertIn("Validation:", status_stdout.getvalue())
            self.assertIn("last status: passed", status_stdout.getvalue())


def _write_toy_project(code_root: Path) -> None:
    write_text(
        code_root / "spam_model.py",
        (
            "import math\n\n\n"
            "class SpamModel:\n"
            "    def score(self, text):\n"
            "        return math.log(len(text) + 1)\n\n\n"
            "def predict(text):\n"
            "    return 'spam' if 'win' in text.lower() else 'ham'\n\n\n"
            "if __name__ == \"__main__\":\n"
            "    print(predict('win a prize'))\n"
        ),
    )
    write_text(
        code_root / "tests" / "test_spam_model.py",
        (
            "import unittest\n\n"
            "from spam_model import predict\n\n\n"
            "class SpamModelTests(unittest.TestCase):\n"
            "    def test_predicts_spam_keyword(self):\n"
            "        self.assertEqual(predict('win now'), 'spam')\n"
        ),
    )
    write_text(code_root / ".git" / "config", "[core]\nrepositoryformatversion = 0\n")
    write_text(code_root / ".env", "TOKEN=secret\n")
    write_text(
        code_root / "pyproject.toml",
        "[project]\nname = \"toy-project\"\nversion = \"0.1.0\"\n",
    )


def _write_metric_project(
    code_root: Path,
    *,
    value: str,
    metric_name: str = "accuracy",
) -> None:
    write_text(code_root / "metric_value.txt", value + "\n")
    write_text(
        code_root / "benchmark.py",
        (
            "from pathlib import Path\n\n"
            "value = float(Path('metric_value.txt').read_text().strip())\n"
            f"print(f'{metric_name}: {{value:.6f}}')\n"
            "print('train_time_sec: 0.010000')\n"
        ),
    )


def _write_valid_edit_proposal(run_dir: Path, path: Path | None = None) -> Path:
    proposal_path = path or run_dir / "code_task" / "meta" / "proposed_edits.json"
    write_json(
        proposal_path,
        {
            "schema_version": 1,
            "edits": [
                {
                    "path": "spam_model.py",
                    "old": (
                        "def predict(text):\n"
                        "    return 'spam' if 'win' in text.lower() else 'ham'\n"
                    ),
                    "new": (
                        "def predict(text):\n"
                        "    lowered = text.lower()\n"
                        "    return 'spam' if any(keyword in lowered for keyword in ('win', 'prize')) else 'ham'\n"
                    ),
                    "reason": "Extend the keyword baseline while preserving the public API.",
                }
            ],
        },
    )
    return proposal_path


def _write_failing_edit_proposal(run_dir: Path) -> Path:
    proposal_path = run_dir / "code_task" / "meta" / "proposed_edits.json"
    write_json(
        proposal_path,
        {
            "schema_version": 1,
            "edits": [
                {
                    "path": "spam_model.py",
                    "old": (
                        "def predict(text):\n"
                        "    return 'spam' if 'win' in text.lower() else 'ham'\n"
                    ),
                    "new": (
                        "def predict(text):\n"
                        "    return 'ham'\n"
                    ),
                    "reason": "Deliberately break the classifier for failure-analysis coverage.",
                }
            ],
        },
    )
    return proposal_path


def _indexed_file(index: dict[str, object], path: str) -> dict[str, object]:
    files = index.get("files", [])
    if isinstance(files, list):
        for item in files:
            if isinstance(item, dict) and item.get("path") == path:
                return item
    raise AssertionError(f"Missing indexed file: {path}")


if __name__ == "__main__":
    unittest.main()
