from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

from simple_ar.research.contracts import DocumentRecord, DocumentSection
from simple_ar.research.documents.ports import MATERIAL_TEXT_SUFFIXES


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
NUMBERED_HEADING = re.compile(
    r"^(?:[1-9]\d?(?:\.\d{1,2})*|[A-Z])[.)]?\s+([A-Z][^\n]{2,90})$"
)

# Letter-numbered appendices are often mixed case, unlike extracted main
# headings. Do not let the References section swallow their experiments/proofs.
APPENDIX_HEADING = re.compile(
    r"^(?:#{1,6}\s+)?(?:Appendix|Appendices|Supplementary (?:Material|Information))"
    r"(?:\s+[A-Z0-9](?:\.\d+)*)?(?:[.:]?\s+(?P<title>[^\n]{1,80}?))?\s*:?$",
    re.IGNORECASE,
)
LETTER_HEADING = re.compile(r"^[A-Z](?:\.\d{1,2})*[.)]?\s+([A-Z][^\n]{2,90})$")

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



def abstract_excerpt(text: str, *, limit: int = 1200) -> str:
    """Use an explicit abstract heading, never a byline/preamble as an abstract.

    This is a literal excerpt, not a generated summary. Unheaded notes retain
    their full body through document sections/chunks rather than a false label.
    """
    lines = text.splitlines()
    start = None
    for index, line in enumerate(lines):
        heading = _heading_for_line(line)
        if heading is None:
            continue
        if start is not None:
            return " ".join("\n".join(lines[start:index]).split())[:limit]
        if _normalize_section(heading) == "abstract":
            start = index + 1
    return " ".join("\n".join(lines[start:]).split())[:limit] if start is not None else ""


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
        raw_sections = (_split_sections(text) if record.extraction_status == "parsed" else
                        [{"section": "abstract", "heading": "Abstract", "text": text,
                          "line_start": 1, "line_end": len(text.splitlines()) or 1}])
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
        if path.is_file() and path.suffix.lower() in MATERIAL_TEXT_SUFFIXES:
            return _read_text(path), str(path)
    return record.abstract or "", record.local_path or record.url


def _split_sections(text: str) -> list[dict[str, object]]:
    lines = text.splitlines()
    heading_rows: list[tuple[int, str, str]] = []
    fence_char, fence_size = '', 0
    for index, line in enumerate(lines):
        fence = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
        if fence_char:
            if (fence and fence.group(1)[0] == fence_char and len(fence.group(1)) >= fence_size
                    and not line[fence.end():].strip()):
                fence_char, fence_size = '', 0
            continue
        if fence:
            fence_char, fence_size = fence.group(1)[0], len(fence.group(1))
            continue
        heading = _heading_for_line(line)
        if heading is None:
            continue
        # Bibliography entries are not main-text sections just because their
        # publication titles begin with a number. Explicit structure or an
        # appendix may reopen the body; all source text stays in its span.
        if heading_rows and heading_rows[-1][1] == "references" and not (
            re.match(r"^\s*#{2,6}\s", line) or APPENDIX_HEADING.fullmatch(line.strip())
            or LETTER_HEADING.fullmatch(line.strip())
        ):
            continue
        section = _normalize_section(heading)
        heading_rows.append((index, section, heading))

    if not heading_rows:
        return [_fallback_section(text, lines)]

    sections: list[dict[str, object]] = []
    # Title/byline/version declarations and supplied caveats before the first
    # recognized heading are source text too. Do not infer metadata from them,
    # but retain them for targeted identity or condition checks.
    first_heading = heading_rows[0][0]
    preamble = "\n".join(lines[:first_heading]).strip()
    if preamble:
        sections.append({"section": "front_matter", "heading": "Front matter",
                         "text": preamble, "line_start": 1, "line_end": first_heading})
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
    section = "body"
    return {
        "section": section,
        "heading": section.title(),
        "text": text,
        "line_start": 1,
        "line_end": len(lines) or 1,
    }


def _heading_for_line(line: str) -> str | None:
    stripped = line.strip()
    if not stripped or len(stripped) > 100:
        return None
    match = HEADING_PATTERN.match(stripped)
    if match:
        return match.group(1).strip()
    # HTML heading tags and Markdown ATX headings carry explicit structure;
    # prose/PDF heuristics below must not discard sentence-case section names.
    explicit = re.fullmatch(r"#{2,6}\s+(.+?)(?:\s+#+)?", stripped)
    if explicit:
        stripped = explicit.group(1).strip()
    appendix = APPENDIX_HEADING.fullmatch(stripped)
    if appendix is not None:
        title = appendix.group("title")
        # A prose cross-reference such as "Appendix A.2 discusses ..." is
        # not a heading. Preserve original case when testing the title.
        if title is None or (title[0].isupper()
                             and re.search(r"[,;!?]|\.(?:\s|$)", title) is None):
            return stripped.lstrip('#').strip()
    letter = LETTER_HEADING.fullmatch(stripped)
    if letter is not None:
        title = letter.group(1).strip()
        # Citation initials, author lists, table values and complete prose
        # sentences are not an appendix heading merely because they start A/B.
        if (len([char for char in title if char.isalpha()]) >= 4
                and len(title.split()) <= 15
                and re.search(r"[,;!?]|\.(?:\s|$)", title) is None
                and re.search(r"\s\d+\.\d+(?:\s|$)", title) is None):
            return title
    numbered = NUMBERED_HEADING.match(stripped)
    if numbered is None:
        return stripped if explicit else None
    title = numbered.group(1).strip()
    if explicit:
        return title
    if re.search(r"(?<!\w)\d+(?:\.\d+)?\s+\d+(?:\.\d+)?\s*$", title):
        return None  # Numeric table cells do not establish a heading.
    letters = [char for char in title if char.isalpha()]
    if len(letters) < 4 or len(title.split()) > 15:
        return None
    words = re.findall(r"[^\W\d_]+", title)
    title_case = all(word[0].isupper() or word.lower() in {
        "a", "an", "the", "and", "or", "of", "in", "on", "for", "to", "with", "without", "under", "by"
    } for word in words)
    if (re.search(r"[,;!?]|\.(?:\s|$)|\s\d+\.\d+(?:\s|$)", title)
            or (sum(char.isupper() for char in letters) / len(letters) < 0.8 and not title_case)):
        return None
    return title


def _normalize_section(heading: str) -> str:
    direct = SECTION_ALIASES.get(heading.strip().lower())
    if direct:
        return direct
    compact = re.sub(r"[^a-z]", "", heading.lower())
    if compact in {"references", "bibliography"}:
        return "references"  # Retain PDF drop-cap spacing, not substring matches.
    for term, section in (
        ("appendix", "body"), ("appendices", "body"), ("supplementary", "body"),
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
