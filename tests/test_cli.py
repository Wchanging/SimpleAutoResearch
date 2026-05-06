from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from simple_ar.artifacts import read_json
from simple_ar.cli import main


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


if __name__ == "__main__":
    unittest.main()
