from __future__ import annotations

import contextlib
import io
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from simple_ar.core.artifacts import read_json, read_jsonl, read_text, write_json, write_text
from simple_ar.cli import main
from simple_ar.code_task import (
    PatchValidationError,
    analyze_code_task_failure,
    apply_patch_edits,
    build_code_task_context_pack,
    build_code_task_repo_map,
    create_code_task_batch,
    execute_code_task,
    generate_code_task_work_plan,
    generate_patch_plan,
    initialize_code_task,
    locate_code_task_context,
    probe_code_task_environment,
    propose_patch_edits,
    propose_repair_edits,
    record_plan_decision,
    run_code_task_baseline,
    run_code_task_benchmark,
    validate_code_task,
)
from simple_ar.code_task.runtime.config import CodeTaskConfigError, load_code_task_init_options
from simple_ar.code_task.generation.dependencies import DEPENDENCY_CATALOG, build_dependency_advice
from simple_ar.code_task.generation.generated_project_repair import (
    repair_generated_project_from_review,
    repair_generated_project_from_run_failure,
)
from simple_ar.code_task.orchestration.execute import _apply_greenfield_review_repair_metadata
from simple_ar.experiment.code_task_bridge import (
    CodeTaskExperimentSpec,
    prepare_code_task_experiment,
    write_code_task_experiment_meta,
)
from simple_ar.integrations.llm import LLMError


TEST_ROOT = Path(__file__).resolve().parents[1] / ".tmp_tests"


