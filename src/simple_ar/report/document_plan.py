"""Resolve one stable document plan before report drafting begins.

The report stage used to keep separate template, survey, outline, and visual
plans alive while writing.  This module deliberately narrows that lifecycle:
after the optional outline agent has proposed its sections, every downstream
component reads the same frozen ``ReportDocumentPlan``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from simple_ar.report.schema import (
    ReportDocumentPlan,
    ReportRuntimeConfig,
    ReportSectionPlan,
    ReportVisualIntent,
)


_RENDERABLE_FIGURE_VIEWS = {
    "taxonomy-map",
    "system-construction-flow",
    "evaluation-landscape",
    "challenge-roadmap",
}


def resolve_document_plan(
    *,
    sections: Sequence[ReportSectionPlan],
    contract: Mapping[str, Any] | None,
    config: ReportRuntimeConfig,
    visual_candidates: Sequence[Mapping[str, Any]] = (),
    status: str = "resolved",
) -> ReportDocumentPlan:
    """Freeze sections, budgets, and feasible visual intents in one artifact.

    The outline agent may propose optional visuals, but deterministic checks
    bind them to existing sections and evidence. Configured counts are maxima,
    never obligations to manufacture content.
    """
    frozen_sections = _rebalance_sections(sections, _target_words(contract))
    visual_budget = {
        "tables": _visual_budget(contract, "tables", int(config.longform.target_tables or 0)),
        "figures": (
            _visual_budget(contract, "figures", int(config.figures.max_figures or 0))
            if config.figures.enabled
            else 0
        ),
    }
    intents = _normalize_visual_intents(
        visual_candidates,
        sections=frozen_sections,
        table_limit=visual_budget["tables"],
        figure_limit=visual_budget["figures"],
    )
    return ReportDocumentPlan(
        status="fallback" if status == "fallback" else "resolved",
        sections=frozen_sections,
        target_words=_target_words(contract),
        visual_budget=visual_budget,
        visual_intents=intents,
        notes=[
            "DocumentPlan is frozen after outline resolution; downstream report components consume this artifact only.",
            "Visual budgets are upper bounds. Missing intents are not synthesized from fixed section templates.",
        ],
    )


def visual_requirements(plan: ReportDocumentPlan | None, section: ReportSectionPlan) -> dict[str, list[dict[str, Any]]]:
    """Return the compact visual obligations owned by one section."""
    if plan is None:
        return {"tables": [], "figures": []}
    output: dict[str, list[dict[str, Any]]] = {"tables": [], "figures": []}
    for intent in plan.visual_intents:
        if intent.section_id != section.section_id:
            continue
        payload = {
            "visual_id": intent.visual_id,
            "title": intent.title,
            "purpose": intent.purpose,
            "evidence_handles": intent.evidence_handles,
        }
        if intent.kind == "table":
            payload["columns"] = intent.columns
            output["tables"].append(payload)
        else:
            payload["view"] = intent.view
            output["figures"].append(payload)
    return output


def visual_plan_for_renderer(plan: ReportDocumentPlan | None) -> list[ReportVisualIntent]:
    """Return only deterministic-renderable figure intents."""
    if plan is None:
        return []
    return [
        intent
        for intent in plan.visual_intents
        if intent.kind == "figure" and intent.view in _RENDERABLE_FIGURE_VIEWS
    ]


def _target_words(contract: Mapping[str, Any] | None) -> int:
    expected = contract.get("expected_coverage") if isinstance(contract, Mapping) else None
    raw = expected.get("target_words") if isinstance(expected, Mapping) else 0
    try:
        return max(0, min(50000, int(raw or 0)))
    except (TypeError, ValueError):
        return 0


def _visual_budget(contract: Mapping[str, Any] | None, kind: str, configured: int) -> int:
    if configured > 0:
        return configured
    budget = contract.get("visual_budget") if isinstance(contract, Mapping) else None
    raw = budget.get(kind) if isinstance(budget, Mapping) else 0
    try:
        return max(0, min(12, int(raw or 0)))
    except (TypeError, ValueError):
        return 0


def _rebalance_sections(
    sections: Sequence[ReportSectionPlan],
    total_target_words: int,
) -> list[ReportSectionPlan]:
    rows = list(sections)
    if not rows or total_target_words <= 0:
        return rows
    minima = [_minimum_words(section) for section in rows]
    desired = [max(minimum, int(section.target_words or 0)) for section, minimum in zip(rows, minima)]
    minimum_total = sum(minima)
    if minimum_total >= total_target_words:
        weights = minima
    else:
        weights = [max(1, value - minimum) for value, minimum in zip(desired, minima)]
    if minimum_total >= total_target_words:
        raw = [total_target_words * value / max(1, sum(weights)) for value in weights]
        allocated = [int(value) for value in raw]
    else:
        remaining = total_target_words - minimum_total
        additions = [remaining * value / max(1, sum(weights)) for value in weights]
        allocated = [minimum + int(addition) for minimum, addition in zip(minima, additions)]
        raw = additions
    leftover = total_target_words - sum(allocated)
    for index in sorted(range(len(allocated)), key=lambda item: raw[item] - int(raw[item]), reverse=True)[:leftover]:
        allocated[index] += 1
    return [section.model_copy(update={"target_words": words}) for section, words in zip(rows, allocated)]


def _minimum_words(section: ReportSectionPlan) -> int:
    heading = section.heading.lower()
    if "abstract" in heading:
        return 180
    if "conclusion" in heading:
        return 350
    if "introduction" in heading:
        return 600
    return 500


def _normalize_visual_intents(
    candidates: Sequence[Mapping[str, Any]],
    *,
    sections: Sequence[ReportSectionPlan],
    table_limit: int,
    figure_limit: int,
) -> list[ReportVisualIntent]:
    by_id = {section.section_id: section for section in sections}
    by_heading = {_key(section.heading): section for section in sections}
    seen: set[tuple[str, str]] = set()
    used_figure_views: set[str] = set()
    counts = {"table": 0, "figure": 0}
    intents: list[ReportVisualIntent] = []
    for index, raw in enumerate(candidates, start=1):
        kind = str(raw.get("kind") or "").strip().lower()
        if kind not in counts:
            continue
        if kind == "table" and counts[kind] >= table_limit:
            continue
        if kind == "figure" and counts[kind] >= figure_limit:
            continue
        section = by_id.get(str(raw.get("section_id") or ""))
        if section is None:
            section = by_heading.get(_key(str(raw.get("section_heading") or "")))
        if section is None or len(section.evidence_handles) < 2:
            continue
        title = _clean_text(raw.get("title"), limit=100)
        purpose = _clean_text(raw.get("purpose"), limit=260)
        if not title or not purpose:
            continue
        view = str(raw.get("view") or "").strip()
        if kind == "figure" and view not in _RENDERABLE_FIGURE_VIEWS:
            continue
        if kind == "figure" and view in used_figure_views:
            continue
        columns = _text_list(raw.get("columns"), limit=6)
        if kind == "table" and len(columns) < 2:
            continue
        key = (kind, title.lower())
        if key in seen:
            continue
        seen.add(key)
        evidence = _text_list(raw.get("evidence_handles"), limit=8)
        allowed = set(section.evidence_handles)
        evidence = [handle for handle in evidence if handle in allowed] or section.evidence_handles[:6]
        intents.append(
            ReportVisualIntent(
                visual_id=f"{kind}-{index:02d}",
                kind=kind,
                title=title,
                purpose=purpose,
                section_id=section.section_id,
                evidence_handles=evidence,
                view=view if kind == "figure" else "",
                columns=columns if kind == "table" else [],
            )
        )
        if kind == "figure":
            used_figure_views.add(view)
        counts[kind] += 1
    return intents


def _key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _clean_text(value: object, *, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit]


def _text_list(value: object, *, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    output: list[str] = []
    for item in value:
        text = _clean_text(item, limit=120)
        if text and text not in output:
            output.append(text)
        if len(output) >= limit:
            break
    return output
