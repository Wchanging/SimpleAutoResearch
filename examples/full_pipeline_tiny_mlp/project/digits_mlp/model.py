from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from digits_mlp.data import one_hot


@dataclass
class TinyMLP:
    """A small one-hidden-layer MLP implemented with NumPy.

    The baseline is intentionally modest: it is fast and deterministic, but it
    leaves room for a code-task agent to improve accuracy by changing training
    settings or optimization logic.
    """

    input_dim: int = 64
    hidden_dim: int = 16
    output_dim: int = 10
    learning_rate: float = 0.08
    seed: int = 7

    def __post_init__(self) -> None:
        rng = np.random.default_rng(self.seed)
        self.w1 = rng.normal(
            loc=0.0,
            scale=np.sqrt(2.0 / self.input_dim),
            size=(self.input_dim, self.hidden_dim),
        ).astype(np.float32)
        self.b1 = np.zeros(self.hidden_dim, dtype=np.float32)
        self.w2 = rng.normal(
            loc=0.0,
            scale=np.sqrt(2.0 / self.hidden_dim),
            size=(self.hidden_dim, self.output_dim),
        ).astype(np.float32)
        self.b2 = np.zeros(self.output_dim, dtype=np.float32)

    def fit(
        self,
        features: np.ndarray,
        labels: np.ndarray,
        *,
        epochs: int = 12,
        batch_size: int = 128,
    ) -> "TinyMLP":
        """Train the MLP with mini-batch gradient descent."""
        targets = one_hot(labels, class_count=self.output_dim)
        sample_count = features.shape[0]
        for _ in range(epochs):
            for start in range(0, sample_count, batch_size):
                stop = min(start + batch_size, sample_count)
                batch_features = features[start:stop]
                batch_targets = targets[start:stop]
                self._train_batch(batch_features, batch_targets)
        return self

    def predict(self, features: np.ndarray) -> np.ndarray:
        """Return predicted class ids."""
        return np.argmax(self.predict_proba(features), axis=1)

    def predict_proba(self, features: np.ndarray) -> np.ndarray:
        """Return class probabilities for each row."""
        _, _, probabilities = self._forward(features)
        return probabilities

    def parameter_count(self) -> int:
        """Return the number of trainable scalar parameters."""
        return int(self.w1.size + self.b1.size + self.w2.size + self.b2.size)

    def _train_batch(self, features: np.ndarray, targets: np.ndarray) -> None:
        z1, hidden, probabilities = self._forward(features)
        batch_size = features.shape[0]

        d_logits = (probabilities - targets) / batch_size
        d_w2 = hidden.T @ d_logits
        d_b2 = d_logits.sum(axis=0)
        d_hidden = d_logits @ self.w2.T
        d_z1 = d_hidden * (z1 > 0.0)
        d_w1 = features.T @ d_z1
        d_b1 = d_z1.sum(axis=0)

        self.w2 -= self.learning_rate * d_w2
        self.b2 -= self.learning_rate * d_b2
        self.w1 -= self.learning_rate * d_w1
        self.b1 -= self.learning_rate * d_b1

    def _forward(
        self,
        features: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        z1 = features @ self.w1 + self.b1
        hidden = np.maximum(z1, 0.0)
        logits = hidden @ self.w2 + self.b2
        logits = logits - logits.max(axis=1, keepdims=True)
        exp_logits = np.exp(logits)
        probabilities = exp_logits / exp_logits.sum(axis=1, keepdims=True)
        return z1, hidden, probabilities
