from __future__ import annotations

import time
from dataclasses import dataclass

from sklearn.metrics import f1_score

from digits_mlp.data import load_digits_split
from digits_mlp.model import TinyMLP


@dataclass(frozen=True)
class BenchmarkConfig:
    """Configuration for the local digits benchmark."""

    seed: int = 7
    hidden_dim: int = 16
    learning_rate: float = 0.08
    epochs: int = 12
    batch_size: int = 128


def run_benchmark(config: BenchmarkConfig) -> dict[str, float]:
    """Train and evaluate the baseline MLP on the sklearn digits dataset."""
    train_x, test_x, train_y, test_y = load_digits_split(seed=config.seed)
    model = TinyMLP(
        hidden_dim=config.hidden_dim,
        learning_rate=config.learning_rate,
        seed=config.seed,
    )

    train_start = time.perf_counter()
    model.fit(
        train_x,
        train_y,
        epochs=config.epochs,
        batch_size=config.batch_size,
    )
    train_time_sec = time.perf_counter() - train_start

    inference_start = time.perf_counter()
    predictions = model.predict(test_x)
    inference_time_ms = (time.perf_counter() - inference_start) * 1000.0

    accuracy = float((predictions == test_y).mean())
    macro_f1 = float(f1_score(test_y, predictions, average="macro"))
    return {
        "accuracy": accuracy,
        "macro_f1": macro_f1,
        "train_time_sec": float(train_time_sec),
        "inference_time_ms": float(inference_time_ms),
        "parameter_count": float(model.parameter_count()),
    }
