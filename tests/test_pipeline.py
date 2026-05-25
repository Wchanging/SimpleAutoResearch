from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from simple_ar.artifacts import read_json, read_text
from simple_ar.experiment.code_task_experiment import (
    CODE_TASK_PROJECT_TEMPLATE,
    CODE_TASK_TOY_SPAM_TEMPLATE,
    CodeTaskExperimentResult,
)
from simple_ar.pipeline import Context, MissingInputError, PipelineEvent, PipelineRunner
from simple_ar.stage_handlers import HANDLERS, execute_code, execute_design
from simple_ar.stages import Stage


TEST_ROOT = Path(__file__).resolve().parents[1] / ".tmp_tests"


def handlers():
    return {Stage(number): handler for number, handler in HANDLERS.items()}


class PipelineTests(unittest.TestCase):
    def test_run_to_plan_creates_expected_outputs(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            ctx = Context(Path(tmp) / "run", "toy topic")
            PipelineRunner(handlers()).run(ctx, to_stage=Stage.PLAN)

            self.assertTrue((ctx.run_dir / "01-plan" / "goal.md").is_file())
            self.assertTrue((ctx.run_dir / "01-plan" / "problem.md").is_file())
            self.assertTrue((ctx.run_dir / "01-plan" / "stage_meta.json").is_file())

    def test_missing_input_fails_clearly(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            ctx = Context(Path(tmp) / "run", "toy topic")
            with self.assertRaises(MissingInputError):
                PipelineRunner(handlers()).run(
                    ctx,
                    from_stage=Stage.SEARCH,
                    to_stage=Stage.SEARCH,
                )

    def test_full_stub_pipeline_reaches_report(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            ctx = Context(Path(tmp) / "run", "toy topic")
            executions = PipelineRunner(handlers()).run(ctx)

            self.assertEqual(len(executions), 8)
            self.assertTrue((ctx.run_dir / "02-search" / "research_questions.json").is_file())
            self.assertTrue((ctx.run_dir / "02-search" / "query_plan.json").is_file())
            self.assertTrue((ctx.run_dir / "02-search" / "search_meta.json").is_file())
            self.assertTrue((ctx.run_dir / "08-report" / "report.md").is_file())
            self.assertTrue((ctx.run_dir / "08-report" / "references.bib").is_file())
            self.assertTrue((ctx.run_dir / "08-report" / "manifest.json").is_file())
            self.assertTrue((ctx.run_dir / "08-report" / "report_quality.json").is_file())
            self.assertTrue((ctx.run_dir / "manifest.json").is_file())
            self.assertTrue((ctx.run_dir / "source_plan.json").is_file())
            self.assertTrue((ctx.run_dir / "activity_log.jsonl").is_file())
            self.assertTrue((ctx.run_dir / "evidence_ledger.jsonl").is_file())

            manifest = read_json(ctx.run_dir / "manifest.json")
            self.assertTrue(all(item["status"] == "done" for item in manifest["stages"]))

            report_manifest = read_json(ctx.run_dir / "08-report" / "manifest.json")
            self.assertEqual(report_manifest["experiment"]["template"], "toy_text_classification")
            self.assertIn("results.json", report_manifest["source_artifacts"])
            self.assertIn("evidence_ledger.jsonl", report_manifest["source_artifacts"])
            self.assertIn("report_quality.json", report_manifest["report_artifacts"])
            self.assertGreaterEqual(len(report_manifest["cited_papers"]), 1)
            self.assertLessEqual(len(report_manifest["cited_papers"]), len(report_manifest["papers"]))
            report_quality = read_json(ctx.run_dir / "08-report" / "report_quality.json")
            self.assertEqual(report_quality["status"], "passed")
            self.assertEqual(report_quality["summary"]["body_cited_paper_count"], 1)

            report = read_text(ctx.run_dir / "08-report" / "report.md")
            self.assertIn("## Abstract", report)
            self.assertIn("## Literature Search", report)
            self.assertIn("## Limitations", report)
            self.assertIn("fixture metadata", report)
            self.assertIn("## References", report)

    def test_synthesize_to_report_uses_research_only_mode_without_results(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            ctx = Context(Path(tmp) / "run", "toy topic")
            runner = PipelineRunner(handlers())
            runner.run(ctx, to_stage=Stage.SYNTHESIZE)

            executions = runner.run(ctx, from_stage=Stage.REPORT, to_stage=Stage.REPORT)

            self.assertEqual(len(executions), 1)
            self.assertFalse((ctx.run_dir / "07-run" / "results.json").exists())
            report_manifest = read_json(ctx.run_dir / "08-report" / "manifest.json")
            self.assertEqual(report_manifest["report_mode"], "research_only")
            report = read_text(ctx.run_dir / "08-report" / "report.md")
            self.assertIn("## Search Scope", report)
            self.assertIn("## Thematic Synthesis", report)
            self.assertIn("## Approach Patterns", report)
            self.assertIn("## Open Questions", report)
            self.assertNotIn("## Method", report)
            self.assertNotIn("## Experiments", report)
            self.assertNotIn("## Results", report)
            self.assertIn("No experiment was executed", report)

    def test_reporter_receives_stage_progress_events(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            events: list[PipelineEvent] = []
            ctx = Context(Path(tmp) / "run", "toy topic")

            PipelineRunner(handlers(), reporter=events.append).run(ctx, to_stage=Stage.PLAN)

            event_names = [event.name for event in events]
            self.assertIn("pipeline_start", event_names)
            self.assertIn("stage_start", event_names)
            self.assertIn("stage_done", event_names)
            self.assertIn("pipeline_done", event_names)

    def test_code_task_experiment_template_writes_harness_artifacts(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            run_dir = Path(tmp) / "run"
            ctx = Context(
                run_dir,
                "LLM code task demo",
                config={
                    "experiment_template": CODE_TASK_TOY_SPAM_TEMPLATE,
                    "experiment_timeout_sec": 45,
                    "use_llm": True,
                },
            )
            synth_dir = run_dir / "04-synthesize"
            synth_dir.mkdir(parents=True)
            (synth_dir / "hypothesis.md").write_text(
                "# Hypothesis\n\nPatch an existing baseline.\n",
                encoding="utf-8",
            )

            ctx.current_stage = Stage.DESIGN
            ctx.stage_dir().mkdir(parents=True)
            execute_design(ctx)

            plan = read_json(run_dir / "05-design" / "experiment_plan.json")
            self.assertEqual(plan["template"], CODE_TASK_TOY_SPAM_TEMPLATE)
            self.assertEqual(plan["mode"], "embedded_code_task")
            self.assertEqual(plan["method"], "llm_planned_controlled_patch")

            code_dir = run_dir / "06-code"
            fake_result = CodeTaskExperimentResult(
                code_task_run_dir=code_dir / "code_task_run",
                workspace_dir=code_dir / "code_task_run" / "code_task" / "workspace",
                patch_plan_path=code_dir / "code_task_run" / "code_task" / "patch_plan.md",
                proposed_edits_path=(
                    code_dir / "code_task_run" / "code_task" / "meta" / "proposed_edits.json"
                ),
                patch_diff_path=code_dir / "code_task_run" / "code_task" / "patch.diff",
                validation_report_path=(
                    code_dir / "code_task_run" / "code_task" / "meta" / "validation_report.json"
                ),
                plan_mode="llm",
                edit_mode="llm",
                edit_count=1,
                changed_files=("spamfilter/rules.py",),
                validation_status="passed",
            )

            ctx.current_stage = Stage.CODE
            ctx.stage_dir().mkdir(parents=True)
            with patch(
                "simple_ar.stage_handlers.prepare_code_task_experiment",
                return_value=fake_result,
            ):
                execute_code(ctx)

            script = read_text(run_dir / "06-code" / "experiment.py")
            self.assertIn("run_code_task_benchmark", script)
            self.assertIn("spamfilter/rules.py", script)
            meta = read_json(run_dir / "06-code" / "code_task_experiment.json")
            self.assertEqual(meta["template"], CODE_TASK_TOY_SPAM_TEMPLATE)
            self.assertEqual(meta["changed_files"], ["spamfilter/rules.py"])

    def test_code_task_project_template_accepts_user_project_config(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        repo_root = Path(__file__).resolve().parents[1]
        config_path = repo_root / "examples" / "code_tasks" / "configs" / "tiny_digits_mlp.toml"
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            run_dir = Path(tmp) / "run"
            ctx = Context(
                run_dir,
                "Tiny digits MLP",
                config={
                    "experiment_template": CODE_TASK_PROJECT_TEMPLATE,
                    "code_task_config": str(config_path),
                    "experiment_timeout_sec": 45,
                    "use_llm": True,
                },
            )
            synth_dir = run_dir / "04-synthesize"
            synth_dir.mkdir(parents=True)
            (synth_dir / "hypothesis.md").write_text(
                "# Hypothesis\n\nImprove an existing lightweight MLP benchmark.\n",
                encoding="utf-8",
            )

            ctx.current_stage = Stage.DESIGN
            ctx.stage_dir().mkdir(parents=True)
            execute_design(ctx)

            plan = read_json(run_dir / "05-design" / "experiment_plan.json")
            self.assertEqual(plan["template"], CODE_TASK_PROJECT_TEMPLATE)
            self.assertEqual(plan["code_task"]["benchmark_command"], "python benchmark.py")
            self.assertEqual(plan["code_task"]["primary_metric"], "accuracy")
            self.assertEqual(plan["code_task"]["scope"], "user_project")

            code_dir = run_dir / "06-code"
            fake_result = CodeTaskExperimentResult(
                code_task_run_dir=code_dir / "code_task_run",
                workspace_dir=code_dir / "code_task_run" / "code_task" / "workspace",
                patch_plan_path=code_dir / "code_task_run" / "code_task" / "patch_plan.md",
                proposed_edits_path=(
                    code_dir / "code_task_run" / "code_task" / "meta" / "proposed_edits.json"
                ),
                patch_diff_path=code_dir / "code_task_run" / "code_task" / "patch.diff",
                validation_report_path=(
                    code_dir / "code_task_run" / "code_task" / "meta" / "validation_report.json"
                ),
                plan_mode="llm",
                edit_mode="llm",
                edit_count=2,
                changed_files=("digits_mlp/model.py", "digits_mlp/train.py"),
                validation_status="passed",
                template=CODE_TASK_PROJECT_TEMPLATE,
                baseline_status="passed",
            )

            ctx.current_stage = Stage.CODE
            ctx.stage_dir().mkdir(parents=True)
            with patch(
                "simple_ar.stage_handlers.prepare_code_task_experiment",
                return_value=fake_result,
            ):
                execute_code(ctx)

            script = read_text(run_dir / "06-code" / "experiment.py")
            self.assertIn("run_code_task_benchmark", script)
            self.assertIn("comparison_improved", script)
            meta = read_json(run_dir / "06-code" / "code_task_experiment.json")
            self.assertEqual(meta["template"], CODE_TASK_PROJECT_TEMPLATE)
            self.assertEqual(meta["baseline_status"], "passed")

    def test_code_task_project_design_can_generate_missing_task_file(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        repo_root = Path(__file__).resolve().parents[1]
        code_root = repo_root / "examples" / "code_tasks" / "tiny_digits_mlp_project"
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            run_dir = Path(tmp) / "run"
            ctx = Context(
                run_dir,
                "Upgrade a tiny MLP after literature review",
                config={
                    "experiment_template": CODE_TASK_PROJECT_TEMPLATE,
                    "code_task_code_root": str(code_root),
                    "code_task_benchmark_command": "python benchmark.py",
                    "code_task_primary_metric": "accuracy",
                    "use_llm": False,
                },
            )
            for stage_name, filename, text in (
                ("01-plan", "goal.md", "# Goal\n\nImprove a lightweight MLP baseline.\n"),
                ("01-plan", "problem.md", "# Problem\n\nFind a small local improvement.\n"),
                ("04-synthesize", "synthesis.md", "# Synthesis\n\nPrefer modest architecture tuning.\n"),
                ("04-synthesize", "hypothesis.md", "# Hypothesis\n\nA small source patch can improve validation accuracy.\n"),
            ):
                stage_dir = run_dir / stage_name
                stage_dir.mkdir(parents=True, exist_ok=True)
                (stage_dir / filename).write_text(text, encoding="utf-8")

            ctx.current_stage = Stage.DESIGN
            ctx.stage_dir().mkdir(parents=True)
            execute_design(ctx)

            generated_task = run_dir / "05-design" / "generated_code_task.md"
            self.assertTrue(generated_task.is_file())
            task_text = read_text(generated_task)
            self.assertIn("# Code Task", task_text)
            self.assertIn("python benchmark.py", task_text)
            plan = read_json(run_dir / "05-design" / "experiment_plan.json")
            self.assertEqual(plan["template"], CODE_TASK_PROJECT_TEMPLATE)
            self.assertEqual(plan["code_task"]["task_source"], "generated_from_research")
            self.assertEqual(
                plan["code_task"]["generated_task_file"],
                "05-design/generated_code_task.md",
            )
            self.assertEqual(plan["code_task"]["task_generation"]["mode"], "fallback")


if __name__ == "__main__":
    unittest.main()
