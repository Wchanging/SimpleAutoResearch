from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

from simple_ar.research.contracts import DocumentRecord, DocumentSection


HEADING_PATTERN = re.compile(
    r"^\s*(?:#{1,6}\s+)?(?:\d+(?:\.\d+)*[.)]?\s+)?"
    r"(abstract|introduction|related work|background|method|methods|methodology|"
    r"approach|system|experiment|experiments|evaluation|results|discussion|"
    r"limitations?|conclusion|references|bibliography)\s*:?\s*$",
    re.IGNORECASE,
)

# PDF text extraction often inserts a space after a drop-cap ("1 I NTRODUCTION")
# or uses a descriptive numbered heading ("3.3 A RCHITECTURE"). Keep this
# conservative: a table row or numbered sentence is not a section boundary.
NUMBERED_UPPER_HEADING = re.compile(
    r"^(?:[1-9]\d?(?:\.\d{1,2})*|[A-Z])[.)]?\s+([A-Z][^\n]{2,90})$"
)

SECTION_ALIASES = {
    "abstract": "abstract",
    "introduction": "introduction",
    "related work": "related_work",
    "background": "related_work",
    "method": "method",
    "methods": "method",
    "methodology": "method",
    "approach": "method",
    "system": "method",
    "experiment": "experiments",
    "experiments": "experiments",
    "evaluation": "experiments",
    "results": "results",
    "discussion": "discussion",
    "limitation": "limitations",
    "limitations": "limitations",
    "conclusion": "conclusion",
    "references": "references",
    "bibliography": "references",
}

TEXT_SUFFIXES = {".md", ".txt"}


def build_document_sections(records: Iterable[DocumentRecord]) -> list[DocumentSection]:
    """Build section-aware records from parsed text or abstracts.

    Args:
        records: Document records after optional full-text extraction.

    Returns:
        Ordered section records. Parsed text with recognizable headings is split
        into paper-like sections. Metadata-only rows contribute a compact
        ``abstract`` section when an abstract is available. Parsed text without
        headings falls back to one ``body`` section.
    """
    sections: list[DocumentSection] = []
    for record in records:
        text, source_path = _record_text(record)
        text = text.strip()
        if not text:
            continue
        raw_sections = _split_sections(text)
        for index, row in enumerate(raw_sections, start=1):
            section_text = row["text"].strip()
            if not section_text:
                continue
            section = str(row["section"])
            heading = str(row["heading"])
            sections.append(
                DocumentSection(
                    section_id=f"{record.document_id}#section-{index:03d}-{section}",
                    document_id=record.document_id,
                    section=section,
                    heading=heading,
                    text=section_text,
                    source_path=source_path,
                    line_start=row.get("line_start"),
                    line_end=row.get("line_end"),
                    token_estimate=max(1, len(section_text) // 4),
                    metadata={
                        "title": record.title,
                        "source": record.source,
                        "extraction_status": record.extraction_status,
                        "parser": record.parser or "",
                    },
                )
            )
    return sections


def _record_text(record: DocumentRecord) -> tuple[str, str | None]:
    if record.extraction_status == "parsed" and record.local_path:
        path = Path(record.local_path)
        if path.is_file() and path.suffix.lower() in TEXT_SUFFIXES:
            return _read_text(path), str(path)
    return record.abstract or "", record.local_path or record.url


def _split_sections(text: str) -> list[dict[str, object]]:
    lines = text.splitlines()
    heading_rows: list[tuple[int, str, str]] = []
    for index, line in enumerate(lines):
        heading = _heading_for_line(line)
        if heading is None:
            continue
        section = _normalize_section(heading)
        heading_rows.append((index, section, heading))

    if not heading_rows:
        return [_fallback_section(text, lines)]

    sections: list[dict[str, object]] = []
    for position, (line_index, section, heading) in enumerate(heading_rows):
        next_line = heading_rows[position + 1][0] if position + 1 < len(heading_rows) else len(lines)
        body_lines = lines[line_index + 1 : next_line]
        body = "\n".join(body_lines).strip()
        if not body:
            continue
        sections.append(
            {
                "section": section,
                "heading": heading,
                "text": body,
                "line_start": line_index + 2,
                "line_end": next_line,
            }
        )

    if sections:
        return sections
    return [_fallback_section(text, lines)]


def _fallback_section(text: str, lines: list[str]) -> dict[str, object]:
    compact = _strip_leading_title(text).strip()
    section = "abstract" if len(compact) <= 1800 else "body"
    return {
        "section": section,
        "heading": section.title(),
        "text": compact,
        "line_start": 1,
        "line_end": len(lines) or 1,
    }


def _strip_leading_title(text: str) -> str:
    lines = text.splitlines()
    while lines and not lines[0].strip():
        lines.pop(0)
    if lines and lines[0].lstrip().startswith("#"):
        lines = lines[1:]
        while lines and not lines[0].strip():
            lines.pop(0)
    return "\n".join(lines) or text


def _heading_for_line(line: str) -> str | None:
    stripped = line.strip()
    if not stripped or len(stripped) > 100:
        return None
    match = HEADING_PATTERN.match(stripped)
    if match:
        return match.group(1).strip()
    numbered = NUMBERED_UPPER_HEADING.match(stripped)
    if numbered is None:
        return None
    title = numbered.group(1).strip()
    letters = [char for char in title if char.isalpha()]
    if len(letters) < 4 or len(title.split()) > 15:
        return None
    if sum(char.isupper() for char in letters) / len(letters) < 0.8:
        return None
    return title


def _normalize_section(heading: str) -> str:
    direct = SECTION_ALIASES.get(heading.strip().lower())
    if direct:
        return direct
    compact = re.sub(r"[^a-z]", "", heading.lower())
    for term, section in (
        ("references", "references"), ("bibliography", "references"),
        ("abstract", "abstract"), ("relatedwork", "related_work"),
        ("background", "related_work"), ("introduction", "introduction"),
        ("evaluation", "experiments"), ("evaluating", "experiments"),
        ("experiment", "experiments"), ("baseline", "experiments"),
        ("dataset", "experiments"),
        ("architecture", "method"), ("implementation", "method"),
        ("algorithm", "method"), ("modification", "method"), ("method", "method"),
        ("results", "results"), ("performance", "results"),
        ("analysis", "results"), ("discussion", "discussion"),
        ("limitation", "limitations"), ("conclusion", "conclusion"),
    ):
        if term in compact:
            return section
    return "body"


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="replace")
