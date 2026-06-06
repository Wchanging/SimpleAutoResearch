"""Report generation, source backtracking, and audit helpers."""

from simple_ar.report.audit import build_report_audit
from simple_ar.report.context import build_report_context
from simple_ar.report.memory import initialize_report_memory
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
    "build_report_context",
    "initialize_report_memory",
    "load_report_template_bundle",
]
