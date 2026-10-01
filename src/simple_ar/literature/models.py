from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


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
    year = published[:4] if re.fullmatch(r"\d{4}(?:-\d{2}(?:-\d{2})?)?", published) else ""
    authors = [name.strip() for name in paper.authors if name.strip()]
    url = paper.url.strip()
    public_url = url if paper.source != "local_files" and url.lower().startswith(("https://", "http://")) else ""
    doi = (paper.doi or "").strip()
    missing = [key for key, value in (("title", paper.title.strip()), ("authors", authors),
                                    ("year", year), ("public_locator", doi or public_url)) if not value]
    return {"title": paper.title, "authors": authors, "published": published,
            "year": year, "doi": doi, "url": public_url, "source": paper.source,
            "missing_fields": missing, "notes": list(paper.bibliographic_notes),
            "verification_status": "not_independently_verified"}


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
