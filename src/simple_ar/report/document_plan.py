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
    ReportContext,
    ReportRuntimeConfig,
    ReportSectionPlan,
    ReportVisualIntent,
)

LENGTH_REQUEST_SCHEMA = {
    "unit": "words", "scope": "whole_document",
    "request_quote": "exact task quotation, or return null instead of this object",
    "min_words": 0, "max_words": 0, "target_words": 0,
}
LENGTH_REQUEST_RULE = (
    "For an explicit whole-document word limit/range, return length_request with the exact original task quotation "
    "and numeric bounds (an exact count uses equal min/max; an upper limit uses min=0). Choose a target within it. "
    "Section target_words are relative body shares: the controller reserves known title/headings/attachments before "
    "freezing. References and future generated visuals can add unresolved cost; final delivery must be checked. "
    "Return null if no such request, or if the request uses pages, characters or exclusions; do not convert units "
    "or silently infer exclusions."
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
    title: str = "",
    supplied_figure_handles: Sequence[str] = (),
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
        supplied_figure_handles=supplied_figure_handles,
    )
    return ReportDocumentPlan(
        status="fallback" if status == "fallback" else "resolved",
        title=title,
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
            payload["assembly_owned"] = intent.view == "supplied-data"
            if payload["assembly_owned"]:
                payload["delivery"] = "Existing figures are attached after drafting in this frozen owning section. Explain recorded values; do not generate image paths or duplicate the chart. Missing image links in a pre-assembly draft are not a missing deliverable."
            output["figures"].append(payload)
    return output


def validate_length_request(value: Any, *, objective: str) -> dict[str, Any]:
    """Validate a planner interpretation, not infer a word limit from keywords.

    An exact task quotation anchors the interpretation. This does not certify
    its semantics; the complete original request remains available to review.
    Unsupported units/scopes must not be silently converted to word counts.
    """
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError("length_request must be null or an explicit whole-document word request")
    if value.get("unit") != "words" or value.get("scope") != "whole_document":
        raise ValueError("length_request supports only explicit whole-document words; do not convert pages, characters or excluded-body scopes")
    quote = value.get("request_quote")
    if not isinstance(quote, str) or not quote.strip() or quote.strip() not in objective:
        raise ValueError("length_request requires an exact quotation from the original task")
    counts = {key: value.get(key) for key in ("min_words", "max_words", "target_words")}
    if any(type(number) is not int for number in counts.values()):
        raise ValueError("length_request word bounds and target must be integers")
    minimum, maximum, target = (counts[key] for key in ("min_words", "max_words", "target_words"))
    if not 0 <= minimum <= target <= maximum <= 50000 or target < 1:
        raise ValueError("length_request requires 0 <= min <= target <= max <= 50000 and a positive target")
    quoted_numbers = {int(number.replace(",", "")) for number in re.findall(r"\d+(?:,\d{3})*", quote)}
    if maximum not in quoted_numbers or (minimum and minimum not in quoted_numbers):
        raise ValueError("length_request bounds must occur in its original task quotation")
    return {"unit": "words", "scope": "whole_document", "request_quote": quote.strip(), **counts}


def check_document_length(budget: Mapping[str, Any], *, objective: str, token_count: int | None) -> dict[str, Any]:
    """One side-effect-free check for canonical previews and final deliveries.

    A preview remains a preview: the caller owns completeness and adoption.
    Never infer a legacy contract or certify its semantic interpretation.
    """
    if not budget:
        return {"status": "no_contract"}
    try:
        request = validate_length_request(budget, objective=objective)
    except ValueError as exc:
        return {"status": "invalid_contract", "reason": str(exc)}
    if type(token_count) is not int or token_count < 0:
        return {"status": "unavailable", **request}
    status = ("below_range" if token_count < request["min_words"] else
              "above_range" if token_count > request["max_words"] else "within_range")
    return {"status": status, **request, "markdown_token_count": token_count}


def reserve_document_words(
    plan: ReportDocumentPlan, *, request: Mapping[str, Any], forecast: Mapping[str, Any],
) -> ReportDocumentPlan:
    """Allocate model prose after known assembly costs on this same frozen plan.

    Section suggestions are relative weights, not genre-specific minimums.
    Future citations and generated visuals remain explicit unknowns. This is a
    planning aid; only the completed canonical preview can check final length.
    """
    if not request:
        return plan
    fixed = forecast.get("known_fixed_markdown_tokens")
    if forecast.get("status") != "known_assembly_forecast" or type(fixed) is not int or fixed < 0:
        raise ValueError("Cannot allocate whole-document words without a known assembly forecast")
    remaining = request["target_words"] - fixed
    if not plan.sections or remaining < len(plan.sections):
        raise ValueError("Known assembly text leaves insufficient prose for the proposed sections; revise organization or clarify the length request, not drop registered evidence")
    # Give each nonempty planned section one word, then distribute its share.
    weights = [max(1, section.target_words) for section in plan.sections]
    shares = [(remaining - len(weights)) * weight / sum(weights) for weight in weights]
    allocation = [1 + int(share) for share in shares]
    leftover = remaining - sum(allocation)
    for index in sorted(range(len(weights)), key=lambda i: shares[i] - int(shares[i]), reverse=True)[:leftover]:
        allocation[index] += 1
    return plan.model_copy(update={
        "target_words": request["target_words"],
        "sections": [section.model_copy(update={"target_words": words}) for section, words in zip(plan.sections, allocation)],
        "length_budget": {**request, **forecast, "model_body_target_words": remaining,
            "interpretation_status": "planner_interpretation_anchored_to_task_not_semantically_verified",
            "guidance": "The original request governs. Section shares are approximate, not minima. Known assembly text is reserved; unresolved future costs are not zero. Recheck the complete delivery, and trim redundancy rather than necessary evidence."},
    })


def supplied_figure_sources(context: ReportContext | None) -> list[dict[str, Any]]:
    """Identify existing analysis figures by registered document identity only."""
    if context is None:
        return []
    analyses = context.results.get("supplied_analyses", [])
    if not isinstance(analyses, list):
        return []
    by_document = {str(row.get("document_id")): row for row in analyses
                   if isinstance(row, Mapping) and row.get("document_id")
                   and isinstance(row.get("figures"), list) and row["figures"]}
    return [{"handle": handle.handle, "title": handle.title,
             "figure_count": len(by_document[str(handle.metadata["document_id"])]["figures"]),
             "captions": [row.get("caption", "") for row in
                          by_document[str(handle.metadata["document_id"])]["figures"]]}
            for handle in context.source_handles
            if str(handle.metadata.get("document_id")) in by_document]


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
    supplied_figure_handles: Sequence[str] = (),
) -> list[ReportVisualIntent]:
    by_id = {section.section_id: section for section in sections}
    by_heading = {_key(section.heading): section for section in sections}
    seen: set[tuple[str, str]] = set()
    used_figure_views: set[str] = set()
    counts = {"table": 0, "figure": 0}
    intents: list[ReportVisualIntent] = []
    assigned_sources: set[str] = set()
    for index, raw in enumerate(candidates, start=1):
        kind = str(raw.get("kind") or "").strip().lower()
        # Placement of existing registered figures is not a new rendering job
        # and does not consume the generated-figure quota. Final physical
        # output limits still apply at the report capability boundary.
        if kind == "figure" and raw.get("view") == "supplied-data":
            section = by_id.get(str(raw.get("section_id") or ""))
            if section is None:
                # An explicit heading names the planner's own section; it is
                # not a semantic guess about where arbitrary data belong.
                matches = [row for row in sections if row.heading == raw.get("section_heading")]
                section = matches[0] if len(matches) == 1 else None
            evidence = _text_list(raw.get("evidence_handles"), limit=8)
            title = _clean_text(raw.get("title"), limit=100)
            purpose = _clean_text(raw.get("purpose"), limit=260)
            if (section is None or not title or not purpose or len(evidence) != 1
                    or evidence[0] not in supplied_figure_handles
                    or evidence[0] not in section.evidence_handles):
                raise ValueError("supplied-data placement requires one registered analysis figure source in its owning section")
            if evidence[0] in assigned_sources:
                raise ValueError("A supplied figure source cannot have multiple placement owners")
            assigned_sources.add(evidence[0])
            intents.append(ReportVisualIntent(visual_id=f"figure-{index:02d}", kind="figure",
                title=title, purpose=purpose, section_id=section.section_id,
                evidence_handles=evidence, view="supplied-data"))
            continue
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
