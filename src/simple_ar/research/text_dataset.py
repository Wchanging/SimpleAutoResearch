"""Inspect the currently supported small, explicitly split CSV text dataset."""

import csv
import hashlib
import io
from pathlib import Path


def read_text_dataset(path: Path, *, max_bytes: int = 10_000_000, max_rows: int = 10_000) -> dict:
    """Read complete bounded input; never silently truncate an experiment dataset."""
    with path.open("rb") as stream:
        raw = stream.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise ValueError(f"CSV exceeds the supported {max_bytes}-byte preparation limit.")
    reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig")))
    if not {"text", "label", "split"}.issubset(reader.fieldnames or ()):
        raise ValueError("Text CSV requires text, label and split columns.")
    rows = []
    for index, row in enumerate(reader, start=1):
        if index > max_rows:
            raise ValueError(f"CSV exceeds the supported {max_rows}-row preparation limit.")
        if not row["text"] or not row["text"].strip() or not row["label"] or not row["label"].strip():
            raise ValueError(f"CSV row {index} needs non-empty text and label.")
        if row["split"] not in {"train", "eval"}:
            raise ValueError(f"CSV row {index} split must be train or eval; no split is inferred.")
        rows.append({key: row[key] for key in ("text", "label", "split")})
    train = [row for row in rows if row["split"] == "train"]
    evaluation = [row for row in rows if row["split"] == "eval"]
    if not train or not evaluation or len({row["label"] for row in train}) < 2:
        raise ValueError("Text baseline needs train/eval rows and at least two training labels.")
    train_texts = {" ".join(row["text"].casefold().split()) for row in train}
    overlap = sum(" ".join(row["text"].casefold().split()) in train_texts for row in evaluation)
    return {"rows": rows, "source": str(path), "sha256": hashlib.sha256(raw).hexdigest(),
            "train_rows": len(train), "eval_rows": len(evaluation),
            "overlapping_eval_rows": overlap,
            "limitations": ([f"{overlap} evaluation rows repeat normalized training text; possible leakage."] if overlap else [])}
