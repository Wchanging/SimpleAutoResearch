from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from simple_ar.core.artifacts import (
    read_json,
    write_json,
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
    validate_citations,
)
from simple_ar.integrations.llm import LLMError
from simple_ar.core.pipeline import (
    Context,
    utcnow_iso,
)
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
    evidence = _stage_evidence(ctx, "report")
    evidence_snippets = format_evidence_snippets(evidence)
    research_evidence_summary = _research_evidence_summary(ctx, papers)
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
    report_body = _ensure_code_task_evidence_section(ctx, plan, report_body)
    cited_papers = _cited_papers(report_body, papers)
    if papers and not cited_papers:
        raise CitationError("Report body did not cite any paper from papers.jsonl")
    report = _append_references_section(report_body, cited_papers)
    validate_citations(report, {paper.id for paper in papers})
    quality = build_report_quality(report, report_body, search_meta, results, papers, cited_papers)
    write_text(ctx.artifact_path("report.md"), report)
    write_text(ctx.artifact_path("references.bib"), papers_to_bibtex(cited_papers))
    write_json(ctx.artifact_path("report_quality.json"), quality)
    write_json(
        ctx.artifact_path("manifest.json"),
        _report_manifest(ctx, search_meta, plan, results, papers, cited_papers, report_mode=report_mode),
    )

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
    """Build a compact, report-ready summary from structured search evidence."""
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
        f"- Paper Briefs: {len(paper_briefs)}; themes: {len(themes)}; gaps: {len(gaps)}.",
    ]
    if paper_cards or claim_cards or method_cards or dataset_cards or code_links:
        lines.append(
            f"- Debug cards: paper={len(paper_cards)}, claim={len(claim_cards)}, "
            f"method={len(method_cards)}, dataset={len(dataset_cards)}, code_links={len(code_links)}."
        )
    if section_counts:
        coverage = ", ".join(f"{name}={count}" for name, count in sorted(section_counts.items()))
        lines.append(f"- Section coverage: {coverage}.")

    paper_ids = {paper.id for paper in papers}
    for row in paper_briefs[:5]:
        if not isinstance(row, dict):
            continue
        paper_id = str(row.get("paper_id") or "")
        citation = f" [@{paper_id}]" if paper_id in paper_ids else ""
        role = str(row.get("evidence_role") or "other")
        summary = _compact_field(row.get("one_sentence_summary"), default="No summary captured")
        hint = _compact_field(row.get("synthesis_hint"), default="")
        hint_text = f" Hint: {hint}" if hint else ""
        lines.append(f"- Paper Brief `{paper_id or 'unknown'}`{citation} ({role}): {summary}.{hint_text}")
    for row in themes[:4]:
        if isinstance(row, dict):
            lines.append(
                f"- Theme `{row.get('role') or 'other'}`: "
                f"{_compact_field(row.get('summary'), default='No theme summary captured')}."
            )
    if gaps:
        lines.append("- Open gaps: " + "; ".join(_compact_field(gap, default="unknown gap") for gap in gaps[:4]) + ".")
    for row in paper_cards[:4]:
        paper_id = str(row.get("paper_id") or "")
        citation = f" [@{paper_id}]" if paper_id in paper_ids else ""
        method = _compact_field(row.get("method_summary"), default="unknown method")
        claims = _string_items(row.get("main_claims"), limit=1)
        claim_text = f" Main claim: {claims[0]}" if claims else ""
        evidence = _string_items(row.get("evidence_refs"), limit=2)
        evidence_text = f" Evidence refs: {', '.join(evidence)}." if evidence else ""
        lines.append(
            f"- Paper card `{paper_id or 'unknown'}`{citation}: {method}.{claim_text}{evidence_text}"
        )

    for row in claim_cards[:5]:
        paper_id = str(row.get("paper_id") or "")
        citation = f" [@{paper_id}]" if paper_id in paper_ids else ""
        claim = _compact_field(row.get("claim"), default="unknown claim")
        scope = str(row.get("scope") or "unknown")
        refs = _string_items(row.get("evidence_refs"), limit=2)
        ref_text = f" refs={', '.join(refs)}" if refs else ""
        lines.append(f"- Claim card `{scope}`{citation}: {claim}{ref_text}.")

    if method_cards:
        method_summaries = [
            _compact_field(row.get("name"), default="unknown method")
            for row in method_cards[:3]
        ]
        lines.append("- Method evidence: " + "; ".join(method_summaries) + ".")
    if dataset_cards:
        dataset_summaries = [
            _compact_field(row.get("name"), default="unknown dataset")
            for row in dataset_cards[:3]
        ]
        lines.append("- Dataset/metric evidence: " + "; ".join(dataset_summaries) + ".")
    if code_links:
        link_summaries = [
            str(row.get("repository") or row.get("url") or "unknown link")
            for row in code_links[:3]
        ]
        lines.append("- Code-link evidence: " + "; ".join(link_summaries) + ".")
    return "\n".join(lines)

