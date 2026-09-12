from __future__ import annotations

import json
from typing import Any


class ExperimentTemplateError(RuntimeError):
    """Raised when an experiment plan asks for an unsupported template."""


SUPPORTED_TEMPLATES = {"toy_text_classification", "csv_text_classification"}


def build_experiment_code(plan: dict[str, Any]) -> str:
    """Render a generated experiment script from a safe template.

    Args:
        plan: Experiment plan produced by the design stage.

    Returns:
        Complete Python source code for ``experiment.py``.

    Raises:
        ExperimentTemplateError: If the plan requests an unknown template.
    """
    template = str(plan.get("template", "toy_text_classification"))
    if template not in SUPPORTED_TEMPLATES:
        raise ExperimentTemplateError(f"Unsupported experiment template: {template}")
    if template == "csv_text_classification":
        return _csv_text_classification_code()
    return _toy_text_classification_code(plan)


def _csv_text_classification_code() -> str:
    """The existing bag-of-words baseline, fitted only on prepared train rows."""
    return '''"""Prepared CSV baseline; scores are measured, not template constants."""
import os
for variable in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[variable] = "1"
import json
from pathlib import Path
from sklearn.feature_extraction.text import CountVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, f1_score
from sklearn.pipeline import make_pipeline

def main():
    rows = json.loads(Path("dataset.json").read_text(encoding="utf-8"))
    train = [row for row in rows if row["split"] == "train"]
    evaluation = [row for row in rows if row["split"] == "eval"]
    model = make_pipeline(CountVectorizer(lowercase=True), LogisticRegression(max_iter=200, random_state=0))
    model.fit([row["text"] for row in train], [row["label"] for row in train])
    predictions = model.predict([row["text"] for row in evaluation])
    truth = [row["label"] for row in evaluation]
    print("accuracy:", float(accuracy_score(truth, predictions)))
    print("macro_f1:", float(f1_score(truth, predictions, average="macro", zero_division=0)))
    print("train_examples:", len(train))
    print("eval_examples:", len(evaluation))

if __name__ == "__main__":
    main()
'''


def _toy_text_classification_code(plan: dict[str, Any]) -> str:
    """Generate the toy spam-classification experiment script."""
    experiment_name = json.dumps(str(plan.get("name", "toy_text_classification")))
    hypothesis = json.dumps(str(plan.get("hypothesis", "")))
    return f'''"""Generated toy text-classification experiment.

This script is generated from a fixed SimpleAutoResearch template. It compares
keyword rules against bag-of-words logistic regression on a tiny built-in spam
classification dataset.
"""

from __future__ import annotations

from sklearn.feature_extraction.text import CountVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, precision_score, recall_score
from sklearn.pipeline import make_pipeline


EXPERIMENT_NAME = {experiment_name}
HYPOTHESIS = {hypothesis}

TRAIN_ROWS = [
    ("claim your free prize now", 1),
    ("urgent winner click to claim cash", 1),
    ("limited offer free bonus today", 1),
    ("win money now click here", 1),
    ("cheap meds available now", 1),
    ("team meeting agenda attached", 0),
    ("please review the project notes", 0),
    ("lunch plans with the team tomorrow", 0),
    ("your invoice has been approved", 0),
    ("can we reschedule our call", 0),
    ("free for lunch after the meeting", 0),
    ("project bonus budget was approved", 0),
]

TEST_ROWS = [
    ("free cash prize waiting", 1),
    ("urgent offer click now", 1),
    ("winner claim your bonus", 1),
    ("project meeting notes attached", 0),
    ("free lunch with the project team", 0),
    ("project bonus budget update", 0),
    ("can we have lunch tomorrow", 0),
    ("invoice for last month", 0),
]

KEYWORDS = {{
    "bonus",
    "cash",
    "claim",
    "click",
    "free",
    "limited",
    "meds",
    "money",
    "offer",
    "prize",
    "urgent",
    "win",
    "winner",
}}


def keyword_predict(text: str) -> int:
    tokens = {{token.strip(".,!?;:").lower() for token in text.split()}}
    return int(bool(tokens & KEYWORDS))


def metric_bundle(prefix: str, truth: list[int], predictions: list[int]) -> dict[str, float]:
    return {{
        f"{{prefix}}_accuracy": accuracy_score(truth, predictions),
        f"{{prefix}}_precision": precision_score(truth, predictions, zero_division=0),
        f"{{prefix}}_recall": recall_score(truth, predictions, zero_division=0),
    }}


def main() -> None:
    train_texts = [text for text, _label in TRAIN_ROWS]
    train_labels = [label for _text, label in TRAIN_ROWS]
    test_texts = [text for text, _label in TEST_ROWS]
    test_labels = [label for _text, label in TEST_ROWS]

    keyword_predictions = [keyword_predict(text) for text in test_texts]
    model = make_pipeline(
        CountVectorizer(lowercase=True),
        LogisticRegression(max_iter=1000, random_state=0),
    )
    model.fit(train_texts, train_labels)
    model_predictions = list(model.predict(test_texts))

    metrics = {{}}
    metrics.update(metric_bundle("keyword", test_labels, keyword_predictions))
    metrics.update(metric_bundle("bow_logreg", test_labels, model_predictions))
    metrics["accuracy_delta"] = metrics["bow_logreg_accuracy"] - metrics["keyword_accuracy"]

    print(f"experiment_name: {{EXPERIMENT_NAME}}")
    print(f"hypothesis_chars_text: {{len(HYPOTHESIS)}} chars")
    for name in sorted(metrics):
        print(f"{{name}}: {{metrics[name]:.6f}}")


if __name__ == "__main__":
    main()
'''


__all__ = ["ExperimentTemplateError", "SUPPORTED_TEMPLATES", "build_experiment_code"]
