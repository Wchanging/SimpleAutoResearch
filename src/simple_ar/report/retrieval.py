from __future__ import annotations

from simple_ar.report.schema import ReportContext, SourceHandle


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
