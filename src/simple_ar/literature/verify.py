from __future__ import annotations

import re


class CitationError(RuntimeError):
    """Raised when a report cites a paper outside ``papers.jsonl``."""


def find_citation_ids(markdown: str) -> set[str]:
    """Find citation ids in Pandoc-style Markdown citations.

    Args:
        markdown: Report Markdown.

    Returns:
        Set of ids referenced as ``[@paper_id]`` or ``@paper_id``.
    """
    return set(re.findall(r"@([A-Za-z0-9_.:-]+)", markdown))


def validate_citations(markdown: str, allowed_ids: set[str]) -> None:
    """Require every report citation to point at a known paper id.

    Args:
        markdown: Report Markdown to validate.
        allowed_ids: Paper ids loaded from ``papers.jsonl``.

    Raises:
        CitationError: If any cited id is not allowed.
    """
    invalid = sorted(find_citation_ids(markdown) - allowed_ids)
    if invalid:
        raise CitationError("Unknown citation id(s): " + ", ".join(invalid))
