from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any
from simple_ar.core.artifacts import (
    read_json,
    write_json,
    write_jsonl,
    write_text,
)
from simple_ar.code_task.editing.scope import is_protected_edit_path
from simple_ar.experiment.code_task_experiment import (
    CODE_TASK_PROJECT_TEMPLATE,
    is_code_task_experiment_template,
)
from simple_ar.literature.bibtex import papers_to_bibtex
from simple_ar.literature.models import Paper
from simple_ar.literature.verify import (
    CitationError,
    find_citation_ids,
    validate_citations,
)
from simple_ar.integrations.llm import LLMError
from simple_ar.core.pipeline import (
    Context,
    utcnow_iso,
)
from simple_ar.report.agent import run_report_agent
from simple_ar.report.audit import build_report_audit
from simple_ar.report.context import build_report_context
from simple_ar.report.memory import initialize_report_memory, write_report_memory
from simple_ar.report.schema import AgentReportResult, ReportAuditConfig, ReportRuntimeConfig
from simple_ar.report.templates import load_report_template_bundle
from simple_ar.report.tool_gateway import ReportToolGateway
from simple_ar.research.outputs.artifacts import (
    DESIGN_DECISION_LOG,
    DESIGN_EVAL_JSON,
    DESIGN_EVAL_MD,
    DESIGN_EVIDENCE_REVIEW_MD,
    DESIGN_EXPERIMENT_CONTRACT_JSON,
    DESIGN_EXPERIMENT_CONTRACT_MD,
    DESIGN_TOOL_CONTEXT_JSON,
    DESIGN_TOOL_CONTEXT_MD,
    READ_CLAIM_CARDS,
    READ_CODE_LINKS,
    READ_DATASET_CARDS,
    READ_METHOD_CARDS,
    READ_PAPER_CARDS,
    READ_SCREENING_DECISIONS,
    READ_SHORTLIST,
    READ_READING_TABLE,
    SEARCH_CACHE_MANIFEST,
    SEARCH_CHUNKS,
    SEARCH_COVERAGE_JSON,
    SEARCH_COVERAGE_MD,
    SEARCH_DOCUMENTS,
    SEARCH_FULLTEXT_EXTRACTION,
    SEARCH_FULLTEXT_MANIFEST,
    SEARCH_SECTIONS,
    SEARCH_INDEX_META,
    SEARCH_RESEARCH_PLAN,
    SEARCH_RETRIEVAL_ROUNDS,
    SEARCH_RETRIEVAL_SELECTION,
    SYNTHESIS_EVIDENCE_PACK_JSON,
    SYNTHESIS_EVIDENCE_PACK_MD,
    SYNTHESIS_GAP_SUMMARY,
    SYNTHESIS_IDEA_CANDIDATES,
    SYNTHESIS_NOVELTY_CHECKS,
    SYNTHESIS_BRIEF_JSON,
)
from simple_ar.research.prompts import (
    REPORT_SYSTEM,
    report_user_prompt,
)
from simple_ar.research.service import load_search_paper_rows
from simple_ar.report.quality import build_report_quality
from simple_ar.retrieval.evidence import format_evidence_snippets
from simple_ar.experiment.service import load_experiment_plan
from simple_ar.pipeline_stages.common import (
    _list_value,
    _llm_client,
    _markdown_body,
    _read_jsonl_artifact,
    _relative_artifact,
    _safe_read_artifact,
    _safe_read_json_artifact,
    _stage_evidence,
    _string_items,
    _text_field,
)

def execute_report(ctx: Context) -> None:
    goal = _safe_read_artifact(ctx, "goal.md")
    problem = _safe_read_artifact(ctx, "problem.md")
    search_meta = _safe_read_json_artifact(ctx, "search_meta.json")
    synthesis = _safe_read_artifact(ctx, "synthesis.md")
    hypothesis = _safe_read_artifact(ctx, "hypothesis.md")
    plan = load_experiment_plan(ctx)
    results_path = ctx.find_artifact("results.json")
    results_present = results_path is not None
    results = read_json(results_path) if results_present else {}
    paper_rows = load_search_paper_rows(ctx)
    papers = [
        Paper.from_row(row)
        for row in paper_rows
    ]
    report_mode = _resolve_report_mode(ctx.config.get("report_mode"), results_present=results_present)
    if report_mode == "experiment" and not results_present:
        raise FileNotFoundError(
            "report_mode=experiment requires results.json. Run the experiment stage or "
            "set --report-mode research_only."
        )
    report_config = _report_runtime_config(ctx)
    evidence = _stage_evidence(ctx, "report")
    evidence_snippets = format_evidence_snippets(evidence)
    research_evidence_summary = _research_evidence_summary(ctx, papers)
    template = load_report_template_bundle(
        report_mode=report_mode,
        config=report_config,
        project_root=Path.cwd(),
    )
    report_context = build_report_context(
        ctx,
        report_mode=report_mode,
        goal=goal,
        problem=problem,
        search_meta=search_meta,
        synthesis=synthesis,
        hypothesis=hypothesis,
        plan=plan,
        results=results,
        paper_rows=paper_rows,
        papers=papers,
        research_evidence_summary=research_evidence_summary,
        max_section_sources=report_config.max_section_sources,
    )
    report_memory = initialize_report_memory(context=report_context, template=template)
    tool_gateway = ReportToolGateway(report_context)
    agent_removed_unknown_citations: list[str] = []
    agent_result = run_report_agent(
        client=_llm_client(ctx),
        context=report_context,
        template=template,
        memory=report_memory,
        config=report_config,
        gateway=tool_gateway,
        emit=lambda message: ctx.emit("stage_message", message),
    )
    if agent_result is not None:
        validated_agent_report = _validated_agent_report(
            ctx,
            agent_result.report_body,
            search_meta=search_meta,
            plan=plan,
            papers=papers,
            citation_key_map=report_context.citation_key_map,
            report_mode=report_mode,
            results_present=results_present,
        )
        if validated_agent_report is not None:
            report, agent_removed_unknown_citations = validated_agent_report
            report_memory = agent_result.memory
        else:
            report = None
    else:
        report = None
    if report is None and report_config.agent == "legacy":
        report = _report_with_llm(
            ctx,
            goal=goal,
            problem=problem,
            search_meta=search_meta,
            synthesis=synthesis,
            hypothesis=hypothesis,
            plan=plan,
            results=results,
            paper_rows=paper_rows,
            papers=papers,
            evidence_snippets=evidence_snippets,
            research_evidence_summary=research_evidence_summary,
            report_mode=report_mode,
            results_present=results_present,
            citation_key_map=report_context.citation_key_map,
        )
    if report is None:
        if report_mode == "research_only":
            report = _build_research_report(
                ctx,
                goal,
                problem,
                search_meta,
                synthesis,
                hypothesis,
                papers,
                research_evidence_summary,
            )
        else:
            report = _build_report(
                ctx,
                goal,
                problem,
                search_meta,
                synthesis,
                hypothesis,
                plan,
                results,
                papers,
                research_evidence_summary,
            )
    report_body = _strip_references_section(report)
    report_body = _expand_short_citation_keys(report_body, report_context.citation_key_map)
    report_body = _ensure_code_task_evidence_section(ctx, plan, report_body)
    report_body = _expand_short_citation_keys(report_body, report_context.citation_key_map)
    report_body = _normalize_bare_source_id_citations(report_body, {paper.id for paper in papers})
    report_body, removed_unknown_citations = _sanitize_report_citations(
        report_body,
        {paper.id for paper in papers},
    )
    removed_unknown_citations = agent_removed_unknown_citations + removed_unknown_citations
    cited_papers = _cited_papers(report_body, papers)
    if papers and not cited_papers:
        raise CitationError("Report body did not cite any paper from papers.jsonl")
    validate_citations(report_body, {paper.id for paper in papers})
    citation_map = _citation_display_map(cited_papers)
    display_body = _display_citation_numbers(report_body, citation_map)
    report = _append_references_section(display_body, cited_papers, citation_map)
    quality = build_report_quality(report, report_body, search_meta, results, papers, cited_papers)
    report_audit = build_report_audit(
        report=report,
        report_body=report_body,
        context=report_context,
        memory=report_memory,
    )
    _record_removed_citations(report_audit, removed_unknown_citations)
    ctx.emit(
        "stage_message",
        (
            f"Report template `{template.name}` loaded; "
            f"{len(tool_gateway.list_specs())} read-only report tool(s) available."
        ),
    )
    report_dir = _prepare_report_output_dir(ctx, report_config)
    if agent_result is not None:
        _write_agent_artifacts(report_dir, agent_result, report_config)
    write_text(report_dir / "report.md", report)
    write_text(report_dir / "references.bib", papers_to_bibtex(cited_papers))
    write_json(
        report_dir / "citation_map.json",
        _citation_map_artifact(citation_map, cited_papers, report_context.citation_key_map),
    )
    write_report_memory(report_dir / "report_memory.json", report_memory)
    write_json(report_dir / "report_quality.json", quality)
    write_json(report_dir / "report_audit.json", report_audit.model_dump(mode="json"))
    write_json(
        report_dir / "manifest.json",
        _report_manifest(
            ctx,
            search_meta,
            plan,
            results,
            papers,
            cited_papers,
            citation_map=citation_map,
            citation_key_map=report_context.citation_key_map,
            report_dir=report_dir,
            report_mode=report_mode,
            template_name=template.name,
            template_path=template.template_path,
            criteria_path=template.criteria_path,
            audit_status=report_audit.status,
        ),
    )


