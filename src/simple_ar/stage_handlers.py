from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from simple_ar.artifacts import read_json, read_jsonl, read_text, write_json, write_jsonl, write_text
from simple_ar.experiment.runner import run_experiment
from simple_ar.experiment.templates import build_experiment_code
from simple_ar.literature.arxiv_client import ArxivRateLimitError, ArxivSearchClient, LiteratureSearchError
from simple_ar.literature.bibtex import papers_to_bibtex
from simple_ar.literature.cache import get_cached, put_cache
from simple_ar.literature.models import Paper
from simple_ar.literature.verify import CitationError, validate_citations
from simple_ar.llm import LLMClient, LLMError, LLMRequest
from simple_ar.pipeline import Context, utcnow_iso
from simple_ar.prompts import (
    PLAN_SYSTEM,
    READ_SYSTEM,
    REPORT_SYSTEM,
    SYNTHESIZE_SYSTEM,
    paper_note_user_prompt,
    plan_user_prompt,
    report_user_prompt,
    synthesize_user_prompt,
)
from simple_ar.usage import record_llm_usage


def execute_plan(ctx: Context) -> None:
    client = _llm_client(ctx)
    if client is not None:
        try:
            ctx.emit("stage_message", "Calling LLM for research planning.")
            response = client.ask_json(
                PLAN_SYSTEM,
                plan_user_prompt(ctx.topic),
                label="plan",
            )
            goal = _text_field(response, "goal_markdown")
            problem = _text_field(response, "problem_markdown")
            if goal and problem:
                write_text(ctx.artifact_path("goal.md"), _ensure_heading(goal, "Research Goal"))
                write_text(ctx.artifact_path("problem.md"), _ensure_heading(problem, "Research Problem"))
                return
        except LLMError as exc:
            ctx.emit("stage_message", f"LLM planning failed; using offline fallback. {exc}")
            pass

    write_text(
        ctx.artifact_path("goal.md"),
        (
            "# Research Goal\n\n"
            f"Topic: {ctx.topic}\n\n"
            "Create a small, reproducible research workflow that can be inspected "
            "stage by stage.\n"
        ),
    )
    write_text(
        ctx.artifact_path("problem.md"),
        (
            "# Research Problem\n\n"
            f"How can we study `{ctx.topic}` with a simple literature-backed "
            "experiment and a transparent artifact pipeline?\n"
        ),
    )


def execute_search(ctx: Context) -> None:
    problem = read_text(ctx.find_artifact("problem.md") or ctx.artifact_path("problem.md"))
    query = _search_query(ctx, problem)
    max_papers = _max_papers(ctx)

    papers: list[Paper]
    meta: dict[str, object] = {
        "query": query,
        "max_papers": max_papers,
        "source": "fixture",
        "status": "fallback",
    }
    if ctx.config.get("use_arxiv") is True:
        try:
            ctx.emit("stage_message", f"Searching arXiv for up to {max_papers} paper(s).")
            papers = ArxivSearchClient(page_size=max_papers).search(query, max_results=max_papers)
            if not papers:
                raise LiteratureSearchError("arXiv returned no results")
            put_cache(query, "arxiv", max_papers, [paper.to_row() for paper in papers])
            meta.update({"source": "arxiv", "status": "ok", "returned": len(papers)})
        except ArxivRateLimitError as exc:
            if ctx.config.get("strict_search") is True:
                raise
            ctx.emit("stage_message", "arXiv rate limit hit; checking local cache.")
            papers, cache_hit = _cached_or_fixture_papers(ctx, query, max_papers, problem)
            meta.update(_fallback_meta("rate_limited", str(exc), papers, cache_hit))
        except LiteratureSearchError as exc:
            if ctx.config.get("strict_search") is True:
                raise
            ctx.emit("stage_message", f"arXiv search failed. {exc}")
            papers, cache_hit = _cached_or_fixture_papers(ctx, query, max_papers, problem)
            meta.update(_fallback_meta("provider_error", str(exc), papers, cache_hit))
    else:
        ctx.emit("stage_message", "Using fixture paper metadata.")
        papers = _fixture_papers(problem)
        meta.update({"returned": len(papers)})

    write_jsonl(ctx.artifact_path("papers.jsonl"), [paper.to_row() for paper in papers])
    write_json(ctx.artifact_path("search_meta.json"), meta)


