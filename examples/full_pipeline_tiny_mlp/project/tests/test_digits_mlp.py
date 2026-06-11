from __future__ import annotations

import unittest

from digits_mlp import BenchmarkConfig, TinyMLP, load_digits_split, run_benchmark


class TinyDigitsMlpTests(unittest.TestCase):
    def test_load_digits_split_is_deterministic(self) -> None:
        first = load_digits_split(seed=7)
        second = load_digits_split(seed=7)

        self.assertEqual(first[0].shape[1], 64)
        self.assertEqual(first[2].tolist(), second[2].tolist())

    def test_model_predicts_one_label_per_sample(self) -> None:
        train_x, test_x, train_y, _ = load_digits_split(seed=7)
        model = TinyMLP(hidden_dim=8, learning_rate=0.05, seed=7)
        model.fit(train_x[:128], train_y[:128], epochs=1, batch_size=64)

        predictions = model.predict(test_x[:12])

        self.assertEqual(predictions.shape, (12,))
        self.assertTrue(((predictions >= 0) & (predictions < 10)).all())

    def test_benchmark_is_lightweight_and_nontrivial(self) -> None:
        result = run_benchmark(BenchmarkConfig())

        self.assertGreaterEqual(result["accuracy"], 0.70)
        self.assertLess(result["accuracy"], 0.90)
        self.assertLess(result["train_time_sec"], 5.0)
        self.assertGreater(result["parameter_count"], 0)


if __name__ == "__main__":
    unittest.main()
