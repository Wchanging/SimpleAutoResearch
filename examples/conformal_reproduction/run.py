"""Adapted synthetic check of weighted split conformal under covariate shift.

Reference: Tibshirani et al., Conformal Prediction Under Covariate Shift (2019),
https://arxiv.org/abs/1904.06019. This is not the paper's airfoil experiment.
No fitting is needed: a fixed predictor and exactly known discrete density
ratio isolate the weighting construction. Only the standard library is used.
"""
from __future__ import annotations

import argparse
import bisect
import json
import math
from pathlib import Path
import random


def simulate(*, seed: int = 7, repetitions: int = 3000, calibration_size: int = 200,
             source_positive: float = 0.1, target_positive: float = 0.9, alpha: float = 0.1) -> dict:
    if repetitions < 1 or calibration_size < 1 or not all(0 < v < 1 for v in (source_positive, target_positive, alpha)):
        raise ValueError("Positive sample sizes and probabilities strictly between zero and one are required.")
    rng = random.Random(seed)
    weighted_hits = ordinary_hits = infinite = 0
    for _ in range(repetitions):
        calibration = []
        for _ in range(calibration_size):
            x = int(rng.random() < source_positive)
            # Same Y|X in source and target: only the covariate marginal shifts.
            score = abs(x + rng.gauss(0, 0.1))  # fixed regression function f(x)=0
            weight = target_positive / source_positive if x else (1 - target_positive) / (1 - source_positive)
            calibration.append((score, weight))
        calibration.sort()
        target_x = int(rng.random() < target_positive)
        test_score = abs(target_x + rng.gauss(0, 0.1))
        test_weight = target_positive / source_positive if target_x else (1 - target_positive) / (1 - source_positive)
        # The target-point mass is at infinity, not at its unseen residual.
        cumulative = []
        total = 0.0
        for _, weight in calibration:
            total += weight
            cumulative.append(total)
        index = bisect.bisect_left(cumulative, (1 - alpha) * (total + test_weight))
        weighted_radius = calibration[index][0] if index < calibration_size else math.inf
        ordinary_rank = math.ceil((calibration_size + 1) * (1 - alpha))
        ordinary_radius = calibration[ordinary_rank - 1][0] if ordinary_rank <= calibration_size else math.inf
        weighted_hits += test_score <= weighted_radius
        ordinary_hits += test_score <= ordinary_radius
        infinite += math.isinf(weighted_radius)
    weighted = weighted_hits / repetitions
    # Monte Carlo error of independent calibration/test repetitions, not seeds
    # of a fitted ML model and not a proof of the coverage theorem.
    return {"seed": seed, "repetitions": repetitions, "calibration_size": calibration_size,
            "source_positive": source_positive, "target_positive": target_positive, "alpha": alpha,
            "weighted_coverage": weighted, "ordinary_coverage": ordinary_hits / repetitions,
            "weighted_mc_standard_error": math.sqrt(weighted * (1 - weighted) / repetitions),
            "infinite_radius_fraction": infinite / repetitions,
            "scope": "adapted synthetic check; known ratios; not the published airfoil experiment"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--repetitions", type=int, default=3000)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = simulate(seed=args.seed, repetitions=args.repetitions)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    for name in ("weighted_coverage", "ordinary_coverage", "weighted_mc_standard_error", "infinite_radius_fraction"):
        print(f"METRIC {name}={result[name]:.12g}")


if __name__ == "__main__":
    main()
