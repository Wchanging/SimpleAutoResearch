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
from simple_ar.code_task.analysis.entrypoints import analyze_entrypoint_debuggability
from simple_ar.code_task.analysis.resource_static import analyze_resource_risks
from simple_ar.code_task.generation.common import safe_relative_path, string_list
from simple_ar.code_task.generation.compat_patches import apply_generated_project_compatibility_patch
from simple_ar.code_task.generation.review import review_generated_project
from simple_ar.code_task.generation.writer import write_generated_project
from simple_ar.code_task import initialize_code_task, review_code_task_changes
from simple_ar.core.artifacts import read_json, write_json, write_text


class CodeTaskInterfaceTests(unittest.TestCase):
    def test_generation_common_helpers_normalize_paths_and_lists(self) -> None:
        self.assertEqual(safe_relative_path("pkg\\runner.py"), "pkg/runner.py")
        self.assertEqual(safe_relative_path("../escape.py"), "")
        self.assertEqual(safe_relative_path("/absolute.py"), "absolute.py")
        self.assertEqual(string_list([" a ", "", 3], limit=2), ["a", "3"])

    def test_generated_project_compat_patch_isolated_from_repair_flow(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            write_json(project / "config.json", {"base": {"max_items": 20}, "presets": {}})

            result = apply_generated_project_compatibility_patch(
                project_dir=project,
                stderr_text="ERROR: Unknown preset 'standard'",
            )
            payload = read_json(project / "config.json")

            self.assertTrue(result.applied)
            self.assertEqual(result.patch_id, "missing_greenfield_preset")
            self.assertIn("standard", payload["presets"])
            self.assertEqual(result.changed_files, ("config.json",))

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

    def test_resource_static_flags_fit_inside_nested_loops(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            write_text(
                project / "search.py",
                (
                    "def run(model, datasets, candidates):\n"
                    "    for dataset in datasets:\n"
                    "        for candidate in candidates:\n"
                    "            model.fit(dataset.X, dataset.y)\n"
                    "    return model\n"
                ),
            )

            analysis = analyze_resource_risks(project)

            self.assertGreaterEqual(analysis["nested_fit_call_count"], 1)
            self.assertGreaterEqual(analysis["max_fit_loop_depth"], 2)
            self.assertEqual(analysis["files"][0]["path"], "search.py")

    def test_writer_treats_runtime_output_paths_as_placeholders(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "generated_project"
            artifacts = write_generated_project(
                project_dir=project,
                architecture_plan={
                    "files": [
                        {"path": "main.py", "kind": "source", "entrypoint": True},
                        {"path": "artifacts", "purpose": "Runtime output directory."},
                        {"path": "artifacts/results.json", "purpose": "Runtime evidence bundle."},
                        {"path": "submission/results", "purpose": "Submission metrics directory."},
                        {"path": "submission/results/metrics.json", "purpose": "Runtime metric mirror."},
                    ]
                },
                result_schema={"required_metrics": []},
                contract={},
                memory={},
                client=None,
                allow_fallback=True,
            )

            by_path = {row["path"]: row for row in artifacts["generated_files"]}
            self.assertTrue((project / "artifacts").is_dir())
            self.assertTrue((project / "artifacts" / "results.json").is_file())
            self.assertTrue((project / "submission" / "results").is_dir())
            self.assertEqual(by_path["artifacts"]["mode"], "deterministic_runtime_placeholder")
            self.assertEqual(by_path["artifacts/results.json"]["line_count"], 0)

    def test_review_accepts_runtime_placeholders_as_runtime_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            write_text(project / "main.py", "def main():\n    print('score: 1.0')\n")
            (project / "artifacts").mkdir()
            write_text(project / "artifacts" / "results.json", "{}\n")

            review = review_generated_project(
                project_dir=project,
                code_artifacts={
                    "generated_files": [
                        {"path": "main.py", "kind": "source", "mode": "llm", "line_count": 2},
                        {"path": "artifacts", "kind": "runtime_dir", "mode": "deterministic_runtime_placeholder", "line_count": 0},
                        {
                            "path": "artifacts/results.json",
                            "kind": "output_placeholder",
                            "mode": "deterministic_runtime_placeholder",
                            "line_count": 0,
                        },
                    ]
                },
                result_schema={"primary_metric": "score", "required_metrics": ["score"]},
                resource_plan={"max_files": 8, "max_generated_lines": 200},
                use_llm=False,
            )

            self.assertNotIn("missing_file", {row["category"] for row in review["findings"]})

    def test_entrypoint_static_analysis_flags_suppressed_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            write_text(
                project / "main.py",
                (
                    "import sys\n\n"
                    "def main():\n"
                    "    try:\n"
                    "        raise RuntimeError('hidden')\n"
                    "    except Exception as exc:\n"
                    "        print(f'ERROR: {exc}', file=sys.stderr)\n"
                    "        return 1\n"
                ),
            )

            analysis = analyze_entrypoint_debuggability(project)

            self.assertEqual(len(analysis["findings"]), 1)
            self.assertEqual(analysis["findings"][0]["path"], "main.py")

    def test_review_blocks_entrypoint_that_suppresses_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            write_text(
                project / "main.py",
                (
                    "def main():\n"
                    "    try:\n"
                    "        print('score: 1.0')\n"
                    "    except Exception as exc:\n"
                    "        print(f'ERROR: {exc}')\n"
                    "        return 1\n"
                ),
            )

            review = review_generated_project(
                project_dir=project,
                code_artifacts={"generated_files": [{"path": "main.py", "mode": "llm", "line_count": 7}]},
                result_schema={"primary_metric": "score", "required_metrics": ["score"]},
                resource_plan={"max_files": 4, "max_generated_lines": 200},
                use_llm=False,
            )

            self.assertEqual(review["status"], "failed")
            self.assertTrue(
                any(row["category"] == "entrypoint_exception_suppresses_traceback" for row in review["findings"])
            )

    def test_review_accepts_entrypoint_that_preserves_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            write_text(
                project / "main.py",
                (
                    "import traceback\n\n"
                    "def main():\n"
                    "    try:\n"
                    "        print('score: 1.0')\n"
                    "    except Exception:\n"
                    "        traceback.print_exc()\n"
                    "        return 1\n"
                ),
            )

            review = review_generated_project(
                project_dir=project,
                code_artifacts={"generated_files": [{"path": "main.py", "mode": "llm", "line_count": 8}]},
                result_schema={"primary_metric": "score", "required_metrics": ["score"]},
                resource_plan={"max_files": 4, "max_generated_lines": 200},
                use_llm=False,
            )

            self.assertFalse(
                any(row["category"] == "entrypoint_exception_suppresses_traceback" for row in review["findings"])
            )

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

    def test_review_warns_when_planned_public_api_is_not_exported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            write_text(project / "main.py", "def main():\n    print('score: 1.0')\n")
            write_text(project / "pkg" / "__init__.py", "")
            write_text(project / "pkg" / "data.py", "def load_rows():\n    return []\n")

            review = review_generated_project(
                project_dir=project,
                code_artifacts={
                    "generated_files": [
                        {"path": "main.py", "mode": "llm", "line_count": 2},
                        {"path": "pkg/__init__.py", "mode": "llm", "line_count": 1},
                        {"path": "pkg/data.py", "mode": "llm", "line_count": 2},
                    ]
                },
                architecture_plan={
                    "files": [
                        {"path": "main.py", "public_api": ["main(argv=None)"]},
                        {"path": "pkg/data.py", "public_api": ["load_dataset(config)"]},
                    ]
                },
                result_schema={"primary_metric": "score", "required_metrics": ["score"]},
                resource_plan={"max_files": 8, "max_generated_lines": 200},
                use_llm=False,
            )

            self.assertTrue(any(row["category"] == "planned_api_not_exported" for row in review["findings"]))
            self.assertTrue(all(row["severity"] != "blocking" for row in review["findings"] if row["category"] == "planned_api_not_exported"))

    def test_review_does_not_block_defensive_placeholder_policy_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            write_text(
                project / "main.py",
                (
                    "def main():\n"
                    "    # Refusing to emit placeholder metrics keeps failed benchmark paths honest.\n"
                    "    print('accuracy: 0.91')\n"
                ),
            )

            review = review_generated_project(
                project_dir=project,
                code_artifacts={"generated_files": [{"path": "main.py", "mode": "llm", "line_count": 3}]},
                result_schema={"primary_metric": "accuracy", "required_metrics": ["accuracy"]},
                resource_plan={"max_files": 4, "max_generated_lines": 200},
                contract={"objective": "Run an experiment and evaluate metrics without placeholder values."},
                use_llm=False,
            )

            self.assertFalse(any(row["category"] == "placeholder_execution_path" for row in review["findings"]))

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

    def test_review_blocks_missing_explicit_greenfield_task_surface(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            write_text(project / "main.py", "def main():\n    print('best_score: 1.0')\n")
            write_text(project / "generated_experiment" / "__init__.py", "")
            write_text(
                project / "generated_experiment" / "runner.py",
                "def run_experiment():\n    return {'best_score': 1.0, 'task_count': 1.0}\n",
            )

            review = review_generated_project(
                project_dir=project,
                code_artifacts={
                    "generated_files": [
                        {"path": "main.py", "mode": "llm", "line_count": 2},
                        {"path": "generated_experiment/__init__.py", "mode": "llm", "line_count": 1},
                        {"path": "generated_experiment/runner.py", "mode": "llm", "line_count": 2},
                    ]
                },
                result_schema={"primary_metric": "best_score", "required_metrics": ["best_score", "task_count"]},
                resource_plan={"max_files": 16, "max_generated_lines": 2000},
                contract={
                    "objective": "Greenfield analysis suite",
                    "task": (
                        "Create README.md, self-check, prefer sample-lib/sample_lib when available, "
                        "at least two tasks, artifacts/results.json, artifacts/report.md, "
                        "and artifacts/condition_results.jsonl."
                    ),
                },
                dependency_advice={
                    "packages": [
                        {
                            "package": "sample-lib",
                            "import_name": "sample_lib",
                            "status": "installed",
                            "matched_terms": ["sample-lib", "sample_lib"],
                        }
                    ]
                },
                use_llm=False,
            )

            categories = {row["category"] for row in review["findings"]}
            self.assertEqual(review["status"], "failed")
            self.assertIn("missing_required_artifact", categories)
            self.assertIn("missing_artifact_writer", categories)
            self.assertIn("missing_cli_mode", categories)
            self.assertIn("missing_requested_dependency_path", categories)
            self.assertIn("insufficient_task_count", categories)

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