def _cached_or_fixture_papers(
    ctx: Context,
    query: str,
    max_papers: int,
    problem: str,
) -> tuple[list[Paper], bool]:
    """Return cached arXiv papers after failure, or deterministic fixture rows.

    Args:
        ctx: Current pipeline context for progress messages.
        query: arXiv query used for the live search attempt.
        max_papers: Search result limit.
        problem: Problem artifact used to build fixture metadata if needed.

    Returns:
        ``(papers, cache_hit)`` where ``cache_hit`` tells the caller whether the
        papers came from the local literature cache.
    """
    cached_rows = get_cached(query, "arxiv", max_papers)
    if cached_rows:
        ctx.emit(
            "stage_message",
            f"Using {len(cached_rows)} cached arXiv paper(s) after live search failed.",
        )
        return [Paper.from_row(row) for row in cached_rows], True

    ctx.emit(
        "stage_message",
        "No cached arXiv metadata available; using fixture metadata. "
        "Use --strict-search when this should fail the run.",
    )
    return _fixture_papers(problem), False


def _fallback_meta(
    failure_type: str,
    reason: str,
    papers: list[Paper],
    cache_hit: bool,
) -> dict[str, object]:
    """Build search metadata for cache or fixture fallback paths."""
    if cache_hit:
        return {
            "source": "arxiv_cache",
            "status": "cache",
            "cache_hit": True,
            "failure_type": failure_type,
            "fallback_reason": reason,
            "returned": len(papers),
        }
    return {
        "source": "fixture",
        "status": "fallback",
        "cache_hit": False,
        "failure_type": failure_type,
        "fallback_reason": reason,
        "returned": len(papers),
    }


def execute_read(ctx: Context) -> None:
    papers = read_jsonl(ctx.find_artifact("papers.jsonl") or ctx.artifact_path("papers.jsonl"))
    client = _llm_client(ctx)
    if client is not None and papers:
        try:
            notes = _read_paper_notes_with_llm(ctx, client, papers)
            write_json(ctx.artifact_path("paper_notes.json"), notes)
            write_text(ctx.artifact_path("notes.md"), _notes_markdown(notes))
            return
        except LLMError as exc:
            ctx.emit("stage_message", f"LLM reading failed; using offline fallback. {exc}")
            pass

    notes = [
        {
            "paper_id": paper["id"],
            "title": paper.get("title", ""),
            "problem": "Pipeline validation",
            "method": "Placeholder metadata",
            "limitation": "No real literature search has been implemented yet.",
            "relevance": "Confirms artifact passing between stages.",
        }
        for paper in papers
    ]
    write_json(ctx.artifact_path("paper_notes.json"), notes)
    write_text(ctx.artifact_path("notes.md"), _notes_markdown(notes))


def execute_synthesize(ctx: Context) -> None:
    notes = read_text(ctx.find_artifact("notes.md") or ctx.artifact_path("notes.md"))
    paper_notes_path = ctx.find_artifact("paper_notes.json") or ctx.artifact_path("paper_notes.json")
    paper_notes = read_text(paper_notes_path)
    client = _llm_client(ctx)
    if client is not None:
        try:
            ctx.emit("stage_message", "Calling LLM for synthesis.")
            response = client.ask_json(
                SYNTHESIZE_SYSTEM,
                synthesize_user_prompt(notes, paper_notes),
                label="synthesize",
            )
            synthesis = _text_field(response, "synthesis_markdown")
            hypothesis = _text_field(response, "hypothesis_markdown")
            if synthesis and hypothesis:
                write_text(ctx.artifact_path("synthesis.md"), _ensure_heading(synthesis, "Synthesis"))
                write_text(ctx.artifact_path("hypothesis.md"), _ensure_heading(hypothesis, "Hypothesis"))
                return
        except LLMError as exc:
            ctx.emit("stage_message", f"LLM synthesis failed; using offline fallback. {exc}")
            pass

    write_text(
        ctx.artifact_path("synthesis.md"),
        "# Synthesis\n\n"
        "The current skeleton confirms that stage outputs can become later inputs.\n\n"
        f"Notes excerpt:\n\n{notes[:500]}\n",
    )
    write_text(
        ctx.artifact_path("hypothesis.md"),
        "# Hypothesis\n\n"
        "A file-first staged pipeline makes auto-research behavior easier to inspect "
        "and resume than a hidden monolithic agent loop.\n",
    )


