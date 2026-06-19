from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from simple_ar.code_task.analysis.interfaces import (
    dependency_context,
    find_local_api_mismatches,
    order_file_specs,
    snippet_api_contract,
)
from simple_ar.code_task.generation.review import review_generated_project
from simple_ar.code_task import initialize_code_task, review_code_task_changes
from simple_ar.core.artifacts import read_json, write_json, write_text


class CodeTaskInterfaceTests(unittest.TestCase):
    def test_file_specs_are_ordered_dependencies_first(self) -> None:
        files = [
            {"path": "main.py", "dependencies": ["pkg/runner.py"]},
            {"path": "pkg/runner.py", "dependencies": ["pkg/data.py"]},
            {"path": "pkg/data.py", "dependencies": []},
        ]

        ordered = order_file_specs(files)

        self.assertEqual([row["path"] for row in ordered], ["pkg/data.py", "pkg/runner.py", "main.py"])

    def test_dependency_context_exposes_actual_generated_api(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            write_text(
                project / "pkg" / "data.py",
                "def load_text_classification_splits(config_path=None):\n    return [], {}\n",
            )

            context = dependency_context(
                project,
                {"path": "pkg/runner.py", "dependencies": ["pkg/data.py"]},
            )

            dependency = context["dependencies"][0]
            self.assertTrue(dependency["available"])
            self.assertIn("def load_text_classification_splits(config_path=None)", dependency["public_api"])
            self.assertNotIn("load_text_classification_dataset", str(context))

    def test_existing_context_snippets_expose_api_without_extra_repo_reads(self) -> None:
        contract = snippet_api_contract(
            [
                {
                    "path": "service.py",
                    "text": "class Service:\n    def run(self, value: int) -> str:\n        return str(value)\n",
                }
            ]
        )

        self.assertIn("class Service", contract["service.py"][0])
        self.assertTrue(any("Service.def run" in row for row in contract["service.py"]))

    def test_review_blocks_cross_file_api_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            write_text(project / "main.py", "from pkg.runner import run_experiment\n")
            write_text(project / "pkg" / "__init__.py", "")
            write_text(
                project / "pkg" / "data.py",
                "def load_text_classification_splits(config_path=None):\n    return [], {}\n",
            )
            write_text(
                project / "pkg" / "runner.py",
                (
                    "from . import data as data_module\n\n"
                    "def run_experiment():\n"
                    "    return data_module.load_text_classification_dataset()\n"
                ),
            )
            artifacts = {
                "generated_files": [
                    {"path": "main.py", "mode": "llm", "line_count": 1},
                    {"path": "pkg/__init__.py", "mode": "llm", "line_count": 1},
                    {"path": "pkg/data.py", "mode": "llm", "line_count": 2},
                    {"path": "pkg/runner.py", "mode": "llm", "line_count": 4},
                ]
            }

            mismatches = find_local_api_mismatches(project)
            review = review_generated_project(
                project_dir=project,
                code_artifacts=artifacts,
                result_schema={"primary_metric": "score", "required_metrics": ["score"]},
                resource_plan={"max_files": 8, "max_generated_lines": 200},
                use_llm=False,
            )

            self.assertEqual(mismatches[0]["missing_symbol"], "load_text_classification_dataset")
            self.assertEqual(review["status"], "failed")
            self.assertTrue(any(row["category"] == "missing_local_api" for row in review["findings"]))

    def test_review_blocks_mixed_llm_and_core_fallback_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            write_text(project / "main.py", "def main():\n    return 0\n")
            write_text(project / "pkg" / "models.py", "# Reserved generated module.\n")
            review = review_generated_project(
                project_dir=project,
                code_artifacts={
                    "generated_files": [
                        {"path": "main.py", "mode": "llm", "line_count": 2},
                        {"path": "pkg/models.py", "mode": "fallback", "line_count": 1},
                    ]
                },
                result_schema={},
                resource_plan={"max_files": 8, "max_generated_lines": 200},
                use_llm=False,
            )

            self.assertEqual(review["status"], "failed")
            self.assertTrue(any(row["category"] == "mixed_generation_fallback" for row in review["findings"]))

    def test_existing_project_review_blocks_changed_local_api_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            task = root / "task.md"
            write_text(source / "api.py", "def available():\n    return 1\n")
            write_text(source / "caller.py", "import api as api_module\n\ndef run():\n    return api_module.available()\n")
            write_text(task, "# Task\n\nUpdate the caller without breaking local interfaces.\n")
            run_dir = root / "run"
            initialized = initialize_code_task(
                run_dir=run_dir,
                code_root=source,
                task_file=task,
                benchmark_command="python caller.py",
            )
            write_text(
                initialized.workspace_dir / "caller.py",
                "import api as api_module\n\ndef run():\n    return api_module.missing()\n",
            )
            manifest = read_json(run_dir / "manifest.json")
            manifest["patch"] = {"status": "applied", "changed_files": ["caller.py"]}
            write_json(run_dir / "manifest.json", manifest)

            result = review_code_task_changes(run_dir, use_llm=False)
            report = read_json(result.report_path)

            self.assertEqual(result.status, "failed")
            self.assertTrue(any(row["category"] == "interface_compatibility" for row in report["findings"]))


if __name__ == "__main__":
    unittest.main()