def _report_evidence_summary_markdown(summary: str) -> str:
    """Render structured evidence summary for fallback reports."""
    if summary.strip():
        return (
            "The following structured evidence summary was generated from read-stage "
            "Paper Briefs, the synthesis brief, section-aware chunks, and optional debug cards. "
            "It should be read as bounded evidence rather than a complete literature review.\n\n"
            f"{summary.strip()}"
        )
    return (
        "No structured Paper Brief evidence was available. The report therefore relies on "
        "paper metadata, synthesis artifacts, and explicit limitations."
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
                citation_instruction=_citation_instruction(papers),
                report_mode=report_mode,
            ),
            label="report",
        )
        report = _text_field(response, "report_markdown")
        if not report:
            raise LLMError("report_markdown was empty")
        report = _strip_references_section(report)
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
    """Build a literature-only report when no experiment results exist."""
    return (
        f"# {_research_report_title(ctx)}\n\n"
        "## Abstract\n\n"
        f"{_research_abstract_markdown(ctx, search_meta, papers)}\n\n"
        "## Introduction\n\n"
        f"{_research_introduction_markdown(ctx, goal, problem, search_meta, papers)}\n\n"
        "## Search Scope\n\n"
        f"{_research_search_scope_markdown(search_meta, papers)}\n\n"
        "## Evidence Summary\n\n"
        f"{_report_evidence_summary_markdown(research_evidence_summary)}\n\n"
        "## Thematic Synthesis\n\n"
        f"{_synthesis_markdown(synthesis, hypothesis)}\n\n"
        "## Approach Patterns\n\n"
        f"{_approach_patterns_markdown(papers, synthesis)}\n\n"
        "## Open Questions\n\n"
        f"{_open_questions_markdown(ctx, synthesis, hypothesis)}\n\n"
        "## Limitations\n\n"
        f"{_research_limitations_markdown(ctx, search_meta)}\n\n"
        "## Conclusion\n\n"
        f"{_research_conclusion_markdown(ctx, synthesis, hypothesis)}\n"
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

def _research_abstract_markdown(
    ctx: Context,
    search_meta: dict[str, Any],
    papers: list[Paper],
) -> str:
    """Summarize a literature-only report without implying experiments."""
    source = search_meta.get("source", "unknown") if search_meta else "unknown"
    status = search_meta.get("status", "unknown") if search_meta else "unknown"
    return (
        f"This report summarizes literature metadata for `{ctx.topic}` using "
        "the SimpleAutoResearch pipeline. The literature stage recorded source "
        f"`{source}` with status `{status}` and returned {len(papers)} paper "
        "record(s). No experiment was executed; the report focuses on the "
        "available metadata and synthesis artifacts."
    )

def _report_title(ctx: Context, plan: dict[str, Any]) -> str:
    """Create a conservative paper-style title for fallback reports."""
    template = str(plan.get("template", "template experiment"))
    return f"A Reproducible Mini Auto-Research Study of {ctx.topic} with {template}"

def _research_report_title(ctx: Context) -> str:
    """Create a conservative title for literature-only reports."""
    return f"A Literature-Focused Review of {ctx.topic}"

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

def _research_introduction_markdown(
    ctx: Context,
    goal: str,
    problem: str,
    search_meta: dict[str, Any],
    papers: list[Paper],
) -> str:
    """Render a literature-only introduction without experiment-stage claims."""
    research_question = _markdown_body(problem) or _markdown_body(goal) or ctx.topic
    source = search_meta.get("source", "unknown") if search_meta else "unknown"
    status = search_meta.get("status", "unknown") if search_meta else "unknown"
    literature_sentence = _literature_citation_sentence(papers)
    return (
        f"The starting point for this report is the question of how to understand "
        f"`{ctx.topic}` from the available literature metadata and synthesis "
        "artifacts. SimpleAutoResearch decomposes the literature-only pass into "
        "planning, metadata search, reading, synthesis, and reporting stages, so "
        "the intermediate reasoning remains visible as files.\n\n"
        f"The planned research question was: {research_question} The literature "
        f"stage recorded search source `{source}` with status `{status}`, so the "
        "strength of the narrative depends directly on that provenance. "
        f"{literature_sentence} "
        "No experiment was executed for this report; the conclusions are bounded "
        "to the retrieved metadata and staged synthesis."
    )

def _research_search_scope_markdown(
    search_meta: dict[str, Any],
    papers: list[Paper],
) -> str:
    """Describe search provenance for a survey-style report."""
    if not search_meta:
        return "No search metadata was available, so the scope of this survey-style report is undefined."
    query = search_meta.get("query", "")
    source = search_meta.get("source", "unknown")
    status = search_meta.get("status", "unknown")
    returned = search_meta.get("returned", len(papers))
    citation_sentence = _literature_citation_sentence(papers)
    return (
        f"The search stage used query `{query}` and recorded source `{source}` "
        f"with status `{status}`. It returned `{returned}` paper metadata "
        f"record(s). {citation_sentence} This scope statement is provenance, "
        "not a claim that the report covers the full literature."
    )

def _approach_patterns_markdown(papers: list[Paper], synthesis: str) -> str:
    """Summarize approach patterns supported by metadata and synthesis text."""
    if not papers:
        return "No paper metadata was available to compare approach patterns."
    if all(paper.source == "fixture" for paper in papers):
        return (
            "The available records are fixture metadata, so no real approach "
            "taxonomy can be inferred. The only defensible pattern is that the "
            "pipeline can carry citation keys and placeholder abstracts through "
            "a survey-style report package."
        )
    categories = sorted({category for paper in papers for category in paper.categories if category})
    category_text = ", ".join(categories[:8]) if categories else "unspecified categories"
    synthesis_body = _clean_discussion_artifact(_markdown_body(synthesis))
    synthesis_sentence = (
        f" The synthesis artifact further frames the records as: {synthesis_body}"
        if synthesis_body
        else ""
    )
    return (
        f"The retrieved metadata spans {category_text}. At this stage, SimpleAutoResearch "
        "does not inspect full paper PDFs, so approach patterns are limited to titles, "
        f"abstract snippets, categories, and staged notes.{synthesis_sentence}"
    )

def _open_questions_markdown(ctx: Context, synthesis: str, hypothesis: str) -> str:
    """Render survey-style gaps and next steps without claiming results."""
    hypothesis_body = _clean_discussion_artifact(_markdown_body(hypothesis))
    synthesis_body = _clean_discussion_artifact(_markdown_body(synthesis))
    if hypothesis_body:
        return (
            f"The staged hypothesis suggests a possible next step: {hypothesis_body} "
            "A future run should turn this into a concrete benchmark or code-task "
            "before making empirical claims."
        )
    if synthesis_body:
        return (
            "The synthesis identifies themes but does not yet define an executable "
            "evaluation. A useful next step is to choose a target codebase, define "
            "a benchmark command, and decide which claims can be measured."
        )
    return (
        f"The report does not yet identify a concrete experiment for `{ctx.topic}`. "
        "A future run should refine the question and collect stronger metadata before coding."
    )

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
        if any(
            heading in lower
            for heading in ("## experiments", "## results", "## method")
        ):
            errors.append("research-only report included experiment sections")
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

def _synthesis_markdown(synthesis: str, hypothesis: str) -> str:
    """Render a standalone synthesis section for literature-only reports."""
    synthesis_body = _clean_discussion_artifact(_markdown_body(synthesis))
    hypothesis_body = _clean_discussion_artifact(_markdown_body(hypothesis))
    if not synthesis_body and not hypothesis_body:
        return "No synthesis or hypothesis artifacts were available."
    if synthesis_body and hypothesis_body:
        return f"{synthesis_body}\n\nProposed hypothesis: {hypothesis_body}"
    if synthesis_body:
        return synthesis_body
    return f"Proposed hypothesis: {hypothesis_body}"

def _clean_discussion_artifact(text: str) -> str:
    """Remove debug-style excerpts from synthesis artifacts before reporting."""
    cleaned = text.split("Notes excerpt:", maxsplit=1)[0].strip()
    return " ".join(cleaned.split())

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
        "This report is literature-only; no experiment was executed.",
        f"Literature coverage is limited by the configured search query and paper limit ({max_papers}).",
    ]
    if search_meta.get("status") in {"fallback", "fixture_fallback", "offline_fixture"} or search_meta.get("source") == "fixture":
        lines.append(
            "The literature stage used fixture metadata, so the report should not be treated as a real literature-backed review."
        )
    return " ".join(lines)

