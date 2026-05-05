from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

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
            self.assertTrue((ctx.run_dir / "manifest.json").is_file())

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
