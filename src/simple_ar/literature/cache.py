from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any


DEFAULT_CACHE_DIR = Path(".simple_ar_cache") / "literature"
ARXIV_TTL_SEC = 86400


def cache_key(query: str, source: str, limit: int) -> str:
    """Build a deterministic cache key for literature search parameters.

    Args:
        query: Search query text.
        source: Literature provider name, such as ``arxiv``.
        limit: Maximum requested result count.

    Returns:
        Short SHA-256 key suitable for a local JSON cache filename.
    """
    raw = f"{query.strip().lower()}|{source.strip().lower()}|{limit}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def get_cached(
    query: str,
    source: str,
    limit: int,
    *,
    cache_dir: Path = DEFAULT_CACHE_DIR,
    ttl_sec: float = ARXIV_TTL_SEC,
) -> list[dict[str, Any]] | None:
    """Read cached search rows when the entry exists and has not expired.

    Args:
        query: Search query text.
        source: Literature provider name.
        limit: Maximum requested result count.
        cache_dir: Directory containing cache JSON files.
        ttl_sec: Maximum accepted cache age in seconds.

    Returns:
        Cached paper rows, or ``None`` on miss, expiry, or invalid data.
    """
    path = _cache_path(query, source, limit, cache_dir)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        timestamp = float(data.get("timestamp", 0))
        if time.time() - timestamp > ttl_sec:
            return None
        rows = data.get("papers")
        if not isinstance(rows, list):
            return None
        return [row for row in rows if isinstance(row, dict)]
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None


def put_cache(
    query: str,
    source: str,
    limit: int,
    papers: list[dict[str, Any]],
    *,
    cache_dir: Path = DEFAULT_CACHE_DIR,
) -> Path:
    """Write search rows into the local literature cache.

    Args:
        query: Search query text.
        source: Literature provider name.
        limit: Maximum requested result count.
        papers: JSON-serializable paper rows.
        cache_dir: Directory where the cache entry should be stored.

    Returns:
        Path to the written cache file.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = _cache_path(query, source, limit, cache_dir)
    payload = {
        "query": query,
        "source": source,
        "limit": limit,
        "timestamp": time.time(),
        "papers": papers,
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def _cache_path(query: str, source: str, limit: int, cache_dir: Path) -> Path:
    """Return the cache file path for a search parameter tuple."""
    return cache_dir / f"{cache_key(query, source, limit)}.json"
