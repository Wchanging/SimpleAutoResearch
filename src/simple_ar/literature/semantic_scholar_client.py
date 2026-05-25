from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from simple_ar.literature.models import Paper, normalize_paper_id


class SemanticScholarSearchError(RuntimeError):
    """Raised when Semantic Scholar cannot return usable metadata."""


_BASE_URL = "https://api.semanticscholar.org/graph/v1/paper/search"
_FIELDS = "paperId,title,abstract,year,venue,citationCount,authors,externalIds,url"
_MAX_RESULTS = 25
_TIMEOUT_SEC = 20
_REQUEST_GAP_SEC = 1.5
_MAX_RETRIES = 2
_last_request_at = 0.0
_rate_lock = threading.Lock()


class SemanticScholarSearchClient:
    """Small Semantic Scholar Graph API search client.

    Args:
        api_key: Optional Semantic Scholar API key.
        timeout_sec: Request timeout in seconds.
    """

    def __init__(self, *, api_key: str = "", timeout_sec: int = _TIMEOUT_SEC) -> None:
        self.api_key = api_key
        self.timeout_sec = timeout_sec

    def search(self, query: str, *, max_results: int = 5) -> list[Paper]:
        """Search Semantic Scholar and return normalized paper metadata."""
        query = query.strip()
        if not query:
            raise SemanticScholarSearchError("Semantic Scholar query is empty")
        if max_results < 1:
            raise SemanticScholarSearchError("max_results must be at least 1")

        _respect_rate_limit(0.3 if self.api_key else _REQUEST_GAP_SEC)
        url = self._url(query, max_results)
        headers = {"Accept": "application/json"}
        if self.api_key:
            headers["x-api-key"] = self.api_key
        payload = self._request_json(url, headers)
        results = payload.get("data", [])
        if not isinstance(results, list):
            raise SemanticScholarSearchError("Semantic Scholar response did not contain a data list")
        return [_paper_from_row(item) for item in results if isinstance(item, dict)]

    def _url(self, query: str, max_results: int) -> str:
        params = {
            "query": query,
            "limit": str(min(max_results, _MAX_RESULTS)),
            "fields": _FIELDS,
        }
        return f"{_BASE_URL}?{urllib.parse.urlencode(params)}"

    def _request_json(self, url: str, headers: dict[str, str]) -> dict[str, Any]:
        last_error = ""
        for attempt in range(_MAX_RETRIES + 1):
            try:
                request = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(request, timeout=self.timeout_sec) as response:
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                last_error = f"HTTP {exc.code}: {exc.reason}"
                if exc.code == 429 and attempt < _MAX_RETRIES:
                    time.sleep(min(2 ** (attempt + 1), 8))
                    continue
                raise SemanticScholarSearchError(f"Semantic Scholar search failed: {last_error}") from exc
            except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
                last_error = str(exc)
                if attempt < _MAX_RETRIES:
                    time.sleep(min(2 ** attempt, 4))
                    continue
                raise SemanticScholarSearchError(f"Semantic Scholar search failed: {last_error}") from exc
        raise SemanticScholarSearchError(f"Semantic Scholar search failed: {last_error}")


def _paper_from_row(item: dict[str, Any]) -> Paper:
    external = item.get("externalIds") if isinstance(item.get("externalIds"), dict) else {}
    paper_id = str(item.get("paperId") or "").strip()
    doi = str(external.get("DOI") or "").strip()
    arxiv_id = str(external.get("ArXiv") or "").strip()
    title = _clean_space(str(item.get("title") or "Untitled Semantic Scholar paper"))
    authors = _authors(item.get("authors"))
    published = str(item.get("year") or "").strip() or None
    url = str(item.get("url") or "").strip()
    if not url and arxiv_id:
        url = f"https://arxiv.org/abs/{arxiv_id}"
    return Paper(
        id=normalize_paper_id(f"s2-{paper_id or title[:40]}"),
        title=title,
        authors=authors,
        abstract=_clean_space(str(item.get("abstract") or "")),
        url=url,
        published=published,
        categories=[str(item.get("venue") or "").strip()] if item.get("venue") else [],
        source="semantic_scholar",
        source_id=paper_id or None,
        doi=doi or None,
    )


def _authors(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    names: list[str] = []
    for author in value:
        if not isinstance(author, dict):
            continue
        name = str(author.get("name") or "").strip()
        if name:
            names.append(name)
    return names


def _clean_space(text: str) -> str:
    return " ".join(text.split())


def _respect_rate_limit(gap_sec: float) -> None:
    global _last_request_at
    with _rate_lock:
        elapsed = time.monotonic() - _last_request_at
        if elapsed < gap_sec:
            time.sleep(gap_sec - elapsed)
        _last_request_at = time.monotonic()
