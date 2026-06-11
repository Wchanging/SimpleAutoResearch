from __future__ import annotations

import re
from typing import Any

from simple_ar.literature.models import Paper
from simple_ar.literature.verify import find_citation_ids


def references_markdown(
    papers: list[Paper],
    citation_map: dict[str, int] | None = None,
) -> str:
    """Render a reader-friendly reference list with known citation keys."""
    if not papers:
        return "No references were available."
    lines = []
    for paper in papers:
        label = f"[{citation_map[paper.id]}]" if citation_map and paper.id in citation_map else f"[@{paper.id}]"
        url = f" {paper.url}" if paper.url else ""
        lines.append(f"- {label} {paper.title}.{url}")
    return "\n".join(lines)


def strip_references_section(markdown: str) -> str:
    """Remove a model-written References section before appending verified refs."""
    lines = markdown.strip().splitlines()
    kept: list[str] = []
    for line in lines:
        if line.strip().lower().lstrip("#").strip() == "references":
            break
        kept.append(line)
    return "\n".join(kept).strip() + "\n"


def append_references_section(
    markdown: str,
    papers: list[Paper],
    citation_map: dict[str, int] | None = None,
) -> str:
    """Append deterministic references generated from known paper metadata."""
    body = markdown.strip()
    return f"{body}\n\n## References\n\n{references_markdown(papers, citation_map)}\n"


def sanitize_report_citations(markdown_body: str, allowed_ids: set[str]) -> tuple[str, list[str]]:
    """Remove citation ids that are not part of the current run source map."""
    invalid = sorted(find_citation_ids(markdown_body) - allowed_ids)
    if not invalid:
        return markdown_body, []
    sanitized = markdown_body
    for citation_id in invalid:
        sanitized = re.sub(
            rf"@{re.escape(citation_id)}(?![A-Za-z0-9_.:-])",
            "",
            sanitized,
        )
    sanitized = re.sub(r"\[\s*(?:;\s*)*\]", "", sanitized)
    sanitized = re.sub(r"\[\s*;\s*", "[", sanitized)
    sanitized = re.sub(r";\s*\]", "]", sanitized)
    sanitized = re.sub(r";\s*;", ";", sanitized)
    sanitized = re.sub(r"[ \t]{2,}", " ", sanitized)
    return sanitized.strip() + "\n", invalid


def expand_short_citation_keys(markdown_body: str, citation_key_map: dict[str, str]) -> str:
    """Map model-facing short citation keys back to real source ids."""
    if not citation_key_map:
        return markdown_body
    normalized = {key.upper(): paper_id for key, paper_id in citation_key_map.items()}

    def paper_id_for(key: str) -> str:
        return normalized.get(key.strip().upper(), "")

    def replace_pandoc_key(match: re.Match[str]) -> str:
        paper_id = paper_id_for(match.group(1))
        return f"@{paper_id}" if paper_id else match.group(0)

    expanded = re.sub(r"@([Pp]\d+)(?![A-Za-z0-9_.:-])", replace_pandoc_key, markdown_body)

    def replace_bare_group(match: re.Match[str]) -> str:
        content = match.group(1).strip()
        if "@" in content:
            return match.group(0)
        parts = [part.strip() for part in re.split(r"[;,]", content) if part.strip()]
        paper_ids = [paper_id_for(part) for part in parts]
        if not paper_ids or any(not paper_id for paper_id in paper_ids):
            return match.group(0)
        return "[" + "; ".join(f"@{paper_id}" for paper_id in paper_ids) + "]"

    return re.sub(r"\[([Pp]\d+(?:\s*[;,]\s*[Pp]\d+)*)\]", replace_bare_group, expanded)


def record_removed_citations(report_audit: object, citation_ids: list[str]) -> None:
    """Annotate report audit when invalid citation placeholders were removed."""
    if not citation_ids:
        return
    joined = ", ".join(citation_ids)
    warning = (
        "Removed citation id(s) not present in the current run source map before "
        f"writing references: {joined}."
    )
    if hasattr(report_audit, "citation_audit"):
        report_audit.citation_audit.warnings.append(warning)
        if report_audit.citation_audit.status == "passed":
            report_audit.citation_audit.status = "warning"
    if hasattr(report_audit, "notes"):
        report_audit.notes.append(warning)
    if getattr(report_audit, "status", "passed") == "passed":
        report_audit.status = "warning"


def citation_display_map(papers: list[Paper]) -> dict[str, int]:
    """Return stable numeric citation labels for body-cited papers."""
    return {paper.id: index for index, paper in enumerate(papers, start=1)}


