from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ReportModel(BaseModel):
    """Base model for report-domain artifacts.

    Report artifacts are part of the public run record. Unknown fields are
    ignored on load so older reports remain readable as the schema evolves.
    """

    model_config = ConfigDict(extra="ignore")


class ReportAuditConfig(ReportModel):
    """Mechanical audit switches for the report stage."""

    citations: bool = True
    metrics: bool = True
    claims: bool = True
    strict: bool = False


class ReportRuntimeConfig(ReportModel):
    """User-facing report-stage configuration."""

    mode: str = "auto"
    template: str = "auto"
    criteria: str = "auto"
    style: str = "paper"
    draft_sections: bool = False
    debug_artifacts: bool = False
    agent: str = "llm"
    reviewer: str = "llm"
    max_review_iterations: int = 2
    max_section_tokens: int = 1200
    max_report_tokens: int = 5000
    max_section_sources: int = 8
    source_strategy: Literal["full", "batch_refine"] = "full"
    source_batch_size: int = 10
    max_source_batches: int = 0
    review_source_batches: bool = False
    review_trace: Literal["off", "meta", "full"] = "meta"
    output_mode: Literal["overwrite", "archive", "variant"] = "overwrite"
    output_label: str = ""
    allow_source_backtracking: bool = True
    max_backtracking_calls: int = 8
    max_backtracking_tokens: int = 6000
    audit: ReportAuditConfig = Field(default_factory=ReportAuditConfig)


class ReportTemplateBundle(ReportModel):
    """Loaded writing template and reviewer criteria."""

    name: str
    mode: str
    template_path: str
    criteria_path: str
    template_markdown: str
    criteria_markdown: str


class SourceHandle(ReportModel):
    """Stable, read-only pointer to a report source artifact."""

    handle: str
    kind: str
    citation_key: str = ""
    title: str = ""
    artifact: str = ""
    paper_id: str = ""
    chunk_id: str = ""
    section: str = ""
    summary: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class MetricSource(ReportModel):
    """Metric value and provenance used by metric audit and tools."""

    metric_id: str
    name: str
    value: float | int | str
    artifact: str
    label: str = ""
    direction: str = ""


class ClaimEvidenceRecord(ReportModel):
    """Traceable relationship between a claim and supporting evidence."""

    claim_id: str
    claim: str
    status: Literal["supported", "partially_supported", "unsupported", "speculative"] = "speculative"
    evidence_handles: list[str] = Field(default_factory=list)
    metric_ids: list[str] = Field(default_factory=list)
    citation_ids: list[str] = Field(default_factory=list)
    notes: str = ""


class ReportSectionPlan(ReportModel):
    """One planned report section."""

    section_id: str
    heading: str
    goal: str
    evidence_handles: list[str] = Field(default_factory=list)
    required: bool = True
    final_order: int = 0
    draft_order: int = 0


class ReviewerFinding(ReportModel):
    """Structured finding from reviewer or mechanical audit."""

    finding_id: str
    type: str
    severity: Literal["info", "minor", "major", "critical"] = "info"
    message: str
    section_id: str = ""
    claim_id: str = ""
    evidence_handles: list[str] = Field(default_factory=list)
    suggested_action: str = ""


class ReportContext(ReportModel):
    """Compact report input assembled from earlier pipeline stages."""

    topic: str
    report_mode: str
    goal_markdown: str = ""
    problem_markdown: str = ""
    synthesis_markdown: str = ""
    hypothesis_markdown: str = ""
    evidence_summary: str = ""
    search_meta: dict[str, Any] = Field(default_factory=dict)
    experiment_plan: dict[str, Any] = Field(default_factory=dict)
    results: dict[str, Any] = Field(default_factory=dict)
    papers: list[dict[str, Any]] = Field(default_factory=list)
    source_handles: list[SourceHandle] = Field(default_factory=list)
    metric_sources: list[MetricSource] = Field(default_factory=list)
    citation_key_map: dict[str, str] = Field(default_factory=dict)
    max_section_sources: int = 8


class ReportMemory(ReportModel):
    """Compact recoverable state for section-wise report generation."""

    schema_version: int = 1
    objective: str = ""
    template: str = ""
    report_mode: str = ""
    section_plan: list[ReportSectionPlan] = Field(default_factory=list)
    claims_evidence_matrix: list[ClaimEvidenceRecord] = Field(default_factory=list)
    source_handles: list[SourceHandle] = Field(default_factory=list)
    metric_sources: list[MetricSource] = Field(default_factory=list)
    reviewer_findings: list[ReviewerFinding] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    key_decisions: list[str] = Field(default_factory=list)


