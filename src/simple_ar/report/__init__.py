"""Report generation, source backtracking, and audit helpers."""

from simple_ar.report.audit import (
    ReportAuditCapabilityRequest,
    ReportAuditRequest,
    audit_report,
    build_report_audit,
    run_report_audit_capability,
)
from simple_ar.report.capability import (
    ReportAssemblyRequest,
    ReportAssemblyResult,
    assemble_report_document,
    run_report_capability,
)
from simple_ar.report.context import build_report_context
from simple_ar.report.memory import initialize_report_memory
from simple_ar.report.ports import DeterministicFigureRenderer, FigureRenderer
from simple_ar.report.schema import (
    AgentReportResult,
    ReportAudit,
    ReportContext,
    ReportIterationRecord,
    ReportMemory,
    ReportRuntimeConfig,
    ReportSectionDraft,
    ReportSectionReview,
    ReportTemplateBundle,
    ReportToolCall,
    ReportToolResult,
)
from simple_ar.report.templates import load_report_template_bundle
from simple_ar.report.tool_gateway import ReportToolGateway

__all__ = [
    "AgentReportResult",
    "ReportAudit",
    "ReportContext",
    "ReportIterationRecord",
    "ReportMemory",
    "ReportRuntimeConfig",
    "ReportSectionDraft",
    "ReportSectionReview",
    "ReportTemplateBundle",
    "ReportToolCall",
    "ReportToolGateway",
    "ReportToolResult",
    "build_report_audit",
    "ReportAuditCapabilityRequest",
    "ReportAuditRequest",
    "audit_report",
    "run_report_audit_capability",
    "ReportAssemblyRequest",
    "ReportAssemblyResult",
    "assemble_report_document",
    "run_report_capability",
    "build_report_context",
    "initialize_report_memory",
    "load_report_template_bundle",
    "DeterministicFigureRenderer",
    "FigureRenderer",
]
