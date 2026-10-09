from __future__ import annotations

import os
from datetime import date
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

import requests

from simple_ar.literature.models import Paper, normalize_paper_id
from simple_ar.research.sources.base import SearchQuery, SearchResponse, temporal_scope


class WebConnector:
    """Optional Tavily discovery; original-page acquisition stays with documents.

    Search excerpts are not full text or verified scholarly metadata. Construction
    is lazy and selecting this provider explicitly requires its own credential.
    """

    source_name = "web"

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = (os.environ.get("TAVILY_API_KEY", "") if api_key is None else api_key).strip()

    def search(self, request: SearchQuery) -> SearchResponse:
        if not self._api_key:
            raise ValueError("The web provider requires TAVILY_API_KEY; paper providers remain available.")
        if not request.query.strip() or type(request.max_results) is not int or not 1 <= request.max_results <= 20:
            raise ValueError("Web search requires a non-empty query and max_results between 1 and 20.")
        scope = temporal_scope(request.filters)
        payload = {
            "query": request.query, "max_results": request.max_results,
            "search_depth": "basic", "topic": "general", "auto_parameters": False,
            "include_answer": False, "include_raw_content": False,
            "include_published_date": True, "include_usage": True,
        }
        if scope:
            payload.update(start_date=f"{scope['start_year']:04d}-01-01",
                           end_date=f"{scope['end_year']:04d}-12-31", filter_by_published_date=True)
        response = requests.post("https://api.tavily.com/search",
            headers={"Authorization": f"Bearer {self._api_key}"}, json=payload, timeout=(10, 45))
        response.raise_for_status()
        result = response.json()
        if not isinstance(result, dict) or not isinstance(result.get("results"), list):
            raise ValueError("Web search returned an invalid results payload.")
        papers = []
        seen = set()
        for row in result["results"][:request.max_results]:
            if not isinstance(row, dict):
                continue
            url = row.get("url")
            if not isinstance(url, str) or urlsplit(url).scheme not in {"http", "https"} or not urlsplit(url).hostname:
                continue
            if url in seen:
                continue
            seen.add(url)
            papers.append(Paper(id=normalize_paper_id(url), title=str(row.get("title") or url),
                authors=[], abstract=str(row.get("content") or ""), url=url,
                source=self.source_name, source_id=url, fulltext_url=url,
                published=_published_date(row.get("published_date")),
                bibliographic_notes=["Web search excerpt supplied by Tavily; not verified paper metadata or full text."]))
        usage = result.get("usage")
        credits = usage.get("credits") if isinstance(usage, dict) else None
        return SearchResponse(source=self.source_name, query=request.query, papers=papers,
            message=f"Tavily basic search; credits={credits if isinstance(credits, (int, float)) else 'unreported'}. Original pages require acquisition.")

    def extract_page(self, url: str) -> tuple[str, dict]:
        """One explicit basic extraction; no generated answer or fallback."""
        from simple_ar.research.preparation_assets import validate_public_url
        validate_public_url(url, dns=True)
        if not self._api_key:
            raise ValueError("Tavily page extraction requires TAVILY_API_KEY.")
        response = requests.post("https://api.tavily.com/extract",
            headers={"Authorization": f"Bearer {self._api_key}"},
            json={"urls": [url], "extract_depth": "basic", "format": "markdown",
                  "include_images": False, "include_usage": True, "timeout": 20}, timeout=(10, 30))
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError("Invalid Tavily extraction response.")
        provenance = {"provider": "tavily", "depth": "basic", "source_url": url,
                      "request_id": data.get("request_id"), "usage": data.get("usage"),
                      "content_scope": "provider_extracted_page_not_verified_paper_fulltext"}
        rows = data.get("results", [])
        text = next((r.get("raw_content") for r in rows if isinstance(r, dict) and r.get("url") == url), None)
        provenance["failed_results"] = data.get("failed_results", [])
        return text if isinstance(text, str) else "", provenance


def _published_date(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        try:
            return parsedate_to_datetime(value).date().isoformat()
        except (ValueError, TypeError, OverflowError):
            return None
