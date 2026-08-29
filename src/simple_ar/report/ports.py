"""Replaceable report rendering ports.

The default renderer remains the existing deterministic SVG implementation.
External image tools may implement the same protocol without changing report
assembly or audit behavior.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Protocol, runtime_checkable

from simple_ar.report.figures import ReportFigureResult, maybe_add_report_figures
from simple_ar.report.schema import ReportDocumentPlan, ReportFigureConfig


@runtime_checkable
class FigureRenderer(Protocol):
    """Render optional report figures from a report and structured plan."""

    name: str

    def render(
        self,
        *,
        report_markdown: str,
        report_dir: Path,
        config: ReportFigureConfig,
        template_name: str = "",
        document_plan: ReportDocumentPlan | None = None,
        emit: Callable[[str], None] | None = None,
    ) -> ReportFigureResult:
        """Return the report with any supported figure artifacts inserted."""


class DeterministicFigureRenderer:
    """Default renderer backed by the existing local SVG implementation."""

    name = "deterministic_svg"

    def render(
        self,
        *,
        report_markdown: str,
        report_dir: Path,
        config: ReportFigureConfig,
        template_name: str = "",
        document_plan: ReportDocumentPlan | None = None,
        emit: Callable[[str], None] | None = None,
    ) -> ReportFigureResult:
        return maybe_add_report_figures(
            report_markdown=report_markdown,
            report_dir=report_dir,
            config=config,
            template_name=template_name,
            document_plan=document_plan,
            emit=emit,
        )


__all__ = ["DeterministicFigureRenderer", "FigureRenderer"]
