from __future__ import annotations

import html
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from simple_ar.core.artifacts import write_text
from simple_ar.report.schema import ReportFigureConfig


class ReportFigureRecord(BaseModel):
    """One deterministic report figure artifact."""

    figure_id: str
    title: str
    path: str
    anchor: str
    caption: str = ""


class ReportFigureResult(BaseModel):
    """Report markdown plus generated figure metadata."""

    report_markdown: str
    figures: list[ReportFigureRecord] = Field(default_factory=list)


@dataclass(frozen=True)
class _FigureSpec:
    figure_id: str
    title: str
    anchor_patterns: tuple[str, ...]
    section_keywords: tuple[str, ...]
    fallback_items: tuple[str, ...]
    caption: str


_FIGURE_SPECS: tuple[_FigureSpec, ...] = (
    _FigureSpec(
        figure_id="taxonomy-map",
        title="Conceptual taxonomy map",
        anchor_patterns=("foundations and taxonomy", "taxonomy", "organizing axes"),
        section_keywords=("taxonomy", "axis", "family", "foundation", "class"),
        fallback_items=("Foundations", "Methods", "Applications", "Evaluation", "Challenges"),
        caption="Figure: a compact taxonomy map derived from the survey structure and comparison tables.",
    ),
    _FigureSpec(
        figure_id="system-construction-flow",
        title="System construction flow",
        anchor_patterns=("system construction", "method", "architecture", "construction"),
        section_keywords=("role", "coordination", "grounding", "retrieval", "verification", "memory"),
        fallback_items=("Task", "Decomposition", "Specialized agents", "Grounding", "Synthesis", "Validation"),
        caption="Figure: a high-level construction flow summarizing recurring system components.",
    ),
    _FigureSpec(
        figure_id="evaluation-landscape",
        title="Evaluation landscape",
        anchor_patterns=("evaluation and benchmarks", "evaluation", "benchmarks"),
        section_keywords=("benchmark", "metric", "dataset", "baseline", "cost", "failure"),
        fallback_items=("Datasets", "Baselines", "Metrics", "Cost", "Robustness", "Reproducibility"),
        caption="Figure: an evaluation landscape distilled from the benchmark and metric discussion.",
    ),
    _FigureSpec(
        figure_id="challenge-roadmap",
        title="Challenges and future directions",
        anchor_patterns=("challenges and open problems", "future directions", "open problems"),
        section_keywords=("challenge", "gap", "limitation", "risk", "future", "direction"),
        fallback_items=("Coverage", "Faithfulness", "Efficiency", "Robustness", "Safety", "Transfer"),
        caption="Figure: challenge and future-direction map grounded in the survey's open-problem sections.",
    ),
)


def maybe_add_report_figures(
    *,
    report_markdown: str,
    report_dir: Path,
    config: ReportFigureConfig,
    template_name: str = "",
    survey_contract: dict[str, Any] | None = None,
    emit=None,
) -> ReportFigureResult:
    """Generate deterministic SVG figures and insert Markdown image links.

    The generator is intentionally conservative: it uses only the existing
    report text, produces small SVG files, and never calls an LLM. This keeps
    the feature useful for generic reports while avoiding benchmark-specific
    image hallucination.
    """

    if not config.enabled or config.mode == "off":
        return ReportFigureResult(report_markdown=report_markdown)
    if config.format != "svg":
        return ReportFigureResult(report_markdown=report_markdown)
    if re.search(r"!\[[^\]]*\]\([^)]+\)", report_markdown):
        return ReportFigureResult(report_markdown=report_markdown)

    max_figures = config.max_figures if config.max_figures > 0 else _default_figure_count(template_name)
    if max_figures <= 0:
        return ReportFigureResult(report_markdown=report_markdown)

    sections = _markdown_sections(report_markdown)
    figures_dir = report_dir / "figures"
    generated: list[ReportFigureRecord] = []
    updated = report_markdown

    for spec in _FIGURE_SPECS:
        if len(generated) >= max_figures:
            break
        section = _best_section(sections, spec)
        if not section:
            continue
        items = _figure_items(section["body"], spec, survey_contract=survey_contract)
        if len(items) < 3:
            items = list(spec.fallback_items)
        path = figures_dir / f"{spec.figure_id}.svg"
        write_text(path, _render_svg(spec.title, items[:8]))
        rel_path = f"figures/{path.name}"
        image_block = f"\n![{spec.title}]({rel_path})\n\n*{spec.caption}*\n"
        updated = _insert_after_heading(updated, section["heading"], image_block)
        generated.append(
            ReportFigureRecord(
                figure_id=spec.figure_id,
                title=spec.title,
                path=rel_path,
                anchor=section["heading"],
                caption=spec.caption,
            )
        )

    if generated and emit is not None:
        emit(f"Generated {len(generated)} report figure(s).")
    return ReportFigureResult(report_markdown=updated, figures=generated)


