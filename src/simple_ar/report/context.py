from __future__ import annotations

from pathlib import Path
from typing import Any

from simple_ar.core.artifacts import read_json
from simple_ar.core.pipeline import Context
from simple_ar.literature.models import Paper
from simple_ar.report.schema import MetricSource, ReportContext, SourceHandle


def build_report_context(
    ctx: Context,
    *,
    report_mode: str,
    goal: str,
    problem: str,
    search_meta: dict[str, Any],
    synthesis: str,
    hypothesis: str,
    plan: dict[str, Any],
    results: dict[str, Any],
    paper_rows: list[dict[str, Any]],
    papers: list[Paper],
    research_evidence_summary: str,
    max_section_sources: int = 8,
) -> ReportContext:
    """Collect compact report inputs from earlier stages."""
    citation_key_map = _citation_key_map(papers)
    source_handles = _paper_handles(papers, citation_key_map)
    source_handles.extend(_paper_brief_handles(ctx, citation_key_map))
    source_handles.extend(_chunk_handles(ctx, limit=80, citation_key_map=citation_key_map))
    source_handles.extend(_synthesis_handles(ctx))
    source_handles.extend(_experiment_result_handles(ctx, results))
    source_handles.extend(_code_task_handles(ctx))
    metric_sources = [
        *_code_task_metric_sources(ctx),
        *_metric_sources(ctx, results),
    ]
    return ReportContext(
        topic=ctx.topic,
        report_mode=report_mode,
        goal_markdown=goal,
        problem_markdown=problem,
        synthesis_markdown=synthesis,
        hypothesis_markdown=hypothesis,
        evidence_summary=research_evidence_summary,
        search_meta=search_meta,
        experiment_plan=plan,
        results=results,
        papers=paper_rows,
        source_handles=source_handles,
        metric_sources=metric_sources,
        citation_key_map=citation_key_map,
        max_section_sources=max_section_sources,
    )


def _citation_key_map(papers: list[Paper]) -> dict[str, str]:
    """Return short model-facing citation keys mapped to real paper ids."""
    return {f"P{index}": paper.id for index, paper in enumerate(papers, start=1)}


def _reverse_citation_key_map(citation_key_map: dict[str, str]) -> dict[str, str]:
    return {paper_id: key for key, paper_id in citation_key_map.items()}


def _paper_handles(papers: list[Paper], citation_key_map: dict[str, str]) -> list[SourceHandle]:
    handles: list[SourceHandle] = []
    paper_to_key = _reverse_citation_key_map(citation_key_map)
    for paper in papers:
        handles.append(
            SourceHandle(
                handle=f"paper:{paper.id}",
                kind="paper",
                citation_key=paper_to_key.get(paper.id, ""),
                title=paper.title,
                paper_id=paper.id,
                summary=paper.abstract[:600],
                metadata={
                    "authors": paper.authors,
                    "url": paper.url,
                    "source": paper.source,
                    "source_id": paper.source_id,
                    "published": paper.published,
                },
            )
        )
    return handles


def _paper_brief_handles(ctx: Context, citation_key_map: dict[str, str]) -> list[SourceHandle]:
    rows = _read_jsonl(ctx, "paper_notes.json")
    handles: list[SourceHandle] = []
    paper_to_key = _reverse_citation_key_map(citation_key_map)
    for row in rows:
        paper_id = str(row.get("paper_id") or row.get("id") or "").strip()
        if not paper_id:
            continue
        summary = str(row.get("one_sentence_summary") or row.get("relevance") or row.get("method") or "")
        handles.append(
            SourceHandle(
                handle=f"brief:{paper_id}",
                kind="paper_brief",
                citation_key=paper_to_key.get(paper_id, ""),
                title=str(row.get("title") or paper_id),
                paper_id=paper_id,
                artifact=_relative_artifact(ctx, "paper_notes.json"),
                summary=summary[:800],
                metadata=row,
            )
        )
    return handles


