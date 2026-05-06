from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from simple_ar.experiment.runner import run_experiment
from simple_ar.experiment.templates import build_experiment_code


TEST_ROOT = Path(__file__).resolve().parents[1] / ".tmp_tests"


class ExperimentRunnerTests(unittest.TestCase):
    def test_run_experiment_captures_stdout_stderr_and_metrics(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            script = Path(tmp) / "experiment.py"
            script.write_text(
                "import sys\n"
                "print('accuracy: 0.75')\n"
                "print('warning text', file=sys.stderr)\n",
                encoding="utf-8",
            )

            result = run_experiment(script, timeout_sec=5)

            self.assertEqual(result.returncode, 0)
            self.assertFalse(result.timed_out)
            self.assertEqual(result.metrics["accuracy"], 0.75)
            self.assertIn("warning text", result.stderr)

    def test_run_experiment_marks_timeout(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            script = Path(tmp) / "experiment.py"
            script.write_text(
                "import time\n"
                "print('accuracy: 0.1', flush=True)\n"
                "time.sleep(5)\n",
                encoding="utf-8",
            )

            result = run_experiment(script, timeout_sec=1)

            self.assertIsNone(result.returncode)
            self.assertTrue(result.timed_out)
            self.assertIn("Timed out", result.stderr)

    def test_generated_template_runs_and_outputs_metrics(self) -> None:
        TEST_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as tmp:
            script = Path(tmp) / "experiment.py"
            script.write_text(
                build_experiment_code(
                    {
                        "name": "toy_text_classification",
                        "template": "toy_text_classification",
                        "hypothesis": "Bag-of-words can match keyword rules on a tiny dataset.",
                    }
                ),
                encoding="utf-8",
            )

            result = run_experiment(script, timeout_sec=30)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("keyword_accuracy", result.metrics)
            self.assertIn("bow_logreg_accuracy", result.metrics)
            self.assertIn("accuracy_delta", result.metrics)


if __name__ == "__main__":
    unittest.main()
