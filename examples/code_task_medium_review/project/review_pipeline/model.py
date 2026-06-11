from __future__ import annotations

from review_pipeline.data import ReviewExample
from review_pipeline.features import extract_features


WORD_WEIGHTS = {
    "bad": -1.4,
    "blocks": -0.7,
    "broken": -1.5,
    "confusing": -1.2,
    "crash": -1.4,
    "error": -0.9,
    "fails": -1.0,
    "issue": -1.2,
    "issues": -1.2,
    "noisy": -0.8,
    "slow": -1.3,
    "unstable": -1.5,
    "waste": -1.4,
    "clear": 0.9,
    "excellent": 1.5,
    "fast": 1.2,
    "fix": 0.5,
    "good": 1.2,
    "love": 1.5,
    "progress": 0.6,
    "stable": 1.1,
    "support": 0.6,
    "useful": 1.3,
    "workflow": 0.5,
}

PHRASE_WEIGHTS: dict[str, float] = {}


class KeywordLinearClassifier:
    """Tiny weighted feature classifier with a scikit-like API."""

    def __init__(self, *, feature_families: list[str], threshold: float = 0.0) -> None:
        self.feature_families = list(feature_families)
        self.threshold = float(threshold)
        self.seen_labels: set[str] = set()

    def fit(self, rows: list[ReviewExample]) -> None:
        """Record labels for diagnostics.

        This project keeps the model intentionally lightweight; the code-task is
        about improving feature extraction and model wiring rather than adding a
        heavy training loop.
        """

        self.seen_labels = {row.label for row in rows}

    def predict_one(self, text: str) -> str:
        score = self.score(text)
        return "positive" if score >= self.threshold else "negative"

    def predict_many(self, rows: list[ReviewExample]) -> list[str]:
        return [self.predict_one(row.text) for row in rows]

    def score(self, text: str) -> float:
        features = extract_features(text, self.feature_families)
        return sum(value * self._weight(name) for name, value in features.items())

    def _weight(self, feature_name: str) -> float:
        if feature_name.startswith("word:"):
            return WORD_WEIGHTS.get(feature_name.removeprefix("word:"), 0.0)
        if feature_name.startswith("phrase:"):
            return PHRASE_WEIGHTS.get(feature_name.removeprefix("phrase:"), 0.0)
        return 0.0

    def model_size(self) -> int:
        return len(WORD_WEIGHTS) + len(PHRASE_WEIGHTS)
