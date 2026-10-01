from __future__ import annotations

import re
from collections.abc import Iterator

from simple_ar.report.schema import ReportSectionDraft


def normalize_report_markdown(markdown: str) -> str:
    """Normalize final Markdown without changing report semantics."""
    lines = [line if code else line.rstrip() for line, _, code in _markdown_lines(markdown)]
    return "\n".join(lines).strip("\r\n") + "\n"


def _markdown_lines(markdown: str) -> Iterator[tuple[str, re.Match[str] | None, bool]]:
    """Share ATX/fence boundaries; heading-like code is not document structure."""
    fence_char, fence_size = '', 0
    for line in markdown.splitlines():
        if fence_char:
            closing = re.fullmatch(r" {0,3}([`~]+)[ \t]*", line)
            if closing and set(closing[1]) == {fence_char} and len(closing[1]) >= fence_size:
                fence_char, fence_size = '', 0
            yield line, None, True
            continue
        opening = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line)
        if opening and not (opening[1][0] == '`' and '`' in opening[2]):
            fence_char, fence_size = opening[1][0], len(opening[1])
            yield line, None, True
            continue
        yield line, re.match(r"^ {0,3}(#{1,6})[ \t]+(.+?)\s*$", line), False


def _heading_title(title: str) -> str:
    return re.sub(r"\s+#+\s*$", '', title).strip()


def strip_report_references(markdown: str) -> str:
    """Remove only a document-level References heading outside fenced code."""
    kept = []
    for line, match, _ in _markdown_lines(markdown):
        if match and len(match[1]) <= 2 and _heading_title(match[2]).casefold() == 'references':
            break
        kept.append(line)
    return normalize_report_markdown("\n".join(kept))


def apply_section_numbering(
    markdown: str,
    *,
    mode: str = "auto",
    template_name: str = "",
    style: str = "",
) -> str:
    """Render a standard academic heading hierarchy without changing prose.

    Writers plan semantic headings, while the renderer owns presentation-level
    numbering.  Keeping these responsibilities separate makes reports easier
    to navigate and avoids relying on a model to reproduce a fragile Markdown
    convention.  The function is deliberately generic: it is useful for
    surveys, technical reports, and paper-style reports alike.
    """
    selected = mode.strip().lower()
    if selected == "off":
        return normalize_report_markdown(markdown)
    if selected not in {"auto", "academic"}:
        return normalize_report_markdown(markdown)
    if selected == "auto":
        style_text = f"{template_name} {style}".lower()
        if not any(token in style_text for token in ("paper", "survey", "academic", "report")):
            return normalize_report_markdown(markdown)

    counters = [0] * 6
    lines: list[str] = []
    for line, match, _ in _markdown_lines(markdown):
        if not match or len(match[1]) == 1:
            lines.append(line)
            continue
        level = len(match.group(1))
        title = _strip_heading_number(_heading_title(match.group(2)))
        if _is_unnumbered_academic_heading(title):
            lines.append(f"{'#' * level} {title}")
            continue
        parent_level = level - 2
        if parent_level > 0 and not any(counters[:parent_level]):
            # A malformed local heading should not invent a top-level section.
            lines.append(f"{'#' * level} {title}")
            continue
        counters[parent_level] += 1
        for index in range(parent_level + 1, len(counters)):
            counters[index] = 0
        number = ".".join(str(value) for value in counters[: parent_level + 1] if value)
        lines.append(f"{'#' * level} {number} {title}")
    return normalize_report_markdown("\n".join(lines))


def _strip_heading_number(title: str) -> str:
    return re.sub(r"^\s*(?:\d+(?:\.\d+)*\.?|[A-Z])\s+", "", title).strip()


def _is_unnumbered_academic_heading(title: str) -> bool:
    normalized = title.strip().lower().rstrip(":")
    return normalized in {
        "abstract",
        "acknowledgements",
        "acknowledgments",
        "references",
    }


def assemble_report_sections(*, title: str, sections: list[ReportSectionDraft]) -> str:
    """Assemble section drafts into one final Markdown body without references."""
    parts = [f"# {title.strip() or 'Research Report'}"]
    for section in sections:
        body = _section_body(section.draft_markdown, heading=section.heading)
        if body:
            parts.append(f"## {section.heading}\n\n{body}")
    return normalize_report_markdown("\n\n".join(parts))


def _section_body(markdown: str, *, heading: str) -> str:
    lines = markdown.splitlines()
    while lines and not lines[0].strip():
        lines.pop(0)
    if lines:
        _, first, _ = next(_markdown_lines(lines[0]))
        if first and len(first[1]) <= 2 and (
            _heading_title(first[2]).casefold() == _heading_title(heading).casefold()
        ):
            lines.pop(0)
        while lines and not lines[0].strip():
            lines.pop(0)
    return _demote_body_headings(strip_report_references("\n".join(lines)))


def _demote_body_headings(markdown: str) -> str:
    """Keep section-local headings below the assembled report section level."""

    return "\n".join(f"### {match[2]}" if match and len(match[1]) <= 2 else line
                     for line, match, _ in _markdown_lines(markdown)).strip("\r\n")