def execute_design(ctx: Context) -> None:
    hypothesis = read_text(ctx.find_artifact("hypothesis.md") or ctx.artifact_path("hypothesis.md"))
    write_json(
        ctx.artifact_path("experiment_plan.json"),
        {
            "name": "toy_text_classification",
            "template": _experiment_template(ctx),
            "hypothesis": hypothesis.strip(),
            "dataset": "built_in_toy_spam",
            "baseline": "keyword_rules",
            "method": "bag_of_words_logistic_regression",
            "metrics": ["accuracy", "precision", "recall"],
            "timeout_sec": _experiment_timeout(ctx),
        },
    )


def execute_code(ctx: Context) -> None:
    plan = read_json(ctx.find_artifact("experiment_plan.json") or ctx.artifact_path("experiment_plan.json"))
    ctx.emit("stage_message", f"Generating experiment from template `{plan.get('template', '')}`.")
    code = build_experiment_code(plan)
    write_text(ctx.artifact_path("experiment.py"), code)


def execute_run(ctx: Context) -> None:
    experiment_path = ctx.find_artifact("experiment.py")
    if experiment_path is None:
        raise FileNotFoundError("experiment.py was not found")
    timeout_sec = _experiment_timeout(ctx)
    ctx.emit("stage_message", f"Running experiment subprocess with {timeout_sec}s timeout.")
    result = run_experiment(experiment_path, timeout_sec=timeout_sec)
    write_text(ctx.artifact_path("stdout.txt"), result.stdout or "No stdout output.\n")
    write_text(ctx.artifact_path("stderr.txt"), result.stderr or "No stderr output.\n")
    write_json(ctx.artifact_path("results.json"), result.to_json())


def execute_report(ctx: Context) -> None:
    goal = _safe_read_artifact(ctx, "goal.md")
    problem = _safe_read_artifact(ctx, "problem.md")
    search_meta = _safe_read_json_artifact(ctx, "search_meta.json")
    synthesis = read_text(ctx.find_artifact("synthesis.md") or ctx.artifact_path("synthesis.md"))
    hypothesis = _safe_read_artifact(ctx, "hypothesis.md")
    plan = _safe_read_json_artifact(ctx, "experiment_plan.json")
    results = read_json(ctx.find_artifact("results.json") or ctx.artifact_path("results.json"))
    paper_rows = read_jsonl(ctx.find_artifact("papers.jsonl") or ctx.artifact_path("papers.jsonl"))
    papers = [
        Paper.from_row(row)
        for row in paper_rows
    ]
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
    )
    if report is None:
        report = _build_report(ctx, goal, problem, search_meta, synthesis, hypothesis, plan, results, papers)
    report = _append_references_section(_strip_references_section(report), papers)
    validate_citations(report, {paper.id for paper in papers})
    write_text(ctx.artifact_path("report.md"), report)
    write_text(ctx.artifact_path("references.bib"), papers_to_bibtex(papers))
    write_json(
        ctx.artifact_path("manifest.json"),
        _report_manifest(ctx, search_meta, plan, results, papers),
    )


HANDLERS = {
    1: execute_plan,
    2: execute_search,
    3: execute_read,
    4: execute_synthesize,
    5: execute_design,
    6: execute_code,
    7: execute_run,
    8: execute_report,
}


