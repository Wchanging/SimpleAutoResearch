from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


MetricDirection = Literal["higher", "lower", "resource", "ignore", "unknown"]
ClaimVerdict = Literal["supported", "partially_supported", "unsupported", "not_evaluated"]
Confidence = Literal["high", "medium", "low"]
AnalysisStatus = Literal["passed", "failed", "incomplete", "blocked", "metric_below_target"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore")


class AnalysisMetric(_Model):
    name: str
    value: float | None = None
    direction: MetricDirection = "unknown"
    present: bool = True
    issues: list[str] = Field(default_factory=list)


class AnalysisClaim(_Model):
    claim_id: str
    claim: str
    verdict: ClaimVerdict = "not_evaluated"
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    metric_refs: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    confidence: Confidence = "low"


class AnalysisAudit(_Model):
    llm_used: bool = False
    missing_required_metrics: list[str] = Field(default_factory=list)
    weak_metric_signals: list[str] = Field(default_factory=list)
    unsupported_claims: list[str] = Field(default_factory=list)
    downgraded_claims: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class AnalysisContext(_Model):
    task_id: str = ""
    title: str = ""
    research_question: str = ""
    hypotheses: list[dict[str, Any]] = Field(default_factory=list)
    criteria: list[dict[str, Any]] = Field(default_factory=list)
    expected_metrics: list[dict[str, Any]] = Field(default_factory=list)
    metric_directions: dict[str, MetricDirection] = Field(default_factory=dict)
    metrics: dict[str, float] = Field(default_factory=dict)
    project_results: dict[str, Any] = Field(default_factory=dict)
    task_contract: dict[str, Any] = Field(default_factory=dict)
    existing_writeup: str = ""
    artifacts: dict[str, str] = Field(default_factory=dict)
    run_dir: str = ""
    benchmark: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class AnalysisResult(_Model):
    readme_markdown: str
    status: AnalysisStatus = "incomplete"
    status_reasons: list[str] = Field(default_factory=list)
    claims: list[AnalysisClaim] = Field(default_factory=list)
    claims_payload: dict[str, Any] = Field(default_factory=dict)
    metric_summary: dict[str, Any] = Field(default_factory=dict)
    rubric_coverage: list[dict[str, Any]] = Field(default_factory=list)
    audit: AnalysisAudit = Field(default_factory=AnalysisAudit)
    raw_llm_response: dict[str, Any] | None = None
