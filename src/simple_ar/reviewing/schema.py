from __future__ import annotations

from typing import Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field


ReviewSeverity = Literal["blocking", "warning", "info"]


class ReviewFinding(BaseModel):
    """Structured reviewer finding shared by local and external reviewers."""

    model_config = ConfigDict(extra="ignore")

    key: str = ""
    severity: ReviewSeverity = "info"
    category: str = "general"
    summary: str
    evidence: list[str] = Field(default_factory=list)
    recommendation: str = ""
    source: str = "reviewer"


class ReviewReport(BaseModel):
    """A compact review report suitable for artifacts and task memory."""

    model_config = ConfigDict(extra="ignore")

    schema_version: str = "review_report.v1"
    reviewer: str
    subject: str
    status: Literal["passed", "warning", "failed"]
    findings: list[ReviewFinding] = Field(default_factory=list)
    summary: dict[str, int] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


def normalize_review_findings(
    rows: object,
    *,
    source: str,
    default_category: str = "general",
    default_evidence: list[str] | None = None,
    max_findings: int = 24,
) -> list[ReviewFinding]:
    findings: list[ReviewFinding] = []
    if not isinstance(rows, list):
        return findings
    for index, row in enumerate(rows[:max_findings]):
        if not isinstance(row, Mapping):
            continue
        summary = _first_text(row, "summary", "message", "finding", "description")
        if not summary:
            continue
        category = _first_text(row, "category", "code", "type") or default_category
        severity = normalize_severity(row.get("severity"))
        evidence = _string_list(row.get("evidence")) or list(default_evidence or [])
        recommendation = _first_text(row, "recommendation", "suggestion", "fix", "action")
        key = _first_text(row, "key", "id") or f"{source}:{category}:{index}"
        findings.append(
            ReviewFinding(
                key=key,
                severity=severity,
                category=category,
                summary=_clip(summary, 700),
                evidence=evidence[:12],
                recommendation=_clip(recommendation, 500),
                source=source,
            )
        )
    return findings


def review_report(
    *,
    reviewer: str,
    subject: str,
    findings: list[ReviewFinding],
    metadata: dict[str, Any] | None = None,
) -> ReviewReport:
    blocking = sum(1 for row in findings if row.severity == "blocking")
    warnings = sum(1 for row in findings if row.severity == "warning")
    infos = sum(1 for row in findings if row.severity == "info")
    status: Literal["passed", "warning", "failed"] = "passed"
    if blocking:
        status = "failed"
    elif warnings:
        status = "warning"
    return ReviewReport(
        reviewer=reviewer,
        subject=subject,
        status=status,
        findings=findings,
        summary={
            "blocking_count": blocking,
            "warning_count": warnings,
            "info_count": infos,
            "finding_count": len(findings),
        },
        metadata=metadata or {},
    )


def normalize_severity(value: object) -> ReviewSeverity:
    text = str(value or "").strip().lower()
    if text in {"blocking", "error", "failed", "failure", "critical", "fatal"}:
        return "blocking"
    if text in {"warning", "warn", "risk", "major", "minor"}:
        return "warning"
    return "info"


def _first_text(row: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = row.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _clip(text: str, limit: int) -> str:
    clean = " ".join(str(text).split())
    if len(clean) <= limit:
        return clean
    return clean[: max(0, limit - 3)].rstrip() + "..."
