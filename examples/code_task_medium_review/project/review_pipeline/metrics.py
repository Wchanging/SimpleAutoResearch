from __future__ import annotations


def classification_metrics(y_true: list[str], y_pred: list[str]) -> dict[str, float]:
    """Compute accuracy and macro-F1 for binary labels."""

    if len(y_true) != len(y_pred):
        raise ValueError("y_true and y_pred must have the same length")
    if not y_true:
        raise ValueError("at least one labelled example is required")
    labels = sorted(set(y_true) | set(y_pred))
    accuracy = sum(1 for actual, pred in zip(y_true, y_pred) if actual == pred) / len(y_true)
    f1_scores = [_f1_for_label(label, y_true, y_pred) for label in labels]
    macro_f1 = sum(f1_scores) / len(f1_scores)
    return {
        "accuracy": accuracy,
        "macro_f1": macro_f1,
    }


def _f1_for_label(label: str, y_true: list[str], y_pred: list[str]) -> float:
    tp = sum(1 for actual, pred in zip(y_true, y_pred) if actual == label and pred == label)
    fp = sum(1 for actual, pred in zip(y_true, y_pred) if actual != label and pred == label)
    fn = sum(1 for actual, pred in zip(y_true, y_pred) if actual == label and pred != label)
    if tp == 0:
        return 0.0
    precision = tp / (tp + fp)
    recall = tp / (tp + fn)
    return 2 * precision * recall / (precision + recall)
