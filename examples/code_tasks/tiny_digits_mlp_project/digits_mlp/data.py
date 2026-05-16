from __future__ import annotations

import numpy as np
from sklearn.datasets import load_digits
from sklearn.model_selection import train_test_split


def load_digits_split(
    *,
    test_size: float = 0.25,
    seed: int = 7,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Return a deterministic train/test split for the bundled digits dataset.

    The dataset ships with scikit-learn, so the benchmark never downloads data.
    Features are scaled to ``[0, 1]`` because the original pixel values are in
    ``[0, 16]``.
    """
    dataset = load_digits()
    features = dataset.data.astype(np.float32) / 16.0
    targets = dataset.target.astype(np.int64)
    return train_test_split(
        features,
        targets,
        test_size=test_size,
        random_state=seed,
        stratify=targets,
    )


def one_hot(labels: np.ndarray, *, class_count: int = 10) -> np.ndarray:
    """Convert integer labels into a one-hot matrix."""
    encoded = np.zeros((labels.shape[0], class_count), dtype=np.float32)
    encoded[np.arange(labels.shape[0]), labels] = 1.0
    return encoded
