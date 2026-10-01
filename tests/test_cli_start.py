from __future__ import annotations

import contextlib
import io
from pathlib import Path
import tempfile
import tomllib
import unittest
from unittest.mock import patch

from simple_ar.cli.parser import build_parser
from simple_ar.cli.research_config import research_defaults
from simple_ar.cli.start import prepare_start
from simple_ar.code_task.runtime.config import load_code_task_init_options, load_code_task_execute_options


class StartTests(unittest.TestCase):
    def test_data_setup_reports_columns_and_rejects_bad_shape_before_saving(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "data.csv"
            output = root / "runs"
            args = ["--kind", "data_analysis", "--goal", "Describe values",
                    "--data-file", str(source), "--value-column", "loss",
                    "--observation-unit", "one run", "--output-root", str(output), "--prepare-only"]
            source.write_text("value,group\n0.2,A\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Available columns.*value.*group"):
                self.prepare(*args)
            self.assertFalse(output.exists())
            source.write_text("loss,group\n0.2,A,extra\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "expected 2"):
                self.prepare(*args)
            self.assertFalse(output.exists())
            source.write_bytes(b"loss\n\xff\n")
            with self.assertRaisesRegex(ValueError, "UTF-8"):
                self.prepare(*args)
            self.assertFalse(output.exists())

    def test_data_prepare_does_not_measure_or_infer_numeric_column_semantics(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "data.csv"
            source.write_text("loss\nnot-a-number\n", encoding="utf-8")
            config = self.prepare("--kind", "data_analysis", "--goal", "Describe",
                                  "--data-file", str(source), "--value-column", "loss",
                                  "--observation-unit", "one row", "--output-root", str(root / "runs"),
                                  "--prepare-only")
            self.assertTrue(config.is_file())
            self.assertEqual(research_defaults(["research-session", "--config", str(config)])["value_column"], ["loss"])

    def test_writing_roundtrips_material_role_template_and_no_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            material = root / "results with spaces.markdown"
            material.write_text("# Results\nExternal result, not independently verified.\n", encoding="utf-8")
            config = self.prepare("--kind", "writing", "--goal", "Write an honest paper draft",
                                  "--material", str(material), "--template", "experiment",
                                  "--output-root", str(root / "runs"), "--prepare-only")
            defaults = research_defaults(["research-session", "--config", str(config)])
            self.assertEqual(defaults["task_kind"], "writing")
            self.assertEqual(defaults["material"], [str(material.resolve())])
            self.assertEqual(defaults["report_template"], "experiment")
            self.assertTrue(defaults["report_document_review"])
            self.assertEqual(defaults["outputs"], ["report"])
            self.assertNotIn("command_argv", defaults)
            self.assertNotIn("total_tokens", defaults)

    def test_writing_invalid_scope_does_not_save_a_task(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            material = root / "notes.md"
            material.write_text("Notes", encoding="utf-8")
            for extra in (["--sources", "search"], ["--document", str(material)], ["--fulltext"],
                          ["--command", "python", "run.py"]):
                with self.subTest(extra=extra), self.assertRaises(ValueError):
                    self.prepare("--kind", "writing", "--goal", "Write from material", "--material", str(material),
                                 "--output-root", str(root / "runs"), "--prepare-only", *extra)
            self.assertFalse((root / "runs").exists())

    def test_writing_checks_template_before_saving_or_calling_a_model(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            material = root / "notes.md"
            material.write_text("Notes", encoding="utf-8")
            empty_template = root / "empty.md"
            empty_template.write_text("", encoding="utf-8")
            for template in ("experimnt", str(root / "missing.md"), str(empty_template)):
                with self.subTest(template=template), self.assertRaisesRegex(ValueError, "Invalid writing template"):
                    self.prepare("--kind", "writing", "--goal", "Draft", "--material", str(material),
                                 "--template", template, "--output-root", str(root / "runs"), "--prepare-only")
            self.assertFalse((root / "runs").exists())

    def prepare(self, *arguments):
        args = build_parser().parse_args(["start", *arguments])
        with patch("sys.stdin.isatty", return_value=False), contextlib.redirect_stdout(io.StringIO()):
            return prepare_start(args)

    def test_local_survey_roundtrips_canonical_config_without_api_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paper = root / "材料 one's source.md"
            paper.write_text("# A source\n", encoding="utf-8")
            config = self.prepare("--kind", "survey", "--goal", '比较 "A" 和 B\n说明局限',
                                  "--document", str(paper), "--sources", "materials",
                                  "--output-root", str(root / "runs"), "--prepare-only")
            defaults = research_defaults(["research-session", "--config", str(config)])
            self.assertEqual(defaults["task_kind"], "survey")
            self.assertEqual(defaults["local_document"], [str(paper.resolve())])
            self.assertTrue(defaults["research_materials_only"])
            self.assertEqual(defaults["output_root"], str((config.parent / "sessions").resolve()))
            self.assertEqual(defaults["model"], "env")
            self.assertNotIn("total_tokens", defaults)
            self.assertNotIn("execution_details", defaults)

    def test_code_inputs_use_existing_loader_and_protect_tests(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project with spaces"
            project.mkdir()
            config = self.prepare("--kind", "bug_fix", "--goal", "Fix the rounding error",
                                  "--project", str(project), "--allow", "src/**",
                                  "--validate", "python -m unittest discover -s tests",
                                  "--output-root", str(root / "runs"), "--prepare-only")
            defaults = research_defaults(["research-session", "--config", str(config)])
            code_config = Path(defaults["code_task_config"])
            options = load_code_task_init_options(config_path=code_config)
            execution = load_code_task_execute_options(config_path=code_config)
            self.assertEqual(Path(options.code_root), project.resolve())
            self.assertEqual(options.workspace_mode, "copy")
            self.assertEqual(options.env_mode, "current")
            self.assertEqual(options.edit_scope_allowed_patterns, ("src/**",))
            self.assertIn("tests/**", options.edit_scope_protected_patterns)
            self.assertTrue(execution.use_llm)
            self.assertEqual(execution.baseline_policy, "skip")
            self.assertEqual(Path(options.task_file).read_text(encoding="utf-8"), "Fix the rounding error\n")
            self.assertEqual(list(project.iterdir()), [])

    def test_fulltext_is_explicit_and_maps_to_existing_retrieval_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            for flags, expected in (([], False), (["--fulltext"], True)):
                config = self.prepare("--kind", "survey", "--goal", "Compare methods", "--sources", "search",
                                      "--output-root", directory, "--prepare-only", *flags)
                values = research_defaults(["research-session", "--config", str(config)])
                self.assertEqual(values["research_use_fulltext"], expected)
                self.assertEqual(values["research_allow_pdf_download"], expected)
                self.assertEqual(values["research_keep_raw_pdf"], expected)
                if expected:
                    self.assertEqual(values["research_max_fulltext_documents"], 4)
                    self.assertEqual(values["research_max_pdf_mb"], 20)
                else:
                    self.assertNotIn("research_max_fulltext_documents", values)
                    self.assertNotIn("research_max_pdf_mb", values)
            with self.assertRaisesRegex(ValueError, "bug_fix does not consume"):
                self.prepare("--kind", "bug_fix", "--goal", "Fix", "--fulltext", "--prepare-only")

    def test_explicit_report_source_bound_roundtrips_without_affecting_other_tasks(self):
        with tempfile.TemporaryDirectory() as directory:
            config = self.prepare("--kind", "survey", "--goal", "Compare evidence across fields",
                                  "--sources", "search", "--max-cited-sources", "3",
                                  "--output-root", directory, "--prepare-only")
            values = research_defaults(["research-session", "--config", str(config)])
            self.assertEqual(values["report_max_cited_sources"], 3)
            with self.assertRaisesRegex(ValueError, "positive"):
                self.prepare("--kind", "survey", "--goal", "Compare evidence", "--sources", "search",
                             "--max-cited-sources", "0", "--output-root", directory, "--prepare-only")

    def test_bad_inputs_create_no_bundle(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "runs"
            for extra in (
                ["--kind", "survey", "--sources", "materials"],
                ["--kind", "survey", "--sources", "search", "--allow", "src/**"],
                ["--kind", "bug_fix", "--project", directory, "--validate", "python tests.py", "--allow", "../*"],
                ["--kind", "survey"],
            ):
                with self.subTest(extra=extra), self.assertRaises(ValueError):
                    self.prepare("--goal", "task", "--prepare-only", "--output-root", str(root), *extra)
                self.assertFalse(root.exists())

    def test_noninteractive_requires_execution_confirmation_but_keeps_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "--yes"):
                self.prepare("--kind", "survey", "--goal", "task", "--sources", "search", "--output-root", directory)
            configs = list(Path(directory).glob("*/research.toml"))
            self.assertEqual(len(configs), 1)
            self.assertEqual(tomllib.loads(configs[0].read_text())["task"]["goal"], "task")

    def test_interactive_decline_saves_without_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            args = build_parser().parse_args(["start", "--output-root", directory])
            with patch("sys.stdin.isatty", return_value=True), patch("builtins.input", side_effect=[
                "survey", "Review this direction", "", "search", "n",
            ]), contextlib.redirect_stdout(io.StringIO()):
                self.assertIsNone(prepare_start(args))
            self.assertEqual(len(list(Path(directory).glob("*/research.toml"))), 1)

    def test_explicit_source_scope_never_prompts_for_optional_documents(self):
        with tempfile.TemporaryDirectory() as directory:
            args = build_parser().parse_args(["start", "--kind", "survey", "--goal", "task",
                "--sources", "search", "--yes", "--output-root", directory])
            with patch("sys.stdin.isatty", return_value=True), patch("builtins.input") as prompt, \
                    contextlib.redirect_stdout(io.StringIO()):
                config = prepare_start(args)
            self.assertTrue(config.is_file())
            prompt.assert_not_called()

    def test_main_dispatches_only_to_existing_session_cli(self):
        from simple_ar.cli.main import main
        with tempfile.TemporaryDirectory() as directory, patch("sys.stdin.isatty", return_value=False), \
                patch("simple_ar.cli.main._print_research_session") as run, contextlib.redirect_stdout(io.StringIO()):
            main(["start", "--kind", "survey", "--goal", "task", "--sources", "search",
                  "--output-root", directory, "--yes"])
            run.assert_called_once()
            self.assertEqual(run.call_args.args[0].task_kind, "survey")
            self.assertEqual(run.call_args.args[0].outputs, ["report"])

    def test_prepared_reproduction_uses_existing_config_and_preserves_argv(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paper = root / "source with spaces.md"
            paper.write_text("# Published conclusion\n", encoding="utf-8")
            config = self.prepare("--kind", "reproduction", "--goal", "Check a published conclusion",
                "--document", str(paper), "--hypothesis", "Published claim, not a new method",
                "--dataset", "User-prepared adapted data", "--expected-outcome", "Compare coverage with 0.9",
                "--metric", "coverage", "--metric", "mc_error", "--cwd", directory, "--timeout-sec", "12",
                "--max-cited-sources", "1", "--output-root", str(root / "runs"), "--prepare-only",
                "--command", "python", "a script.py", "--label", "one value")
            values = research_defaults(["research-session", "--config", str(config)])
            self.assertEqual(values["task_kind"], "reproduction")
            self.assertEqual(values["outputs"], ["experiments", "report"])
            self.assertEqual(values["command_argv"], ["python", "a script.py", "--label", "one value"])
            self.assertEqual(values["cwd"], str(root.resolve()))
            self.assertEqual(values["execution_details"]["baseline_policy"], "skip")
            self.assertEqual(values["execution_details"]["protocol"]["dataset"], "User-prepared adapted data")
            self.assertEqual(values["process_invocations"], 1)
            self.assertEqual(values["process_wall_seconds"], 12)
            self.assertEqual(values["report_template"], "reproduction")
            self.assertTrue(values["report_document_review"])
            self.assertEqual(values["report_max_cited_sources"], 1)
            self.assertNotIn("total_tokens", values)
            self.assertFalse(values["research_allow_pdf_download"])
            self.assertFalse((config.parent / "code_task.toml").exists())

    def test_invalid_reproduction_and_cross_function_options_create_no_bundle(self):
        with tempfile.TemporaryDirectory() as directory:
            paper = Path(directory) / "paper.md"
            paper.write_text("source", encoding="utf-8")
            output = Path(directory) / "runs"
            base = ["--kind", "reproduction", "--goal", "Check", "--document", str(paper),
                "--hypothesis", "Claim", "--dataset", "Data", "--expected-outcome", "Criterion",
                "--metric", "coverage", "--output-root", str(output), "--prepare-only"]
            for flags in (["--sources", "search"], ["--fulltext"], ["--timeout-sec", "0"],
                          ["--cwd", str(Path(directory) / "missing")], ["--metric", "coverage"],
                          ["--project", directory]):
                with self.subTest(flags=flags), self.assertRaises(ValueError):
                    self.prepare(*base, *flags, "--command", "python", "run.py")
                self.assertFalse(output.exists())
            for command in ([], [""]):
                with self.subTest(command=command), self.assertRaises(ValueError):
                    self.prepare(*base, "--command", *command)
                self.assertFalse(output.exists())
            with self.assertRaisesRegex(ValueError, "Missing input"):
                self.prepare("--kind", "reproduction", "--goal", "Check", "--document", str(paper),
                    "--output-root", str(output), "--prepare-only")
            with self.assertRaisesRegex(ValueError, "require --kind reproduction"):
                self.prepare("--kind", "survey", "--goal", "Read", "--sources", "search", "--prepare-only",
                    "--output-root", str(output), "--command", "python", "run.py")
            self.assertFalse(output.exists())

    def test_interactive_reproduction_asks_for_protocol_not_a_research_algorithm(self):
        with tempfile.TemporaryDirectory() as directory:
            paper = Path(directory) / "paper.md"
            paper.write_text("source", encoding="utf-8")
            args = build_parser().parse_args(["start", "--kind", "reproduction", "--goal", "Check",
                "--document", str(paper), "--output-root", directory, "--prepare-only"])
            with patch("sys.stdin.isatty", return_value=True), patch("builtins.input", side_effect=[
                    "Published claim", "Existing data", "Value near 0.9", '["python", "run.py"]', "coverage",
                    ]), contextlib.redirect_stdout(io.StringIO()):
                config = prepare_start(args)
            values = research_defaults(["research-session", "--config", str(config)])
            self.assertEqual(values["command_argv"], ["python", "run.py"])
            self.assertEqual(values["timeout_sec"], 300)
            self.assertEqual(values["metric"], ["coverage"])
            self.assertTrue(values["research_materials_only"])


if __name__ == "__main__":
    unittest.main()
