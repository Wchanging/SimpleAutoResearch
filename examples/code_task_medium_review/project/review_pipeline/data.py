from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ReviewExample:
    """One labelled review example.

    Args:
        text: Short product-review style input.
        label: ``positive`` or ``negative``.
        split: ``train`` or ``eval``.
    """

    text: str
    label: str
    split: str


def load_reviews() -> list[ReviewExample]:
    """Return a deterministic tiny corpus with phrase-level edge cases."""

    rows = [
        ("fast stable checkout and good docs", "positive", "train"),
        ("love the stable release and useful logs", "positive", "train"),
        ("clear interface with excellent export", "positive", "train"),
        ("slow confusing setup with broken output", "negative", "train"),
        ("bad defaults waste time", "negative", "train"),
        ("crash issue blocks training", "negative", "train"),
        ("not bad support after the update", "positive", "eval"),
        ("no issues after patch and useful workflow", "positive", "eval"),
        ("not slow anymore after the fix", "positive", "eval"),
        ("good performance with no crash", "positive", "eval"),
        ("useful logs and stable progress", "positive", "eval"),
        ("fast clear release with useful docs", "positive", "eval"),
        ("excellent support and stable export", "positive", "eval"),
        ("not good for long reports", "negative", "eval"),
        ("hardly useful when data is noisy", "negative", "eval"),
        ("good looking but broken scheduler", "negative", "eval"),
        ("no clear error when it fails", "negative", "eval"),
        ("unstable slow and confusing interface", "negative", "eval"),
        ("broken slow checkout wastes time", "negative", "eval"),
        ("confusing crash issue after update", "negative", "eval"),
    ]
    return [ReviewExample(text=text, label=label, split=split) for text, label, split in rows]


def split_reviews(rows: list[ReviewExample]) -> tuple[list[ReviewExample], list[ReviewExample]]:
    """Split examples by their explicit split label."""

    train = [row for row in rows if row.split == "train"]
    eval_rows = [row for row in rows if row.split == "eval"]
    return train, eval_rows
