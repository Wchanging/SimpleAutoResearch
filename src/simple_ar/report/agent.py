from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

from pydantic import ValidationError

from simple_ar.integrations.llm import LLMClient, LLMError
from simple_ar.report.assembler import assemble_report_sections
from simple_ar.report.schema import (
    AgentReportResult,
    ClaimEvidenceRecord,
    ReportContext,
    ReportIterationRecord,
    ReportMemory,
    ReportRuntimeConfig,
    ReportSectionDraft,
    ReportSectionPlan,
    ReportSectionReview,
    ReportTemplateBundle,
    ReportToolCall,
    ReportToolResult,
    ReviewerFinding,
)
from simple_ar.report.survey import is_survey_report, route_section_sources
from simple_ar.report.tool_gateway import ReportToolGateway


WRITER_SYSTEM = """You are the SimpleAutoResearch report Writer.
Write only evidence-bounded Markdown sections for the current run.
Do not invent citations, metrics, datasets, methods, or external references.
Use short citation keys exactly as provided, in Pandoc-style form like [@P1].
Write survey prose, not a pipeline run log: do not mention artifact paths,
stage names, JSON files, tool traces, or search/debug internals unless the
section is explicitly about limitations.
Keep paragraphs short and focused. Use as many paragraphs as the requested
section target needs; only when no substantive length target is provided,
prefer 2-4 compact paragraphs or a short comparison list instead of one dense
block.
For survey reports, synthesize across papers: build taxonomies, contrast
assumptions, compare evaluation settings, and state boundary conditions.
When many sources are available, use them to improve coverage and confidence;
do not make the report grow linearly by writing one paragraph per paper.
Never write prompt-planning language such as "Hint:", "Use this paper as", or
"Additional synthesis detail is available".
Return one JSON object matching the requested schema."""


REVIEWER_SYSTEM = """You are the SimpleAutoResearch report Reviewer.
Review independently against the provided criteria, source handles, metrics, and current-run boundaries.
Do not rewrite prose unless asked. Return structured findings and optional bounded context requests.
Flag operational/provenance sections in research-only reports when they make
the report read like a pipeline log instead of an academic survey.
Flag any section that is one huge paragraph or mixes many unrelated claims
without paragraph breaks or comparison bullets.
Flag paper-by-paper note dumps, prompt/planning residue, missing taxonomy,
missing cross-paper comparison, and performance claims without boundary
conditions.
Prefer revision instructions that improve synthesis density, evidence coverage,
and section structure over requests to add more paper-by-paper detail.
Return one JSON object matching the requested schema."""


OUTLINE_PLANNER_SYSTEM = """You are the SimpleAutoResearch survey Outline Planner.
Create a topic-specific academic survey outline from the current run evidence.
Do not use external gold outlines, benchmark references, or hidden evaluator
expectations. Keep the outline broad, readable, and evidence-bounded.
Return one JSON object matching the requested schema."""


def run_report_agent(
    *,
    client: LLMClient | None,
    context: ReportContext,
    template: ReportTemplateBundle,
    memory: ReportMemory,
    config: ReportRuntimeConfig,
    gateway: ReportToolGateway,
    emit: Callable[[str], None] | None = None,
) -> AgentReportResult | None:
    """Run the bounded Writer/Reviewer loop for the report stage.

    Args:
        client: Configured LLM client. ``None`` disables agent mode.
        context: Compact report input from earlier stages.
        template: Loaded writing template and reviewer criteria.
        memory: Recoverable report memory.
        config: Runtime report config.
        gateway: Read-only report tool gateway.
        emit: Optional progress callback.

    Returns:
        Agent-generated report body and updated memory, or ``None`` when agent
        mode is disabled or a validated agent pass cannot be produced.
    """
    if client is None or config.agent == "disabled":
        return None
    if not memory.section_plan:
        return None

    current = memory.model_copy(deep=True)
    current = _maybe_adapt_survey_outline(
        client=client,
        context=context,
        template=template,
        memory=current,
        config=config,
        emit=emit,
    )
    sections: list[ReportSectionDraft] = []
    iterations: list[ReportIterationRecord] = []
    all_findings: list[ReviewerFinding] = []
    all_tool_results: list[ReportToolResult] = []

    try:
        for section_index, section in enumerate(_draft_sequence(current.section_plan), start=1):
            source_batches = _source_batches(section.evidence_handles, config)
            first_batch = source_batches[0] if source_batches else section.evidence_handles
            draft_section = _section_with_evidence(section, first_batch)
            _emit(emit, f"Writer drafting `{section.heading}`.")
            draft = _draft_section_with_recovery(
                client=client,
                context=context,
                template=template,
                memory=current,
                section=draft_section,
                config=config,
                extra_context=[],
                label=f"report-writer-{section.section_id}",
                source_batch_index=1,
                source_batch_count=len(source_batches),
                emit=emit,
            )
            iterations.append(_iteration(section_index, section, "draft", draft.status, draft.used_sources))

            if config.source_strategy == "batch_refine" and len(source_batches) > 1:
                for batch_index, batch in enumerate(source_batches[1:], start=2):
                    _emit(
                        emit,
                        (
                            f"Writer integrating source batch {batch_index}/"
                            f"{len(source_batches)} for `{section.heading}`."
                        ),
                    )
                    batch_section = _section_with_evidence(section, batch)
                    revised = _draft_section_with_recovery(
                        client=client,
                        context=context,
                        template=template,
                        memory=current,
                        section=batch_section,
                        config=config,
                        extra_context=[],
                        previous_draft=draft,
                        label=f"report-integrator-{section.section_id}-{batch_index}",
                        source_batch_index=batch_index,
                        source_batch_count=len(source_batches),
                        emit=emit,
                    )
                    draft = _merge_incremental_draft(draft, revised)
                    iterations.append(
                        _iteration(section_index, section, "integrate_sources", draft.status, draft.used_sources)
                    )
                    if config.review_source_batches and config.reviewer != "disabled":
                        _emit(
                            emit,
                            (
                                f"Reviewer checking source batch {batch_index}/"
                                f"{len(source_batches)} for `{section.heading}`."
                            ),
                        )
                        batch_review = _review_section_with_recovery(
                            client=client,
                            context=context,
                            template=template,
                            memory=current,
                            section=batch_section,
                            draft=draft,
                            label=f"report-batch-reviewer-{section.section_id}-{batch_index}",
                            emit=emit,
                        )
                        batch_tool_results = _run_context_requests(gateway, batch_review, config)
                        all_tool_results.extend(batch_tool_results)
                        all_findings.extend(batch_review.findings)
                        iterations.append(
                            _iteration(
                                section_index,
                                section,
                                "review_source_batch",
                                batch_review.verdict,
                                draft.used_sources,
                                findings=batch_review.findings,
                                tool_results=batch_tool_results,
                            )
                        )
                        if _needs_revision(batch_review) and config.max_review_iterations > 0:
                            _emit(emit, f"Writer revising `{section.heading}` from batch reviewer findings.")
                            batch_revised = _draft_section_with_recovery(
                                client=client,
                                context=context,
                                template=template,
                                memory=current,
                                section=batch_section,
                                config=config,
                                extra_context=batch_tool_results,
                                previous_draft=draft,
                                review=batch_review,
                                label=f"report-batch-reviser-{section.section_id}-{batch_index}",
                                source_batch_index=batch_index,
                                source_batch_count=len(source_batches),
                                emit=emit,
                            )
                            draft = _merge_incremental_draft(draft, batch_revised)
                            iterations.append(
                                _iteration(
                                    section_index,
                                    section,
                                    "revise_source_batch",
                                    draft.status,
                                    draft.used_sources,
                                )
                            )

            completed, completion_attempted = _complete_section_if_needed(
                client=client,
                context=context,
                template=template,
                memory=current,
                section=section,
                draft=draft,
                config=config,
                emit=emit,
            )
            if completion_attempted:
                draft = completed
                iterations.append(
                    _iteration(
                        section_index,
                        section,
                        "complete_contract",
                        draft.status,
                        draft.used_sources,
                    )
                )

            if config.reviewer == "disabled":
                sections.append(draft)
                _merge_draft_into_memory(current, draft, [])
                continue

            _emit(emit, f"Reviewer checking `{section.heading}`.")
            review = _review_section_with_recovery(
                client=client,
                context=context,
                template=template,
                memory=current,
                section=section,
                draft=draft,
                label=f"report-reviewer-{section.section_id}",
                emit=emit,
            )
            tool_results = _run_context_requests(gateway, review, config)
            all_tool_results.extend(tool_results)
            all_findings.extend(review.findings)
            iterations.append(
                _iteration(
                    section_index,
                    section,
                    "review",
                    review.verdict,
                    draft.used_sources,
                    findings=review.findings,
                    tool_results=tool_results,
                )
            )

            if _needs_revision(review) and config.max_review_iterations > 0:
                _emit(emit, f"Writer revising `{section.heading}` from reviewer findings.")
                revised = _draft_section_with_recovery(
                    client=client,
                    context=context,
                    template=template,
                    memory=current,
                    section=section,
                    config=config,
                    previous_draft=draft,
                    review=review,
                    extra_context=tool_results,
                    label=f"report-reviser-{section.section_id}",
                    emit=emit,
                )
                draft = _merge_revision_draft(draft, revised)
                iterations.append(_iteration(section_index, section, "revise", draft.status, draft.used_sources))

                if config.max_review_iterations > 1:
                    second_review = _review_section_with_recovery(
                        client=client,
                        context=context,
                        template=template,
                        memory=current,
                        section=section,
                        draft=draft,
                        label=f"report-reviewer-{section.section_id}-revise",
                        emit=emit,
                    )
                    all_findings.extend(second_review.findings)
                    iterations.append(
                        _iteration(
                            section_index,
                            section,
                            "review_revision",
                            second_review.verdict,
                            draft.used_sources,
                            findings=second_review.findings,
                        )
                    )

            sections.append(draft)
            _merge_draft_into_memory(current, draft, review.findings)

        body = assemble_report_sections(title=context.topic, sections=_final_sequence(current.section_plan, sections))
        current.reviewer_findings = _dedupe_findings(current.reviewer_findings + all_findings)
        return AgentReportResult(
            report_body=body,
            memory=current,
            sections=sections,
            iterations=iterations,
            reviewer_findings=all_findings,
            tool_results=all_tool_results,
            used_agent=True,
        )
    except (LLMError, ValidationError, ValueError) as exc:
        _emit(emit, f"Report agent failed validation; using deterministic fallback. {exc}")
        return None


