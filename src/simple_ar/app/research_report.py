"""Continue an existing research session into an auditable report.

The application boundary accepts explicit section drafts and report state. It
reuses the existing report assembler and audit capabilities, so report
generation policy remains replaceable and the session controller only owns
attempt lineage, transitions, and budget accounting.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from simple_ar.core import (
    ArtifactRef,
    AttemptManifest,
    CapabilityRegistry,
    DecisionRecord,
    SessionController,
)
from simple_ar.report.audit import (
    ReportAudit,
    ReportAuditCapabilityRequest,
)
from simple_ar.report.capability import ReportAssemblyRequest
from simple_ar.report.schema import (
    ReportContext,
    ReportDocumentPlan,
    ReportMemory,
    ReportSectionDraft,
)
from simple_ar.research.registry import register_research_capabilities


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

    @property
    def status(self) -> str:
        if self.report_status == "completed" and self.audit_status == "passed":
            return "completed"
        if self.audit_status == "failed" or self.report_status == "failed":
            return "failed"
        return "partial"


class ResearchReportSessionError(RuntimeError):
    """Raised when an existing session cannot accept a report continuation."""


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
    "run_research_report_session",
]
