from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from simple_ar.report.schema import (
    ReportContext,
    ReportSectionPlan,
    SourceHandle,
)




SURVEY_TEMPLATE_NAMES = {"survey", "survey_long"}

STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "by",
    "for",
    "from",
    "in",
    "into",
    "is",
    "large",
    "language",
    "large language",
    "model",
    "models",
    "of",
    "on",
    "or",
    "the",
    "to",
    "with",
}


def is_survey_report(*, template_name: str, style: str = "", report_mode: str = "") -> bool:
    """Return whether report generation should use survey-oriented guidance."""
    text = " ".join([template_name, style, report_mode]).lower()
    return template_name in SURVEY_TEMPLATE_NAMES or "survey" in text






def enrich_survey_sections(
    sections: Sequence[ReportSectionPlan],
    *,
    context: ReportContext,
) -> list[ReportSectionPlan]:
    """Return topic-specific section plans for survey reports.

    This is deterministic and cheap: it augments generic template sections with
    contract-aware goals and routes source handles by keyword overlap. It is
    deliberately not a benchmark-specific outline oracle.
    """
    contract = context.survey_contract
    if not isinstance(contract, dict) or not contract.get("enabled"):
        return list(sections)
    strategy = str(contract.get("outline_strategy") or "auto").lower()
    if strategy == "template":
        return list(sections)
    planned_sections = _sections_from_outline_plan(contract)
    if planned_sections:
        return _restore_section_source_budget(
            planned_sections,
            context=context,
            contract=contract,
        )
    budget = _bounded_int(contract.get("section_source_budget"), default=12, lower=4, upper=40)
    enriched: list[ReportSectionPlan] = []
    for section in sections:
        goal = _survey_section_goal(section.heading, section.goal, contract)
        handles = route_section_sources(
            context=context,
            heading=section.heading,
            goal=goal,
            contract=contract,
            budget=budget,
        )
        enriched.append(
            section.model_copy(
                update={
                    "goal": goal,
                    "evidence_handles": handles or section.evidence_handles,
                }
            )
        )
    return enriched


def route_section_sources(
    *,
    context: ReportContext,
    heading: str,
    goal: str,
    contract: Mapping[str, Any],
    budget: int,
) -> list[str]:
    """Select a bounded source set for one survey section."""
    candidate_handles = _dedupe_paperish_handles(context.source_handles)
    if not candidate_handles:
        return []
    keywords = _section_keywords(heading, goal, contract)
    scored: list[tuple[int, int, str]] = []
    for index, handle in enumerate(candidate_handles):
        text = _handle_text(handle)
        score = _keyword_score(text, keywords)
        if handle.kind == "paper_brief":
            score += 2
        elif handle.kind == "paper":
            score += 1
        scored.append((score, -index, handle.handle))
    scored.sort(reverse=True)
    selected = [handle for score, _index, handle in scored if score > 0][:budget]
    if len(selected) < min(4, budget):
        fallback = [handle.handle for handle in candidate_handles if handle.handle not in selected]
        selected.extend(fallback[: max(0, min(4, budget) - len(selected))])
    return selected[:budget]














def _restore_section_source_budget(
    sections: Sequence[ReportSectionPlan],
    *,
    context: ReportContext,
    contract: Mapping[str, Any],
) -> list[ReportSectionPlan]:
    """Restore bounded evidence routing for a deterministic outline fallback.

    The deterministic outline includes a few citation keys as an initial
    anchor.  Those keys are not a source budget.  Returning them directly
    silently turns a thorough multi-batch report into a four-source report
    when adaptive planning is unavailable.  Re-route every section through
    the configured budget so fallback changes organization, not evidence
    availability.
    """
    budget = _bounded_int(contract.get("section_source_budget"), default=12, lower=4, upper=40)
    restored: list[ReportSectionPlan] = []
    for section in sections:
        routed = route_section_sources(
            context=context,
            heading=section.heading,
            goal=section.goal,
            contract=contract,
            budget=budget,
        )
        merged = _dedupe_strings([*section.evidence_handles, *routed])[:budget]
        restored.append(section.model_copy(update={"evidence_handles": merged or section.evidence_handles}))
    return restored


