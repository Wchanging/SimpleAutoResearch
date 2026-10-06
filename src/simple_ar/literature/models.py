from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any
from urllib.parse import unquote, urlsplit


@dataclass(frozen=True)
class Paper:
    """Normalized paper metadata used by the pipeline.

    Args:
        id: Citation-safe identifier used in ``papers.jsonl`` and reports.
        title: Paper title.
        authors: Author names in display order.
        abstract: Abstract or summary text.
        url: Source URL for the paper metadata.
        published: Publication date as an ISO date string when available.
        categories: arXiv or source categories.
        source: Metadata source name.
        source_id: Original provider identifier before normalization.
        doi: DOI when available.
        fulltext_url: Optional direct full-text URL or provider open-access hint.
        bibliographic_notes: Source-reported or documented metadata coverage limits.
    """

    id: str
    title: str
    authors: list[str]
    abstract: str
    url: str
    published: str | None = None
    categories: list[str] = field(default_factory=list)
    source: str = "arxiv"
    source_id: str | None = None
    doi: str | None = None
    fulltext_url: str | None = None
    bibliographic_notes: list[str] = field(default_factory=list)

    def to_row(self) -> dict[str, Any]:
        """Convert paper metadata into a JSON-serializable row."""
        return {
            "id": self.id,
            "title": self.title,
            "authors": list(self.authors),
            "abstract": self.abstract,
            "url": self.url,
            "published": self.published,
            "categories": list(self.categories),
            "source": self.source,
            "source_id": self.source_id,
            "doi": self.doi,
            "fulltext_url": self.fulltext_url,
            "bibliographic_notes": list(self.bibliographic_notes),
        }

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "Paper":
        """Build a ``Paper`` from a row read out of ``papers.jsonl``."""
        return cls(
            id=str(row["id"]),
            title=str(row.get("title", "")),
            authors=[str(author) for author in row.get("authors", [])],
            abstract=str(row.get("abstract", "")),
            url=str(row.get("url", "")),
            published=str(row["published"]) if row.get("published") else None,
            categories=[str(category) for category in row.get("categories", [])],
            source=str(row.get("source", "unknown")),
            source_id=str(row["source_id"]) if row.get("source_id") else None,
            doi=str(row["doi"]) if row.get("doi") else None,
            fulltext_url=str(row["fulltext_url"]) if row.get("fulltext_url") else None,
            bibliographic_notes=[str(note) for note in (row.get("bibliographic_notes") or [])],
        )


def bibliographic_details(paper: Paper) -> dict[str, Any]:
    """Project recorded metadata consistently; availability is not verification.

    No network lookup or model inference happens here. Local paths stay in the
    original provenance row, not reader-facing references or export metadata.
    Missing dates must not turn into invented publication years.
    """
    published = (paper.published or "").strip()
    issues: list[str] = []
    year = ""
    if re.fullmatch(r"\d{4}(?:-\d{2}(?:-\d{2})?)?", published):
        parts = [int(part) for part in published.split('-')]
        parts += [1] * (3 - len(parts))
        try:
            date(*parts)
            year = published[:4]
        except ValueError:
            issues.append("Recorded publication date is invalid; publication year was not inferred.")
    else:
        # Unambiguous written-month dates are common in supplied publications.
        # Parse a complete calendar date, not an arbitrary four-digit substring
        # or an ambiguous numeric day/month order. Preserve the recorded text.
        for date_format in ("%d %B %Y", "%d %b %Y", "%B %d, %Y", "%b %d, %Y"):
            try:
                year = f"{datetime.strptime(published, date_format).year:04d}"
                break
            except ValueError:
                continue
    authors = [name.strip() for name in paper.authors if name.strip()]
    url = paper.url.strip()
    public_url = url if url.lower().startswith(("https://", "http://")) else ""
    doi = _doi_identifier(paper.doi or "")
    if paper.doi and not doi:
        issues.append("Recorded DOI format is unresolved; no DOI was inferred.")
    url_doi = _doi_identifier(public_url)
    if doi and url_doi and doi.casefold() != url_doi.casefold():
        issues.append("Recorded DOI and DOI URL contain different identifiers; work/version relationship is unresolved.")
    identifiers = [value for locator in (paper.source_id or '', public_url, paper.fulltext_url or '', doi)
                   if (value := _arxiv_identifier(locator))]
    if len({re.sub(r'v\d+$', '', value) for value in identifiers}) > 1:
        issues.append("Recorded arXiv identifiers refer to different documents; identity is unresolved.")
    elif len({match[0] for value in identifiers if (match := re.findall(r'v\d+$', value))}) > 1:
        issues.append("Recorded arXiv identifiers refer to different explicit versions; identity is unresolved.")
    missing = [key for key, value in (("title", paper.title.strip()), ("authors", authors),
                                    ("year", year), ("public_locator", doi or public_url)) if not value]
    return {"title": paper.title, "authors": authors, "published": published,
            "year": year, "doi": doi, "url": public_url, "source": paper.source,
            "missing_fields": missing, "notes": list(dict.fromkeys([*paper.bibliographic_notes, *issues])),
            "consistency_issues": issues,
            "verification_status": "not_independently_verified"}


def _doi_identifier(value: str) -> str:
    """Normalize known DOI wrappers, not titles or publication identities."""
    value = value.strip()
    if value.lower().startswith('doi:'):
        value = unquote(value[4:].lstrip())
    if value.lower().startswith(('https://', 'http://')):
        try:
            url = urlsplit(value)
        except ValueError:
            return ''
        if url.hostname not in {'doi.org', 'dx.doi.org'}:
            return ''
        value = unquote(url.path.lstrip('/'))
    prefix, separator, suffix = value.partition('/')
    # DOI Handbook §3.3: no length cap, numeric subprefixes, Graphic Unicode
    # suffix (including spaces). A common search regex is not a validity rule.
    graphic = all(unicodedata.category(char)[0] in 'LMNPS' or
                  unicodedata.category(char) == 'Zs' for char in suffix)
    if separator and suffix and graphic and prefix != '10' and re.fullmatch(r'[0-9]+(?:\.[0-9]+)*', prefix):
        return value
    return ''


def _arxiv_identifier(value: str) -> str:
    value = _doi_identifier(value) or value
    value = re.sub(r'^arxiv:\s*', '', value.strip(), flags=re.IGNORECASE)
    value = re.sub(r'^10\.48550/arxiv\.', '', value, flags=re.IGNORECASE)
    if value.lower().startswith(('https://', 'http://')):
        try:
            url = urlsplit(value)
        except ValueError:
            return ''
        if url.hostname not in {'arxiv.org', 'export.arxiv.org'}:
            return ''
        value = re.sub(r'^/(?:abs|pdf)/', '', unquote(url.path)).removesuffix('.pdf')
    return value if re.fullmatch(r'(?:\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Za-z]{2})?/\d{7})(?:v\d+)?', value) else ''


def normalize_paper_id(raw_id: str) -> str:
    """Normalize a provider paper id into a citation-safe identifier.

    Args:
        raw_id: Provider id or URL, such as an arXiv entry id.

    Returns:
        Identifier containing only letters, digits, dots, underscores, colons,
        and hyphens.
    """
    value = raw_id.strip().rstrip("/")
    if "/abs/" in value:
        value = value.split("/abs/", maxsplit=1)[1]
    value = re.sub(r"[^A-Za-z0-9_.:-]+", "_", value)
    return value.strip("_") or "paper"
