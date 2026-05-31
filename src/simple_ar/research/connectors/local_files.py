from __future__ import annotations

from pathlib import Path
import re

from simple_ar.literature.models import Paper, normalize_paper_id
from simple_ar.research.sources.base import SearchQuery, SearchResponse


class LocalFileConnector:
    """Connector that exposes user-provided local text documents as papers.

    This is intentionally conservative: it does not parse PDFs yet. It gives
    V2.3 a stable connector boundary for local `.txt` and `.md` files while the
    full document-ingestion layer is built out.
    """

    source_name = "local_files"

    def __init__(self, paths: list[str] | None = None) -> None:
        self._paths = [Path(path) for path in paths or []]

    def search(self, request: SearchQuery) -> SearchResponse:
        """Return local text-like files whose names or contents match the query.

        Local files are usually user-provided notes rather than provider search
        indexes, so matching uses lightweight keyword overlap instead of exact
        query-substring matching. This keeps local evidence stable when the LLM
        planner normalizes a paper-search query for arXiv/OpenAlex.
        """
        query = request.query.lower().strip()
        query_terms = _terms(query)
        matches: list[tuple[int, Paper]] = []
        for path in self._paths:
            if not path.exists() or not path.is_file() or path.suffix.lower() not in {".md", ".txt"}:
                continue
            text = _safe_read(path)
            haystack = f"{path.name}\n{text}".lower()
            score = _match_score(query, query_terms, haystack)
            if query and score <= 0:
                continue
            matches.append(
                (
                    score,
                    Paper(
                        id=normalize_paper_id(f"local-{path.stem}"),
                        title=path.stem.replace("_", " ").replace("-", " ").strip() or path.name,
                        authors=[],
                        abstract=_abstract(text),
                        url=str(path),
                        source=self.source_name,
                        source_id=str(path),
                    ),
                )
            )
        matches.sort(key=lambda item: item[0], reverse=True)
        papers = [paper for _, paper in matches[: request.max_results]]
        return SearchResponse(source=self.source_name, query=request.query, papers=papers)


def _match_score(query: str, query_terms: set[str], haystack: str) -> int:
    if not query:
        return 1
    if query in haystack:
        return 100 + len(query_terms)
    haystack_terms = _terms(haystack)
    overlap = query_terms & haystack_terms
    if not overlap:
        return 0
    required = 1 if len(query_terms) <= 2 else max(2, len(query_terms) // 3)
    return len(overlap) if len(overlap) >= required else 0


def _terms(text: str) -> set[str]:
    stopwords = {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "by",
        "for",
        "from",
        "in",
        "is",
        "of",
        "on",
        "or",
        "the",
        "to",
        "with",
    }
    return {
        word
        for word in re.findall(r"[a-z0-9]{2,}", text.lower())
        if word not in stopwords
    }


def _safe_read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="replace")


def _abstract(text: str, *, limit: int = 1200) -> str:
    compact = " ".join(text.split())
    return compact[:limit]
