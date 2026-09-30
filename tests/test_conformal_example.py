"""Construction checks, not a formal benchmark or theorem proof."""
import importlib.util
from pathlib import Path
import unittest


class ConformalExampleTests(unittest.TestCase):
    def test_no_shift_equal_weights_match_ordinary_construction(self):
        path = Path(__file__).resolve().parents[1] / "examples/conformal_reproduction/run.py"
        spec = importlib.util.spec_from_file_location("conformal_example", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        result = module.simulate(seed=3, repetitions=30, calibration_size=19,
                                 source_positive=0.5, target_positive=0.5)
        self.assertEqual(result["weighted_coverage"], result["ordinary_coverage"])
        self.assertEqual(result["infinite_radius_fraction"], 0)

    def test_target_mass_at_infinity_is_retained(self):
        path = Path(__file__).resolve().parents[1] / "examples/conformal_reproduction/run.py"
        spec = importlib.util.spec_from_file_location("conformal_example", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        result = module.simulate(seed=3, repetitions=30, calibration_size=1)
        self.assertGreater(result["infinite_radius_fraction"], 0)
        self.assertEqual(result["ordinary_coverage"], 1)