def _chunk_handles(
    ctx: Context,
    *,
    limit: int,
    citation_key_map: dict[str, str],
) -> list[SourceHandle]:
    rows = _read_jsonl(ctx, "research_index/chunks.jsonl")[:limit]
    handles: list[SourceHandle] = []
    paper_to_key = _reverse_citation_key_map(citation_key_map)
    for index, row in enumerate(rows, start=1):
        chunk_id = str(row.get("chunk_id") or row.get("id") or f"chunk-{index:03d}")
        paper_id = str(row.get("paper_id") or row.get("document_id") or "")
        section = str(row.get("section") or row.get("heading") or "")
        text = str(row.get("text") or row.get("content") or "")
        handles.append(
            SourceHandle(
                handle=f"chunk:{chunk_id}",
                kind="chunk",
                citation_key=paper_to_key.get(paper_id, ""),
                title=str(row.get("title") or section or chunk_id),
                paper_id=paper_id,
                chunk_id=chunk_id,
                section=section,
                artifact=_relative_artifact(ctx, "research_index/chunks.jsonl"),
                summary=text[:800],
                metadata={key: value for key, value in row.items() if key != "text"},
            )
        )
    return handles


def _synthesis_handles(ctx: Context) -> list[SourceHandle]:
    handles: list[SourceHandle] = []
    for name in ("synthesis_brief.json", "synthesis.md", "hypothesis.md"):
        path = ctx.find_artifact(name)
        if path is None:
            continue
        summary = _read_text(path)[:800]
        handles.append(
            SourceHandle(
                handle=f"artifact:{name}",
                kind="synthesis",
                title=name,
                artifact=_relative_path(ctx, path),
                summary=summary,
            )
        )
    return handles


def _code_task_handles(ctx: Context) -> list[SourceHandle]:
    comparison, artifact = _code_task_comparison(ctx)
    if not comparison:
        return []
    baseline = comparison.get("baseline") if isinstance(comparison.get("baseline"), dict) else {}
    patched = comparison.get("patched") if isinstance(comparison.get("patched"), dict) else {}
    baseline_metrics = baseline.get("metrics") if isinstance(baseline.get("metrics"), dict) else {}
    patched_metrics = patched.get("metrics") if isinstance(patched.get("metrics"), dict) else {}
    reasons = comparison.get("reasons")
    reason_text = "; ".join(str(item) for item in reasons) if isinstance(reasons, list) else ""
    summary = (
        f"Code-task before/after comparison verdict: {comparison.get('verdict', 'unknown')}. "
        f"Baseline metrics: {_metric_summary(baseline_metrics)}. "
        f"Patched metrics: {_metric_summary(patched_metrics)}. "
        f"Reasons: {reason_text or 'not recorded'}."
    )
    return [
        SourceHandle(
            handle="artifact:code_task_comparison",
            kind="experiment",
            title="Code task before/after comparison",
            artifact=artifact,
            summary=summary[:1200],
            metadata=comparison,
        )
    ]


