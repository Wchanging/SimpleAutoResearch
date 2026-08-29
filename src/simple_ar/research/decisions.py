"""Translate research-level outcomes into bounded session transitions.

The core session controller deliberately does not know what an analysis or a
report audit means.  These small adapters keep that interpretation at the
research boundary: they produce an existing ``TransitionRequest`` but never
execute a transition, retry an attempt, or choose a target on the caller's
behalf.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from simple_ar.core.transitions import TransitionRequest
from simple_ar.report.schema import ReportAudit
from simple_ar.result_analysis.schema import AnalysisResult
from simple_ar.research.synthesis import SynthesisResult


def transition_request_from_analysis(
    result: AnalysisResult | Mapping[str, Any],
    *,
    target: str | None = None,
    source: str = "analysis",
    expected_delta: str = "",
) -> TransitionRequest:
    """Build a bounded transition input from an analysis handoff.

    ``AnalysisResult.status`` is an observation, not a research policy.  The
    mapping below only normalizes it to the status vocabulary understood by
    the finite core policy.  In particular, a below-target metric requests a
    revision opportunity; it does not decide whether to run another
    experiment.  Persisted mappings are accepted so callers can use the
    same function with ``analysis.json`` without inventing a second model.
    """

    analysis = _analysis_result(result)
    status = analysis.status
    result_status = {
        "passed": "completed",
        "incomplete": "partial",
        "metric_below_target": "failed",
        "failed": "failed",
        "blocked": "blocked",
    }[status]
    failure_kind = {
        "incomplete": "evidence",
        "metric_below_target": "metric",
    }.get(status)
    return TransitionRequest(
        source=source,
        result_status=result_status,
        failure_kind=failure_kind,
        target=target,
        evidence_sufficient=False if status == "incomplete" else None,
        experiment_needed=True if status == "metric_below_target" else None,
        expected_delta=expected_delta,
        signals=tuple(analysis.status_reasons),
    )


def transition_request_from_synthesis(
    result: SynthesisResult | Mapping[str, Any],
    *,
    target: str | None = None,
    source: str = "synthesize",
    expected_delta: str = "",
) -> TransitionRequest:
    """Build a bounded transition input from a synthesis handoff.

    ``needs_review`` means that the evidence-to-direction result is usable as
    a diagnostic but not complete enough to accept as a downstream handoff.
    The adapter therefore marks evidence as insufficient without deciding
    whether the caller should search, read, or revise synthesis.  It does not
    infer hypothesis support from novelty hints or decide to run an experiment.
    """

    synthesis = _synthesis_result(result)
    ready = synthesis.status == "ready"
    return TransitionRequest(
        source=source,
        result_status="completed" if ready else "partial",
        target=target,
        evidence_sufficient=ready,
        expected_delta=expected_delta,
        signals=tuple(synthesis.diagnostics),
    )


def transition_request_from_report_audit(
    audit: ReportAudit | Mapping[str, Any],
    *,
    target: str | None = None,
    source: str = "report_audit",
    expected_delta: str = "",
) -> TransitionRequest:
    """Build a bounded transition input from a report audit handoff.

    A warning is represented as a partial result and a failed audit as a
    failed result.  The caller still owns the choice between revising the
    report, adding evidence, or stopping at the session budget.
    """

    report_audit = _report_audit(audit)
    status = report_audit.status
    result_status = {
        "passed": "completed",
        "warning": "partial",
        "failed": "failed",
    }[status]
    signals = _audit_signals(report_audit)
    return TransitionRequest(
        source=source,
        result_status=result_status,
        target=target,
        report_auditable=status == "passed",
        expected_delta=expected_delta,
        signals=signals,
    )


def _analysis_result(result: AnalysisResult | Mapping[str, Any]) -> AnalysisResult:
    if isinstance(result, AnalysisResult):
        return result
    payload = dict(result)
    nested = payload.get("analysis")
    if isinstance(nested, Mapping):
        payload = dict(nested)
    return AnalysisResult.model_validate(payload)


def _synthesis_result(result: SynthesisResult | Mapping[str, Any]) -> SynthesisResult:
    if isinstance(result, SynthesisResult):
        return result
    return SynthesisResult.from_handoff_dict(result)


def _report_audit(audit: ReportAudit | Mapping[str, Any]) -> ReportAudit:
    if isinstance(audit, ReportAudit):
        return audit
    return ReportAudit.model_validate(audit)


def _audit_signals(audit: ReportAudit) -> tuple[str, ...]:
    signals: list[str] = []
    for section in (
        audit.citation_audit,
        audit.metric_audit,
        audit.claim_audit,
    ):
        signals.extend(str(item) for item in section.warnings if str(item).strip())
    signals.extend(
        finding.message
        for finding in audit.reviewer_findings
        if finding.message.strip()
    )
    return tuple(dict.fromkeys(signals))


__all__ = [
    "transition_request_from_analysis",
    "transition_request_from_synthesis",
    "transition_request_from_report_audit",
]
