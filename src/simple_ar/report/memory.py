from __future__ import annotations

import re
from pathlib import Path

from simple_ar.core.artifacts import write_json
from simple_ar.report.schema import (
    ClaimEvidenceRecord,
    ReportContext,
    ReportMemory,
    ReportSectionPlan,
    ReportTemplateBundle,
)
from simple_ar.report.survey import enrich_survey_sections


SECTION_PATTERN = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
DRAFT_ORDER_PATTERN = re.compile(r"(?im)^draft\s+order\s*:\s*(.+?)\s*$")
NON_DRAFT_SECTION_NAMES = {
    "intended use",
    "output expectations",
    "review goal",
    "required checks",
    "writing workflow",
    "writing order",
    "generation strategy",
    "references",
}


def initialize_report_memory(
    *,
    context: ReportContext,
    template: ReportTemplateBundle,
) -> ReportMemory:
    """Create compact report memory from template and stage context."""
    sections = enrich_survey_sections(
        _section_plan(template.template_markdown, context),
        context=context,
    )
    claims = _initial_claims(context)
    limitations = _initial_limitations(context)
    return ReportMemory(
        objective=context.problem_markdown.strip()[:1200] or context.topic,
        template=template.name,
        report_mode=context.report_mode,
        survey_contract=context.survey_contract,
        section_plan=sections,
        claims_evidence_matrix=claims,
        source_handles=context.source_handles,
        metric_sources=context.metric_sources,
        limitations=limitations,
        key_decisions=[
            "Report generation must use only current-run evidence, metrics, and paper ids.",
            "Unsupported claims should be weakened or moved to limitations/future work.",
        ],
    )


def write_report_memory(path: Path, memory: ReportMemory) -> None:
    """Persist report memory as compact JSON."""
    write_json(path, memory.model_dump(mode="json"))


def _section_plan(template_markdown: str, context: ReportContext) -> list[ReportSectionPlan]:
    headings = [
        heading
        for heading in (match.group(1).strip() for match in SECTION_PATTERN.finditer(template_markdown))
        if heading.strip().lower() not in NON_DRAFT_SECTION_NAMES
    ]
    if not headings:
        headings = _fallback_headings(context.report_mode)
    evidence_handles = _section_evidence_handles(context)
    draft_order_map = _template_draft_order(template_markdown, headings)
    sections: list[ReportSectionPlan] = []
    for index, heading in enumerate(headings, start=1):
        section_id = _slug(heading) or f"section_{index}"
        goal = _section_goal(heading, context.report_mode)
        draft_order = draft_order_map.get(_heading_key(heading), index)
        sections.append(
            ReportSectionPlan(
                section_id=section_id,
                heading=heading,
                goal=goal,
                evidence_handles=evidence_handles,
                final_order=index,
                draft_order=draft_order,
            )
        )
    return sections


def _section_evidence_handles(context: ReportContext) -> list[str]:
    """Choose a bounded source set for each writer call.

    Paper handles are prioritized because they carry citation keys and concise
    metadata. Extra non-paper handles fill remaining slots only when the paper
    set is smaller than the configured section-source budget.
    """
    experiment_handles = [
        handle.handle for handle in context.source_handles if handle.kind == "experiment"
    ]
    paper_handles = [handle.handle for handle in context.source_handles if handle.kind == "paper"]
    if context.max_section_sources <= 0:
        if context.report_mode == "experiment":
            return [*experiment_handles, *paper_handles] or [
                handle.handle for handle in context.source_handles if handle.kind != "chunk"
            ]
        if paper_handles:
            return paper_handles
        return [handle.handle for handle in context.source_handles if handle.kind != "chunk"]

    budget = max(1, context.max_section_sources)
    selected = experiment_handles[:budget] if context.report_mode == "experiment" else []
    selected.extend(path for path in paper_handles if path not in selected)
    selected = selected[:budget]
    if len(selected) >= budget:
        return selected
    selected_set = set(selected)
    for handle in context.source_handles:
        if handle.handle in selected_set:
            continue
        selected.append(handle.handle)
        selected_set.add(handle.handle)
        if len(selected) >= budget:
            break
    return selected


