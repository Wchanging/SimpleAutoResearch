from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from simple_ar.artifacts import read_json, read_jsonl, read_text, write_json, write_jsonl, write_text
from simple_ar.code_task.index import build_codebase_index
from simple_ar.code_task.edit_scope import is_protected_edit_path
from simple_ar.experiment.runner import run_experiment
from simple_ar.experiment.code_task_experiment import (
    CODE_TASK_PROJECT_TEMPLATE,
    build_code_task_experiment_script,
    code_task_experiment_spec,
    is_code_task_experiment_template,
    prepare_code_task_experiment,
    write_code_task_experiment_meta,
)
from simple_ar.experiment.templates import build_experiment_code
from simple_ar.literature.arxiv_client import ArxivRateLimitError, ArxivSearchClient, LiteratureSearchError
from simple_ar.literature.bibtex import papers_to_bibtex
from simple_ar.literature.cache import get_cached, put_cache
from simple_ar.literature.models import Paper
from simple_ar.literature.openalex_client import OpenAlexSearchClient, OpenAlexSearchError
from simple_ar.literature.verify import CitationError, validate_citations
from simple_ar.llm import LLMClient, LLMError, LLMRequest
from simple_ar.pipeline import Context, utcnow_iso
from simple_ar.research.prompts import (
    CODE_TASK_DESIGN_SYSTEM,
    PLAN_SYSTEM,
    READ_SYSTEM,
    REPORT_SYSTEM,
    SYNTHESIZE_SYSTEM,
    code_task_design_user_prompt,
    paper_note_user_prompt,
    plan_user_prompt,
    report_user_prompt,
    synthesize_user_prompt,
)
from simple_ar.report_quality import build_report_quality
from simple_ar.retrieval.evidence import collect_stage_evidence, format_evidence_snippets
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
        "source": "arxiv" if ctx.config.get("use_arxiv") is True else "fixture",
        "status": "pending",
    }
    if ctx.config.get("use_arxiv") is True:
        papers, meta_update = _live_literature_search(ctx, query, max_papers, problem)
        meta.update(meta_update)
    else:
        ctx.emit("stage_message", "Using fixture paper metadata because --offline-search is enabled.")
        papers = _fixture_papers(problem)
        meta.update({"source": "fixture", "status": "offline_fixture", "returned": len(papers)})

    write_jsonl(ctx.artifact_path("papers.jsonl"), [paper.to_row() for paper in papers])
    write_json(ctx.artifact_path("search_meta.json"), meta)


def _live_literature_search(
    ctx: Context,
    query: str,
    max_papers: int,
    problem: str,
) -> tuple[list[Paper], dict[str, object]]:
    """Search real literature sources before considering explicit fixture fallback.

    Args:
        ctx: Current pipeline context.
        query: Literature query.
        max_papers: Result limit per provider.
        problem: Problem artifact used only for explicit fixture fallback.

    Returns:
        ``(papers, metadata_update)`` for the selected source.

    Raises:
        LiteratureSearchError: If no live or cached metadata is available and
            fixture fallback has not been explicitly enabled.
    """
    attempts: list[dict[str, object]] = []

    openalex_result = _try_openalex(ctx, query, max_papers, attempts)
    if openalex_result is not None:
        papers, source = openalex_result
        return papers, {
            "source": source,
            "status": "ok" if source == "openalex" else "cache",
            "returned": len(papers),
            "attempts": attempts,
        }

    arxiv_result = _try_arxiv(ctx, query, max_papers, attempts)
    if arxiv_result is not None:
        papers, source = arxiv_result
        return papers, {
            "source": source,
            "status": "ok" if source == "arxiv" else "cache",
            "returned": len(papers),
            "attempts": attempts,
        }

    if _allow_fixture_fallback(ctx):
        ctx.emit(
            "stage_message",
            "No live or cached literature metadata available; using fixture metadata because "
            "--allow-fixture-fallback is enabled.",
        )
        papers = _fixture_papers(problem)
        return papers, {
            "source": "fixture",
            "status": "fixture_fallback",
            "allow_fixture_fallback": True,
            "returned": len(papers),
            "attempts": attempts,
        }

    raise LiteratureSearchError(_live_search_failure_message(attempts))


