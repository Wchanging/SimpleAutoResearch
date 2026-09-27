"""Low-cost checks for the real-paper validation adapter."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import tomllib
import unittest

import numpy as np


_ADAPTER = Path(__file__).resolve().parents[1] / "examples" / "tabm_research" / "run_tabm.py"
_SPEC = importlib.util.spec_from_file_location("tabm_adapter", _ADAPTER)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)


class TabmAdapterTest(unittest.TestCase):
    def test_seed_override_preserves_unrelated_toml(self) -> None:
        original = 'seed = 0  # official\n[model]\nname = "tabm"\n'
        changed = _MODULE._config_with_seed(original, 7)
        self.assertEqual(changed, 'seed = 7  # official\n[model]\nname = "tabm"\n')
        self.assertEqual(tomllib.loads(changed)["seed"], 7)

    def test_seed_override_rejects_missing_or_ambiguous_seed(self) -> None:
        for original in ('[model]\nname = "tabm"\n', 'seed = 0\nseed = 1\n'):
            with self.subTest(original=original):
                with self.assertRaises(ValueError):
                    _MODULE._config_with_seed(original, 2)

    def test_case_keeps_seed_extension_available(self) -> None:
        case = tomllib.loads((_ADAPTER.parent / "research.toml").read_text(encoding="utf-8"))
        self.assertEqual(case["execution"]["seed_flag"], "--seed")
        self.assertNotIn("seed", case["execution"]["protocol"]["comparison_conditions"])

    def test_regression_uses_fixed_validation_labels(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            labels = root / "data" / "california"
            labels.mkdir(parents=True)
            np.save(labels / "Y_val.npy", np.array([2.0, 4.0]))
            np.savez(root / "predictions.npz", val=np.array([3.0, 5.0]))
            self.assertAlmostEqual(_MODULE._measured_validation(root, root, "california"), 1.0)

    def test_classification_uses_fixed_validation_labels(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            labels = root / "data" / "adult"
            labels.mkdir(parents=True)
            np.save(labels / "Y_val.npy", np.array([0, 1, 0]))
            np.savez(root / "predictions.npz", val=np.array([0.1, 0.9, 0.8]))
            self.assertAlmostEqual(_MODULE._measured_validation(root, root, "adult"), 2 / 3)

    def test_rejects_misaligned_predictions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            labels = root / "data" / "california"
            labels.mkdir(parents=True)
            np.save(labels / "Y_val.npy", np.array([2.0, 4.0]))
            np.savez(root / "predictions.npz", val=np.array([3.0]))
            with self.assertRaisesRegex(ValueError, "misaligned"):
                _MODULE._measured_validation(root, root, "california")


if __name__ == "__main__":
    unittest.main()