def _search_query(ctx: Context, problem: str) -> str:
    """Build the literature search query for the current run.

    Args:
        ctx: Current pipeline context.
        problem: Research problem Markdown from the plan stage.

    Returns:
        User-provided search query when configured, otherwise a compact query
        derived from the original topic.
    """
    configured = ctx.config.get("search_query")
    if isinstance(configured, str) and configured.strip():
        return configured.strip()
    topic = ctx.topic.strip()
    if topic:
        return topic[:240]
    return " ".join(problem.split())[:240]


def _max_papers(ctx: Context) -> int:
    """Read the configured paper limit with a conservative default."""
    value = ctx.config.get("max_papers", 5)
    try:
        limit = int(value)
    except (TypeError, ValueError):
        limit = 5
    return min(max(1, limit), 20)


def _fixture_papers(problem: str) -> list[Paper]:
    """Return deterministic paper metadata for offline tests and demos."""
    return [
        Paper(
            id="fixture-001",
            title="Placeholder Paper for Pipeline Testing",
            authors=["SimpleAutoResearch"],
            abstract="This placeholder record lets the pipeline validate JSONL artifacts.",
            url="https://example.com/fixture-001",
            published="2026-01-01",
            categories=["cs.CL"],
            source="fixture",
            source_id="fixture-001",
        )
    ]


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
            ),
            label="report",
        )
        report = _text_field(response, "report_markdown")
        if not report:
            raise LLMError("report_markdown was empty")
        report = _strip_references_section(report)
        validate_citations(report, {paper.id for paper in papers})
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
        f"{_introduction_markdown(ctx, goal, problem, search_meta)}\n\n"
        "## Related Work\n\n"
        f"{_related_work_markdown(papers)}\n\n"
        "## Method\n\n"
        f"{_method_markdown(plan)}\n\n"
        "## Experiments\n\n"
        f"{_experiment_markdown(results)}\n\n"
        "## Results\n\n"
        f"{_results_markdown(results)}\n\n"
        "## Literature Search\n\n"
        f"{_search_markdown(search_meta)}\n\n"
        "## Discussion\n\n"
        f"{_discussion_markdown(synthesis, hypothesis)}\n\n"
        "## Limitations\n\n"
        f"{_limitations_markdown(ctx, search_meta, results)}\n\n"
        "## Conclusion\n\n"
        f"{_conclusion_markdown(results)}\n"
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
        "a controlled template experiment, and a reproducible report package. "
        f"The experiment {status} and produced {metric_count} parsed metric(s), "
        "which are treated as evidence about the pipeline and its toy task rather "
        "than as broad scientific proof."
    )


def _report_title(ctx: Context, plan: dict[str, Any]) -> str:
    """Create a conservative paper-style title for fallback reports."""
    template = str(plan.get("template", "template experiment"))
    return f"A Reproducible Mini Auto-Research Study of {ctx.topic} with {template}"


def _introduction_markdown(
    ctx: Context,
    goal: str,
    problem: str,
    search_meta: dict[str, Any],
) -> str:
    """Render a prose introduction from planning and search artifacts."""
    research_question = _markdown_body(problem) or _markdown_body(goal) or ctx.topic
    source = search_meta.get("source", "unknown") if search_meta else "unknown"
    status = search_meta.get("status", "unknown") if search_meta else "unknown"
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
        "The report therefore treats the experiment and the literature notes as "
        "bounded evidence rather than as a general claim about the entire topic."
    )


def _method_markdown(plan: dict[str, Any]) -> str:
    """Render the experiment plan into a compact method section."""
    if not plan:
        return "No experiment plan artifact was available."
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


def _clean_discussion_artifact(text: str) -> str:
    """Remove debug-style excerpts from synthesis artifacts before reporting."""
    cleaned = text.split("Notes excerpt:", maxsplit=1)[0].strip()
    return " ".join(cleaned.split())


