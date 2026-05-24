from __future__ import annotations

from pathlib import Path

from simple_ar.literature.models import Paper, normalize_paper_id
from simple_ar.research.sources import SearchQuery, SearchResponse


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
        """Return local text-like files whose names or contents match the query."""
        query = request.query.lower().strip()
        papers: list[Paper] = []
        for path in self._paths:
            if len(papers) >= request.max_results:
                break
            if not path.exists() or not path.is_file() or path.suffix.lower() not in {".md", ".txt"}:
                continue
            text = _safe_read(path)
            haystack = f"{path.name}\n{text}".lower()
            if query and query not in haystack:
                continue
            papers.append(
                Paper(
                    id=normalize_paper_id(f"local-{path.stem}"),
                    title=path.stem.replace("_", " ").replace("-", " ").strip() or path.name,
                    authors=[],
                    abstract=_abstract(text),
                    url=str(path),
                    source=self.source_name,
                    source_id=str(path),
                )
            )
        return SearchResponse(source=self.source_name, query=request.query, papers=papers)


def _safe_read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="replace")


def _abstract(text: str, *, limit: int = 1200) -> str:
    compact = " ".join(text.split())
    return compact[:limit]
