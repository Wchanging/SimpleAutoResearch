"""Adapter contracts only: no upstream imports, installs or scientific training."""
import copy
import importlib.util
from pathlib import Path
import unittest
import tempfile
from unittest.mock import patch


def adapter():
    path = Path(__file__).resolve().parents[1] / "examples/rcp_reproduction/run.py"
    spec = importlib.util.spec_from_file_location("rcp_example", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RCPExampleTests(unittest.TestCase):
    def setUp(self):
        self.module = adapter()
        self.rows = [{"seed": seed, "method": method, "rows": 21613, "splits": [10123, 3639, 2048, 5803],
            "output_dim": 2, "metrics": dict(coverage=0.9, cond_cov_x_error=value, wsc=0.8, exact_region_size=5.0)}
            for seed in (0, 1) for method, value in (("Ball", 0.003), ("RCP-Ball", 0.002))]

    def test_exact_pairs_produce_descriptive_not_significance_results(self):
        summary = self.module.summarize(self.rows, [0, 1])
        self.assertAlmostEqual(summary["cond_cov_x_error"]["paired_delta"], -0.001)
        self.assertEqual(summary["cond_cov_x_error"]["paired_delta_seed_se"], 0.0)
        self.assertNotIn("significance", summary)

    def test_single_seed_has_no_invented_uncertainty(self):
        summary = self.module.summarize(self.rows[:2], [0])
        self.assertIsNone(summary["coverage"]["paired_delta_seed_se"])

    def test_missing_duplicate_or_unexpected_observation_rejected(self):
        for rows in (self.rows[:-1], self.rows + [self.rows[0]], [{**self.rows[0], "seed": 9}, *self.rows[1:]]):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                self.module.summarize(rows, [0, 1])

    def test_condition_changes_are_not_silently_compared(self):
        for name, value in (("rows", 1000), ("splits", [400, 100, 300, 200]), ("output_dim", 1)):
            rows = copy.deepcopy(self.rows)
            rows[0][name] = value
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.module.summarize(rows, [0, 1])

    def test_nonfinite_missing_and_boolean_values_rejected(self):
        for value in (None, float("nan"), float("inf"), True):
            rows = copy.deepcopy(self.rows)
            rows[0]["metrics"]["coverage"] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.module.summarize(rows, [0, 1])

    def test_interpreter_symlink_preserves_the_selected_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "base-python"
            target.touch()
            executable = root / "venv-python"
            try:
                executable.symlink_to(target)
            except OSError:
                self.skipTest("Platform does not permit symlink creation")
            self.assertEqual(self.module.resolve_python(str(executable)), str(executable.absolute()))
            self.assertNotEqual(self.module.resolve_python(str(executable)), str(executable.resolve()))

    def test_relative_and_path_lookup_keep_argv_absolute(self):
        expected = str(Path("env/bin/python").absolute())
        self.assertEqual(self.module.resolve_python("env/bin/python"), expected)
        with patch.object(self.module.shutil, "which", return_value="env/bin/python"):
            self.assertEqual(self.module.resolve_python("python"), expected)
        with patch.object(self.module.shutil, "which", return_value=None):
            self.assertIsNone(self.module.resolve_python("missing-python"))


if __name__ == "__main__":
    unittest.main()
