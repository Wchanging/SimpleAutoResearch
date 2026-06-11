from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from simple_ar.core.artifacts import read_json, read_text, write_json
from simple_ar.code_task import (
    apply_patch_edits,
    execute_code_task,
    generate_patch_plan,
    initialize_code_task,
    probe_code_task_environment,
    record_plan_decision,
    run_code_task_baseline,
    run_code_task_benchmark,
    validate_code_task,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
TEST_ROOT = REPO_ROOT / ".tmp_tests"
EXAMPLE_ROOT = REPO_ROOT / "tests" / "fixtures" / "code_tasks" / "toy_spam_project"
TASK_FILE = REPO_ROOT / "tests" / "fixtures" / "code_tasks" / "improve_toy_spam_baseline.md"
TINY_DIGITS_ROOT = REPO_ROOT / "examples" / "full_pipeline_tiny_mlp" / "project"
TINY_DIGITS_TASK_FILE = (
    REPO_ROOT / "examples" / "full_pipeline_tiny_mlp" / "task.md"
)
MEDIUM_REVIEW_ROOT = REPO_ROOT / "examples" / "code_task_medium_review" / "project"
MEDIUM_REVIEW_TASK_FILE = (
    REPO_ROOT / "examples" / "code_task_medium_review" / "task.md"
)


class CodeTaskExampleTests(unittest.TestCase):
    def test_toy_spam_example_fails_then_passes_after_patch(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            run_dir = Path(tmp) / "runs" / "toy-spam-code-task"
            initialize_code_task(
                run_dir=run_dir,
                code_root=EXAMPLE_ROOT,
                task_file=TASK_FILE,
                benchmark_command="python -m unittest discover -s tests",
            )

            initial = run_code_task_baseline(run_dir, timeout_sec=10)
            self.assertEqual(initial.status, "failed")
            self.assertIn("AssertionError", read_text(initial.stderr_path))
            self.assertTrue((run_dir / "code_task" / "run" / "baseline" / "execution_report.json").is_file())

            plan = generate_patch_plan(run_dir, use_llm=False)
            self.assertIn("spamfilter/rules.py", plan.selected_files)
            record_plan_decision(run_dir, decision="approve", note="Example patch is small.")
            edits_path = _write_keyword_patch(run_dir)
            apply_patch_edits(run_dir, edits_file=edits_path)

            validation = validate_code_task(run_dir)
            self.assertEqual(validation.status, "passed")
            final = run_code_task_benchmark(run_dir, timeout_sec=10)

            self.assertEqual(final.status, "passed")
            self.assertEqual(final.returncode, 0)
            self.assertTrue((run_dir / "code_task" / "run" / "patched" / "execution_report.json").is_file())
            summary_path = run_dir / "code_task" / "summary.md"
            self.assertTrue(summary_path.is_file())
            summary_text = read_text(summary_path)
            self.assertIn("# Code Task Summary", summary_text)
            self.assertIn("Status: `objective_improved`", summary_text)
            self.assertIn("spamfilter/rules.py", summary_text)
            workspace_rules = run_dir / "code_task" / "workspace" / "spamfilter" / "rules.py"
            self.assertIn('"lottery"', read_text(workspace_rules))
            self.assertNotIn('"lottery"', read_text(EXAMPLE_ROOT / "spamfilter" / "rules.py"))
            manifest = read_json(run_dir / "manifest.json")
            self.assertEqual(manifest["status"], "objective_improved")
            self.assertEqual(manifest["benchmark"]["last_status"], "passed")
            self.assertEqual(manifest["benchmark"]["runs"]["baseline"]["status"], "failed")
            self.assertEqual(manifest["benchmark"]["runs"]["patched"]["status"], "passed")
            self.assertEqual(manifest["layout"]["summary"], "code_task/summary.md")

    def test_tiny_digits_mlp_example_records_lightweight_ml_metrics(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            run_dir = Path(tmp) / "runs" / "tiny-digits-mlp-code-task"
            initialize_code_task(
                run_dir=run_dir,
                code_root=TINY_DIGITS_ROOT,
                task_file=TINY_DIGITS_TASK_FILE,
                benchmark_command="python benchmark.py",
            )

            probe = probe_code_task_environment(run_dir)
            self.assertIn(probe.status, {"ok", "warning"})
            validation = validate_code_task(run_dir)
            self.assertEqual(validation.status, "passed")
            baseline = run_code_task_baseline(run_dir, timeout_sec=30)

            self.assertEqual(baseline.status, "passed")
            self.assertIn("accuracy", baseline.metrics)
            self.assertIn("macro_f1", baseline.metrics)
            self.assertGreaterEqual(baseline.metrics["accuracy"], 0.70)
            self.assertLess(baseline.metrics["accuracy"], 0.90)
            self.assertLess(baseline.metrics["train_time_sec"], 5.0)
            report = read_json(baseline.report_path)
            self.assertEqual(report["environment"]["mode"], "current")
            self.assertEqual(report["command_text"], "python benchmark.py")
            summary = read_text(run_dir / "code_task" / "summary.md")
            self.assertIn("### Baseline", summary)
            self.assertIn("accuracy", summary)
            self.assertFalse((run_dir / "code_task" / "run" / "comparison.json").exists())

    def test_medium_review_pipeline_example_has_visible_progress_and_metrics(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            run_dir = Path(tmp) / "runs" / "medium-review-pipeline-code-task"
            initialize_code_task(
                run_dir=run_dir,
                code_root=MEDIUM_REVIEW_ROOT,
                task_file=MEDIUM_REVIEW_TASK_FILE,
                benchmark_command="python main.py --config configs/experiment.json --show-progress",
            )

            messages: list[str] = []
            result = execute_code_task(
                run_dir,
                to_step="plan",
                use_llm=False,
                timeout_sec=30,
                stream_benchmark_output=True,
                message_callback=messages.append,
            )

            self.assertEqual(result.stop_reason, "approval_required")
            self.assertTrue(any("benchmark stdout: round 1/4" in item for item in messages))
            baseline_metrics = read_json(run_dir / "code_task" / "run" / "baseline" / "metrics.json")
            self.assertIn("accuracy", baseline_metrics)
            self.assertIn("macro_f1", baseline_metrics)
            self.assertGreaterEqual(baseline_metrics["accuracy"], 0.60)
            self.assertLess(baseline_metrics["accuracy"], 0.95)
            stdout = read_text(run_dir / "code_task" / "run" / "baseline" / "stdout.txt")
            self.assertIn("round 1/4", stdout)
            self.assertIn("features=word", stdout)
            report = read_json(run_dir / "code_task" / "run" / "baseline" / "execution_report.json")
            self.assertEqual(
                report["command_text"],
                "python main.py --config configs/experiment.json --show-progress",
            )
            index = read_json(run_dir / "code_task" / "meta" / "codebase_index.json")
            indexed_paths = {
                item.get("path")
                for item in index.get("files", [])
                if isinstance(item, dict)
            }
            self.assertIn("main.py", indexed_paths)
            self.assertIn("configs/experiment.json", indexed_paths)

    def test_auto_stream_mode_handles_carriage_return_progress(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            project = root / "progress_project"
            project.mkdir()
            (project / "progress_benchmark.py").write_text(
                "\n".join(
                    [
                        "import sys",
                        "for step in range(1, 4):",
                        "    sys.stdout.write(f'progress {step}/3\\r')",
                        "    sys.stdout.flush()",
                        "print('progress 3/3')",
                        "print('accuracy: 1.0')",
                    ]
                ),
                encoding="utf-8",
            )
            task_file = root / "task.md"
            task_file.write_text("Keep the progress benchmark working.\n", encoding="utf-8")
            run_dir = root / "runs" / "progress-code-task"
            initialize_code_task(
                run_dir=run_dir,
                code_root=project,
                task_file=task_file,
                benchmark_command="python progress_benchmark.py",
            )

            messages: list[str] = []
            result = execute_code_task(
                run_dir,
                to_step="baseline",
                use_llm=False,
                timeout_sec=10,
                stream_benchmark_output="auto",
                message_callback=messages.append,
            )

            self.assertEqual(result.stop_reason, "stop_point")
            self.assertTrue(any("benchmark stdout: progress 3/3" in item for item in messages))
            self.assertTrue(any("benchmark stdout: accuracy: 1.0" in item for item in messages))
            stdout = read_text(run_dir / "code_task" / "run" / "baseline" / "stdout.txt")
            self.assertIn("progress 1/3", stdout)
            self.assertIn("progress 3/3", stdout)
            metrics = read_json(run_dir / "code_task" / "run" / "baseline" / "metrics.json")
            self.assertEqual(metrics["accuracy"], 1.0)


def _write_keyword_patch(run_dir: Path) -> Path:
    path = run_dir / "manual_keyword_patch.json"
    write_json(
        path,
        {
            "schema_version": 1,
            "edits": [
                {
                    "path": "spamfilter/rules.py",
                    "old": (
                        "SPAM_KEYWORDS = {\n"
                        "    \"free\",\n"
                        "    \"winner\",\n"
                        "    \"win\",\n"
                        "}\n"
                    ),
                    "new": (
                        "SPAM_KEYWORDS = {\n"
                        "    \"free\",\n"
                        "    \"lottery\",\n"
                        "    \"prize\",\n"
                        "    \"urgent\",\n"
                        "    \"winner\",\n"
                        "    \"win\",\n"
                        "}\n"
                    ),
                    "reason": "Add common lottery/prize spam markers while keeping the public API unchanged.",
                }
            ],
        },
    )
    return path


if __name__ == "__main__":
    unittest.main()
