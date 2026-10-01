from __future__ import annotations

import math
import re

from simple_ar.report.schema import ReportContext, SourceHandle
from simple_ar.research.contracts import TextChunk


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
    scored.sort(key=lambda row: (-row[0], row[1]))
    return [chunks[index] for _, index in scored[:limit]]


class ReportSourceResolver:
    """Read-only resolver for report source handles."""

    def __init__(self, context: ReportContext) -> None:
        self.context = context
        self._handles = {handle.handle: handle for handle in context.source_handles}

    def get(self, handle: str) -> SourceHandle | None:
        """Return one source handle by id."""
        return self._handles.get(handle)

    def find_by_paper(self, paper_id: str) -> list[SourceHandle]:
        """Return handles related to one paper id."""
        return [
            handle
            for handle in self.context.source_handles
            if handle.paper_id == paper_id or handle.handle == f"paper:{paper_id}"
        ]

    def find_by_citation_key(self, citation_key: str) -> list[SourceHandle]:
        """Return handles related to one model-facing citation key."""
        key = citation_key.strip()
        if not key:
            return []
        paper_id = self.context.citation_key_map.get(key.upper()) or self.context.citation_key_map.get(key)
        if paper_id:
            return self.find_by_paper(paper_id)
        return [handle for handle in self.context.source_handles if handle.citation_key == key]

    def search(self, query: str, *, limit: int = 5) -> list[SourceHandle]:
        """Lightweight lexical search over handle title/summary/metadata."""
        terms = {term.lower() for term in query.split() if len(term) > 2}
        if not terms:
            return []
        scored: list[tuple[int, SourceHandle]] = []
        for handle in self.context.source_handles:
            haystack = (
                handle.title
                + "\n"
                + handle.summary
                + "\n"
                + " ".join(str(value) for value in handle.metadata.values())
            ).lower()
            score = sum(1 for term in terms if term in haystack)
            if score:
                scored.append((score, handle))
        scored.sort(key=lambda item: (-item[0], item[1].handle))
        return [handle for _, handle in scored[:limit]]