def _experiment_result_handles(ctx: Context, results: dict[str, Any]) -> list[SourceHandle]:
    """Expose canonical experiment evidence to the report writer/reviewer."""
    if not isinstance(results, dict) or not results:
        return []
    handles: list[SourceHandle] = []
    metrics = results.get("metrics") if isinstance(results.get("metrics"), dict) else {}
    guard = results.get("guard") if isinstance(results.get("guard"), dict) else {}
    diagnosis = results.get("diagnosis") if isinstance(results.get("diagnosis"), dict) else {}
    code_review = results.get("code_review") if isinstance(results.get("code_review"), dict) else {}
    resource_plan = results.get("resource_plan") if isinstance(results.get("resource_plan"), dict) else {}
    recovery = (
        results.get("review_failure_recovery")
        if isinstance(results.get("review_failure_recovery"), dict)
        else {}
    )
    verdicts = results.get("verdicts") if isinstance(results.get("verdicts"), list) else []
    summary_parts = [
        f"status={results.get('status', 'unknown')}",
        f"returncode={results.get('returncode', 'unknown')}",
        f"timed_out={results.get('timed_out', 'unknown')}",
        f"metrics={_metric_summary(metrics)}",
    ]
    if guard:
        summary_parts.append(f"guard={guard.get('status', 'unknown')}")
    if diagnosis:
        summary_parts.append(f"diagnosis={diagnosis.get('status', 'unknown')}")
    if code_review:
        summary_parts.append(f"code_review={code_review.get('status', 'unknown')}")
    handles.append(
        SourceHandle(
            handle="artifact:canonical_results",
            kind="experiment",
            title="Canonical experiment results",
            artifact=_relative_artifact(ctx, "results.json"),
            summary="; ".join(summary_parts)[:1400],
            metadata={
                "status": results.get("status"),
                "metrics": metrics,
                "verdicts": verdicts,
                "guard": guard,
                "diagnosis": diagnosis,
            },
        )
    )
    if guard:
        issues = guard.get("issues") if isinstance(guard.get("issues"), list) else []
        handles.append(
            SourceHandle(
                handle="artifact:result_guard",
                kind="experiment",
                title="Experiment result guard",
                artifact=_relative_artifact(ctx, "guard_report.json"),
                summary=_guard_summary(guard),
                metadata={"status": guard.get("status"), "issues": issues[:20]},
            )
        )
    if diagnosis:
        deficiencies = diagnosis.get("deficiencies") if isinstance(diagnosis.get("deficiencies"), list) else []
        handles.append(
            SourceHandle(
                handle="artifact:experiment_diagnosis",
                kind="experiment",
                title="Experiment run diagnosis",
                artifact=_relative_artifact(ctx, "diagnosis.json"),
                summary=str(diagnosis.get("summary") or "")[:1200],
                metadata={
                    "status": diagnosis.get("status"),
                    "completion": diagnosis.get("completion", {}),
                    "repair": diagnosis.get("repair", {}),
                    "deficiencies": deficiencies[:20],
                },
            )
        )
    if code_review:
        handles.append(
            SourceHandle(
                handle="artifact:code_review",
                kind="experiment",
                title="Generated code review",
                artifact=_relative_artifact(ctx, "code_review.json"),
                summary=_code_review_summary(code_review),
                metadata=code_review,
            )
        )
    if resource_plan:
        handles.append(
            SourceHandle(
                handle="artifact:resource_plan",
                kind="experiment",
                title="Experiment resource plan",
                artifact=_relative_artifact(ctx, "resource_plan.json"),
                summary=_resource_plan_summary(resource_plan),
                metadata=resource_plan,
            )
        )
    if recovery:
        handles.append(
            SourceHandle(
                handle="artifact:review_failure_recovery",
                kind="experiment",
                title="Greenfield review failure recovery",
                artifact=_relative_artifact(ctx, "review_failure_recovery.json"),
                summary=(
                    f"Recovery reason: {recovery.get('reason', 'unknown')}. "
                    f"Recovery mode: {recovery.get('recovery_mode', 'unknown')}."
                )[:1200],
                metadata=recovery,
            )
        )
    return handles


def _code_task_metric_sources(ctx: Context) -> list[MetricSource]:
    comparison, artifact = _code_task_comparison(ctx)
    if not comparison:
        return []
    sources: list[MetricSource] = []
    for label in ("baseline", "patched"):
        run = comparison.get(label)
        metrics = run.get("metrics") if isinstance(run, dict) else {}
        if not isinstance(metrics, dict):
            continue
        for name, value in metrics.items():
            _append_metric_source(
                sources,
                metric_id=f"metric:code_task_{label}_{name}",
                name=str(name),
                value=value,
                artifact=artifact,
                label=f"code_task_{label}",
            )
    for row in comparison.get("metrics", []):
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "").strip()
        if not name:
            continue
        _append_metric_source(
            sources,
            metric_id=f"metric:code_task_delta_{name}",
            name=name,
            value=row.get("delta"),
            artifact=artifact,
            label="code_task_delta",
            direction=str(row.get("direction") or ""),
        )
    return sources


def _metric_sources(ctx: Context, results: dict[str, Any]) -> list[MetricSource]:
    metrics = results.get("metrics", {}) if isinstance(results, dict) else {}
    if not isinstance(metrics, dict):
        return []
    artifact = _relative_artifact(ctx, "results.json")
    sources: list[MetricSource] = []
    for name, value in metrics.items():
        if isinstance(value, bool):
            continue
        _append_metric_source(
            sources,
            metric_id=f"metric:{name}",
            name=str(name),
            value=value,
            artifact=artifact,
            label="experiment",
        )
    return sources


