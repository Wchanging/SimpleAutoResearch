from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from simple_ar.artifacts import write_json
from simple_ar.retrieval.chunking import ArtifactChunk, build_artifact_chunks
from simple_ar.retrieval.index import build_artifact_index


WORD_RE = re.compile(r"[\w.-]+", re.UNICODE)
SNIPPET_CHARS = 360


def search_artifacts(
    run_dir: Path,
    query: str,
    *,
    top_k: int = 8,
    write: bool = True,
) -> dict[str, Any]:
    """Build retrieval artifacts and run lexical search over local chunks.

    Args:
        run_dir: Root run directory to inspect.
        query: User query to match against artifact text and paths.
        top_k: Maximum number of matches to return.
        write: When true, save ``artifact_index.json``,
            ``artifact_chunks.jsonl``, and ``artifact_search_results.json``.

    Returns:
        A JSON-serializable search result with scored snippets and provenance.
    """
    root = Path(run_dir)
    index = build_artifact_index(root, write=write)
    chunks = build_artifact_chunks(root, index=index, write=write)
    matches = search_chunks(chunks, query, top_k=top_k)
    results = {
        "schema_version": 1,
        "query": query,
        "top_k": top_k,
        "generated_at": _utcnow_iso(),
        "chunk_count": len(chunks),
        "match_count": len(matches),
        "matches": matches,
    }
    if write:
        write_json(root / "artifact_search_results.json", results)
    return results


def search_chunks(
    chunks: list[ArtifactChunk],
    query: str,
    *,
    top_k: int = 8,
) -> list[dict[str, Any]]:
    """Score prebuilt chunks using a simple deterministic lexical model."""
    terms = _terms(query)
    if not terms:
        return []
    phrase = query.strip().lower()
    scored: list[dict[str, Any]] = []
    for chunk in chunks:
        score = _score_chunk(chunk, terms, phrase)
        if score <= 0:
            continue
        scored.append(
            {
                "path": chunk.path,
                "kind": chunk.kind,
                "chunk_kind": chunk.chunk_kind,
                "line_start": chunk.line_start,
                "line_end": chunk.line_end,
                "title": chunk.title,
                "score": round(score, 3),
                "snippet": _snippet(chunk.text, terms),
            }
        )

    scored.sort(key=lambda item: (-float(item["score"]), str(item["path"]), int(item["line_start"])))
    return scored[: max(0, top_k)]


def _score_chunk(chunk: ArtifactChunk, terms: list[str], phrase: str) -> float:
    text = chunk.text.lower()
    path = chunk.path.lower()
    title = (chunk.title or "").lower()
    score = 0.0
    if phrase and phrase in text:
        score += 5.0
    for term in terms:
        count = text.count(term)
        if count:
            score += min(count, 6) * 1.0
        if term in path:
            score += 2.0
        if term in title:
            score += 1.5
    if chunk.kind in {"json", "jsonl"}:
        score *= 1.05
    if "result" in path or "metric" in path:
        score *= 1.1
    return score


def _snippet(text: str, terms: list[str]) -> str:
    normalized = re.sub(r"\s+", " ", text).strip()
    if len(normalized) <= SNIPPET_CHARS:
        return normalized
    lower = normalized.lower()
    first = min((lower.find(term) for term in terms if term in lower), default=0)
    start = max(0, first - SNIPPET_CHARS // 3)
    end = min(len(normalized), start + SNIPPET_CHARS)
    snippet = normalized[start:end].strip()
    if start > 0:
        snippet = "..." + snippet
    if end < len(normalized):
        snippet += "..."
    return snippet


def _terms(query: str) -> list[str]:
    terms = [term.lower() for term in WORD_RE.findall(query)]
    seen: set[str] = set()
    unique_terms: list[str] = []
    for term in terms:
        if term not in seen:
            seen.add(term)
            unique_terms.append(term)
    return unique_terms


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
