"""Experiment run diagnosis for guarded repair and report context."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping


def diagnose_experiment_run(
    *,
    results: Mapping[str, Any],
    guard_report: Mapping[str, Any],
    result_schema: Mapping[str, Any] | None = None,
    code_review: Mapping[str, Any] | None = None,
    stdout_tail: str = "",
    stderr_tail: str = "",
) -> dict[str, Any]:
    """Build a compact, actionable diagnosis from run, guard, and review signals."""

    schema = dict(result_schema or results.get("result_schema") or {})
    metrics = results.get("metrics")
    metrics = metrics if isinstance(metrics, Mapping) else {}
    required = _required_metrics(schema)
    observed = sorted(str(name) for name in metrics)
    missing = [name for name in required if name not in metrics]
    deficiencies = _deficiencies_from_guard(guard_report, missing)
    deficiencies.extend(_deficiencies_from_code_review(code_review or results.get("code_review")))
    if results.get("timed_out") is True and not _has_code(deficiencies, "timeout"):
        deficiencies.append(
            _deficiency(
                "critical",
                "runtime",
                "timeout",
                "The experiment timed out.",
                "Reduce workload, make the benchmark smaller, or increase the declared timeout only if resources allow.",
            )
        )
    if results.get("returncode") not in {0, "0"} and not _has_code(deficiencies, "nonzero_returncode"):
        deficiencies.append(
            _deficiency(
                "critical",
                "runtime",
                "nonzero_returncode",
                f"The experiment returned non-zero code: {results.get('returncode')}.",
                "Inspect stderr and repair the failing entrypoint before trusting metrics.",
                evidence={"stderr_tail": stderr_tail[-1200:]},
            )
        )
    completion_rate = 1.0 if not required else (len(required) - len(missing)) / len(required)
    local_repair_supported = any(
        item.get("code") in {"missing_primary_metric", "missing_required_metric", "missing_metrics"}
        for item in deficiencies
    )
    status = _diagnosis_status(deficiencies, guard_report)
    actions = _suggested_actions(deficiencies, local_repair_supported)
    return {
        "schema_version": "experiment_diagnosis.v1",
        "generated_at": _utcnow_iso(),
        "status": status,
        "summary": _summary_text(status, deficiencies, missing),
        "completion": {
            "required_metrics": required,
            "observed_metrics": observed,
            "missing_metrics": missing,
            "metric_completion_rate": round(completion_rate, 4),
        },
        "deficiencies": deficiencies,
        "repair": {
            "local_repair_supported": local_repair_supported,
            "suggested_actions": actions,
        },
        "context": {
            "stdout_tail": stdout_tail[-1200:],
            "stderr_tail": stderr_tail[-1200:],
        },
    }


def render_diagnosis_markdown(diagnosis: Mapping[str, Any]) -> str:
    """Render the diagnosis as a human-readable run artifact."""

    completion = diagnosis.get("completion")
    completion = completion if isinstance(completion, Mapping) else {}
    deficiencies = diagnosis.get("deficiencies")
    rows = [item for item in deficiencies if isinstance(item, Mapping)] if isinstance(deficiencies, list) else []
    actions_raw = diagnosis.get("repair")
    actions = actions_raw.get("suggested_actions") if isinstance(actions_raw, Mapping) else []
    action_rows = [str(item) for item in actions if str(item).strip()] if isinstance(actions, list) else []
    lines = [
        "# Experiment Diagnosis",
        "",
        f"- Status: `{diagnosis.get('status', 'unknown')}`",
        f"- Summary: {diagnosis.get('summary', '')}",
        f"- Metric completion: `{completion.get('metric_completion_rate', 0)}`",
        f"- Required metrics: {_join_list(completion.get('required_metrics'))}",
        f"- Observed metrics: {_join_list(completion.get('observed_metrics'))}",
        f"- Missing metrics: {_join_list(completion.get('missing_metrics'))}",
        "",
        "## Deficiencies",
        "",
    ]
    if rows:
        lines.extend(["| Severity | Category | Code | Suggested fix |", "|---|---|---|---|"])
        for item in rows:
            lines.append(
                "| "
                + " | ".join(
                    [
                        _cell(item.get("severity")),
                        _cell(item.get("category")),
                        _cell(item.get("code")),
                        _cell(item.get("suggested_fix") or item.get("message")),
                    ]
                )
                + " |"
            )
    else:
        lines.append("No blocking deficiencies were detected.")
    lines.extend(["", "## Suggested Actions", ""])
    if action_rows:
        lines.extend(f"- {item}" for item in action_rows)
    else:
        lines.append("- No repair action is needed.")
    lines.append("")
    return "\n".join(lines)


def compact_diagnosis(diagnosis: Mapping[str, Any]) -> dict[str, Any]:
    """Keep canonical results small while retaining repair/report signals."""

    deficiencies = diagnosis.get("deficiencies")
    rows = [item for item in deficiencies if isinstance(item, Mapping)] if isinstance(deficiencies, list) else []
    return {
        "schema_version": diagnosis.get("schema_version", "experiment_diagnosis.v1"),
        "status": diagnosis.get("status", "unknown"),
        "summary": diagnosis.get("summary", ""),
        "completion": diagnosis.get("completion", {}),
        "repair": diagnosis.get("repair", {}),
        "deficiencies": rows[:12],
    }


def _deficiencies_from_guard(guard_report: Mapping[str, Any], missing: list[str]) -> list[dict[str, Any]]:
    issues = guard_report.get("issues")
    rows = [item for item in issues if isinstance(item, Mapping)] if isinstance(issues, list) else []
    deficiencies: list[dict[str, Any]] = []
    for issue in rows:
        code = str(issue.get("code") or "guard_issue").strip()
        severity = _guard_severity(str(issue.get("severity") or "warning"))
        category = _issue_category(code)
        fix = _suggested_fix_for_issue(code, missing)
        deficiencies.append(
            _deficiency(
                severity,
                category,
                code,
                str(issue.get("message") or code),
                fix,
                evidence={"guard_severity": issue.get("severity", "")},
            )
        )
    return _dedupe_deficiencies(deficiencies)


def _deficiencies_from_code_review(review: Any) -> list[dict[str, Any]]:
    if not isinstance(review, Mapping):
        return []
    findings = review.get("findings")
    rows = [item for item in findings if isinstance(item, Mapping)] if isinstance(findings, list) else []
    deficiencies: list[dict[str, Any]] = []
    for finding in rows[:20]:
        code = str(finding.get("code") or "").strip().lower()
        text = " ".join(
            str(finding.get(key) or "")
            for key in ("code", "message", "rationale", "recommendation")
        ).lower()
        if any(token in text for token in ("duplicate", "duplicated", "multiple pipeline", "inconsistent")):
            deficiencies.append(
                _deficiency(
                    "major",
                    "implementation",
                    "duplicated_or_inconsistent_pipeline",
                    str(finding.get("message") or "Generated code review found duplicated or inconsistent logic."),
                    "Collapse duplicated pipelines into one entrypoint and make the result schema the single source of truth.",
                    evidence={"review_code": code},
                )
            )
        elif any(token in text for token in ("metric", "schema", "result")):
            deficiencies.append(
                _deficiency(
                    "major",
                    "metrics",
                    "metric_wiring_risk",
                    str(finding.get("message") or "Generated code review found metric/result wiring risk."),
                    "Ensure the benchmark prints every required metric with stable parseable names.",
                    evidence={"review_code": code},
                )
            )
    return _dedupe_deficiencies(deficiencies)


def _deficiency(
    severity: str,
    category: str,
    code: str,
    message: str,
    suggested_fix: str,
    *,
    evidence: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "severity": severity,
        "category": category,
        "code": code,
        "message": message,
        "suggested_fix": suggested_fix,
        "evidence": dict(evidence or {}),
    }


def _required_metrics(schema: Mapping[str, Any]) -> list[str]:
    raw = schema.get("required_metrics")
    names = [str(item) for item in raw if str(item).strip()] if isinstance(raw, list) else []
    primary = str(schema.get("primary_metric") or "").strip()
    if primary and primary not in names:
        names.insert(0, primary)
    return names


def _guard_severity(value: str) -> str:
    if value == "error":
        return "critical"
    if value == "warning":
        return "major"
    return "info"


def _issue_category(code: str) -> str:
    if "metric" in code:
        return "metrics"
    if "timeout" in code or "returncode" in code or "stdout" in code:
        return "runtime"
    if "review" in code or "generation" in code or "repair" in code or "repaired" in code:
        return "implementation"
    if "comparison" in code or "verdict" in code:
        return "evaluation"
    return "design"


def _suggested_fix_for_issue(code: str, missing: list[str]) -> str:
    if code in {"missing_primary_metric", "missing_required_metric", "missing_metrics"}:
        target = ", ".join(missing) if missing else "the declared metrics"
        return f"Wire the experiment runner to emit parseable values for: {target}."
    if code == "nonfinite_metric":
        return "Clamp or handle invalid numerical values before writing metrics."
    if code == "timeout":
        return "Reduce the workload or make the run budget explicit before increasing the timeout."
    if code == "nonzero_returncode":
        return "Inspect stderr and fix the failing entrypoint before rerunning."
    if code == "code_review_warning":
        return "Inspect code_review.json and repair warnings that affect validity or maintainability."
    if code == "code_generation_recovered":
        return "Treat fallback-generated code as a baseline scaffold and request a stronger implementation pass."
    if code == "experiment_repaired":
        return "Disclose the repair context and prefer a semantic code repair before making substantive performance claims."
    return "Review this issue before making unqualified experiment claims."


def _suggested_actions(deficiencies: list[dict[str, Any]], local_repair_supported: bool) -> list[str]:
    if not deficiencies:
        return ["Proceed to report with bounded claims and cite the guard status."]
    actions: list[str] = []
    if local_repair_supported:
        actions.append("Run the bounded local repair to satisfy declared metric outputs, then rerun the experiment.")
    if any(item.get("category") == "runtime" for item in deficiencies):
        actions.append("Inspect stderr/stdout tails and fix the entrypoint before evaluating research claims.")
    if any(item.get("category") == "implementation" for item in deficiencies):
        actions.append("Use generated-code inspection tools to locate duplicated, inconsistent, or fallback code.")
    if any(item.get("category") == "evaluation" for item in deficiencies):
        actions.append("Keep report conclusions conditional until comparison verdicts are stronger.")
    return actions or ["Review deficiencies before continuing."]


def _diagnosis_status(deficiencies: list[dict[str, Any]], guard_report: Mapping[str, Any]) -> str:
    if any(item.get("severity") == "critical" for item in deficiencies):
        return "failed"
    if any(item.get("severity") == "major" for item in deficiencies):
        return "warning"
    return str(guard_report.get("status") or "passed")


def _summary_text(status: str, deficiencies: list[dict[str, Any]], missing: list[str]) -> str:
    if status == "passed":
        return "Run artifacts passed local guard checks."
    if missing:
        return "Run is missing declared metric outputs: " + ", ".join(missing) + "."
    critical = [item for item in deficiencies if item.get("severity") == "critical"]
    if critical:
        return str(critical[0].get("message") or "Run has critical deficiencies.")
    return "Run has warnings that should constrain downstream claims."


def _dedupe_deficiencies(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str]] = set()
    result: list[dict[str, Any]] = []
    for row in rows:
        key = (str(row.get("category")), str(row.get("code")))
        if key in seen:
            continue
        seen.add(key)
        result.append(row)
    return result


def _has_code(rows: list[dict[str, Any]], code: str) -> bool:
    return any(item.get("code") == code for item in rows)


def _join_list(value: Any) -> str:
    if not isinstance(value, list) or not value:
        return "`none`"
    return ", ".join(f"`{item}`" for item in value)


def _cell(value: Any) -> str:
    return str(value or "").replace("|", "\\|").replace("\n", " ")


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