def citation_map_artifact(
    citation_map: dict[str, int],
    papers: list[Paper],
    citation_key_map: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Return the written citation map artifact for display/reference lookup."""
    by_id = {paper.id: paper for paper in papers}
    key_by_id = {paper_id: key for key, paper_id in (citation_key_map or {}).items()}
    entries: list[dict[str, Any]] = []
    for paper_id, number in sorted(citation_map.items(), key=lambda item: item[1]):
        paper = by_id.get(paper_id)
        entries.append(
            {
                "number": number,
                "model_key": key_by_id.get(paper_id, ""),
                "paper_id": paper_id,
                "title": paper.title if paper else "",
                "url": paper.url if paper else "",
                "source": paper.source if paper else "",
            }
        )
    return {
        "schema_version": "citation_map.v1",
        "display_style": "numeric_brackets",
        "model_key_style": "short_keys",
        "entries": entries,
    }


def display_citation_numbers(markdown_body: str, citation_map: dict[str, int]) -> str:
    """Convert internal ``[@paper-id]`` citations to readable ``[1]`` labels."""
    if not citation_map:
        return markdown_body

    def replace_group(match: re.Match[str]) -> str:
        ids = re.findall(r"@([A-Za-z0-9_.:-]+)", match.group(1))
        numbers = [citation_map[citation_id] for citation_id in ids if citation_id in citation_map]
        if not numbers:
            return match.group(0)
        deduped = list(dict.fromkeys(numbers))
        return "[" + ", ".join(str(number) for number in deduped) + "]"

    converted = re.sub(
        r"\[([^\]]*@([A-Za-z0-9_.:-]+)[^\]]*)\]",
        replace_group,
        markdown_body,
    )

    def replace_standalone(match: re.Match[str]) -> str:
        citation_id = match.group(1)
        number = citation_map.get(citation_id)
        return f"[{number}]" if number is not None else match.group(0)

    converted = re.sub(r"@([A-Za-z0-9_.:-]+)", replace_standalone, converted)
    return display_bare_source_id_numbers(converted, citation_map)


def normalize_bare_source_id_citations(markdown_body: str, allowed_ids: set[str]) -> str:
    """Convert upstream ``[paper-id]`` notes into the internal citation form."""
    if not allowed_ids:
        return markdown_body

    def replace_bracket(match: re.Match[str]) -> str:
        content = match.group(1).strip()
        if "@" in content:
            return match.group(0)
        parts = [part.strip() for part in re.split(r"[;,]", content) if part.strip()]
        if not parts or any(part not in allowed_ids for part in parts):
            return match.group(0)
        return "[" + "; ".join(f"@{part}" for part in parts) + "]"

    return re.sub(r"\[([A-Za-z0-9_.:;\-\s]+)\]", replace_bracket, markdown_body)


def display_bare_source_id_numbers(markdown_body: str, citation_map: dict[str, int]) -> str:
    """Convert source-id brackets copied from upstream notes into display labels."""
    if not citation_map:
        return markdown_body

    id_pattern = "|".join(re.escape(paper_id) for paper_id in sorted(citation_map, key=len, reverse=True))
    if not id_pattern:
        return markdown_body

    def replace_bracket(match: re.Match[str]) -> str:
        content = match.group(1).strip()
        parts = [part.strip() for part in re.split(r"[;,]", content) if part.strip()]
        if not parts or any(part not in citation_map for part in parts):
            return match.group(0)
        numbers = sorted({citation_map[part] for part in parts})
        return "[" + ", ".join(str(number) for number in numbers) + "]"

    converted = re.sub(r"\[([A-Za-z0-9_.:;\-\s]+)\]", replace_bracket, markdown_body)

    def replace_parenthesized(match: re.Match[str]) -> str:
        citation_id = match.group(1)
        number = citation_map.get(citation_id)
        return f"[{number}]" if number is not None else match.group(0)

    converted = re.sub(rf"\(({id_pattern})\)", replace_parenthesized, converted)
    return re.sub(rf"`(?:{id_pattern})`", "", converted)


def cited_papers(markdown_body: str, papers: list[Paper]) -> list[Paper]:
    """Return papers cited in the report body, preserving metadata order."""
    paper_by_id = {paper.id: paper for paper in papers}
    ordered_ids = ordered_body_citation_ids(markdown_body, set(paper_by_id))
    return [paper_by_id[paper_id] for paper_id in ordered_ids if paper_id in paper_by_id]


def ordered_body_citation_ids(markdown: str, allowed_ids: set[str]) -> list[str]:
    """Return allowed citation ids in first-mention order before references."""
    body = strip_references_section(markdown)
    ordered: list[str] = []
    seen: set[str] = set()
    for paper_id in re.findall(r"@([A-Za-z0-9_.:-]+)", body):
        if paper_id in allowed_ids and paper_id not in seen:
            ordered.append(paper_id)
            seen.add(paper_id)
    return ordered


def citation_instruction(papers: list[Paper], citation_key_map: dict[str, str] | None = None) -> str:
    """Build citation guidance from known paper metadata."""
    if not papers:
        return ""
    key_by_id = {paper_id: key for key, paper_id in (citation_key_map or {}).items()}
    lines = [
        "Use only these short citation keys in body text, in Pandoc form `[@P1]`:",
    ]
    for paper in papers:
        abstract = f" Abstract: {paper.abstract[:220]}" if paper.abstract else ""
        source = f" Source: {paper.source}" if paper.source else ""
        key = key_by_id.get(paper.id, paper.id)
        lines.append(f"- [@{key}] TITLE: \"{paper.title}\".{source}{abstract}")
    lines.extend(
        [
            "Do not cite a paper unless the sentence discusses that paper or its listed metadata.",
            "If no listed paper supports a claim, write the claim without a citation or weaken it.",
        ]
    )
    return "\n".join(lines)


def literature_citation_sentence(papers: list[Paper]) -> str:
    """Create one conservative citation sentence for fallback introductions."""
    real_papers = [paper for paper in papers if paper.source != "fixture"]
    selected = real_papers or papers
    if not selected:
        return ""
    keys = " ".join(f"[@{paper.id}]" for paper in selected[:3])
    return f"The body cites examples from the retrieved set such as {keys}."


def body_citation_ids(markdown: str, allowed_ids: set[str]) -> set[str]:
    """Return allowed citation ids that appear before the References section."""
    body = strip_references_section(markdown)
    found = set(re.findall(r"@([A-Za-z0-9_.:-]+)", body))
    return found & allowed_ids


def model_citation_key(paper_id: str, citation_key_map: dict[str, str]) -> str:
    """Return the short model-facing citation key for one paper id."""
    for key, mapped_paper_id in citation_key_map.items():
        if mapped_paper_id == paper_id:
            return key
    return ""