def _validated_agent_report(
    ctx: Context,
    report: str,
    *,
    search_meta: dict[str, Any],
    plan: dict[str, Any],
    papers: list[Paper],
    citation_key_map: dict[str, str],
    report_mode: str,
    results_present: bool,
) -> tuple[str, list[str]] | None:
    """Accept an agent report only when it respects citation and bound rules."""
    try:
        report_body = _strip_references_section(report)
        report_body = _expand_short_citation_keys(report_body, citation_key_map)
        allowed_ids = {paper.id for paper in papers}
        report_body = _normalize_bare_source_id_citations(report_body, allowed_ids)
        report_body, removed_unknown_citations = _sanitize_report_citations(report_body, allowed_ids)
        if removed_unknown_citations:
            ctx.emit(
                "stage_message",
                (
                    "Removed unknown citation id(s) from agent draft before "
                    f"validation: {', '.join(removed_unknown_citations)}."
                ),
            )
        validate_citations(report_body, allowed_ids)
        if papers and not _body_citation_ids(report_body, {paper.id for paper in papers}):
            raise LLMError("agent report did not cite any known paper in the body")
        bound_errors = _report_bound_errors(
            report_body,
            search_meta,
            plan,
            report_mode=report_mode,
            results_present=results_present,
        )
        if bound_errors:
            raise LLMError("agent report exceeded artifact bounds: " + "; ".join(bound_errors))
        return report_body.strip() + "\n", removed_unknown_citations
    except (LLMError, CitationError) as exc:
        ctx.emit("stage_message", f"Report agent output failed validation; using structured fallback. {exc}")
        return None


def _report_runtime_config(ctx: Context) -> ReportRuntimeConfig:
    """Read report runtime config from flattened pipeline config."""
    return ReportRuntimeConfig(
        mode=str(ctx.config.get("report_mode") or "auto"),
        template=str(ctx.config.get("report_template") or "auto"),
        criteria=str(ctx.config.get("report_criteria") or "auto"),
        style=str(ctx.config.get("report_style") or "paper"),
        draft_sections=_bool_config(ctx.config.get("report_draft_sections"), default=False),
        debug_artifacts=_bool_config(ctx.config.get("report_debug_artifacts"), default=False),
        agent=str(ctx.config.get("report_agent") or "llm"),
        reviewer=str(ctx.config.get("report_reviewer") or "llm"),
        max_review_iterations=_int_config(ctx.config.get("report_max_review_iterations"), default=2),
        max_section_tokens=_int_config(ctx.config.get("report_max_section_tokens"), default=1200),
        max_report_tokens=_int_config(ctx.config.get("report_max_report_tokens"), default=5000),
        max_section_sources=_non_negative_int_config(ctx.config.get("report_max_section_sources"), default=8),
        source_strategy=_report_source_strategy_config(ctx.config.get("report_source_strategy")),
        source_batch_size=_int_config(ctx.config.get("report_source_batch_size"), default=10),
        max_source_batches=_non_negative_int_config(ctx.config.get("report_max_source_batches"), default=0),
        review_source_batches=_bool_config(ctx.config.get("report_review_source_batches"), default=False),
        review_trace=_review_trace_config(ctx.config.get("report_review_trace")),
        output_mode=_report_output_mode_config(ctx.config.get("report_output_mode")),
        output_label=_safe_output_label(ctx.config.get("report_output_label")),
        allow_source_backtracking=_bool_config(
            ctx.config.get("report_allow_source_backtracking"),
            default=True,
        ),
        max_backtracking_calls=_int_config(ctx.config.get("report_max_backtracking_calls"), default=8),
        max_backtracking_tokens=_int_config(ctx.config.get("report_max_backtracking_tokens"), default=6000),
        audit=ReportAuditConfig(
            citations=_bool_config(ctx.config.get("report_audit_citations"), default=True),
            metrics=_bool_config(ctx.config.get("report_audit_metrics"), default=True),
            claims=_bool_config(ctx.config.get("report_audit_claims"), default=True),
            strict=_bool_config(ctx.config.get("report_audit_strict"), default=False),
        ),
    )


def _write_agent_artifacts(
    report_dir: Path,
    result: AgentReportResult,
    config: ReportRuntimeConfig,
) -> None:
    """Persist optional Writer/Reviewer artifacts according to report config."""
    if config.draft_sections:
        for section in result.sections:
            if section.draft_markdown.strip():
                write_text(
                    report_dir / "sections" / f"{section.section_id}.md",
                    section.draft_markdown.strip() + "\n",
                )
    keep_iterations = config.debug_artifacts or config.review_trace == "full"
    if keep_iterations:
        write_jsonl(
            report_dir / "iterations" / "report_iterations.jsonl",
            [item.model_dump(mode="json") for item in result.iterations],
        )
    if config.debug_artifacts:
        write_json(
            report_dir / "audit" / "reviewer_findings.json",
            [finding.model_dump(mode="json") for finding in result.reviewer_findings],
        )
        write_json(
            report_dir / "audit" / "tool_results.json",
            [tool_result.model_dump(mode="json") for tool_result in result.tool_results],
        )


def _bool_config(value: object, *, default: bool) -> bool:
    return value if isinstance(value, bool) else default

def _int_config(value: object, *, default: int) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int) and value > 0:
        return value
    return default

def _non_negative_int_config(value: object, *, default: int) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int) and value >= 0:
        return value
    return default

def _review_trace_config(value: object) -> str:
    text = str(value or "meta").strip().lower()
    return text if text in {"off", "meta", "full"} else "meta"

def _report_source_strategy_config(value: object) -> str:
    text = str(value or "full").strip().lower()
    return text if text in {"full", "batch_refine"} else "full"

def _report_output_mode_config(value: object) -> str:
    text = str(value or "overwrite").strip().lower()
    return text if text in {"overwrite", "archive", "variant"} else "overwrite"

def _safe_output_label(value: object) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    return re.sub(r"[^a-z0-9_.-]+", "-", text).strip("-._")[:80]

def _prepare_report_output_dir(ctx: Context, config: ReportRuntimeConfig) -> Path:
    """Resolve the report package output directory and preserve prior outputs.

    ``overwrite`` keeps the historical behavior. ``archive`` first copies the
    existing package into ``08-report/archives/<label>``. ``variant`` writes a
    sibling package under ``08-report/variants/<label>`` without replacing the
    current ``report.md`` when one already exists.
    """
    stage_dir = ctx.stage_dir()
    stage_dir.mkdir(parents=True, exist_ok=True)
    primary_report = stage_dir / "report.md"
    label = config.output_label or _timestamp_label()

    if config.output_mode == "variant" and primary_report.exists():
        variant_dir = stage_dir / "variants" / label
        variant_dir.mkdir(parents=True, exist_ok=True)
        write_json(
            stage_dir / "latest_variant.json",
            {
                "schema_version": "report_variant.v1",
                "generated_at": utcnow_iso(),
                "output_dir": _relative_artifact(ctx, variant_dir),
                "output_mode": "variant",
                "label": label,
                "primary_report_preserved": _relative_artifact(ctx, primary_report),
            },
        )
        ctx.emit(
            "stage_message",
            f"Writing report variant `{label}` without replacing current report.md.",
        )
        return variant_dir

    if config.output_mode == "archive" and primary_report.exists():
        archive_dir = stage_dir / "archives" / label
        _archive_report_package(stage_dir, archive_dir)
        ctx.emit(
            "stage_message",
            f"Archived existing report package to `{_relative_artifact(ctx, archive_dir)}` before overwrite.",
        )

    return stage_dir

def _archive_report_package(stage_dir: Path, archive_dir: Path) -> None:
    archive_dir.mkdir(parents=True, exist_ok=True)
    for name in (
        "report.md",
        "references.bib",
        "citation_map.json",
        "manifest.json",
        "report_memory.json",
        "report_quality.json",
        "report_audit.json",
    ):
        source = stage_dir / name
        if source.exists() and source.is_file():
            shutil.copy2(source, archive_dir / name)
    for name in ("sections", "iterations", "audit"):
        source_dir = stage_dir / name
        if source_dir.exists() and source_dir.is_dir():
            target_dir = archive_dir / name
            if target_dir.exists():
                shutil.rmtree(target_dir)
            shutil.copytree(source_dir, target_dir)