def _try_openalex(
    ctx: Context,
    query: str,
    max_papers: int,
    attempts: list[dict[str, object]],
) -> tuple[list[Paper], str] | None:
    """Try OpenAlex live search and, if allowed, its cache."""
    try:
        ctx.emit("stage_message", f"Searching OpenAlex for up to {max_papers} paper(s).")
        papers = OpenAlexSearchClient().search(query, max_results=max_papers)
        if papers:
            put_cache(query, "openalex", max_papers, [paper.to_row() for paper in papers])
            attempts.append({"source": "openalex", "status": "ok", "returned": len(papers)})
            return papers, "openalex"
        attempts.append({"source": "openalex", "status": "empty", "returned": 0})
    except OpenAlexSearchError as exc:
        attempts.append({"source": "openalex", "status": "error", "error": str(exc)})
        ctx.emit("stage_message", f"OpenAlex search failed. {exc}")

    if ctx.config.get("strict_search") is True:
        return None
    cached = _cached_papers(ctx, query, max_papers, source="openalex")
    if cached is not None:
        attempts.append({"source": "openalex_cache", "status": "cache", "returned": len(cached)})
        return cached, "openalex_cache"
    return None


def _try_arxiv(
    ctx: Context,
    query: str,
    max_papers: int,
    attempts: list[dict[str, object]],
) -> tuple[list[Paper], str] | None:
    """Try arXiv live search and, if allowed, its cache."""
    try:
        ctx.emit("stage_message", f"Searching arXiv for up to {max_papers} paper(s).")
        papers = ArxivSearchClient(page_size=max_papers).search(query, max_results=max_papers)
        if papers:
            put_cache(query, "arxiv", max_papers, [paper.to_row() for paper in papers])
            attempts.append({"source": "arxiv", "status": "ok", "returned": len(papers)})
            return papers, "arxiv"
        attempts.append({"source": "arxiv", "status": "empty", "returned": 0})
    except ArxivRateLimitError as exc:
        attempts.append({"source": "arxiv", "status": "rate_limited", "error": str(exc)})
        ctx.emit("stage_message", "arXiv rate limit hit; checking local cache.")
    except LiteratureSearchError as exc:
        attempts.append({"source": "arxiv", "status": "error", "error": str(exc)})
        ctx.emit("stage_message", f"arXiv search failed. {exc}")

    if ctx.config.get("strict_search") is True:
        return None
    cached = _cached_papers(ctx, query, max_papers, source="arxiv")
    if cached is not None:
        attempts.append({"source": "arxiv_cache", "status": "cache", "returned": len(cached)})
        return cached, "arxiv_cache"
    return None


def _cached_papers(
    ctx: Context,
    query: str,
    max_papers: int,
    *,
    source: str,
) -> list[Paper] | None:
    """Return cached papers after live search failure.

    Args:
        ctx: Current pipeline context for progress messages.
        query: Query used for the live search attempt.
        max_papers: Search result limit.
        source: Cache namespace, such as ``openalex`` or ``arxiv``.

    Returns:
        Cached papers, or ``None`` when no cache entry is available.
    """
    cached_rows = get_cached(query, source, max_papers)
    if cached_rows:
        ctx.emit(
            "stage_message",
            f"Using {len(cached_rows)} cached {source} paper(s) after live search failed.",
        )
        return [Paper.from_row(row) for row in cached_rows]

    ctx.emit(
        "stage_message",
        f"No cached {source} metadata available.",
    )
    return None


def _allow_fixture_fallback(ctx: Context) -> bool:
    """Return whether live search failures may fall back to fixture metadata."""
    return ctx.config.get("allow_fixture_fallback") is True


def _live_search_failure_message(attempts: list[dict[str, object]]) -> str:
    """Explain why live search failed without silently substituting fixture rows."""
    attempt_summary = "; ".join(
        f"{item.get('source')}={item.get('status')}" for item in attempts
    ) or "no provider attempts recorded"
    return (
        "No live or cached literature metadata is available. Default runs do not "
        "use fixture metadata because that would make the report look literature-backed "
        "when it is not. Retry later, lower --max-papers, run with --offline-search "
        "for tests, or add --allow-fixture-fallback for demos. "
        f"Provider attempts: {attempt_summary}"
    )


