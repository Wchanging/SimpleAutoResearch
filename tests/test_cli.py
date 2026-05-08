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


if __name__ == "__main__":
    unittest.main()
