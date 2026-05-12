from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from simple_ar.artifacts import read_json, read_text
from simple_ar.experiment.code_task_demo import (
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
                "simple_ar.stage_handlers.prepare_code_task_demo_experiment",
                return_value=fake_result,
            ):
                execute_code(ctx)

            script = read_text(run_dir / "06-code" / "experiment.py")
            self.assertIn("run_code_task_benchmark", script)
            self.assertIn("spamfilter/rules.py", script)
            meta = read_json(run_dir / "06-code" / "code_task_experiment.json")
            self.assertEqual(meta["template"], CODE_TASK_TOY_SPAM_TEMPLATE)
            self.assertEqual(meta["changed_files"], ["spamfilter/rules.py"])


if __name__ == "__main__":
    unittest.main()