def _append_metric_source(
    sources: list[MetricSource],
    *,
    metric_id: str,
    name: str,
    value: object,
    artifact: str,
    label: str,
    direction: str = "",
) -> None:
    if isinstance(value, bool):
        return
    if isinstance(value, (int, float, str)):
        sources.append(
            MetricSource(
                metric_id=metric_id,
                name=name,
                value=value,
                artifact=artifact,
                label=label,
                direction=direction,
            )
        )


def _code_task_comparison(ctx: Context) -> tuple[dict[str, Any], str]:
    meta_path = ctx.find_artifact("code_task_experiment.json")
    if meta_path is None or not meta_path.exists():
        return {}, ""
    try:
        meta = read_json(meta_path)
    except Exception:
        return {}, ""
    if not isinstance(meta, dict):
        return {}, ""
    comparison_ref = meta.get("comparison")
    comparison_path: Path | None = None
    if isinstance(comparison_ref, str) and comparison_ref.strip():
        comparison_path = _resolve_artifact_ref(ctx, comparison_ref, base=meta_path.parent)
    if comparison_path is None or not comparison_path.exists():
        run_dir_value = meta.get("code_task_run_dir")
        if isinstance(run_dir_value, str) and run_dir_value.strip():
            run_dir = _resolve_artifact_ref(ctx, run_dir_value, base=meta_path.parent)
        else:
            run_dir = meta_path.parent / "code_task_run"
        comparison_path = run_dir / "code_task" / "run" / "comparison.json"
    if not comparison_path.exists():
        return {}, ""
    try:
        comparison = read_json(comparison_path)
    except Exception:
        return {}, ""
    if not isinstance(comparison, dict):
        return {}, ""
    return comparison, _relative_path(ctx, comparison_path)


def _resolve_artifact_ref(ctx: Context, value: str, *, base: Path | None = None) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    candidates = [
        path,
        ctx.run_dir / path,
    ]
    if base is not None:
        candidates.insert(1, base / path)
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def _metric_summary(metrics: object) -> str:
    if not isinstance(metrics, dict) or not metrics:
        return "none"
    parts = [
        f"{key}={value}"
        for key, value in sorted(metrics.items())
        if isinstance(value, (int, float, str)) and not isinstance(value, bool)
    ]
    return ", ".join(parts[:8]) if parts else "none"


def _guard_summary(guard: dict[str, Any]) -> str:
    issues = guard.get("issues") if isinstance(guard.get("issues"), list) else []
    issue_text = "; ".join(
        f"{item.get('severity', 'unknown')}:{item.get('code', 'issue')}"
        for item in issues
        if isinstance(item, dict)
    )
    return (
        f"Guard status: {guard.get('status', 'unknown')}. "
        f"Issues: {issue_text or 'none'}."
    )[:1200]


def _code_review_summary(review: dict[str, Any]) -> str:
    findings = review.get("findings") if isinstance(review.get("findings"), list) else []
    finding_text = "; ".join(
        f"{item.get('severity', 'unknown')}:{item.get('code', 'review')}"
        for item in findings
        if isinstance(item, dict)
    )
    summary = review.get("summary") if isinstance(review.get("summary"), dict) else {}
    return (
        f"Code review status: {review.get('status', 'unknown')}. "
        f"Errors: {summary.get('error_count', 0)}; warnings: {summary.get('warning_count', 0)}. "
        f"Findings: {finding_text or 'none'}."
    )[:1400]


def _resource_plan_summary(plan: dict[str, Any]) -> str:
    fields = []
    for key in ("max_runtime_sec", "max_files", "max_generated_lines", "max_memory_mb", "allow_gpu"):
        if key in plan:
            fields.append(f"{key}={plan.get(key)}")
    return "Resource limits: " + (", ".join(fields) if fields else "not recorded") + "."


def _read_jsonl(ctx: Context, artifact_name: str) -> list[dict[str, Any]]:
    path = ctx.find_artifact(artifact_name)
    if path is None or not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            import json

            value = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


def _relative_artifact(ctx: Context, artifact_name: str) -> str:
    path = ctx.find_artifact(artifact_name)
    return _relative_path(ctx, path) if path is not None else artifact_name


def _relative_path(ctx: Context, path: Path) -> str:
    try:
        return path.resolve().relative_to(ctx.run_dir.resolve()).as_posix()
    except ValueError:
        return str(path)