def _default_figure_count(template_name: str) -> int:
    if str(template_name or "").strip().lower() == "survey_long":
        return 3
    return 0


def _markdown_sections(markdown: str) -> list[dict[str, str]]:
    lines = markdown.splitlines()
    sections: list[dict[str, str]] = []
    current_heading = ""
    current_lines: list[str] = []
    for line in lines:
        if re.match(r"^##\s+", line):
            if current_heading:
                sections.append({"heading": current_heading, "body": "\n".join(current_lines)})
            current_heading = line.strip()
            current_lines = []
        elif current_heading:
            current_lines.append(line)
    if current_heading:
        sections.append({"heading": current_heading, "body": "\n".join(current_lines)})
    return sections


def _best_section(sections: list[dict[str, str]], spec: _FigureSpec) -> dict[str, str] | None:
    for pattern in spec.anchor_patterns:
        for section in sections:
            if pattern in _normalize_heading(section["heading"]):
                return section
    best: dict[str, str] | None = None
    best_score = 0
    for section in sections:
        text = f"{section['heading']}\n{section['body']}".lower()
        score = sum(text.count(keyword) for keyword in spec.section_keywords)
        if score > best_score:
            best = section
            best_score = score
    return best if best_score > 0 else None


def _normalize_heading(heading: str) -> str:
    text = re.sub(r"^#+\s*", "", heading).strip().lower()
    text = re.sub(r"^\d+(?:\.\d+)*\s+", "", text)
    return text


def _figure_items(
    section_body: str,
    spec: _FigureSpec,
    *,
    survey_contract: dict[str, Any] | None = None,
) -> list[str]:
    candidates: list[str] = []
    candidates.extend(_table_first_column_items(section_body))
    candidates.extend(_heading_items(section_body))
    candidates.extend(_bullet_items(section_body))
    candidates.extend(_contract_items(survey_contract, spec))
    seen: set[str] = set()
    output: list[str] = []
    for item in candidates:
        clean = _clean_item(item)
        if not clean:
            continue
        key = clean.lower()
        if key in seen:
            continue
        if not _matches_spec(clean, spec):
            continue
        seen.add(key)
        output.append(clean)
        if len(output) >= 8:
            break
    if len(output) < 3:
        for item in candidates:
            clean = _clean_item(item)
            key = clean.lower()
            if clean and key not in seen:
                seen.add(key)
                output.append(clean)
            if len(output) >= 6:
                break
    return output


def _contract_items(survey_contract: dict[str, Any] | None, spec: _FigureSpec) -> list[str]:
    if not isinstance(survey_contract, dict):
        return []
    facets = _string_items(survey_contract.get("required_facets"), limit=12)
    reader_needs = _string_items(survey_contract.get("reader_needs"), limit=8)
    candidates: list[str] = []
    if spec.figure_id in {"taxonomy-map", "evaluation-landscape"}:
        candidates.extend(facets)
    if spec.figure_id == "challenge-roadmap":
        candidates.extend(reader_needs)
        candidates.extend(facets)
    if spec.figure_id == "system-construction-flow":
        method_like = [
            item
            for item in facets
            if any(term in item.lower() for term in ("method", "model", "system", "architecture", "pipeline", "framework"))
        ]
        candidates.extend(method_like or facets)
    return [_humanize_contract_item(item) for item in candidates]


