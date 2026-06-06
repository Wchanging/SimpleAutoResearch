from __future__ import annotations

from simple_ar.report.schema import ReportSectionDraft


def normalize_report_markdown(markdown: str) -> str:
    """Normalize final Markdown without changing report semantics."""
    lines = [line.rstrip() for line in markdown.strip().splitlines()]
    return "\n".join(lines).strip() + "\n"


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
    return body
