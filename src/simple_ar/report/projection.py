"""Project persisted research evidence into report-domain inputs.

This module contains the pure joining and formatting logic needed by the
report writer and audit stages.  It does not own session lifecycle, artifact
writing, or report execution.  The application layer may translate
``ReportProjectionError`` into a product-specific error, while canonical
report callers can use this module without importing a legacy application
entry point.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from simple_ar.core import ArtifactRef, ArtifactStore
from pathlib import Path
from simple_ar.report.schema import (
    ClaimEvidenceRecord,
    MetricSource,
    ReportContext,
    ReportMemory,
    ReportSectionDraft,
    SourceHandle,
)
from simple_ar.research.design import ResearchDesignResult
from simple_ar.research.documents.ingest import DocumentBundle
from simple_ar.research.sources import SearchResult
from simple_ar.research.synthesis import SynthesisResult
from simple_ar.result_analysis.schema import AnalysisResult

if TYPE_CHECKING:
    from simple_ar.research.evidence.reader import ReadResult


class ReportProjectionError(ValueError):
    """Raised when persisted research facts are insufficient for a report."""


def attach_implementation_evidence(
    context: ReportContext, store: ArtifactStore, implementation_ref: ArtifactRef,
) -> None:
    """Expose the measured revision's frozen patch through existing report tools."""
    record = store.read_json(implementation_ref)
    evidence = {}
    for name in ("patch", "validation", "review"):
        ref = record["artifact_refs"].get(name)
        if ref is None:
            continue
        path = Path(implementation_ref.path).parent / ref["path"]
        text = store.read_text(path)
        evidence[name] = {"artifact": path.as_posix(), "text": text[:12000],
                          "truncated": len(text) > 12000}
    context.results = {**context.results, "implementation": {
        "artifact": implementation_ref.path, "status": record["status"],
        "asset_integrity": record["asset_integrity"], "evidence": evidence,
        "interpretation": "Patch records code changes; validation and review are checks, not proof of scientific improvement. Missing or truncated evidence cannot support unseen implementation details.",
    }}
    context.source_handles.append(SourceHandle(
        handle="artifact:implementation", kind="implementation_result",
        artifact=implementation_ref.path,
        summary="Frozen implementation patch and checks are available through get_code_task_result.",
    ))


def build_research_report_inputs(
    *,
    topic: str,
    brief: SynthesisResult,
    search: SearchResult,
    documents: DocumentBundle,
    execution: Mapping[str, Any],
    analysis: AnalysisResult,
    brief_ref: ArtifactRef,
    execution_ref: ArtifactRef,
    analysis_ref: ArtifactRef,
    design: ResearchDesignResult | None = None,
    design_ref: ArtifactRef | None = None,
) -> tuple[ReportContext, ReportMemory]:
    """Build experiment-report inputs from canonical persisted artifacts."""

    contract = (
        design.contract
        if design is not None and design.contract is not None
        else brief.experiment_contract
    )
    if contract is None:
        raise ReportProjectionError(
            "Research session has no experiment contract; report input is incomplete."
        )
    topic = (topic or "").strip()
    if not topic:
        raise ReportProjectionError(
            "Research session has no topic in its persisted research plan."
        )

    execution = dict(execution)
    source_handles = _research_source_handles(
        search=search,
        brief_ref=brief_ref,
        execution_ref=execution_ref,
        analysis_ref=analysis_ref,
        design_ref=design_ref,
        execution=execution,
        analysis=analysis,
    )
    metric_sources = metric_sources_from_execution(
        execution,
        artifact=execution_ref.path,
    )
    selected_papers = search.selected_papers
    evidence_summary = (
        f"Search returned {len(search.papers)} raw paper record(s) and retained "
        f"{len(selected_papers)} selected paper(s); "
        f"document ingest retained {len(documents.records)} document(s) and "
        f"{len(documents.chunks)} text chunk(s). "
        f"Execution status={execution.get('status', 'unknown')}; "
        f"analysis status={analysis.status}."
    )
    context = ReportContext(
        topic=topic,
        report_mode="experiment",
        synthesis_markdown=_synthesis_markdown(brief),
        hypothesis_markdown=contract.hypothesis,
        evidence_summary=evidence_summary,
        execution_context=brief.execution_context,
        search_meta={
            "status": search.status,
            "paper_count": len(search.papers),
            "selected_paper_count": len(selected_papers),
            "response_count": len(search.responses),
            "coverage": dict(search.coverage_report),
            "diagnostics": list(search.diagnostics),
        },
        experiment_plan=contract.to_row(),
        results=execution,
        papers=[paper.to_row() for paper in selected_papers],
        source_handles=source_handles,
        metric_sources=metric_sources,
    )
    memory = ReportMemory(
        objective=contract.hypothesis,
        template="experiment",
        report_mode="experiment",
        claims_evidence_matrix=[
            claim_evidence_record_from_analysis(claim)
            for claim in analysis.claims
        ],
        source_handles=source_handles,
        metric_sources=metric_sources,
        limitations=[*analysis.audit.limitations, *contract.risks],
        key_decisions=[
            f"execution_status={execution.get('status', 'unknown')}",
            f"analysis_status={analysis.status}",
            *analysis.status_reasons,
        ],
    )
    return context, memory


