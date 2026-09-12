"""Read-only report evidence projection for historical Code-Task sessions."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from simple_ar.app.research_code_task import ResearchCodeTaskSessionResult
from simple_ar.report.projection import claim_evidence_record_from_analysis, metric_sources_from_execution
from simple_ar.report.schema import ReportContext, ReportMemory, SourceHandle


def build_code_task_report_inputs(
    session: ResearchCodeTaskSessionResult,
) -> tuple[ReportContext, ReportMemory]:
    """Build compact report context and memory from real session evidence."""

    contract = (
        session.design.contract
        if session.design is not None and session.design.contract is not None
        else session.synthesis.experiment_contract
    )
    if contract is None:
        raise ValueError("Code-task session has no experiment contract.")

    execution = dict(session.execution)
    source_handles = _source_handles(session, execution)
    metric_sources = metric_sources_from_execution(
        execution,
        artifact=session.execution_ref.path,
    )
    context = ReportContext(
        topic=session.topic,
        report_mode="experiment",
        hypothesis_markdown=contract.hypothesis,
        evidence_summary="Execution and result-analysis evidence from one Code-Task session.",
        experiment_plan=contract.to_row(),
        results=execution,
        source_handles=source_handles,
        metric_sources=metric_sources,
        max_section_sources=8,
    )
    memory = ReportMemory(
        objective=contract.hypothesis,
        template="experiment",
        report_mode="experiment",
        claims_evidence_matrix=[
            claim_evidence_record_from_analysis(claim)
            for claim in session.analysis.claims
        ],
        source_handles=source_handles,
        metric_sources=metric_sources,
        limitations=[*session.analysis.audit.limitations, *contract.risks],
        key_decisions=[
            f"analysis_status={session.analysis.status}",
            *session.analysis.status_reasons,
        ],
    )
    return context, memory



def _source_handles(
    session: ResearchCodeTaskSessionResult,
    execution: Mapping[str, Any],
) -> list[SourceHandle]:
    metrics = execution.get("metrics")
    metrics = metrics if isinstance(metrics, Mapping) else {}
    return [
        SourceHandle(
            handle="artifact:code_task_execution",
            kind="experiment",
            title="Code-Task execution result",
            artifact=session.execution_ref.path,
            summary=(
                f"status={execution.get('status', 'unknown')}; "
                f"metrics={_metric_summary(metrics)}"
            ),
            metadata={"status": execution.get("status"), "metrics": dict(metrics)},
        ),
        SourceHandle(
            handle="artifact:code_task_analysis",
            kind="analysis",
            title="Code-Task result analysis",
            artifact=session.analysis_ref.path,
            summary=(
                f"status={session.analysis.status}; "
                f"claims={len(session.analysis.claims)}"
            ),
            metadata={
                "status": session.analysis.status,
                "status_reasons": session.analysis.status_reasons,
            },
        ),
    ]


def _metric_summary(metrics: Mapping[str, Any]) -> str:
    if not metrics:
        return "none"
    return ", ".join(f"{name}={value}" for name, value in list(metrics.items())[:8])