def _timestamp_label() -> str:
    return utcnow_iso().replace(":", "").replace("+", "z").replace("-", "").lower()

def _resolve_report_mode(value: object, *, results_present: bool) -> str:
    """Resolve report mode using config overrides and results availability."""
    text = str(value).strip().lower() if value is not None else "auto"
    if text not in {"auto", "research_only", "experiment"}:
        text = "auto"
    if text == "auto":
        return "experiment" if results_present else "research_only"
    return text

def _related_work_markdown(papers: list[Paper]) -> str:
    """Render a short related-work section using only known paper ids."""
    if not papers:
        return "No paper metadata was available."
    lines = []
    for paper in papers:
        author_text = ", ".join(paper.authors[:3]) if paper.authors else "Unknown authors"
        if len(paper.authors) > 3:
            author_text += ", et al."
        published = f" ({paper.published[:4]})" if paper.published else ""
        lines.append(f"- {paper.title} by {author_text}{published} [@{paper.id}].")
    return "\n".join(lines)

def _research_evidence_summary(ctx: Context, papers: list[Paper]) -> str:
    """Build a compact handoff summary for report context, not final prose."""
    synthesis_brief = _safe_read_json_artifact(ctx, SYNTHESIS_BRIEF_JSON)
    paper_cards = _read_jsonl_artifact(ctx, READ_PAPER_CARDS)
    claim_cards = _read_jsonl_artifact(ctx, READ_CLAIM_CARDS)
    method_cards = _read_jsonl_artifact(ctx, READ_METHOD_CARDS)
    dataset_cards = _read_jsonl_artifact(ctx, READ_DATASET_CARDS)
    code_links = _read_jsonl_artifact(ctx, READ_CODE_LINKS)
    sections = _read_jsonl_artifact(ctx, SEARCH_SECTIONS)
    paper_briefs = _list_value(synthesis_brief.get("paper_briefs")) if synthesis_brief else []
    themes = _list_value(synthesis_brief.get("themes")) if synthesis_brief else []
    gaps = _list_value(synthesis_brief.get("gaps")) if synthesis_brief else []
    if not any((paper_briefs, themes, gaps, paper_cards, claim_cards, method_cards, dataset_cards, code_links, sections)):
        return ""

    section_counts: dict[str, int] = {}
    for row in sections:
        section = str(row.get("section") or "unknown")
        section_counts[section] = section_counts.get(section, 0) + 1

    lines = [
        f"- Selected papers: {len(papers)}.",
        f"- Read-stage paper briefs: {len(paper_briefs)}.",
        f"- Synthesis themes: {len(themes)}.",
        f"- Gap hints: {len(gaps)}.",
    ]
    if paper_cards or claim_cards or method_cards or dataset_cards or code_links:
        lines.append(
            f"- Structured extraction rows: paper={len(paper_cards)}, claim={len(claim_cards)}, "
            f"method={len(method_cards)}, dataset={len(dataset_cards)}, code_links={len(code_links)}."
        )
    if section_counts:
        coverage = ", ".join(f"{name}={count}" for name, count in sorted(section_counts.items()))
        lines.append(f"- Full-text section coverage: {coverage}.")
    if themes:
        theme_text = "; ".join(
            text
            for text in (
                _handoff_item_text(item, keys=("summary", "theme", "description", "role"), limit=160)
                for item in themes[:4]
            )
            if text
        )
        if theme_text:
            lines.append(f"- Theme hints for the writer: {theme_text}.")
    if gaps:
        gap_text = "; ".join(
            text
            for text in (
                _handoff_item_text(item, keys=("gap", "question", "summary", "description"), limit=160)
                for item in gaps[:4]
            )
            if text
        )
        if gap_text:
            lines.append(f"- Gap hints for the writer: {gap_text}.")
    return "\n".join(lines)

def _report_evidence_summary_markdown(summary: str) -> str:
    """Render a compact artifact handoff summary for fallback reports."""
    if summary.strip():
        return (
            "The run produced the following compact evidence handoff. This is "
            "not a substitute for a model-written survey; it is retained so the "
            "user can inspect whether enough material exists for drafting.\n\n"
            f"{_readable_markdown_excerpt(summary.strip(), max_chars=1800)}"
        )
    return (
        "No structured evidence handoff was available. Inspect the search, read, "
        "and synthesize artifacts before treating this fallback as useful."
    )

def _report_with_llm(
    ctx: Context,
    *,
    goal: str,
    problem: str,
    search_meta: dict[str, Any],
    synthesis: str,
    hypothesis: str,
    plan: dict[str, Any],
    results: dict[str, Any],
    paper_rows: list[dict[str, Any]],
    papers: list[Paper],
    evidence_snippets: str,
    research_evidence_summary: str,
    report_mode: str,
    results_present: bool,
    citation_key_map: dict[str, str],
) -> str | None:
    """Generate the final report with an evidence-bounded LLM prompt.

    Args:
        ctx: Current pipeline context.
        goal: Goal Markdown produced by the plan stage.
        problem: Problem Markdown produced by the plan stage.
        search_meta: Search metadata produced by the search stage.
        synthesis: Synthesis Markdown produced by the synthesize stage.
        hypothesis: Hypothesis Markdown produced by the synthesize stage.
        plan: Experiment plan JSON from the design stage.
        results: Experiment run result JSON from the run stage.
        paper_rows: Raw paper rows loaded from ``papers.jsonl``.
        papers: Normalized paper metadata.
        evidence_snippets: Source-labelled retrieval snippets selected for the
            report stage.

    Returns:
        Model-written report Markdown, or ``None`` if LLM mode is disabled or
        the output fails validation.
    """
    client = _llm_client(ctx)
    if client is None:
        return None

    try:
        ctx.emit("stage_message", "Calling LLM for polished report drafting.")
        response = client.ask_json(
            REPORT_SYSTEM,
            report_user_prompt(
                topic=ctx.topic,
                goal_markdown=goal,
                problem_markdown=problem,
                search_meta_json=json.dumps(search_meta, indent=2, ensure_ascii=False),
                papers_json=json.dumps(paper_rows, indent=2, ensure_ascii=False),
                synthesis_markdown=synthesis,
                hypothesis_markdown=hypothesis,
                experiment_plan_json=json.dumps(plan, indent=2, ensure_ascii=False),
                results_json=json.dumps(results, indent=2, ensure_ascii=False),
                evidence_snippets=evidence_snippets,
                research_evidence_summary=research_evidence_summary,
                citation_instruction=_citation_instruction(papers, citation_key_map),
                report_mode=report_mode,
            ),
            label="report",
        )
        report = _text_field(response, "report_markdown")
        if not report:
            raise LLMError("report_markdown was empty")
        report = _strip_references_section(report)
        report = _expand_short_citation_keys(report, citation_key_map)
        report = _normalize_bare_source_id_citations(report, {paper.id for paper in papers})
        validate_citations(report, {paper.id for paper in papers})
        if papers and not _body_citation_ids(report, {paper.id for paper in papers}):
            raise LLMError("report_markdown did not cite any known paper in the body")
        bound_errors = _report_bound_errors(
            report,
            search_meta,
            plan,
            report_mode=report_mode,
            results_present=results_present,
        )
        if bound_errors:
            raise LLMError("report_markdown exceeded artifact bounds: " + "; ".join(bound_errors))
        return report.strip() + "\n"
    except (LLMError, CitationError) as exc:
        ctx.emit("stage_message", f"LLM report drafting failed; using structured fallback. {exc}")
        return None

def _build_report(
    ctx: Context,
    goal: str,
    problem: str,
    search_meta: dict[str, Any],
    synthesis: str,
    hypothesis: str,
    plan: dict[str, Any],
    results: dict[str, Any],
    papers: list[Paper],
    research_evidence_summary: str = "",
) -> str:
    """Build the final Markdown report strictly from staged artifacts.

    Args:
        ctx: Current pipeline context.
        goal: Goal Markdown produced by the plan stage.
        problem: Problem Markdown produced by the plan stage.
        search_meta: Search metadata produced by the search stage.
        synthesis: Synthesis Markdown produced by the synthesize stage.
        hypothesis: Hypothesis Markdown produced by the synthesize stage.
        plan: Experiment plan JSON from the design stage.
        results: Experiment run result JSON from the run stage.
        papers: Paper metadata loaded from ``papers.jsonl``.

    Returns:
        A complete Markdown report with citation ids limited to known papers.
    """
    return (
        f"# {_report_title(ctx, plan)}\n\n"
        "## Abstract\n\n"
        f"{_abstract_markdown(ctx, results)}\n\n"
        "## Introduction\n\n"
        f"{_introduction_markdown(ctx, goal, problem, search_meta, papers)}\n\n"
        "## Related Work\n\n"
        f"{_related_work_markdown(papers)}\n\n"
        "## Evidence Summary\n\n"
        f"{_report_evidence_summary_markdown(research_evidence_summary)}\n\n"
        "## Method\n\n"
        f"{_method_markdown(plan)}\n\n"
        "## Experiments\n\n"
        f"{_experiment_markdown(results)}\n\n"
        "## Results\n\n"
        f"{_results_markdown(results)}\n\n"
        "## Literature Search\n\n"
        f"{_search_markdown(search_meta)}\n\n"
        "## Discussion\n\n"
        f"{_experiment_discussion_markdown(search_meta, plan, results, synthesis, hypothesis)}\n\n"
        "## Limitations\n\n"
        f"{_limitations_markdown(ctx, search_meta, results, plan)}\n\n"
        "## Conclusion\n\n"
        f"{_conclusion_markdown(results)}\n"
    )