def _initial_claims(context: ReportContext) -> list[ClaimEvidenceRecord]:
    claims: list[ClaimEvidenceRecord] = []
    for metric in context.metric_sources[:8]:
        claims.append(
            ClaimEvidenceRecord(
                claim_id=f"claim:{_slug(metric.metric_id)}",
                claim=f"Report may discuss metric `{metric.name}` only with value from `{metric.artifact}`.",
                status="supported",
                metric_ids=[metric.metric_id],
                notes="Auto-created metric provenance guard.",
            )
        )
    for handle in context.source_handles[:8]:
        if handle.kind in {"paper", "paper_brief", "chunk"}:
            claims.append(
                ClaimEvidenceRecord(
                    claim_id=f"claim:{handle.handle}",
                    claim=f"Report may cite `{handle.title or handle.handle}` for claims supported by this handle.",
                    status="partially_supported",
                    evidence_handles=[handle.handle],
                    citation_ids=[handle.paper_id] if handle.paper_id else [],
                    notes="Auto-created source provenance guard.",
                )
            )
    return claims


def _initial_limitations(context: ReportContext) -> list[str]:
    limitations: list[str] = []
    if not context.papers:
        limitations.append("No paper metadata was available for citation-backed claims.")
    if context.report_mode == "research_only" and not context.results:
        limitations.append("No experiment results are available; empirical claims must be avoided.")
    if context.search_meta.get("source") == "fixture" or "fixture" in str(context.search_meta.get("status", "")):
        limitations.append("Literature evidence came from fixture/fallback metadata.")
    guard = context.results.get("guard") if isinstance(context.results, dict) else {}
    if isinstance(guard, dict) and guard.get("status") in {"warning", "failed"}:
        limitations.append(
            f"Experiment result guard status is `{guard.get('status')}`; claims must be qualified."
        )
    code_review = context.results.get("code_review") if isinstance(context.results, dict) else {}
    if isinstance(code_review, dict) and code_review.get("status") in {"warning", "failed"}:
        limitations.append(
            f"Generated code review status is `{code_review.get('status')}`; implementation risk must be disclosed."
        )
    recovery = (
        context.results.get("review_failure_recovery")
        if isinstance(context.results, dict)
        else {}
    )
    if isinstance(recovery, dict) and recovery:
        limitations.append(
            "Initial generated code failed review and was replaced by a bounded fallback; "
            "treat implementation claims as recovered rather than fully autonomous."
        )
    return limitations


def _fallback_headings(report_mode: str) -> list[str]:
    if report_mode == "experiment":
        return [
            "Abstract",
            "Problem And Motivation",
            "Related Work",
            "Method / Change Summary",
            "Experimental Setup",
            "Results",
            "Discussion",
            "Limitations",
        ]
    return [
        "Abstract",
        "Introduction And Scope",
        "Method Families",
        "Evaluation And Benchmarks",
        "Design Patterns And Failure Modes",
        "Research Gaps And Opportunities",
        "Limitations",
        "Conclusion",
    ]


def _section_goal(heading: str, report_mode: str) -> str:
    lowered = heading.lower()
    if "result" in lowered or "experiment" in lowered:
        return "Use only recorded metrics and experiment artifacts; avoid unstaged performance claims."
    if "related" in lowered or "evidence" in lowered or "method" in lowered or "benchmark" in lowered:
        return "Ground prose in paper ids, briefs, chunks, and synthesis evidence."
    if "limitation" in lowered:
        return "State evidence gaps and runtime boundaries clearly."
    if report_mode == "research_only":
        return "Summarize literature evidence without implying experiment execution."
    return "Write this section with traceable evidence and conservative claims."


def _template_draft_order(template_markdown: str, headings: list[str]) -> dict[str, int]:
    """Parse an optional ``Draft order: A -> B`` directive from the template.

    Writing order belongs to the template because different report forms should
    be free to draft sections differently. Unknown names are ignored and
    undeclared headings keep their final display order.
    """
    match = DRAFT_ORDER_PATTERN.search(template_markdown)
    if not match:
        return {}
    heading_positions = {_heading_key(heading): index for index, heading in enumerate(headings, start=1)}
    requested = [
        _heading_key(item)
        for item in re.split(r"\s*(?:->|,|\|)\s*", match.group(1).strip())
        if item.strip()
    ]
    draft_order: dict[str, int] = {}
    order = 1
    for key in requested:
        if key not in heading_positions or key in draft_order:
            continue
        draft_order[key] = order
        order += 1
    for key, final_order in heading_positions.items():
        draft_order.setdefault(key, len(headings) + final_order)
    return draft_order


def _heading_key(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", text.strip().lower()).strip("_")
    return slug[:60]
