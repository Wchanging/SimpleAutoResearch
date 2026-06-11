from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from review_pipeline.data import load_reviews, split_reviews
from review_pipeline.features import feature_family_summary
from review_pipeline.metrics import classification_metrics
from review_pipeline.model import KeywordLinearClassifier
from review_pipeline.progress import progress_line


def run_experiment(config_path: Path, *, show_progress: bool) -> dict[str, float]:
    """Run the configured review-classification experiment."""

    config = _load_config(config_path)
    rows = load_reviews()
    train_rows, eval_rows = split_reviews(rows)
    feature_families = _string_list(config.get("feature_families")) or ["word"]
    rounds = _positive_int(config.get("rounds"), default=4)
    width = _positive_int(config.get("progress_width"), default=18)
    model = KeywordLinearClassifier(
        feature_families=feature_families,
        threshold=float(config.get("threshold", 0.0)),
    )

    started = time.perf_counter()
    for step in range(1, rounds + 1):
        model.fit(train_rows)
        if show_progress:
            print(
                progress_line(
                    step,
                    rounds,
                    width=width,
                    message=(
                        f"features={feature_family_summary(feature_families)} "
                        f"train={len(train_rows)} eval={len(eval_rows)}"
                    ),
                ),
                flush=True,
            )
    train_time = time.perf_counter() - started

    inference_started = time.perf_counter()
    predictions = model.predict_many(eval_rows)
    inference_time_ms = (time.perf_counter() - inference_started) * 1000.0
    metrics = classification_metrics([row.label for row in eval_rows], predictions)
    metrics.update(
        {
            "train_time_sec": train_time,
            "inference_time_ms": inference_time_ms,
            "model_size": float(model.model_size()),
            "feature_family_count": float(len(feature_families)),
            "eval_examples": float(len(eval_rows)),
        }
    )
    return metrics


def format_metric_lines(metrics: dict[str, float]) -> str:
    """Format metrics as ``name: value`` lines for SimpleAutoResearch parsing."""

    order = [
        "accuracy",
        "macro_f1",
        "train_time_sec",
        "inference_time_ms",
        "model_size",
        "feature_family_count",
        "eval_examples",
    ]
    lines = []
    for key in order:
        value = metrics.get(key)
        if value is not None:
            lines.append(f"{key}: {value:.6f}")
    return "\n".join(lines)


def _load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return data


def _positive_int(value: object, *, default: int) -> int:
    if isinstance(value, int) and value > 0:
        return value
    return default


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]
