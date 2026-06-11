"""Tiny spam-filter package used by SimpleAutoResearch code-task examples."""

from spamfilter.model import classify, score_message

__all__ = ["classify", "score_message"]