def _limitations_markdown(
    ctx: Context,
    search_meta: dict[str, Any],
    results: dict[str, Any],
) -> str:
    """Explain scope limits using only runtime configuration and result state."""
    max_papers = ctx.config.get("max_papers", "unknown")
    timeout = ctx.config.get("experiment_timeout_sec", "unknown")
    lines = [
        "This report is generated from staged artifacts rather than an external human review.",
        f"Literature coverage is limited by the configured search query and paper limit ({max_papers}).",
        "The current experiment uses a tiny built-in teaching dataset, so the metrics demonstrate pipeline mechanics rather than real-world model quality.",
        f"The experiment timeout was configured as {timeout} second(s).",
    ]
    if search_meta.get("status") == "fallback" or search_meta.get("source") == "fixture":
        lines.append(
            "The literature stage used fixture metadata, so the report should not be treated as a real literature-backed review."
        )
    if results.get("timed_out") is True:
        lines.append("The experiment timed out, so any partial metrics should be treated as incomplete.")
    elif results.get("returncode") not in {0, "0"}:
        lines.append("The experiment returned a non-zero code, so the run should be inspected before drawing conclusions.")
    lines.append(
        "All citations are restricted to ids present in `02-search/papers.jsonl`, and `references.bib` is generated from the same metadata."
    )
    return " ".join(lines)


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