def _build_research_report(
    ctx: Context,
    goal: str,
    problem: str,
    search_meta: dict[str, Any],
    synthesis: str,
    hypothesis: str,
    papers: list[Paper],
    research_evidence_summary: str = "",
) -> str:
    """Build a conservative literature fallback when agent drafting is unavailable."""
    return (
        f"# {_research_report_title(ctx)}\n\n"
        "## Draft Status\n\n"
        f"{_fallback_status_markdown(search_meta, papers)}\n\n"
        "## Research Question\n\n"
        f"{_fallback_question_markdown(ctx, goal, problem, papers)}\n\n"
        "## Available Sources\n\n"
        f"{_fallback_sources_markdown(papers)}\n\n"
        "## Evidence Handoff\n\n"
        f"{_report_evidence_summary_markdown(research_evidence_summary)}\n\n"
        "## Boundaries And Next Steps\n\n"
        f"{_research_limitations_markdown(ctx, search_meta)}\n\n"
        f"{_fallback_next_steps_markdown(ctx)}\n"
    )

def _abstract_markdown(ctx: Context, results: dict[str, Any]) -> str:
    """Summarize the run without introducing unstaged research claims."""
    status = "timed out" if results.get("timed_out") is True else "completed"
    metrics = results.get("metrics")
    metric_count = len(metrics) if isinstance(metrics, dict) else 0
    return (
        f"This short report studies `{ctx.topic}` through the deliberately narrow "
        "lens of SimpleAutoResearch, a staged and file-based auto-research "
        "workflow. The run combines literature metadata, artifact-level synthesis, "
        "a controlled experiment, and a reproducible report package. "
        f"The experiment {status} and produced {metric_count} parsed metric(s), "
        "which are treated as evidence about the pipeline and its toy task rather "
        "than as broad scientific proof."
    )

def _report_title(ctx: Context, plan: dict[str, Any]) -> str:
    """Create a conservative paper-style title for fallback reports."""
    template = str(plan.get("template", "template experiment"))
    return f"A Reproducible Mini Auto-Research Study of {ctx.topic} with {template}"

def _research_report_title(ctx: Context) -> str:
    """Create a clear title for a non-agent fallback report."""
    return f"Evidence-Limited Draft for {ctx.topic}"

def _introduction_markdown(
    ctx: Context,
    goal: str,
    problem: str,
    search_meta: dict[str, Any],
    papers: list[Paper],
) -> str:
    """Render a prose introduction from planning and search artifacts."""
    research_question = _markdown_body(problem) or _markdown_body(goal) or ctx.topic
    source = search_meta.get("source", "unknown") if search_meta else "unknown"
    status = search_meta.get("status", "unknown") if search_meta else "unknown"
    literature_sentence = _literature_citation_sentence(papers)
    return (
        f"The starting point for this run is the question of how to study "
        f"`{ctx.topic}` with a workflow whose intermediate reasoning remains "
        "visible. Rather than asking a single opaque agent call to produce a final "
        "answer, SimpleAutoResearch decomposes the work into explicit stages: "
        "planning, literature search, reading, synthesis, experiment design, code "
        "generation, execution, and reporting. This design makes the research "
        "process easier to inspect because every transition is represented by a "
        "concrete file artifact.\n\n"
        f"The planned research question was: {research_question} The literature "
        f"stage recorded search source `{source}` with status `{status}`, so the "
        "strength of the resulting narrative depends directly on that provenance. "
        f"{literature_sentence} "
        "The report therefore treats the experiment and the literature notes as "
        "bounded evidence rather than as a general claim about the entire topic."
    )

def _fallback_status_markdown(search_meta: dict[str, Any], papers: list[Paper]) -> str:
    """Explain why this fallback should not be read as a finished survey."""
    source = search_meta.get("source", "unknown") if search_meta else "unknown"
    status = search_meta.get("status", "unknown") if search_meta else "unknown"
    return (
        "The LLM report writer was unavailable or did not produce a validated "
        "draft, so this file is a conservative fallback. It is intentionally not "
        "a finished survey. It records the research question, the available source "
        "set, and the main evidence boundaries so the run remains inspectable.\n\n"
        f"Search status: `{status}` from `{source}`. Selected paper records: {len(papers)}."
    )

def _fallback_question_markdown(ctx: Context, goal: str, problem: str, papers: list[Paper]) -> str:
    """Render the user-facing question and cite the available source set."""
    question = _readable_markdown_excerpt(
        _markdown_body(problem) or _markdown_body(goal) or ctx.topic,
        max_chars=900,
    )
    return (
        f"{question}\n\n"
        f"{_literature_citation_sentence(papers)}"
    ).strip()

def _fallback_sources_markdown(papers: list[Paper]) -> str:
    """List selected sources without trying to synthesize them."""
    if not papers:
        return "No paper metadata was available."
    lines: list[str] = []
    for index, paper in enumerate(papers[:12], start=1):
        published = f", {paper.published[:4]}" if paper.published else ""
        lines.append(f"{index}. {paper.title}{published} [@{paper.id}]")
    if len(papers) > 12:
        lines.append(f"... {len(papers) - 12} additional source(s) omitted from the fallback list.")
    return "\n".join(lines)

def _report_bound_errors(
    report: str,
    search_meta: dict[str, Any],
    plan: dict[str, Any],
    *,
    report_mode: str,
    results_present: bool,
) -> list[str]:
    """Return issues where an LLM report overstates weak staged evidence."""
    lower = report.lower()
    errors: list[str] = []
    if report_mode == "research_only":
        if re.search(r"^##\s+(method|experiments|results)\s*$", lower, flags=re.MULTILINE):
            errors.append("research-only report included experiment sections")
        forbidden_residue = (
            "hint:",
            "use this paper as",
            "paper brief",
            "additional synthesis detail",
            "search scope",
            "evidence summary",
        )
        if any(term in lower for term in forbidden_residue):
            errors.append("research-only report contained prompt or pipeline residue")
    if report_mode == "experiment" and not results_present:
        errors.append("experiment report mode selected without results.json")
    if _uses_fixture_metadata(search_meta):
        fixture_disclosure_terms = (
            "fixture metadata",
            "offline fixture",
            "placeholder metadata",
            "placeholder paper",
        )
        if not any(term in lower for term in fixture_disclosure_terms):
            errors.append("fixture metadata was not disclosed in plain language")
        fixture_overclaims = (
            "prior research has",
            "prior research shows",
            "papers such as",
            "existing literature",
            "literature showcases",
            "innovative solution",
            "unexplored potential",
            "establishing groundwork",
            "real-world",
            "practical solution",
            "practical solutions",
            "transformative",
            "significantly",
            "substantially",
            "compelling case",
        )
        if any(term in lower for term in fixture_overclaims):
            errors.append("fixture metadata was used with literature-style overclaims")

    if is_code_task_experiment_template(plan.get("template")):
        broad_code_task_overclaims = (
            "effectiveness of the llm",
            "effectiveness of the llm-guided",
            "effective solution",
            "potential of llms",
            "feasibility of employing llms",
            "feasibility of the llm",
            "promising direction",
            "superior",
            "fresh perspective",
            "new opportunities",
            "contribute meaningfully",
            "meaningful contribution",
            "significantly enhanced",
            "significant improvement",
            "substantial improvement",
            "transformative potential",
            "real-world",
            "practical solution",
            "practical solutions",
            "general applicability",
            "improved robustness",
        )
        toy_only_overclaims = (
            "enhancing the performance",
            "enhancing spam detection",
            "enhance spam detection",
            "enhancement of spam detection",
            "enhance the performance",
            "improve the baseline performance",
            "potentially improve",
            "performance improvement",
            "performance improvements",
            "improve the system's ability",
            "improving spam detection capabilities",
            "overall accuracy",
            "improved accuracy",
        )
        template = str(plan.get("template", ""))
        overclaims = broad_code_task_overclaims
        if template != CODE_TASK_PROJECT_TEMPLATE:
            overclaims = broad_code_task_overclaims + toy_only_overclaims
        if any(term in lower for term in overclaims):
            errors.append("code-task benchmark was described beyond measured evidence")
    return errors

