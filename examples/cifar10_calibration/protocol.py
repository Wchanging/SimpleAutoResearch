"""Protected data partition and calibration metric definitions; stdlib only."""

import hashlib
import math
import random
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def make_split(labels: list[int], seed: int = 1729) -> dict:
    if len(labels) != 50000 or set(labels) != set(range(10)):
        raise ValueError("Expected official CIFAR-10 training labels, not the test set.")
    rng = random.Random(seed)
    train, validation = [], []
    for label in range(10):
        indices = [i for i, target in enumerate(labels) if target == label]
        if len(indices) != 5000:
            raise ValueError("Expected 5000 training examples per class.")
        rng.shuffle(indices)
        train.extend(indices[:1000])
        validation.extend(indices[1000:1500])
    return {"split_seed": seed, "train": sorted(train), "validation": sorted(validation),
            "official_test_used": False}


def calibration_metrics(probabilities, labels, bins: int = 15) -> dict:
    """Fractions, natural-log NLL, bins [i/B,(i+1)/B), last includes 1."""
    if bins < 1 or not labels or len(probabilities) != len(labels):
        raise ValueError("Nonempty aligned predictions and labels are required.")
    counts, correct, confidence = [0] * bins, [0] * bins, [0.0] * bins
    hits, nll = 0, 0.0
    for row, label in zip(probabilities, labels):
        if (not row or not 0 <= label < len(row)
                or any(not math.isfinite(p) or p < 0 or p > 1 for p in row)
                or not math.isclose(sum(row), 1.0, abs_tol=1e-5)):
            raise ValueError("Expected finite normalized probabilities and valid labels.")
        prediction = max(range(len(row)), key=row.__getitem__)
        value = row[prediction]
        index = min(int(value * bins), bins - 1)
        hit = int(prediction == label)
        hits += hit
        # Fixed numerical clipping is part of the evaluator definition.
        nll -= math.log(max(row[label], 1e-12))
        counts[index] += 1
        correct[index] += hit
        confidence[index] += value
    size = len(labels)
    return {"accuracy": hits / size, "nll": nll / size,
            "ece": sum(abs(correct[i] - confidence[i]) for i in range(bins)) / size,
            "examples": size,
            "reliability_bins": [{"count": counts[i],
                "accuracy": correct[i] / counts[i] if counts[i] else None,
                "confidence": confidence[i] / counts[i] if counts[i] else None}
                for i in range(bins)]}
