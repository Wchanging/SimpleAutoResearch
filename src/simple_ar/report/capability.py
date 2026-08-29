"""Explicit report assembly capability.

The legacy report stage still owns LLM planning, writing, and review.  This
module exposes only the stable downstream boundary: section drafts become one
report artifact, with optional deterministic figure rendering.  It does not
choose sections, call an LLM, or run an audit.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from simple_ar.core.capabilities import ArtifactRef, CapabilityContext, CapabilityResult
from simple_ar.report.assembler import apply_section_numbering, assemble_report_sections
from simple_ar.report.figures import ReportFigureRecord
from simple_ar.report.ports import DeterministicFigureRenderer, FigureRenderer
from simple_ar.report.schema import (
    ReportDocumentPlan,
    ReportRuntimeConfig,
    ReportSectionDraft,
)


@dataclass(frozen=True, slots=True)
class ReportAssemblyRequest:
    """Explicit inputs for assembling one report from completed sections."""

    title: str
    sections: tuple[ReportSectionDraft | Mapping[str, Any], ...]
    config: ReportRuntimeConfig | Mapping[str, Any] = field(
        default_factory=ReportRuntimeConfig
    )
    document_plan: ReportDocumentPlan | Mapping[str, Any] | None = None
    template_name: str = ""

    def __post_init__(self) -> None:
        if not self.title.strip():
            raise ValueError("ReportAssemblyRequest.title cannot be empty.")
        object.__setattr__(self, "sections", tuple(self.sections))


@dataclass(frozen=True, slots=True)
class ReportAssemblyResult:
    """Rendered report text and the figure records produced for it."""

    report_markdown: str
    figures: tuple[ReportFigureRecord, ...] = ()


def assemble_report_document(
    request: ReportAssemblyRequest,
    *,
    report_dir: Path,
    figure_renderer: FigureRenderer | None = None,
) -> ReportAssemblyResult:
    """Assemble explicit section drafts without changing their meaning."""

    sections = tuple(
        section
        if isinstance(section, ReportSectionDraft)
        else ReportSectionDraft.model_validate(section)
        for section in request.sections
    )
    if not sections or not any(section.draft_markdown.strip() for section in sections):
        raise ValueError("Report assembly requires at least one non-empty section draft.")

    config = (
        request.config
        if isinstance(request.config, ReportRuntimeConfig)
        else ReportRuntimeConfig.model_validate(request.config)
    )
    document_plan = (
        request.document_plan
        if isinstance(request.document_plan, ReportDocumentPlan)
        else (
            ReportDocumentPlan.model_validate(request.document_plan)
            if request.document_plan is not None
            else None
        )
    )
    report = assemble_report_sections(
        title=request.title,
        sections=_order_sections(sections, document_plan),
    )
    renderer = figure_renderer or DeterministicFigureRenderer()
    rendered = renderer.render(
        report_markdown=report,
        report_dir=report_dir,
        config=config.figures,
        template_name=request.template_name,
        document_plan=document_plan,
    )
    report = apply_section_numbering(
        rendered.report_markdown,
        mode=config.section_numbering,
        template_name=request.template_name,
        style=config.style,
    )
    return ReportAssemblyResult(
        report_markdown=report,
        figures=tuple(rendered.figures),
    )


def _order_sections(
    sections: tuple[ReportSectionDraft, ...],
    document_plan: ReportDocumentPlan | None,
) -> list[ReportSectionDraft]:
    """Apply the frozen document order without dropping unplanned drafts."""

    if document_plan is None or not document_plan.sections:
        return list(sections)

    ordered_plans = sorted(
        enumerate(document_plan.sections),
        key=lambda item: (
            item[1].final_order if item[1].final_order > 0 else item[0],
            item[0],
        ),
    )
    planned_order = {
        section.section_id: index
        for index, (_, section) in enumerate(ordered_plans)
    }
    return sorted(
        sections,
        key=lambda section: (planned_order.get(section.section_id, len(planned_order)),),
    )


def run_report_capability(
    *,
    context: CapabilityContext,
    request: ReportAssemblyRequest,
    figure_renderer: FigureRenderer | None = None,
) -> CapabilityResult:
    """Persist one assembled report through the session capability boundary."""

    renderer = figure_renderer or DeterministicFigureRenderer()
    result = assemble_report_document(
        request,
        report_dir=context.store.root,
        figure_renderer=renderer,
    )
    report_ref = context.store.write_text(
        "report.md",
        result.report_markdown,
        kind="report",
        schema="report.v1",
        producer="report.assembly",
    )
    artifacts: list[ArtifactRef] = [report_ref]
    diagnostics: list[str] = []
    figure_refs: list[ArtifactRef] = []
    for figure in result.figures:
        status = "available" if context.store.exists(figure.path) else "missing"
        figure_ref = context.store.ref(
            figure.path,
            kind="figure",
            schema="report_figure.v1",
            producer=f"report.{renderer.name}",
            status=status,  # type: ignore[arg-type]
        )
        figure_refs.append(figure_ref)
        if status == "missing":
            diagnostics.append(
                f"Figure renderer reported a missing artifact: {figure.path}."
            )
    artifacts.extend(figure_refs)
    if result.figures:
        manifest_ref = context.store.write_json(
            "figures/figures_manifest.json",
            {
                "schema_version": "report_figures.v1",
                "figure_count": len(result.figures),
                "figures": [figure.model_dump(mode="json") for figure in result.figures],
            },
            kind="report_figures",
            schema="report_figures.v1",
            producer="report.assembly",
        )
        artifacts.append(manifest_ref)
    return CapabilityResult(
        status="partial" if diagnostics else "completed",
        artifacts=tuple(artifacts),
        diagnostics=tuple(diagnostics),
        usage={"section_count": len(request.sections), "figure_count": len(result.figures)},
        provenance={
            "capability": "report",
            "assembly": "section_drafts",
            "result_schema": "report.v1",
        },
    )


__all__ = [
    "ReportAssemblyRequest",
    "ReportAssemblyResult",
    "assemble_report_document",
    "run_report_capability",
]