def build_literature_report_inputs(
    *,
    topic: str,
    brief: SynthesisResult,
    search: SearchResult,
    documents: DocumentBundle,
    brief_ref: ArtifactRef,
) -> tuple[ReportContext, ReportMemory]:
    """Project literature evidence without inventing measurements."""

    handles = [
        SourceHandle(handle="artifact:synthesis", kind="synthesis", artifact=brief_ref.path),
        *_paper_source_handles(search),
    ]
    limitation = (
        "No experiment was requested or executed; cited results describe "
        "prior work, not measurements from this session."
    )
    context = ReportContext(
        topic=topic,
        report_mode="research_only",
        synthesis_markdown=_synthesis_markdown(brief),
        evidence_summary=(
            f"Search retained {len(search.selected_papers)} selected papers; "
            f"ingest retained {len(documents.records)} documents and "
            f"{len(documents.chunks)} chunks. {limitation}"
        ),
        search_meta={
            "status": search.status,
            "coverage": dict(search.coverage_report),
            "diagnostics": list(search.diagnostics),
        },
        papers=[paper.to_row() for paper in search.selected_papers],
        source_handles=handles,
    )
    memory = ReportMemory(
        objective=topic,
        template="survey",
        report_mode="research_only",
        source_handles=handles,
        limitations=[limitation],
    )
    return context, memory


def attach_paired_report_measurements(
    context: ReportContext,
    memory: ReportMemory,
    measurements: list[tuple[int, str, ArtifactRef, Mapping[str, Any]]],
    *,
    comparisons: list[Mapping[str, Any]],
    comparison_ref: ArtifactRef,
    summaries: Sequence[Mapping[str, Any]] = (),
) -> tuple[ReportContext, ReportMemory]:
    """Project measured conditions into report provenance rows."""

    metrics: list[MetricSource] = []
    handles = list(context.source_handles)
    for seed, role, ref, result in measurements:
        label = f"{role}:seed={seed}"
        handles.append(
            SourceHandle(
                handle=f"artifact:{label}",
                kind="experiment_result",
                artifact=ref.path,
                summary=f"{label}; execution status={result['status']}",
            )
        )
        if result["status"] != "passed":
            memory.limitations.append(
                f"{label} did not pass; its residual metrics are not valid measurements for the report."
            )
            continue
        metrics.extend(
            row.model_copy(update={"metric_id": f"metric:{label}:{row.name}", "label": label})
            for row in metric_sources_from_execution(result, artifact=ref.path)
        )

    passed = {
        (seed, role)
        for seed, role, _, result in measurements
        if result["status"] == "passed"
    }
    for comparison in comparisons:
        seed = comparison["seed"]
        if (seed, "baseline") not in passed or (seed, "candidate") not in passed:
            continue
        label = f"comparison_delta:seed={seed}"
        metrics.extend(
            row.model_copy(
                update={
                    "metric_id": f"metric:{label}:{row.name}",
                    "label": label,
                    "condition_id": f"paired:seed={seed}",
                }
            )
            for row in metric_sources_from_execution(
                {"comparisons": [comparison]},
                artifact=comparison_ref.path,
            )
        )
    for summary in summaries:
        label = f"paired_summary:{summary['group_id']}"
        for field in ("n", "baseline_mean", "candidate_mean", "delta_mean", "delta_sample_std"):
            if summary[field] is not None:
                name = f"{summary['metric']}.{field}"
                metrics.append(
                    MetricSource(
                        metric_id=f"metric:{label}:{name}",
                        name=name,
                        value=summary[field],
                        artifact=comparison_ref.path,
                        label=label,
                        unit="count" if field == "n" else str(summary.get("unit", "")),
                        condition_id=f"seeds={','.join(map(str, summary['seeds']))}",
                        source_kind="derived_summary",
                    )
                )

    context.metric_sources = metrics
    memory.metric_sources = metrics
    context.source_handles = handles
    memory.source_handles = handles
    context.experiment_plan = {
        **context.experiment_plan,
        "paired_protocols": [
            {
                "seed": seed,
                "condition": role,
                "artifact": ref.path,
                "protocol": result.get("experiment_contract"),
            }
            for seed, role, ref, result in measurements
        ]
    }
    memory.key_decisions.append(
        "Report every seed separately. Do not turn the last run into a mean, "
        "or claim significance from these descriptive comparisons."
    )
    return context, memory


