from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from simple_ar.artifacts import read_json, read_text
from simple_ar.pipeline import Context, MissingInputError, PipelineEvent, PipelineRunner
from simple_ar.stage_handlers import HANDLERS
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
            self.assertGreaterEqual(len(report_manifest["cited_papers"]), 1)
            self.assertLessEqual(len(report_manifest["cited_papers"]), len(report_manifest["papers"]))

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


if __name__ == "__main__":
    unittest.main()
