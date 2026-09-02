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
from simple_ar.result_analysis.schema import AnalysisResult
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


def _analysis_result(result: AnalysisResult | Mapping[str, Any]) -> AnalysisResult:
    if isinstance(result, AnalysisResult):
        return result
    payload = dict(result)
    nested = payload.get("analysis")
    if isinstance(nested, Mapping):
        payload = dict(nested)
    return AnalysisResult.model_validate(payload)


__all__ = [
    "transition_request_from_analysis",
]
