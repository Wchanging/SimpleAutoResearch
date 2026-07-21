from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from simple_ar.core.artifacts import write_json, write_text
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
    if not config.survey_contract or not config.longform.enabled:
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
    paper_selection = _build_paper_selection(
        context=context,
        facets=facets,
        raw_config=raw_config,
        config=config,
    )
    taxonomy = _build_taxonomy(
        topic=context.topic,
        coverage_facets=facets,
        selected_papers=paper_selection["selected_papers"],
    )
    expected_citations = _bounded_int(
        config.longform.target_papers or raw_config.get("research_read_min_shortlist"),
        default=min(max(paper_count, 12), 30) if paper_count else 12,
        lower=8,
        upper=60,
    )
    target_words = _bounded_int(
        config.longform.target_words or None,
        default=12000 if template.name == "survey_long" else 4500,
        lower=1200,
        upper=50000,
    )
    source_budget = _source_budget_for_profile(config.cost_profile)
    figure_expectation = 0
    if config.figures.enabled and config.figures.max_figures > 0:
        figure_expectation = min(config.figures.max_figures, 4)
    table_expectation = _bounded_int(
        config.longform.target_tables or None,
        default=4 if template.name == "survey_long" else 1,
        lower=0,
        upper=12,
    )
    min_citations = _bounded_int(
        config.longform.min_citations_per_section or None,
        default=3,
        lower=1,
        upper=12,
    )
    outline_plan = _build_outline_plan(
        topic=context.topic,
        template_name=template.name,
        target_words=target_words,
        taxonomy=taxonomy,
        selected_papers=paper_selection["selected_papers"],
        min_citations_per_section=min_citations,
    )
    visual_plan = _build_visual_plan(
        taxonomy=taxonomy,
        target_figures=figure_expectation,
        target_tables=table_expectation,
    )
    return {
        "schema_version": "survey_contract.v3",
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
            "target_papers": paper_selection["target_papers"],
            "selected_papers": len(paper_selection["selected_papers"]),
            "min_papers": paper_selection["min_papers"],
            "target_words": target_words,
            "min_citations_per_section": min_citations,
            "target_tables": table_expectation,
            "target_figures": figure_expectation,
        },
        "paper_selection": paper_selection,
        "taxonomy": taxonomy,
        "outline_plan": outline_plan,
        "visual_plan": visual_plan,
        "citation_policy": {
            "use_short_keys": True,
            "cite_claims_adjacent_to_text": True,
            "prefer_cross_paper_synthesis": True,
            "avoid_paper_by_paper_dump": True,
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


def write_survey_planning_artifacts(
    *,
    report_dir: Path,
    context: ReportContext,
    contract: dict[str, Any],
    report_body: str,
    final_report: str,
    enabled: bool,
    evidence_audit: bool = True,
) -> None:
    """Persist long-form planning and coverage artifacts when active.

    The artifacts are diagnostic and reusable. They do not gate report
    generation by default, which keeps ordinary reports compatible while making
    long-form synthesis behavior inspectable.
    """

    if not enabled or not isinstance(contract, dict) or not contract.get("enabled"):
        return
    survey_dir = report_dir / "longform"
    paper_selection = contract.get("paper_selection") if isinstance(contract.get("paper_selection"), dict) else {}
    taxonomy = contract.get("taxonomy") if isinstance(contract.get("taxonomy"), dict) else {}
    outline_plan = contract.get("outline_plan") if isinstance(contract.get("outline_plan"), dict) else {}
    visual_plan = contract.get("visual_plan") if isinstance(contract.get("visual_plan"), dict) else {}
    citation_audit = (
        _build_citation_coverage_audit(
            report_body=report_body,
            final_report=final_report,
            context=context,
            contract=contract,
        )
        if evidence_audit
        else {"schema_version": "survey_citation_coverage_audit.v1", "status": "disabled"}
    )
    write_json(survey_dir / "longform_contract.json", contract)
    write_json(survey_dir / "paper_selection.json", paper_selection)
    write_json(survey_dir / "taxonomy.json", taxonomy)
    write_json(survey_dir / "outline_plan.json", outline_plan)
    write_json(survey_dir / "visual_plan.json", visual_plan)
    if evidence_audit:
        write_json(survey_dir / "citation_coverage_audit.json", citation_audit)
    write_text(
        survey_dir / "longform_plan.md",
        _render_survey_plan_markdown(
            contract=contract,
            paper_selection=paper_selection,
            taxonomy=taxonomy,
            outline_plan=outline_plan,
            visual_plan=visual_plan,
            citation_audit=citation_audit,
        ),
    )


def _build_paper_selection(
    *,
    context: ReportContext,
    facets: Sequence[str],
    raw_config: Mapping[str, object],
    config: ReportRuntimeConfig,
) -> dict[str, Any]:
    candidates = _candidate_papers(context)
    configured_target = config.longform.target_papers or raw_config.get("research_read_min_shortlist")
    target = _bounded_int(
        configured_target,
        default=min(max(len(candidates), 12), 30) if candidates else 12,
        lower=1,
        upper=80,
    )
    min_papers = (
        _bounded_int(
            config.longform.min_papers,
            default=0,
            lower=0,
            upper=target,
        )
        if config.longform.min_papers > 0
        else 0
    )
    keywords = set(_topic_terms(context.topic))
    for facet in facets:
        keywords.update(_topic_terms(facet.replace("_", " ")))
    scored: list[tuple[float, int, dict[str, Any]]] = []
    for index, paper in enumerate(candidates):
        text = _paper_text(paper)
        score = float(_keyword_score(text.lower(), keywords))
        if paper.get("kind") == "paper_brief":
            score += 2.0
        if paper.get("abstract"):
            score += 1.0
        if _is_review_like(text):
            score += 0.5
        role = _paper_role(text)
        if role in {"method", "evaluation", "application", "related_survey"}:
            score += 0.5
        paper["role"] = role
        paper["facets"] = _paper_facets(text, facets)
        paper["selection_score"] = round(score, 3)
        paper["selection_reason"] = _selection_reason(paper)
        scored.append((score, -index, paper))
    scored.sort(reverse=True)
    selected = [paper for _score, _index, paper in scored[:target]]
    return {
        "schema_version": "survey_paper_selection.v1",
        "available_candidates": len(candidates),
        "target_papers": target,
        "min_papers": min_papers,
        "selected_papers": selected,
        "needs_more_evidence": len(selected) < min_papers,
        "warnings": (
            [f"Only {len(selected)} selected paper(s), below configured minimum {min_papers}."]
            if len(selected) < min_papers
            else []
        ),
    }


def _candidate_papers(context: ReportContext) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for handle in _dedupe_paperish_handles(context.source_handles):
        if handle.kind not in {"paper", "paper_brief"}:
            continue
        key = handle.citation_key or handle.paper_id or handle.handle
        title_key = _normalize_key(handle.title or key)
        dedupe_key = handle.paper_id or title_key or key
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        metadata = handle.metadata if isinstance(handle.metadata, dict) else {}
        candidates.append(
            {
                "citation_key": handle.citation_key,
                "paper_id": handle.paper_id,
                "handle": handle.handle,
                "kind": handle.kind,
                "title": handle.title,
                "abstract": handle.summary,
                "year": _metadata_year(metadata),
                "venue": str(metadata.get("venue") or metadata.get("source") or "")[:120],
                "source": str(metadata.get("source") or "")[:80],
            }
        )
    return candidates


def _build_taxonomy(
    *,
    topic: str,
    coverage_facets: Sequence[str],
    selected_papers: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Keep coverage requirements distinct from evidence-derived organization.

    Configured research facets answer "what must be checked".  They are often
    operational labels such as ``datasets_benchmarks_and_evaluation`` and are
    not necessarily defensible conceptual axes for a reader-facing taxonomy.
    The report planner receives both views: a deterministic coverage checklist
    and lightweight evidence-role seeds from the selected literature.
    """
    coverage_rows: list[dict[str, Any]] = []
    for index, facet in enumerate(coverage_facets[:10], start=1):
        paper_keys = [
            str(paper.get("citation_key") or paper.get("paper_id") or paper.get("handle") or "")
            for paper in selected_papers
            if _facet_matches_paper(facet, paper)
        ]
        coverage_rows.append(
            {
                "facet_id": f"coverage_{index:02d}",
                "label": _human_label(facet),
                "paper_keys": [key for key in paper_keys if key][:12],
                "evidence_count": len([key for key in paper_keys if key]),
            }
        )

    groups: list[dict[str, Any]] = []
    if selected_papers:
        role_groups = _role_groups(selected_papers)
        for label, keys in role_groups.items():
            if not keys:
                continue
            groups.append(
                {
                    "facet_id": f"role_{_normalize_key(label)}",
                    "label": _human_label(label),
                    "keywords": _topic_terms(label.replace("_", " "))[:8],
                    "paper_keys": keys[:12],
                    "evidence_count": len(keys),
                }
            )
    groups = _dedupe_taxonomy_groups(groups)
    return {
        "schema_version": "survey_taxonomy.v2",
        "topic": topic,
        "facets": groups[:12],
        "coverage_facets": coverage_rows,
        "taxonomy_note": (
            "Taxonomy seeds are derived from roles in current-run selected papers; "
            "the outline planner must derive reader-facing conceptual axes from source evidence."
        ),
        "coverage_note": (
            "Coverage facets are a deterministic checklist for evidence collection and audit. "
            "They are not a prescribed report outline or taxonomy."
        ),
    }


def _build_outline_plan(
    *,
    topic: str,
    template_name: str,
    target_words: int,
    taxonomy: Mapping[str, Any],
    selected_papers: Sequence[Mapping[str, Any]],
    min_citations_per_section: int,
) -> dict[str, Any]:
    # Evidence roles are useful for routing and audit, but they are not a
    # reader-facing taxonomy.  Keeping the deterministic fallback neutral
    # avoids headings such as "Method Evidence as a Taxonomy Axis" when a
    # topic-specific outline call cannot be used.
    facet_labels: list[str] = []
    selected_keys = [
        str(paper.get("citation_key") or paper.get("paper_id") or paper.get("handle") or "")
        for paper in selected_papers
    ]
    selected_keys = [key for key in selected_keys if key]
    section_specs = _default_outline_specs(topic, facet_labels, template_name)
    weights = [weight for _heading, _goal, weight, _keywords in section_specs]
    total_weight = sum(weights) or 1
    sections: list[dict[str, Any]] = []
    for index, (heading, goal, weight, keywords) in enumerate(section_specs, start=1):
        target = max(120, int(target_words * weight / total_weight))
        citation_keys = _section_citation_keys(
            heading=heading,
            keywords=keywords,
            taxonomy=taxonomy,
            selected_papers=selected_papers,
            fallback_keys=selected_keys,
            minimum=min_citations_per_section,
        )
        subsections = _section_subsections(
            heading=heading,
            topic=topic,
            keywords=keywords,
            facet_labels=facet_labels,
            long_form=template_name == "survey_long",
        )
        sections.append(
            {
                "section_id": _normalize_key(heading) or f"section_{index}",
                "heading": heading,
                "goal": goal,
                "keywords": keywords[:10],
                "target_words": target,
                "min_citations": min_citations_per_section,
                "subsections": subsections,
                "citation_keys": citation_keys,
                "required": True,
            }
        )
    return {
        "schema_version": "survey_outline_plan.v1",
        "target_words": target_words,
        "sections": sections,
    }


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


def _build_visual_plan(
    *,
    taxonomy: Mapping[str, Any],
    target_figures: int,
    target_tables: int,
) -> dict[str, Any]:
    facets = taxonomy.get("facets") if isinstance(taxonomy.get("facets"), list) else []
    facet_labels = [
        str(facet.get("label") or "").strip()
        for facet in facets
        if isinstance(facet, Mapping) and str(facet.get("label") or "").strip()
    ][:8]
    tables = [
        {
            "table_id": "taxonomy-comparison",
            "title": "Taxonomy and Representative Evidence",
            "purpose": "Compare method families or conceptual facets with representative cited papers.",
            "suggested_columns": ["Facet", "Core idea", "Representative papers", "Evidence boundary"],
        },
        {
            "table_id": "evaluation-landscape",
            "title": "Evaluation Settings and Metrics",
            "purpose": "Summarize datasets, tasks, metrics, and reproducibility limitations when evidence permits.",
            "suggested_columns": ["Setting", "Task or dataset", "Metric", "Observed limitation"],
        },
        {
            "table_id": "challenge-map",
            "title": "Challenges and Future Directions",
            "purpose": "Connect open challenges with evidence gaps and potential research directions.",
            "suggested_columns": ["Challenge", "Current evidence", "Risk", "Future direction"],
        },
    ][: max(0, target_tables)]
    figures = [
        {
            "figure_id": "taxonomy-map",
            "title": "Survey Taxonomy Map",
            "items": facet_labels[:8],
        },
        {
            "figure_id": "system-construction-flow",
            "title": "System Construction Flow",
            "items": facet_labels[:8],
        },
        {
            "figure_id": "challenge-roadmap",
            "title": "Challenge and Future Roadmap",
            "items": facet_labels[:8],
        },
    ][: max(0, target_figures)]
    return {
        "schema_version": "survey_visual_plan.v1",
        "tables": tables,
        "figures": figures,
        "note": "Visuals are derived from the current-run taxonomy and report text; no benchmark reference survey is used.",
    }


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


def _build_citation_coverage_audit(
    *,
    report_body: str,
    final_report: str,
    context: ReportContext,
    contract: Mapping[str, Any],
) -> dict[str, Any]:
    selection = contract.get("paper_selection") if isinstance(contract.get("paper_selection"), Mapping) else {}
    selected = selection.get("selected_papers") if isinstance(selection.get("selected_papers"), list) else []
    selected_ids = {
        str(paper.get("paper_id") or "").strip()
        for paper in selected
        if isinstance(paper, Mapping) and str(paper.get("paper_id") or "").strip()
    }
    selected_keys = {
        str(paper.get("citation_key") or "").strip()
        for paper in selected
        if isinstance(paper, Mapping) and str(paper.get("citation_key") or "").strip()
    }
    cited_ids = set(re.findall(r"@([A-Za-z0-9_.:-]+)", report_body))
    cited_keys = set(re.findall(r"@([A-Za-z0-9_.:-]+)", final_report))
    sections = _markdown_sections_for_audit(report_body)
    per_section = []
    for heading, body in sections:
        citations = sorted(set(re.findall(r"@([A-Za-z0-9_.:-]+)", body)))
        per_section.append({"heading": heading, "citation_count": len(citations), "citations": citations[:20]})
    table_count = _markdown_table_count(final_report)
    figure_count = len(re.findall(r"!\[[^\]]*\]\([^)]+\)", final_report))
    target = contract.get("expected_coverage") if isinstance(contract.get("expected_coverage"), Mapping) else {}
    warnings: list[str] = []
    min_papers = _safe_int(target.get("min_papers"), 0)
    if min_papers and len(selected) < min_papers:
        warnings.append(f"Selected paper count {len(selected)} is below target minimum {min_papers}.")
    min_citations = _safe_int(target.get("min_citations_per_section"), 0)
    sparse = [
        row["heading"]
        for row in per_section
        if row["heading"].lower() not in {"abstract", "conclusion"} and row["citation_count"] < min_citations
    ]
    if sparse:
        warnings.append("Some body sections have fewer citations than the configured target.")
    return {
        "schema_version": "survey_citation_coverage_audit.v1",
        "selected_paper_count": len(selected),
        "selected_citation_keys": sorted(selected_keys),
        "selected_paper_ids": sorted(selected_ids),
        "cited_paper_ids": sorted(cited_ids),
        "cited_short_keys_in_final_report": sorted(cited_keys),
        "selected_paper_ids_cited": sorted(selected_ids & cited_ids),
        "unused_selected_paper_ids": sorted(selected_ids - cited_ids),
        "body_citation_count": len(cited_ids),
        "section_citation_coverage": per_section,
        "table_count": table_count,
        "figure_count": figure_count,
        "warnings": warnings,
        "status": "warning" if warnings else "passed",
        "source_handle_count": len(context.source_handles),
    }


def _render_survey_plan_markdown(
    *,
    contract: Mapping[str, Any],
    paper_selection: Mapping[str, Any],
    taxonomy: Mapping[str, Any],
    outline_plan: Mapping[str, Any],
    visual_plan: Mapping[str, Any],
    citation_audit: Mapping[str, Any],
) -> str:
    lines = [
        "# Survey Planning Artifacts",
        "",
        f"- Topic: {contract.get('topic', '')}",
        f"- Selected papers: {paper_selection.get('selected_papers') and len(paper_selection.get('selected_papers', [])) or 0} / target {paper_selection.get('target_papers', '')}",
        f"- Citation audit status: {citation_audit.get('status', 'unknown')}",
        "",
        "## Taxonomy",
        "",
        "| Facet | Evidence Count | Representative Keys |",
        "| --- | ---: | --- |",
    ]
    taxonomy_rows = taxonomy.get("facets") if isinstance(taxonomy.get("facets"), list) else []
    for facet in taxonomy_rows:
        if not isinstance(facet, Mapping):
            continue
        lines.append(
            "| "
            + _md_cell(str(facet.get("label") or ""))
            + " | "
            + str(facet.get("evidence_count") or 0)
            + " | "
            + _md_cell(", ".join(_string_list(facet.get("paper_keys"))[:8]))
            + " |"
        )
    lines.extend(
        [
            "",
            "## Outline Plan",
            "",
            "| Section | Target Words | Min Citations | Subsections | Citation Keys |",
            "| --- | ---: | ---: | --- | --- |",
        ]
    )
    outline_rows = outline_plan.get("sections") if isinstance(outline_plan.get("sections"), list) else []
    for section in outline_rows:
        if not isinstance(section, Mapping):
            continue
        lines.append(
            "| "
            + _md_cell(str(section.get("heading") or ""))
            + " | "
            + str(section.get("target_words") or "")
            + " | "
            + str(section.get("min_citations") or "")
            + " | "
            + _md_cell(", ".join(_string_list(section.get("subsections"))[:6]))
            + " | "
            + _md_cell(", ".join(_string_list(section.get("citation_keys"))[:10]))
            + " |"
        )
    lines.extend(["", "## Visual Plan", ""])
    for key in ("tables", "figures"):
        rows = visual_plan.get(key) if isinstance(visual_plan.get(key), list) else []
        lines.append(f"### {key.title()}")
        lines.append("")
        for row in rows:
            if isinstance(row, Mapping):
                lines.append(f"- {row.get('title') or row.get('figure_id') or row.get('table_id')}")
        lines.append("")
    if citation_audit.get("warnings"):
        lines.extend(["## Warnings", ""])
        for warning in _string_list(citation_audit.get("warnings")):
            lines.append(f"- {warning}")
    return "\n".join(lines).rstrip() + "\n"


def _paper_text(paper: Mapping[str, Any]) -> str:
    return " ".join(
        str(paper.get(key) or "")
        for key in ("title", "abstract", "venue", "source", "role")
    )


def _metadata_year(metadata: Mapping[str, Any]) -> str:
    for key in ("year", "published", "publication_year"):
        value = metadata.get(key)
        if value in (None, ""):
            continue
        match = re.search(r"(19|20)\d{2}", str(value))
        if match:
            return match.group(0)
    return ""


def _is_review_like(text: str) -> bool:
    lowered = text.lower()
    return any(term in lowered for term in ("survey", "review", "overview", "taxonomy", "systematic"))


def _paper_role(text: str) -> str:
    lowered = text.lower()
    if _is_review_like(lowered):
        return "related_survey"
    if any(term in lowered for term in ("benchmark", "evaluation", "dataset", "metric", "leaderboard")):
        return "evaluation"
    if any(term in lowered for term in ("application", "domain", "medical", "education", "recommendation", "robot")):
        return "application"
    if any(term in lowered for term in ("architecture", "framework", "method", "algorithm", "model", "training")):
        return "method"
    if any(term in lowered for term in ("theory", "foundation", "principle", "analysis")):
        return "foundation"
    return "evidence"


def _paper_facets(text: str, facets: Sequence[str]) -> list[str]:
    lowered = text.lower()
    matched: list[str] = []
    for facet in facets:
        label = facet.replace("_", " ")
        terms = _topic_terms(label)
        if any(term in lowered for term in terms):
            matched.append(facet)
    if not matched:
        role = _paper_role(text)
        if role:
            matched.append(role)
    return matched[:5]


def _selection_reason(paper: Mapping[str, Any]) -> str:
    role = str(paper.get("role") or "evidence")
    facets = ", ".join(_string_list(paper.get("facets"))[:3])
    if facets:
        return f"Selected as {role} evidence for {facets}."
    return f"Selected as {role} evidence for the survey topic."


def _normalize_key(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(text or "").lower()).strip("_")[:64]


def _human_label(value: str) -> str:
    text = str(value or "").replace("_", " ").replace("-", " ").strip()
    if not text:
        return ""
    return " ".join(word.capitalize() if not word.isupper() else word for word in text.split())


def _facet_matches_paper(facet: str, paper: Mapping[str, Any]) -> bool:
    facets = _string_list(paper.get("facets"))
    if facet in facets:
        return True
    text = _paper_text(paper).lower()
    return any(term in text for term in _topic_terms(facet.replace("_", " ")))


def _role_groups(selected_papers: Sequence[Mapping[str, Any]]) -> dict[str, list[str]]:
    groups = {
        "method_evidence": [],
        "evaluation_evidence": [],
        "application_evidence": [],
        "related_surveys": [],
        "foundational_evidence": [],
    }
    for paper in selected_papers:
        key = str(paper.get("citation_key") or paper.get("paper_id") or paper.get("handle") or "")
        if not key:
            continue
        role = str(paper.get("role") or "")
        if role == "method":
            groups["method_evidence"].append(key)
        elif role == "evaluation":
            groups["evaluation_evidence"].append(key)
        elif role == "application":
            groups["application_evidence"].append(key)
        elif role == "related_survey":
            groups["related_surveys"].append(key)
        elif role == "foundation":
            groups["foundational_evidence"].append(key)
    return groups


def _dedupe_taxonomy_groups(groups: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    output: list[dict[str, Any]] = []
    for group in groups:
        label = str(group.get("label") or "").strip()
        key = _normalize_key(label)
        if not label or key in seen:
            continue
        seen.add(key)
        output.append(dict(group))
    output.sort(key=lambda row: int(row.get("evidence_count") or 0), reverse=True)
    return output


def _default_outline_specs(
    topic: str,
    facet_labels: Sequence[str],
    template_name: str,
) -> list[tuple[str, str, int, list[str]]]:
    topic_text = topic or "the topic"
    core_facets = ", ".join(facet_labels[:4]) or "major method families and evidence dimensions"
    long = template_name == "survey_long"
    specs: list[tuple[str, str, int, list[str]]] = [
        (
            "Abstract",
            f"Summarize the survey scope, organizing taxonomy, evidence base, and open challenges for {topic_text}.",
            4,
            ["scope", "taxonomy", "evidence", "challenge"],
        ),
        (
            "Introduction and Scope",
            f"Define {topic_text}, reader needs, boundaries, and why the topic matters.",
            8,
            ["introduction", "scope", "motivation", "boundary"],
        ),
        (
            "Conceptual Foundations and Taxonomy",
            f"Explain core concepts and organize the field around {core_facets}.",
            13 if long else 10,
            ["foundation", "taxonomy", *facet_labels[:4]],
        ),
        (
            "Methods and System Construction",
            "Compare construction patterns, modules, assumptions, and implementation tradeoffs.",
            16 if long else 12,
            ["method", "system", "architecture", "construction", *facet_labels[:5]],
        ),
        (
            "Applications and Use Cases",
            "Map tasks and domains to method choices, evidence, advantages, and limitations.",
            11 if long else 8,
            ["application", "domain", "task", "use case"],
        ),
        (
            "Evaluation, Benchmarks, and Evidence Quality",
            "Compare datasets, metrics, protocols, baselines, reproducibility, and evidence strength.",
            14 if long else 11,
            ["evaluation", "benchmark", "dataset", "metric", "protocol"],
        ),
        (
            "Related Surveys and Positioning",
            "Position the survey against prior surveys and adjacent areas while clarifying what is newly synthesized.",
            8,
            ["survey", "review", "positioning", "adjacent"],
        ),
        (
            "Challenges and Future Directions",
            "Synthesize unresolved technical, empirical, and deployment challenges with evidence-grounded directions.",
            14 if long else 10,
            ["challenge", "limitation", "future", "direction"],
        ),
        (
            "Conclusion",
            "State the field-level takeaways and remaining uncertainty without adding new evidence.",
            4,
            ["conclusion", "takeaway"],
        ),
    ]
    return specs


def _section_subsections(
    *,
    heading: str,
    topic: str,
    keywords: Sequence[str],
    facet_labels: Sequence[str],
    long_form: bool,
) -> list[str]:
    """Return reader-facing subsection hints for long evidence synthesis.

    These are not benchmark labels. They are compact navigation aids derived
    from the section role and the current taxonomy, so a writer can expand a
    long report without flattening it into a few broad blocks.
    """

    if not long_form:
        return []
    lowered = heading.lower()
    topic_label = topic.strip() or "the topic"
    facets = [label for label in facet_labels if label][:5]
    if "abstract" in lowered or "introduction" in lowered or "conclusion" in lowered:
        return []
    if "foundation" in lowered or "taxonomy" in lowered:
        rows = ["Core terminology and assumptions"]
        rows.extend(f"{facet} as a taxonomy axis" for facet in facets[:3])
        rows.append("How the axes interact")
        return _dedupe_strings(rows)[:5]
    if "method" in lowered or "construction" in lowered or "system" in lowered:
        rows = ["Common pipeline or system pattern"]
        rows.extend(f"{facet} design choices" for facet in facets[:3])
        rows.append("Trade-offs and failure modes")
        return _dedupe_strings(rows)[:5]
    if "application" in lowered or "use case" in lowered:
        return [
            "Representative task families",
            "Deployment settings and constraints",
            "Where evidence is strongest",
            "Where transfer remains uncertain",
        ]
    if "evaluation" in lowered or "benchmark" in lowered:
        return [
            "Datasets, benchmarks, and protocols",
            "Metrics and comparison baselines",
            "Reproducibility and evidence quality",
            "Limitations of current evaluation",
        ]
    if "related survey" in lowered or "positioning" in lowered:
        return [
            "Prior surveys and their scope",
            f"What this synthesis adds for {topic_label}",
            "Adjacent fields and boundary cases",
        ]
    if "challenge" in lowered or "future" in lowered:
        return [
            "Technical bottlenecks",
            "Empirical and evaluation gaps",
            "Deployment and governance risks",
            "Actionable research directions",
        ]
    fallback = [keyword.replace("_", " ").title() for keyword in keywords if keyword]
    return _dedupe_strings(fallback)[:4]


def _section_citation_keys(
    *,
    heading: str,
    keywords: Sequence[str],
    taxonomy: Mapping[str, Any],
    selected_papers: Sequence[Mapping[str, Any]],
    fallback_keys: Sequence[str],
    minimum: int,
) -> list[str]:
    lowered = " ".join([heading, *keywords]).lower()
    selected: list[str] = []
    facets = taxonomy.get("facets") if isinstance(taxonomy.get("facets"), list) else []
    for facet in facets:
        if not isinstance(facet, Mapping):
            continue
        label = str(facet.get("label") or "")
        facet_terms = set(_topic_terms(label))
        facet_terms.update(_string_list(facet.get("keywords")))
        if not any(term.lower() in lowered for term in facet_terms):
            continue
        for key in _string_list(facet.get("paper_keys")):
            if key not in selected:
                selected.append(key)
            if len(selected) >= max(1, minimum):
                break
    if len(selected) < minimum:
        for paper in selected_papers:
            text = _paper_text(paper).lower()
            if not any(keyword.lower() in text for keyword in keywords):
                continue
            key = str(paper.get("citation_key") or paper.get("paper_id") or paper.get("handle") or "")
            if key and key not in selected:
                selected.append(key)
            if len(selected) >= minimum:
                break
    if len(selected) < minimum:
        for key in fallback_keys:
            if key not in selected:
                selected.append(key)
            if len(selected) >= minimum:
                break
    return selected[:12]


def _markdown_sections_for_audit(markdown: str) -> list[tuple[str, str]]:
    sections: list[tuple[str, str]] = []
    current = ""
    body: list[str] = []
    for line in markdown.splitlines():
        match = re.match(r"^##\s+(.+?)\s*$", line)
        if match:
            if current:
                sections.append((_strip_section_number(current), "\n".join(body)))
            current = match.group(1).strip()
            body = []
        elif current:
            body.append(line)
    if current:
        sections.append((_strip_section_number(current), "\n".join(body)))
    return sections


def _strip_section_number(text: str) -> str:
    return re.sub(r"^\d+(?:\.\d+)*\s+", "", text).strip()


def _markdown_table_count(markdown: str) -> int:
    count = 0
    previous = ""
    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped.startswith("|") and re.match(r"^\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?$", stripped):
            if previous.strip().startswith("|"):
                count += 1
        previous = line
    return count


def _safe_int(value: object, default: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _md_cell(text: str) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ").strip()


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
