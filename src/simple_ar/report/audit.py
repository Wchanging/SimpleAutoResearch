from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
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
NUMBER_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_])[-+]?(?:(?:\d{1,3}(?:,\d{3})+)|(?:\d+(?:\.\d+)?))"
    r"(?:[eE][-+]?\d+)?"
    r"(?:%|ms|s|sec|seconds)?(?![A-Za-z0-9_])"
)


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
    citation_cleanup_ref: ArtifactRef | None = None


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
            "Semantic support of final prose is unchecked; metric visibility and section review do not prove final conclusions.",
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
    if request.citation_cleanup_ref is not None:
        removed = context.read_input_json(request.citation_cleanup_ref)["removed_citations"]
        if removed:
            message = "Assembly removed unknown citations; associated claims need revision: " + ", ".join(removed)
            audit.citation_audit.unknown_citations = sorted(set(audit.citation_audit.unknown_citations) | set(removed))
            audit.citation_audit.warnings.append(message)
            audit.citation_audit.status = "failed"
            audit.status = "failed"
            audit.reviewer_findings.append(ReviewerFinding(
                finding_id="citation-cleanup", type="unresolved_citation", severity="major",
                message=message, suggested_action="Revise the affected claims against existing evidence; do not only delete citation markers.",
            ))
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
    # ``context.papers`` is the selected source pool, not the final reference
    # list.  Report assembly intentionally prunes that pool to body-cited
    # papers before writing References/references.bib.  Keep the unused ids in
    # the audit for provenance, but do not turn normal source selection into a
    # report warning.  Unknown citations and a completely uncited paper pool
    # remain hard quality signals above.
    return CitationAudit(
        status=status,
        known_citations=sorted(found & known),
        unknown_citations=unknown,
        unused_references=unused,
        warnings=warnings,
    )


def _metric_audit(report_body: str, context: ReportContext) -> MetricAudit:
    """Check metric visibility and ledger attribution, not arbitrary prose numbers."""
    if not context.metric_sources:
        errors = _measurement_table_errors(report_body, context)
        return MetricAudit(status="failed" if errors else "passed", warnings=errors)
    metrics = _report_metric_sources(context)
    lower = report_body.lower()
    matched: list[str] = []
    unmatched: list[str] = []
    for metric in metrics:
        if _metric_is_visible(report_body, lower, metric):
            matched.append(metric.metric_id)
        else:
            unmatched.append(metric.metric_id)
    warnings: list[str] = []
    status = "passed"
    if unmatched:
        warnings.append("Some experiment metrics were not visible with their values in the report body.")
        status = "warning"
    table_errors = _measurement_table_errors(report_body, context)
    if table_errors:
        warnings.extend(table_errors)
        status = "failed"
    return MetricAudit(
        status=status,
        matched_metrics=matched,
        unmatched_metrics=unmatched,
        warnings=warnings,
    )


def _measurement_table_errors(report_body: str, context: ReportContext) -> list[str]:
    """Check our structured metric ledger, not arbitrary natural-language tables."""
    header = ["Source", "Metric", "Value", "Unit", "Condition", "Origin"]

    def cell(value: str) -> str:
        return value.replace("|", "/").replace("\n", " ").strip()

    expected = {
        (cell(metric.label or "experiment"), f"`{cell(metric.name)}`", _format_metric(metric.value),
         cell(metric.unit or "not recorded"), cell(metric.condition_id or "not recorded"), cell(metric.source_kind))
        for metric in context.metric_sources
    }
    errors = []
    in_table = False
    for line_number, line in enumerate(report_body.splitlines(), 1):
        if not line.strip().startswith("|"):
            in_table = False
            continue
        row = [part.strip() for part in line.strip().strip("|").split("|")]
        if row == header:
            in_table = True
            continue
        if not in_table or all(re.fullmatch(r":?-+:?", part) for part in row):
            continue
        if tuple(row) not in expected:
            errors.append(f"Measurement table row {line_number} does not match its source, metric, value, unit, condition and origin.")
    return errors


