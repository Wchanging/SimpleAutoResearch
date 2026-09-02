from __future__ import annotations

import re
from dataclasses import dataclass
from itertools import count
from typing import Any, Mapping

from simple_ar.core.capabilities import ArtifactRef, CapabilityContext, CapabilityResult
from simple_ar.report.schema import (
    CitationAudit,
    ClaimAudit,
    MetricAudit,
    ReportAudit,
    ReportContext,
    ReportMemory,
    ReviewerFinding,
)


CITATION_PATTERN = re.compile(r"(?<![A-Za-z0-9_])@([A-Za-z0-9_.:-]+)")
NUMBER_PATTERN = re.compile(r"(?<![A-Za-z0-9_])(?:\d+\.\d+|\d+)(?:%|ms|s|sec|seconds)?(?![A-Za-z0-9_])")


@dataclass(frozen=True, slots=True)
class ReportAuditRequest:
    """Inputs for the standalone, side-effect-free report audit."""

    report: str
    report_body: str
    context: ReportContext
    memory: ReportMemory


@dataclass(frozen=True, slots=True)
class ReportAuditCapabilityRequest:
    """Explicit artifact inputs for a controller-managed report audit."""

    report_ref: ArtifactRef
    context: ReportContext | Mapping[str, Any]
    memory: ReportMemory | Mapping[str, Any]
    report_body_ref: ArtifactRef | None = None


def build_report_audit(
    *,
    report: str,
    report_body: str,
    context: ReportContext,
    memory: ReportMemory,
) -> ReportAudit:
    """Build compact mechanical audit for the final report."""
    citation = _citation_audit(report_body, context)
    metric = _metric_audit(report_body, context)
    claim = _claim_audit(memory)
    findings = citation.warnings + metric.warnings + claim.warnings
    reviewer_findings = list(memory.reviewer_findings) + _mechanical_findings(findings)
    status = _overall_status([citation.status, metric.status, claim.status])
    if any(finding.severity == "critical" for finding in reviewer_findings):
        status = "failed"
    elif any(finding.severity == "major" for finding in reviewer_findings) and status == "passed":
        status = "warning"
    return ReportAudit(
        status=status,
        citation_audit=citation,
        metric_audit=metric,
        claim_audit=claim,
        reviewer_findings=reviewer_findings,
        notes=[
            "V2.4 audit combines local rule gates with Writer/Reviewer findings when agent mode is enabled.",
            "Mechanical checks remain conservative and provenance-focused.",
        ],
    )


def audit_report(request: ReportAuditRequest) -> ReportAudit:
    """Audit one report without invoking the writer or changing artifacts."""

    return build_report_audit(
        report=request.report,
        report_body=request.report_body,
        context=request.context,
        memory=request.memory,
    )


def run_report_audit_capability(
    *,
    context: CapabilityContext,
    request: ReportAuditCapabilityRequest,
) -> CapabilityResult:
    """Audit explicit report artifacts through the session boundary.

    The adapter preserves the existing ``report_audit.json`` shape and leaves
    writer/revision policy to the caller. A separate body reference is
    optional because callers that do not persist a pre-reference report can
    audit the same text for both the final report and its body.
    """

    report = context.read_input_text(request.report_ref)
    report_body = (
        context.read_input_text(request.report_body_ref)
        if request.report_body_ref is not None
        else report
    )
    report_context = (
        request.context
        if isinstance(request.context, ReportContext)
        else ReportContext.model_validate(request.context)
    )
    report_memory = (
        request.memory
        if isinstance(request.memory, ReportMemory)
        else ReportMemory.model_validate(request.memory)
    )
    audit = audit_report(
        ReportAuditRequest(
            report=report,
            report_body=report_body,
            context=report_context,
            memory=report_memory,
        )
    )
    output = context.store.write_json(
        "report_audit.json",
        audit.model_dump(mode="json"),
        kind="report_audit",
        schema="report_audit.v1",
        producer="report.audit",
    )
    warnings = (
        *audit.citation_audit.warnings,
        *audit.metric_audit.warnings,
        *audit.claim_audit.warnings,
    )
    capability_status = {
        "passed": "completed",
        "warning": "partial",
        "failed": "failed",
    }[audit.status]
    return CapabilityResult(
        status=capability_status,  # type: ignore[arg-type]
        artifacts=(output,),
        diagnostics=tuple(warnings),
        usage={
            "audit_status": audit.status,
            "citation_warning_count": len(audit.citation_audit.warnings),
            "metric_warning_count": len(audit.metric_audit.warnings),
            "claim_warning_count": len(audit.claim_audit.warnings),
        },
        provenance={
            "capability": "report_audit",
            "report_ref": request.report_ref.path,
            "report_body_ref": (
                request.report_body_ref.path if request.report_body_ref is not None else ""
            ),
            "result_schema": "report_audit.v1",
        },
    )