def attach_report_read_evidence(
    context: ReportContext,
    memory: ReportMemory,
    *,
    documents: DocumentBundle,
    read: "ReadResult",
    read_ref: ArtifactRef,
) -> tuple[ReportContext, ReportMemory]:
    """Join reading notes by document identity, not title or position."""

    by_paper = {
        record.metadata["paper_id"]: record
        for record in documents.records
        if "paper_id" in record.metadata
    }
    notes = {note["paper_id"]: note for note in read.paper_notes}
    handles: list[SourceHandle] = []
    for handle in context.source_handles:
        record = by_paper.get(handle.paper_id)
        if record is None:
            handles.append(handle)
            continue
        note = notes.get(record.document_id)
        metadata = dict(handle.metadata)
        metadata.update(
            document_id=record.document_id,
            extraction_status=record.extraction_status,
            reading_artifact=read_ref.path,
        )
        if note is not None:
            metadata["reading_notes"] = {
                key: note[key]
                for key in ("method", "key_claims", "limitations", "evidence_refs")
                if key in note
            }
            metadata["reading_notes_kind"] = "model_interpretation_not_source_text"
        handles.append(
            handle.model_copy(
                update={"summary": record.abstract or handle.summary, "metadata": metadata}
            )
        )
    return (
        context.model_copy(update={"source_handles": handles}),
        memory.model_copy(update={"source_handles": handles}),
    )