def execute_read(ctx: Context) -> None:
    papers = read_jsonl(ctx.find_artifact("papers.jsonl") or ctx.artifact_path("papers.jsonl"))
    evidence = _stage_evidence(ctx, "read")
    evidence_snippets = format_evidence_snippets(evidence)
    client = _llm_client(ctx)
    if client is not None and papers:
        try:
            notes = _read_paper_notes_with_llm(ctx, client, papers, evidence_snippets)
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
    evidence = _stage_evidence(ctx, "synthesize")
    evidence_snippets = format_evidence_snippets(evidence)
    client = _llm_client(ctx)
    if client is not None:
        try:
            ctx.emit("stage_message", "Calling LLM for synthesis.")
            response = client.ask_json(
                SYNTHESIZE_SYSTEM,
                synthesize_user_prompt(notes, paper_notes, evidence_snippets=evidence_snippets),
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
    template = _experiment_template(ctx)
    if is_code_task_experiment_template(template):
        spec = code_task_experiment_spec(_repo_root(), ctx.config)
        task_file, task_source, task_generation = _resolve_code_task_design_task(ctx, spec)
        is_generic = spec.template == CODE_TASK_PROJECT_TEMPLATE
        write_json(
            ctx.artifact_path("experiment_plan.json"),
            {
                "name": spec.name or spec.template,
                "template": spec.template,
                "mode": "embedded_code_task",
                "hypothesis": hypothesis.strip(),
                "dataset": str(spec.code_root),
                "baseline": "existing_codebase",
                "method": "llm_planned_controlled_patch",
                "metrics": [
                    "benchmark_passed",
                    "benchmark_returncode",
                    "benchmark_timed_out",
                    "changed_files",
                    "llm_patch_applied",
                    "comparison_improved",
                    "primary_metric_delta",
                ],
                "timeout_sec": _experiment_timeout(ctx),
                "code_task": {
                    "code_root": str(spec.code_root),
                    "task_file": str(task_file),
                    "task_source": task_source,
                    "generated_task_file": _relative_artifact(ctx, task_file)
                    if task_source == "generated_from_research"
                    else None,
                    "task_generation": task_generation,
                    "benchmark_command": spec.benchmark_command,
                    "config_path": spec.config_path,
                    "primary_metric": spec.primary_metric,
                    "metric_directions": spec.metric_directions,
                    "env_mode": spec.env_mode,
                    "python_executable": spec.python_executable,
                    "workspace_mode": spec.workspace_mode,
                    "workspace_include": list(spec.workspace_include),
                    "workspace_exclude": list(spec.workspace_exclude),
                    "workspace_reuse_source_venv": spec.workspace_reuse_source_venv,
                    "workspace_setup_hook": spec.workspace_setup_hook,
                    "max_file_bytes": spec.max_file_bytes,
                    "approval": "auto_approved_inside_isolated_pipeline_workspace",
                    "allow_test_changes": spec.allow_test_changes,
                    "scope": "user_project" if is_generic else "bundled_demo",
                },
            },
        )
        return

    write_json(
        ctx.artifact_path("experiment_plan.json"),
        {
            "name": "toy_text_classification",
            "template": template,
            "hypothesis": hypothesis.strip(),
            "dataset": "built_in_toy_spam",
            "baseline": "keyword_rules",
            "method": "bag_of_words_logistic_regression",
            "metrics": ["accuracy", "precision", "recall"],
            "timeout_sec": _experiment_timeout(ctx),
        },
    )


def _resolve_code_task_design_task(
    ctx: Context,
    spec: Any,
) -> tuple[Path, str, dict[str, Any]]:
    """Return the task file used by an embedded code-task experiment.

    Explicit user-provided task files remain the preferred source. For generic
    8-stage code-task runs without a task file, the design stage writes a
    generated Markdown task from earlier research artifacts. That keeps the
    standalone code-task workflow strict while allowing research-first pipeline
    runs to discover and frame the code task gradually.
    """
    if spec.task_file is not None:
        return spec.task_file, "user_file", {"mode": "user_file"}
    if spec.template != CODE_TASK_PROJECT_TEMPLATE:
        raise RuntimeError(f"Missing task file for code-task template: {spec.template}")

    task_markdown, generation = _generate_code_task_design_markdown(ctx, spec)
    task_path = ctx.artifact_path("generated_code_task.md")
    write_text(task_path, task_markdown)
    write_json(ctx.artifact_path("generated_code_task_meta.json"), generation)
    ctx.emit(
        "stage_message",
        "Generated code-task task file from research artifacts because no task_file was provided.",
    )
    return task_path, "generated_from_research", generation


def _generate_code_task_design_markdown(ctx: Context, spec: Any) -> tuple[str, dict[str, Any]]:
    """Generate a conservative code-task Markdown file for a research-first run."""
    goal = _safe_read_artifact(ctx, "goal.md")
    problem = _safe_read_artifact(ctx, "problem.md")
    synthesis = _safe_read_artifact(ctx, "synthesis.md")
    hypothesis = _safe_read_artifact(ctx, "hypothesis.md")
    codebase_summary = _codebase_design_summary(spec.code_root)
    client = _llm_client(ctx)
    if client is not None:
        try:
            ctx.emit("stage_message", "Calling LLM to derive an embedded code-task from research artifacts.")
            response = client.ask_json(
                CODE_TASK_DESIGN_SYSTEM,
                code_task_design_user_prompt(
                    topic=ctx.topic,
                    goal_markdown=goal,
                    problem_markdown=problem,
                    synthesis_markdown=synthesis,
                    hypothesis_markdown=hypothesis,
                    codebase_summary_json=json.dumps(codebase_summary, indent=2, ensure_ascii=False),
                    benchmark_command=spec.benchmark_command or "",
                    primary_metric=spec.primary_metric or "",
                ),
                label="design.code_task_task",
            )
            task = _text_field(response, "task_markdown")
            if task:
                return _ensure_heading(task, "Code Task"), {
                    "mode": "llm",
                    "source_artifacts": ["goal.md", "problem.md", "synthesis.md", "hypothesis.md"],
                    "codebase_summary": codebase_summary,
                }
        except LLMError as exc:
            ctx.emit("stage_message", f"LLM code-task design failed; using deterministic fallback. {exc}")

    return _fallback_code_task_design_markdown(
        topic=ctx.topic,
        goal=goal,
        problem=problem,
        synthesis=synthesis,
        hypothesis=hypothesis,
        codebase_summary=codebase_summary,
        benchmark_command=spec.benchmark_command or "",
        primary_metric=spec.primary_metric or "",
    ), {
        "mode": "fallback",
        "source_artifacts": ["goal.md", "problem.md", "synthesis.md", "hypothesis.md"],
        "codebase_summary": codebase_summary,
    }


def _codebase_design_summary(code_root: Path) -> dict[str, Any]:
    """Build a compact codebase summary for task generation prompts."""
    try:
        index = build_codebase_index(code_root)
    except Exception as exc:
        return {
            "status": "unavailable",
            "error": str(exc),
            "code_root": str(code_root),
        }
    project = index.get("project", {})
    files = index.get("files", [])
    source_files = [
        {
            "path": item.get("path"),
            "kind": item.get("kind"),
            "role_tags": item.get("role_tags", []),
            "summary": item.get("summary", ""),
        }
        for item in files
        if isinstance(item, dict) and "test" not in set(item.get("role_tags", []))
    ][:20]
    protected_files = [
        item.get("path")
        for item in files
        if isinstance(item, dict) and is_protected_edit_path(str(item.get("path", "")))
    ][:20]
    return {
        "status": "ok",
        "code_root": str(code_root),
        "project": {
            "file_count": project.get("file_count", 0),
            "python_file_count": project.get("python_file_count", 0),
            "test_file_count": project.get("test_file_count", 0),
            "entrypoint_candidates": project.get("entrypoint_candidates", []),
            "common_imports": project.get("common_imports", [])[:10],
        },
        "source_files": source_files,
        "protected_validation_files": protected_files,
    }


def _fallback_code_task_design_markdown(
    *,
    topic: str,
    goal: str,
    problem: str,
    synthesis: str,
    hypothesis: str,
    codebase_summary: dict[str, Any],
    benchmark_command: str,
    primary_metric: str,
) -> str:
    """Create a deterministic task file when task-generation LLM calls fail."""
    project = codebase_summary.get("project", {}) if isinstance(codebase_summary, dict) else {}
    files = codebase_summary.get("source_files", []) if isinstance(codebase_summary, dict) else []
    file_lines = [
        f"- `{item.get('path')}`: {item.get('summary', '')}"
        for item in files
        if isinstance(item, dict) and item.get("path")
    ][:8]
    if not file_lines:
        file_lines = ["- Inspect the source files selected by the code-task planner."]
    metric_text = primary_metric or "the configured benchmark metrics"
    command_text = benchmark_command or "the configured benchmark command"
    context = _first_non_empty_markdown_body(hypothesis, synthesis, problem, goal)
    return (
        "# Code Task\n\n"
        "## Objective\n\n"
        f"Improve the existing codebase for the research goal `{topic}` with a small, benchmarkable patch.\n\n"
        "## Research Motivation\n\n"
        f"{context}\n\n"
        "## Target Codebase Signals\n\n"
        f"- Python files: {project.get('python_file_count', 'unknown')}\n"
        f"- Test files: {project.get('test_file_count', 'unknown')}\n"
        f"- Entrypoint candidates: {', '.join(str(item) for item in project.get('entrypoint_candidates', [])[:5]) or 'unknown'}\n"
        + "\n".join(file_lines)
        + "\n\n"
        "## Constraints\n\n"
        "- Modify implementation/source files only; do not edit tests, benchmark files, or validation targets.\n"
        "- Keep the patch small and readable.\n"
        "- Preserve public APIs unless a minimal internal API change is necessary.\n"
        "- Avoid adding heavyweight dependencies or resource-intensive training loops.\n\n"
        "## Success Criteria\n\n"
        f"- `{command_text}` completes successfully after the patch.\n"
        f"- `{metric_text}` improves or at least does not regress under the recorded metric direction.\n"
        "- The patch remains easy to review through `code_task/patch.diff`.\n\n"
        "## Suggested Investigation Steps\n\n"
        "- Inspect the codebase index and benchmark output before editing.\n"
        "- Identify the smallest source-level bottleneck or modeling weakness connected to the research synthesis.\n"
        "- Propose a controlled old/new text edit, then validate with the recorded benchmark.\n"
    )


def _first_non_empty_markdown_body(*values: str) -> str:
    """Return a compact Markdown body from the first non-empty artifact."""
    for value in values:
        body = _markdown_body(value)
        if body:
            return body[:1200]
    return "The earlier research artifacts were thin; treat this as an exploratory local improvement task."


def execute_code(ctx: Context) -> None:
    plan = read_json(ctx.find_artifact("experiment_plan.json") or ctx.artifact_path("experiment_plan.json"))
    if is_code_task_experiment_template(plan.get("template")):
        _execute_code_task_experiment_code(ctx, plan)
        return

    ctx.emit("stage_message", f"Generating experiment from template `{plan.get('template', '')}`.")
    code = build_experiment_code(plan)
    write_text(ctx.artifact_path("experiment.py"), code)


def _execute_code_task_experiment_code(ctx: Context, plan: dict[str, Any]) -> None:
    """Prepare an embedded code-task experiment and write its run harness."""
    ctx.emit("stage_message", "Preparing embedded LLM code-task experiment.")
    spec = code_task_experiment_spec(
        _repo_root(),
        ctx.config,
        task_file_override=_code_task_task_file_override(ctx, plan),
    )
    result = prepare_code_task_experiment(
        code_task_run_dir=ctx.stage_dir() / "code_task_run",
        spec=spec,
        model=_model(ctx),
        use_llm=ctx.config.get("use_llm") is True,
        timeout_sec=int(plan.get("timeout_sec") or _experiment_timeout(ctx)),
        message_callback=lambda message: ctx.emit("stage_message", message),
    )
    write_text(
        ctx.artifact_path("experiment.py"),
        build_code_task_experiment_script(
            changed_files=result.changed_files,
            timeout_sec=int(plan.get("timeout_sec") or _experiment_timeout(ctx)),
        ),
    )
    write_code_task_experiment_meta(ctx.artifact_path("code_task_experiment.json"), result)


def _code_task_task_file_override(ctx: Context, plan: dict[str, Any]) -> Path | None:
    """Resolve a design-stage generated task file for embedded code-task runs."""
    code_task = plan.get("code_task")
    if not isinstance(code_task, dict):
        return None
    generated = code_task.get("generated_task_file")
    if isinstance(generated, str) and generated.strip():
        path = Path(generated)
        return path if path.is_absolute() else ctx.run_dir / path
    if code_task.get("task_source") == "generated_from_research":
        task_file = code_task.get("task_file")
        if isinstance(task_file, str) and task_file.strip():
            path = Path(task_file)
            return path if path.is_absolute() else ctx.run_dir / path
    return None


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
    results_path = ctx.find_artifact("results.json")
    results_present = results_path is not None
    results = read_json(results_path) if results_present else {}
    paper_rows = read_jsonl(ctx.find_artifact("papers.jsonl") or ctx.artifact_path("papers.jsonl"))
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
            )
        else:
            report = _build_report(ctx, goal, problem, search_meta, synthesis, hypothesis, plan, results, papers)
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
    evidence_snippets: str,
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


