from __future__ import annotations

from spamfilter.rules import SPAM_KEYWORDS


def score_message(text: str) -> float:
    """Return a simple keyword score for a short message."""
    normalized = text.lower()
    hits = sum(1 for keyword in SPAM_KEYWORDS if keyword in normalized)
    return hits / max(1, len(SPAM_KEYWORDS))


def classify(text: str) -> str:
    """Classify a message as ``spam`` or ``ham``."""
    return "spam" if score_message(text) > 0 else "ham"
