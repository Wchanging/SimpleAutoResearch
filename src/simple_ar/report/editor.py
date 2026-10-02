"""Bounded cross-section review using the existing report drafts and evidence."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from simple_ar.integrations.llm import LLMResponseError
from simple_ar.research.contracts import CLAIM_SCOPE_RULES
from simple_ar.report.tools import report_tool_specs
from simple_ar.report.schema import (
    ReportMemory,
    ReportRuntimeConfig,
    ReportSectionDraft,
    ReportSectionReview,
    ReportTemplateBundle,
    ReportToolResult,
    finding_requires_resolution,
    REVIEW_ACTION_RULES,
)


MAX_DOCUMENT_REVIEW_CHARS = 60_000
MAX_DOCUMENT_REVIEW_PROMPT_CHARS = 90_000
MAX_DOCUMENT_REVIEW_SECTIONS = 2


def review_document(
    *, client: Any, template: ReportTemplateBundle, memory: ReportMemory,
    sections: list[ReportSectionDraft], config: ReportRuntimeConfig,
    execution_summary: Mapping[str, Any], metric_summary: Mapping[str, Any],
    source_evidence: list[dict[str, Any]] | None = None,
    execution_evidence: Mapping[str, Any] | None = None,
    supplementary_evidence: list[ReportToolResult] | None = None,
    label: str = "report-document-reviewer",
) -> list[ReportSectionReview]:
    """Identify a few actionable cross-section defects without rewriting facts."""
    drafts = [{"section_id": row.section_id, "heading": row.heading,
               "markdown": row.draft_markdown} for row in sections]
    if sum(len(row["markdown"]) for row in drafts) > MAX_DOCUMENT_REVIEW_CHARS:
        raise ValueError("Whole-document review exceeds its bounded source window; no complete review was performed.")
    known = {row.section_id for row in sections}
    unresolved = [finding.model_dump(mode="json") for finding in memory.reviewer_findings
                  if finding_requires_resolution(finding)]
    prompt = json.dumps({
        "task": "review_document_coherence",
        "objective": memory.objective,
        "document_title": memory.document_plan.title if memory.document_plan else "",
        "report_mode": memory.report_mode,
        "template": template.name,
        "criteria": template.criteria_markdown,
        "sections": drafts,
        "section_responsibilities": [
            {"section_id": row.section_id, "heading": row.heading, "purpose": row.goal}
            for row in memory.section_plan
        ],
        "verified_execution_results": execution_summary,
        "execution_evidence": dict(execution_evidence or {}),
        "metric_sources": metric_summary,
        "source_evidence": source_evidence or [],
        "supplementary_evidence": [row.model_dump(mode="json") for row in (supplementary_evidence or [])[-8:]],
        "supplementary_evidence_omitted": max(0, len(supplementary_evidence or []) - 8),
        "context_tools": [{"name": spec.name, "description": spec.description, "input_schema": spec.input_schema}
                          for spec in report_tool_specs()] if config.allow_source_backtracking else [],
        "context_request_limit": config.max_backtracking_calls if config.allow_source_backtracking else 0,
        "known_limitations": memory.limitations[:12],
        "unresolved_section_findings": unresolved[:12],
        "unresolved_section_findings_omitted": max(0, len(unresolved) - 12),
        "focus": [
            *CLAIM_SCOPE_RULES,
            *REVIEW_ACTION_RULES,
            "Prioritize unresolved required corrections and verification before optional polish. Recheck them against the current draft and supplied evidence, not superseded prose. If still valid, target their sections for correction; if more remain than the two-section budget, retain them as unresolved rather than claiming complete repair.",
            "Find contradictions between sections about the same method, setting, result or conclusion.",
            "Find substantial repetition of protocol, metrics or limitations across sections; assign each fact a clear home.",
            "Check that abstract and conclusion do not claim more than results, and that paper versus analysis-report tone matches the evidence.",
            "Do not request new experiments or rewrite measurements. An evidence gap remains an unresolved finding.",
            "Separate declared protocol, executor observations and method verification. Invocation does not prove algorithmic details; elapsed duration is not the configured timeout.",
            "Distinguish parsed source passages from model reading notes and abstract-only access. Do not deny a reported result merely because it is absent from an abstract; check the supplied passages. Missing passages are not proof that the paper omits the result.",
            "Recorded bibliography is not independently verified identity or edition information. Check important attribution against original source passages; retain conflicting dates/identifiers and author-list coverage limits instead of inventing metadata.",
            "Return at most two section-level reviews for the most consequential issues; do not pad findings.",
        ],
        "output_schema": {"section_reviews": [{
            "section_id": "one of the supplied section ids",
            "verdict": "pass|warning|revise_required|fail",
            "findings": [{"finding_id": "stable id", "type": "style|unsupported_claim|metric_mismatch|citation_misuse|missing_limitation|evidence_gap",
                          "severity": "info|minor|major|critical", "message": "specific cross-section issue",
                          "required_action": "advisory|revise|verify",
                          "section_id": "same target section", "suggested_action": "bounded correction"}],
            "revision_instructions": ["specific change to this section without changing measured facts"],
            "context_requests": [{"tool_name": "get_paper_brief|get_metric_source|get_code_task_result|get_neighbor_chunks|search_source_chunks|get_synthesis_brief",
                                  "arguments": {}, "caller": "document_reviewer"}],
        }]},
    }, ensure_ascii=False)
    if len(prompt) > MAX_DOCUMENT_REVIEW_PROMPT_CHARS:
        raise ValueError("Whole-document review exceeds its bounded evidence window; no complete review was performed.")
    response = client.ask_json(
        "Review the complete report for cross-section coherence. Return only the requested JSON object.",
        prompt, label=label,
        max_output_tokens=config.max_section_tokens if config.max_section_tokens > 0 else None,
    )
    if not isinstance(response, Mapping) or not isinstance(response.get("section_reviews"), list):
        raise LLMResponseError("Whole-document reviewer did not return section_reviews.")
    raw_reviews = response["section_reviews"]
    if len(raw_reviews) > MAX_DOCUMENT_REVIEW_SECTIONS:
        raise LLMResponseError("Whole-document reviewer exceeded the bounded section review count.")
    reviews: list[ReportSectionReview] = []
    seen: set[str] = set()
    for raw in raw_reviews:
        if (not isinstance(raw, Mapping) or not isinstance(raw.get("section_id"), str)
                or raw["section_id"] not in known):
            raise LLMResponseError("Whole-document reviewer targeted an unknown section.")
        raw_findings = raw.get("findings") or []
        if not isinstance(raw_findings, list):
            raise LLMResponseError("Whole-document findings must be a list.")
        normalized = dict(raw)
        normalized["findings"] = [
            {**finding, "section_id": finding.get("section_id") or raw["section_id"]}
            if isinstance(finding, Mapping) else finding
            for finding in raw_findings
        ]
        review = ReportSectionReview.model_validate(normalized)
        if review.verdict in {"revise_required", "fail"} and not review.findings:
            raise LLMResponseError("Whole-document revision needs a concrete finding.")
        if review.section_id not in known or review.section_id in seen:
            raise LLMResponseError("Whole-document reviewer targeted an unknown or repeated section.")
        if any(finding.section_id != review.section_id for finding in review.findings):
            raise LLMResponseError("Whole-document finding must identify its target section.")
        seen.add(review.section_id)
        reviews.append(review)
    return reviews