def _research_conclusion_markdown(ctx: Context, synthesis: str, hypothesis: str) -> str:
    """Close a literature-only report with conservative next-step guidance."""
    synthesis_body = _clean_discussion_artifact(_markdown_body(synthesis))
    hypothesis_body = _clean_discussion_artifact(_markdown_body(hypothesis))
    if synthesis_body or hypothesis_body:
        return (
            f"The workflow produced a literature-focused report package on `{ctx.topic}` from staged artifacts. "
            "The next step is to translate the synthesized themes into a concrete experiment "
            "or code-task benchmark once a target codebase is selected."
        )
    return (
        f"The workflow produced a literature-focused report package on `{ctx.topic}` from staged artifacts, "
        "but additional analysis is needed to define a concrete experiment target."
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

def _references_markdown(papers: list[Paper]) -> str:
    """Render a reader-friendly reference list with known citation keys."""
    if not papers:
        return "No references were available."
    lines = []
    for paper in papers:
        url = f" {paper.url}" if paper.url else ""
        lines.append(f"- [@{paper.id}] {paper.title}.{url}")
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

def _append_references_section(markdown: str, papers: list[Paper]) -> str:
    """Append deterministic references generated from known paper metadata."""
    body = markdown.strip()
    return f"{body}\n\n## References\n\n{_references_markdown(papers)}\n"

def _cited_papers(markdown_body: str, papers: list[Paper]) -> list[Paper]:
    """Return papers cited in the report body, preserving metadata order.

    Args:
        markdown_body: Report Markdown before the generated References section.
        papers: Papers loaded from ``papers.jsonl``.

    Returns:
        Subset of ``papers`` whose ids appear in body citations.
    """
    cited_ids = _body_citation_ids(markdown_body, {paper.id for paper in papers})
    return [paper for paper in papers if paper.id in cited_ids]

def _citation_instruction(papers: list[Paper]) -> str:
    """Build AutoResearchClaw-style guidance from known paper metadata.

    Args:
        papers: Papers loaded from ``papers.jsonl``.

    Returns:
        A compact prompt block that lists allowed citation keys and reminds the
        model to cite papers only when the local metadata supports the claim.
    """
    if not papers:
        return ""
    lines = [
        "Use only these citation keys in body text, in Pandoc form `[@key]`:",
    ]
    for paper in papers:
        abstract = f" Abstract: {paper.abstract[:220]}" if paper.abstract else ""
        source = f" Source: {paper.source}" if paper.source else ""
        lines.append(f"- [@{paper.id}] TITLE: \"{paper.title}\".{source}{abstract}")
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
    return f"The retrieved metadata is cited in the body using known keys such as {keys}."

def _body_citation_ids(markdown: str, allowed_ids: set[str]) -> set[str]:
    """Return allowed citation ids that appear before the References section."""
    body = _strip_references_section(markdown)
    found = set(re.findall(r"@([A-Za-z0-9_.:-]+)", body))
    return found & allowed_ids

def _report_manifest(
    ctx: Context,
    search_meta: dict[str, Any],
    plan: dict[str, Any],
    results: dict[str, Any],
    papers: list[Paper],
    cited_papers: list[Paper],
    *,
    report_mode: str,
) -> dict[str, Any]:
    """Create a reproducibility manifest for the final report directory."""
    return {
        "schema_version": 1,
        "generated_at": utcnow_iso(),
        "topic": ctx.topic,
        "run_dir": str(ctx.run_dir),
        "report_mode": report_mode,
        "source_artifacts": _source_artifacts(ctx),
        "literature_search": search_meta,
        "report_artifacts": {
            "report.md": _relative_artifact(ctx, ctx.artifact_path("report.md")),
            "references.bib": _relative_artifact(ctx, ctx.artifact_path("references.bib")),
            "manifest.json": _relative_artifact(ctx, ctx.artifact_path("manifest.json")),
            "report_quality.json": _relative_artifact(ctx, ctx.artifact_path("report_quality.json")),
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