def _uses_fixture_metadata(search_meta: dict[str, Any]) -> bool:
    """Return true when literature rows are placeholders rather than live papers."""
    return (
        search_meta.get("source") == "fixture"
        or search_meta.get("status") in {"fallback", "fixture_fallback", "offline_fixture"}
    )

def _method_markdown(plan: dict[str, Any]) -> str:
    """Render the experiment plan into a compact method section."""
    if not plan:
        return "No experiment plan artifact was available."
    if is_code_task_experiment_template(plan.get("template")):
        code_task = plan.get("code_task", {})
        benchmark = ""
        scope = "unknown"
        if isinstance(code_task, dict):
            benchmark = str(code_task.get("benchmark_command", ""))
            scope = str(code_task.get("scope", "unknown"))
        metrics = plan.get("metrics", [])
        metric_text = ", ".join(str(item) for item in metrics) if isinstance(metrics, list) else str(metrics)
        return (
            f"The experiment uses the `{plan.get('template')}` embedded code-task "
            "template. Instead of generating a script from scratch, the code stage "
            f"prepares an existing project (`{scope}`) inside an isolated workspace, "
            "runs a baseline benchmark, builds a local context pack, asks the LLM "
            "for a batch-oriented work plan, creates an attempt/batch record for "
            "the first executable work item, asks for a reviewable patch plan, "
            "auto-approves that plan only inside the pipeline workspace, asks the "
            "LLM for controlled old/new edits, and applies the patch after "
            "validation. The recorded benchmark command "
            f"is `{benchmark or 'not specified'}`. Parsed metrics are "
            f"{metric_text or 'not specified'}, and they come from the run-stage "
            "harness rather than from handwritten report text."
        )
    metrics = plan.get("metrics", [])
    metric_text = ", ".join(str(item) for item in metrics) if isinstance(metrics, list) else str(metrics)
    return (
        f"The experiment is generated from the `{plan.get('template', 'unknown')}` "
        f"template, which fixes the dataset, baseline, method, and metric set before "
        "execution. This restriction is intentional: the current system favors a "
        "small, auditable experiment over unconstrained code generation. The dataset "
        f"is `{plan.get('dataset', 'unknown')}`, the baseline is "
        f"`{plan.get('baseline', 'unknown')}`, and the comparison method is "
        f"`{plan.get('method', 'unknown')}`. The recorded metrics are "
        f"{metric_text or 'not specified'}, and they are parsed from stdout rather "
        "than handwritten into the report."
    )

def _experiment_markdown(results: dict[str, Any]) -> str:
    """Describe how the generated experiment was executed."""
    command = results.get("command", [])
    command_text = " ".join(str(item) for item in command) if isinstance(command, list) else str(command)
    return (
        "The generated script is saved at `06-code/experiment.py`. It was run "
        "as a subprocess with stdout and stderr captured into `07-run/stdout.txt` "
        "and `07-run/stderr.txt`, while structured execution metadata is stored in "
        "`07-run/results.json`. The command was "
        f"`{command_text or 'not recorded'}`. The process returned "
        f"`{results.get('returncode')}` and the timeout flag was "
        f"`{results.get('timed_out')}`."
    )

def _results_markdown(results: dict[str, Any]) -> str:
    """Render parsed metrics and raw result metadata."""
    metrics = results.get("metrics", {})
    if isinstance(metrics, dict) and metrics:
        rows = ["| Metric | Value |", "|---|---:|"]
        rows.extend(f"| `{name}` | {_format_metric(value)} |" for name, value in sorted(metrics.items()))
        return (
            "The subprocess output yielded the following parsed metrics. The table "
            "reports only values found in `results.json`, preserving the distinction "
            "between measured output and narrative interpretation.\n\n"
            + "\n".join(rows)
        )
    return "No numeric metrics were parsed from stdout, so the report cannot make quantitative claims."

def _ensure_code_task_evidence_section(ctx: Context, plan: dict[str, Any], markdown: str) -> str:
    """Append deterministic code-task evidence when the report omits it."""
    if not is_code_task_experiment_template(plan.get("template")):
        return markdown
    if "## Code Task Evidence" in markdown:
        return markdown
    section = _code_task_evidence_markdown(ctx, plan)
    if not section:
        return markdown
    return markdown.strip() + "\n\n## Code Task Evidence\n\n" + section.strip() + "\n"

def _code_task_evidence_markdown(ctx: Context, plan: dict[str, Any]) -> str:
    """Summarize nested code-task artifacts for the final report."""
    meta_path = ctx.find_artifact("code_task_experiment.json")
    if meta_path is None:
        return ""
    meta = read_json(meta_path)
    if not isinstance(meta, dict):
        return ""
    run_dir_value = meta.get("code_task_run_dir")
    code_task_run_dir = Path(str(run_dir_value)) if run_dir_value else meta_path.parent / "code_task_run"
    summary_path = code_task_run_dir / "code_task" / "summary.md"
    comparison_path = code_task_run_dir / "code_task" / "run" / "comparison.json"
    comparison = read_json(comparison_path) if comparison_path.exists() else {}
    changed_files = meta.get("changed_files", [])
    changed_text = ", ".join(f"`{path}`" for path in changed_files) if isinstance(changed_files, list) else ""
    if not changed_text:
        changed_text = "none recorded"
    code_task = plan.get("code_task", {})
    benchmark = code_task.get("benchmark_command") if isinstance(code_task, dict) else ""
    lines = [
        "The code-task experiment is backed by nested artifacts under `06-code/code_task_run`, "
        "which contains the isolated workspace, repo map, context pack, work plan, "
        "attempt/batch state, patch plan, controlled edit proposal, diff, validation "
        "report, baseline run, and patched benchmark run.",
        f"The benchmark command was `{benchmark or 'not specified'}`.",
        f"Changed workspace files: {changed_text}.",
    ]
    work_plan = meta.get("work_plan")
    batch = meta.get("batch")
    if work_plan or isinstance(batch, dict):
        batch_text = ""
        if isinstance(batch, dict) and batch:
            batch_text = (
                f" The active batch was `{batch.get('id', 'unknown')}` for "
                f"work item `{batch.get('work_item_id', 'unknown')}` with final "
                f"state `{batch.get('state', 'unknown')}`."
            )
        lines.append(
            f"The embedded code path used a batch-oriented work plan artifact "
            f"`{work_plan or 'not recorded'}` before proposing edits.{batch_text}"
        )
    risky_files = _review_sensitive_changed_files(changed_files)
    if risky_files:
        lines.append(
            "Review risk: the patch changed test or benchmark files "
            + ", ".join(f"`{path}`" for path in risky_files)
            + ", so the diff should be inspected before trusting or applying the patch."
        )
    baseline_status = meta.get("baseline_status")
    validation_status = meta.get("validation_status")
    if baseline_status or validation_status:
        lines.append(
            f"Recorded preparation status: baseline=`{baseline_status or 'unknown'}`, "
            f"validation=`{validation_status or 'unknown'}`."
        )
    if isinstance(comparison, dict) and comparison:
        verdict = comparison.get("verdict", "inconclusive")
        reasons = comparison.get("reasons", [])
        reason_text = "; ".join(str(item) for item in reasons[:3]) if isinstance(reasons, list) else ""
        lines.append(
            f"The before/after comparison verdict is `{verdict}`"
            + (f" ({reason_text})." if reason_text else ".")
        )
    if summary_path.exists():
        lines.append("The consolidated code-task summary is stored at `06-code/code_task_run/code_task/summary.md`.")
    return " ".join(lines)

def _review_sensitive_changed_files(changed_files: object) -> list[str]:
    """Return changed code-task files that should be highlighted in reports."""
    if not isinstance(changed_files, list):
        return []
    return [item for item in changed_files if isinstance(item, str) and is_protected_edit_path(item)]

def _search_markdown(search_meta: dict[str, Any]) -> str:
    """Render search provenance so fallback runs are visible in the report."""
    if not search_meta:
        return "No search metadata was available."
    text = (
        f"The literature stage searched for `{search_meta.get('query', '')}` and "
        f"recorded source `{search_meta.get('source', 'unknown')}` with status "
        f"`{search_meta.get('status', 'unknown')}`. It returned "
        f"`{search_meta.get('returned', 0)}` paper record(s)."
    )
    failure_type = search_meta.get("failure_type")
    extras: list[str] = []
    if failure_type:
        extras.append(f"failure type `{failure_type}`")
    fallback_reason = search_meta.get("fallback_reason")
    if fallback_reason:
        extras.append(f"fallback reason: {fallback_reason}")
    if extras:
        text += " The recorded fallback details are " + "; ".join(extras) + "."
    return text