def metric_sources_from_execution(
    execution: Mapping[str, Any],
    *,
    artifact: str,
) -> list[MetricSource]:
    """Convert measured candidate, baseline, and comparison values to rows."""

    rows: list[MetricSource] = []
    for label, values in _metric_groups(execution):
        record = execution["baseline"] if label == "baseline" else execution
        schema = record.get("result_schema") or {}
        directions = schema.get("metric_directions") or {}
        measurement = record.get("measurement") or {}
        protocol = record.get("experiment_contract") or {}
        units = {
            spec["name"]: spec.get("unit", "")
            for spec in protocol.get("metric_specs", [])
        }
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
                    direction=str(
                        directions.get(name)
                        or (
                            schema.get("direction")
                            if name == schema.get("primary_metric")
                            else ""
                        )
                        or ""
                    ),
                    measurement_id=measurement.get("measurement_id"),
                    protocol_id=measurement.get("protocol_id"),
                    protocol_revision=measurement.get("protocol_revision"),
                    protocol_fingerprint=measurement.get("protocol_fingerprint"),
                    condition_id=measurement.get("condition_id"),
                    unit=str(units.get(name, "")),
                    source_kind=str(
                        measurement.get("source_kind") or "legacy_unverified"
                    ),
                )
            )

    comparisons = execution.get("comparisons")
    if isinstance(comparisons, list):
        for index, comparison in enumerate(comparisons):
            if not isinstance(comparison, Mapping):
                continue
            metric_rows = comparison.get("metrics")
            if not isinstance(metric_rows, list):
                continue
            for metric_row in metric_rows:
                if not isinstance(metric_row, Mapping):
                    continue
                name = str(metric_row.get("name") or "").strip()
                value = metric_row.get("delta")
                if not name or isinstance(value, bool) or not isinstance(value, (int, float, str)):
                    continue
                rows.append(
                    MetricSource(
                        metric_id=f"metric:comparison:{index}:{name}",
                        name=name,
                        value=value,
                        artifact=artifact,
                        label="comparison_delta",
                        direction=str(metric_row.get("direction") or ""),
                        source_kind="derived_comparison",
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


def _append_verified_experiment_evidence(
    sections: tuple[ReportSectionDraft | Mapping[str, Any], ...],
    context: ReportContext,
) -> tuple[ReportSectionDraft | Mapping[str, Any], ...]:
    """Append a deterministic metric appendix to experiment reports."""

    if context.report_mode != "experiment":
        return sections
    evidence = _verified_experiment_evidence(context)
    if not evidence:
        return sections
    return (
        *sections,
        ReportSectionDraft(
            section_id="verified_experiment_metrics",
            heading="Verified Experiment Metrics",
            draft_markdown=evidence,
            metric_ids=[metric.metric_id for metric in context.metric_sources],
        ),
    )


def _verified_experiment_evidence(context: ReportContext) -> str:
    """Render a compact measured summary without asking the Writer.

    Per-task measurements remain in the immutable experiment artifacts.  They
    are useful for audit and follow-up analysis, but expanding every value into
    the paper makes the paper unreadable.  Paired sessions therefore expose
    aggregate metrics and a small seed-level primary-metric table here.
    """

    if not context.metric_sources:
        return ""
    lines = [
        "The following values are copied from the persisted experiment evidence "
        "and are included to keep the quantitative record complete."
    ]
    comparisons = context.results.get("comparisons") if isinstance(context.results, Mapping) else None
    summaries = context.results.get("paired_summary") if isinstance(context.results, Mapping) else None
    if isinstance(summaries, list) and summaries:
        table = _paired_summary_markdown(summaries)
        if table:
            lines.extend(["", "### Aggregate Paired Metrics", "", table])
        seed_table = _paired_primary_metric_markdown(comparisons)
        if seed_table:
            lines.extend(["", "### Seed-Level Primary Metric", "", seed_table])
        source_labels = _metric_source_labels(context.metric_sources)
        if source_labels:
            lines.extend(
                [
                    "",
                    "Measurement provenance labels: " + source_labels + ".",
                ]
            )
        collection_ref = context.results.get("collection_ref")
        if isinstance(collection_ref, Mapping) and collection_ref.get("path"):
            lines.extend([
                "",
                "Detailed per-task and raw per-seed measurements are preserved "
                f"in the persisted artifact `{collection_ref['path']}` and the "
                "individual experiment result artifacts; this paper shows the "
                "compact aggregate view.",
            ])
        return "\n".join(lines)
    if isinstance(comparisons, list):
        for comparison in comparisons:
            if not isinstance(comparison, Mapping):
                continue
            table = _comparison_markdown(comparison)
            if table:
                lines.extend(["", "### Baseline and Patched Comparison", "", table])
                break
    ledger = _metric_ledger(context.metric_sources)
    if ledger:
        lines.extend(["", "### Metric Provenance", "", ledger])
    return "\n".join(lines)


def _paired_summary_markdown(summaries: list[Mapping[str, Any]]) -> str:
    """Render aggregate paired summaries, excluding task-by-task diagnostics."""
    rows = [
        row
        for row in summaries
        if isinstance(row, Mapping)
        and str(row.get("metric") or "").strip()
        and "_after_task_" not in str(row.get("metric") or "")
    ]
    if not rows:
        return ""
    table = [
        "| Metric | Seeds | Baseline mean | Candidate mean | Mean delta | Delta std |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in rows:
        table.append(
            "| "
            + " | ".join(
                (
                    f"`{_markdown_cell(str(row['metric']))}`",
                    _format_report_metric(row.get("n")),
                    _format_report_metric(row.get("baseline_mean")),
                    _format_report_metric(row.get("candidate_mean")),
                    _format_report_metric(row.get("delta_mean")),
                    _format_report_metric(row.get("delta_sample_std")),
                )
            )
            + " |"
        )
    return "\n".join(table)


def _paired_primary_metric_markdown(comparisons: object) -> str:
    """Render one measured primary-metric row per seed when available."""
    if not isinstance(comparisons, list):
        return ""
    table = [
        "| Seed | Baseline | Candidate | Delta |",
        "| ---: | ---: | ---: | ---: |",
    ]
    for comparison in comparisons:
        if not isinstance(comparison, Mapping):
            continue
        metrics = comparison.get("metrics")
        if not isinstance(metrics, list):
            continue
        metric = next(
            (
                row
                for row in metrics
                if isinstance(row, Mapping) and str(row.get("name")) == "accuracy"
            ),
            None,
        )
        if not isinstance(metric, Mapping):
            continue
        table.append(
            "| "
            + " | ".join(
                (
                    _format_report_metric(comparison.get("seed")),
                    _format_report_metric(metric.get("baseline")),
                    _format_report_metric(metric.get("candidate")),
                    _format_report_metric(metric.get("delta")),
                )
            )
            + " |"
        )
    return "\n".join(table) if len(table) > 2 else ""


def _comparison_markdown(comparison: Mapping[str, Any]) -> str:
    metric_rows = comparison.get("metrics")
    if not isinstance(metric_rows, list):
        return ""
    table = [
        "| Metric | Baseline | Patched | Delta | Interpretation |",
        "| --- | ---: | ---: | ---: | --- |",
    ]
    for row in metric_rows:
        if not isinstance(row, Mapping):
            continue
        name = str(row.get("name") or "").strip()
        if not name:
            continue
        table.append(
            "| "
            + " | ".join(
                (
                    f"`{_markdown_cell(name)}`",
                    _format_report_metric(row.get("baseline")),
                    _format_report_metric(row.get("candidate")),
                    _format_report_metric(row.get("delta")),
                    _markdown_cell(str(row.get("interpretation") or "recorded")),
                )
            )
            + " |"
        )
    return "\n".join(table) if len(table) > 2 else ""


def _metric_ledger(metrics: list[MetricSource]) -> str:
    table = [
        "| Source | Metric | Value | Unit | Condition | Origin |",
        "| --- | --- | ---: | --- | --- | --- |",
    ]
    for metric in metrics:
        table.append(
            "| "
            + " | ".join(
                (
                    _markdown_cell(metric.label or "experiment"),
                    f"`{_markdown_cell(metric.name)}`",
                    _format_report_metric(metric.value),
                    _markdown_cell(metric.unit or "not recorded"),
                    _markdown_cell(metric.condition_id or "not recorded"),
                    _markdown_cell(metric.source_kind),
                )
            )
            + " |"
        )
    return "\n".join(table) if len(table) > 2 else ""


def _metric_source_labels(metrics: list[MetricSource]) -> str:
    """Keep paired evidence labels visible without expanding every measurement."""
    labels = dict.fromkeys(
        metric.label.strip() for metric in metrics if metric.label.strip()
    )
    return ", ".join(f"`{_markdown_cell(label)}`" for label in labels)


def _format_report_metric(value: object) -> str:
    if value is None:
        return "not recorded"
    if isinstance(value, float):
        return f"{value:.6g}"
    return _markdown_cell(str(value))


def _markdown_cell(value: str) -> str:
    return value.replace("|", "/").replace("\n", " ").strip()


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


def _research_source_handles(
    *,
    search: SearchResult,
    brief_ref: ArtifactRef,
    execution_ref: ArtifactRef,
    analysis_ref: ArtifactRef,
    design_ref: ArtifactRef | None,
    execution: Mapping[str, Any],
    analysis: AnalysisResult,
) -> list[SourceHandle]:
    handles = [
        SourceHandle(
            handle="artifact:synthesis",
            kind="synthesis",
            artifact=brief_ref.path,
            summary="Evidence-derived research direction and experiment contract.",
        ),
        SourceHandle(
            handle="artifact:experiment_execution",
            kind="experiment",
            artifact=execution_ref.path,
            summary=f"Execution status: {execution.get('status', 'unknown')}.",
        ),
        SourceHandle(
            handle="artifact:result_analysis",
            kind="analysis",
            artifact=analysis_ref.path,
            summary=f"Analysis status: {analysis.status}.",
        ),
    ]
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
    handles.extend(_paper_source_handles(search))
    return handles


def _paper_source_handles(search: SearchResult) -> list[SourceHandle]:
    return [
        SourceHandle(
            handle=f"paper:{paper.id}",
            kind="paper",
            citation_key=paper.id,
            paper_id=paper.id,
            title=paper.title,
            summary=paper.abstract,
            metadata={
                "source": paper.source,
                "source_id": paper.source_id,
                "url": paper.url,
                "published": paper.published,
            },
        )
        for paper in search.selected_papers
    ]


__all__ = [
    "ReportProjectionError",
    "attach_paired_report_measurements",
    "attach_report_read_evidence",
    "build_literature_report_inputs",
    "build_research_report_inputs",
    "claim_evidence_record_from_analysis",
    "evidence_handles_from_claim",
    "metric_sources_from_execution",
]