def build_writer_brief(
    *,
    context: ReportContext,
    template: ReportTemplateBundle,
    memory: ReportMemory,
) -> str:
    """Build a compact prompt-side brief for writer/reviewer calls."""
    sections = ", ".join(section.heading for section in memory.section_plan[:8])
    return (
        f"Report mode: {context.report_mode}\n"
        f"Template: {template.name}\n"
        f"Sections: {sections}\n"
        f"Source handles: {len(context.source_handles)}\n"
        f"Metric sources: {len(context.metric_sources)}\n"
        f"Long-form synthesis contract: {'enabled' if memory.survey_contract.get('enabled') else 'disabled'}\n"
    )


def _maybe_adapt_survey_outline(
    *,
    client: LLMClient,
    context: ReportContext,
    template: ReportTemplateBundle,
    memory: ReportMemory,
    config: ReportRuntimeConfig,
    emit: Callable[[str], None] | None,
) -> ReportMemory:
    contract = memory.survey_contract if isinstance(memory.survey_contract, dict) else {}
    if not contract.get("enabled"):
        return memory
    strategy = str(contract.get("outline_strategy") or config.outline_strategy or "auto").lower()
    if strategy == "template":
        return memory
    if not is_survey_report(template_name=template.name, style=config.style, report_mode=context.report_mode):
        return memory
    if len(memory.section_plan) < 3:
        return memory
    errors: list[str] = []
    planned: list[ReportSectionPlan] = []
    for attempt in (1, 2):
        try:
            planned = _plan_topic_specific_outline(
                client=client,
                context=context,
                template=template,
                memory=memory,
                config=config,
                retry=attempt == 2,
            )
            break
        except (LLMError, ValidationError, ValueError, TypeError) as exc:
            errors.append(str(exc))
            if attempt == 1:
                _emit(emit, f"Survey outline planner did not yield a usable plan; retrying once. {exc}")
            else:
                _emit(emit, f"Survey outline planner fallback used; keeping template outline. {exc}")
    if not planned:
        return memory.model_copy(
            update={
                "outline_planning": {
                    "schema_version": "report_outline_planning.v1",
                    "status": "fallback",
                    "strategy": "deterministic_outline_with_full_evidence_budget",
                    "attempts": len(errors),
                    "errors": errors,
                    "section_source_budget": _outline_source_budget(memory.survey_contract, config),
                },
                "key_decisions": memory.key_decisions
                + [
                    "Survey outline planner fallback retained the configured evidence budget for every section."
                ],
            }
        )
    _emit(emit, f"Survey outline planner produced {len(planned)} topic-specific section(s).")
    return memory.model_copy(
        update={
            "section_plan": planned,
            "outline_planning": {
                "schema_version": "report_outline_planning.v1",
                "status": "adapted",
                "strategy": "topic_specific_outline",
                "attempts": len(errors) + 1,
                "errors": errors,
                "section_source_budget": _outline_source_budget(memory.survey_contract, config),
            },
            "key_decisions": memory.key_decisions
            + ["Survey section plan adapted to the topic and current-run evidence before drafting."],
        }
    )


def _plan_topic_specific_outline(
    *,
    client: LLMClient,
    context: ReportContext,
    template: ReportTemplateBundle,
    memory: ReportMemory,
    config: ReportRuntimeConfig,
    retry: bool = False,
) -> list[ReportSectionPlan]:
    response = client.ask_json(
        OUTLINE_PLANNER_SYSTEM,
        _outline_planner_prompt(
            context=context,
            template=template,
            memory=memory,
            config=config,
            retry=retry,
        ),
        label="report-outline-planner-retry" if retry else "report-outline-planner",
    )
    sections = _ensure_survey_outline_coverage(_normalize_outline_sections(response))
    if len(sections) < 5:
        raise ValueError("outline planner returned fewer than 5 usable sections")
    if _outline_is_overly_template_like(sections):
        raise ValueError(
            "outline reused the default structural headings instead of deriving "
            "topic-specific body axes from the selected evidence"
        )
    budget = _outline_source_budget(memory.survey_contract, config)
    planned: list[ReportSectionPlan] = []
    planned_sections = sections[:12]
    total_target_words = _outline_target_words(memory.survey_contract, default=12000)
    default_min_citations = _outline_min_citations(memory.survey_contract, default=3)
    for index, row in enumerate(planned_sections, start=1):
        heading = _clean_outline_heading(row["heading"])
        goal = row["goal"]
        keywords = " ".join(row.get("keywords", []))
        default_target_words = _planned_section_target_words(
            heading=heading,
            index=index,
            total=len(planned_sections),
            target_words=total_target_words,
        )
        target_words = _coerce_int(
            row.get("target_words") or default_target_words,
            default=default_target_words,
            lower=0,
            upper=8000,
        )
        min_citations = _coerce_int(
            row.get("min_citations") or default_min_citations,
            default=default_min_citations,
            lower=0,
            upper=20,
        )
        subsections = _string_items(row.get("subsections"))[:6] or _default_subsections_for_heading(
            heading,
            row.get("keywords", []),
        )
        if not _section_allows_subsections(heading):
            subsections = []
        evidence_handles = route_section_sources(
            context=context,
            heading=heading,
            goal=f"{goal} {keywords}",
            contract=memory.survey_contract,
            budget=budget,
        )
        planned.append(
            ReportSectionPlan(
                section_id=_section_slug(heading) or f"section_{index}",
                heading=heading,
                goal=goal,
                evidence_handles=evidence_handles or _fallback_section_handles(memory, limit=budget),
                target_words=target_words,
                min_citations=min_citations,
                subsections=subsections,
                required=True,
                final_order=index,
                draft_order=_survey_draft_order(heading, index, len(planned_sections)),
            )
        )
    return _dedupe_section_ids(planned)