def _discussion_markdown(synthesis: str, hypothesis: str) -> str:
    """Integrate synthesis and hypothesis artifacts into paper-style discussion."""
    synthesis_body = _clean_discussion_artifact(_markdown_body(synthesis))
    hypothesis_body = _clean_discussion_artifact(_markdown_body(hypothesis))
    if not synthesis_body and not hypothesis_body:
        return "No synthesis or hypothesis artifacts were available for discussion."
    if not synthesis_body:
        return f"The run produced the following testable framing: {hypothesis_body}"
    if not hypothesis_body:
        return synthesis_body
    return (
        f"The synthesis stage framed the available evidence as follows: {synthesis_body} "
        f"Building on that synthesis, the run proposed this hypothesis: {hypothesis_body}"
    )

def _experiment_discussion_markdown(
    search_meta: dict[str, Any],
    plan: dict[str, Any],
    results: dict[str, Any],
    synthesis: str,
    hypothesis: str,
) -> str:
    """Discuss experiment evidence without treating fixture synthesis as literature."""
    if _uses_fixture_metadata(search_meta) and is_code_task_experiment_template(plan.get("template")):
        metrics = results.get("metrics", {})
        changed_files = metrics.get("changed_files") if isinstance(metrics, dict) else None
        benchmark_passed = metrics.get("benchmark_passed") if isinstance(metrics, dict) else None
        changed_text = (
            f" and changed {int(changed_files)} file(s)"
            if isinstance(changed_files, (int, float))
            else ""
        )
        if benchmark_passed == 1.0 and results.get("timed_out") is not True:
            outcome_text = "recorded that the benchmark passed without timeout"
        else:
            outcome_text = (
                "captured the benchmark status, return code, timeout flag, and "
                "parsed metrics for inspection"
            )
        return (
            "Because the literature source is fixture metadata, the useful evidence "
            "in this run is operational rather than literature-backed. The code "
            f"stage produced an LLM-proposed patch{changed_text}, and the run stage "
            f"{outcome_text}. The synthesis "
            "artifacts remain visible for traceability, but they should not be read "
            "as evidence about real prior work."
        )
    if _uses_fixture_metadata(search_meta):
        return (
            "The synthesis artifacts are retained as pipeline context, but fixture "
            "metadata prevents drawing literature-backed conclusions. The experiment "
            "results should therefore be read only as a local reproducibility "
            "demonstration."
        )
    return _discussion_markdown(synthesis, hypothesis)

def _clean_discussion_artifact(text: str) -> str:
    """Remove debug-style excerpts from synthesis artifacts before reporting."""
    cleaned = text.split("Notes excerpt:", maxsplit=1)[0].strip()
    cleaned = cleaned.replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r"[ \t]+", " ", line.strip()) for line in cleaned.splitlines()]
    return _clean_prompt_residue("\n".join(line for line in lines if line).strip())


def _clean_prompt_residue(text: str) -> str:
    """Remove prompt-planning residue that should not appear in report prose."""
    cleaned = str(text or "")
    cleaned = re.sub(r"(?im)^\s*hint\s*:\s*", "", cleaned)
    cleaned = re.sub(r"(?i)\buse this paper as\b", "treat this work as", cleaned)
    cleaned = re.sub(
        r"\n?\(Additional synthesis detail is available in the stage artifacts\.\)\s*",
        "",
        cleaned,
    )
    cleaned = re.sub(r"(?i)\bpaper brief\b", "paper evidence", cleaned)
    return cleaned.strip()

def _readable_markdown_excerpt(text: str, *, max_chars: int) -> str:
    """Return a bounded Markdown excerpt while preserving paragraph breaks."""
    cleaned = _clean_discussion_artifact(text)
    if len(cleaned) <= max_chars:
        return cleaned
    excerpt = cleaned[:max_chars].rstrip()
    last_break = max(excerpt.rfind("\n\n"), excerpt.rfind("\n- "), excerpt.rfind(". "))
    if last_break > max_chars // 2:
        excerpt = excerpt[: last_break + 1].rstrip()
    return excerpt.rstrip() + "..."

def _limitations_markdown(
    ctx: Context,
    search_meta: dict[str, Any],
    results: dict[str, Any],
    plan: dict[str, Any],
) -> str:
    """Explain scope limits using only runtime configuration and result state."""
    max_papers = ctx.config.get("max_papers", "unknown")
    timeout = ctx.config.get("experiment_timeout_sec", "unknown")
    lines = [
        "This report is generated from staged artifacts rather than an external human review.",
        f"Literature coverage is limited by the configured search query and paper limit ({max_papers}).",
        f"The experiment timeout was configured as {timeout} second(s).",
    ]
    if is_code_task_experiment_template(plan.get("template")):
        lines.append(
            "The current experiment uses an editable codebase inside an isolated workspace. "
            "The 8-stage pipeline auto-approves the code-task plan to finish end to end, "
            "so safety-sensitive tasks should use the standalone code-task workflow for human review. "
            "The metrics show local benchmark behavior rather than general model quality."
        )
    else:
        lines.append(
            "The current experiment uses a tiny built-in teaching dataset, so the metrics demonstrate "
            "pipeline mechanics rather than real-world model quality."
        )
    if search_meta.get("status") in {"fallback", "fixture_fallback", "offline_fixture"} or search_meta.get("source") == "fixture":
        lines.append(
            "The literature stage used fixture metadata, so the report should not be treated as a real literature-backed review."
        )
    if results.get("timed_out") is True:
        lines.append("The experiment timed out, so any partial metrics should be treated as incomplete.")
    elif results.get("returncode") not in {0, "0"}:
        lines.append("The experiment returned a non-zero code, so the run should be inspected before drawing conclusions.")
    lines.append(
        "All citations are restricted to ids present in `02-search/papers.jsonl`, and `references.bib` is generated from the subset cited in the report body."
    )
    return " ".join(lines)

def _research_limitations_markdown(ctx: Context, search_meta: dict[str, Any]) -> str:
    """Explain literature-only scope limits without experiment claims."""
    max_papers = ctx.config.get("max_papers", "unknown")
    lines = [
        "This is a literature-only fallback draft. No experiment was executed.",
        f"Coverage is bounded by the selected search terms and the configured paper limit ({max_papers}).",
    ]
    if search_meta.get("status") in {"fallback", "fixture_fallback", "offline_fixture"} or search_meta.get("source") == "fixture":
        lines.append(
            "The available records include fixture or fallback metadata, so the report should not be treated as a complete literature-backed review."
        )
    return "\n\n".join(lines)

def _fallback_next_steps_markdown(ctx: Context) -> str:
    """Describe the next action without pretending to complete survey writing."""
    return (
        "For a polished report, rerun the report stage with the LLM report agent "
        "enabled and a suitable Markdown template under `templates/report/`. "
        "The writer/reviewer loop should handle taxonomy, comparison tables, "
        "critical synthesis, and section-level revision. This fallback should be "
        "used only as an audit trail and a quick source-set preview."
    )

def _conclusion_markdown(results: dict[str, Any]) -> str:
    """Close the report with a conservative conclusion tied to run status."""
    if results.get("timed_out") is True:
        return "The workflow produced a report package, but the experiment timed out and should be rerun or debugged."
    if results.get("returncode") not in {0, "0"}:
        return "The workflow produced a report package, but the experiment did not exit cleanly."
    return (
        "The workflow produced a complete, inspectable report package from the "
        "available staged artifacts. The result is best read as a reproducibility "
        "demo for SimpleAutoResearch rather than a standalone scientific claim."
    )

def _references_markdown(
    papers: list[Paper],
    citation_map: dict[str, int] | None = None,
) -> str:
    """Render a reader-friendly reference list with known citation keys."""
    if not papers:
        return "No references were available."
    lines = []
    for paper in papers:
        label = f"[{citation_map[paper.id]}]" if citation_map and paper.id in citation_map else f"[@{paper.id}]"
        url = f" {paper.url}" if paper.url else ""
        lines.append(f"- {label} {paper.title}.{url}")
    return "\n".join(lines)

def _strip_references_section(markdown: str) -> str:
    """Remove a model-written References section before appending verified refs."""
    lines = markdown.strip().splitlines()
    kept: list[str] = []
    for line in lines:
        if line.strip().lower().lstrip("#").strip() == "references":
            break
        kept.append(line)
    return "\n".join(kept).strip() + "\n"

def _append_references_section(
    markdown: str,
    papers: list[Paper],
    citation_map: dict[str, int] | None = None,
) -> str:
    """Append deterministic references generated from known paper metadata."""
    body = markdown.strip()
    return f"{body}\n\n## References\n\n{_references_markdown(papers, citation_map)}\n"

