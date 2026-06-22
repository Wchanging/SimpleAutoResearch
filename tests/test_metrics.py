from __future__ import annotations

import unittest

from simple_ar.experiment.metrics import parse_metric_lines


class MetricParsingTests(unittest.TestCase):
    def test_parse_numeric_metric_lines(self) -> None:
        text = "status: ok\naccuracy: 0.84\nmodel.precision: 1\nloss_delta: -0.25\n"

        metrics = parse_metric_lines(text)

        self.assertEqual(metrics["accuracy"], 0.84)
        self.assertEqual(metrics["model.precision"], 1.0)
        self.assertEqual(metrics["loss_delta"], -0.25)
        self.assertNotIn("status", metrics)

    def test_parse_prefixed_equals_metric_lines(self) -> None:
        text = "METRIC accuracy=0.91\nMETRIC train_time_sec=1.2e-3\nMETRIC label=best\n"

        metrics = parse_metric_lines(text)

        self.assertEqual(metrics["accuracy"], 0.91)
        self.assertEqual(metrics["train_time_sec"], 0.0012)
        self.assertNotIn("label", metrics)


if __name__ == "__main__":
    unittest.main()