def _sections_from_outline_plan(contract: Mapping[str, Any]) -> list[ReportSectionPlan]:
    outline = contract.get("outline_plan") if isinstance(contract.get("outline_plan"), Mapping) else {}
    raw_sections = outline.get("sections") if isinstance(outline.get("sections"), list) else []
    if len(raw_sections) < 5:
        return []
    budget = _bounded_int(contract.get("section_source_budget"), default=12, lower=4, upper=40)
    sections: list[ReportSectionPlan] = []
    for index, raw in enumerate(raw_sections[:12], start=1):
        if not isinstance(raw, Mapping):
            continue
        heading = str(raw.get("heading") or "").strip()
        if not heading:
            continue
        citation_keys = _string_list(raw.get("citation_keys"))
        evidence_handles = _handles_from_citation_keys(contract, citation_keys)[:budget]
        target_words = _bounded_int(raw.get("target_words"), default=0, lower=0, upper=8000)
        min_citations = _bounded_int(raw.get("min_citations"), default=0, lower=0, upper=20)
        subsections = _string_list(raw.get("subsections"))[:6]
        if any(term in heading.lower() for term in ("abstract", "introduction", "conclusion")):
            subsections = []
        sections.append(
            ReportSectionPlan(
                section_id=str(raw.get("section_id") or _normalize_key(heading) or f"section_{index}"),
                heading=heading,
                goal=str(raw.get("goal") or f"Synthesize evidence for {heading}."),
                evidence_handles=evidence_handles,
                target_words=target_words,
                min_citations=min_citations,
                subsections=subsections,
                required=bool(raw.get("required", True)),
                final_order=index,
                draft_order=_survey_draft_order(heading, index, len(raw_sections)),
            )
        )
    return sections


def _survey_draft_order(heading: str, final_order: int, total: int) -> int:
    """Draft synthesis-heavy sections before summary-style sections."""

    lowered = heading.lower()
    if "abstract" in lowered:
        return total + 20
    if "introduction" in lowered:
        return total + 10
    if "conclusion" in lowered:
        return total + 5
    return final_order


def _handles_from_citation_keys(contract: Mapping[str, Any], keys: Sequence[str]) -> list[str]:
    selection = contract.get("paper_selection") if isinstance(contract.get("paper_selection"), Mapping) else {}
    papers = selection.get("selected_papers") if isinstance(selection.get("selected_papers"), list) else []
    by_key: dict[str, str] = {}
    fallback: list[str] = []
    for paper in papers:
        if not isinstance(paper, Mapping):
            continue
        handle = str(paper.get("handle") or "").strip()
        if not handle:
            continue
        fallback.append(handle)
        for key_name in ("citation_key", "paper_id"):
            key = str(paper.get(key_name) or "").strip()
            if key:
                by_key[key] = handle
    selected: list[str] = []
    for key in keys:
        handle = by_key.get(str(key).strip())
        if handle and handle not in selected:
            selected.append(handle)
    if len(selected) < 4:
        for handle in fallback:
            if handle not in selected:
                selected.append(handle)
            if len(selected) >= 4:
                break
    return selected






























def _normalize_key(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(text or "").lower()).strip("_")[:64]


























def _survey_section_goal(heading: str, fallback: str, contract: Mapping[str, Any]) -> str:
    lowered = heading.lower()
    facets = ", ".join(_string_list(contract.get("required_facets"))[:8])
    topic = str(contract.get("topic") or "the topic")
    prefix = f"For the survey on {topic}, "
    if "abstract" in lowered:
        return prefix + "summarize scope, taxonomy, evidence base, evaluation state, and open challenges after the body is known."
    if "introduction" in lowered:
        return prefix + "define reader needs, topic boundaries, major facets, and the organization of the survey."
    if "foundation" in lowered or "taxonomy" in lowered:
        return prefix + f"build a topic-specific taxonomy over facets such as {facets or 'methods, systems, applications, and evaluation'}."
    if "construction" in lowered or "system" in lowered:
        return prefix + "compare how systems are built, including modules, data/context flow, adaptation, and implementation tradeoffs."
    if "application" in lowered or "domain" in lowered:
        return prefix + "map use cases to task settings, evidence, benefits, and limitations."
    if "evaluation" in lowered or "benchmark" in lowered:
        return prefix + "compare datasets, metrics, protocols, baselines, reproducibility, and evidence quality."
    if "related" in lowered or "position" in lowered:
        return prefix + "position this synthesis against prior surveys and adjacent areas without becoming chronological notes."
    if "challenge" in lowered or "problem" in lowered:
        return prefix + "synthesize unresolved technical, empirical, deployment, and evaluation challenges with evidence boundaries."
    if "future" in lowered:
        return prefix + "state concrete research directions and what evidence would confirm or falsify them."
    if "conclusion" in lowered:
        return prefix + "close with the field state, strongest takeaways, and remaining uncertainty."
    return fallback or prefix + "write a traceable, cross-paper synthesis section."