def _sanitize_report_citations(markdown_body: str, allowed_ids: set[str]) -> tuple[str, list[str]]:
    """Remove citation ids that are not part of the current run source map.

    Report generation can be strict without being brittle: the writer/reviewer
    may occasionally produce numeric placeholders such as ``[@1]``. Those are
    not valid source ids, so they are removed before deterministic references
    are appended and recorded in the audit as a warning.
    """
    invalid = sorted(find_citation_ids(markdown_body) - allowed_ids)
    if not invalid:
        return markdown_body, []
    sanitized = markdown_body
    for citation_id in invalid:
        sanitized = re.sub(
            rf"@{re.escape(citation_id)}(?![A-Za-z0-9_.:-])",
            "",
            sanitized,
        )
    sanitized = re.sub(r"\[\s*(?:;\s*)*\]", "", sanitized)
    sanitized = re.sub(r"\[\s*;\s*", "[", sanitized)
    sanitized = re.sub(r";\s*\]", "]", sanitized)
    sanitized = re.sub(r";\s*;", ";", sanitized)
    sanitized = re.sub(r"[ \t]{2,}", " ", sanitized)
    return sanitized.strip() + "\n", invalid

def _expand_short_citation_keys(markdown_body: str, citation_key_map: dict[str, str]) -> str:
    """Map model-facing short citation keys back to real source ids.

    Writer prompts use short keys such as ``P1`` to avoid making the model copy
    long OpenAlex/Semantic Scholar ids. Validation and references still operate
    on the real paper ids, so expansion happens before citation audit.
    """
    if not citation_key_map:
        return markdown_body
    normalized = {key.upper(): paper_id for key, paper_id in citation_key_map.items()}

    def paper_id_for(key: str) -> str:
        return normalized.get(key.strip().upper(), "")

    def replace_pandoc_key(match: re.Match[str]) -> str:
        paper_id = paper_id_for(match.group(1))
        return f"@{paper_id}" if paper_id else match.group(0)

    expanded = re.sub(r"@([Pp]\d+)(?![A-Za-z0-9_.:-])", replace_pandoc_key, markdown_body)

    def replace_bare_group(match: re.Match[str]) -> str:
        content = match.group(1).strip()
        if "@" in content:
            return match.group(0)
        parts = [part.strip() for part in re.split(r"[;,]", content) if part.strip()]
        paper_ids = [paper_id_for(part) for part in parts]
        if not paper_ids or any(not paper_id for paper_id in paper_ids):
            return match.group(0)
        return "[" + "; ".join(f"@{paper_id}" for paper_id in paper_ids) + "]"

    return re.sub(r"\[([Pp]\d+(?:\s*[;,]\s*[Pp]\d+)*)\]", replace_bare_group, expanded)

def _record_removed_citations(report_audit: object, citation_ids: list[str]) -> None:
    """Annotate report audit when invalid citation placeholders were removed."""
    if not citation_ids:
        return
    joined = ", ".join(citation_ids)
    warning = (
        "Removed citation id(s) not present in the current run source map before "
        f"writing references: {joined}."
    )
    if hasattr(report_audit, "citation_audit"):
        report_audit.citation_audit.warnings.append(warning)
        if report_audit.citation_audit.status == "passed":
            report_audit.citation_audit.status = "warning"
    if hasattr(report_audit, "notes"):
        report_audit.notes.append(warning)
    if getattr(report_audit, "status", "passed") == "passed":
        report_audit.status = "warning"

def _citation_display_map(papers: list[Paper]) -> dict[str, int]:
    """Return stable numeric citation labels for body-cited papers."""
    return {paper.id: index for index, paper in enumerate(papers, start=1)}

