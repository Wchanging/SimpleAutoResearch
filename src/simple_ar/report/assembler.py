from __future__ import annotations

import re

from simple_ar.report.schema import ReportSectionDraft


def normalize_report_markdown(markdown: str) -> str:
    """Normalize final Markdown without changing report semantics."""
    lines = [line.rstrip() for line in markdown.strip().splitlines()]
    return "\n".join(lines).strip() + "\n"


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
    in_fence = False
    lines: list[str] = []
    for line in markdown.splitlines():
        if line.strip().startswith("```"):
            in_fence = not in_fence
            lines.append(line.rstrip())
            continue
        if in_fence:
            lines.append(line.rstrip())
            continue
        match = re.match(r"^(#{2,6})\s+(.+?)\s*$", line)
        if not match:
            lines.append(line.rstrip())
            continue
        level = len(match.group(1))
        title = _strip_heading_number(match.group(2))
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
        body = _section_body(section.draft_markdown)
        if body:
            parts.append(f"## {section.heading}\n\n{body}")
    return normalize_report_markdown("\n\n".join(parts))


def _section_body(markdown: str) -> str:
    text = markdown.strip()
    if not text:
        return ""
    lines = text.splitlines()
    while lines and lines[0].strip().startswith("#"):
        lines.pop(0)
        while lines and not lines[0].strip():
            lines.pop(0)
    body = "\n".join(lines).strip()
    if "## References" in body:
        body = body.split("## References", maxsplit=1)[0].strip()
    return _demote_body_headings(body)


def _demote_body_headings(markdown: str) -> str:
    """Keep section-local headings below the assembled report section level."""

    def replace(match: re.Match[str]) -> str:
        hashes = match.group(1)
        title = match.group(2)
        if len(hashes) <= 2:
            return f"### {title}"
        return match.group(0)

    return re.sub(r"(?m)^(#{1,6})\s+(.+\S)\s*$", replace, markdown).strip()