def _string_items(value: object, *, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    output: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if text:
            output.append(text)
        if len(output) >= limit:
            break
    return output


def _humanize_contract_item(value: str) -> str:
    text = re.sub(r"[_-]+", " ", value).strip()
    return " ".join(part.capitalize() if part.isupper() is False else part for part in text.split())


def _table_first_column_items(text: str) -> list[str]:
    items: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|") or "---" in stripped:
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if cells and cells[0] and cells[0].lower() not in {"axis", "challenge", "direction", "domain / use case"}:
            items.append(cells[0])
    return items


def _heading_items(text: str) -> list[str]:
    return [
        re.sub(r"^#+\s*", "", line).strip()
        for line in text.splitlines()
        if re.match(r"^###\s+", line)
    ]


def _bullet_items(text: str) -> list[str]:
    items: list[str] = []
    for line in text.splitlines():
        match = re.match(r"^\s*[-*]\s+(?:\*\*)?([^:.;\n*]+)", line)
        if match:
            items.append(match.group(1))
    return items


def _matches_spec(item: str, spec: _FigureSpec) -> bool:
    text = item.lower()
    return any(keyword in text for keyword in spec.section_keywords) or len(spec.section_keywords) == 0


def _clean_item(item: str) -> str:
    text = re.sub(r"\[[^\]]+\]\([^)]+\)", "", item)
    text = re.sub(r"\[[\d,\s]+\]", "", text)
    text = re.sub(r"[*_`]+", "", text)
    text = re.sub(r"\s+", " ", text).strip(" -:;.,")
    if not text or len(text) > 70:
        return ""
    if text.lower() in {"what it foregrounds", "representative framing", "boundary condition"}:
        return ""
    return text


def _insert_after_heading(markdown: str, heading: str, block: str) -> str:
    lines = markdown.splitlines()
    for index, line in enumerate(lines):
        if line.strip() == heading.strip():
            insert_at = index + 1
            while insert_at < len(lines) and lines[insert_at].strip() == "":
                insert_at += 1
            return "\n".join(lines[:insert_at]) + block + "\n".join(lines[insert_at:]) + "\n"
    return markdown.rstrip() + "\n" + block


def _render_svg(title: str, items: list[str]) -> str:
    width = 1120
    card_w = 250
    card_h = 82
    gap_x = 28
    gap_y = 32
    columns = 3 if len(items) > 4 else 2
    rows = (len(items) + columns - 1) // columns
    height = 120 + rows * card_h + max(0, rows - 1) * gap_y + 30
    start_x = (width - (columns * card_w + (columns - 1) * gap_x)) // 2
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img">',
        "<style>text{font-family:Arial,Helvetica,sans-serif}.title{font-size:28px;font-weight:700;fill:#172033}.node{fill:#f8fafc;stroke:#475569;stroke-width:1.5}.label{font-size:17px;fill:#172033}.index{font-size:13px;fill:#64748b}</style>",
        f'<rect x="0" y="0" width="{width}" height="{height}" fill="#ffffff"/>',
        f'<text class="title" x="{width // 2}" y="48" text-anchor="middle">{html.escape(title)}</text>',
    ]
    center_points: list[tuple[int, int]] = []
    for idx, item in enumerate(items):
        row = idx // columns
        col = idx % columns
        x = start_x + col * (card_w + gap_x)
        y = 86 + row * (card_h + gap_y)
        cx = x + card_w // 2
        cy = y + card_h // 2
        center_points.append((cx, cy))
        parts.append(f'<rect class="node" x="{x}" y="{y}" rx="10" ry="10" width="{card_w}" height="{card_h}"/>')
        parts.append(f'<text class="index" x="{x + 18}" y="{y + 24}">{idx + 1:02d}</text>')
        for line_no, line in enumerate(_wrap_text(item, max_chars=26)[:2]):
            parts.append(
                f'<text class="label" x="{cx}" y="{y + 42 + line_no * 22}" text-anchor="middle">{html.escape(line)}</text>'
            )
    for idx in range(len(center_points) - 1):
        x1, y1 = center_points[idx]
        x2, y2 = center_points[idx + 1]
        if abs(y1 - y2) < 5:
            parts.append(f'<path d="M{x1 + card_w // 2 - 8} {y1} L{x2 - card_w // 2 + 8} {y2}" stroke="#94a3b8" stroke-width="1.2" fill="none" marker-end="url(#arrow)"/>')
    parts.insert(
        2,
        '<defs><marker id="arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="#94a3b8"/></marker></defs>',
    )
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def _wrap_text(text: str, *, max_chars: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) <= max_chars:
            current = candidate
            continue
        if current:
            lines.append(current)
        current = word[:max_chars]
    if current:
        lines.append(current)
    return lines or [text[:max_chars]]
