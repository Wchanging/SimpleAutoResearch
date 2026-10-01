from __future__ import annotations

from simple_ar.literature.models import Paper, bibliographic_details


def papers_to_bibtex(papers: list[Paper]) -> str:
    """Render paper metadata into BibTeX entries.

    Args:
        papers: Papers previously stored in ``papers.jsonl``.

    Returns:
        BibTeX text using each paper id as the citation key.
    """
    return "\n\n".join(_paper_to_bibtex(paper) for paper in papers).strip() + "\n"


def _paper_to_bibtex(paper: Paper) -> str:
    metadata = bibliographic_details(paper)
    fields = {
        "title": metadata["title"],
        "author": " and ".join(metadata["authors"]),
        "url": metadata["url"],
    }
    if metadata["year"]:
        fields["year"] = metadata["year"]
    if metadata["doi"]:
        fields["doi"] = metadata["doi"]
    if metadata["notes"]:
        fields["note"] = " ".join(metadata["notes"])
    if paper.source == "arxiv" and paper.source_id:
        fields["eprint"] = paper.source_id
        fields["archivePrefix"] = "arXiv"

    lines = [f"@misc{{{paper.id},"]
    for key, value in fields.items():
        if value:
            lines.append(f"  {key} = {{{_escape_bibtex(value)}}},")
    lines[-1] = lines[-1].rstrip(",")
    lines.append("}")
    return "\n".join(lines)


def _escape_bibtex(value: str) -> str:
    """Escape the small subset of BibTeX-sensitive characters we emit."""
    return value.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")
