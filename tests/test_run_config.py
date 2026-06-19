from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from simple_ar.app.run_config import RunConfigError, load_pipeline_run_config
from simple_ar.code_task.runtime.config import (
    load_code_task_execute_options,
    load_code_task_init_options,
)


TEST_ROOT = Path(__file__).resolve().parents[1] / ".tmp_tests"


class RunConfigTests(unittest.TestCase):
    def test_public_example_configs_load_with_expected_runtime_shape(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]

        research_config = repo_root / "examples" / "research_report" / "configs" / "research_report.toml"
        research = load_pipeline_run_config(
            str(research_config)
        )
        self.assertEqual(research["report_mode"], "research_only")
        self.assertEqual(research["research_use_fulltext"], True)
        self.assertIn("openalex", research["research_sources"])
        self.assertEqual(research["report_source_strategy"], "full")

        full_pipeline_config = (
            repo_root / "examples" / "full_pipeline_tiny_mlp" / "configs" / "pipeline.toml"
        )
        full_pipeline = load_pipeline_run_config(
            str(full_pipeline_config)
        )
        self.assertEqual(full_pipeline["task_kind"], "existing_project")
        self.assertEqual(full_pipeline["experiment_template"], "code_task_project")
        self.assertEqual(full_pipeline["implementation_task_handoff"], "merge")
        self.assertEqual(full_pipeline["code_task_config"], str(full_pipeline_config.resolve()))
        self.assertTrue(Path(str(full_pipeline["code_task_code_root"])).is_dir())
        self.assertTrue(Path(str(full_pipeline["code_task_task_file"])).is_file())

        greenfield_config = (
            repo_root
            / "examples"
            / "greenfield_lightweight_training"
            / "configs"
            / "greenfield_training.toml"
        )
        greenfield = load_pipeline_run_config(
            str(greenfield_config)
        )
        self.assertEqual(greenfield["task_kind"], "greenfield")
        self.assertEqual(greenfield["implementation_mode"], "generate_project")
        self.assertEqual(greenfield["generation_enabled"], True)
        self.assertEqual(greenfield["resource_max_files"], 10)
        self.assertIn("condition_count", greenfield["evaluation_required_metrics"])
        self.assertIn("char_ngram_accuracy", greenfield["evaluation_required_metrics"])

        code_task_config = (
            repo_root / "examples" / "code_task_medium_review" / "configs" / "code_task.toml"
        )
        init_options = load_code_task_init_options(config_path=str(code_task_config))
        execute_options = load_code_task_execute_options(config_path=str(code_task_config))

        self.assertEqual(init_options.name, "medium-review-pipeline")
        self.assertEqual(init_options.workspace_mode, "copy")
        self.assertIn("review_pipeline/**", init_options.edit_scope_allowed_patterns)
        self.assertTrue((repo_root / init_options.code_root).is_dir())
        self.assertTrue((repo_root / str(init_options.task_file)).is_file())
        self.assertEqual(execute_options.to_step, "run")
        self.assertEqual(execute_options.llm_retry_attempts, 2)
        self.assertFalse(execute_options.allow_planning_fallback)
        self.assertEqual(execute_options.implementation_provider, "local")

    def test_research_section_is_flattened_for_pipeline_config(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            config = root / "pipeline.toml"
            notes = root / "notes" / "local.md"
            notes.parent.mkdir()
            notes.write_text("# Local Evidence\n", encoding="utf-8")
            config.write_text(
                """
[run]
topic = "agent simulation"
debug_artifacts = true

[research]
mode = "strong"
planner = "llm"
sources = ["local_files", "openalex"]
queries = ["agent simulation benchmark"]
auto_query_expansion = true
max_retrieval_rounds = 3
max_queries = 6
required_facets = ["method", "benchmark", "code_link"]
use_fulltext = true
allow_pdf_download = false
max_fulltext_documents = 5
max_pdf_mb = 12
keep_raw_pdf = true
parser_backend = "basic"
cache = true
index_backend = "sqlite_fts"
index_root = ".simple_ar_cache/research_index"
local_documents = ["notes/local.md"]

[research.budget]
max_documents = 12
max_chunks = 80
max_context_tokens = 6000
max_llm_calls = 8
max_follow_up_queries = 4
novelty_backend = "local"
""".strip(),
                encoding="utf-8",
            )

            parsed = load_pipeline_run_config(str(config))

            self.assertEqual(parsed["topic"], "agent simulation")
            self.assertEqual(parsed["debug_artifacts"], True)
            self.assertEqual(parsed["research_mode"], "strong")
            self.assertEqual(parsed["research_planner"], "llm")
            self.assertEqual(parsed["research_sources"], ["local_files", "openalex"])
            self.assertEqual(parsed["research_queries"], ["agent simulation benchmark"])
            self.assertEqual(parsed["research_auto_query_expansion"], True)
            self.assertEqual(parsed["research_max_retrieval_rounds"], 3)
            self.assertEqual(parsed["research_max_queries"], 6)
            self.assertEqual(parsed["research_required_facets"], ["method", "benchmark", "code_link"])
            self.assertEqual(parsed["research_use_fulltext"], True)
            self.assertEqual(parsed["research_allow_pdf_download"], False)
            self.assertEqual(parsed["research_max_fulltext_documents"], 5)
            self.assertEqual(parsed["research_max_pdf_mb"], 12)
            self.assertEqual(parsed["research_keep_raw_pdf"], True)
            self.assertEqual(parsed["research_parser_backend"], "basic")
            self.assertEqual(parsed["research_cache"], True)
            self.assertEqual(parsed["research_index_backend"], "sqlite_fts")
            self.assertEqual(parsed["research_index_root"], ".simple_ar_cache/research_index")
            self.assertEqual(parsed["research_local_documents"], [str(notes.resolve())])
            self.assertEqual(parsed["research_max_documents"], 12)
            self.assertEqual(parsed["research_max_chunks"], 80)
            self.assertEqual(parsed["research_max_context_tokens"], 6000)
            self.assertEqual(parsed["research_max_llm_calls"], 8)
            self.assertEqual(parsed["research_max_follow_up_queries"], 4)
            self.assertEqual(parsed["research_novelty_backend"], "local")

    def test_run_config_rejects_wrong_section_types(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            config = Path(tmp) / "pipeline.toml"
            config.write_text(
                """
[run]
topic = "agent simulation"

[research]
sources = "openalex"
""".strip(),
                encoding="utf-8",
            )

            with self.assertRaises(RunConfigError) as raised:
                load_pipeline_run_config(str(config))

            self.assertIn("Invalid run config", str(raised.exception))
            self.assertIn("sources", str(raised.exception))

    def test_unified_task_sections_are_flattened_and_mapped_to_legacy_keys(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            root = Path(tmp)
            config = root / "pipeline.toml"
            code_root = root / "project"
            task_file = root / "task.md"
            code_root.mkdir()
            task_file.write_text("# Task\n", encoding="utf-8")
            config.write_text(
                """
[run]
topic = "repository-level coding agent benchmark"

[task]
kind = "existing_project"
name = "medium-code-agent"
objective = "Improve the benchmark score without changing tests."
code_root = "project"
task_file = "task.md"

[implementation]
mode = "patch_existing"
domain_profile = "code_agent_eval"
provider = "local"
agent_mode = "model"
task_handoff = "merge"
max_repair_attempts = 2

[workspace]
mode = "git_worktree"
reuse_source_venv = true
include = ["src/**"]
exclude = ["secrets/**"]

[execution]
backend = "local"
command = "uv run python benchmark.py"
timeout_sec = 240
stream_output = "auto"

[resource]
max_runtime_sec = 240
max_files = 8
max_generated_lines = 700
max_memory_mb = 2048

[evaluation]
primary_metric = "accuracy"
direction = "maximize"
required_metrics = ["accuracy", "macro_f1"]
metric_directions = { accuracy = "higher", runtime_sec = "lower" }

[generation]
enabled = true
max_batches = 2
files_per_batch = 3
allow_fallback_scaffold = true
""".strip(),
                encoding="utf-8",
            )

            parsed = load_pipeline_run_config(str(config))

            self.assertEqual(parsed["task_kind"], "existing_project")
            self.assertEqual(parsed["experiment_template"], "code_task_project")
            self.assertEqual(parsed["code_task_code_root"], str(code_root.resolve()))
            self.assertEqual(parsed["code_task_task_file"], str(task_file.resolve()))
            self.assertEqual(parsed["code_task_benchmark_command"], "uv run python benchmark.py")
            self.assertEqual(parsed["code_task_workspace_mode"], "git_worktree")
            self.assertEqual(parsed["code_task_workspace_include"], ["src/**"])
            self.assertEqual(parsed["code_task_primary_metric"], "accuracy")
            self.assertEqual(parsed["code_task_metric_directions"]["runtime_sec"], "lower")
            self.assertEqual(parsed["implementation_agent_mode"], "model")
            self.assertEqual(parsed["implementation_task_handoff"], "merge")
            task_config = parsed["task_config"]
            self.assertIsInstance(task_config, dict)
            self.assertEqual(task_config["task"]["kind"], "existing_project")
            self.assertEqual(task_config["implementation"]["domain_profile"], "code_agent_eval")
            self.assertEqual(task_config["implementation"]["agent_mode"], "model")
            self.assertEqual(task_config["implementation"]["task_handoff"], "merge")
            self.assertEqual(task_config["resource"]["max_files"], 8)
            self.assertEqual(task_config["generation"]["files_per_batch"], 3)
            self.assertEqual(task_config["generation"]["allow_fallback_scaffold"], True)

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


if __name__ == "__main__":
    unittest.main()