def _citation_audit(report_body: str, context: ReportContext) -> CitationAudit:
    known = {
        str(paper.get("id"))
        for paper in context.papers
        if isinstance(paper, dict) and str(paper.get("id") or "").strip()
    }
    found = set(CITATION_PATTERN.findall(report_body))
    unknown = sorted(found - known)
    unused = sorted(known - found)
    warnings: list[str] = []
    status = "passed"
    if unknown:
        warnings.append("Report contains citation ids that are not in papers.jsonl.")
        status = "failed"
    if known and not found:
        warnings.append("Report has paper metadata but no body citations.")
        status = "failed"
    elif unused:
        warnings.append("Some retrieved papers are not cited in the report body.")
        if status == "passed":
            status = "warning"
    return CitationAudit(
        status=status,
        known_citations=sorted(found & known),
        unknown_citations=unknown,
        unused_references=unused,
        warnings=warnings,
    )


def _metric_audit(report_body: str, context: ReportContext) -> MetricAudit:
    if not context.metric_sources:
        return MetricAudit(status="passed")
    lower = report_body.lower()
    matched: list[str] = []
    unmatched: list[str] = []
    for metric in context.metric_sources:
        value_text = _format_metric(metric.value)
        if metric.name.lower() in lower and (value_text in report_body or str(metric.value) in report_body):
            matched.append(metric.metric_id)
        else:
            unmatched.append(metric.metric_id)
    warnings: list[str] = []
    status = "passed"
    if unmatched:
        warnings.append("Some experiment metrics were not visible with their values in the report body.")
        status = "warning"
    unmatched_numbers = _unmatched_numbers(report_body, context)
    if unmatched_numbers:
        warnings.append("Report contains numbers that are not obviously tied to metric sources.")
        if status == "passed":
            status = "warning"
    return MetricAudit(
        status=status,
        matched_metrics=matched,
        unmatched_metrics=unmatched,
        unmatched_numbers=unmatched_numbers,
        warnings=warnings,
    )


def _claim_audit(memory: ReportMemory) -> ClaimAudit:
    findings: list[ReviewerFinding] = []
    for claim in memory.claims_evidence_matrix:
        if claim.status == "unsupported":
            findings.append(
                ReviewerFinding(
                    finding_id=f"claim-{len(findings)+1:03d}",
                    type="unsupported_claim",
                    severity="major",
                    message=f"Unsupported claim remains in report memory: {claim.claim}",
                    claim_id=claim.claim_id,
                    suggested_action="Remove, weaken, or move to limitations/future work.",
                )
            )
    status = "warning" if findings else "passed"
    return ClaimAudit(
        status=status,
        claims=memory.claims_evidence_matrix,
        findings=findings,
        warnings=[finding.message for finding in findings],
    )


def _mechanical_findings(messages: list[str]) -> list[ReviewerFinding]:
    ids = count(1)
    return [
        ReviewerFinding(
            finding_id=f"audit-{next(ids):03d}",
            type="mechanical_audit",
            severity="minor" if "not cited" in message.lower() else "major",
            message=message,
        )
        for message in messages
    ]


def _unmatched_numbers(report_body: str, context: ReportContext) -> list[str]:
    metric_values = {_format_metric(metric.value) for metric in context.metric_sources}
    allowed = metric_values | {"0", "1", "2", "3", "4", "5", "8", "10"}
    citation_free = CITATION_PATTERN.sub("", report_body)
    found = sorted(set(NUMBER_PATTERN.findall(citation_free)))
    return [value for value in found if value not in allowed][:12]


def _format_metric(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.6g}"
    return str(value)


def _overall_status(statuses: list[str]) -> str:
    if "failed" in statuses:
        return "failed"
    if "warning" in statuses:
        return "warning"
    return "passed"