def _outline_planner_prompt(
    *,
    context: ReportContext,
    template: ReportTemplateBundle,
    memory: ReportMemory,
    config: ReportRuntimeConfig,
    retry: bool = False,
) -> str:
    compact_contract = _compact_survey_contract(memory.survey_contract)
    # Role groups help the Writer balance sources, but exposing them as
    # ``taxonomy_facets`` anchors the outline agent to provenance labels.
    compact_contract["taxonomy_facets"] = []
    # The deterministic contract outline is a coverage fallback, not a gold
    # outline. Showing it to the planner made weaker models copy its generic
    # headings verbatim and still appear to have produced an "adapted" plan.
    compact_contract["outline_sections"] = []
    payload = {
        "task": "plan_topic_specific_survey_outline",
        "topic": context.topic,
        "report_mode": context.report_mode,
        "template": template.name,
        "style": config.style,
        "survey_contract": compact_contract,
        "required_structure": {
            "front_matter": ["Abstract", "Introduction"],
            "back_matter": ["Conclusion"],
            "body_requirement": "Derive 4-7 topic-specific body sections from current-run evidence.",
        },
        "available_source_brief": _outline_source_brief(context),
        "synthesis_excerpt": context.synthesis_markdown[:2500],
        "evidence_summary_excerpt": context.evidence_summary[:2500],
            "planning_rules": [
            "Create 7-10 display sections, including Abstract, Introduction, body sections, and Conclusion.",
            "For long-form surveys, each major body section should include 2-4 planned third-level subsection hints so the final report has a navigable internal structure.",
            "Do not add subsection hints for Abstract, Introduction, or Conclusion; those sections should remain single-section prose.",
            "Body sections must be topic-specific; do not blindly reuse generic headings if the topic suggests better axes.",
            "Do not use the default body headings `Conceptual Foundations and Taxonomy`, `Methods and System Construction`, `Applications and Use Cases`, or `Evaluation, Benchmarks, and Evidence Quality`. Replace them with concrete conceptual axes, method families, task settings, or evaluation regimes evident in the selected literature.",
            "At least three body headings must identify topic-specific concepts from the source brief. A reader should be able to distinguish this outline from an outline for a different research topic without reading the prose.",
            "Treat required facets as coverage checks, not as section titles or taxonomy axes. Derive reader-facing taxonomy and headings from the source brief and selected papers.",
            "Keep SurveyBench-compatible Markdown in mind: final report will use # Title and ## numbered sections.",
            "Use headings that a human survey reader would expect for this topic.",
            "Cover foundations/scope, a taxonomy or method families, system or method construction, evaluation practice, applications or domains when relevant, challenges, and future directions.",
            "For long surveys, keep coverage broad even when headings are topic-specific: include related surveys or adjacent fields when evidence permits, and include future directions separately when it improves reader utility.",
            "If a facet has weak evidence, include it only as a limitation or open problem instead of inventing coverage.",
            "Do not mention SimpleAutoResearch, pipeline stages, artifacts, prompts, or evaluation benchmark internals.",
            "Do not include a References section; references are appended separately.",
            "Return the requested JSON object with a non-empty `sections` list; do not return prose outside JSON.",
        ],
        "output_schema": {
            "sections": [
                {
                    "heading": "Short academic section heading without numbering",
                    "goal": "Reader-facing purpose and synthesis target for this section",
                    "keywords": ["routing keywords for evidence selection"],
                    "target_words": 1200,
                    "min_citations": 3,
                    "subsections": ["optional third-level subsection heading"],
                    "required": True,
                }
            ],
            "notes": "optional planning notes",
        },
    }
    if retry:
        payload["retry_instruction"] = (
            "The previous outline copied the generic structural template instead of organizing the "
            "available evidence. Keep the required report functions, but name the body sections after "
            "concrete concepts, method families, task settings, or evaluation regimes from the source brief. "
            "Return 7-10 valid, reader-facing sections with the required JSON fields."
        )
    return _json_prompt(payload)


_DEFAULT_SURVEY_BODY_HEADINGS = frozenset(
    {
        "conceptual foundations and taxonomy",
        "methods and system construction",
        "applications and use cases",
        "evaluation benchmarks and evidence quality",
        "related surveys and positioning",
        "challenges and future directions",
    }
)


