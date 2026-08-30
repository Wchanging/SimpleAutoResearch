"""Bridge an analyzed Code-Task session into the generic report boundary.

The adapter derives report context from persisted execution and analysis
evidence, then delegates assembly and audit to ``research_report``. It does
not write prose, invent metrics, or create a Code-Task-specific report path.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from simple_ar.app.research_code_task import (
    ResearchCodeTaskSessionError,
    ResearchCodeTaskSessionRequest,
    ResearchCodeTaskSessionResult,
    run_research_code_task_session,
)
from simple_ar.integrations.llm import LLMClient
from simple_ar.app.research_report import (
    ResearchReportSessionRequest,
    ResearchReportSessionResult,
    claim_evidence_record_from_analysis,
    metric_sources_from_execution,
    run_research_report_agent_session,
    run_research_report_session,
)
from simple_ar.report.schema import (
    ClaimEvidenceRecord,
    MetricSource,
    ReportContext,
    ReportDocumentPlan,
    ReportMemory,
    ReportRuntimeConfig,
    ReportSectionDraft,
    ReportTemplateBundle,
    SourceHandle,
)
from simple_ar.report.tool_gateway import ReportToolGateway


@dataclass(frozen=True, slots=True)
class ResearchCodeTaskReportRequest:
    """Inputs for one Code-Task execution followed by report assembly."""

    code_task: ResearchCodeTaskSessionRequest
    title: str
    sections: tuple[ReportSectionDraft | Mapping[str, Any], ...]
    config: Mapping[str, Any] = field(default_factory=dict)
    document_plan: ReportDocumentPlan | Mapping[str, Any] | None = None
    template_name: str = ""

    def __post_init__(self) -> None:
        if not self.title.strip():
            raise ValueError("ResearchCodeTaskReportRequest.title cannot be empty.")
        if not self.sections:
            raise ValueError("ResearchCodeTaskReportRequest.sections cannot be empty.")
        object.__setattr__(self, "sections", tuple(self.sections))
        object.__setattr__(self, "config", dict(self.config))


def build_code_task_report_inputs(
    session: ResearchCodeTaskSessionResult,
) -> tuple[ReportContext, ReportMemory]:
    """Build compact report context and memory from real session evidence."""

    contract = session.synthesis.experiment_contract
    if contract is None:
        raise ValueError("Code-task session has no experiment contract.")

    execution = dict(session.execution)
    source_handles = _source_handles(session, execution)
    metric_sources = _metric_sources(session, execution)
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
        claims_evidence_matrix=[_claim_record(claim) for claim in session.analysis.claims],
        source_handles=source_handles,
        metric_sources=metric_sources,
        limitations=[*session.analysis.audit.limitations, *contract.risks],
        key_decisions=[
            f"analysis_status={session.analysis.status}",
            *session.analysis.status_reasons,
        ],
    )
    return context, memory


def run_research_code_task_report_session(
    request: ResearchCodeTaskReportRequest,
) -> ResearchReportSessionResult:
    """Run Code-Task, then append generic Report and Report Audit attempts."""

    session = run_research_code_task_session(request.code_task, next_capability="report")
    context, memory = build_code_task_report_inputs(session)
    return run_research_report_session(
        ResearchReportSessionRequest(
            session_root=session.session_root,
            title=request.title,
            sections=request.sections,
            context=context,
            memory=memory,
            source_refs=(session.execution_ref, session.analysis_ref),
            config=request.config,
            document_plan=request.document_plan,
            template_name=request.template_name,
        )
    )


def run_research_code_task_report_agent(
    session: ResearchCodeTaskSessionResult,
    *,
    title: str,
    template: ReportTemplateBundle,
    config: ReportRuntimeConfig | Mapping[str, Any],
    client: LLMClient,
    gateway: ReportToolGateway | None = None,
    emit: Callable[[str], None] | None = None,
) -> ResearchReportSessionResult:
    """Continue an explicitly report-ready Code-Task session into Report/Audit.

    The Code-Task session must have been opened with ``next_capability="report"``.
    This keeps a closed session closed and makes the continuation decision
    visible in its persisted decision history.
    """

    if not session.decisions or session.decisions[-1].next_capability != "report":
        raise ResearchCodeTaskSessionError(
            "Code-task session did not leave the report continuation open. "
            "Run it with next_capability='report' before restoring it."
        )
    context, memory = build_code_task_report_inputs(session)
    return run_research_report_agent_session(
        session_root=session.session_root,
        context=context,
        memory=memory,
        template=template,
        config=config,
        client=client,
        gateway=gateway,
        source_refs=(session.execution_ref, session.analysis_ref),
        emit=emit,
    )


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


def _metric_sources(
    session: ResearchCodeTaskSessionResult,
    execution: Mapping[str, Any],
) -> list[MetricSource]:
    return metric_sources_from_execution(
        execution,
        artifact=session.execution_ref.path,
    )


def _claim_record(claim: Any) -> ClaimEvidenceRecord:
    return claim_evidence_record_from_analysis(claim)


def _metric_summary(metrics: Mapping[str, Any]) -> str:
    if not metrics:
        return "none"
    return ", ".join(f"{name}={value}" for name, value in list(metrics.items())[:8])


__all__ = [
    "ResearchCodeTaskReportRequest",
    "build_code_task_report_inputs",
    "run_research_code_task_report_agent",
    "run_research_code_task_report_session",
]
