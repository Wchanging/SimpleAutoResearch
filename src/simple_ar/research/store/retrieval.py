"""Read-only lexical ranking and bounded views of already retained source text."""
from __future__ import annotations

import math
import re
from typing import Any

from simple_ar.research.contracts import TextChunk


def order_source_chunks(chunks: list[TextChunk]) -> list[TextChunk]:
    """Restore retained source order when every chunk has comparable positions.

    Ingest may budget the body before front matter. Storage priority is not
    paragraph adjacency. Missing/mixed locations retain legacy order rather
    than guessing from titles or IDs; even located retained chunks may have gaps.
    """
    if (chunks and len({(row.document_id, row.source_path) for row in chunks}) == 1
            and all(type(row.line_start) is int and row.line_start > 0 for row in chunks)):
        return sorted(chunks, key=lambda row: row.line_start)
    return list(chunks)


def _caption_match(text: str, query: str) -> re.Match[str] | None:
    """Locate an explicitly requested figure/table label, not its prose mention.

    PDF extraction can move a caption away from its mentioning paragraph. A
    line-start caption is a navigation cue, never a quality/support verdict.
    """
    labels = re.findall(r"\b(table|figure|fig\.?)\s*([A-Z]?\d+(?:[.-]\d+)*[a-z]?|[IVX]+)\b", query, re.I)
    for kind, number in labels:
        kinds = r"(?:figure|fig\.?)" if kind.casefold().startswith("fig") else "table"
        match = re.search(rf"(?im)^[ \t]*{kinds}[ \t]*{re.escape(number)}\b[ \t]*[:：.]", text)
        if match:
            return match
    return None


def source_query_terms(query: str) -> set[str]:
    """Lexical anchors, not a semantic relevance or evidence verdict."""
    terms = set(re.findall(r"[^\W\d_]{3,}|\d+(?:\.\d+)?", query.casefold()))
    for run in re.findall(r"[\u3400-\u9fff]+", query):
        terms.update(run[index:index + 2] for index in range(len(run) - 1))
    return terms


def rank_source_chunks(chunks: list[TextChunk], query: str, *, limit: int) -> list[TextChunk]:
    """Rank retained source text without creating another index or opening files."""
    terms = source_query_terms(query)
    if not terms:
        return []
    overlaps = [source_query_terms(chunk.text) & terms for chunk in chunks]
    weights = {term: 1 + math.log((len(chunks) + 1) / (1 + sum(term in row for row in overlaps)))
               for term in terms}
    phrase = " ".join(re.findall(r"\w+", query.casefold()))
    phrase_weight = math.fsum(weights[term] for term in sorted(terms))
    scored = [(math.fsum(weights[term] for term in sorted(matches)) +
               (phrase_weight if phrase and f" {phrase} " in
                " " + " ".join(re.findall(r"\w+", chunk.text.casefold())) + " " else 0), index)
              for index, (chunk, matches) in enumerate(zip(chunks, overlaps)) if matches]
    # Full quoted prose can score above the underlying table. Explicit labels
    # route to a retained caption first; ordinary queries keep lexical order.
    scored.sort(key=lambda row: (_caption_match(chunks[row[1]].text, query) is None, -row[0], row[1]))
    return [chunks[index] for _, index in scored[:limit]]


def source_chunk_views(chunks: list[TextChunk], *, query: str = "", max_chars: int = 4800,
                       max_chunk_chars: int | None = None) -> list[dict[str, Any]]:
    """Expose bounded source text and exact offsets; no identity/support verdict."""
    if max_chars < 1 or (max_chunk_chars is not None and max_chunk_chars < 1):
        raise ValueError("Source view character budgets must be positive.")
    remaining = max_chars
    rows = []
    terms = source_query_terms(query)
    for chunk in chunks:
        limit = remaining // (len(chunks) - len(rows))
        if max_chunk_chars is not None:
            limit = min(limit, max_chunk_chars)
        exact = _caption_match(chunk.text, query) or (re.search(re.escape(query.strip()), chunk.text, re.I) if query.strip() else None)
        matches = [(len(hits), -len(term), hits[0].start()) for term in sorted(terms)
                   if (hits := list(re.finditer(re.escape(term), chunk.text, re.I)))]
        anchor = exact.start() if exact else (min(matches)[2] if matches else 0)
        start = min(max(0, anchor - limit // 3), max(0, len(chunk.text) - limit))
        text = chunk.text[start:start + limit]
        remaining -= len(text)
        rows.append({"chunk_id": chunk.chunk_id, "document_id": chunk.document_id,
            "text": text, "character_start": start, "character_end": start + len(text),
            "total_characters": len(chunk.text), "truncated": len(text) < len(chunk.text),
            "source_path": chunk.source_path, "page": chunk.page, "line_start": chunk.line_start,
            "line_end": chunk.line_end, "extraction_status": chunk.metadata.get("extraction_status", "unknown")})
    return rows