def _research_discussion_markdown(ctx: Context, synthesis: str, hypothesis: str) -> str:
    """Discuss literature-only findings without implying experiments."""
    base = _discussion_markdown(synthesis, hypothesis)
    if not base:
        base = "The available literature metadata was synthesized into a small set of themes."
    return (
        f"{base} The current report is literature-only; experiments are left for a later "
        "workflow once a concrete implementation target is selected."
    )


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
        "source_plan.json",
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
        candidate = ctx.run_dir / filename
        if candidate.exists():
            path = candidate
    if path is None:
        return None
    return _relative_artifact(ctx, path)


def _stage_evidence(ctx: Context, stage: str) -> list[dict[str, Any]]:
    """Collect local retrieval evidence for a stage when enabled.

    Args:
        ctx: Current pipeline context.
        stage: Logical stage name used in ``source_plan.json``.

    Returns:
        Evidence rows with source paths and line ranges. Empty when retrieval is
        explicitly disabled.
    """
    if ctx.config.get("use_retrieval", True) is False:
        return []
    top_k = _retrieval_top_k(ctx)
    try:
        rows = collect_stage_evidence(ctx.run_dir, ctx.topic, stage, top_k=top_k)
        if rows:
            ctx.emit(
                "stage_message",
                f"Retrieved {len(rows)} source snippet(s) for {stage} evidence.",
            )
        return rows
    except Exception as exc:
        ctx.emit("stage_message", f"Retrieval evidence failed for {stage}; continuing. {exc}")
        return []