def _outline_is_overly_template_like(sections: list[dict[str, Any]]) -> bool:
    """Detect copied fallback structure without constraining valid survey organization.

    Generic front and back matter are appropriate.  The signal only fires when
    most body headings exactly match the deterministic fallback vocabulary,
    which is evidence that a model copied the contract scaffold rather than
    synthesizing an outline from the current literature.
    """

    body_headings = [
        _clean_outline_heading(str(row.get("heading") or "")).lower()
        for row in sections
        if _clean_outline_heading(str(row.get("heading") or "")).lower()
        not in {"abstract", "introduction", "introduction and scope", "conclusion"}
    ]
    if len(body_headings) < 4:
        return False
    copied = sum(heading in _DEFAULT_SURVEY_BODY_HEADINGS for heading in body_headings)
    return copied >= max(3, (len(body_headings) * 3 + 4) // 5)


def _outline_source_brief(context: ReportContext) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for handle in context.source_handles:
        if handle.kind not in {"paper", "paper_brief"}:
            continue
        rows.append(
            {
                "handle": handle.handle,
                "cite_as": handle.citation_key,
                "title": handle.title[:180],
                "summary": handle.summary[:500],
                "section": handle.section[:120],
                "metadata": _compact_source_metadata(handle.metadata),
            }
        )
        if len(rows) >= 28:
            break
    return rows


def _compact_source_metadata(metadata: dict[str, Any]) -> dict[str, str]:
    keys = ("method", "contribution", "evaluation", "relevance", "venue", "year")
    compact: dict[str, str] = {}
    for key in keys:
        value = metadata.get(key) if isinstance(metadata, dict) else None
        if value not in (None, ""):
            compact[key] = str(value)[:240]
    return compact


def _normalize_outline_sections(response: dict[str, Any]) -> list[dict[str, Any]]:
    raw = response.get("sections")
    if not isinstance(raw, list):
        raise ValueError("outline planner response missing `sections` list")
    rows: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        heading = _clean_outline_heading(str(item.get("heading") or ""))
        if not heading or heading.lower() == "references":
            continue
        goal = str(item.get("goal") or "").strip()
        keywords = item.get("keywords")
        if isinstance(keywords, str):
            keyword_rows = [keywords]
        elif isinstance(keywords, list):
            keyword_rows = [str(keyword).strip() for keyword in keywords if str(keyword).strip()]
        else:
            keyword_rows = []
        rows.append(
            {
                "heading": heading,
                "goal": goal or f"Synthesize evidence relevant to {heading}.",
                "keywords": keyword_rows[:10],
                "target_words": item.get("target_words") or 0,
                "min_citations": item.get("min_citations") or 0,
                "subsections": _string_items(item.get("subsections"))[:6],
            }
        )
    return rows


def _ensure_survey_outline_coverage(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Preserve broad survey coverage while allowing topic-specific headings."""
    if not sections:
        return sections
    rows = list(sections)
    text = " ".join(f"{row.get('heading', '')} {row.get('goal', '')}" for row in rows).lower()
    additions: list[dict[str, Any]] = []
    if not any(term in text for term in ("related survey", "prior survey", "positioning", "adjacent", "neighboring")):
        additions.append(
            {
                "heading": "Related Surveys and Positioning",
                "goal": "Position the topic against prior surveys and neighboring fields, explaining what this synthesis adds and where boundaries remain.",
                "keywords": ["related surveys", "positioning", "adjacent fields", "neighboring areas"],
                "subsections": [
                    "Prior surveys and their scope",
                    "Neighboring fields",
                    "What this synthesis adds",
                ],
            }
        )
    if not any(term in text for term in ("future direction", "future work", "research direction")):
        additions.append(
            {
                "heading": "Future Directions",
                "goal": "State concrete research directions, testable hypotheses, and evidence needed to validate or falsify them.",
                "keywords": ["future directions", "research directions", "open problems", "hypotheses"],
                "subsections": [
                    "Open technical problems",
                    "Evidence needed next",
                    "Research roadmap",
                ],
            }
        )
    if not additions:
        return rows
    insert_at = len(rows)
    for index, row in enumerate(rows):
        if "conclusion" in str(row.get("heading", "")).lower():
            insert_at = index
            break
    return rows[:insert_at] + additions + rows[insert_at:]


def _clean_outline_heading(text: str) -> str:
    heading = text.strip().strip("#").strip()
    heading = " ".join(heading.split())
    heading = re.sub(r"^\d+(?:\.\d+)*\s+", "", heading)
    return heading[:100]


def _outline_source_budget(contract: dict[str, Any], config: ReportRuntimeConfig) -> int:
    raw = contract.get("section_source_budget") if isinstance(contract, dict) else None
    try:
        value = int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        value = 24 if config.cost_profile == "thorough" else 12
    return max(4, min(40, value))


def _outline_target_words(contract: dict[str, Any], *, default: int) -> int:
    expected = contract.get("expected_coverage") if isinstance(contract, dict) else {}
    raw = expected.get("target_words") if isinstance(expected, dict) else None
    return _coerce_int(raw, default=default, lower=1200, upper=50000)


def _outline_min_citations(contract: dict[str, Any], *, default: int) -> int:
    expected = contract.get("expected_coverage") if isinstance(contract, dict) else {}
    raw = expected.get("min_citations_per_section") if isinstance(expected, dict) else None
    return _coerce_int(raw, default=default, lower=0, upper=20)


def _planned_section_target_words(
    *,
    heading: str,
    index: int,
    total: int,
    target_words: int,
) -> int:
    lowered = heading.lower()
    if "abstract" in lowered or "conclusion" in lowered:
        return max(250, min(900, target_words // max(total * 3, 1)))
    body_count = max(1, total - 2)
    body_budget = max(600, target_words - min(1800, target_words // 5))
    return max(600, min(3500, body_budget // body_count))


def _default_subsections_for_heading(heading: str, keywords: object) -> list[str]:
    lowered = heading.lower()
    if not _section_allows_subsections(heading):
        return []
    if "foundation" in lowered or "taxonomy" in lowered:
        return ["Core concepts", "Taxonomy axes", "Interactions between axes"]
    if "method" in lowered or "system" in lowered or "construction" in lowered:
        return ["Common design pattern", "Representative method families", "Trade-offs"]
    if "application" in lowered or "use case" in lowered:
        return ["Task families", "Deployment settings", "Evidence strength"]
    if "evaluation" in lowered or "benchmark" in lowered:
        return ["Evaluation protocols", "Metrics and datasets", "Evidence limitations"]
    if "survey" in lowered or "positioning" in lowered:
        return ["Prior surveys", "Adjacent fields", "Added synthesis"]
    if "challenge" in lowered or "future" in lowered:
        return ["Technical bottlenecks", "Evaluation gaps", "Future research directions"]
    rows = [str(item).replace("_", " ").title() for item in _string_items(keywords)]
    return rows[:3]


def _section_allows_subsections(heading: str) -> bool:
    lowered = heading.lower()
    return not any(term in lowered for term in ("abstract", "introduction", "conclusion"))


def _coerce_int(value: object, *, default: int, lower: int, upper: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        parsed = default
    return max(lower, min(upper, parsed))


def _fallback_section_handles(memory: ReportMemory, *, limit: int) -> list[str]:
    return [
        handle.handle
        for handle in memory.source_handles
        if handle.kind in {"paper", "paper_brief"}
    ][:limit]


def _survey_draft_order(heading: str, final_order: int, total: int) -> int:
    lowered = heading.lower()
    if "abstract" in lowered:
        return total + 20
    if "introduction" in lowered:
        return total + 10
    if "conclusion" in lowered:
        return total + 5
    return final_order


def _section_slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.strip().lower()).strip("_")[:60]


def _dedupe_section_ids(sections: list[ReportSectionPlan]) -> list[ReportSectionPlan]:
    seen: dict[str, int] = {}
    result: list[ReportSectionPlan] = []
    for section in sections:
        base = section.section_id or "section"
        count = seen.get(base, 0) + 1
        seen[base] = count
        section_id = base if count == 1 else f"{base}_{count}"
        result.append(section.model_copy(update={"section_id": section_id}))
    return result


def _draft_section(
    *,
    client: LLMClient,
    context: ReportContext,
    template: ReportTemplateBundle,
    memory: ReportMemory,
    section: ReportSectionPlan,
    config: ReportRuntimeConfig,
    extra_context: list[ReportToolResult],
    label: str,
    previous_draft: ReportSectionDraft | None = None,
    review: ReportSectionReview | None = None,
    source_batch_index: int = 1,
    source_batch_count: int = 1,
    prompt_suffix: str = "",
    include_previous_draft: bool = True,
    draft_mode: str = "section",
) -> ReportSectionDraft:
    prompt = _writer_prompt(
        context=context,
        template=template,
        memory=memory,
        section=section,
        config=config,
        extra_context=extra_context,
        previous_draft=previous_draft,
        review=review,
        source_batch_index=source_batch_index,
        source_batch_count=source_batch_count,
        include_previous_draft=include_previous_draft,
        draft_mode=draft_mode,
    )
    if prompt_suffix:
        prompt = prompt + "\n\n" + prompt_suffix
    response = client.ask_json(
        WRITER_SYSTEM,
        prompt,
        label=label,
    )
    draft = ReportSectionDraft.model_validate(_normalize_draft_response(response, section))
    if not draft.draft_markdown.strip() and draft.status != "skipped":
        keys = ", ".join(sorted(str(key) for key in response)[:12])
        raise LLMError(f"Writer returned empty draft for {section.section_id}; response keys: {keys or '(none)'}")
    return draft


def _complete_section_if_needed(
    *,
    client: LLMClient,
    context: ReportContext,
    template: ReportTemplateBundle,
    memory: ReportMemory,
    section: ReportSectionPlan,
    draft: ReportSectionDraft,
    config: ReportRuntimeConfig,
    emit: Callable[[str], None] | None,
) -> tuple[ReportSectionDraft, bool]:
    """Run one bounded completion pass when a declared section is underfilled.

    Target words are planning constraints rather than a license to pad prose.
    Still, a long-form section that is only a small fraction of its declared
    target is an unmet contract, not a stylistic preference.  This pass keeps
    the original evidence set and requests only missing, non-duplicative prose
    when the deterministic word-count check finds such a gap.
    """
    target_words = int(section.target_words or 0)
    attempts = max(0, int(config.longform.max_completion_revisions or 0))
    if target_words <= 0 or attempts <= 0:
        return draft, False
    required_words = max(180, int(target_words * config.longform.min_section_word_ratio))
    current_words = _draft_word_count(draft)
    if current_words >= required_words:
        return draft, False

    best = draft
    for attempt in range(1, attempts + 1):
        _emit(
            emit,
            (
                f"Writer completing `{section.heading}`: {_draft_word_count(best)}/{required_words} "
                "minimum planned words."
            ),
        )
        candidate = _draft_section_with_recovery(
            client=client,
            context=context,
            template=template,
            memory=memory,
            section=section,
            config=config,
            extra_context=[],
            previous_draft=best,
            label=f"report-completer-{section.section_id}-{attempt}",
            source_batch_index=1,
            source_batch_count=1,
            emit=emit,
            include_previous_draft=False,
            draft_mode="completion",
            prompt_suffix=_completion_prompt(
                section=section,
                current_words=_draft_word_count(best),
                required_words=required_words,
            ),
        )
        if candidate.draft_markdown.strip():
            best = _merge_completion_draft(best, candidate)
        if _draft_word_count(best) >= required_words:
            break
    return best, True


def _completion_prompt(
    *,
    section: ReportSectionPlan,
    current_words: int,
    required_words: int,
) -> str:
    table_guidance = ""
    heading = section.heading.lower()
    if any(term in heading for term in ("taxonomy", "method", "evaluation", "benchmark", "application")):
        table_guidance = (
            " Add one compact evidence-grounded Markdown comparison table when it clarifies "
            "the section; every representative paper in the table must be cited from the allowed set."
        )
    return (
        "This is a contract-completion pass. Return only additional, evidence-bounded Markdown "
        f"paragraphs for the existing section. The existing draft has about {current_words} words, "
        f"while this section requires at least about {required_words} substantive words. Add missing "
        "analysis, comparisons, boundary conditions, and evidence from the complete allowed source "
        "set. Do not repeat, summarize, replace, or reintroduce existing prose; do not add the "
        "section heading; do not pad with generic background; do not invent sources; and do not "
        "mention this completion instruction."
        + table_guidance
    )


def _draft_word_count(draft: ReportSectionDraft) -> int:
    return len(re.findall(r"\b\w+[\w'-]*\b", draft.draft_markdown))


def _review_section(
    *,
    client: LLMClient,
    context: ReportContext,
    template: ReportTemplateBundle,
    memory: ReportMemory,
    section: ReportSectionPlan,
    draft: ReportSectionDraft,
    label: str,
    prompt_suffix: str = "",
) -> ReportSectionReview:
    prompt = _reviewer_prompt(
        context=context,
        template=template,
        memory=memory,
        section=section,
        draft=draft,
    )
    if prompt_suffix:
        prompt = prompt + "\n\n" + prompt_suffix
    response = client.ask_json(
        REVIEWER_SYSTEM,
        prompt,
        label=label,
    )
    return ReportSectionReview.model_validate(_normalize_review_response(response, section))


def _draft_section_with_recovery(
    *,
    client: LLMClient,
    context: ReportContext,
    template: ReportTemplateBundle,
    memory: ReportMemory,
    section: ReportSectionPlan,
    config: ReportRuntimeConfig,
    extra_context: list[ReportToolResult],
    label: str,
    previous_draft: ReportSectionDraft | None = None,
    review: ReportSectionReview | None = None,
    source_batch_index: int = 1,
    source_batch_count: int = 1,
    emit: Callable[[str], None] | None = None,
    prompt_suffix: str = "",
    include_previous_draft: bool = True,
    draft_mode: str = "section",
) -> ReportSectionDraft:
    try:
        return _draft_section(
            client=client,
            context=context,
            template=template,
            memory=memory,
            section=section,
            config=config,
            extra_context=extra_context,
            label=label,
            previous_draft=previous_draft,
            review=review,
            source_batch_index=source_batch_index,
            source_batch_count=source_batch_count,
            prompt_suffix=prompt_suffix,
            include_previous_draft=include_previous_draft,
            draft_mode=draft_mode,
        )
    except (LLMError, ValidationError, ValueError) as exc:
        _emit(emit, f"Writer JSON validation failed for `{section.heading}`; retrying once. {exc}")
    try:
        return _draft_section(
            client=client,
            context=context,
            template=template,
            memory=memory,
            section=section,
            config=config,
            extra_context=extra_context,
            label=f"{label}-retry",
            previous_draft=previous_draft,
            review=review,
            source_batch_index=source_batch_index,
            source_batch_count=source_batch_count,
            prompt_suffix=(prompt_suffix + "\n\n" if prompt_suffix else "") + (
                "The previous response was not accepted as valid JSON. "
                "Return exactly one JSON object matching the requested schema. "
                "`draft_markdown` is mandatory and must contain the complete section prose. "
                "You may leave claims, open_questions, and limitations empty if needed. "
                "Do not include Markdown fences, commentary, or partial prose outside JSON."
            ),
            include_previous_draft=include_previous_draft,
            draft_mode=draft_mode,
        )
    except (LLMError, ValidationError, ValueError) as exc:
        _emit(emit, f"Writer fallback used for `{section.heading}` after retry failed. {exc}")
        return _fallback_section_draft(section)


def _review_section_with_recovery(
    *,
    client: LLMClient,
    context: ReportContext,
    template: ReportTemplateBundle,
    memory: ReportMemory,
    section: ReportSectionPlan,
    draft: ReportSectionDraft,
    label: str,
    emit: Callable[[str], None] | None = None,
) -> ReportSectionReview:
    try:
        return _review_section(
            client=client,
            context=context,
            template=template,
            memory=memory,
            section=section,
            draft=draft,
            label=label,
        )
    except (LLMError, ValidationError, ValueError) as exc:
        _emit(emit, f"Reviewer JSON validation failed for `{section.heading}`; retrying once. {exc}")
    try:
        return _review_section(
            client=client,
            context=context,
            template=template,
            memory=memory,
            section=section,
            draft=draft,
            label=f"{label}-retry",
            prompt_suffix=(
                "The previous response was not accepted as valid JSON. "
                "Return exactly one JSON object matching the reviewer schema, with "
                "`revision_instructions` as a list of strings."
            ),
        )
    except (LLMError, ValidationError, ValueError) as exc:
        _emit(emit, f"Reviewer fallback used for `{section.heading}` after retry failed. {exc}")
        return ReportSectionReview(
            section_id=section.section_id,
            verdict="warning",
            findings=[
                ReviewerFinding(
                    finding_id=f"{section.section_id}-review-fallback",
                    type="review_agent_fallback",
                    severity="minor",
                    message="Reviewer returned invalid structured output; section kept with fallback warning.",
                    section_id=section.section_id,
                    evidence_handles=section.evidence_handles[:5],
                    suggested_action="Manually inspect this section before publishing.",
                )
            ],
            revision_instructions=["Manually inspect this section before publishing."],
            notes="Reviewer fallback used after invalid structured output.",
        )


def _fallback_section_draft(section: ReportSectionPlan) -> ReportSectionDraft:
    handles = [handle for handle in section.evidence_handles if handle][:5]
    citation_text = " ".join(f"[@{handle}]" for handle in handles[:3])
    body = (
        f"## {section.heading}\n\n"
        "This section could not be fully drafted by the report writer after a "
        "structured-output retry. The available evidence should still be "
        "reviewed before publication"
        + (f" ({citation_text})." if citation_text else ".")
        + "\n\n"
        f"Section goal: {section.goal.strip() or 'No section goal was recorded.'}"
    )
    return ReportSectionDraft(
        section_id=section.section_id,
        heading=section.heading,
        status="drafted",
        draft_markdown=body,
        used_sources=handles,
        citations=handles,
        open_questions=[
            "Report writer failed structured-output validation for this section; inspect evidence manually."
        ],
        limitations=[
            "This section is a conservative section-level fallback, not a polished model-written section."
        ],
    )


def _writer_prompt(
    *,
    context: ReportContext,
    template: ReportTemplateBundle,
    memory: ReportMemory,
    section: ReportSectionPlan,
    config: ReportRuntimeConfig,
    extra_context: list[ReportToolResult],
    previous_draft: ReportSectionDraft | None,
    review: ReportSectionReview | None,
    source_batch_index: int,
    source_batch_count: int,
    include_previous_draft: bool,
    draft_mode: str,
) -> str:
    completion_mode = draft_mode == "completion"
    payload = {
        "task": "draft_or_revise_one_report_section",
        "draft_mode": draft_mode,
        "report_mode": context.report_mode,
        "style": config.style,
        "max_section_tokens": config.max_section_tokens,
        "section": section.model_dump(mode="json"),
        "section_constraints": _section_constraints(
            section,
            include_subsections=not completion_mode,
        ),
        "length_requirement": (
            _completion_length_requirement(section)
            if completion_mode
            else _section_length_requirement(section)
        ),
        "source_strategy": {
            "mode": config.source_strategy,
            "batch_index": source_batch_index,
            "batch_count": source_batch_count,
            "instruction": _source_strategy_instruction(
                config=config,
                batch_index=source_batch_index,
                batch_count=source_batch_count,
            ),
        },
        "template_markdown": template.template_markdown,
        "writer_brief": build_writer_brief(context=context, template=template, memory=memory),
        "objective": memory.objective,
        "survey_contract": _compact_survey_contract(memory.survey_contract),
        "global_research_context": {
            "evidence_summary": context.evidence_summary[:3000],
            "synthesis": context.synthesis_markdown[:3000],
            "hypothesis": context.hypothesis_markdown[:1500],
        },
        "limitations": memory.limitations[:8],
        "source_handles": _handles_for_section(memory, section),
        "metric_sources": [metric.model_dump(mode="json") for metric in memory.metric_sources[:12]],
        "claims_evidence_matrix": [
            claim.model_dump(mode="json") for claim in memory.claims_evidence_matrix[:20]
        ],
        "previous_draft": (
            previous_draft.model_dump(mode="json")
            if previous_draft is not None and include_previous_draft
            else {}
        ),
        "review_findings": [finding.model_dump(mode="json") for finding in (review.findings if review else [])],
        "revision_preservation_requirement": _revision_preservation_requirement(
            section=section,
            previous_draft=previous_draft if include_previous_draft else None,
            review=review,
        ),
        "completion_context": (
            _completion_context(previous_draft)
            if completion_mode and previous_draft is not None
            else {}
        ),
        "extra_tool_context": [result.model_dump(mode="json") for result in extra_context[:6]],
        "style_rules": [
            "Write as a long-form academic synthesis for the user topic, not as documentation of the SimpleAutoResearch pipeline.",
            "Do not include artifact names, file paths, JSON filenames, stage numbers, or command provenance in the body.",
            "Do not create sections named Search Scope, Evidence Summary, Pipeline, Artifacts, or Stage Outputs.",
            "Do not use prompt-planning phrases such as Hint:, Use this paper as, Paper Brief, or Additional synthesis detail.",
            "Do not write a paper-by-paper literature note dump; group papers by taxonomy and comparison dimensions.",
            "Use the available source set broadly, but compress by grouping similar papers and citing representative evidence.",
            "For long survey templates, optimize for topic coverage and reader needs: explain foundations, construction patterns, applications, evaluation practice, related surveys, challenges, and future directions.",
            "When the long-form synthesis contract is enabled, satisfy its reader_needs, required_facets, expected coverage, outline_sections, visual_plan, and boundaries before adding optional details.",
            "Use selected_paper_brief and taxonomy_facets as evidence seeds; derive a topic-specific organizing argument rather than copying checklist labels into the prose.",
            "When outline_sections specify target_words and citation_keys, treat them as section planning constraints and explain any evidence gap conservatively.",
            "When section_constraints specify target_words, min_citations, or subsections, treat them as local writing constraints for this section.",
            "When length_requirement is present, write enough focused prose to meet it; do not stop after a few compact paragraphs merely because the section is well structured.",
            "When revision_preservation_requirement is present, return a complete revised section that preserves the valid analytical coverage and approximate length of the previous draft.",
            "If subsections are listed, use them as meaningful `###` subheadings unless the section is Abstract, Introduction, or Conclusion.",
            "For long survey templates, use meaningful subheadings inside large sections when they improve navigation.",
            "For long survey templates, include compact comparison tables aligned with visual_plan when useful, but do not add Markdown image links unless a real generated image artifact exists.",
            "When source_strategy.mode is batch_refine and previous_draft is present, update the existing section instead of appending a separate mini-section.",
            "For incremental source batches, revise taxonomy tables, evidence maps, and contrasts to absorb new papers compactly.",
            "For Method Families, include a compact taxonomy table or grouped comparison before prose.",
            "For Evaluation, include an evidence-quality map when enough source metadata is available.",
            "For Evaluation, distinguish benchmark/task scale and state whether evidence transfers to repository-level tasks.",
            "Draft front-matter as if it is written after the body: Abstract and Introduction should summarize the actual synthesis, not generic background.",
            "For each strong conclusion, add a boundary condition or uncertainty statement.",
            "Keep paragraphs under roughly 120 words; split dense synthesis into short paragraphs or concise bullets.",
            "Use only `cite_as` values such as [@P1] for body citations; never cite long source handles or raw paper ids.",
            "The final renderer will map short citation keys back to verified source ids and numeric citations.",
            "`draft_markdown` is the required primary payload. Put the complete Markdown prose there and never substitute `content`, `body`, or an explanation outside the JSON object.",
        ],
        "output_schema": {
            "section_id": section.section_id,
            "heading": section.heading,
            "status": "drafted|revised|skipped",
            "draft_markdown": "REQUIRED non-empty Markdown body for this section only; do not include References",
            "used_sources": ["source handle ids used"],
            "metric_ids": ["metric ids used"],
            "citations": ["short citation keys cited, such as P1"],
            "claims": [
                {
                    "claim_id": "stable id",
                    "claim": "claim text",
                    "status": "supported|partially_supported|unsupported|speculative",
                    "evidence_handles": [],
                    "metric_ids": [],
                    "citation_ids": ["short citation keys, such as P1"],
                    "notes": "",
                }
            ],
            "open_questions": [],
            "limitations": [],
        },
    }
    if completion_mode:
        payload["style_rules"].extend(
            [
                "This is a continuation pass, not a complete section draft. Return only new body paragraphs that extend the existing section.",
                "Do not emit Markdown headings at any level, a recap, or another version of an existing subsection.",
                "Use completion_context.existing_subheadings only to avoid repeating already-covered themes; do not restate the list in the prose.",
            ]
        )
    return _json_prompt(payload)


def _reviewer_prompt(
    *,
    context: ReportContext,
    template: ReportTemplateBundle,
    memory: ReportMemory,
    section: ReportSectionPlan,
    draft: ReportSectionDraft,
) -> str:
    payload = {
        "task": "review_one_report_section",
        "report_mode": context.report_mode,
        "section": section.model_dump(mode="json"),
        "section_constraints": _section_constraints(section),
        "criteria_markdown": template.criteria_markdown,
        "objective": memory.objective,
        "survey_contract": _compact_survey_contract(memory.survey_contract),
        "known_limitations": memory.limitations[:8],
        "allowed_sources": _handles_for_section(memory, section),
        "metric_sources": [metric.model_dump(mode="json") for metric in memory.metric_sources[:12]],
        "draft": draft.model_dump(mode="json"),
        "tool_policy": {
            "allowed_tools": [
                "get_paper_brief",
                "get_neighbor_chunks",
                "get_metric_source",
                "get_synthesis_brief",
                "get_code_task_result",
            ],
            "only_request_tools_when_evidence_is_insufficient": True,
            "prefer_get_paper_brief_arguments": {"citation_key": "P1"},
        },
        "review_focus": [
            "Does this section read like a survey section rather than a pipeline log?",
            "Are citations adjacent to paper-specific claims?",
            "If section_constraints include target_words, min_citations, or subsections, did the draft reasonably satisfy them without padding or unsupported citations?",
            "If planned subsections are present for a body section, does the draft use clear internal `###` headings or equivalent navigational structure? Do not require this for Abstract, Introduction, or Conclusion.",
            "Are long paragraphs split into readable units?",
            "Are operational details moved out of the body unless they are reader-facing limitations?",
            "Does the section synthesize across papers instead of listing paper briefs?",
            "Does it contain taxonomy/comparison dimensions when discussing methods?",
            "Does it use the selected source set broadly without becoming a paper-by-paper dump?",
            "For long survey templates, does the section improve topic coverage for reader needs rather than merely restating a compact technical brief?",
            "If the long-form synthesis contract is enabled, does the section cover the relevant outline_sections, required facets, citation policy, and reader needs without drifting into an experiment report or pipeline log?",
            "For long survey templates, are construction, applications, evaluation, related surveys, challenges, and future directions covered across the report plan?",
            "For long survey templates, are tables and figure-ready diagram specs used where the visual_plan indicates they clarify taxonomy, benchmarks, or workflows?",
            "Does Evaluation include an evidence-quality map or equivalent compact comparison when useful?",
            "Are benchmark limitations and transfer boundaries stated near empirical claims?",
            "Is it free of prompt residue such as Hint, Use this paper as, Paper Brief, or Additional synthesis detail?",
        ],
        "output_schema": {
            "section_id": section.section_id,
            "verdict": "pass|warning|revise_required|fail",
            "findings": [
                {
                    "finding_id": "stable id",
                    "type": "unsupported_claim|citation_misuse|missing_limitation|metric_mismatch|style",
                    "severity": "info|minor|major|critical",
                    "message": "specific issue",
                    "section_id": section.section_id,
                    "claim_id": "",
                    "evidence_handles": [],
                    "suggested_action": "",
                }
            ],
            "context_requests": [
                {
                    "tool_name": "get_paper_brief",
                    "arguments": {"handle": "paper:..."},
                    "caller": "reviewer",
                    "trace_id": "optional",
                }
            ],
            "revision_instructions": [],
            "notes": "",
        },
    }
    return _json_prompt(payload)


def _json_prompt(payload: dict[str, Any]) -> str:
    return (
        "Return exactly one JSON object. Do not wrap it in Markdown fences.\n\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
    )


def _compact_survey_contract(contract: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(contract, dict) or not contract.get("enabled"):
        return {}
    expected = contract.get("expected_coverage") if isinstance(contract.get("expected_coverage"), dict) else {}
    selection = contract.get("paper_selection") if isinstance(contract.get("paper_selection"), dict) else {}
    selected_papers = selection.get("selected_papers") if isinstance(selection.get("selected_papers"), list) else []
    taxonomy = contract.get("taxonomy") if isinstance(contract.get("taxonomy"), dict) else {}
    outline = contract.get("outline_plan") if isinstance(contract.get("outline_plan"), dict) else {}
    visual = contract.get("visual_plan") if isinstance(contract.get("visual_plan"), dict) else {}
    return {
        "schema_version": contract.get("schema_version", "survey_contract.v1"),
        "topic": contract.get("topic", ""),
        "objective": contract.get("objective", ""),
        "reader_needs": _string_items(contract.get("reader_needs"))[:6],
        "required_facets": _string_items(contract.get("required_facets"))[:10],
        "expected_coverage": expected,
        "selected_paper_brief": [
            {
                "cite_as": item.get("citation_key") or item.get("paper_id") or "",
                "title": str(item.get("title") or "")[:160],
                "role": item.get("role") or "",
                "facets": _string_items(item.get("facets"))[:4],
            }
            for item in selected_papers[:20]
            if isinstance(item, dict)
        ],
        "taxonomy_facets": [
            {
                "label": facet.get("label") or "",
                "paper_keys": _string_items(facet.get("paper_keys"))[:8],
                "evidence_count": facet.get("evidence_count") or 0,
            }
            for facet in (taxonomy.get("facets") if isinstance(taxonomy.get("facets"), list) else [])[:10]
            if isinstance(facet, dict)
        ],
        "coverage_requirements": [
            {
                "label": facet.get("label") or "",
                "evidence_count": facet.get("evidence_count") or 0,
            }
            for facet in (taxonomy.get("coverage_facets") if isinstance(taxonomy.get("coverage_facets"), list) else [])[:10]
            if isinstance(facet, dict)
        ],
        "outline_sections": [
            {
                "heading": section.get("heading") or "",
                "goal": section.get("goal") or "",
                "target_words": section.get("target_words") or "",
                "min_citations": section.get("min_citations") or "",
                "subsections": _string_items(section.get("subsections"))[:6],
                "citation_keys": _string_items(section.get("citation_keys"))[:10],
            }
            for section in (outline.get("sections") if isinstance(outline.get("sections"), list) else [])[:12]
            if isinstance(section, dict)
        ],
        "visual_plan": {
            "tables": [
                {"title": row.get("title") or "", "purpose": row.get("purpose") or ""}
                for row in (visual.get("tables") if isinstance(visual.get("tables"), list) else [])[:6]
                if isinstance(row, dict)
            ],
            "figures": [
                {"title": row.get("title") or "", "items": _string_items(row.get("items"))[:8]}
                for row in (visual.get("figures") if isinstance(visual.get("figures"), list) else [])[:4]
                if isinstance(row, dict)
            ],
        },
        "citation_policy": contract.get("citation_policy") if isinstance(contract.get("citation_policy"), dict) else {},
        "boundaries": _string_items(contract.get("boundaries"))[:6],
    }


def _string_items(value: object) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, dict):
        for key in (
            "text",
            "question",
            "limitation",
            "description",
            "message",
            "content",
            "body",
            "label",
            "title",
            "name",
            "notes",
        ):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return [candidate.strip()]
        return []
    if isinstance(value, list):
        items: list[str] = []
        for item in value:
            items.extend(_string_items(item))
        return items
    return []


def _normalize_draft_response(response: dict[str, Any], section: ReportSectionPlan) -> dict[str, Any]:
    normalized = dict(response)
    nested = normalized.get("draft")
    if isinstance(nested, dict):
        for key, value in nested.items():
            normalized.setdefault(key, value)
    nested_section = normalized.get("section")
    if isinstance(nested_section, dict):
        for key, value in nested_section.items():
            normalized.setdefault(key, value)
    if not str(normalized.get("draft_markdown") or "").strip():
        for key in ("markdown", "content", "body", "text", "section_markdown"):
            value = normalized.get(key)
            if isinstance(value, str) and value.strip():
                normalized["draft_markdown"] = value
                break
    normalized.setdefault("section_id", section.section_id)
    normalized.setdefault("heading", section.heading)
    normalized.setdefault("status", "drafted")
    normalized.setdefault("draft_markdown", "")
    normalized.setdefault("used_sources", [])
    normalized.setdefault("metric_ids", [])
    normalized.setdefault("citations", [])
    normalized.setdefault("claims", [])
    normalized.setdefault("open_questions", [])
    normalized.setdefault("limitations", [])
    normalized["open_questions"] = _string_items(normalized["open_questions"])
    normalized["limitations"] = _string_items(normalized["limitations"])
    # Some models copy a claim-level evidence label such as ``supported`` into
    # the section-level status field.  The two enums intentionally differ:
    # preserve the draft and its claim statuses while normalizing the section
    # lifecycle state to the writer contract.
    if str(normalized.get("status") or "").strip().lower() not in {"drafted", "revised", "skipped"}:
        normalized["status"] = "drafted"
    return normalized


def _normalize_review_response(response: dict[str, Any], section: ReportSectionPlan) -> dict[str, Any]:
    normalized = dict(response)
    normalized.setdefault("section_id", section.section_id)
    normalized.setdefault("verdict", "warning")
    normalized.setdefault("findings", [])
    normalized.setdefault("context_requests", [])
    normalized.setdefault("revision_instructions", [])
    normalized.setdefault("notes", "")
    normalized["findings"] = [
        _normalize_finding(finding, section, index)
        for index, finding in enumerate(normalized.get("findings") or [], start=1)
        if isinstance(finding, dict)
    ]
    normalized["context_requests"] = [
        _normalize_tool_call(call, index)
        for index, call in enumerate(normalized.get("context_requests") or [], start=1)
        if isinstance(call, dict)
    ]
    return normalized


def _normalize_finding(
    finding: dict[str, Any],
    section: ReportSectionPlan,
    index: int,
) -> dict[str, Any]:
    item = dict(finding)
    item.setdefault("finding_id", f"{section.section_id}-finding-{index:03d}")
    item.setdefault("type", "review")
    item.setdefault("severity", "minor")
    item.setdefault("message", item.get("suggested_action") or "Reviewer finding.")
    item.setdefault("section_id", section.section_id)
    item.setdefault("claim_id", "")
    item.setdefault("evidence_handles", [])
    item.setdefault("suggested_action", "")
    return item


def _normalize_tool_call(call: dict[str, Any], index: int) -> dict[str, Any]:
    item = dict(call)
    item.setdefault("tool_name", "")
    item.setdefault("arguments", {})
    item.setdefault("caller", "reviewer")
    item.setdefault("trace_id", f"review-tool-{index:03d}")
    return item


def _run_context_requests(
    gateway: ReportToolGateway,
    review: ReportSectionReview,
    config: ReportRuntimeConfig,
) -> list[ReportToolResult]:
    if not config.allow_source_backtracking:
        return []
    results: list[ReportToolResult] = []
    for call in review.context_requests[: config.max_backtracking_calls]:
        if not call.tool_name:
            continue
        results.append(gateway.call(call))
    return results


def _handles_for_section(memory: ReportMemory, section: ReportSectionPlan) -> list[dict[str, Any]]:
    handles = {handle.handle: handle for handle in memory.source_handles}
    selected: list[str] = []
    for handle in section.evidence_handles:
        if handle in handles:
            selected.append(handle)
    if not selected:
        selected = list(handles)[:8]
    return [_prompt_handle_view(handles[handle]) for handle in selected]


def _source_batches(evidence_handles: list[str], config: ReportRuntimeConfig) -> list[list[str]]:
    handles = [handle for handle in evidence_handles if handle]
    if not handles:
        return [[]]
    if config.source_strategy != "batch_refine":
        return [handles]
    batch_size = max(1, config.source_batch_size)
    batches = [handles[index : index + batch_size] for index in range(0, len(handles), batch_size)]
    if config.max_source_batches > 0:
        return batches[: config.max_source_batches]
    return batches


def _section_with_evidence(section: ReportSectionPlan, evidence_handles: list[str]) -> ReportSectionPlan:
    return section.model_copy(update={"evidence_handles": list(evidence_handles)})


def _merge_incremental_draft(
    previous: ReportSectionDraft,
    revised: ReportSectionDraft,
) -> ReportSectionDraft:
    """Keep accumulated provenance without allowing batch integration to erase prose."""
    used_sources = _stable_union(previous.used_sources, revised.used_sources)
    metric_ids = _stable_union(previous.metric_ids, revised.metric_ids)
    citations = _stable_union(previous.citations, revised.citations)
    open_questions = _stable_union(previous.open_questions, revised.open_questions)
    limitations = _stable_union(previous.limitations, revised.limitations)
    claims = previous.claims[:]
    claim_ids = {claim.claim_id for claim in claims}
    for claim in revised.claims:
        if claim.claim_id in claim_ids:
            continue
        claims.append(claim)
        claim_ids.add(claim.claim_id)
    # Batch refinement should extend a draft.  Models occasionally respond to
    # a later evidence batch with a short summary, which used to replace a
    # richer preceding section wholesale.  Preserve the longer body in that
    # case; the bounded completion pass can then integrate the full evidence
    # set coherently.
    previous_words = _draft_word_count(previous)
    revised_words = _draft_word_count(revised)
    base = revised if revised_words >= max(120, int(previous_words * 0.8)) else previous
    return base.model_copy(
        update={
            "used_sources": used_sources,
            "metric_ids": metric_ids,
            "citations": citations,
            "claims": claims,
            "open_questions": open_questions,
            "limitations": limitations,
        }
    )


def _merge_revision_draft(
    previous: ReportSectionDraft,
    revised: ReportSectionDraft,
) -> ReportSectionDraft:
    """Accept reviewer rewrites only when they preserve the prior draft's scale."""
    merged = _merge_incremental_draft(previous, revised)
    previous_words = _draft_word_count(previous)
    revised_words = _draft_word_count(revised)
    if previous_words <= 0 or revised_words >= max(120, int(previous_words * 0.9)):
        return merged.model_copy(update={"draft_markdown": revised.draft_markdown})
    return merged.model_copy(update={"draft_markdown": previous.draft_markdown})


def _merge_completion_draft(
    previous: ReportSectionDraft,
    completion: ReportSectionDraft,
) -> ReportSectionDraft:
    """Merge a completion response without allowing a short rewrite to erase prose."""
    merged = _merge_incremental_draft(previous, completion)
    prior_body = previous.draft_markdown.strip()
    added_body = _strip_completion_headings(completion.draft_markdown)
    if not prior_body:
        return merged
    if _completion_looks_like_replacement(prior_body, added_body):
        if _draft_word_count(completion) >= _draft_word_count(previous):
            return merged.model_copy(update={"draft_markdown": completion.draft_markdown})
        return merged.model_copy(update={"draft_markdown": previous.draft_markdown})
    return merged.model_copy(update={"draft_markdown": f"{prior_body}\n\n{added_body}"})


def _strip_completion_headings(markdown: str) -> str:
    """Keep continuation passes from mutating a section's planned hierarchy.

    A completion is deliberately merged after an existing section.  A writer
    occasionally adds fresh Markdown headings despite the continuation prompt,
    which makes the final document look like two competing outlines.  Heading
    labels add no prose evidence here, so remove them while retaining the
    accompanying paragraphs or tables.
    """
    lines = markdown.strip().splitlines()
    return "\n".join(line for line in lines if not re.match(r"^\s{0,3}#{1,6}\s+", line)).strip()


def _completion_looks_like_replacement(previous: str, candidate: str) -> bool:
    """Identify an accidental full-section rewrite with a cheap deterministic check."""
    normalized_previous = re.sub(r"\s+", " ", previous).strip().lower()
    normalized_candidate = re.sub(r"\s+", " ", candidate).strip().lower()
    if not normalized_candidate:
        return True
    if normalized_candidate in normalized_previous or normalized_previous in normalized_candidate:
        return True
    prefix = normalized_previous[:240]
    return bool(prefix and normalized_candidate.startswith(prefix))


def _stable_union(first: list[str], second: list[str]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for item in first + second:
        if not item or item in seen:
            continue
        merged.append(item)
        seen.add(item)
    return merged


def _source_strategy_instruction(
    *,
    config: ReportRuntimeConfig,
    batch_index: int,
    batch_count: int,
) -> str:
    if config.source_strategy != "batch_refine":
        return "Draft using the provided source handles as the section evidence set."
    if batch_index <= 1:
        return (
            "Draft an initial section from the first source batch. Leave the "
            "structure easy to revise when later source batches arrive."
        )
    return (
        "Revise the previous draft to integrate this new source batch. Update "
        "tables, comparisons, and evidence-quality judgments compactly; do not "
        "append a separate list of new papers."
    )


def _section_constraints(
    section: ReportSectionPlan,
    *,
    include_subsections: bool = True,
) -> dict[str, Any]:
    constraints: dict[str, Any] = {
        "target_words": section.target_words if section.target_words > 0 else "",
        "min_citations": section.min_citations if section.min_citations > 0 else "",
        "subsections": section.subsections[:6] if include_subsections else [],
    }
    guidance: list[str] = []
    if section.target_words > 0:
        guidance.append(
            "Aim for this approximate section length, but prefer evidence-bounded prose over padding."
        )
    if section.min_citations > 0:
        guidance.append(
            "Use at least this many distinct allowed citations when the evidence set supports it."
        )
    if section.subsections and include_subsections:
        guidance.append(
            "Use the subsection list as navigational `###` headings or close equivalents."
        )
    constraints["guidance"] = guidance
    return constraints


def _section_length_requirement(section: ReportSectionPlan) -> str:
    target_words = max(0, int(section.target_words or 0))
    if target_words <= 0:
        return ""
    return (
        f"Draft approximately {target_words} substantive words for this section. "
        "Use multiple focused paragraphs and subsections when useful; concise prose does not "
        "mean stopping before the requested analytical coverage is complete."
    )


def _completion_length_requirement(section: ReportSectionPlan) -> str:
    target_words = max(0, int(section.target_words or 0))
    if target_words <= 0:
        return ""
    return (
        "Extend the existing section toward its approximate "
        f"{target_words}-word target with only missing, evidence-bounded prose. "
        "Do not rewrite the section or recreate its internal outline."
    )


def _completion_context(previous_draft: ReportSectionDraft) -> dict[str, Any]:
    headings = [
        match.group(1).strip()
        for match in re.finditer(r"(?m)^\s{0,3}#{2,6}\s+(.+?)\s*$", previous_draft.draft_markdown)
    ]
    return {
        "existing_subheadings": headings[:16],
        "existing_word_count": _draft_word_count(previous_draft),
    }


def _revision_preservation_requirement(
    *,
    section: ReportSectionPlan,
    previous_draft: ReportSectionDraft | None,
    review: ReportSectionReview | None,
) -> str:
    if previous_draft is None or review is None:
        return ""
    prior_words = _draft_word_count(previous_draft)
    if prior_words <= 0:
        return ""
    minimum_words = max(120, int(prior_words * 0.9))
    return (
        "This is a reviewer-directed revision, not a fresh summary. Return the complete "
        f"section with at least about {minimum_words} substantive words, preserving valid prior "
        "analysis and citations while addressing the review findings."
    )


def _draft_sequence(sections: list[ReportSectionPlan]) -> list[ReportSectionPlan]:
    return sorted(
        sections,
        key=lambda section: (
            section.draft_order if section.draft_order else section.final_order,
            section.final_order,
            section.section_id,
        ),
    )


def _final_sequence(
    plans: list[ReportSectionPlan],
    drafts: list[ReportSectionDraft],
) -> list[ReportSectionDraft]:
    order = {plan.section_id: plan.final_order or index for index, plan in enumerate(plans, start=1)}
    return sorted(drafts, key=lambda draft: (order.get(draft.section_id, 9999), draft.section_id))


def _prompt_handle_view(handle: Any) -> dict[str, Any]:
    """Return a compact model-facing handle with short citation guidance.

    The raw handle is retained for source provenance and chunk backtracking, but
    prose citations should use ``cite_as``. This keeps long provider ids out of
    normal body citation generation.
    """
    data = handle.model_dump(mode="json")
    citation_key = data.get("citation_key") or ""
    if citation_key:
        data["cite_as"] = f"[@{citation_key}]"
        data["paper_id_for_display"] = citation_key
        data.pop("paper_id", None)
        data["tool_args"] = {"citation_key": citation_key}
    return data


def _needs_revision(review: ReportSectionReview) -> bool:
    if review.verdict in {"revise_required", "fail"}:
        return True
    return any(finding.severity in {"major", "critical"} for finding in review.findings)


def _merge_draft_into_memory(
    memory: ReportMemory,
    draft: ReportSectionDraft,
    findings: list[ReviewerFinding],
) -> None:
    existing_claim_ids = {claim.claim_id for claim in memory.claims_evidence_matrix}
    for claim in draft.claims:
        if claim.claim_id not in existing_claim_ids:
            memory.claims_evidence_matrix.append(claim)
            existing_claim_ids.add(claim.claim_id)
    memory.reviewer_findings = _dedupe_findings(memory.reviewer_findings + findings)
    for item in draft.open_questions:
        if item and item not in memory.open_questions:
            memory.open_questions.append(item)
    for item in draft.limitations:
        if item and item not in memory.limitations:
            memory.limitations.append(item)
    decision = f"Section `{draft.section_id}` drafted with {len(draft.used_sources)} source handle(s)."
    if decision not in memory.key_decisions:
        memory.key_decisions.append(decision)


def _dedupe_findings(findings: list[ReviewerFinding]) -> list[ReviewerFinding]:
    seen: set[tuple[str, str, str]] = set()
    deduped: list[ReviewerFinding] = []
    for finding in findings:
        key = (finding.type, finding.section_id, finding.message)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(finding)
    return deduped


def _iteration(
    iteration: int,
    section: ReportSectionPlan,
    action: str,
    status: str,
    used_sources: list[str],
    *,
    findings: list[ReviewerFinding] | None = None,
    tool_results: list[ReportToolResult] | None = None,
) -> ReportIterationRecord:
    return ReportIterationRecord(
        iteration=iteration,
        section_id=section.section_id,
        action=action,
        status=status,
        summary=f"{action} `{section.heading}` -> {status}",
        used_sources=used_sources[:8],
        findings=findings or [],
        tool_results=tool_results or [],
    )


def _emit(callback: Callable[[str], None] | None, message: str) -> None:
    if callback is not None:
        callback(message)
