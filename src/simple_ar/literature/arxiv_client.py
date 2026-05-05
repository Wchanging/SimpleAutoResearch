from __future__ import annotations

from typing import Iterable

import arxiv

from simple_ar.literature.models import Paper, normalize_paper_id


class LiteratureSearchError(RuntimeError):
    """Raised when a literature provider cannot return usable metadata."""


class ArxivSearchClient:
    """Small arXiv metadata search client.

    Args:
        page_size: Number of records requested per arXiv API page.
        delay_seconds: Delay between arXiv API requests.
        num_retries: Retry count delegated to the ``arxiv`` package.
    """

    def __init__(
        self,
        *,
        page_size: int = 10,
        delay_seconds: float = 3.0,
        num_retries: int = 3,
    ) -> None:
        self._client = arxiv.Client(
            page_size=page_size,
            delay_seconds=delay_seconds,
            num_retries=num_retries,
        )

    def search(self, query: str, *, max_results: int = 5) -> list[Paper]:
        """Search arXiv and return normalized paper metadata.

        Args:
            query: arXiv search query.
            max_results: Maximum number of papers to return.

        Returns:
            Deduplicated papers in arXiv relevance order.

        Raises:
            LiteratureSearchError: If the query is empty, the limit is invalid,
                or the provider request fails.
        """
        query = query.strip()
        if not query:
            raise LiteratureSearchError("arXiv query is empty")
        if max_results < 1:
            raise LiteratureSearchError("max_results must be at least 1")

        try:
            search = arxiv.Search(
                query=query,
                max_results=max_results,
                sort_by=arxiv.SortCriterion.Relevance,
                sort_order=arxiv.SortOrder.Descending,
            )
            return _dedupe_papers(_paper_from_result(result) for result in self._client.results(search))
        except Exception as exc:
            raise LiteratureSearchError(f"arXiv search failed: {exc}") from exc


def _paper_from_result(result: arxiv.Result) -> Paper:
    """Convert an ``arxiv.Result`` into the project paper schema."""
    source_id = result.get_short_id()
    return Paper(
        id=normalize_paper_id(source_id),
        title=_clean_space(result.title),
        authors=[str(author) for author in result.authors],
        abstract=_clean_space(result.summary),
        url=result.entry_id,
        published=result.published.date().isoformat() if result.published else None,
        categories=list(result.categories),
        source="arxiv",
        source_id=source_id,
        doi=result.doi,
    )


def _dedupe_papers(papers: Iterable[Paper]) -> list[Paper]:
    """Drop duplicate paper ids while preserving result order."""
    seen: set[str] = set()
    unique: list[Paper] = []
    for paper in papers:
        if paper.id in seen:
            continue
        seen.add(paper.id)
        unique.append(paper)
    return unique


def _clean_space(text: str) -> str:
    """Collapse whitespace from arXiv text fields."""
    return " ".join(text.split())