def _report_manifest(
    ctx: Context,
    search_meta: dict[str, Any],
    plan: dict[str, Any],
    results: dict[str, Any],
    papers: list[Paper],
) -> dict[str, Any]:
    """Create a reproducibility manifest for the final report directory."""
    return {
        "schema_version": 1,
        "generated_at": utcnow_iso(),
        "topic": ctx.topic,
        "run_dir": str(ctx.run_dir),
        "source_artifacts": _source_artifacts(ctx),
        "literature_search": search_meta,
        "report_artifacts": {
            "report.md": _relative_artifact(ctx, ctx.artifact_path("report.md")),
            "references.bib": _relative_artifact(ctx, ctx.artifact_path("references.bib")),
            "manifest.json": _relative_artifact(ctx, ctx.artifact_path("manifest.json")),
        },
        "experiment": {
            "template": plan.get("template"),
            "dataset": plan.get("dataset"),
            "baseline": plan.get("baseline"),
            "method": plan.get("method"),
            "timeout_sec": plan.get("timeout_sec"),
            "command": results.get("command", []),
            "returncode": results.get("returncode"),
            "timed_out": results.get("timed_out"),
            "metrics": results.get("metrics", {}),
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
        "paper_notes.json",
        "notes.md",
        "synthesis.md",
        "hypothesis.md",
        "experiment_plan.json",
        "experiment.py",
        "stdout.txt",
        "stderr.txt",
        "results.json",
    ):
        ref = _artifact_ref(ctx, name)
        if ref is not None:
            artifacts[name] = ref
    return artifacts


def _safe_read_artifact(ctx: Context, filename: str) -> str:
    """Read an artifact when present, otherwise return an empty string."""
    path = ctx.find_artifact(filename)
    return read_text(path) if path is not None else ""


def _safe_read_json_artifact(ctx: Context, filename: str) -> dict[str, Any]:
    """Read a JSON artifact as a dictionary when present."""
    path = ctx.find_artifact(filename)
    if path is None:
        return {}
    data = read_json(path)
    return data if isinstance(data, dict) else {}


def _markdown_body(markdown: str) -> str:
    """Remove one leading Markdown heading to avoid nested report sections."""
    lines = markdown.strip().splitlines()
    if lines and lines[0].lstrip().startswith("#"):
        lines = lines[1:]
        while lines and not lines[0].strip():
            lines = lines[1:]
    return "\n".join(lines).strip()


def _format_metric(value: object) -> str:
    """Format metric values consistently for Markdown."""
    if isinstance(value, float):
        return f"{value:.6g}"
    return str(value)


def _artifact_ref(ctx: Context, filename: str) -> str | None:
    """Return a run-relative artifact path for an existing artifact."""
    path = ctx.find_artifact(filename)
    if path is None:
        return None
    return _relative_artifact(ctx, path)


def _relative_artifact(ctx: Context, path: Path) -> str:
    """Render a path relative to the run directory when possible."""
    try:
        return str(path.relative_to(ctx.run_dir)).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")


def _experiment_template(ctx: Context) -> str:
    """Read the configured experiment template name."""
    value = ctx.config.get("experiment_template", "toy_text_classification")
    template = str(value).strip()
    return template or "toy_text_classification"


def _experiment_timeout(ctx: Context) -> int:
    """Read the experiment subprocess timeout with a safe default."""
    value = ctx.config.get("experiment_timeout_sec", 30)
    try:
        timeout = int(value)
    except (TypeError, ValueError):
        timeout = 30
    return min(max(1, timeout), 300)


def _llm_client(ctx: Context) -> LLMClient | None:
    """Create an LLM client for a stage when LLM mode is enabled.

    Args:
        ctx: Current pipeline context containing runtime configuration.

    Returns:
        Configured client, or ``None`` when offline fallback should be used.
    """
    if ctx.config.get("use_llm") is not True:
        return None
    model_value = ctx.config.get("model")
    model = str(model_value) if model_value else None
    try:
        return LLMClient.from_env(
            model=model,
            usage_callback=lambda usage: record_llm_usage(ctx, usage),
        )
    except LLMError as exc:
        ctx.emit("stage_message", f"LLM unavailable; using offline fallback. {exc}")
        return None


def _read_paper_notes_with_llm(
    ctx: Context,
    client: LLMClient,
    papers: list[dict[str, Any]],
) -> list[dict[str, str]]:
    """Create one structured note per paper using concurrent LLM requests.

    Args:
        ctx: Current pipeline context.
        client: Configured LLM client.
        papers: Paper metadata loaded from ``papers.jsonl``.

    Returns:
        Normalized paper notes suitable for ``paper_notes.json``.
    """
    requests = [
        LLMRequest(
            system=READ_SYSTEM,
            user=paper_note_user_prompt(json.dumps(paper, indent=2, ensure_ascii=False)),
            label=_paper_id(paper, index),
        )
        for index, paper in enumerate(papers, start=1)
    ]
    workers = min(_llm_max_workers(ctx), len(requests))
    ctx.emit(
        "stage_message",
        f"Calling LLM for {len(requests)} paper note(s) with {workers} worker(s).",
    )
    responses = client.ask_json_many(requests, max_workers=workers)
    return [
        _normalize_paper_note(paper, response, index)
        for index, (paper, response) in enumerate(zip(papers, responses), start=1)
    ]


def _normalize_paper_note(
    paper: dict[str, Any],
    response: dict[str, Any],
    index: int,
) -> dict[str, str]:
    """Merge model output with source metadata into a stable note schema."""
    return {
        "paper_id": _text_field(response, "paper_id") or _paper_id(paper, index),
        "title": str(paper.get("title", "")),
        "problem": _text_field(response, "problem") or "Not specified.",
        "method": _text_field(response, "method") or "Not specified.",
        "limitation": _text_field(response, "limitation") or "Not specified.",
        "relevance": _text_field(response, "relevance") or "Not specified.",
    }


def _notes_markdown(notes: list[dict[str, str]]) -> str:
    """Render structured paper notes as inspectable Markdown."""
    lines = ["# Literature Notes", ""]
    for note in notes:
        lines.append(f"## {note['paper_id']}")
        if note.get("title"):
            lines.append(f"Title: {note['title']}")
        lines.extend(
            [
                f"- Problem: {note['problem']}",
                f"- Method: {note['method']}",
                f"- Limitation: {note['limitation']}",
                f"- Relevance: {note['relevance']}",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def _paper_id(paper: dict[str, Any], index: int) -> str:
    """Return a stable paper identifier for prompts and generated notes."""
    value = paper.get("id")
    return str(value) if value else f"paper-{index:03d}"


def _llm_max_workers(ctx: Context) -> int:
    """Read the configured LLM worker limit, falling back to a safe default."""
    value = ctx.config.get("llm_max_workers", 4)
    try:
        workers = int(value)
    except (TypeError, ValueError):
        workers = 4
    return max(1, workers)


def _text_field(data: dict[str, object], key: str) -> str:
    value = data.get(key)
    return value.strip() if isinstance(value, str) else ""


def _ensure_heading(markdown: str, heading: str) -> str:
    stripped = markdown.strip()
    if stripped.startswith("#"):
        return stripped + "\n"
    return f"# {heading}\n\n{stripped}\n"