def _claim_audit(memory: ReportMemory) -> ClaimAudit:
    findings: list[ReviewerFinding] = []
    for claim in memory.claims_evidence_matrix:
        # ``unsupported`` is a valid scientific outcome when the analysis
        # recorded the measured evidence that failed to support a hypothesis.
        # It is not the same as an unsupported assertion in the report. Only
        # flag a rejected claim when its record has no evidence or measurement
        # link at all; the Writer/Reviewer remains responsible for wording.
        if claim.status == "unsupported" and not (
            claim.evidence_handles or claim.metric_ids or claim.citation_ids
        ):
            findings.append(
                ReviewerFinding(
                    finding_id=f"claim-{len(findings)+1:03d}",
                    type="unsupported_claim",
                    severity="major",
                    message=f"Rejected claim has no linked evidence: {claim.claim}",
                    claim_id=claim.claim_id,
                    suggested_action="Link the measured evidence or remove the claim.",
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




def _metric_is_visible(report_body: str, lower_report: str, metric: Any) -> bool:
    """Check a metric using readable names as well as machine identifiers."""

    names = _metric_name_variants(str(metric.name))
    if not any(_contains_phrase(lower_report, name) for name in names):
        return False
    value_variants = _metric_value_variants(metric.value)
    if any(_contains_phrase(lower_report, value_text.lower()) for value_text in value_variants):
        return True
    expected_numeric = {
        numeric
        for value_text in value_variants
        if (numeric := _numeric_token_key(value_text)) is not None
    }
    if not expected_numeric:
        return False
    citation_free = CITATION_PATTERN.sub("", report_body)
    return any(
        (numeric := _numeric_token_key(value_text)) is not None
        and numeric in expected_numeric
        for value_text in NUMBER_PATTERN.findall(citation_free)
    )


def _report_metric_sources(context: ReportContext) -> list[Any]:
    """Audit compact paired summaries while retaining raw metrics in artifacts."""
    summaries = context.results.get("paired_summary") if isinstance(context.results, Mapping) else None
    if not isinstance(summaries, list) or not summaries:
        return context.metric_sources
    aggregate_names = {
        str(row.get("metric") or "").strip()
        for row in summaries
        if isinstance(row, Mapping)
        and str(row.get("metric") or "").strip()
        and "_after_task_" not in str(row.get("metric") or "")
    }
    selected = [
        metric for metric in context.metric_sources
        if metric.label.startswith("paired_summary:")
        and metric.name.split(".", 1)[0] in aggregate_names
    ]
    return selected or context.metric_sources


def _metric_name_variants(name: str) -> set[str]:
    raw = name.lower().strip()
    normalized = re.sub(r"[_-]+", " ", raw).strip()
    if not normalized:
        return set()
    variants = {normalized, raw}
    if "." in raw:
        base = raw.split(".", 1)[0].strip()
        variants.update({base, re.sub(r"[_-]+", " ", base).strip()})
    words = normalized.split()
    if words and words[-1] in {
        "s",
        "sec",
        "secs",
        "second",
        "seconds",
        "ms",
        "millisecond",
        "milliseconds",
    }:
        variants.add(" ".join(words[:-1]))
    aliases = {
        "train time": "training time",
        "eval examples": "evaluation examples",
        "evaluation examples": "eval examples",
    }
    for variant in tuple(variants):
        alias = aliases.get(variant)
        if alias:
            variants.add(alias)
    if normalized == "eval examples":
        variants.add("evaluation set")
    return {variant for variant in variants if variant}


def _metric_value_variants(value: Any) -> set[str]:
    variants = {_format_metric(value), str(value)}
    if isinstance(value, float):
        variants.add(format(value, ".12g"))
        if value.is_integer():
            variants.add(str(int(value)))
    return {variant for variant in variants if variant}


def _numeric_token_key(value: object) -> tuple[Decimal, str] | None:
    """Normalize a report number while preserving an optional display unit."""

    match = re.fullmatch(
        r"\s*([-+]?(?:(?:\d{1,3}(?:,\d{3})+)|(?:\d+(?:\.\d+)?))"
        r"(?:[eE][-+]?\d+)?)(%|ms|s|sec|seconds)?\s*",
        str(value),
        flags=re.IGNORECASE,
    )
    if match is None:
        return None
    try:
        number = Decimal(match.group(1).replace(",", ""))
    except InvalidOperation:
        return None
    unit = (match.group(2) or "").lower()
    if unit == "seconds":
        unit = "sec"
    return number, unit


def _contains_phrase(text: str, phrase: str) -> bool:
    return re.search(
        rf"(?<![A-Za-z0-9_]){re.escape(phrase)}(?![A-Za-z0-9_])",
        text,
    ) is not None




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