def _citation_map_artifact(
    citation_map: dict[str, int],
    papers: list[Paper],
    citation_key_map: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Return the written citation map artifact for display/reference lookup."""
    by_id = {paper.id: paper for paper in papers}
    key_by_id = {paper_id: key for key, paper_id in (citation_key_map or {}).items()}
    entries: list[dict[str, Any]] = []
    for paper_id, number in sorted(citation_map.items(), key=lambda item: item[1]):
        paper = by_id.get(paper_id)
        entries.append(
            {
                "number": number,
                "model_key": key_by_id.get(paper_id, ""),
                "paper_id": paper_id,
                "title": paper.title if paper else "",
                "url": paper.url if paper else "",
                "source": paper.source if paper else "",
            }
        )
    return {
        "schema_version": "citation_map.v1",
        "display_style": "numeric_brackets",
        "model_key_style": "short_keys",
        "entries": entries,
    }

def _display_citation_numbers(markdown_body: str, citation_map: dict[str, int]) -> str:
    """Convert internal ``[@paper-id]`` citations to readable ``[1]`` labels."""
    if not citation_map:
        return markdown_body

    def replace_group(match: re.Match[str]) -> str:
        ids = re.findall(r"@([A-Za-z0-9_.:-]+)", match.group(1))
        numbers = [citation_map[citation_id] for citation_id in ids if citation_id in citation_map]
        if not numbers:
            return match.group(0)
        deduped = list(dict.fromkeys(numbers))
        return "[" + ", ".join(str(number) for number in deduped) + "]"

    converted = re.sub(
        r"\[([^\]]*@([A-Za-z0-9_.:-]+)[^\]]*)\]",
        replace_group,
        markdown_body,
    )

    def replace_standalone(match: re.Match[str]) -> str:
        citation_id = match.group(1)
        number = citation_map.get(citation_id)
        return f"[{number}]" if number is not None else match.group(0)

    converted = re.sub(r"@([A-Za-z0-9_.:-]+)", replace_standalone, converted)
    return _display_bare_source_id_numbers(converted, citation_map)


def _normalize_bare_source_id_citations(markdown_body: str, allowed_ids: set[str]) -> str:
    """Convert upstream ``[paper-id]`` notes into the internal citation form."""
    if not allowed_ids:
        return markdown_body

    def replace_bracket(match: re.Match[str]) -> str:
        content = match.group(1).strip()
        if "@" in content:
            return match.group(0)
        parts = [part.strip() for part in re.split(r"[;,]", content) if part.strip()]
        if not parts or any(part not in allowed_ids for part in parts):
            return match.group(0)
        return "[" + "; ".join(f"@{part}" for part in parts) + "]"

    return re.sub(r"\[([A-Za-z0-9_.:;\-\s]+)\]", replace_bracket, markdown_body)


def _display_bare_source_id_numbers(markdown_body: str, citation_map: dict[str, int]) -> str:
    """Convert source-id brackets copied from upstream notes into display labels."""
    if not citation_map:
        return markdown_body

    id_pattern = "|".join(re.escape(paper_id) for paper_id in sorted(citation_map, key=len, reverse=True))
    if not id_pattern:
        return markdown_body

    def replace_bracket(match: re.Match[str]) -> str:
        content = match.group(1).strip()
        parts = [part.strip() for part in re.split(r"[;,]", content) if part.strip()]
        if not parts or any(part not in citation_map for part in parts):
            return match.group(0)
        numbers = sorted({citation_map[part] for part in parts})
        return "[" + ", ".join(str(number) for number in numbers) + "]"

    converted = re.sub(r"\[([A-Za-z0-9_.:;\-\s]+)\]", replace_bracket, markdown_body)

    def replace_parenthesized(match: re.Match[str]) -> str:
        citation_id = match.group(1)
        number = citation_map.get(citation_id)
        return f"[{number}]" if number is not None else match.group(0)

    converted = re.sub(rf"\(({id_pattern})\)", replace_parenthesized, converted)
    return re.sub(rf"`(?:{id_pattern})`", "", converted)

def _cited_papers(markdown_body: str, papers: list[Paper]) -> list[Paper]:
    """Return papers cited in the report body, preserving metadata order.

    Args:
        markdown_body: Report Markdown before the generated References section.
        papers: Papers loaded from ``papers.jsonl``.

    Returns:
        Subset of ``papers`` whose ids appear in body citations.
    """
    paper_by_id = {paper.id: paper for paper in papers}
    ordered_ids = _ordered_body_citation_ids(markdown_body, set(paper_by_id))
    return [paper_by_id[paper_id] for paper_id in ordered_ids if paper_id in paper_by_id]

def _ordered_body_citation_ids(markdown: str, allowed_ids: set[str]) -> list[str]:
    """Return allowed citation ids in first-mention order before references."""
    body = _strip_references_section(markdown)
    ordered: list[str] = []
    seen: set[str] = set()
    for paper_id in re.findall(r"@([A-Za-z0-9_.:-]+)", body):
        if paper_id in allowed_ids and paper_id not in seen:
            ordered.append(paper_id)
            seen.add(paper_id)
    return ordered

def _citation_instruction(papers: list[Paper], citation_key_map: dict[str, str] | None = None) -> str:
    """Build AutoResearchClaw-style guidance from known paper metadata.

    Args:
        papers: Papers loaded from ``papers.jsonl``.

    Returns:
        A compact prompt block that lists allowed citation keys and reminds the
        model to cite papers only when the local metadata supports the claim.
    """
    if not papers:
        return ""
    key_by_id = {paper_id: key for key, paper_id in (citation_key_map or {}).items()}
    lines = [
        "Use only these short citation keys in body text, in Pandoc form `[@P1]`:",
    ]
    for paper in papers:
        abstract = f" Abstract: {paper.abstract[:220]}" if paper.abstract else ""
        source = f" Source: {paper.source}" if paper.source else ""
        key = key_by_id.get(paper.id, paper.id)
        lines.append(f"- [@{key}] TITLE: \"{paper.title}\".{source}{abstract}")
    lines.extend(
        [
            "Do not cite a paper unless the sentence discusses that paper or its listed metadata.",
            "If no listed paper supports a claim, write the claim without a citation or weaken it.",
        ]
    )
    return "\n".join(lines)

def _literature_citation_sentence(papers: list[Paper]) -> str:
    """Create one conservative citation sentence for fallback introductions."""
    real_papers = [paper for paper in papers if paper.source != "fixture"]
    selected = real_papers or papers
    if not selected:
        return ""
    keys = " ".join(f"[@{paper.id}]" for paper in selected[:3])
    return f"The body cites examples from the retrieved set such as {keys}."

def _body_citation_ids(markdown: str, allowed_ids: set[str]) -> set[str]:
    """Return allowed citation ids that appear before the References section."""
    body = _strip_references_section(markdown)
    found = set(re.findall(r"@([A-Za-z0-9_.:-]+)", body))
    return found & allowed_ids

def _model_citation_key(paper_id: str, citation_key_map: dict[str, str]) -> str:
    """Return the short model-facing citation key for one paper id."""
    for key, mapped_paper_id in citation_key_map.items():
        if mapped_paper_id == paper_id:
            return key
    return ""

def _report_manifest(
    ctx: Context,
    search_meta: dict[str, Any],
    plan: dict[str, Any],
    results: dict[str, Any],
    papers: list[Paper],
    cited_papers: list[Paper],
    citation_map: dict[str, int],
    citation_key_map: dict[str, str],
    report_dir: Path,
    *,
    report_mode: str,
    template_name: str,
    template_path: str,
    criteria_path: str,
    audit_status: str,
) -> dict[str, Any]:
    """Create a reproducibility manifest for the final report directory."""
    return {
        "schema_version": 1,
        "generated_at": utcnow_iso(),
        "topic": ctx.topic,
        "run_dir": str(ctx.run_dir),
        "report_mode": report_mode,
        "report_template": {
            "name": template_name,
            "template_path": template_path,
            "criteria_path": criteria_path,
        },
        "source_artifacts": _source_artifacts(ctx),
        "literature_search": search_meta,
        "report_artifacts": {
            "report.md": _relative_artifact(ctx, report_dir / "report.md"),
            "references.bib": _relative_artifact(ctx, report_dir / "references.bib"),
            "citation_map.json": _relative_artifact(ctx, report_dir / "citation_map.json"),
            "manifest.json": _relative_artifact(ctx, report_dir / "manifest.json"),
            "report_memory.json": _relative_artifact(ctx, report_dir / "report_memory.json"),
            "report_quality.json": _relative_artifact(ctx, report_dir / "report_quality.json"),
            "report_audit.json": _relative_artifact(ctx, report_dir / "report_audit.json"),
        },
        "audit": {
            "status": audit_status,
            "report_audit": "report_audit.json",
            "report_quality": "report_quality.json",
        },
        "citation_display": {
            "style": "numeric_brackets",
            "citation_map": "citation_map.json",
            "model_key_style": "short_keys",
            "source_key_policy": "writers cite short keys such as P1; report.md uses numeric labels for readability; citation_map.json preserves both mappings",
            "entries": [
                {
                    "number": citation_map.get(paper.id),
                    "model_key": _model_citation_key(paper.id, citation_key_map),
                    "paper_id": paper.id,
                }
                for paper in cited_papers
            ],
        },
        "experiment": {
            "template": plan.get("template"),
            "mode": plan.get("mode", "template"),
            "dataset": plan.get("dataset"),
            "baseline": plan.get("baseline"),
            "method": plan.get("method"),
            "timeout_sec": plan.get("timeout_sec"),
            "command": results.get("command", []),
            "returncode": results.get("returncode"),
            "timed_out": results.get("timed_out"),
            "metrics": results.get("metrics", {}),
            "code_task": plan.get("code_task", {}),
        },
        "papers": [
            {
                "id": paper.id,
                "title": paper.title,
                "url": paper.url,
                "source": paper.source,
                "source_id": paper.source_id,
            }
            for paper in papers
        ],
        "cited_papers": [
            {
                "id": paper.id,
                "title": paper.title,
                "url": paper.url,
                "source": paper.source,
                "source_id": paper.source_id,
            }
            for paper in cited_papers
        ],
        "citation_policy": {
            "references_bib": "contains only papers cited in the report body",
            "papers_jsonl": "contains all retrieved paper metadata",
        },
        "reproduce": {
            "rerun_report": f"uv run simple-ar resume {ctx.run_dir} --from-stage report",
            "rerun_experiment_and_report": f"uv run simple-ar resume {ctx.run_dir} --from-stage run",
        },
    }

def _source_artifacts(ctx: Context) -> dict[str, str]:
    """List the source artifacts used by the report package."""
    artifacts: dict[str, str] = {}
    for name in (
        "goal.md",
        "problem.md",
        "papers.jsonl",
        "search_meta.json",
        SEARCH_RESEARCH_PLAN,
        SEARCH_RETRIEVAL_ROUNDS,
        SEARCH_RETRIEVAL_SELECTION,
        SEARCH_COVERAGE_JSON,
        SEARCH_COVERAGE_MD,
        SEARCH_DOCUMENTS,
        SEARCH_CACHE_MANIFEST,
        SEARCH_FULLTEXT_MANIFEST,
        SEARCH_FULLTEXT_EXTRACTION,
        SEARCH_SECTIONS,
        SEARCH_CHUNKS,
        SEARCH_INDEX_META,
        READ_SCREENING_DECISIONS,
        READ_SHORTLIST,
        READ_READING_TABLE,
        READ_PAPER_CARDS,
        READ_CLAIM_CARDS,
        READ_METHOD_CARDS,
        READ_DATASET_CARDS,
        READ_CODE_LINKS,
        SYNTHESIS_EVIDENCE_PACK_JSON,
        SYNTHESIS_EVIDENCE_PACK_MD,
        SYNTHESIS_GAP_SUMMARY,
        SYNTHESIS_IDEA_CANDIDATES,
        SYNTHESIS_NOVELTY_CHECKS,
        SYNTHESIS_BRIEF_JSON,
        DESIGN_EXPERIMENT_CONTRACT_JSON,
        DESIGN_EXPERIMENT_CONTRACT_MD,
        DESIGN_TOOL_CONTEXT_JSON,
        DESIGN_TOOL_CONTEXT_MD,
        DESIGN_EVIDENCE_REVIEW_MD,
        DESIGN_DECISION_LOG,
        DESIGN_EVAL_JSON,
        DESIGN_EVAL_MD,
        "activity_log.jsonl",
        "evidence_ledger.jsonl",
        "artifact_index.json",
        "artifact_chunks.jsonl",
        "code_task_experiment.json",
        "paper_notes.json",
        "notes.md",
        "synthesis.md",
        "hypothesis.md",
        "experiment_plan.json",
        "generated_code_task.md",
        "generated_code_task_meta.json",
        "experiment.py",
        "stdout.txt",
        "stderr.txt",
        "results.json",
    ):
        ref = _artifact_ref(ctx, name)
        if ref is not None:
            artifacts[name] = ref
    return artifacts

def _compact_field(value: object, *, default: str, limit: int = 220) -> str:
    text = str(value or "").strip()
    if not text or text.lower() == "unknown":
        return default
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3].rsplit(" ", 1)[0].strip() + "..."

def _handoff_item_text(value: object, *, keys: tuple[str, ...], limit: int) -> str:
    """Extract a human-readable handoff note without leaking internal ids."""
    if isinstance(value, dict):
        for key in keys:
            item = value.get(key)
            if isinstance(item, str) and item.strip():
                return _clean_prompt_residue(_compact_field(item, default="", limit=limit))
        return ""
    return _clean_prompt_residue(_compact_field(value, default="", limit=limit))

def _format_metric(value: object) -> str:
    """Format metric values consistently for Markdown."""
    if isinstance(value, float):
        return f"{value:.6g}"
    return str(value)

def _artifact_ref(ctx: Context, filename: str) -> str | None:
    """Return a run-relative artifact path for an existing artifact."""
    path = ctx.find_artifact(filename)
    if path is None:
        candidate = ctx.run_dir / filename
        if candidate.exists():
            path = candidate
    if path is None:
        return None
    return _relative_artifact(ctx, path)

__all__ = [
    "execute_report",
]
