"""Continue an existing research session into an auditable report.

The application boundary accepts explicit section drafts and report state. It
reuses the existing report assembler and audit capabilities, so report
generation policy remains replaceable and the session controller only owns
attempt lineage, transitions, and budget accounting.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping

from simple_ar.core import (
    ArtifactRef,
    ArtifactStore,
    AttemptManifest,
    CapabilityRegistry,
    DecisionRecord,
    SessionController,
)
from simple_ar.integrations.llm import LLMClient
from simple_ar.report.agent import run_report_agent
from simple_ar.report.audit import (
    ReportAudit,
    ReportAuditCapabilityRequest,
)
from simple_ar.report.capability import ReportAssemblyRequest
from simple_ar.report.schema import (
    ReportContext,
    ReportDocumentPlan,
    ReportMemory,
    ReportRuntimeConfig,
    ReportSectionDraft,
    ReportTemplateBundle,
    ClaimEvidenceRecord,
    MetricSource,
    SourceHandle,
)
from simple_ar.research.registry import register_research_capabilities
from simple_ar.report.tool_gateway import ReportToolGateway

if TYPE_CHECKING:
    from simple_ar.app.research_session import ResearchSessionResult


@dataclass(frozen=True, slots=True)
class ResearchReportSessionRequest:
    """Explicit report inputs for an existing capability session."""

    session_root: Path
    title: str
    sections: tuple[ReportSectionDraft | Mapping[str, Any], ...]
    context: ReportContext | Mapping[str, Any]
    memory: ReportMemory | Mapping[str, Any] = field(default_factory=ReportMemory)
    source_refs: tuple[ArtifactRef, ...] = ()
    config: Mapping[str, Any] = field(default_factory=dict)
    document_plan: ReportDocumentPlan | Mapping[str, Any] | None = None
    template_name: str = ""

    def __post_init__(self) -> None:
        if not str(self.session_root).strip():
            raise ValueError("ResearchReportSessionRequest.session_root is required.")
        if not self.title.strip():
            raise ValueError("ResearchReportSessionRequest.title cannot be empty.")
        if not self.sections:
            raise ValueError("ResearchReportSessionRequest.sections cannot be empty.")
        object.__setattr__(self, "session_root", Path(self.session_root))
        object.__setattr__(
            self,
            "sections",
            tuple(
                section
                if isinstance(section, ReportSectionDraft)
                else dict(section)
                for section in self.sections
            ),
        )
        object.__setattr__(self, "source_refs", tuple(self.source_refs))
        object.__setattr__(self, "config", dict(self.config))
        if self.document_plan is not None:
            object.__setattr__(
                self,
                "document_plan",
                self.document_plan
                if isinstance(self.document_plan, ReportDocumentPlan)
                else dict(self.document_plan),
            )


@dataclass(frozen=True, slots=True)
class ResearchReportSessionResult:
    """Report and audit outputs appended to an existing session."""

    session_root: Path
    report_ref: ArtifactRef
    audit_ref: ArtifactRef
    audit: ReportAudit
    report_status: str
    audit_status: str
    attempts: tuple[AttemptManifest, ...]
    decisions: tuple[DecisionRecord, ...]
    writer_ref: ArtifactRef | None = None

    @property
    def status(self) -> str:
        if self.report_status == "completed" and self.audit_status == "passed":
            return "completed"
        if self.audit_status == "failed" or self.report_status == "failed":
            return "failed"
        return "partial"


class ResearchReportSessionError(RuntimeError):
    """Raised when an existing session cannot accept a report continuation."""


def build_research_session_report_inputs(
    session: "ResearchSessionResult",
) -> tuple[ReportContext, ReportMemory]:
    """Derive compact report inputs from one completed research session.

    The derivation is intentionally deterministic. It exposes the persisted
    brief, literature metadata, execution result, and analysis claims to the
    existing report agent without copying private stage directories or adding
    a second report-specific synthesis path.
    """

    design = getattr(session, "design", None)
    contract = (
        design.contract
        if design is not None and design.contract is not None
        else session.brief.experiment_contract
    )
    if contract is None:
        raise ResearchReportSessionError(
            "Research session has no experiment contract; report input is incomplete."
        )
    topic = (session.plan.query_plan.topic or "").strip()
    if not topic:
        raise ResearchReportSessionError(
            "Research session has no topic in its persisted research plan."
        )
    execution = dict(session.execution)
    source_handles = _research_source_handles(session)
    metric_sources = _research_metric_sources(session)
    synthesis_markdown = _synthesis_markdown(session.brief)
    evidence_summary = (
        f"Search returned {len(session.search.papers)} paper record(s); "
        f"document ingest retained {len(session.documents.records)} document(s) and "
        f"{len(session.documents.chunks)} text chunk(s). "
        f"Execution status={execution.get('status', 'unknown')}; "
        f"analysis status={session.analysis.status}."
    )
    context = ReportContext(
        topic=topic,
        report_mode="experiment",
        synthesis_markdown=synthesis_markdown,
        hypothesis_markdown=contract.hypothesis,
        evidence_summary=evidence_summary,
        search_meta={
            "status": session.search.status,
            "paper_count": len(session.search.papers),
            "response_count": len(session.search.responses),
            "diagnostics": list(session.search.diagnostics),
        },
        experiment_plan=contract.to_row(),
        results=execution,
        papers=[paper.to_row() for paper in session.search.papers],
        source_handles=source_handles,
        metric_sources=metric_sources,
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
        limitations=[
            *session.analysis.audit.limitations,
            *contract.risks,
        ],
        key_decisions=[
            f"execution_status={execution.get('status', 'unknown')}",
            f"analysis_status={session.analysis.status}",
            *session.analysis.status_reasons,
        ],
    )
    return context, memory


def run_research_session_report_agent(
    session: "ResearchSessionResult",
    *,
    template: ReportTemplateBundle,
    config: ReportRuntimeConfig | Mapping[str, Any],
    client: LLMClient,
    gateway: ReportToolGateway | None = None,
    emit: Callable[[str], None] | None = None,
) -> ResearchReportSessionResult:
    """Run the existing report agent on a research-session handoff.

    This is the narrow product-facing bridge for the literature-to-report
    path. The caller still chooses the template, runtime budget, and LLM
    client; report writing, review, assembly, and audit remain shared code.
    """

    context, memory = build_research_session_report_inputs(session)
    return run_research_report_agent_session(
        session_root=session.session_root,
        context=context,
        memory=memory,
        template=template,
        config=config,
        client=client,
        gateway=gateway,
        source_refs=tuple(
            ref
            for ref in (
                session.brief_ref,
                getattr(session, "design_ref", None),
                session.execution_ref,
                session.analysis_ref,
            )
            if ref is not None
        ),
        emit=emit,
    )


def run_research_report_agent_session(
    *,
    session_root: Path,
    context: ReportContext | Mapping[str, Any],
    memory: ReportMemory | Mapping[str, Any],
    template: ReportTemplateBundle,
    config: ReportRuntimeConfig | Mapping[str, Any],
    client: LLMClient,
    gateway: ReportToolGateway | None = None,
    source_refs: tuple[ArtifactRef, ...] = (),
    emit: Callable[[str], None] | None = None,
) -> ResearchReportSessionResult:
    """Use the existing Writer/Reviewer agent before generic report assembly.

    This is a thin application adapter: ``report.agent`` remains the only
    prose-generation implementation, while ``research_report`` remains the
    only report/audit session boundary.  The writer trace is stored as an
    input to the report attempt so it is inspectable without duplicating the
    final Markdown body.
    """

    root = Path(session_root)
    if not (root / "session_manifest.json").is_file():
        raise ResearchReportSessionError(
            f"Research session manifest not found: {root / 'session_manifest.json'}"
        )
    if client is None:
        raise ResearchReportSessionError("An LLM client is required for agent report generation.")
    report_context = (
        context
        if isinstance(context, ReportContext)
        else ReportContext.model_validate(context)
    )
    report_memory = (
        memory
        if isinstance(memory, ReportMemory)
        else ReportMemory.model_validate(memory)
    )
    report_config = (
        config
        if isinstance(config, ReportRuntimeConfig)
        else ReportRuntimeConfig.model_validate(config)
    )
    agent_result = run_report_agent(
        client=client,
        context=report_context,
        template=template,
        memory=report_memory,
        config=report_config,
        gateway=gateway or ReportToolGateway(report_context),
        emit=emit,
    )
    if agent_result is None:
        raise ResearchReportSessionError(
            "Report agent did not return a validated result; no report was assembled."
        )
    sections = tuple(agent_result.sections)
    if not sections or not any(section.draft_markdown.strip() for section in sections):
        raise ResearchReportSessionError(
            "Report agent returned no non-empty section drafts; no report was assembled."
        )

    writer_ref = ArtifactStore(root).write_json(
        "inputs/report_agent_result.json",
        _report_agent_handoff(agent_result),
        kind="report_writer_result",
        schema="report_agent_result.v1",
        producer="report.agent",
    )
    result = run_research_report_session(
        ResearchReportSessionRequest(
            session_root=root,
            title=report_context.topic,
            sections=sections,
            context=report_context,
            memory=agent_result.memory,
            source_refs=tuple((*source_refs, writer_ref)),
            config=report_config,
            document_plan=agent_result.memory.document_plan,
            template_name=template.name,
        )
    )
    return replace(result, writer_ref=writer_ref)


def _report_agent_handoff(result: Any) -> dict[str, Any]:
    """Persist the writer trace without copying the assembled report body."""

    payload = result.model_dump(mode="json")
    payload.pop("report_body", None)
    payload["schema_version"] = "report_agent_result.v1"
    return payload


def _research_source_handles(session: "ResearchSessionResult") -> list[SourceHandle]:
    handles = [
        SourceHandle(
            handle="artifact:research_brief",
            kind="research_brief",
            artifact=session.brief_ref.path,
            summary="Evidence-derived research direction and experiment contract.",
        ),
        SourceHandle(
            handle="artifact:experiment_execution",
            kind="experiment",
            artifact=session.execution_ref.path,
            summary=f"Execution status: {session.execution.get('status', 'unknown')}.",
        ),
        SourceHandle(
            handle="artifact:result_analysis",
            kind="analysis",
            artifact=session.analysis_ref.path,
            summary=f"Analysis status: {session.analysis.status}.",
        ),
    ]
    design_ref = getattr(session, "design_ref", None)
    if design_ref is not None:
        handles.insert(
            1,
            SourceHandle(
                handle="artifact:research_design",
                kind="research_design",
                artifact=design_ref.path,
                summary="Selected research direction and executable contract.",
            ),
        )
    handles.extend(
        SourceHandle(
            handle=f"paper:{paper.id}",
            kind="paper",
            citation_key=paper.id,
            paper_id=paper.id,
            title=paper.title,
            metadata={
                "source": paper.source,
                "source_id": paper.source_id,
                "url": paper.url,
                "published": paper.published,
            },
        )
        for paper in session.search.papers
    )
    return handles


def _research_metric_sources(session: "ResearchSessionResult") -> list[MetricSource]:
    return metric_sources_from_execution(
        session.execution,
        artifact=session.execution_ref.path,
    )


def metric_sources_from_execution(
    execution: Mapping[str, Any],
    *,
    artifact: str,
) -> list[MetricSource]:
    """Convert candidate/baseline metrics into report provenance rows."""

    result_schema = execution.get("result_schema")
    directions = result_schema.get("metric_directions", {}) if isinstance(result_schema, Mapping) else {}
    directions = directions if isinstance(directions, Mapping) else {}
    rows: list[MetricSource] = []
    for label, values in _metric_groups(execution):
        for name, value in values.items():
            if isinstance(value, bool) or not isinstance(value, (int, float, str)):
                continue
            rows.append(
                MetricSource(
                    metric_id=f"metric:{label}:{name}",
                    name=str(name),
                    value=value,
                    artifact=artifact,
                    label=label,
                    direction=str(directions.get(name) or ""),
                )
            )
    return rows


def _metric_groups(execution: Mapping[str, Any]) -> list[tuple[str, Mapping[str, Any]]]:
    groups: list[tuple[str, Mapping[str, Any]]] = []
    metrics = execution.get("metrics")
    if isinstance(metrics, Mapping):
        groups.append(("candidate", metrics))
    baseline = execution.get("baseline")
    baseline_metrics = baseline.get("metrics") if isinstance(baseline, Mapping) else None
    if isinstance(baseline_metrics, Mapping):
        groups.append(("baseline", baseline_metrics))
    return groups


def claim_evidence_record_from_analysis(claim: Any) -> ClaimEvidenceRecord:
    verdict = str(getattr(claim, "verdict", "not_evaluated"))
    status = {
        "supported": "supported",
        "partially_supported": "partially_supported",
        "unsupported": "unsupported",
    }.get(verdict, "speculative")
    return ClaimEvidenceRecord(
        claim_id=str(getattr(claim, "claim_id", "claim")),
        claim=str(getattr(claim, "claim", "")),
        status=status,  # type: ignore[arg-type]
        evidence_handles=evidence_handles_from_claim(getattr(claim, "evidence", ())),
        metric_ids=[str(item) for item in getattr(claim, "metric_refs", ())],
        notes=f"Analysis verdict: {verdict}.",
    )


def evidence_handles_from_claim(value: object) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    handles: list[str] = []
    for item in value:
        if isinstance(item, Mapping):
            for key in ("handle", "ref", "artifact", "source"):
                candidate = str(item.get(key) or "").strip()
                if candidate:
                    handles.append(candidate)
                    break
        elif str(item).strip():
            handles.append(str(item).strip())
    return list(dict.fromkeys(handles))


def _synthesis_markdown(synthesis: Any) -> str:
    lines = [f"Gap summary: {synthesis.gap_summary}".strip()]
    for idea in synthesis.ideas:
        lines.append(
            f"{idea.idea_id} {idea.title}: {idea.hypothesis} "
            f"Proposed change: {idea.proposed_change}"
        )
    return "\n".join(line for line in lines if line.strip())


def run_research_report_session(
    request: ResearchReportSessionRequest,
) -> ResearchReportSessionResult:
    """Append report assembly and audit attempts to an existing session."""

    registry = CapabilityRegistry()
    register_research_capabilities(
        registry,
        names=("report", "report_audit"),
    )
    try:
        controller = SessionController.load(request.session_root, registry=registry)
    except (FileNotFoundError, OSError, TypeError, ValueError) as exc:
        raise ResearchReportSessionError(
            f"Could not load research session {request.session_root}: {exc}"
        ) from exc

    report_request = ReportAssemblyRequest(
        title=request.title,
        sections=request.sections,
        config=request.config,
        document_plan=request.document_plan,
        template_name=request.template_name,
    )
    try:
        controller.execute(
            "report",
            attempt_id="report-001",
            inputs=request.source_refs,
            next_capability="report_audit",
            request=report_request,
        )
        report_ref = controller.attempt_output_ref(
            "report-001",
            kind="report",
            schema="report.v1",
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
        audit_result, _ = controller.execute(
            "report_audit",
            attempt_id="report-audit-001",
            inputs=(report_ref,),
            request=ReportAuditCapabilityRequest(
                report_ref=report_ref,
                context=report_context,
                memory=report_memory,
            ),
        )
        audit_ref = controller.attempt_output_ref(
            "report-audit-001",
            kind="report_audit",
            schema="report_audit.v1",
        )
        audit_payload = controller.store.read_json(audit_ref)
        if not isinstance(audit_payload, Mapping):
            raise ResearchReportSessionError(
                f"Report audit output is not a JSON object; inspect {request.session_root}."
            )
        audit = ReportAudit.model_validate(audit_payload)
    except ResearchReportSessionError:
        raise
    except (KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
        raise ResearchReportSessionError(
            f"Could not complete report continuation in {request.session_root}: {exc}"
        ) from exc

    report_attempt = _attempt(controller, "report-001")
    return ResearchReportSessionResult(
        session_root=request.session_root,
        report_ref=report_ref,
        audit_ref=audit_ref,
        audit=audit,
        report_status=_result_status(controller, "report-001", report_attempt),
        audit_status=audit.status,
        attempts=controller.list_attempts(),
        decisions=tuple(controller.manifest.decisions),
    )


def _attempt(controller: SessionController, attempt_id: str) -> AttemptManifest | None:
    return next(
        (attempt for attempt in controller.list_attempts() if attempt.attempt_id == attempt_id),
        None,
    )


def _result_status(
    controller: SessionController,
    attempt_id: str,
    attempt: AttemptManifest | None,
) -> str:
    decision = next(
        (
            item
            for item in reversed(controller.manifest.decisions)
            if item.attempt_id == attempt_id
        ),
        None,
    )
    if decision is not None:
        return decision.result_status
    return attempt.status if attempt is not None else "unknown"


__all__ = [
    "ResearchReportSessionError",
    "ResearchReportSessionRequest",
    "ResearchReportSessionResult",
    "build_research_session_report_inputs",
    "claim_evidence_record_from_analysis",
    "evidence_handles_from_claim",
    "metric_sources_from_execution",
    "run_research_session_report_agent",
    "run_research_report_agent_session",
    "run_research_report_session",
]
