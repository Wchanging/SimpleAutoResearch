from __future__ import annotations

import tempfile
import sys
import unittest
from pathlib import Path

from simple_ar.code_task.runtime.config import (
    CodeTaskConfigError,
    load_code_task_execute_options,
    load_code_task_init_options,
)


TEST_ROOT = Path(__file__).resolve().parents[1] / ".tmp_tests"


class RunConfigTests(unittest.TestCase):
    def test_case_local_and_machine_paths_resolve_without_copying_config(self) -> None:
        from simple_ar.cli.main import _split_cli_command

        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            case = Path(tmp) / "case with spaces"
            case.mkdir()
            project = case / "project with spaces"
            data = case / "data with spaces"
            project.mkdir()
            data.mkdir()
            (case / "task.md").write_text("Task", encoding="utf-8")
            (case / "run.py").write_text("", encoding="utf-8")
            config = case / "code_task.toml"
            config.write_text(
                '[code_task]\ncode_root = "{config_dir}/project with spaces"\n'
                'task_file = "{config_dir}/task.md"\n'
                '[environment]\nmode = "external"\n'
                f'python_executable = "{Path(sys.executable).as_posix()}"\n'
                'required_paths = ["{config_dir}/data with spaces"]\n'
                '[benchmark]\n'
                "command = 'python \"{config_dir}/run.py\" --data-root \"{config_dir}/data with spaces\"'\n",
                encoding="utf-8",
            )
            options = load_code_task_init_options(config_path=str(config))

            self.assertEqual(Path(options.code_root), project)
            self.assertEqual(Path(options.task_file), case / "task.md")
            self.assertEqual(Path(options.python_executable), Path(sys.executable))
            argv = _split_cli_command(options.benchmark_command)
            self.assertEqual(argv[0], "python")
            self.assertEqual(Path(argv[1]), case / "run.py")
            self.assertEqual(argv[2], "--data-root")
            self.assertEqual(Path(argv[3]), data)

    def test_task_resource_environment_references_are_rejected(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            project = root / "project"
            project.mkdir()
            config = root / "code_task.toml"
            config.write_text(
                '[code_task]\ncode_root = "${SIMPLE_AR_CONFIG_TEST_ROOT}"\n',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(CodeTaskConfigError, "Task resource paths"):
                load_code_task_init_options(config_path=str(config), require_task_file=False)

    def test_missing_required_paths_fail_before_session(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            config = Path(tmp) / "code_task.toml"
            config.write_text(
                '[environment]\nrequired_paths = ["{config_dir}/missing-data"]\n',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(CodeTaskConfigError, "missing-data"):
                load_code_task_execute_options(config_path=str(config))

    def test_config_and_direct_execution_share_step_and_review_rules(self) -> None:
        from simple_ar.code_task.runtime.config import EXECUTE_STEPS, normalize_review_gate
        from simple_ar.code_task.orchestration.execute import execute_code_task
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "task.toml"
            for step in EXECUTE_STEPS:
                config.write_text(f'[execute]\nto_step = "{step}"\n', encoding="utf-8")
                self.assertEqual(load_code_task_execute_options(config_path=str(config)).to_step, step)
            for mode in ("strict", "full", "quality", "runtime", "safety-only", "runtime_only", "soft", "nonblocking", ""):
                config.write_text(f'[execute.ablation]\nreview_gate = "{mode}"\n', encoding="utf-8")
                self.assertEqual(load_code_task_execute_options(config_path=str(config)).review_gate,
                                 normalize_review_gate(mode))
            config.write_text('[execute.ablation]\nreview_gate = "invalid"\n', encoding="utf-8")
            with self.assertRaises(CodeTaskConfigError):
                load_code_task_execute_options(config_path=str(config))
            with self.assertRaisesRegex(ValueError, "review_gate"):
                execute_code_task(Path(tmp), review_gate="invalid")

    def test_retired_workspace_hook_is_not_silently_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "code_task.toml"
            config.write_text('[workspace]\nsetup_hook = "prepare-env"\n', encoding="utf-8")
            with self.assertRaisesRegex(CodeTaskConfigError, "setup_hook"):
                load_code_task_init_options(config_path=str(config))

    def test_retired_context_ablations_are_not_silently_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "code_task.toml"
            for field, value in (("repair_context", '"raw_logs_only"'),
                                 ("contract_context", '"minimal"'),
                                 ("use_repair_memory", "false")):
                with self.subTest(field=field):
                    config.write_text(f'[execute.ablation]\n{field} = {value}\n', encoding="utf-8")
                    with self.assertRaisesRegex(CodeTaskConfigError, field):
                        load_code_task_execute_options(config_path=str(config))

    def test_migrated_cpu_examples_use_current_code_task_configuration(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        for name, mode, baseline in (
            ("code_task_digits_mlp", "copy", "auto"),
            ("greenfield_lightweight_training", "empty", "none"),
        ):
            with self.subTest(example=name):
                base = "examples" if name == "code_task_digits_mlp" else "tests/fixtures"
                config = repo / base / name / "configs" / "code_task.toml"
                init = load_code_task_init_options(config_path=str(config))
                execute = load_code_task_execute_options(config_path=str(config))
                self.assertEqual(init.workspace_mode, mode)
                self.assertTrue((repo / init.task_file).is_file())
                self.assertEqual(execute.timeout_sec, 60)
                self.assertEqual(execute.baseline_policy, baseline)
                self.assertFalse(execute.allow_planning_fallback)
                if mode == "copy":
                    self.assertTrue((repo / init.code_root).is_dir())
                    self.assertEqual(init.edit_scope_allowed_patterns, ("digits_mlp/**",))

    def test_public_code_task_config_loads_with_expected_runtime_shape(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]

        code_task_config = (
            repo_root / "examples" / "code_task_medium_review" / "configs" / "code_task.toml"
        )
        init_options = load_code_task_init_options(config_path=str(code_task_config))
        execute_options = load_code_task_execute_options(config_path=str(code_task_config))

        self.assertEqual(init_options.name, "medium-review-pipeline")
        self.assertEqual(init_options.workspace_mode, "auto")
        self.assertIn("review_pipeline/**", init_options.edit_scope_allowed_patterns)
        self.assertTrue((repo_root / init_options.code_root).is_dir())
        self.assertTrue((repo_root / str(init_options.task_file)).is_file())
        self.assertEqual(execute_options.to_step, "run")
        self.assertEqual(execute_options.llm_retry_attempts, 2)
        self.assertFalse(execute_options.allow_planning_fallback)
        self.assertEqual(execute_options.implementation_provider, "local")


    def test_code_task_execute_options_read_shared_implementation_section(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            config = Path(tmp) / "code_task.toml"
            config.write_text(
                """
[execute]
to_step = "run"

[resource]
max_runtime_sec = 321
max_files = 12
max_generated_lines = 3456

[implementation]
provider = "fake"
agent_mode = "handoff"
allow_external_agent = true
agent_model = "small"
agent_binary = "fake-agent"
agent_args = ["--quiet"]
agent_timeout_sec = 123
""".strip(),
                encoding="utf-8",
            )

            options = load_code_task_execute_options(config_path=str(config))

            self.assertEqual(options.implementation_provider, "fake")
            self.assertEqual(options.implementation_agent_mode, "handoff")
            self.assertTrue(options.implementation_allow_external_agent)
            self.assertEqual(options.implementation_agent_model, "small")
            self.assertEqual(options.implementation_agent_binary, "fake-agent")
            self.assertEqual(options.implementation_agent_args, ("--quiet",))
            self.assertEqual(options.implementation_agent_timeout_sec, 123)
            self.assertEqual(options.timeout_sec, 321)
            self.assertEqual(options.max_files, 12)
            self.assertEqual(options.max_generated_lines, 3456)

    def test_code_task_config_normalizes_windows_style_path_separators(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            config = root / "code_task.toml"
            config.write_text(
                r"""
[code_task]
kind = "greenfield"
task_file = 'tests\fixtures\greenfield_lightweight_training\task.md'
output_root = 'runs\code-task-greenfield-ml-suite'

[environment]
python = '.venv\bin\python'
""".strip(),
                encoding="utf-8",
            )

            options = load_code_task_init_options(config_path=str(config))

            self.assertEqual(options.task_file, "tests/fixtures/greenfield_lightweight_training/task.md")
            self.assertEqual(options.output_root, "runs/code-task-greenfield-ml-suite")
            self.assertEqual(options.python_executable, ".venv/bin/python")


if __name__ == "__main__":
    unittest.main()
