from __future__ import annotations

import re
from collections import Counter


TOKEN_PATTERN = re.compile(r"[a-z']+")


def tokenize(text: str) -> list[str]:
    """Tokenize a review into lowercase word tokens."""

    return TOKEN_PATTERN.findall(text.lower())


def extract_features(text: str, feature_families: list[str]) -> dict[str, float]:
    """Extract sparse model features for one review.

    The current baseline intentionally supports only word features. The bundled
    code-task asks the model to add phrase-level features and wire them into the
    classifier and config without changing the evaluation data or tests.
    """

    tokens = tokenize(text)
    features: Counter[str] = Counter()
    if "word" in feature_families:
        for token in tokens:
            features[f"word:{token}"] += 1.0
    return dict(features)


def feature_family_summary(feature_families: list[str]) -> str:
    """Return a compact description for logs and tests."""

    return ", ".join(feature_families) if feature_families else "none"
