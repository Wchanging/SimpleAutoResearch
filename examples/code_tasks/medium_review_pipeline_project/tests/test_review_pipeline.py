from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

from review_pipeline.experiment import run_experiment


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class ReviewPipelineTests(unittest.TestCase):
    def test_baseline_metrics_are_reasonable_not_perfect(self) -> None:
        metrics = run_experiment(PROJECT_ROOT / "configs" / "experiment.json", show_progress=False)
        self.assertGreaterEqual(metrics["accuracy"], 0.60)
        self.assertLess(metrics["accuracy"], 0.95)
        self.assertGreaterEqual(metrics["macro_f1"], 0.55)

    def test_main_entrypoint_prints_progress_and_metric_lines(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                "main.py",
                "--config",
                "configs/experiment.json",
                "--show-progress",
            ],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("round 1/4", completed.stdout)
        self.assertIn("accuracy:", completed.stdout)
        self.assertIn("macro_f1:", completed.stdout)


if __name__ == "__main__":
    unittest.main()
