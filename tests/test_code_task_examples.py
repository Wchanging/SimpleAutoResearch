from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from simple_ar.artifacts import read_json, read_text, write_json
from simple_ar.code_task import (
    apply_patch_edits,
    generate_patch_plan,
    initialize_code_task,
    record_plan_decision,
    run_code_task_benchmark,
    validate_code_task,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
TEST_ROOT = REPO_ROOT / ".tmp_tests"
EXAMPLE_ROOT = REPO_ROOT / "examples" / "code_tasks" / "toy_spam_project"
TASK_FILE = REPO_ROOT / "examples" / "code_tasks" / "tasks" / "improve_toy_spam_baseline.md"


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

            initial = run_code_task_benchmark(run_dir, timeout_sec=10)
            self.assertEqual(initial.status, "failed")
            self.assertIn("AssertionError", read_text(initial.stderr_path))

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
            workspace_rules = run_dir / "code_task" / "workspace" / "spamfilter" / "rules.py"
            self.assertIn('"lottery"', read_text(workspace_rules))
            self.assertNotIn('"lottery"', read_text(EXAMPLE_ROOT / "spamfilter" / "rules.py"))
            manifest = read_json(run_dir / "manifest.json")
            self.assertEqual(manifest["status"], "benchmark_passed")
            self.assertEqual(manifest["benchmark"]["last_status"], "passed")


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