def _section_keywords(heading: str, goal: str, contract: Mapping[str, Any]) -> set[str]:
    lowered = f"{heading} {goal}".lower()
    terms = set(_topic_terms(lowered))
    terms.update(_topic_terms(" ".join(_string_list(contract.get("topic_terms")))))
    if any(word in lowered for word in ("foundation", "taxonomy", "introduction")):
        terms.update({"taxonomy", "survey", "overview", "foundation", "framework", "architecture"})
    if any(word in lowered for word in ("construction", "system")):
        terms.update({"architecture", "pipeline", "retrieval", "generation", "training", "prompt", "context"})
    if any(word in lowered for word in ("application", "domain")):
        terms.update({"application", "domain", "task", "use", "medical", "education", "recommendation"})
    if any(word in lowered for word in ("evaluation", "benchmark")):
        terms.update({"evaluation", "benchmark", "dataset", "metric", "protocol", "baseline"})
    if any(word in lowered for word in ("challenge", "future", "problem", "limitation")):
        terms.update({"challenge", "limitation", "future", "robustness", "safety", "hallucination", "cost"})
    for facet in _string_list(contract.get("required_facets")):
        terms.update(_topic_terms(facet.replace("_", " ")))
    return {term for term in terms if term and term not in STOPWORDS}


def _dedupe_paperish_handles(handles: Sequence[SourceHandle]) -> list[SourceHandle]:
    by_paper: dict[str, SourceHandle] = {}
    no_paper: list[SourceHandle] = []
    for handle in handles:
        if handle.kind not in {"paper", "paper_brief"}:
            continue
        key = handle.paper_id or handle.citation_key or handle.handle
        existing = by_paper.get(key)
        if existing is None:
            by_paper[key] = handle
            continue
        if existing.kind == "paper" and handle.kind == "paper_brief":
            by_paper[key] = handle
    for handle in handles:
        if handle.kind in {"paper", "paper_brief"}:
            continue
        if handle.kind == "synthesis":
            no_paper.append(handle)
    return list(by_paper.values()) + no_paper[:2]


def _handle_text(handle: SourceHandle) -> str:
    metadata = handle.metadata if isinstance(handle.metadata, dict) else {}
    pieces = [
        handle.title,
        handle.summary,
        handle.section,
        str(metadata.get("method") or ""),
        str(metadata.get("contribution") or ""),
        str(metadata.get("evaluation") or ""),
        str(metadata.get("relevance") or ""),
    ]
    return " ".join(piece for piece in pieces if piece).lower()


def _keyword_score(text: str, keywords: set[str]) -> int:
    if not text or not keywords:
        return 0
    score = 0
    for keyword in keywords:
        if len(keyword) < 3:
            continue
        if keyword in text:
            score += 2 if " " in keyword else 1
    return score




def _topic_terms(text: str) -> list[str]:
    terms: list[str] = []
    seen: set[str] = set()
    for token in re.findall(r"[A-Za-z][A-Za-z0-9+-]{2,}", text.lower()):
        normalized = token.strip("-+")
        if not normalized or normalized in STOPWORDS or normalized in seen:
            continue
        terms.append(normalized)
        seen.add(normalized)
    return terms


def _string_list(value: object) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _dedupe_strings(values: Sequence[str]) -> list[str]:
    rows: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = " ".join(str(value or "").split())
        key = text.lower()
        if not text or key in seen:
            continue
        rows.append(text)
        seen.add(key)
    return rows


def _bounded_int(value: object, *, default: int, lower: int, upper: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        number = default
    return max(lower, min(upper, number))
