"""Read-only projection of historical research evidence into report inputs."""

from __future__ import annotations

from typing import TYPE_CHECKING
from simple_ar.report.schema import ReportContext, ReportMemory
from simple_ar.report.projection import build_research_report_inputs

if TYPE_CHECKING:
    from simple_ar.app.research_session import ResearchSessionResult


def build_research_session_report_inputs(
    session: "ResearchSessionResult",
) -> tuple[ReportContext, ReportMemory]:
    """Compatibility mapping; report facts are built by the shared constructor."""
    return build_research_report_inputs(
        topic=session.plan.query_plan.topic, brief=session.brief, search=session.search,
        documents=session.documents, execution=session.execution, analysis=session.analysis,
        brief_ref=session.brief_ref, execution_ref=session.execution_ref, analysis_ref=session.analysis_ref,
        design=getattr(session, "design", None), design_ref=getattr(session, "design_ref", None),
    )
