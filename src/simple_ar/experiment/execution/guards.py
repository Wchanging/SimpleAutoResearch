"""Guard checks for canonical experiment results."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class GuardIssue:
    severity: str
    code: str
    message: str

    def to_json(self) -> dict[str, str]:
        return asdict(self)


def evaluate_result_guard(
    results: Mapping[str, Any],
    *,
    result_schema: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Check result integrity before report stages make strong claims."""

    schema = dict(result_schema or results.get("result_schema") or {})
    contract = results.get("experiment_contract")
    contract = contract if isinstance(contract, Mapping) else {}
    issues: list[GuardIssue] = []
    metrics = results.get("metrics")
    if not isinstance(metrics, dict):
        metrics = {}

    if results.get("timed_out") is True:
        issues.append(GuardIssue("error", "timeout", "Experiment timed out."))
    if results.get("returncode") not in {0, "0"}:
        issues.append(
            GuardIssue(
                "error",
                "nonzero_returncode",
                f"Experiment returned non-zero code: {results.get('returncode')}.",
            )
        )
    if not metrics:
        issues.append(GuardIssue("warning", "missing_metrics", "No parsed metrics were found."))
    execution = results.get("execution")
    execution = execution if isinstance(execution, Mapping) else {}
    if not metrics and execution.get("stdout_chars") == 0:
        issues.append(
            GuardIssue(
                "warning",
                "empty_stdout",
                "Experiment produced no stdout, so metric parsing and report claims are weak.",
            )
        )

    primary_metric = str(schema.get("primary_metric") or results.get("primary_metric") or "").strip()
    if primary_metric and primary_metric not in metrics:
        issues.append(
            GuardIssue(
                "error",
                "missing_primary_metric",
                f"Primary metric `{primary_metric}` is missing from results.",
            )
        )
    for metric in _required_metrics(schema):
        if metric and metric not in metrics:
            issues.append(
                GuardIssue(
                    "error",
                    "missing_required_metric",
                    f"Required metric `{metric}` is missing from results.",
                )
            )
    for name, value in metrics.items():
        if isinstance(value, bool):
            issues.append(
                GuardIssue(
                    "warning",
                    "boolean_metric",
                    f"Metric `{name}` is boolean; numeric metric expected.",
                )
            )
            continue
        if isinstance(value, (int, float)) and not math.isfinite(float(value)):
            issues.append(
                GuardIssue("error", "nonfinite_metric", f"Metric `{name}` is NaN or Inf.")
            )
    task_kind = str(contract.get("task_kind", "")).strip()
    comparisons = results.get("comparisons")
    if task_kind == "existing_project" and not comparisons:
        issues.append(
            GuardIssue(
                "warning",
                "missing_existing_project_comparison",
                "Existing-project experiment has no baseline-vs-candidate comparison.",
            )
        )
    if task_kind in {"greenfield", "benchmark_solution"} and not comparisons:
        issues.append(
            GuardIssue(
                "info",
                "no_baseline_expected",
                "Greenfield experiment has no baseline comparison; claims should remain bounded.",
            )
        )
    _append_code_review_issues(results, issues)
    _append_repair_issues(results, issues)
    _append_review_recovery_issues(results, issues)
    _append_comparison_verdict_issues(results, issues)
    _append_success_criteria_issues(contract, schema, results, issues)

    status = _guard_status(issues)
    return {
        "schema_version": "2.5",
        "generated_at": _utcnow_iso(),
        "status": status,
        "issues": [issue.to_json() for issue in issues],
        "summary": {
            "error_count": sum(1 for issue in issues if issue.severity == "error"),
            "warning_count": sum(1 for issue in issues if issue.severity == "warning"),
            "metric_count": len(metrics),
        },
    }


def _required_metrics(schema: Mapping[str, Any]) -> list[str]:
    value = schema.get("required_metrics")
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def _guard_status(issues: list[GuardIssue]) -> str:
    if any(issue.severity == "error" for issue in issues):
        return "failed"
    if any(issue.severity == "warning" for issue in issues):
        return "warning"
    return "passed"


def _append_code_review_issues(results: Mapping[str, Any], issues: list[GuardIssue]) -> None:
    review = results.get("code_review")
    if not isinstance(review, Mapping):
        return
    status = str(review.get("status") or "").strip().lower()
    summary = review.get("summary")
    summary = summary if isinstance(summary, Mapping) else {}
    warning_count = summary.get("warning_count", 0)
    error_count = summary.get("error_count", 0)
    if status == "failed" or _positive_int(error_count):
        issues.append(
            GuardIssue(
                "error",
                "code_review_failed",
                "Generated code review reported errors; experiment claims require repair before use.",
            )
        )
    elif status == "warning" or _positive_int(warning_count):
        issues.append(
            GuardIssue(
                "warning",
                "code_review_warning",
                "Generated code review reported warnings; report claims should mention implementation risk.",
            )
        )


def _append_repair_issues(results: Mapping[str, Any], issues: list[GuardIssue]) -> None:
    repair = results.get("repair")
    if not isinstance(repair, Mapping):
        return
    if str(repair.get("status") or "").strip().lower() == "patched":
        issues.append(
            GuardIssue(
                "warning",
                "experiment_repaired",
                "Experiment metrics were produced after a bounded repair; report claims should disclose this repair context.",
            )
        )


def _append_review_recovery_issues(results: Mapping[str, Any], issues: list[GuardIssue]) -> None:
    recovery = results.get("review_failure_recovery")
    if not isinstance(recovery, Mapping):
        return
    issues.append(
        GuardIssue(
            "warning",
            "code_generation_recovered",
            "LLM-generated code failed review and was replaced by a deterministic fallback scaffold.",
        )
    )


def _append_comparison_verdict_issues(results: Mapping[str, Any], issues: list[GuardIssue]) -> None:
    verdicts = results.get("verdicts")
    if not isinstance(verdicts, list):
        return
    weak = {"failed", "regressed", "mixed", "inconclusive", "unknown"}
    for row in verdicts:
        if not isinstance(row, Mapping):
            continue
        verdict = str(row.get("verdict") or "").strip().lower()
        if verdict in weak:
            issues.append(
                GuardIssue(
                    "warning",
                    "weak_result_verdict",
                    f"Result verdict `{verdict}` is not strong enough for unqualified improvement claims.",
                )
            )


def _append_success_criteria_issues(
    contract: Mapping[str, Any],
    schema: Mapping[str, Any],
    results: Mapping[str, Any],
    issues: list[GuardIssue],
) -> None:
    criteria = contract.get("success_criteria") or schema.get("success_criteria")
    if not isinstance(criteria, list) or not criteria:
        return
    comparisons = results.get("comparisons")
    verdicts = results.get("verdicts")
    has_machine_verdict = isinstance(comparisons, list) and bool(comparisons)
    has_observed_metric = isinstance(verdicts, list) and any(
        isinstance(row, Mapping) and row.get("name") == "primary_metric_observed"
        for row in verdicts
    )
    if not has_machine_verdict and has_observed_metric:
        issues.append(
            GuardIssue(
                "info",
                "success_criteria_requires_review",
                "Success criteria were recorded, but only metric presence was machine-checked.",
            )
        )


def _positive_int(value: object) -> bool:
    try:
        return int(value) > 0
    except (TypeError, ValueError):
        return False


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