def _retrieval_top_k(ctx: Context) -> int:
    """Read the per-query retrieval result limit with a conservative default."""
    value = ctx.config.get("retrieval_top_k", 4)
    try:
        top_k = int(value)
    except (TypeError, ValueError):
        top_k = 4
    return min(max(1, top_k), 20)


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


def _model(ctx: Context) -> str | None:
    """Read the configured model name for helper workflows."""
    model_value = ctx.config.get("model")
    return str(model_value) if model_value else None


def _repo_root() -> Path:
    """Return the repository root for bundled examples in editable checkouts."""
    return Path(__file__).resolve().parents[2]


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
    evidence_snippets: str = "",
) -> list[dict[str, str]]:
    """Create one structured note per paper using concurrent LLM requests.

    Args:
        ctx: Current pipeline context.
        client: Configured LLM client.
        papers: Paper metadata loaded from ``papers.jsonl``.
        evidence_snippets: Source-labelled retrieval snippets selected for the
            read stage.

    Returns:
        Normalized paper notes suitable for ``paper_notes.json``.
    """
    requests = [
        LLMRequest(
            system=READ_SYSTEM,
            user=paper_note_user_prompt(
                json.dumps(paper, indent=2, ensure_ascii=False),
                evidence_snippets=evidence_snippets,
            ),
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