class CodeTaskTests(unittest.TestCase):
    def test_dependency_advice_scans_environment_beyond_static_hints(self) -> None:
        base = build_dependency_advice("Use pydantic if available for schema validation.")
        self.assertEqual(base["selection_policy"], "dynamic_environment_scan_plus_semantic_hints")
        self.assertGreater(base["environment_package_count"], 0)
        self.assertTrue(base["environment_packages"])
        self.assertTrue(
            any(
                row.get("package", "").lower() == "pydantic" and row.get("status") == "installed"
                for row in base["packages"]
            )
        )

        hinted = {candidate.package.lower() for candidate in DEPENDENCY_CATALOG}
        dynamic_package = next(
            (
                str(row["package"])
                for row in base["environment_packages"]
                if row.get("package") and str(row["package"]).lower() not in hinted
            ),
            "",
        )
        if dynamic_package:
            dynamic = build_dependency_advice(f"Use {dynamic_package} if available for this task.")
            self.assertTrue(
                any(
                    str(row.get("package", "")).lower() == dynamic_package.lower()
                    and row.get("status") == "installed"
                    for row in dynamic["packages"]
                )
            )

    def test_greenfield_review_repair_regenerates_fallback_core_file(self) -> None:
        class FakeClient:
            def ask_json(self, _system: str, _prompt: str, *, label: str = "") -> dict[str, str]:
                self.label = label
                return {
                    "content": (
                        "from __future__ import annotations\n\n"
                        "def run_experiment() -> dict[str, float]:\n"
                        "    return {'accuracy': 1.0}\n"
                    ),
                    "summary": "Regenerated runner with real metric path.",
                }

        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            project = root / "generated_project"
            runner = project / "generated_experiment" / "runner.py"
            runner.parent.mkdir(parents=True)
            write_text(runner, "from __future__ import annotations\n\n# Reserved generated module.\n")
            review = {
                "status": "failed",
                "findings": [
                    {
                        "category": "mixed_generation_fallback",
                        "summary": "Core file `generated_experiment/runner.py` fell back while related files were LLM-generated.",
                    }
                ],
            }
            artifacts = {
                "generated_files": [
                    {"path": "generated_experiment/runner.py", "mode": "fallback", "line_count": 3}
                ]
            }
            architecture = {
                "files": [
                    {
                        "path": "generated_experiment/runner.py",
                        "purpose": "Single authoritative orchestrator.",
                        "dependencies": [],
                        "public_api": ["run_experiment() -> dict[str, float]"],
                    }
                ]
            }

            result = repair_generated_project_from_review(
                project_dir=project,
                review_report=review,
                output_path=root / "review_repair.json",
                code_artifacts=artifacts,
                architecture_plan=architecture,
                result_schema={"required_metrics": ["accuracy"]},
                contract={"objective": "Generate a runnable metric project."},
                client=FakeClient(),  # type: ignore[arg-type]
            )

            self.assertEqual(result["status"], "patched")
            self.assertEqual(result["regenerated_files"][0]["path"], "generated_experiment/runner.py")
            self.assertIn("return {'accuracy': 1.0}", read_text(runner))

    def test_greenfield_review_repair_metadata_syncs_partial_progress(self) -> None:
        artifacts = {
            "generated_files": [
                {"path": "generated_experiment/processing.py", "mode": "fallback", "line_count": 3},
                {"path": "README.md", "mode": "fallback", "line_count": 1},
                {"path": "generated_experiment/runner.py", "mode": "fallback", "line_count": 3},
            ]
        }
        repair = {
            "status": "failed",
            "changed_files": ["generated_experiment/processing.py", "README.md"],
            "unresolved_errors": ["generated_experiment/runner.py: provider error"],
            "regenerated_files": [
                {
                    "path": "generated_experiment/processing.py",
                    "mode": "llm_review_repair",
                    "line_count": 42,
                    "summary": "Repaired processor.",
                    "public_api": ["def build_processor(config)"],
                }
            ],
        }

        _apply_greenfield_review_repair_metadata(artifacts, repair)

        rows = {row["path"]: row for row in artifacts["generated_files"]}
        self.assertEqual(rows["generated_experiment/processing.py"]["mode"], "llm_review_repair")
        self.assertEqual(rows["generated_experiment/processing.py"]["line_count"], 42)
        self.assertEqual(rows["README.md"]["mode"], "deterministic_review_repair")
        self.assertEqual(rows["generated_experiment/runner.py"]["mode"], "fallback")

    def test_greenfield_review_repair_fills_generic_resources_without_llm(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            project = root / "generated_project"
            package = project / "generated_experiment"
            package.mkdir(parents=True)
            resources = package / "resources.py"
            write_text(resources, "from __future__ import annotations\n\n# Reserved generated module.\n")
            review = {
                "status": "failed",
                "findings": [
                    {
                        "severity": "blocking",
                        "category": "mixed_generation_fallback",
                        "summary": "Core file `generated_experiment/resources.py` fell back.",
                    }
                ],
            }
            artifacts = {
                "generated_files": [
                    {"path": "generated_experiment/resources.py", "mode": "fallback", "line_count": 3}
                ]
            }

            repair = repair_generated_project_from_review(
                project_dir=project,
                review_report=review,
                output_path=root / "review_repair.json",
                code_artifacts=artifacts,
                client=None,
            )

            self.assertEqual(repair["status"], "patched")
            self.assertIn("generated_experiment/resources.py", repair["changed_files"])
            content = read_text(resources)
            self.assertIn("class ResourceInfo", content)
            self.assertIn("def detect_resources", content)
            self.assertIn("def select_profile", content)

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
            self.assertEqual(manifest["workspace"]["mode"], "copy")
            self.assertEqual(manifest["workspace"]["workspace_dir"], "code_task/workspace")
            self.assertEqual(manifest["edit_scope"]["mode"], "source_only_default")
            self.assertIn("tests/**", manifest["edit_scope"]["protected_patterns"])
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

            self.assertTrue(result.repo_map_path.is_file())
            self.assertTrue(result.repo_map_summary_path.is_file())
            repo_map = read_json(result.repo_map_path)
            self.assertEqual(repo_map["schema_version"], 1)
            self.assertEqual(repo_map["project"]["file_count"], 3)
            self.assertEqual(repo_map["project"]["python_file_count"], 2)
            self.assertEqual(repo_map["project"]["test_file_count"], 1)
            self.assertGreaterEqual(repo_map["project"]["symbol_count"], 4)
            mapped_files = {item["path"]: item for item in repo_map["files"]}
            self.assertEqual(mapped_files["spam_model.py"]["access_role"], "editable")
            self.assertEqual(
                mapped_files["tests/test_spam_model.py"]["access_role"],
                "read_only_evidence",
            )
            symbols = {item["qualified_name"] for item in repo_map["symbols"]}
            self.assertIn("SpamModel", symbols)
            self.assertIn("SpamModel.score", symbols)
            self.assertIn("predict", symbols)
            self.assertIn("SpamModelTests.test_predicts_spam_keyword", symbols)
            entrypoint_paths = {item["path"] for item in repo_map["entrypoints"]}
            self.assertIn("spam_model.py", entrypoint_paths)
            self.assertEqual(repo_map["tests"][0]["path"], "tests/test_spam_model.py")
            self.assertEqual(repo_map["configs"][0]["path"], "pyproject.toml")
            repo_summary = read_text(result.repo_map_summary_path)
            self.assertIn("# Repo Map Summary", repo_summary)
            self.assertIn("## Prompt Budget", repo_summary)

    def test_greenfield_init_uses_empty_workspace_without_code_root(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            task_file = root / "task.md"
            write_text(task_file, "# Task\n\nCreate a small runnable Python experiment.\n")
            config = root / "greenfield.toml"
            write_text(
                config,
                """
[code_task]
kind = "greenfield"
task_file = "task.md"
name = "greenfield-smoke"

[benchmark]
command = "python generated_project/main.py"
primary_metric = "accuracy"
""".strip(),
            )

            options = load_code_task_init_options(config_path=str(config))
            self.assertEqual(options.kind, "greenfield")
            self.assertIsNone(options.code_root)
            self.assertEqual(options.workspace_mode, "empty")

            run_dir = root / "runs" / "greenfield-run"
            result = initialize_code_task(
                run_dir=run_dir,
                code_root=None,
                task_file=task_file,
                kind=options.kind,
                benchmark_command=options.benchmark_command,
                workspace_mode=options.workspace_mode,
                primary_metric=options.primary_metric,
            )

            self.assertEqual(result.kind, "greenfield")
            self.assertTrue(result.workspace_dir.is_dir())
            self.assertEqual(list(result.workspace_dir.iterdir()), [])
            manifest = read_json(run_dir / "manifest.json")
            self.assertEqual(manifest["code_task"]["kind"], "greenfield")
            self.assertEqual(manifest["workspace"]["mode"], "empty")
            self.assertEqual(manifest["source"]["code_root"], "")

    def test_greenfield_execute_generates_validates_and_runs_project(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            task_file = root / "task.md"
            write_text(
                task_file,
                "# Task\n\nGenerate a deterministic project that prints accuracy and macro_f1 metrics.\n",
            )
            run_dir = root / "runs" / "greenfield-run"
            initialize_code_task(
                run_dir=run_dir,
                code_root=None,
                task_file=task_file,
                kind="greenfield",
                benchmark_command="python generated_project/main.py",
                workspace_mode="empty",
                primary_metric="accuracy",
                metric_directions={"accuracy": "higher_is_better"},
            )

            result = execute_code_task(
                run_dir,
                use_llm=False,
                to_step="run",
                timeout_sec=30,
                max_files=8,
            )

            self.assertEqual(result.stop_reason, "completed")
            self.assertTrue((run_dir / "code_task" / "workspace" / "generated_project" / "main.py").is_file())
            self.assertTrue((run_dir / "code_task" / "meta" / "resource_probe.json").is_file())
            self.assertTrue((run_dir / "code_task" / "meta" / "resource_decision.json").is_file())
            advice = read_json(run_dir / "code_task" / "meta" / "dependency_advice.json")
            self.assertEqual(advice["schema_version"], "code_task_dependency_advice.v1")
            self.assertEqual(advice["policy"], "advice_only_no_auto_install")
            validation = read_json(run_dir / "code_task" / "meta" / "validation_report.json")
            self.assertEqual(validation["status"], "passed")
            metrics = read_json(run_dir / "code_task" / "run" / "patched" / "metrics.json")
            self.assertIn("accuracy", metrics)
            manifest = read_json(run_dir / "manifest.json")
            self.assertEqual(manifest["implementation"]["status"], "generated")
            self.assertEqual(manifest["patch"]["mode"], "greenfield_generated")

    def test_greenfield_review_failure_can_be_repaired_and_continue(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            task_file = root / "task.md"
            write_text(
                task_file,
                "# Task\n\nGenerate a deterministic project that prints accuracy and macro_f1 metrics.\n",
            )
            run_dir = root / "runs" / "greenfield-review-repair"
            initialize_code_task(
                run_dir=run_dir,
                code_root=None,
                task_file=task_file,
                kind="greenfield",
                benchmark_command="python generated_project/main.py",
                workspace_mode="empty",
                primary_metric="accuracy",
                metric_directions={"accuracy": "higher_is_better", "macro_f1": "higher_is_better"},
            )

            first = execute_code_task(
                run_dir,
                use_llm=False,
                to_step="work-plan",
                timeout_sec=30,
                max_files=8,
            )
            self.assertEqual(first.stop_reason, "stop_point")
            init_file = (
                run_dir
                / "code_task"
                / "workspace"
                / "generated_project"
                / "generated_experiment"
                / "__init__.py"
            )
            write_text(init_file, '__"""generated_experiment package."""\n')
            write_json(
                run_dir / "code_task" / "meta" / "review_report.json",
                {
                    "schema_version": "review_report.v1",
                    "status": "failed",
                    "findings": [
                        {
                            "severity": "blocking",
                            "category": "python_compile_failed",
                            "summary": "generated_experiment/__init__.py does not compile.",
                        }
                    ],
                    "summary": {"blocking_count": 1, "error_count": 1, "warning_count": 0},
                },
            )

            result = execute_code_task(
                run_dir,
                use_llm=False,
                to_step="run",
                timeout_sec=30,
                max_files=8,
                repair_rounds=1,
            )

            self.assertEqual(result.stop_reason, "completed")
            repair = read_json(run_dir / "code_task" / "meta" / "review_repair.json")
            self.assertEqual(repair["status"], "patched")
            rereview = read_json(run_dir / "code_task" / "meta" / "review_report.json")
            self.assertNotEqual(rereview["status"], "failed")
            metrics = read_json(run_dir / "code_task" / "run" / "patched" / "metrics.json")
            self.assertIn("accuracy", metrics)

    def test_greenfield_run_repair_handles_preset_and_function_signature_mismatch(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            project_dir = root / "generated_project"
            package_dir = project_dir / "generated_experiment"
            package_dir.mkdir(parents=True)
            write_json(
                project_dir / "config.json",
                {
                    "objective": "Greenfield run repair test.",
                    "presets": {
                        "{preset_name}": {
                            "conditions": ["baseline", "candidate"],
                            "max_items": 32,
                        }
                    },
                },
            )
            write_text(
                package_dir / "runner.py",
                (
                    "def run_experiment(preset='smoke'):\n"
                    "    return {'score': 1.0}\n"
                ),
            )

            preset_repair = repair_generated_project_from_run_failure(
                project_dir=project_dir,
                failure_analysis={"status": "needs_repair"},
                stderr_text=(
                    "raise KeyError(f\"Unknown preset '{preset_name}'. Available presets: {available}\")\n"
                    "KeyError: \"Unknown preset 'smoke'. Available presets: {preset_name}\"\n"
                ),
                output_path=root / "run_repair_preset.json",
            )

            self.assertEqual(preset_repair["status"], "patched")
            config = read_json(project_dir / "config.json")
            self.assertIn("smoke", config["presets"])
            self.assertNotIn("{preset_name}", config["presets"])

            signature_repair = repair_generated_project_from_run_failure(
                project_dir=project_dir,
                failure_analysis={"status": "needs_repair"},
                stderr_text="TypeError: run_experiment() got an unexpected keyword argument 'data_source'",
                output_path=root / "run_repair_signature.json",
            )

            self.assertEqual(signature_repair["status"], "patched")
            self.assertIn("generated_experiment/runner.py", signature_repair["changed_files"])
            self.assertIn("def run_experiment(preset='smoke', data_source=None):", read_text(package_dir / "runner.py"))

    def test_greenfield_run_repair_uses_llm_for_runtime_contract_mismatch(self) -> None:
        class FakeClient:
            def __init__(self) -> None:
                self.labels: list[str] = []

            def ask_json(self, _system: str, _prompt: str, *, label: str = "") -> dict[str, str]:
                self.labels.append(label)
                path = label.removeprefix("greenfield-run-repair-")
                return {
                    "content": (
                        "from __future__ import annotations\n\n"
                        f"REPAIRED_PATH = {path!r}\n"
                    ),
                    "summary": f"Repaired {path}.",
                }

        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            project_dir = root / "generated_project"
            package_dir = project_dir / "generated_experiment"
            package_dir.mkdir(parents=True)
            for name in ["inputs.py", "processing.py", "runner.py", "__init__.py"]:
                write_text(package_dir / name, "from __future__ import annotations\n")
            write_text(project_dir / "main.py", "from __future__ import annotations\n")

            fake = FakeClient()
            repair = repair_generated_project_from_run_failure(
                project_dir=project_dir,
                failure_analysis={"status": "needs_repair", "implicated_files": ["generated_project/main.py"]},
                stderr_text=(
                    "ERROR: Experiment run failed: "
                    "\"DatasetInput.metadata for 'wine' is missing required keys: features, labels\""
                ),
                output_path=root / "run_repair_contract.json",
                code_artifacts={
                    "generated_files": [
                        {"path": "generated_experiment/inputs.py", "mode": "llm"},
                        {"path": "generated_experiment/processing.py", "mode": "llm"},
                        {"path": "generated_experiment/runner.py", "mode": "llm"},
                        {"path": "main.py", "mode": "llm"},
                    ]
                },
                client=fake,
            )

            self.assertEqual(repair["status"], "patched")
            self.assertIn("generated_experiment/inputs.py", repair["changed_files"])
            self.assertIn("generated_experiment/processing.py", repair["changed_files"])
            self.assertTrue(fake.labels)
            self.assertEqual(
                fake.labels[:3],
                [
                    "greenfield-run-repair-generated_experiment/inputs.py",
                    "greenfield-run-repair-generated_experiment/processing.py",
                    "greenfield-run-repair-generated_experiment/runner.py",
                ],
            )
            self.assertIn("REPAIRED_PATH", read_text(package_dir / "inputs.py"))

    def test_greenfield_benchmark_rejects_empty_zero_evidence(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            task_file = root / "task.md"
            write_text(task_file, "# Task\n\nRun a greenfield experiment with condition-level evidence.\n")
            run_dir = root / "runs" / "empty-greenfield-evidence"
            initialize_code_task(
                run_dir=run_dir,
                code_root=None,
                task_file=task_file,
                kind="greenfield",
                benchmark_command="python generated_project/main.py",
                workspace_mode="empty",
                primary_metric="test_accuracy",
                metric_directions={
                    "test_accuracy": "higher",
                    "macro_f1": "higher",
                    "accuracy_std": "lower",
                    "runtime_sec": "resource",
                },
            )
            project_dir = run_dir / "code_task" / "workspace" / "generated_project"
            project_dir.mkdir(parents=True)
            write_text(
                project_dir / "main.py",
                (
                    "from pathlib import Path\n"
                    "import json\n\n"
                    "artifacts = Path('generated_project') / 'artifacts'\n"
                    "artifacts.mkdir(parents=True, exist_ok=True)\n"
                    "(artifacts / 'results.json').write_text(json.dumps({\n"
                    "    'condition_summaries': {},\n"
                    "    'dataset_comparisons': {},\n"
                    "    'raw_records': [{'condition': {'name': 'baseline'}, 'records': []}],\n"
                    "    'global_metrics': {'test_accuracy': 0.0, 'macro_f1': 0.0, 'accuracy_std': 0.0, 'runtime_sec': 0.0},\n"
                    "}), encoding='utf-8')\n"
                    "print('test_accuracy: 0.0')\n"
                    "print('macro_f1: 0.0')\n"
                    "print('accuracy_std: 0.0')\n"
                    "print('runtime_sec: 0.0')\n"
                ),
            )

            result = run_code_task_benchmark(
                run_dir,
                timeout_sec=30,
                skip_validation=True,
                run_label="patched",
            )

            self.assertEqual(result.status, "failed")
            self.assertIn("Generated benchmark quality guard failed", read_text(result.stderr_path))
            report = read_json(result.report_path)
            self.assertEqual(report["quality_guard"]["reason"], "empty_greenfield_evidence")
            manifest = read_json(run_dir / "manifest.json")
            self.assertEqual(manifest["status"], "benchmark_failed")

    def test_greenfield_run_repair_targets_custom_project_layout(self) -> None:
        class FakeClient:
            def __init__(self) -> None:
                self.labels: list[str] = []

            def ask_json(self, _system: str, _prompt: str, *, label: str = "") -> dict[str, str]:
                self.labels.append(label)
                path = label.removeprefix("greenfield-run-repair-")
                return {
                    "content": (
                        "from __future__ import annotations\n\n"
                        f"REPAIRED_PATH = {path!r}\n"
                    ),
                    "summary": f"Repaired {path}.",
                }

        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            project_dir = root / "generated_project"
            for rel_path in [
                "app.py",
                "src/loaders.py",
                "src/preprocess.py",
                "src/pipeline.py",
                "src/model_core.py",
            ]:
                target = project_dir / rel_path
                target.parent.mkdir(parents=True, exist_ok=True)
                write_text(target, "from __future__ import annotations\n")

            fake = FakeClient()
            repair = repair_generated_project_from_run_failure(
                project_dir=project_dir,
                failure_analysis={"status": "needs_repair", "implicated_files": ["generated_project/app.py"]},
                stderr_text=(
                    "ERROR: Experiment run failed: "
                    "\"DatasetInput.metadata is missing required keys: features, labels\""
                ),
                output_path=root / "run_repair_custom_layout.json",
                code_artifacts={
                    "generated_files": [
                        {"path": "app.py", "mode": "llm"},
                        {"path": "src/loaders.py", "mode": "llm"},
                        {"path": "src/preprocess.py", "mode": "llm"},
                        {"path": "src/pipeline.py", "mode": "llm"},
                        {"path": "src/model_core.py", "mode": "llm"},
                    ]
                },
                client=fake,
            )

            self.assertEqual(repair["status"], "patched")
            self.assertEqual(
                fake.labels[:3],
                [
                    "greenfield-run-repair-src/loaders.py",
                    "greenfield-run-repair-src/preprocess.py",
                    "greenfield-run-repair-src/pipeline.py",
                ],
            )
            self.assertIn("src/loaders.py", repair["changed_files"])

            fake_attr = FakeClient()
            attr_repair = repair_generated_project_from_run_failure(
                project_dir=project_dir,
                failure_analysis={"status": "needs_repair", "implicated_files": ["generated_project/app.py"]},
                stderr_text="Experiment failed: 'str' object has no attribute 'X'",
                output_path=root / "run_repair_custom_attribute_error.json",
                code_artifacts={
                    "generated_files": [
                        {"path": "app.py", "mode": "llm"},
                        {"path": "src/loaders.py", "mode": "llm"},
                        {"path": "src/preprocess.py", "mode": "llm"},
                        {"path": "src/pipeline.py", "mode": "llm"},
                        {"path": "src/model_core.py", "mode": "llm"},
                    ]
                },
                client=fake_attr,
            )

            self.assertEqual(attr_repair["status"], "patched")
            self.assertEqual(
                fake_attr.labels[:4],
                [
                    "greenfield-run-repair-src/loaders.py",
                    "greenfield-run-repair-src/preprocess.py",
                    "greenfield-run-repair-src/model_core.py",
                    "greenfield-run-repair-src/pipeline.py",
                ],
            )

    def test_greenfield_execute_can_use_fake_agent_backend(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            task_file = root / "task.md"
            write_text(
                task_file,
                "# Task\n\nUse an external handoff backend to generate a runnable metric project.\n",
            )
            run_dir = root / "runs" / "greenfield-agent-run"
            initialize_code_task(
                run_dir=run_dir,
                code_root=None,
                task_file=task_file,
                kind="greenfield",
                benchmark_command="python generated_project/main.py",
                workspace_mode="empty",
                primary_metric="accuracy",
            )

            result = execute_code_task(
                run_dir,
                use_llm=False,
                to_step="run",
                timeout_sec=30,
                implementation_provider="fake",
                implementation_agent_mode="handoff",
            )

            self.assertEqual(result.stop_reason, "completed")
            self.assertTrue((run_dir / "agent_handoff" / "code-task-greenfield-fake").is_dir())
            self.assertTrue((run_dir / "agent_outputs" / "code-task-greenfield-fake" / "ingestion.json").is_file())
            self.assertTrue((run_dir / "code_task" / "meta" / "dependency_advice.md").is_file())
            backend = read_json(run_dir / "code_task" / "meta" / "code_backend.json")
            self.assertEqual(backend["backend"], "greenfield_agent")
            self.assertEqual(backend["provider"], "fake")
            manifest = read_json(run_dir / "manifest.json")
            self.assertEqual(manifest["implementation"]["provider"], "fake")
            self.assertEqual(manifest["implementation"]["agent_mode"], "handoff")
            metrics = read_json(run_dir / "code_task" / "run" / "patched" / "metrics.json")
            self.assertIn("accuracy", metrics)

    def test_configured_edit_scope_limits_editable_repo_map_and_apply(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "toy_project"
            task_file = root / "task.md"
            config = root / "code_task.toml"
            _write_toy_project(code_root)
            write_text(task_file, "# Task\n\nImprove the model implementation only.\n")
            config.write_text(
                f"""
[code_task]
code_root = "{code_root.as_posix()}"
task_file = "{task_file.as_posix()}"

[edit_scope]
allowed_patterns = ["spam_model.py"]
protected_patterns = ["pyproject.toml"]
""".strip(),
                encoding="utf-8",
            )
            options = load_code_task_init_options(config_path=str(config))

            run_dir = root / "runs" / "scoped-code-task"
            initialize_code_task(
                run_dir=run_dir,
                code_root=Path(options.code_root),
                task_file=Path(options.task_file or ""),
                benchmark_command="python -m unittest discover -s tests",
                edit_scope_allowed_patterns=options.edit_scope_allowed_patterns,
                edit_scope_protected_patterns=options.edit_scope_protected_patterns,
            )

            manifest = read_json(run_dir / "manifest.json")
            self.assertEqual(manifest["edit_scope"]["allowed_patterns"], ["spam_model.py"])
            self.assertIn("pyproject.toml", manifest["edit_scope"]["protected_patterns"])
            repo_map = read_json(run_dir / "code_task" / "meta" / "repo_map.json")
            files = {row["path"]: row for row in repo_map["files"]}
            self.assertEqual(files["spam_model.py"]["access_role"], "editable")
            self.assertEqual(files["pyproject.toml"]["access_role"], "read_only_evidence")

            write_text(run_dir / "code_task" / "patch_plan.md", "# Patch Plan\n\n- Keep edits in scope.\n")
            record_plan_decision(run_dir, decision="approve", note="scope test")
            edits_path = root / "bad_scope_patch.json"
            write_json(
                edits_path,
                {
                    "schema_version": 1,
                    "edits": [
                        {
                            "path": "pyproject.toml",
                            "old": "[project]\nname = \"toy-project\"\nversion = \"0.1.0\"\n",
                            "new": "[project]\nname = \"toy-project\"\nversion = \"0.2.0\"\n",
                            "reason": "This file is intentionally outside the edit scope.",
                        }
                    ],
                },
            )
            with self.assertRaises(PatchValidationError) as caught:
                apply_patch_edits(run_dir, edits_file=edits_path)
            self.assertIn("path is not editable by the edit scope", str(caught.exception))

    def test_init_can_create_git_worktree_workspace(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("git executable is not available")
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "git_project"
            task_file = root / "task.md"
            _write_toy_project(code_root)
            shutil.rmtree(code_root / ".git")
            write_text(task_file, "# Task\n\nImprove the spam classifier.\n")
            _git(code_root, "init")
            _git(code_root, "config", "user.email", "test@example.com")
            _git(code_root, "config", "user.name", "SimpleAR Test")
            _git(code_root, "add", ".")
            _git(code_root, "commit", "-m", "initial")

            run_dir = root / "runs" / "git-worktree-run"
            result = initialize_code_task(
                run_dir=run_dir,
                code_root=code_root,
                task_file=task_file,
                benchmark_command="python -m unittest discover -s tests",
                workspace_mode="git_worktree",
                max_file_bytes=10_000,
            )

            self.assertTrue((result.workspace_dir / "spam_model.py").is_file())
            self.assertTrue((result.workspace_dir / ".git").exists())
            manifest = read_json(run_dir / "manifest.json")
            self.assertEqual(manifest["workspace"]["mode"], "git_worktree")
            self.assertEqual(manifest["workspace"]["workspace_dir"], "code_task/workspace")
            self.assertEqual(manifest["workspace"]["project_root"], "code_task/workspace")
            self.assertEqual(manifest["copy"]["files_copied"], 0)
            self.assertTrue(manifest["workspace"]["git"]["origin_commit"])
            self.assertEqual(manifest["workspace"]["environment_mapping"]["mode"], "git_worktree")
            index = read_json(result.codebase_index_path)
            self.assertEqual(index["project"]["python_file_count"], 2)
            self.assertNotIn(".env", {item["path"] for item in index["files"]})

    def test_auto_workspace_prefers_git_worktree_for_git_project(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("git executable is not available")
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "git_project"
            task_file = root / "task.md"
            _write_toy_project(code_root)
            shutil.rmtree(code_root / ".git")
            write_text(task_file, "# Task\n\nImprove the spam classifier.\n")
            _git(code_root, "init")
            _git(code_root, "config", "user.email", "test@example.com")
            _git(code_root, "config", "user.name", "SimpleAR Test")
            _git(code_root, "add", ".")
            _git(code_root, "commit", "-m", "initial")

            run_dir = root / "runs" / "auto-worktree-run"
            result = initialize_code_task(
                run_dir=run_dir,
                code_root=code_root,
                task_file=task_file,
                workspace_mode="auto",
                max_file_bytes=10_000,
            )

            self.assertEqual(result.workspace.mode, "git_worktree")
            self.assertEqual(result.workspace.requested_mode, "auto")
            self.assertEqual(result.workspace.selected_mode, "git_worktree")
            self.assertTrue((result.workspace.workspace_dir / ".git").exists())
            manifest = read_json(run_dir / "manifest.json")
            self.assertEqual(manifest["workspace"]["requested_mode"], "auto")
            self.assertEqual(manifest["workspace"]["selected_mode"], "git_worktree")
            self.assertEqual(manifest["workspace"]["fallback_reason"], "")

    def test_git_worktree_supports_project_subdirectory(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("git executable is not available")
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            repo = root / "repo"
            code_root = repo / "package"
            code_root.mkdir(parents=True)
            write_text(code_root / "module.py", "VALUE = 1\n")
            write_text(repo / "root_only.py", "ROOT_VALUE = 1\n")
            task_file = root / "task.md"
            write_text(task_file, "# Task\n\nChange VALUE.\n")
            _git(repo, "init")
            _git(repo, "config", "user.email", "test@example.com")
            _git(repo, "config", "user.name", "SimpleAR Test")
            _git(repo, "add", ".")
            _git(repo, "commit", "-m", "initial")

            run_dir = root / "runs" / "subdir-worktree-run"
            result = initialize_code_task(
                run_dir=run_dir,
                code_root=code_root,
                task_file=task_file,
                workspace_mode="git_worktree",
            )

            self.assertEqual(result.workspace_dir, result.workspace.project_root)
            self.assertTrue((result.workspace_dir / "module.py").is_file())
            self.assertFalse((result.workspace_dir / ".git").exists())
            self.assertTrue((result.workspace.workspace_dir / ".git").exists())
            manifest = read_json(root / "runs" / "subdir-worktree-run" / "manifest.json")
            self.assertEqual(manifest["workspace"]["mode"], "git_worktree")
            self.assertEqual(manifest["workspace"]["workspace_dir"], "code_task/workspace")
            self.assertEqual(manifest["workspace"]["project_root"], "code_task/workspace/package")
            self.assertEqual(manifest["workspace"]["project_relative_path"], "package")
            self.assertIn("subdirectory", " ".join(manifest["workspace"]["warnings"]))
            index = read_json(result.codebase_index_path)
            self.assertEqual(index["project"]["file_count"], 1)
            proposal = run_dir / "code_task" / "meta" / "escape_proposal.json"
            write_json(
                proposal,
                {
                    "edits": [
                        {
                            "path": "../root_only.py",
                            "old": "ROOT_VALUE = 1",
                            "new": "ROOT_VALUE = 2",
                            "reason": "This must not escape the package project root.",
                        }
                    ]
                },
            )
            with self.assertRaises(PatchValidationError):
                apply_patch_edits(run_dir, edits_file=proposal, allow_unapproved_plan=True)
            self.assertEqual(read_text(repo / "root_only.py"), "ROOT_VALUE = 1\n")

    def test_auto_workspace_falls_back_to_copy_for_non_git_project(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "plain_project"
            task_file = root / "task.md"
            _write_toy_project(code_root)
            write_text(task_file, "# Task\n\nImprove the plain project.\n")

            run_dir = root / "runs" / "auto-copy-run"
            result = initialize_code_task(
                run_dir=run_dir,
                code_root=code_root,
                task_file=task_file,
                workspace_mode="auto",
                max_file_bytes=10_000,
            )

            self.assertEqual(result.workspace.mode, "copy")
            self.assertEqual(result.workspace.requested_mode, "auto")
            self.assertEqual(result.workspace.selected_mode, "copy")
            self.assertTrue((result.workspace_dir / "spam_model.py").is_file())
            manifest = read_json(run_dir / "manifest.json")
            self.assertEqual(manifest["workspace"]["requested_mode"], "auto")
            self.assertEqual(manifest["workspace"]["selected_mode"], "copy")
            self.assertTrue(manifest["workspace"]["fallback_reason"])
            self.assertTrue(manifest["workspace"]["user_next_steps"])

    def test_init_can_create_sparse_copy_workspace(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "sparse_project"
            task_file = root / "task.md"
            write_text(code_root / "src" / "pkg" / "model.py", "def predict():\n    return 1\n")
            write_text(code_root / "tests" / "test_model.py", "def test_predict():\n    assert True\n")
            write_text(code_root / "benchmark.py", "print('accuracy: 1.0')\n")
            write_text(code_root / "pyproject.toml", "[project]\nname = 'sparse-project'\n")
            write_text(code_root / "data" / "dataset.csv", "id,label\n1,spam\n")
            write_text(code_root / "models" / "weights.bin", "not really weights\n")
            write_text(code_root / ".env", "TOKEN=secret\n")
            write_text(task_file, "# Task\n\nImprove sparse project.\n")

            run_dir = root / "runs" / "sparse-run"
            result = initialize_code_task(
                run_dir=run_dir,
                code_root=code_root,
                task_file=task_file,
                benchmark_command="python benchmark.py",
                workspace_mode="sparse_copy",
                workspace_include=("src/**", "tests/**", "benchmark.py", "pyproject.toml"),
                workspace_exclude=("models/**",),
            )

            workspace = result.workspace_dir
            self.assertTrue((workspace / "src" / "pkg" / "model.py").is_file())
            self.assertTrue((workspace / "tests" / "test_model.py").is_file())
            self.assertTrue((workspace / "benchmark.py").is_file())
            self.assertTrue((workspace / "pyproject.toml").is_file())
            self.assertFalse((workspace / "data" / "dataset.csv").exists())
            self.assertFalse((workspace / "models" / "weights.bin").exists())
            self.assertFalse((workspace / ".env").exists())

            manifest = read_json(run_dir / "manifest.json")
            self.assertEqual(manifest["workspace"]["mode"], "sparse_copy")
            self.assertEqual(
                manifest["workspace"]["patterns"]["include"],
                ["src/**", "tests/**", "benchmark.py", "pyproject.toml"],
            )
            self.assertIn("models/**", manifest["workspace"]["patterns"]["exclude"])
            skipped_reasons = {item["reason"] for item in manifest["copy"]["skipped"]}
            self.assertIn("sparse_excluded_dir", skipped_reasons)

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
            self.assertIn("Repo map:", output)
            run_dir = next(output_root.iterdir())
            self.assertTrue((run_dir / "code_task" / "workspace" / "spam_model.py").is_file())
            self.assertTrue((run_dir / "code_task" / "meta" / "codebase_index.json").is_file())
            self.assertTrue((run_dir / "code_task" / "meta" / "repo_map.json").is_file())

            status_stdout = io.StringIO()
            with contextlib.redirect_stdout(status_stdout):
                main(["status", str(run_dir)])

            status_text = status_stdout.getvalue()
            self.assertIn("Workflow: code_task", status_text)
            self.assertIn("python files: 2", status_text)

    def test_code_task_map_rebuilds_repo_map_from_workspace(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "toy_project"
            task_file = root / "task.md"
            _write_toy_project(code_root)
            write_text(task_file, "# Task\n\nMap this project.\n")
            run_dir = root / "runs" / "code-task-run"
            initialize_code_task(
                run_dir=run_dir,
                code_root=code_root,
                task_file=task_file,
                benchmark_command="python -m unittest discover -s tests",
            )
            write_text(
                run_dir / "code_task" / "workspace" / "feature.py",
                "def improve_feature(value):\n    return value + 1\n",
            )

            result = build_code_task_repo_map(run_dir)

            self.assertTrue(result.refreshed_index)
            repo_map = read_json(result.repo_map_path)
            mapped_files = {item["path"] for item in repo_map["files"]}
            self.assertIn("feature.py", mapped_files)
            symbols = {item["qualified_name"] for item in repo_map["symbols"]}
            self.assertIn("improve_feature", symbols)
            manifest = read_json(run_dir / "manifest.json")
            self.assertEqual(manifest["layout"]["repo_map"], "code_task/meta/repo_map.json")
            self.assertEqual(
                manifest["codebase"]["repo_map"]["summary"],
                "code_task/meta/repo_map_summary.md",
            )

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                main(["code-task", "map", str(run_dir), "--no-refresh-index"])
            output = stdout.getvalue()
            self.assertIn("Repo map:", output)
            self.assertIn("Index refreshed: False", output)

    def test_code_task_locate_writes_ranked_targets_and_evidence(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "toy_project"
            task_file = root / "task.md"
            _write_toy_project(code_root)
            write_text(task_file, "# Task\n\nImprove spam keyword prediction.\n")
            run_dir = root / "runs" / "code-task-run"
            initialize_code_task(
                run_dir=run_dir,
                code_root=code_root,
                task_file=task_file,
                benchmark_command="python -m unittest discover -s tests",
            )

            result = locate_code_task_context(
                run_dir,
                query="improve spam keyword predict behavior",
                top_k=4,
            )

            self.assertTrue(result.results_path.is_file())
            self.assertTrue(result.summary_path.is_file())
            self.assertEqual(result.editable_targets[0]["path"], "spam_model.py")
            evidence_paths = {row["path"] for row in result.read_only_evidence}
            self.assertIn("tests/test_spam_model.py", evidence_paths)
            locate_data = read_json(result.results_path)
            self.assertEqual(locate_data["schema_version"], 1)
            self.assertIn("spam", locate_data["query_terms"])
            summary = read_text(result.summary_path)
            self.assertIn("# Locate Results", summary)
            self.assertIn("spam_model.py", summary)
            manifest = read_json(run_dir / "manifest.json")
            self.assertEqual(manifest["layout"]["locate_results"], "code_task/meta/locate_results.json")
            self.assertEqual(manifest["locate"]["status"], "completed")

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                main(
                    [
                        "code-task",
                        "locate",
                        str(run_dir),
                        "--query",
                        "spam keyword",
                        "--top-k",
                        "3",
                    ]
                )
            output = stdout.getvalue()
            self.assertIn("Locate results:", output)
            self.assertIn("Editable targets:", output)

    def test_code_task_context_pack_writes_prompt_and_snippets(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "toy_project"
            task_file = root / "task.md"
            _write_toy_project(code_root)
            write_text(task_file, "# Task\n\nImprove spam keyword prediction.\n")
            run_dir = root / "runs" / "code-task-run"
            initialize_code_task(
                run_dir=run_dir,
                code_root=code_root,
                task_file=task_file,
                benchmark_command="python -m unittest discover -s tests",
            )

            result = build_code_task_context_pack(
                run_dir,
                query="improve spam keyword predict behavior",
                top_k=4,
                max_files=3,
                max_source_chars_per_file=600,
                max_total_chars=1200,
            )

            self.assertTrue(result.context_pack_path.is_file())
            self.assertTrue(result.prompt_context_path.is_file())
            self.assertTrue(result.snippets_path.is_file())
            self.assertIn("spam_model.py", result.selected_files)
            snippets = read_jsonl(result.snippets_path)
            snippet_paths = {row["path"] for row in snippets}
            self.assertIn("spam_model.py", snippet_paths)
            self.assertIn("tests/test_spam_model.py", snippet_paths)
            prompt_context = read_text(result.prompt_context_path)
            self.assertIn("# Code Task Context Pack", prompt_context)
            self.assertIn("## Editable Targets", prompt_context)
            self.assertIn("## Read-Only Evidence", prompt_context)
            context_pack = read_json(result.context_pack_path)
            self.assertEqual(context_pack["schema_version"], 1)
            self.assertLessEqual(context_pack["budget"]["used_chars"], 1200)
            manifest = read_json(run_dir / "manifest.json")
            self.assertEqual(manifest["layout"]["context_packs"], "code_task/context_packs")
            self.assertEqual(manifest["context_pack"]["status"], "completed")

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                main(
                    [
                        "code-task",
                        "context",
                        str(run_dir),
                        "--query",
                        "spam keyword",
                        "--max-files",
                        "2",
                    ]
                )
            output = stdout.getvalue()
            self.assertIn("Context pack:", output)
            self.assertIn("Selected files:", output)

    def test_work_plan_offline_writes_batchable_items_and_manifest(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "toy_project"
            task_file = root / "task.md"
            _write_toy_project(code_root)
            write_text(
                task_file,
                "# Task\n\nImprove spam keyword prediction without changing tests.\n",
            )
            run_dir = root / "runs" / "code-task-run"
            initialize_code_task(
                run_dir=run_dir,
                code_root=code_root,
                task_file=task_file,
                benchmark_command="python -m unittest discover -s tests",
            )
            build_code_task_context_pack(
                run_dir,
                query="improve spam keyword prediction",
                top_k=4,
                max_files=3,
            )

            result = generate_code_task_work_plan(run_dir, use_llm=False)

            self.assertEqual(result.mode, "offline")
            self.assertTrue(result.pending_approval)
            self.assertEqual(result.item_count, 1)
            self.assertTrue(result.work_plan_path.is_file())
            self.assertTrue(result.work_plan_markdown_path.is_file())
            plan = read_json(result.work_plan_path)
            self.assertEqual(plan["schema_version"], 1)
            self.assertEqual(plan["items"][0]["id"], "W1")
            self.assertIn("spam_model.py", plan["items"][0]["target_files"])
            self.assertNotIn("tests/test_spam_model.py", plan["items"][0]["target_files"])
            self.assertIn("tests/test_spam_model.py", plan["items"][0]["read_only_evidence"])
            markdown = read_text(result.work_plan_markdown_path)
            self.assertIn("# Work Plan", markdown)
            self.assertIn("## Work Items", markdown)
            self.assertIn("W1", markdown)

            manifest = read_json(run_dir / "manifest.json")
            self.assertEqual(manifest["status"], "work_planned")
            self.assertEqual(manifest["layout"]["work_plan"], "code_task/work_plan.json")
            self.assertEqual(manifest["work_plan"]["status"], "pending_approval")
            self.assertEqual(manifest["work_plan"]["item_count"], 1)

    def test_work_plan_and_batch_cli_create_attempt_state(self) -> None:
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

            work_plan_stdout = io.StringIO()
            with contextlib.redirect_stdout(work_plan_stdout):
                main(["code-task", "work-plan", str(run_dir), "--no-llm"])

            self.assertIn("Work plan:", work_plan_stdout.getvalue())
            self.assertTrue((run_dir / "code_task" / "work_plan.json").is_file())

            batch_stdout = io.StringIO()
            with contextlib.redirect_stdout(batch_stdout):
                main(["code-task", "batch", str(run_dir), "--work-item", "W1"])

            output = batch_stdout.getvalue()
            self.assertIn("Attempt: attempt-001", output)
            self.assertIn("Batch: batch-001", output)
            attempt_state = read_json(
                run_dir / "code_task" / "attempts" / "attempt-001" / "attempt_state.json"
            )
            batch_state = read_json(
                run_dir
                / "code_task"
                / "attempts"
                / "attempt-001"
                / "batches"
                / "batch-001"
                / "batch_state.json"
            )
            self.assertEqual(attempt_state["state"], "batching")
            self.assertEqual(batch_state["state"], "created")
            self.assertEqual(batch_state["work_item_id"], "W1")
            manifest = read_json(run_dir / "manifest.json")
            self.assertEqual(manifest["status"], "batch_created")
            self.assertEqual(manifest["attempts"]["active"], "attempt-001")
            self.assertIn("latest_batch", manifest["attempts"])

    def test_create_code_task_batch_reuses_existing_item_batch(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "toy_project"
            task_file = root / "task.md"
            _write_toy_project(code_root)
            write_text(task_file, "# Task\n\nImprove spam classifier accuracy.\n")
            run_dir = root / "runs" / "code-task-run"
            initialize_code_task(
                run_dir=run_dir,
                code_root=code_root,
                task_file=task_file,
                benchmark_command="python -m unittest discover -s tests",
            )
            generate_code_task_work_plan(run_dir, use_llm=False)

            first = create_code_task_batch(run_dir, work_item_id="W1")
            second = create_code_task_batch(run_dir, work_item_id="W1")
            forced = create_code_task_batch(run_dir, work_item_id="W1", force=True)

            self.assertEqual(first.batch_id, "batch-001")
            self.assertEqual(second.batch_id, "batch-001")
            self.assertEqual(forced.batch_id, "batch-002")
            self.assertTrue(forced.batch_state_path.is_file())

    def test_execute_selects_first_implementation_work_item(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "toy_project"
            task_file = root / "task.md"
            _write_toy_project(code_root)
            write_text(task_file, "# Task\n\nImprove spam prediction.\n")
            run_dir = root / "runs" / "code-task-run"
            initialize_code_task(
                run_dir=run_dir,
                code_root=code_root,
                task_file=task_file,
                benchmark_command="python -m unittest discover -s tests",
            )
            _write_analysis_first_work_plan(run_dir)

            result = execute_code_task(run_dir, use_llm=False, to_step="batch", timeout_sec=10)

            self.assertEqual(result.stop_reason, "stop_point")
            batch_state = read_json(
                run_dir
                / "code_task"
                / "attempts"
                / "attempt-001"
                / "batches"
                / "batch-001"
                / "batch_state.json"
            )
            self.assertEqual(batch_state["work_item_id"], "W2")
            self.assertIn("Implement", batch_state["work_item"]["objective"])

    def test_create_code_task_batch_merges_serial_dependent_items(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "toy_project"
            task_file = root / "task.md"
            _write_toy_project(code_root)
            write_text(code_root / "extra.py", "VALUE = 1\n")
            write_text(task_file, "# Task\n\nImplement a coupled feature, scorer, and config change.\n")
            run_dir = root / "runs" / "code-task-run"
            initialize_code_task(
                run_dir=run_dir,
                code_root=code_root,
                task_file=task_file,
                benchmark_command="python -m unittest discover -s tests",
            )
            _write_dependent_work_plan(run_dir)

            result = create_code_task_batch(run_dir, work_item_id="W1")

            batch_state = read_json(result.batch_state_path)
            self.assertEqual(batch_state["work_item_id"], "W1")
            work_item = batch_state["work_item"]
            self.assertEqual(work_item["source_work_item_ids"], ["W1", "W2", "W3"])
            self.assertEqual(work_item["execution_scope"], "merged_dependent_chain")
            self.assertEqual(
                work_item["target_files"],
                ["spam_model.py", "extra.py", "pyproject.toml"],
            )
            self.assertEqual(work_item["budget_profile"], "large")
            self.assertTrue(work_item["requires_budget_override"])

    def test_create_code_task_batch_can_disable_serial_merge(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "toy_project"
            task_file = root / "task.md"
            _write_toy_project(code_root)
            write_text(code_root / "extra.py", "VALUE = 1\n")
            write_text(task_file, "# Task\n\nImplement a coupled feature safely.\n")
            run_dir = root / "runs" / "code-task-run"
            initialize_code_task(
                run_dir=run_dir,
                code_root=code_root,
                task_file=task_file,
                benchmark_command="python -m unittest discover -s tests",
            )
            _write_dependent_work_plan(run_dir)

            result = create_code_task_batch(
                run_dir,
                work_item_id="W1",
                merge_dependent_chain=False,
            )

            batch_state = read_json(result.batch_state_path)
            work_item = batch_state["work_item"]
            self.assertEqual(work_item["source_work_item_ids"], ["W1"])
            self.assertEqual(work_item["execution_scope"], "single_work_item")
            self.assertEqual(work_item["target_files"], ["spam_model.py"])
            self.assertEqual(work_item["budget_profile"], "normal")
            self.assertFalse(work_item["requires_budget_override"])

    def test_record_plan_decision_updates_work_plan_approval(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "toy_project"
            task_file = root / "task.md"
            _write_toy_project(code_root)
            write_text(task_file, "# Task\n\nApprove both plans.\n")
            run_dir = root / "runs" / "code-task-run"
            initialize_code_task(
                run_dir=run_dir,
                code_root=code_root,
                task_file=task_file,
                benchmark_command="python -m unittest discover -s tests",
            )
            generate_code_task_work_plan(run_dir, use_llm=False)
            generate_patch_plan(run_dir, use_llm=False)

            record_plan_decision(run_dir, decision="approve", note="ready")

            manifest = read_json(run_dir / "manifest.json")
            self.assertEqual(manifest["plan"]["status"], "approved")
            self.assertEqual(manifest["work_plan"]["status"], "ready")
            self.assertEqual(manifest["work_plan"]["approval"]["status"], "approved")

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

    def test_plan_and_propose_use_latest_context_pack_when_available(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "toy_project"
            task_file = root / "task.md"
            _write_toy_project(code_root)
            write_text(task_file, "# Task\n\nImprove spam keyword prediction without editing tests.\n")
            run_dir = root / "runs" / "code-task-run"
            initialize_code_task(
                run_dir=run_dir,
                code_root=code_root,
                task_file=task_file,
                benchmark_command="python -m unittest discover -s tests",
            )
            context = build_code_task_context_pack(
                run_dir,
                query="improve spam keyword prediction",
                top_k=4,
                max_files=3,
            )

            plan = generate_patch_plan(run_dir, use_llm=False)

            self.assertIn("spam_model.py", plan.selected_files)
            plan_text = read_text(plan.patch_plan_path)
            self.assertIn("Context pack:", plan_text)
            self.assertIn("code_task/context_packs/context-001/context_pack.json", plan_text)
            manifest = read_json(run_dir / "manifest.json")
            self.assertEqual(
                manifest["plan"]["context_pack"]["path"],
                "code_task/context_packs/context-001/context_pack.json",
            )
            self.assertEqual(
                manifest["plan"]["context_pack"]["prompt_context"],
                "code_task/context_packs/context-001/prompt_context.md",
            )

            record_plan_decision(run_dir, decision="approve")
            proposal = propose_patch_edits(run_dir, use_llm=False)

            self.assertEqual(proposal.edit_count, 0)
            proposal_data = read_json(proposal.proposal_path)
            self.assertEqual(
                proposal_data["context_pack"]["path"],
                "code_task/context_packs/context-001/context_pack.json",
            )
            self.assertEqual(proposal_data["selected_files"], ["spam_model.py"])
            self.assertIn("tests/test_spam_model.py", proposal_data["read_only_context"])
            manifest = read_json(run_dir / "manifest.json")
            self.assertEqual(
                manifest["patch"]["context_pack"]["path"],
                "code_task/context_packs/context-001/context_pack.json",
            )
            self.assertTrue(context.context_pack_path.is_file())

    def test_propose_edits_restricts_llm_to_current_batch_targets(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "toy_project"
            task_file = root / "task.md"
            _write_toy_project(code_root)
            write_text(code_root / "extra.py", "VALUE = 1\n")
            write_text(task_file, "# Task\n\nImprove spam prediction only.\n")
            run_dir = root / "runs" / "code-task-run"
            initialize_code_task(
                run_dir=run_dir,
                code_root=code_root,
                task_file=task_file,
                benchmark_command="python -m unittest discover -s tests",
            )
            generate_code_task_work_plan(run_dir, use_llm=False)
            work_plan_path = run_dir / "code_task" / "work_plan.json"
            work_plan = read_json(work_plan_path)
            work_plan["items"][0]["target_files"] = ["spam_model.py"]
            write_json(work_plan_path, work_plan)
            create_code_task_batch(run_dir, work_item_id="W1")
            generate_patch_plan(run_dir, use_llm=False)
            record_plan_decision(run_dir, decision="approve")
            fake_client = _FakeRepairClient(
                {
                    "summary": "Try one valid batch edit and one unrelated edit.",
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
                            "reason": "Improve the selected batch target.",
                        },
                        {
                            "path": "extra.py",
                            "old": "VALUE = 1\n",
                            "new": "VALUE = 2\n",
                            "reason": "This file is outside the current batch.",
                        },
                    ],
                    "validation": ["Run unit tests."],
                    "risks": [],
                }
            )

            with patch("simple_ar.code_task.editing.patching.LLMClient.from_env", return_value=fake_client):
                proposal = propose_patch_edits(run_dir, use_llm=True)

            self.assertEqual(proposal.edit_count, 1)
            data = read_json(proposal.proposal_path)
            self.assertEqual(data["editor"]["backend"], "controlled_patch")
            self.assertEqual([edit["path"] for edit in data["edits"]], ["spam_model.py"])
            self.assertIn(
                "Dropped edit outside current batch target files: extra.py",
                data["warnings"],
            )
            manifest = read_json(run_dir / "manifest.json")
            self.assertEqual(manifest["patch"]["editor_backend"], "controlled_patch")
            self.assertEqual(manifest["patch"]["editor"]["backend"], "controlled_patch")
            batch_state = read_json(
                run_dir
                / "code_task"
                / "attempts"
                / "attempt-001"
                / "batches"
                / "batch-001"
                / "batch_state.json"
            )
            self.assertEqual(batch_state["state"], "proposal_ready")
            self.assertEqual(batch_state["editor"]["backend"], "controlled_patch")
            self.assertEqual(
                batch_state["artifacts"]["proposed_edits"],
                "code_task/attempts/attempt-001/batches/batch-001/proposed_edits.json",
            )
            batch_proposal = read_json(
                run_dir
                / "code_task"
                / "attempts"
                / "attempt-001"
                / "batches"
                / "batch-001"
                / "proposed_edits.json"
            )
            self.assertEqual(batch_proposal["editor"]["backend"], "controlled_patch")

    def test_propose_edits_budget_requires_large_approval(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "toy_project"
            task_file = root / "task.md"
            _write_toy_project(code_root)
            write_text(task_file, "# Task\n\nMake a deliberately large local implementation.\n")
            run_dir = root / "runs" / "code-task-run"
            initialize_code_task(
                run_dir=run_dir,
                code_root=code_root,
                task_file=task_file,
                benchmark_command="python -m unittest discover -s tests",
            )
            generate_code_task_work_plan(run_dir, use_llm=False)
            create_code_task_batch(run_dir, work_item_id="W1")
            generate_patch_plan(run_dir, use_llm=False)
            record_plan_decision(run_dir, decision="approve")
            large_new = "def predict(text):\n" + "    lowered = text.lower()\n" * 260 + "    return 'spam'\n"
            fake_client = _FakeRepairClient(
                {
                    "summary": "Large but localized function replacement.",
                    "edits": [
                        {
                            "path": "spam_model.py",
                            "old": (
                                "def predict(text):\n"
                                "    return 'spam' if 'win' in text.lower() else 'ham'\n"
                            ),
                            "new": large_new,
                            "reason": "Large local implementation.",
                        }
                    ],
                    "validation": ["Run unit tests."],
                    "risks": ["Large edit."],
                }
            )

            with patch("simple_ar.code_task.editing.patching.LLMClient.from_env", return_value=fake_client):
                blocked = propose_patch_edits(run_dir, use_llm=True)

            blocked_data = read_json(blocked.proposal_path)
            self.assertEqual(blocked.edit_count, 0)
            self.assertEqual(blocked_data["budget"]["status"], "large_requires_approval")
            self.assertTrue(blocked_data["budget"]["requires_approval"])
            self.assertIn("Proposal exceeds the selected edit budget", blocked_data["warnings"][0])

            with patch("simple_ar.code_task.editing.patching.LLMClient.from_env", return_value=fake_client):
                approved = propose_patch_edits(
                    run_dir,
                    use_llm=True,
                    force=True,
                    allow_large_edits=True,
                )

            approved_data = read_json(approved.proposal_path)
            self.assertEqual(approved.edit_count, 1)
            self.assertEqual(approved_data["budget"]["status"], "large_approved")
            self.assertTrue(approved_data["budget"]["approved"])

    def test_embedded_code_task_experiment_creates_work_plan_and_batch(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "toy_project"
            task_file = root / "task.md"
            _write_toy_project(code_root)
            write_text(task_file, "# Task\n\nImprove spam prediction for prize messages.\n")
            run_dir = root / "runs" / "embedded-code-task"
            fake_client = _FakeCodeTaskClient()

            with (
                patch("simple_ar.code_task.editing.work_plan.LLMClient.from_env", return_value=fake_client),
                patch("simple_ar.code_task.editing.planning.LLMClient.from_env", return_value=fake_client),
                patch("simple_ar.code_task.editing.patching.LLMClient.from_env", return_value=fake_client),
            ):
                result = prepare_code_task_experiment(
                    code_task_run_dir=run_dir,
                    spec=CodeTaskExperimentSpec(
                        template="code_task_project",
                        code_root=code_root,
                        task_file=task_file,
                        benchmark_command="python -m unittest discover -s tests",
                    ),
                    model="fake-model",
                    use_llm=True,
                    timeout_sec=30,
                )

            self.assertEqual(result.work_plan_mode, "llm")
            self.assertEqual(result.work_item_id, "W1")
            self.assertTrue(result.context_pack_path and result.context_pack_path.is_file())
            self.assertTrue(result.work_plan_path and result.work_plan_path.is_file())
            self.assertTrue(result.batch_state_path and result.batch_state_path.is_file())
            self.assertEqual(result.changed_files, ("spam_model.py",))

            meta_path = root / "code_task_experiment.json"
            write_code_task_experiment_meta(meta_path, result)
            meta = read_json(meta_path)
            self.assertEqual(meta["work_plan_mode"], "llm")
            self.assertEqual(meta["batch"]["work_item_id"], "W1")
            self.assertEqual(meta["batch"]["state"], "completed")
            self.assertEqual(meta["editor_backend"], "controlled_patch")
            self.assertIn("repo_map", meta)
            self.assertIn("context_pack", meta)
            self.assertIn("comparison", meta)

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
            self.assertEqual(applied["editor"]["backend"], "controlled_patch")
            self.assertEqual(applied["editor"]["source"], "legacy_or_manual_proposal")
            self.assertTrue(applied["edits"][0]["old_sha256"])
            self.assertTrue(applied["edits"][0]["new_sha256"])
            diff_text = read_text(run_dir / "code_task" / "patch.diff")
            self.assertIn("+    lowered = text.lower()", diff_text)
            manifest = read_json(run_dir / "manifest.json")
            self.assertEqual(manifest["status"], "patched")
            self.assertEqual(manifest["patch"]["status"], "applied")
            self.assertEqual(manifest["patch"]["editor_backend"], "controlled_patch")
            self.assertEqual(manifest["patch"]["editor"]["backend"], "controlled_patch")
            self.assertNotIn("pre_patch_manifest", manifest["patch"])
            self.assertNotIn("post_patch_manifest", manifest["patch"])

    def test_apply_large_edits_records_apply_time_approval(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "toy_project"
            task_file = root / "task.md"
            _write_toy_project(code_root)
            write_text(task_file, "# Task\n\nApply a reviewed large proposal.\n")
            run_dir = root / "runs" / "code-task-run"
            initialize_code_task(run_dir=run_dir, code_root=code_root, task_file=task_file)
            generate_patch_plan(run_dir, use_llm=False)
            record_plan_decision(run_dir, decision="approve")
            proposal_path = _write_valid_edit_proposal(run_dir)
            proposal = read_json(proposal_path)
            proposal["budget"] = {
                "status": "accepted",
                "profile": "large",
                "requires_approval": True,
                "approved": False,
            }
            write_json(proposal_path, proposal)

            with self.assertRaises(PermissionError):
                apply_patch_edits(run_dir, edits_file=proposal_path)

            apply_patch_edits(run_dir, edits_file=proposal_path, allow_large_edits=True)

            applied = read_json(run_dir / "code_task" / "meta" / "applied_edits.json")
            self.assertTrue(applied["budget"]["approved"])
            self.assertEqual(applied["budget"]["approval_source"], "apply_edits_allow_large_edits")
            manifest = read_json(run_dir / "manifest.json")
            self.assertTrue(manifest["patch"]["budget"]["approved"])
            self.assertEqual(
                manifest["patch"]["budget"]["approval_source"],
                "apply_edits_allow_large_edits",
            )

    def test_apply_repair_proposal_records_latest_applied_proposal(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "toy_project"
            task_file = root / "task.md"
            _write_toy_project(code_root)
            write_text(task_file, "# Task\n\nApply a reviewed repair proposal.\n")
            run_dir = root / "runs" / "code-task-run"
            initialize_code_task(run_dir=run_dir, code_root=code_root, task_file=task_file)
            generate_patch_plan(run_dir, use_llm=False)
            record_plan_decision(run_dir, decision="approve")
            repair_dir = run_dir / "code_task" / "repairs" / "repair-001"
            repair_dir.mkdir(parents=True)
            repair_proposal = _write_valid_edit_proposal(run_dir, path=repair_dir / "proposed_edits.json")
            manifest = read_json(run_dir / "manifest.json")
            manifest["repair"] = {
                "status": "repair_proposed",
                "repair_count": 1,
                "latest_proposed_edits": "code_task/repairs/repair-001/proposed_edits.json",
            }
            write_json(run_dir / "manifest.json", manifest)

            apply_patch_edits(run_dir, edits_file=repair_proposal)

            manifest = read_json(run_dir / "manifest.json")
            self.assertEqual(manifest["patch"]["latest_applied_proposal"], "code_task/repairs/repair-001/proposed_edits.json")
            self.assertEqual(manifest["repair"]["status"], "repair_applied")
            self.assertEqual(
                manifest["repair"]["latest_applied_proposal"],
                "code_task/repairs/repair-001/proposed_edits.json",
            )
            applied = read_json(run_dir / "code_task" / "meta" / "applied_edits.json")
            self.assertEqual(applied["proposal"], "code_task/repairs/repair-001/proposed_edits.json")

    def test_apply_edits_allows_multiple_ordered_edits_in_one_file(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "toy_project"
            task_file = root / "task.md"
            _write_toy_project(code_root)
            write_text(task_file, "# Task\n\nApply two edits in the same file.\n")
            run_dir = root / "runs" / "code-task-run"
            initialize_code_task(run_dir=run_dir, code_root=code_root, task_file=task_file)
            generate_patch_plan(run_dir, use_llm=False)
            record_plan_decision(run_dir, decision="approve")
            proposal_path = run_dir / "code_task" / "meta" / "proposed_edits.json"
            write_json(
                proposal_path,
                {
                    "edits": [
                        {
                            "path": "spam_model.py",
                            "old": "import math\n\n\n",
                            "new": "import math\n\nSPAM_KEYWORDS = ('win', 'prize')\n\n",
                            "reason": "Add shared keyword configuration.",
                        },
                        {
                            "path": "spam_model.py",
                            "old": (
                                "def predict(text):\n"
                                "    return 'spam' if 'win' in text.lower() else 'ham'\n"
                            ),
                            "new": (
                                "def predict(text):\n"
                                "    lowered = text.lower()\n"
                                "    return 'spam' if any(keyword in lowered for keyword in SPAM_KEYWORDS) else 'ham'\n"
                            ),
                            "reason": "Use the shared keyword configuration.",
                        },
                    ]
                },
            )

            result = apply_patch_edits(run_dir, edits_file=proposal_path)

            self.assertEqual(result.changed_files, ("spam_model.py",))
            text = read_text(run_dir / "code_task" / "workspace" / "spam_model.py")
            self.assertIn("SPAM_KEYWORDS", text)
            self.assertIn("any(keyword in lowered", text)
            applied = read_json(run_dir / "code_task" / "meta" / "applied_edits.json")
            self.assertEqual(applied["edit_count"], 2)
            self.assertEqual(applied["changed_files"], ["spam_model.py"])
            diff_text = read_text(run_dir / "code_task" / "patch.diff")
            self.assertEqual(diff_text.count("--- a/spam_model.py"), 1)

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

    def test_apply_edits_rejects_protected_test_and_benchmark_files(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "toy_project"
            task_file = root / "task.md"
            _write_toy_project(code_root)
            write_text(code_root / "benchmark.py", "print('accuracy: 0.5')\n")
            write_text(task_file, "# Task\n\nImprove source behavior without changing validation targets.\n")
            run_dir = root / "runs" / "code-task-run"
            initialize_code_task(run_dir=run_dir, code_root=code_root, task_file=task_file)
            generate_patch_plan(run_dir, use_llm=False)
            record_plan_decision(run_dir, decision="approve")
            proposal_path = root / "protected_edits.json"
            write_json(
                proposal_path,
                {
                    "edits": [
                        {
                            "path": "tests/test_spam_model.py",
                            "old": "        self.assertEqual(predict('win now'), 'spam')\n",
                            "new": "        self.assertEqual(predict('win now'), 'ham')\n",
                            "reason": "Should be blocked because tests are read-only evidence.",
                        },
                        {
                            "path": "benchmark.py",
                            "old": "print('accuracy: 0.5')\n",
                            "new": "print('accuracy: 1.0')\n",
                            "reason": "Should be blocked because benchmarks are read-only evidence.",
                        },
                    ]
                },
            )

            before_test = read_text(run_dir / "code_task" / "workspace" / "tests" / "test_spam_model.py")
            before_benchmark = read_text(run_dir / "code_task" / "workspace" / "benchmark.py")
            with self.assertRaises(PatchValidationError) as caught:
                apply_patch_edits(run_dir, edits_file=proposal_path)

            self.assertIn("path is protected by the edit scope", str(caught.exception))
            self.assertEqual(
                before_test,
                read_text(run_dir / "code_task" / "workspace" / "tests" / "test_spam_model.py"),
            )
            self.assertEqual(
                before_benchmark,
                read_text(run_dir / "code_task" / "workspace" / "benchmark.py"),
            )
            self.assertFalse((run_dir / "code_task" / "patch.diff").exists())

    def test_propose_edits_drops_protected_llm_paths(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "toy_project"
            task_file = root / "task.md"
            _write_toy_project(code_root)
            write_text(task_file, "# Task\n\nImprove the spam model without changing tests.\n")
            run_dir = root / "runs" / "code-task-run"
            initialize_code_task(run_dir=run_dir, code_root=code_root, task_file=task_file)
            generate_patch_plan(run_dir, use_llm=False)
            record_plan_decision(run_dir, decision="approve")

            fake_client = _FakeRepairClient(
                {
                    "summary": "Attempt one valid edit and one protected edit.",
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
                                "    return 'spam' if 'win' in lowered or 'prize' in lowered else 'ham'\n"
                            ),
                            "reason": "Improve source behavior.",
                        },
                        {
                            "path": "tests/test_spam_model.py",
                            "old": "        self.assertEqual(predict('win now'), 'spam')\n",
                            "new": "        self.assertEqual(predict('win now'), 'ham')\n",
                            "reason": "This protected edit should be dropped.",
                        },
                    ],
                    "validation": ["Run tests."],
                    "risks": ["Changing tests would invalidate evidence."],
                }
            )

            with patch("simple_ar.code_task.editing.patching.LLMClient.from_env", return_value=fake_client):
                result = propose_patch_edits(run_dir, use_llm=True)

            proposal = read_json(result.proposal_path)
            self.assertEqual(result.edit_count, 1)
            self.assertEqual([item["path"] for item in proposal["edits"]], ["spam_model.py"])
            self.assertIn(
                "Dropped edit for protected read-only path: tests/test_spam_model.py",
                proposal["warnings"],
            )

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

    def test_code_task_apply_cli_reports_validation_errors_without_traceback(self) -> None:
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

            edits_file = root / "bad_edits.json"
            write_json(
                edits_file,
                {
                    "schema_version": 1,
                    "edits": [
                        {
                            "path": "spam_model.py",
                            "old": "def missing():\n    pass\n",
                            "new": "def missing():\n    return None\n",
                            "reason": "This cannot match the workspace.",
                        }
                    ],
                },
            )

            apply_stdout = io.StringIO()
            with contextlib.redirect_stdout(apply_stdout):
                with self.assertRaises(SystemExit) as caught:
                    main(["code-task", "apply-edits", str(run_dir), "--edits-file", str(edits_file)])

            self.assertEqual(caught.exception.code, 1)
            output = apply_stdout.getvalue()
            self.assertIn("Patch validation failed; no workspace files were changed.", output)
            self.assertIn("old text was not found", output)

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

    def test_patched_regression_sets_objective_status(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "metric_project"
            task_file = root / "task.md"
            _write_metric_project(code_root, value="0.80")
            write_text(task_file, "# Task\n\nImprove the printed accuracy metric.\n")
            run_dir = root / "runs" / "code-task-run"
            initialize_code_task(
                run_dir=run_dir,
                code_root=code_root,
                task_file=task_file,
                benchmark_command="python benchmark.py",
            )
            run_code_task_baseline(run_dir, timeout_sec=10)
            manifest = read_json(run_dir / "manifest.json")
            manifest["failure_analysis"] = {
                "status": "needs_repair",
                "analysis": "code_task/run/patched/failure_analysis.md",
            }
            manifest["repair"] = {
                "status": "repair_applied",
                "repair_count": 1,
                "latest_proposed_edits": "code_task/repairs/repair-001/proposed_edits.json",
            }
            write_json(run_dir / "manifest.json", manifest)
            failure_path = run_dir / "code_task" / "run" / "patched" / "failure_analysis.md"
            write_text(failure_path, "# Failure Analysis\n\nOld failure.\n")
            write_text(run_dir / "code_task" / "workspace" / "metric_value.txt", "0.50\n")

            run_code_task_benchmark(run_dir, timeout_sec=10)

            manifest = read_json(run_dir / "manifest.json")
            self.assertEqual(manifest["status"], "objective_regressed")
            self.assertEqual(manifest["objective"]["status"], "regressed")
            self.assertEqual(manifest["failure_analysis"]["status"], "resolved")
            self.assertEqual(manifest["repair"]["status"], "benchmark_passed")
            summary = read_text(run_dir / "code_task" / "summary.md")
            self.assertIn("Outcome: `regressed`", summary)
            self.assertNotIn("## Failure Analysis", summary)
            self.assertNotIn("## Repair", summary)

    def test_manual_validate_and_run_sync_latest_batch_state(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "toy_project"
            task_file = root / "task.md"
            _write_toy_project(code_root)
            write_text(task_file, "# Task\n\nDetect prize messages as spam.\n")
            run_dir = root / "runs" / "code-task-run"
            initialize_code_task(
                run_dir=run_dir,
                code_root=code_root,
                task_file=task_file,
                benchmark_command="python -m unittest discover -s tests",
            )
            generate_code_task_work_plan(run_dir, use_llm=False)
            create_code_task_batch(run_dir, work_item_id="W1")
            generate_patch_plan(run_dir, use_llm=False)
            record_plan_decision(run_dir, decision="approve")
            apply_patch_edits(run_dir, edits_file=_write_valid_edit_proposal(run_dir))

            validate_code_task(run_dir)
            batch_path = (
                run_dir
                / "code_task"
                / "attempts"
                / "attempt-001"
                / "batches"
                / "batch-001"
                / "batch_state.json"
            )
            self.assertEqual(read_json(batch_path)["state"], "validating")
            run_code_task_benchmark(run_dir, timeout_sec=10)

            self.assertEqual(read_json(batch_path)["state"], "completed")
            attempt = read_json(run_dir / "code_task" / "attempts" / "attempt-001" / "attempt_state.json")
            self.assertEqual(attempt["state"], "completed")
            manifest = read_json(run_dir / "manifest.json")
            self.assertEqual(manifest["work_plan"]["status"], "completed")

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
            self.assertIn("Primary metric:", output)
            self.assertIn("macro_f1", output)
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

    def test_code_task_config_rejects_wrong_section_types(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            config_file = root / "code_task.toml"
            write_text(
                config_file,
                (
                    "[code_task]\n"
                    f'code_root = "{root.as_posix()}"\n'
                    "task_file = [\"not\", \"a\", \"path\"]\n"
                ),
            )

            with self.assertRaises(CodeTaskConfigError) as raised:
                load_code_task_init_options(config_path=str(config_file))

            self.assertIn("Invalid code-task config", str(raised.exception))
            self.assertIn("task_file", str(raised.exception))

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
            self.assertEqual(analysis.source, "benchmark")
            analysis_text = read_text(analysis.analysis_path)
            self.assertIn("# Failure Analysis", analysis_text)
            self.assertIn("AssertionError", analysis_text)

            repair = propose_repair_edits(run_dir, use_llm=False)
            self.assertEqual(repair.mode, "offline")
            self.assertEqual(repair.edit_count, 0)
            self.assertTrue((repair.repair_dir / "proposed_edits.json").is_file())
            manifest = read_json(run_dir / "manifest.json")
            self.assertEqual(manifest["repair"]["status"], "repair_proposed")
            self.assertIn("## Repair", read_text(run_dir / "code_task" / "summary.md"))

    def test_failure_analysis_prefers_runtime_stderr_over_validation_warning(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "toy_project"
            task_file = root / "task.md"
            _write_toy_project(code_root)
            write_text(task_file, "# Task\n\nDiagnose runtime stderr before validation warnings.\n")
            run_dir = root / "runs" / "code-task-run"
            initialize_code_task(
                run_dir=run_dir,
                code_root=code_root,
                task_file=task_file,
                benchmark_command="python broken_runtime.py",
            )
            write_text(
                run_dir / "code_task" / "workspace" / "broken_runtime.py",
                (
                    "import sys\n"
                    "print(\"Experiment failed: 'str' object has no attribute 'X'\", file=sys.stderr)\n"
                    "raise SystemExit(1)\n"
                ),
            )
            failed = run_code_task_benchmark(run_dir, timeout_sec=10, skip_validation=True)
            self.assertEqual(failed.status, "failed")

            analysis = analyze_code_task_failure(run_dir)

            self.assertEqual(analysis.status, "needs_repair")
            analysis_text = read_text(analysis.analysis_path)
            self.assertIn("Experiment failed: 'str' object has no attribute 'X'", analysis_text)
            self.assertNotIn("strongest error signal is: `warning", analysis_text)

    def test_execute_runs_to_approval_gate(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "toy_project"
            task_file = root / "task.md"
            _write_toy_project(code_root)
            write_text(task_file, "# Task\n\nImprove the spam classifier.\n")
            run_dir = root / "runs" / "code-task-run"
            initialize_code_task(
                run_dir=run_dir,
                code_root=code_root,
                task_file=task_file,
                benchmark_command="python -m unittest discover -s tests",
            )

            result = execute_code_task(run_dir, use_llm=False, timeout_sec=10)

            self.assertEqual(result.stop_reason, "approval_required")
            self.assertTrue((run_dir / "code_task" / "meta" / "environment_report.json").is_file())
            self.assertTrue((run_dir / "code_task" / "run" / "baseline" / "execution_report.json").is_file())
            self.assertTrue((run_dir / "code_task" / "work_plan.json").is_file())
            self.assertTrue(
                (
                    run_dir
                    / "code_task"
                    / "attempts"
                    / "attempt-001"
                    / "batches"
                    / "batch-001"
                    / "batch_state.json"
                ).is_file()
            )
            self.assertTrue((run_dir / "code_task" / "patch_plan.md").is_file())
            self.assertFalse((run_dir / "code_task" / "meta" / "proposed_edits.json").exists())
            self.assertEqual(
                [(step.step, step.status) for step in result.steps[-5:]],
                [
                    ("probe", "done"),
                    ("baseline", "done"),
                    ("work-plan", "done"),
                    ("batch", "done"),
                    ("plan", "done"),
                ],
            )

    def test_execute_blocks_on_llm_work_plan_failure_without_fallback(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "toy_project"
            task_file = root / "task.md"
            _write_toy_project(code_root)
            write_text(task_file, "# Task\n\nImprove the spam classifier with LLM planning.\n")
            run_dir = root / "runs" / "code-task-run"
            initialize_code_task(
                run_dir=run_dir,
                code_root=code_root,
                task_file=task_file,
                benchmark_command="python -m unittest discover -s tests",
            )
            fake_client = _FailingCodeTaskClient()

            with patch("simple_ar.code_task.editing.work_plan.LLMClient.from_env", return_value=fake_client):
                result = execute_code_task(
                    run_dir,
                    use_llm=True,
                    to_step="work-plan",
                    timeout_sec=10,
                    llm_retry_attempts=2,
                )

            self.assertEqual(result.stop_reason, "llm_planning_failed")
            self.assertEqual(result.steps[-1].step, "work-plan")
            self.assertEqual(result.steps[-1].status, "blocked")
            self.assertIn("LLM work planning failed", result.steps[-1].detail)
            self.assertEqual(fake_client.calls, 2)
            self.assertFalse((run_dir / "code_task" / "work_plan.json").exists())
            self.assertFalse((run_dir / "code_task" / "work_plan.md").exists())

    def test_execute_uses_planning_fallback_only_when_explicitly_allowed(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "toy_project"
            task_file = root / "task.md"
            _write_toy_project(code_root)
            write_text(task_file, "# Task\n\nImprove the spam classifier with fallback allowed.\n")
            run_dir = root / "runs" / "code-task-run"
            initialize_code_task(
                run_dir=run_dir,
                code_root=code_root,
                task_file=task_file,
                benchmark_command="python -m unittest discover -s tests",
            )
            fake_client = _FailingCodeTaskClient()

            with patch("simple_ar.code_task.editing.work_plan.LLMClient.from_env", return_value=fake_client):
                result = execute_code_task(
                    run_dir,
                    use_llm=True,
                    to_step="work-plan",
                    timeout_sec=10,
                    allow_planning_fallback=True,
                    llm_retry_attempts=2,
                )

            self.assertEqual(result.stop_reason, "stop_point")
            self.assertEqual(result.steps[-1].step, "work-plan")
            self.assertEqual(result.steps[-1].status, "done")
            self.assertEqual(fake_client.calls, 2)
            work_plan = read_json(run_dir / "code_task" / "work_plan.json")
            self.assertEqual(work_plan["mode"], "offline")

    def test_execute_blocks_on_llm_patch_plan_failure_without_fallback(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "toy_project"
            task_file = root / "task.md"
            _write_toy_project(code_root)
            write_text(task_file, "# Task\n\nImprove the spam classifier patch plan.\n")
            run_dir = root / "runs" / "code-task-run"
            initialize_code_task(
                run_dir=run_dir,
                code_root=code_root,
                task_file=task_file,
                benchmark_command="python -m unittest discover -s tests",
            )
            first = execute_code_task(run_dir, use_llm=False, to_step="batch", timeout_sec=10)
            self.assertEqual(first.stop_reason, "stop_point")
            fake_client = _FailingCodeTaskClient()

            with patch("simple_ar.code_task.editing.planning.LLMClient.from_env", return_value=fake_client):
                result = execute_code_task(
                    run_dir,
                    use_llm=True,
                    to_step="plan",
                    timeout_sec=10,
                    llm_retry_attempts=2,
                )

            self.assertEqual(result.stop_reason, "llm_planning_failed")
            self.assertEqual(result.steps[-1].step, "plan")
            self.assertEqual(result.steps[-1].status, "blocked")
            self.assertIn("LLM patch planning failed", result.steps[-1].detail)
            self.assertEqual(fake_client.calls, 2)
            self.assertFalse((run_dir / "code_task" / "patch_plan.md").exists())

    def test_execute_can_skip_expensive_baseline(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "toy_project"
            task_file = root / "task.md"
            _write_toy_project(code_root)
            write_text(task_file, "# Task\n\nSkip unchanged baseline for an expensive task.\n")
            run_dir = root / "runs" / "code-task-run"
            initialize_code_task(
                run_dir=run_dir,
                code_root=code_root,
                task_file=task_file,
                benchmark_command="python -m unittest discover -s tests",
            )

            result = execute_code_task(
                run_dir,
                use_llm=False,
                to_step="baseline",
                baseline_policy="skip",
            )

            self.assertEqual(result.stop_reason, "stop_point")
            self.assertFalse((run_dir / "code_task" / "run" / "baseline" / "execution_report.json").exists())
            manifest = read_json(run_dir / "manifest.json")
            self.assertEqual(manifest["benchmark"]["baseline_policy"]["policy"], "skip")
            self.assertEqual(manifest["benchmark"]["baseline_policy"]["status"], "skipped")
            summary = read_text(run_dir / "code_task" / "summary.md")
            self.assertIn("Baseline policy", summary)
            self.assertIn("skip", summary)

    def test_execute_can_record_provided_baseline_metrics(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "toy_project"
            task_file = root / "task.md"
            metrics_file = root / "baseline_metrics.json"
            _write_toy_project(code_root)
            write_text(task_file, "# Task\n\nUse existing baseline evidence.\n")
            write_json(metrics_file, {"accuracy": 0.75, "loss": 1.25})
            run_dir = root / "runs" / "code-task-run"
            initialize_code_task(
                run_dir=run_dir,
                code_root=code_root,
                task_file=task_file,
                benchmark_command="python -m unittest discover -s tests",
            )

            result = execute_code_task(
                run_dir,
                use_llm=False,
                to_step="baseline",
                baseline_policy="provided",
                baseline_metrics_file=metrics_file,
            )

            self.assertEqual(result.stop_reason, "stop_point")
            report = read_json(run_dir / "code_task" / "run" / "baseline" / "execution_report.json")
            self.assertTrue(report["provided_baseline"])
            self.assertEqual(report["metric_values"]["accuracy"], 0.75)
            manifest = read_json(run_dir / "manifest.json")
            baseline = manifest["benchmark"]["runs"]["baseline"]
            self.assertTrue(baseline["provided"])
            self.assertEqual(manifest["benchmark"]["baseline_policy"]["policy"], "provided")

    def test_execute_dry_run_has_no_side_effects(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "toy_project"
            task_file = root / "task.md"
            _write_toy_project(code_root)
            write_text(task_file, "# Task\n\nPreview orchestration.\n")
            run_dir = root / "runs" / "code-task-run"
            initialize_code_task(
                run_dir=run_dir,
                code_root=code_root,
                task_file=task_file,
                benchmark_command="python -m unittest discover -s tests",
            )

            result = execute_code_task(run_dir, dry_run=True, use_llm=False)

            self.assertEqual(result.stop_reason, "dry_run")
            self.assertEqual(result.steps[-1].status, "would_run")
            self.assertEqual(result.steps[-1].step, "probe")
            self.assertFalse((run_dir / "code_task" / "meta" / "environment_report.json").exists())

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                main(["code-task", "execute", str(run_dir), "--dry-run", "--no-llm"])
            output = stdout.getvalue()
            self.assertIn("Stop reason", output)
            self.assertIn("dry_run", output)
            self.assertIn("probe", output)
            self.assertIn("would_run", output)

    def test_execute_cli_reads_runtime_config(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "toy_project"
            task_file = root / "task.md"
            config_file = root / "execute.toml"
            _write_toy_project(code_root)
            write_text(task_file, "# Task\n\nRun configured execute.\n")
            write_text(
                config_file,
                (
                    "[execute]\n"
                    'to_step = "baseline"\n'
                    "use_llm = false\n"
                    "timeout_sec = 10\n"
                    "max_files = 3\n"
                    "max_source_chars_per_file = 900\n"
                    "\n"
                    "[models.code_task]\n"
                    'planner = "planner-model"\n'
                    'editor = "editor-model"\n'
                    'repair = "repair-model"\n'
                    "\n"
                    "[budget]\n"
                    'profile = "normal"\n'
                    "max_batches = 2\n"
                    "cost_cap_usd = 1.0\n"
                    "\n"
                    "[budget.normal]\n"
                    "max_edits = 3\n"
                    "\n"
                    "[environment]\n"
                    'mode = "current"\n'
                ),
            )
            run_dir = root / "runs" / "code-task-run"
            initialize_code_task(
                run_dir=run_dir,
                code_root=code_root,
                task_file=task_file,
                benchmark_command="python -m unittest discover -s tests",
            )

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                main(["code-task", "execute", str(run_dir), "--config", str(config_file)])

            output = stdout.getvalue()
            self.assertIn("Stop reason", output)
            self.assertIn("stop_point", output)
            self.assertIn("baseline", output)
            self.assertIn("done", output)
            self.assertFalse((run_dir / "code_task" / "work_plan.json").exists())

    def test_execute_interactive_skips_completed_steps_without_prompting(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "toy_project"
            task_file = root / "task.md"
            _write_toy_project(code_root)
            write_text(task_file, "# Task\n\nResume already completed execute steps.\n")
            run_dir = root / "runs" / "code-task-run"
            initialize_code_task(
                run_dir=run_dir,
                code_root=code_root,
                task_file=task_file,
                benchmark_command="python -m unittest discover -s tests",
            )
            result = execute_code_task(run_dir, to_step="baseline", use_llm=False, timeout_sec=10)
            self.assertEqual(result.stop_reason, "stop_point")

            stdout = io.StringIO()
            with patch("simple_ar.cli.main.confirm_next_step") as confirm:
                with contextlib.redirect_stdout(stdout):
                    main(["code-task", "execute", str(run_dir), "--to-step", "baseline", "--interactive", "--no-llm"])

            confirm.assert_not_called()
            output = stdout.getvalue()
            self.assertIn("probe", output)
            self.assertIn("baseline", output)
            self.assertIn("skipped", output)

    def test_execute_inline_review_can_approve_plan_and_continue(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "toy_project"
            task_file = root / "task.md"
            _write_toy_project(code_root)
            write_text(task_file, "# Task\n\nApprove plan inline and continue.\n")
            run_dir = root / "runs" / "code-task-run"
            initialize_code_task(
                run_dir=run_dir,
                code_root=code_root,
                task_file=task_file,
                benchmark_command="python -m unittest discover -s tests",
            )

            stdout = io.StringIO()
            with (
                patch("sys.stdin.isatty", return_value=True),
                patch("simple_ar.cli.main.confirm_review_gate", return_value=True) as confirm,
                contextlib.redirect_stdout(stdout),
            ):
                main(
                    [
                        "code-task",
                        "execute",
                        str(run_dir),
                        "--to-step",
                        "propose-edits",
                        "--no-llm",
                    ]
                )

            confirm.assert_called_once()
            output = stdout.getvalue()
            self.assertIn("Patch Plan Review", output)
            self.assertTrue((run_dir / "code_task" / "meta" / "proposed_edits.json").is_file())
            manifest = read_json(run_dir / "manifest.json")
            self.assertEqual(manifest["plan"]["status"], "approved")

    def test_execute_applies_reviewed_proposal_after_approval(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "toy_project"
            task_file = root / "task.md"
            _write_toy_project(code_root)
            write_text(task_file, "# Task\n\nAdd another spam keyword.\n")
            run_dir = root / "runs" / "code-task-run"
            initialize_code_task(
                run_dir=run_dir,
                code_root=code_root,
                task_file=task_file,
                benchmark_command="python -m unittest discover -s tests",
            )
            first = execute_code_task(run_dir, use_llm=False, timeout_sec=10)
            self.assertEqual(first.stop_reason, "approval_required")
            record_plan_decision(run_dir, decision="approve")
            _write_valid_edit_proposal(run_dir)

            result = execute_code_task(
                run_dir,
                use_llm=False,
                timeout_sec=10,
                apply_proposed_edits=True,
            )

            self.assertEqual(result.stop_reason, "completed")
            self.assertTrue((run_dir / "code_task" / "patch.diff").is_file())
            self.assertTrue((run_dir / "code_task" / "run" / "patched" / "execution_report.json").is_file())
            self.assertIn("'prize'", read_text(run_dir / "code_task" / "workspace" / "spam_model.py"))
            step_status = [(step.step, step.status) for step in result.steps]
            self.assertIn(("apply-edits", "done"), step_status)
            self.assertIn(("validate", "done"), step_status)
            self.assertIn(("run", "done"), step_status)
            batch_state = read_json(
                run_dir
                / "code_task"
                / "attempts"
                / "attempt-001"
                / "batches"
                / "batch-001"
                / "batch_state.json"
            )
            self.assertEqual(batch_state["state"], "completed")
            self.assertEqual(batch_state["validation_status"], "passed")
            self.assertEqual(batch_state["benchmark_status"], "passed")
            attempt_state = read_json(
                run_dir / "code_task" / "attempts" / "attempt-001" / "attempt_state.json"
            )
            self.assertEqual(attempt_state["state"], "completed")
            self.assertEqual(attempt_state["batches"][0]["state"], "completed")
            manifest = read_json(run_dir / "manifest.json")
            self.assertEqual(manifest["work_plan"]["status"], "completed")

    def test_execute_generates_repair_proposal_after_failed_run(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "toy_project"
            task_file = root / "task.md"
            _write_toy_project(code_root)
            write_text(task_file, "# Task\n\nBreak then repair the spam classifier.\n")
            run_dir = root / "runs" / "code-task-run"
            initialize_code_task(
                run_dir=run_dir,
                code_root=code_root,
                task_file=task_file,
                benchmark_command="python -m unittest discover -s tests",
            )
            first = execute_code_task(run_dir, use_llm=False, timeout_sec=10)
            self.assertEqual(first.stop_reason, "approval_required")
            record_plan_decision(run_dir, decision="approve")
            _write_failing_edit_proposal(run_dir)

            result = execute_code_task(
                run_dir,
                use_llm=False,
                timeout_sec=10,
                apply_proposed_edits=True,
                to_step="repair",
                repair_rounds=1,
            )

            self.assertEqual(result.stop_reason, "repair_review_required")
            self.assertTrue((run_dir / "code_task" / "run" / "patched" / "failure_analysis.md").is_file())
            self.assertTrue((run_dir / "code_task" / "repairs" / "repair-001" / "proposed_edits.json").is_file())
            repair_batch = read_json(
                run_dir
                / "code_task"
                / "attempts"
                / "attempt-001"
                / "batches"
                / "batch-002"
                / "batch_state.json"
            )
            self.assertEqual(repair_batch["kind"], "repair")
            self.assertEqual(repair_batch["parent_batch_id"], "batch-001")
            self.assertIn("repair_proposal", repair_batch["artifacts"])
            attempt_state = read_json(
                run_dir / "code_task" / "attempts" / "attempt-001" / "attempt_state.json"
            )
            self.assertEqual(attempt_state["state"], "failed")
            self.assertEqual(attempt_state["batches"][0]["state"], "failed")
            self.assertEqual(attempt_state["batches"][1]["kind"], "repair")
            step_status = [(step.step, step.status) for step in result.steps]
            self.assertIn(("analyze-failure", "done"), step_status)
            self.assertIn(("repair", "done"), step_status)

    def test_execute_reports_patch_apply_failure_without_traceback(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "toy_project"
            task_file = root / "task.md"
            _write_toy_project(code_root)
            write_text(task_file, "# Task\n\nHandle an invalid proposal.\n")
            run_dir = root / "runs" / "code-task-run"
            initialize_code_task(
                run_dir=run_dir,
                code_root=code_root,
                task_file=task_file,
                benchmark_command="python -m unittest discover -s tests",
            )
            execute_code_task(run_dir, use_llm=False, timeout_sec=10)
            record_plan_decision(run_dir, decision="approve")
            write_json(
                run_dir / "code_task" / "meta" / "proposed_edits.json",
                {
                    "edits": [
                        {
                            "path": "spam_model.py",
                            "old": "text that is not in the file",
                            "new": "replacement",
                            "reason": "invalid proposal",
                        }
                    ]
                },
            )

            result = execute_code_task(
                run_dir,
                use_llm=False,
                timeout_sec=10,
                apply_proposed_edits=True,
            )

            self.assertEqual(result.stop_reason, "patch_apply_failed")
            self.assertEqual(result.steps[-1].step, "apply-edits")
            self.assertEqual(result.steps[-1].status, "blocked")
            self.assertIn("old text was not found", result.steps[-1].detail)

    def test_analyze_validation_failure_without_benchmark_run(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "toy_project"
            task_file = root / "task.md"
            _write_toy_project(code_root)
            write_text(task_file, "# Task\n\nRepair a syntax error before running tests.\n")
            run_dir = root / "runs" / "code-task-run"
            initialize_code_task(
                run_dir=run_dir,
                code_root=code_root,
                task_file=task_file,
                benchmark_command="python -m unittest discover -s tests",
            )
            write_text(run_dir / "code_task" / "workspace" / "spam_model.py", "def broken(:\n")
            validation = validate_code_task(run_dir)
            self.assertEqual(validation.status, "failed")

            analysis = analyze_code_task_failure(run_dir)

            self.assertEqual(analysis.status, "needs_repair")
            self.assertEqual(analysis.source, "validation")
            self.assertEqual(analysis.analysis_path.name, "failure_analysis.md")
            self.assertIn("spam_model.py", analysis.implicated_files)
            analysis_text = read_text(analysis.analysis_path)
            self.assertIn("Static validation failed", analysis_text)

            repair = propose_repair_edits(run_dir, use_llm=False)
            self.assertEqual(repair.mode, "offline")
            proposal = read_json(repair.proposal_path)
            self.assertEqual(proposal["source_analysis"], "code_task/meta/failure_analysis.md")
            self.assertIn("spam_model.py", proposal["selected_files"])

    def test_repair_proposal_drops_edits_outside_selected_context(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "toy_project"
            task_file = root / "task.md"
            _write_toy_project(code_root)
            write_text(code_root / "extra.py", "VALUE = 1\n")
            write_text(task_file, "# Task\n\nRepair the broken spam classifier.\n")
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
            analyze_code_task_failure(run_dir)

            fake_client = _FakeRepairClient(
                {
                    "summary": "Attempt to repair an unrelated file.",
                    "edits": [
                        {
                            "path": "extra.py",
                            "old": "VALUE = 1\n",
                            "new": "VALUE = 2\n",
                            "reason": "This is outside the selected repair context.",
                        }
                    ],
                    "validation": ["Would need tests."],
                    "risks": ["Unrelated edit."],
                }
            )

            with patch("simple_ar.code_task.execution.repair.LLMClient.from_env", return_value=fake_client):
                repair = propose_repair_edits(run_dir, use_llm=True, max_files=1)

            proposal = read_json(repair.proposal_path)
            self.assertEqual(proposal["mode"], "llm")
            self.assertEqual(proposal["edits"], [])
            self.assertIn("spam_model.py", proposal["selected_files"])
            self.assertIn("Dropped edit outside repair context: extra.py", proposal["warnings"])

    def test_repair_proposal_drops_diff_marker_edits(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "toy_project"
            task_file = root / "task.md"
            _write_toy_project(code_root)
            write_text(task_file, "# Task\n\nRepair the broken spam classifier.\n")
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
            analyze_code_task_failure(run_dir)

            fake_client = _FakeRepairClient(
                {
                    "summary": "Accidentally return a diff hunk.",
                    "edits": [
                        {
                            "path": "spam_model.py",
                            "old": (
                                "-def predict(text):\n"
                                "-    return 'ham'\n"
                                "+def predict(text):\n"
                                "+    return 'spam'\n"
                            ),
                            "new": (
                                "def predict(text):\n"
                                "    return 'spam'\n"
                            ),
                            "reason": "The model should not put diff markers in old.",
                        }
                    ],
                    "validation": ["python -m unittest discover -s tests"],
                    "risks": [],
                }
            )

            with patch("simple_ar.code_task.execution.repair.LLMClient.from_env", return_value=fake_client):
                repair = propose_repair_edits(run_dir, use_llm=True)

            proposal = read_json(repair.proposal_path)
            self.assertEqual(proposal["edits"], [])
            self.assertIn(
                "Dropped edit for spam_model.py: old/new must be exact text, not a diff fragment.",
                proposal["warnings"],
            )

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


def _write_analysis_first_work_plan(run_dir: Path) -> None:
    work_plan = {
        "schema_version": 1,
        "generated_at": "2026-01-01T00:00:00+00:00",
        "mode": "test",
        "summary": "Analysis item appears before the actual implementation item.",
        "goal": "Improve spam prediction.",
        "success_criteria": ["Tests pass after a useful code change."],
        "items": [
            {
                "id": "W1",
                "status": "pending",
                "objective": "Inspect current behavior and identify candidate improvements.",
                "target_files": ["spam_model.py"],
                "read_only_evidence": ["tests/test_spam_model.py"],
                "depends_on": [],
                "validation": ["python -m unittest discover -s tests"],
                "done_criteria": ["Behavior is understood."],
                "risk": "Low",
                "parallelizable": False,
                "budget_profile": "normal",
                "requires_budget_override": False,
                "suggested_budget_override": "",
                "context_request": {"query": "inspect", "files": ["spam_model.py"], "symbols": []},
            },
            {
                "id": "W2",
                "status": "pending",
                "objective": "Implement keyword handling improvement in the classifier.",
                "target_files": ["spam_model.py"],
                "read_only_evidence": ["tests/test_spam_model.py"],
                "depends_on": ["W1"],
                "validation": ["python -m unittest discover -s tests"],
                "done_criteria": ["The classifier recognizes the requested keyword."],
                "risk": "Low",
                "parallelizable": False,
                "budget_profile": "normal",
                "requires_budget_override": False,
                "suggested_budget_override": "",
                "context_request": {"query": "implement", "files": ["spam_model.py"], "symbols": []},
            },
        ],
        "context_requests": [],
        "risks": [],
        "approval": {"required": True, "status": "pending", "reason": "Review before editing."},
        "selected_files": ["spam_model.py", "tests/test_spam_model.py"],
        "context_pack": None,
        "run_context": {},
        "budget_profiles": {},
    }
    write_json(run_dir / "code_task" / "work_plan.json", work_plan)
    write_text(run_dir / "code_task" / "work_plan.md", "# Work Plan\n")
    manifest = read_json(run_dir / "manifest.json")
    manifest["layout"]["work_plan"] = "code_task/work_plan.json"
    manifest["layout"]["work_plan_markdown"] = "code_task/work_plan.md"
    manifest["work_plan"] = {
        "status": "pending_approval",
        "mode": "test",
        "path": "code_task/work_plan.json",
        "markdown": "code_task/work_plan.md",
        "item_count": 2,
        "selected_files": ["spam_model.py", "tests/test_spam_model.py"],
        "context_pack": None,
        "approval": work_plan["approval"],
    }
    write_json(run_dir / "manifest.json", manifest)


def _write_dependent_work_plan(run_dir: Path) -> None:
    work_plan = {
        "schema_version": 1,
        "generated_at": "2026-01-01T00:00:00+00:00",
        "mode": "test",
        "summary": "Three tightly coupled implementation items.",
        "goal": "Implement a feature, scorer, and config change together.",
        "success_criteria": ["The coupled implementation passes benchmark validation."],
        "items": [
            {
                "id": "W1",
                "status": "pending",
                "objective": "Add the feature producer.",
                "target_files": ["spam_model.py"],
                "read_only_evidence": ["tests/test_spam_model.py"],
                "depends_on": [],
                "validation": ["python -m unittest discover -s tests"],
                "done_criteria": ["The producer emits the new feature."],
                "risk": "Producer must remain backward compatible.",
                "parallelizable": False,
                "budget_profile": "normal",
                "requires_budget_override": False,
                "suggested_budget_override": "",
                "context_request": {"query": "producer", "files": ["spam_model.py"], "symbols": []},
            },
            {
                "id": "W2",
                "status": "pending",
                "objective": "Use the feature in scoring.",
                "target_files": ["extra.py"],
                "read_only_evidence": ["tests/test_spam_model.py"],
                "depends_on": ["W1"],
                "validation": ["python -m unittest discover -s tests"],
                "done_criteria": ["The scorer consumes the new feature."],
                "risk": "Scoring can drift if the feature is absent.",
                "parallelizable": False,
                "budget_profile": "normal",
                "requires_budget_override": False,
                "suggested_budget_override": "",
                "context_request": {"query": "scorer", "files": ["extra.py"], "symbols": []},
            },
            {
                "id": "W3",
                "status": "pending",
                "objective": "Enable the new behavior in configuration.",
                "target_files": ["pyproject.toml"],
                "read_only_evidence": ["tests/test_spam_model.py"],
                "depends_on": ["W2"],
                "validation": ["python -m unittest discover -s tests"],
                "done_criteria": ["The default config enables the feature."],
                "risk": "Config changes should be additive.",
                "parallelizable": False,
                "budget_profile": "normal",
                "requires_budget_override": False,
                "suggested_budget_override": "",
                "context_request": {"query": "config", "files": ["pyproject.toml"], "symbols": []},
            },
        ],
        "context_requests": [],
        "risks": [],
        "approval": {"required": True, "status": "pending", "reason": "Review before editing."},
        "selected_files": ["spam_model.py", "extra.py", "pyproject.toml", "tests/test_spam_model.py"],
        "context_pack": None,
        "run_context": {},
        "budget_profiles": {},
    }
    write_json(run_dir / "code_task" / "work_plan.json", work_plan)
    write_text(run_dir / "code_task" / "work_plan.md", "# Work Plan\n")
    manifest = read_json(run_dir / "manifest.json")
    manifest["layout"]["work_plan"] = "code_task/work_plan.json"
    manifest["layout"]["work_plan_markdown"] = "code_task/work_plan.md"
    manifest["work_plan"] = {
        "status": "pending_approval",
        "mode": "test",
        "path": "code_task/work_plan.json",
        "markdown": "code_task/work_plan.md",
        "item_count": 3,
        "selected_files": work_plan["selected_files"],
        "context_pack": None,
        "approval": work_plan["approval"],
    }
    write_json(run_dir / "manifest.json", manifest)


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


def _git(cwd: Path, *args: str) -> None:
    completed = subprocess.run(
        ["git", "-c", "safe.directory=*", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    if completed.returncode != 0:
        raise AssertionError(
            f"git {' '.join(args)} failed: {completed.stderr or completed.stdout}"
        )


class _FakeRepairClient:
    def __init__(self, response: dict[str, object]) -> None:
        self._response = response

    def ask_json(self, system: str, user: str, *, label: str = "") -> dict[str, object]:
        return self._response


class _FakeCodeTaskClient:
    def ask_json(self, system: str, user: str, *, label: str = "") -> dict[str, object]:
        if label.startswith("code-task-review-"):
            return {"findings": []}
        if label == "code-task-work-plan":
            return {
                "summary": "Improve prize-message classification in one small batch.",
                "goal": "Classify prize messages as spam without changing tests.",
                "success_criteria": ["Benchmark passes after the patch."],
                "items": [
                    {
                        "id": "W1",
                        "objective": "Implement a small keyword handling improvement.",
                        "target_files": ["spam_model.py"],
                        "read_only_evidence": ["tests/test_spam_model.py"],
                        "depends_on": [],
                        "validation": ["python -m unittest discover -s tests"],
                        "done_criteria": ["Prediction handles prize messages."],
                        "risk": "Low.",
                        "parallelizable": False,
                        "budget_profile": "normal",
                        "requires_budget_override": False,
                        "suggested_budget_override": "",
                        "context_request": {"query": "predict prize", "files": ["spam_model.py"], "symbols": ["predict"]},
                    }
                ],
                "context_requests": [],
                "risks": [],
                "approval": {"required": True, "reason": "Review before editing."},
            }
        if label == "code-task-plan":
            return {
                "summary": "Patch the classifier keyword logic.",
                "goals": ["Handle prize messages as spam."],
                "files_to_modify": [
                    {
                        "path": "spam_model.py",
                        "reason": "Contains predict keyword logic.",
                        "change_type": "modify",
                    }
                ],
                "new_files": [],
                "proposed_steps": ["Update predict keyword check."],
                "validation": ["python -m unittest discover -s tests"],
                "risks": ["Keep API stable."],
                "rollback": ["Discard workspace changes."],
                "open_questions": [],
                "requires_approval_before_patch": True,
            }
        if label == "code-task-propose-edits":
            return {
                "summary": "Add prize keyword support.",
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
                        "reason": "Classify prize messages as spam.",
                    }
                ],
                "validation": ["python -m unittest discover -s tests"],
                "risks": [],
            }
        raise AssertionError(f"Unexpected LLM label: {label}")


class _FailingCodeTaskClient:
    def __init__(self) -> None:
        self.calls = 0

    def ask_json(self, system: str, user: str, *, label: str = "") -> dict[str, object]:
        self.calls += 1
        raise LLMError("LLM response did not contain a JSON object")


def _indexed_file(index: dict[str, object], path: str) -> dict[str, object]:
    files = index.get("files", [])
    if isinstance(files, list):
        for item in files:
            if isinstance(item, dict) and item.get("path") == path:
                return item
    raise AssertionError(f"Missing indexed file: {path}")


if __name__ == "__main__":
    unittest.main()
