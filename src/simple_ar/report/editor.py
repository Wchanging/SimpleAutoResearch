"""Bounded cross-section review using the existing report drafts and evidence."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from simple_ar.integrations.llm import LLMResponseError
from simple_ar.report.schema import (
    ReportMemory,
    ReportRuntimeConfig,
    ReportSectionDraft,
    ReportSectionReview,
    ReportTemplateBundle,
)


MAX_DOCUMENT_REVIEW_CHARS = 60_000
MAX_DOCUMENT_REVIEW_PROMPT_CHARS = 90_000
MAX_DOCUMENT_REVISIONS = 2


def review_document(
    *, client: Any, template: ReportTemplateBundle, memory: ReportMemory,
    sections: list[ReportSectionDraft], config: ReportRuntimeConfig,
    execution_summary: Mapping[str, Any], metric_summary: Mapping[str, Any],
    label: str = "report-document-reviewer",
) -> list[ReportSectionReview]:
    """Identify a few actionable cross-section defects without rewriting facts."""
    drafts = [{"section_id": row.section_id, "heading": row.heading,
               "markdown": row.draft_markdown} for row in sections]
    if sum(len(row["markdown"]) for row in drafts) > MAX_DOCUMENT_REVIEW_CHARS:
        raise ValueError("Whole-document review exceeds its bounded source window; no complete review was performed.")
    known = {row.section_id for row in sections}
    prompt = json.dumps({
        "task": "review_document_coherence",
        "objective": memory.objective,
        "report_mode": memory.report_mode,
        "template": template.name,
        "criteria": template.criteria_markdown,
        "sections": drafts,
        "verified_execution_results": execution_summary,
        "metric_sources": metric_summary,
        "known_limitations": memory.limitations[:12],
        "focus": [
            "Find contradictions between sections about the same method, setting, result or conclusion.",
            "Find substantial repetition of protocol, metrics or limitations across sections; assign each fact a clear home.",
            "Check that abstract and conclusion do not claim more than results, and that paper versus analysis-report tone matches the evidence.",
            "Do not request new experiments or rewrite measurements. An evidence gap remains an unresolved finding.",
            "Return at most two section-level reviews for the most consequential issues; do not pad findings.",
        ],
        "output_schema": {"section_reviews": [{
            "section_id": "one of the supplied section ids",
            "verdict": "pass|warning|revise_required|fail",
            "findings": [{"finding_id": "stable id", "type": "style|unsupported_claim|metric_mismatch|citation_misuse|missing_limitation|evidence_gap",
                          "severity": "info|minor|major|critical", "message": "specific cross-section issue",
                          "section_id": "same target section", "suggested_action": "bounded correction"}],
            "revision_instructions": ["specific change to this section without changing measured facts"],
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
    if len(raw_reviews) > MAX_DOCUMENT_REVISIONS:
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
