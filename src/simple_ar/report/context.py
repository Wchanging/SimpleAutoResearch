from __future__ import annotations

from pathlib import Path
from typing import Any

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
    metric_sources = _metric_sources(ctx, results)
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


def _metric_sources(ctx: Context, results: dict[str, Any]) -> list[MetricSource]:
    metrics = results.get("metrics", {}) if isinstance(results, dict) else {}
    if not isinstance(metrics, dict):
        return []
    artifact = _relative_artifact(ctx, "results.json")
    sources: list[MetricSource] = []
    for name, value in metrics.items():
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float, str)):
            sources.append(
                MetricSource(
                    metric_id=f"metric:{name}",
                    name=str(name),
                    value=value,
                    artifact=artifact,
                    label="experiment",
                )
            )
    return sources


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
