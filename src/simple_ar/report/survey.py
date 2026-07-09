from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from simple_ar.report.schema import ReportContext, ReportRuntimeConfig, ReportSectionPlan, ReportTemplateBundle, SourceHandle


SURVEY_TEMPLATE_NAMES = {"survey", "survey_long"}
DEFAULT_SURVEY_FACETS = [
    "foundations_and_scope",
    "method_taxonomy",
    "system_construction",
    "applications_and_domains",
    "evaluation_and_benchmarks",
    "challenges_and_future_directions",
]
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


def attach_survey_contract(
    context: ReportContext,
    *,
    template: ReportTemplateBundle,
    config: ReportRuntimeConfig,
    raw_config: Mapping[str, object],
) -> ReportContext:
    """Attach a deterministic survey task contract to report context when useful.

    The contract is intentionally benchmark-neutral. It describes the survey
    writing task, reader needs, coverage facets, and source budget without
    looking at any external reference outline or benchmark judge output.
    """
    if not config.survey_contract:
        return context
    if not is_survey_report(template_name=template.name, style=config.style, report_mode=context.report_mode):
        return context
    contract = build_survey_contract(
        context=context,
        template=template,
        config=config,
        raw_config=raw_config,
    )
    return context.model_copy(update={"survey_contract": contract})


def build_survey_contract(
    *,
    context: ReportContext,
    template: ReportTemplateBundle,
    config: ReportRuntimeConfig,
    raw_config: Mapping[str, object],
) -> dict[str, Any]:
    """Build compact guidance that keeps survey writing aligned across stages."""
    configured_facets = _string_list(raw_config.get("research_required_facets"))
    facets = configured_facets or DEFAULT_SURVEY_FACETS
    topic_terms = _topic_terms(context.topic)
    paper_count = len(context.papers)
    expected_citations = _bounded_int(
        raw_config.get("research_read_min_shortlist"),
        default=min(max(paper_count, 12), 25) if paper_count else 12,
        lower=8,
        upper=30,
    )
    source_budget = _source_budget_for_profile(config.cost_profile)
    figure_expectation = 0
    if config.figures.enabled and config.figures.max_figures > 0:
        figure_expectation = min(config.figures.max_figures, 4)
    return {
        "schema_version": "survey_contract.v1",
        "enabled": True,
        "template": template.name,
        "topic": context.topic,
        "objective": (
            f"Write a reader-oriented academic survey about {context.topic}. "
            "The report should synthesize the literature, organize the field, "
            "compare methods and evidence, and identify open problems."
        ),
        "reader_needs": [
            "Clear scope and terminology for newcomers.",
            "A usable taxonomy for researchers comparing method families.",
            "Construction patterns, applications, and evaluation practice for practitioners.",
            "Honest limitations, evidence gaps, and future directions.",
        ],
        "required_facets": facets[:12],
        "topic_terms": topic_terms[:16],
        "expected_coverage": {
            "available_papers": paper_count,
            "target_citations": expected_citations,
            "target_tables": 4,
            "target_figures": figure_expectation,
        },
        "outline_strategy": config.outline_strategy,
        "cost_profile": config.cost_profile,
        "section_source_budget": source_budget,
        "boundaries": [
            "Use only current-run retrieved papers, paper briefs, synthesis, and verified citations.",
            "Do not use benchmark reference surveys, hidden gold outlines, or external judge outputs as generation input.",
            "Do not describe pipeline stages, artifact paths, prompts, or debug traces in the survey body.",
            "When evidence is thin for a facet, state the limitation instead of inventing coverage.",
        ],
    }


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


def _source_budget_for_profile(profile: str) -> int:
    if profile == "fast":
        return 8
    if profile == "thorough":
        return 24
    return 12


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


def _bounded_int(value: object, *, default: int, lower: int, upper: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        number = default
    return max(lower, min(upper, number))
