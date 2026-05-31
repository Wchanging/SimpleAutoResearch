from __future__ import annotations

import re
import threading
import time
from typing import Any

import requests
from pyalex import Works, config as pyalex_config

from simple_ar.literature.models import Paper, normalize_paper_id


class OpenAlexSearchError(RuntimeError):
    """Raised when OpenAlex cannot return usable metadata."""


_MAX_RESULTS = 25
_TIMEOUT_SEC = 20
_REQUEST_GAP_SEC = 0.25
_SELECT_FIELDS = (
    "id,title,display_name,authorships,publication_year,publication_date,"
    "primary_location,doi,ids,abstract_inverted_index,open_access,best_oa_location"
)
_last_request_at = 0.0
_rate_lock = threading.Lock()


class OpenAlexSearchClient:
    """Small OpenAlex metadata search client.

    OpenAlex is used before arXiv because its public API has more generous rate
    limits and indexes many arXiv works through a broader scholarly graph.

    Args:
        mailto: Optional polite-pool contact passed to OpenAlex.
        timeout_sec: Retained for public API compatibility. The pyalex
            transport manages request timing and retry behavior internally.
    """

    def __init__(self, *, mailto: str = "simple-autoresearch@example.com", timeout_sec: int = _TIMEOUT_SEC) -> None:
        self.mailto = mailto
        self.timeout_sec = timeout_sec

    def search(self, query: str, *, max_results: int = 5) -> list[Paper]:
        """Search OpenAlex and return normalized paper metadata.

        Args:
            query: Free-text literature query.
            max_results: Maximum number of papers to return.

        Returns:
            Parsed OpenAlex works in provider relevance order.

        Raises:
            OpenAlexSearchError: If the query is empty, the limit is invalid, or
                the provider request fails.
        """
        query = query.strip()
        if not query:
            raise OpenAlexSearchError("OpenAlex query is empty")
        if max_results < 1:
            raise OpenAlexSearchError("max_results must be at least 1")

        _respect_rate_limit()
        _configure_pyalex(self.mailto)
        try:
            results = (
                Works()
                .search(query)
                .select(_SELECT_FIELDS)
                .get(per_page=min(max_results, _MAX_RESULTS))
            )
        except (requests.RequestException, ValueError, RuntimeError, KeyError) as exc:
            raise OpenAlexSearchError(f"OpenAlex search failed: {exc}") from exc

        if not isinstance(results, list):
            raise OpenAlexSearchError("OpenAlex response did not contain a results list")
        return _dedupe_papers(_paper_from_work(item) for item in results if isinstance(item, dict))


def _paper_from_work(item: dict[str, Any]) -> Paper:
    title = _clean_space(str(item.get("title") or item.get("display_name") or ""))
    ids = item.get("ids") if isinstance(item.get("ids"), dict) else {}
    openalex_id = str(ids.get("openalex") or item.get("id") or "").strip()
    doi = _normalize_doi(str(item.get("doi") or ids.get("doi") or "").strip())
    arxiv_url = str(ids.get("arxiv") or "").strip()
    fulltext_url = _fulltext_url_from_openalex(item)
    url = arxiv_url or fulltext_url or (f"https://doi.org/{doi}" if doi else openalex_id)
    source_id = openalex_id.rsplit("/", maxsplit=1)[-1] if openalex_id else title[:40]
    authors = _authors_from_authorships(item.get("authorships"))
    categories = _categories_from_location(item.get("primary_location"))
    return Paper(
        id=normalize_paper_id(f"openalex-{source_id}"),
        title=title or "Untitled OpenAlex work",
        authors=authors,
        abstract=_abstract_from_inverted_index(item.get("abstract_inverted_index")),
        url=url,
        published=str(item.get("publication_date") or item.get("publication_year") or "") or None,
        categories=categories,
        source="openalex",
        source_id=source_id,
        doi=doi or None,
        fulltext_url=fulltext_url,
    )


def _authors_from_authorships(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    authors: list[str] = []
    for authorship in value:
        if not isinstance(authorship, dict):
            continue
        author = authorship.get("author")
        if isinstance(author, dict):
            name = str(author.get("display_name") or "").strip()
            if name:
                authors.append(name)
    return authors


def _categories_from_location(value: object) -> list[str]:
    if not isinstance(value, dict):
        return []
    source = value.get("source")
    if not isinstance(source, dict):
        return []
    display_name = str(source.get("display_name") or "").strip()
    if not display_name:
        return []
    if re.match(r"^[a-z]{2,}\.[A-Z]{2}$", display_name):
        return [display_name]
    return []


def _abstract_from_inverted_index(value: object) -> str:
    if not isinstance(value, dict):
        return ""
    words: list[tuple[int, str]] = []
    for word, positions in value.items():
        if not isinstance(positions, list):
            continue
        for position in positions:
            if isinstance(position, int):
                words.append((position, str(word)))
    words.sort(key=lambda item: item[0])
    return _clean_space(" ".join(word for _, word in words))


def _normalize_doi(value: str) -> str:
    return value.replace("https://doi.org/", "").replace("http://doi.org/", "").strip()


def _fulltext_url_from_openalex(item: dict[str, Any]) -> str | None:
    best_location = item.get("best_oa_location")
    if isinstance(best_location, dict):
        pdf_url = str(best_location.get("pdf_url") or "").strip()
        if pdf_url:
            return pdf_url
    open_access = item.get("open_access")
    if isinstance(open_access, dict):
        oa_url = str(open_access.get("oa_url") or "").strip()
        if oa_url:
            return oa_url
    if isinstance(best_location, dict):
        landing_url = str(best_location.get("landing_page_url") or "").strip()
        if landing_url:
            return landing_url
    return None


def _dedupe_papers(papers: list[Paper] | Any) -> list[Paper]:
    seen: set[str] = set()
    unique: list[Paper] = []
    for paper in papers:
        if paper.id in seen:
            continue
        seen.add(paper.id)
        unique.append(paper)
    return unique


def _clean_space(text: str) -> str:
    return " ".join(text.split())


def _respect_rate_limit() -> None:
    global _last_request_at
    with _rate_lock:
        elapsed = time.monotonic() - _last_request_at
        if elapsed < _REQUEST_GAP_SEC:
            time.sleep(_REQUEST_GAP_SEC - elapsed)
        _last_request_at = time.monotonic()


def _configure_pyalex(mailto: str) -> None:
    pyalex_config.email = mailto
    pyalex_config.max_retries = max(int(pyalex_config.get("max_retries", 0) or 0), 1)
    pyalex_config.retry_backoff_factor = max(
        float(pyalex_config.get("retry_backoff_factor", 0.0) or 0.0),
        0.25,
    )
