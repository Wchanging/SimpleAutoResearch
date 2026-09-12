"""Lightweight evaluator checks; no Torch, downloads, or model training."""

import importlib.util
import math
import unittest
from collections import Counter
from pathlib import Path

spec = importlib.util.spec_from_file_location("cifar_protocol", Path(__file__).resolve().parents[1]
    / "examples/cifar10_calibration/protocol.py")
protocol = importlib.util.module_from_spec(spec)
spec.loader.exec_module(protocol)


class CifarProtocolTests(unittest.TestCase):
    def test_split_is_balanced_disjoint_and_reproducible(self):
        labels = list(range(10)) * 5000
        split = protocol.make_split(labels)
        self.assertEqual(split, protocol.make_split(labels))
        self.assertEqual(Counter(labels[i] for i in split["train"]), {i: 1000 for i in range(10)})
        self.assertEqual(Counter(labels[i] for i in split["validation"]), {i: 500 for i in range(10)})
        self.assertFalse(set(split["train"]) & set(split["validation"]))
        self.assertFalse(split["official_test_used"])
        with self.assertRaises(ValueError):
            protocol.make_split(list(range(10)) * 1000)

    def test_metrics_match_hand_computation_and_include_confidence_one(self):
        metrics = protocol.calibration_metrics([[0.75, 0.25], [0.75, 0.25], [0, 1]], [0, 1, 1])
        self.assertAlmostEqual(metrics["accuracy"], 2 / 3)
        self.assertAlmostEqual(metrics["nll"], -(math.log(0.75) + math.log(0.25)) / 3)
        self.assertAlmostEqual(metrics["ece"], 1 / 6)
        self.assertEqual(metrics["reliability_bins"][-1]["count"], 1)
        self.assertIsNone(metrics["reliability_bins"][0]["accuracy"])

    def test_invalid_predictions_are_not_silent_zero_metrics(self):
        for probabilities, labels in [([], []), ([[float("nan"), 0]], [0]),
                                      ([[0.2, 0.2]], [0]), ([[0.5, 0.5]], [2])]:
            with self.assertRaises(ValueError):
                protocol.calibration_metrics(probabilities, labels)