class ReportToolSpec(ReportModel):
    """Tool contract that can be exported to local/OpenAI/MCP adapters."""

    name: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    permissions: list[str] = Field(default_factory=lambda: ["read"])
    max_calls: int = 8
    max_output_tokens: int = 1200


class ReportToolCall(ReportModel):
    """One bounded report tool request."""

    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    caller: str = "report"
    trace_id: str = ""


class ReportToolResult(ReportModel):
    """Structured report tool result."""

    tool_name: str
    status: Literal["ok", "not_found", "blocked", "error"] = "ok"
    summary: str = ""
    content: dict[str, Any] = Field(default_factory=dict)
    source_handles: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ReportSectionDraft(ReportModel):
    """Writer output for one report section."""

    section_id: str
    heading: str
    status: Literal["drafted", "revised", "skipped"] = "drafted"
    draft_markdown: str = ""
    used_sources: list[str] = Field(default_factory=list)
    metric_ids: list[str] = Field(default_factory=list)
    citations: list[str] = Field(default_factory=list)
    claims: list[ClaimEvidenceRecord] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class ReportSectionReview(ReportModel):
    """Reviewer output for one drafted report section."""

    section_id: str
    verdict: Literal["pass", "warning", "revise_required", "fail"] = "warning"
    findings: list[ReviewerFinding] = Field(default_factory=list)
    context_requests: list[ReportToolCall] = Field(default_factory=list)
    revision_instructions: list[str] = Field(default_factory=list)
    notes: str = ""

    @field_validator("revision_instructions", mode="before")
    @classmethod
    def _coerce_revision_instructions(cls, value: object) -> list[str]:
        """Accept reviewer instructions returned as strings or small objects."""
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        if not isinstance(value, list):
            return [str(value)]
        instructions: list[str] = []
        for item in value:
            text = _revision_instruction_text(item)
            if text:
                instructions.append(text)
        return instructions


def _revision_instruction_text(value: object) -> str:
    """Return a compact instruction string from flexible reviewer output."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        preferred = (
            value.get("instruction")
            or value.get("revision_instruction")
            or value.get("suggested_action")
            or value.get("message")
            or value.get("reason")
        )
        prefix = str(value.get("finding_id") or value.get("id") or "").strip()
        body = str(preferred).strip() if preferred is not None else ""
        if prefix and body:
            return f"{prefix}: {body}"
        if body:
            return body
    return str(value).strip()


class ReportIterationRecord(ReportModel):
    """Compact trace entry for one writer/reviewer pass."""

    iteration: int
    section_id: str
    action: str
    status: str
    summary: str = ""
    used_sources: list[str] = Field(default_factory=list)
    findings: list[ReviewerFinding] = Field(default_factory=list)
    tool_results: list[ReportToolResult] = Field(default_factory=list)


class AgentReportResult(ReportModel):
    """Final result from the controlled report writer/reviewer loop."""

    report_body: str
    memory: ReportMemory
    sections: list[ReportSectionDraft] = Field(default_factory=list)
    iterations: list[ReportIterationRecord] = Field(default_factory=list)
    reviewer_findings: list[ReviewerFinding] = Field(default_factory=list)
    tool_results: list[ReportToolResult] = Field(default_factory=list)
    used_agent: bool = False


class CitationAudit(ReportModel):
    status: Literal["passed", "warning", "failed"] = "passed"
    known_citations: list[str] = Field(default_factory=list)
    unknown_citations: list[str] = Field(default_factory=list)
    unused_references: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class MetricAudit(ReportModel):
    status: Literal["passed", "warning", "failed"] = "passed"
    matched_metrics: list[str] = Field(default_factory=list)
    unmatched_metrics: list[str] = Field(default_factory=list)
    unmatched_numbers: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ClaimAudit(ReportModel):
    status: Literal["passed", "warning", "failed"] = "passed"
    claims: list[ClaimEvidenceRecord] = Field(default_factory=list)
    findings: list[ReviewerFinding] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ReportAudit(ReportModel):
    """Compact report audit package written by V2.4 report stage."""

    schema_version: int = 1
    status: Literal["passed", "warning", "failed"] = "passed"
    citation_audit: CitationAudit = Field(default_factory=CitationAudit)
    metric_audit: MetricAudit = Field(default_factory=MetricAudit)
    claim_audit: ClaimAudit = Field(default_factory=ClaimAudit)
    reviewer_findings: list[ReviewerFinding] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
