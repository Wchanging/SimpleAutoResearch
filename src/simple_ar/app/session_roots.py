"""Small helpers for creating isolated application session roots."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import re


def new_research_session_root(output_root: str | Path, topic: str) -> Path:
    """Create one unique timestamped directory below an application root."""

    parent = Path(output_root)
    parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    slug = _slug(topic)
    candidate = parent / f"{stamp}-{slug}"
    suffix = 2
    while candidate.exists():
        candidate = parent / f"{stamp}-{slug}-{suffix:02d}"
        suffix += 1
    candidate.mkdir()
    return candidate


def _slug(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return normalized[:80] or "research-topic"


__all__ = ["new_research_session_root"]
