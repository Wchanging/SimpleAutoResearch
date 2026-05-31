from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from simple_ar.core.artifacts import read_json, write_json
from simple_ar.cli import _resume_config, main


TEST_ROOT = Path(__file__).resolve().parents[1] / ".tmp_tests"


class CliTests(unittest.TestCase):
    def test_resume_uses_pipeline_state_and_status_reports_progress(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            output_root = Path(tmp) / "runs"

            with contextlib.redirect_stdout(io.StringIO()):
                main(
                    [
                        "run",
                        "--topic",
                        "toy topic",
                        "--to-stage",
                        "plan",
                        "--output-root",
                        str(output_root),
                        "--no-llm",
                        "--offline-search",
                        "--quiet",
                    ]
                )

            run_dir = next(output_root.iterdir())
            state = read_json(run_dir / "pipeline_state.json")
            self.assertEqual(state["next_stage"], "search")

            with contextlib.redirect_stdout(io.StringIO()):
                main(
                    [
                        "resume",
                        str(run_dir),
                        "--to-stage",
                        "search",
                        "--no-llm",
                        "--offline-search",
                        "--quiet",
                    ]
                )

            state = read_json(run_dir / "pipeline_state.json")
            self.assertEqual(state["last_stage"], "search")
            self.assertEqual(state["next_stage"], "read")

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                main(["status", str(run_dir)])

            status_text = stdout.getvalue()
            self.assertIn("Pipeline: done", status_text)
            self.assertIn("01 plan: done", status_text)
            self.assertIn("02 search: done", status_text)
            self.assertIn("03 read: pending", status_text)

    def test_inspect_and_search_artifacts_commands_write_retrieval_files(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            output_root = Path(tmp) / "runs"

            with contextlib.redirect_stdout(io.StringIO()):
                main(
                    [
                        "run",
                        "--topic",
                        "toy topic",
                        "--to-stage",
                        "plan",
                        "--output-root",
                        str(output_root),
                        "--no-llm",
                        "--offline-search",
                        "--quiet",
                    ]
                )

            run_dir = next(output_root.iterdir())

            inspect_stdout = io.StringIO()
            with contextlib.redirect_stdout(inspect_stdout):
                main(["inspect", str(run_dir)])

            self.assertIn("Artifacts:", inspect_stdout.getvalue())
            self.assertTrue((run_dir / "artifact_index.json").is_file())

            search_stdout = io.StringIO()
            with contextlib.redirect_stdout(search_stdout):
                main(["search-artifacts", str(run_dir), "research", "--top-k", "2"])

            self.assertIn("Matches:", search_stdout.getvalue())
            self.assertIn("Operational metadata included: False", search_stdout.getvalue())
            self.assertTrue((run_dir / "artifact_chunks.jsonl").is_file())
            self.assertTrue((run_dir / "artifact_search_results.json").is_file())

    def test_resume_config_preserves_saved_values_without_cli_overrides(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            run_dir = Path(tmp) / "run"
            run_dir.mkdir()
            write_json(
                run_dir / "config_snapshot.json",
                {
                    "mode": "offline",
                    "model": "saved-model",
                    "llm_max_workers": 2,
                    "max_papers": 3,
                    "experiment_template": "llm_code_task_toy_spam",
                    "experiment_timeout_sec": 60,
                    "use_llm": False,
                    "use_arxiv": False,
                    "allow_fixture_fallback": True,
                    "strict_search": False,
                    "use_retrieval": True,
                    "retrieval_top_k": 7,
                },
            )
            args = SimpleNamespace(
                to_stage="report",
                model=None,
                llm_workers=None,
                max_papers=None,
                search_query=None,
                experiment_template=None,
                experiment_timeout=None,
                retrieval_top_k=None,
                report_mode=None,
                no_llm=False,
                offline_search=False,
                allow_fixture_fallback=False,
                strict_search=False,
                no_retrieval=False,
            )

            config = _resume_config(run_dir, args, "report")

            self.assertEqual(config["experiment_template"], "llm_code_task_toy_spam")
            self.assertEqual(config["experiment_timeout_sec"], 60)
            self.assertEqual(config["retrieval_top_k"], 7)
            self.assertEqual(config["use_llm"], False)
            self.assertEqual(config["use_arxiv"], False)

            args.experiment_timeout = 15
            args.no_retrieval = True
            overridden = _resume_config(run_dir, args, "report")
            self.assertEqual(overridden["experiment_timeout_sec"], 15)
            self.assertEqual(overridden["use_retrieval"], False)

    def test_run_config_can_drive_code_task_project_design(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        repo_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            output_root = root / "configured_runs"
            config_path = root / "pipeline.toml"
            config_path.write_text(
                "\n".join(
                    [
                        "[run]",
                        'topic = "configured tiny digits"',
                        f'output_root = "{output_root.as_posix()}"',
                        'to_stage = "design"',
                        "",
                        "[llm]",
                        "enabled = false",
                        "",
                        "[search]",
                        "offline = true",
                        "max_papers = 1",
                        "",
                        "[experiment]",
                        'template = "code_task_project"',
                        "timeout = 11",
                        "",
                        "[code_task]",
                        f'code_root = "{(repo_root / "examples" / "code_tasks" / "tiny_digits_mlp_project").as_posix()}"',
                        f'task_file = "{(repo_root / "examples" / "code_tasks" / "tasks" / "improve_tiny_digits_mlp.md").as_posix()}"',
                        'name = "configured-pipeline-task"',
                        "",
                        "[benchmark]",
                        'command = "python benchmark.py"',
                        'primary_metric = "accuracy"',
                        "",
                        "[benchmark.metric_directions]",
                        'accuracy = "higher"',
                        "",
                        "[workspace]",
                        'mode = "copy"',
                        'include = ["src/**", "benchmark.py"]',
                        'exclude = ["data/**"]',
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            with contextlib.redirect_stdout(io.StringIO()):
                main(["run", "--config", str(config_path), "--quiet"])

            run_dir = next(output_root.iterdir())
            snapshot = read_json(run_dir / "config_snapshot.json")
            self.assertEqual(snapshot["experiment_template"], "code_task_project")
            self.assertEqual(snapshot["experiment_timeout_sec"], 11)
            self.assertEqual(snapshot["use_llm"], False)
            self.assertEqual(snapshot["use_arxiv"], False)
            self.assertEqual(snapshot["code_task_config"], str(config_path))

            plan = read_json(run_dir / "05-design" / "experiment_plan.json")
            self.assertEqual(plan["template"], "code_task_project")
            self.assertEqual(plan["code_task"]["benchmark_command"], "python benchmark.py")
            self.assertEqual(plan["code_task"]["primary_metric"], "accuracy")
            self.assertEqual(plan["code_task"]["workspace_mode"], "copy")
            self.assertEqual(plan["code_task"]["workspace_include"], ["src/**", "benchmark.py"])
            self.assertEqual(plan["code_task"]["workspace_exclude"], ["data/**"])

    def test_code_task_init_git_worktree_error_gives_next_steps(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "no_git_project"
            code_root.mkdir()
            task_file = root / "task.md"
            task_file.write_text("# Task\n\nImprove this project.\n", encoding="utf-8")

            with self.assertRaises(SystemExit) as raised:
                main(
                    [
                        "code-task",
                        "init",
                        "--code-root",
                        str(code_root),
                        "--task-file",
                        str(task_file),
                        "--workspace-mode",
                        "git_worktree",
                        "--output-root",
                        str(root / "runs"),
                    ]
                )

            message = str(raised.exception)
            self.assertIn("Could not initialize code task", message)
            self.assertIn("git_worktree quick checklist", message)
            self.assertIn("--workspace-mode copy", message)

    def test_code_task_init_missing_task_file_gives_path_hint(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            code_root = root / "project"
            code_root.mkdir()

            with self.assertRaises(SystemExit) as raised:
                main(
                    [
                        "code-task",
                        "init",
                        "--code-root",
                        str(code_root),
                        "--task-file",
                        str(root / "missing-task.md"),
                        "--output-root",
                        str(root / "runs"),
                    ]
                )

            message = str(raised.exception)
            self.assertIn("Check the task file path", message)
            self.assertIn("[code_task].task_file", message)


if __name__ == "__main__":
    unittest.main()
