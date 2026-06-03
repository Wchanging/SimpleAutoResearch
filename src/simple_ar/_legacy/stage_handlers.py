from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from simple_ar.core.artifacts import read_json, read_jsonl, read_text, write_json, write_jsonl, write_text
from simple_ar.code_task.analysis.index import build_codebase_index
from simple_ar.code_task.editing.scope import is_protected_edit_path
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
from simple_ar.literature.semantic_scholar_client import SemanticScholarSearchClient, SemanticScholarSearchError
from simple_ar.literature.verify import CitationError, validate_citations
from simple_ar.integrations.llm import LLMClient, LLMError, LLMRequest
from simple_ar.core.pipeline import Context, utcnow_iso
from simple_ar.research.connectors import (
    ArxivConnector,
    LocalFileConnector,
    OpenAlexConnector,
    SemanticScholarConnector,
)
from simple_ar.research.contracts import QueryPlan, ResearchQuestion, SourcePlan
from simple_ar.research.evidence.coverage import build_coverage_report, coverage_report_markdown
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
    SEARCH_META,
    SEARCH_PAPERS,
    SEARCH_RESEARCH_PLAN,
    SEARCH_RETRIEVAL_ROUNDS,
    SEARCH_RETRIEVAL_SELECTION,
    SYNTHESIS_EVIDENCE_PACK_JSON,
    SYNTHESIS_EVIDENCE_PACK_MD,
    SYNTHESIS_GAP_SUMMARY,
    SYNTHESIS_IDEA_CANDIDATES,
    SYNTHESIS_NOVELTY_CHECKS,
    SYNTHESIS_BRIEF_JSON,
    build_research_plan_artifact,
    write_design_handoff_artifacts,
    write_read_card_artifacts,
    write_read_review_artifacts,
    write_search_document_artifacts,
    write_synthesis_brief_artifact,
    write_synthesis_evidence_artifacts,
)
from simple_ar.research.prompts import (
    CODE_TASK_DESIGN_SYSTEM,
    PLAN_SYSTEM,
    READ_SYSTEM,
    RESEARCH_PLANNER_SYSTEM,
    REPORT_SYSTEM,
    SYNTHESIZE_SYSTEM,
    code_task_design_user_prompt,
    paper_note_user_prompt,
    plan_user_prompt,
    read_coarse_screening_user_prompt,
    read_rerank_user_prompt,
    research_planner_user_prompt,
    report_user_prompt,
    synthesize_user_prompt,
)
from simple_ar.research.planning.planner import build_llm_research_plan, build_query_plan, build_research_questions
from simple_ar.research.evidence.retrieval import RetrievalCandidate, select_retrieval_candidates
from simple_ar.research.service import (
    load_hypothesis_markdown,
    load_notes_markdown,
    load_paper_notes_json,
    load_problem_markdown,
    load_search_paper_rows,
    safe_read_artifact,
    safe_read_json_artifact,
)
from simple_ar.research.sources.base import SearchQuery, build_source_plan, primary_query
from simple_ar.report.quality import build_report_quality
from simple_ar.retrieval.evidence import collect_stage_evidence, format_evidence_snippets
from simple_ar.experiment.service import load_experiment_plan, load_experiment_script_path
from simple_ar.app.usage import record_llm_usage


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
    problem = load_problem_markdown(ctx)
    query = _search_query(ctx, problem)
    max_papers = _max_papers(ctx)
    research_questions, query_plan = _plan_research_retrieval(ctx, problem, query)
    source_config = dict(ctx.config)
    if query_plan.queries:
        source_config["research_queries"] = list(query_plan.queries)
    source_plan = build_source_plan(
        topic=ctx.topic,
        problem_markdown=problem,
        config=source_config,
        default_query=query,
        default_max_results=max_papers,
    )
    write_json(
        ctx.artifact_path(SEARCH_RESEARCH_PLAN),
        build_research_plan_artifact(
            questions=research_questions,
            query_plan=query_plan,
            source_plan=source_plan,
        ),
    )
    planned_query = primary_query(source_plan) or query

    papers: list[Paper]
    meta: dict[str, object] = {
        "query": planned_query,
        "queries": list(source_plan.queries),
        "max_papers": max_papers,
        "sources": list(source_plan.sources),
        "source": "arxiv" if ctx.config.get("use_arxiv") is True else "fixture",
        "source_plan": source_plan.to_row(),
        "research_plan": SEARCH_RESEARCH_PLAN,
        "research_planner": query_plan.planner,
        "query_plan_max_rounds": query_plan.max_rounds,
        "query_plan_auto_expansion": query_plan.auto_expansion,
        "required_facets": list(query_plan.required_facets),
        "status": "pending",
    }
    if _should_use_source_plan(ctx, source_plan):
        papers, meta_update = _live_literature_search(ctx, source_plan, problem, query_plan, research_questions)
        meta.update(meta_update)
    else:
        ctx.emit("stage_message", "Using fixture paper metadata because --offline-search is enabled.")
        papers = _fixture_papers(problem)
        retrieval_rows = [
            _retrieval_round_row(
                round_index=1,
                query_index=1,
                query=planned_query,
                source="fixture",
                status="offline_fixture",
                returned=len(papers),
            )
        ]
        candidates = [
            RetrievalCandidate(
                paper=paper,
                source="fixture",
                query=planned_query,
                query_index=1,
                round_index=1,
                returned_source="fixture",
            )
            for paper in papers
        ]
        papers, selection_rows = select_retrieval_candidates(
            candidates,
            max_documents=max_papers,
            negative_terms=query_plan.negative_terms,
            priority_facets=query_plan.required_facets,
        )
        coverage_report = build_coverage_report(
            topic=ctx.topic,
            questions=research_questions,
            query_plan=query_plan,
            selection_rows=selection_rows,
            retrieval_rows=retrieval_rows,
            max_documents=max_papers,
            next_query_limit=0,
        )
        write_jsonl(ctx.artifact_path(SEARCH_RETRIEVAL_ROUNDS), retrieval_rows)
        write_jsonl(ctx.artifact_path(SEARCH_RETRIEVAL_SELECTION), selection_rows)
        _write_coverage_artifacts(ctx, coverage_report)
        meta.update(
            {
                "source": "fixture",
                "status": "offline_fixture",
                "returned": len(papers),
                "retrieval_rounds": SEARCH_RETRIEVAL_ROUNDS,
                "retrieval_selection": SEARCH_RETRIEVAL_SELECTION,
                "coverage_report": SEARCH_COVERAGE_JSON,
                "attempt_count": len(retrieval_rows),
                "candidate_selection_count": len(selection_rows),
                "kept_after_retrieval_selection": len(papers),
            }
        )

    meta.update(
        write_search_document_artifacts(
            stage_dir=ctx.stage_dir(),
            topic=ctx.topic,
            papers=papers,
            source_plan=source_plan,
        )
    )
    write_jsonl(ctx.artifact_path(SEARCH_PAPERS), [paper.to_row() for paper in papers])
    write_json(ctx.artifact_path(SEARCH_META), meta)


def _plan_research_retrieval(
    ctx: Context,
    problem: str,
    query: str,
) -> tuple[list[ResearchQuestion], QueryPlan]:
    """Create research questions and query plan, using LLM planning when enabled."""
    deterministic_questions = build_research_questions(
        topic=ctx.topic,
        problem_markdown=problem,
        config=ctx.config,
    )
    deterministic_plan = build_query_plan(
        topic=ctx.topic,
        problem_markdown=problem,
        config=ctx.config,
        default_query=query,
        questions=deterministic_questions,
    )
    planner_mode = _research_planner_mode(ctx.config.get("research_planner"))
    if planner_mode == "deterministic":
        return deterministic_questions, deterministic_plan
    if ctx.config.get("use_llm") is not True:
        return deterministic_questions, deterministic_plan

    client = _llm_client(ctx)
    if client is None:
        return deterministic_questions, deterministic_plan

    ctx.emit("stage_message", "Calling LLM for research question and query planning.")
    try:
        response = client.ask_json(
            RESEARCH_PLANNER_SYSTEM,
            research_planner_user_prompt(
                topic=ctx.topic,
                problem_markdown=problem,
                seed_queries_json=json.dumps(deterministic_plan.seed_queries, ensure_ascii=False),
                required_facets_json=json.dumps(deterministic_plan.required_facets, ensure_ascii=False),
                max_queries=_research_query_cap(ctx.config, len(deterministic_plan.queries)),
                max_rounds=deterministic_plan.max_rounds,
                mode=str(ctx.config.get("research_mode") or "standard"),
            ),
            label="research-planner",
        )
        return build_llm_research_plan(
            topic=ctx.topic,
            problem_markdown=problem,
            config=ctx.config,
            default_query=query,
            data=response,
        )
    except (LLMError, ValueError) as exc:
        ctx.emit("stage_message", f"LLM research planning failed; using deterministic fallback. {exc}")
        return deterministic_questions, deterministic_plan


def _research_planner_mode(value: object) -> str:
    text = str(value or "auto").strip().lower()
    return text if text in {"auto", "llm", "deterministic"} else "auto"


def _research_query_cap(config: dict[str, object], default: int) -> int:
    value = config.get("research_max_queries")
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return min(value, 12)
    return min(max(default, 1), 12)


def _live_literature_search(
    ctx: Context,
    source_plan: SourcePlan,
    problem: str,
    query_plan: QueryPlan,
    research_questions: list[ResearchQuestion],
) -> tuple[list[Paper], dict[str, object]]:
    """Search real literature sources before considering explicit fixture fallback.

    Args:
        ctx: Current pipeline context.
        source_plan: Planned queries, providers, local documents, and budget.
        problem: Problem artifact used only for explicit fixture fallback.

    Returns:
        ``(papers, metadata_update)`` for the selected source.

    Raises:
        LiteratureSearchError: If no live or cached metadata is available and
            fixture fallback has not been explicitly enabled.
    """
    max_papers = source_plan.max_results_per_query
    max_documents = _research_document_cap(source_plan, max_papers)
    candidate_cap = _candidate_collection_cap(max_documents)
    retrieval_rows: list[dict[str, object]] = []
    candidates: list[RetrievalCandidate] = []
    query_specs = _query_specs_by_query(query_plan)
    query_attempts = _planned_query_attempts(source_plan)

    _collect_retrieval_round(
        ctx,
        source_plan,
        queries=query_attempts,
        query_specs=query_specs,
        retrieval_rows=retrieval_rows,
        candidates=candidates,
        round_index=1,
        max_documents=candidate_cap,
    )

    if candidates:
        papers, selection_rows = select_retrieval_candidates(
            candidates,
            max_documents=max_documents,
            negative_terms=query_plan.negative_terms,
            priority_facets=query_plan.required_facets,
        )
        coverage_report = build_coverage_report(
            topic=ctx.topic,
            questions=research_questions,
            query_plan=query_plan,
            selection_rows=selection_rows,
            retrieval_rows=retrieval_rows,
            max_documents=max_documents,
            next_query_limit=_follow_up_query_limit(source_plan),
        )
        follow_up_queries = _coverage_follow_up_queries(coverage_report)
        if len(papers) < max_documents and query_plan.max_rounds > 1 and follow_up_queries:
            ctx.emit(
                "stage_message",
                f"Coverage gaps remain; running {len(follow_up_queries)} follow-up query attempt(s).",
            )
            follow_up_specs = _coverage_follow_up_specs(coverage_report)
            _collect_retrieval_round(
                ctx,
                source_plan,
                queries=follow_up_queries,
                query_specs=follow_up_specs,
                retrieval_rows=retrieval_rows,
                candidates=candidates,
                round_index=2,
                max_documents=candidate_cap,
                start_query_index=len(query_attempts) + 1,
            )
            papers, selection_rows = select_retrieval_candidates(
                candidates,
                max_documents=max_documents,
                negative_terms=query_plan.negative_terms,
                priority_facets=query_plan.required_facets,
            )
            coverage_report = build_coverage_report(
                topic=ctx.topic,
                questions=research_questions,
                query_plan=query_plan,
                selection_rows=selection_rows,
                retrieval_rows=retrieval_rows,
                max_documents=max_documents,
                next_query_limit=0,
            )
        write_jsonl(ctx.artifact_path(SEARCH_RETRIEVAL_ROUNDS), retrieval_rows)
        write_jsonl(ctx.artifact_path(SEARCH_RETRIEVAL_SELECTION), selection_rows)
        _write_coverage_artifacts(ctx, coverage_report)
        selected_sources = sorted({paper.source for paper in papers}) or ["unknown"]
        return papers, {
            "source": selected_sources[0] if len(selected_sources) == 1 else "mixed",
            "sources_used": selected_sources,
            "status": "ok",
            "returned": len(papers),
            "retrieval_rounds": SEARCH_RETRIEVAL_ROUNDS,
            "retrieval_selection": SEARCH_RETRIEVAL_SELECTION,
            "coverage_report": SEARCH_COVERAGE_JSON,
            "coverage_status": coverage_report.get("status"),
            "missing_facets": coverage_report.get("missing_facets", []),
            "attempt_count": len(retrieval_rows),
            "candidate_selection_count": len(selection_rows),
            "kept_after_retrieval_selection": len(papers),
            "executed_retrieval_rounds": coverage_report.get("retrieval", {}).get("executed_rounds", 1)
            if isinstance(coverage_report.get("retrieval"), dict)
            else 1,
            "planned_retrieval_rounds": query_plan.max_rounds,
        }

    if _allow_fixture_fallback(ctx):
        ctx.emit(
            "stage_message",
            "No live or cached literature metadata available; using fixture metadata because "
            "--allow-fixture-fallback is enabled.",
        )
        fixture_papers = _fixture_papers(problem)
        fixture_query = primary_query(source_plan)
        fixture_candidates = [
            RetrievalCandidate(
                paper=paper,
                source="fixture",
                query=fixture_query,
                query_index=1,
                round_index=1,
                returned_source="fixture",
            )
            for paper in fixture_papers
        ]
        papers, selection_rows = select_retrieval_candidates(
            fixture_candidates,
            max_documents=max_documents,
            negative_terms=query_plan.negative_terms,
            priority_facets=query_plan.required_facets,
        )
        retrieval_rows.append(
            _retrieval_round_row(
                round_index=1,
                query_index=1,
                query=fixture_query,
                source="fixture",
                status="fixture_fallback",
                returned=len(fixture_papers),
            )
        )
        coverage_report = build_coverage_report(
            topic=ctx.topic,
            questions=research_questions,
            query_plan=query_plan,
            selection_rows=selection_rows,
            retrieval_rows=retrieval_rows,
            max_documents=max_documents,
            next_query_limit=0,
        )
        write_jsonl(ctx.artifact_path(SEARCH_RETRIEVAL_ROUNDS), retrieval_rows)
        write_jsonl(ctx.artifact_path(SEARCH_RETRIEVAL_SELECTION), selection_rows)
        _write_coverage_artifacts(ctx, coverage_report)
        return papers, {
            "source": "fixture",
            "status": "fixture_fallback",
            "allow_fixture_fallback": True,
            "returned": len(papers),
            "retrieval_rounds": SEARCH_RETRIEVAL_ROUNDS,
            "retrieval_selection": SEARCH_RETRIEVAL_SELECTION,
            "coverage_report": SEARCH_COVERAGE_JSON,
            "coverage_status": coverage_report.get("status"),
            "missing_facets": coverage_report.get("missing_facets", []),
            "attempt_count": len(retrieval_rows),
            "candidate_selection_count": len(selection_rows),
            "kept_after_retrieval_selection": len(papers),
        }

    write_jsonl(ctx.artifact_path(SEARCH_RETRIEVAL_ROUNDS), retrieval_rows)
    write_jsonl(ctx.artifact_path(SEARCH_RETRIEVAL_SELECTION), [])
    raise LiteratureSearchError(_live_search_failure_message(retrieval_rows))


def _collect_retrieval_round(
    ctx: Context,
    source_plan: SourcePlan,
    *,
    queries: list[str],
    query_specs: dict[str, dict[str, object]],
    retrieval_rows: list[dict[str, object]],
    candidates: list[RetrievalCandidate],
    round_index: int,
    max_documents: int,
    start_query_index: int = 1,
) -> None:
    """Run one bounded retrieval round and append traces/candidates in place."""
    unique_seen = {candidate.paper.id for candidate in candidates}
    for offset, query in enumerate(queries):
        query_index = start_query_index + offset
        query_spec = query_specs.get(query, {})
        facet = str(query_spec.get("facet") or "")
        for source in source_plan.sources:
            papers, attempt = _search_source_once(
                ctx,
                source_plan,
                source=source,
                query=query,
                query_index=query_index,
                round_index=round_index,
            )
            _attach_query_trace(attempt, query_spec)
            retrieval_rows.append(attempt)
            added_count = 0
            for paper in papers:
                if paper.id in unique_seen:
                    continue
                candidates.append(
                    RetrievalCandidate(
                        paper=paper,
                        source=str(attempt.get("source") or source),
                        query=query,
                        query_index=query_index,
                        round_index=round_index,
                        facet=facet,
                        returned_source=str(attempt.get("returned_source") or attempt.get("source") or source),
                    )
                )
                unique_seen.add(paper.id)
                added_count += 1
            if added_count and len(unique_seen) >= max_documents:
                break
        if len(unique_seen) >= max_documents:
            break


def _write_coverage_artifacts(ctx: Context, report: dict[str, object]) -> None:
    write_json(ctx.artifact_path(SEARCH_COVERAGE_JSON), report)
    write_text(ctx.artifact_path(SEARCH_COVERAGE_MD), coverage_report_markdown(report))


def _coverage_follow_up_queries(report: dict[str, object]) -> list[str]:
    rows = report.get("follow_up_queries")
    if not isinstance(rows, list):
        return []
    queries: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        query = str(row.get("query") or "").strip()
        if query:
            queries.append(query)
    return queries


def _coverage_follow_up_specs(report: dict[str, object]) -> dict[str, dict[str, object]]:
    rows = report.get("follow_up_queries")
    if not isinstance(rows, list):
        return {}
    specs: dict[str, dict[str, object]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        query = str(row.get("query") or "").strip()
        if not query:
            continue
        specs[query] = {
            "query": query,
            "facet": str(row.get("facet") or ""),
            "rationale": str(row.get("reason") or "coverage_follow_up"),
        }
    return specs


def _follow_up_query_limit(source_plan: SourcePlan) -> int:
    value = source_plan.budget.get("max_follow_up_queries")
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return min(value, 5)
    return 3


def _search_source_once(
    ctx: Context,
    source_plan: SourcePlan,
    *,
    source: str,
    query: str,
    query_index: int,
    round_index: int,
) -> tuple[list[Paper], dict[str, object]]:
    max_papers = source_plan.max_results_per_query
    if source == "local_files":
        return _search_local_files_once(ctx, source_plan, query, query_index, round_index)
    if source == "openalex":
        return _search_openalex_once(
            ctx,
            query,
            max_papers,
            query_index=query_index,
            round_index=round_index,
            cache_enabled=source_plan.cache_enabled,
        )
    if source == "semantic_scholar":
        return _search_semantic_scholar_once(
            ctx,
            query,
            max_papers,
            query_index=query_index,
            round_index=round_index,
            cache_enabled=source_plan.cache_enabled,
        )
    if source == "arxiv":
        return _search_arxiv_once(
            ctx,
            query,
            max_papers,
            query_index=query_index,
            round_index=round_index,
            cache_enabled=source_plan.cache_enabled,
        )
    return [], _retrieval_round_row(
        round_index=round_index,
        query_index=query_index,
        query=query,
        source=source,
        status="unsupported",
        returned=0,
    )


def _search_local_files_once(
    ctx: Context,
    source_plan: SourcePlan,
    query: str,
    query_index: int,
    round_index: int,
) -> tuple[list[Paper], dict[str, object]]:
    if not source_plan.local_documents:
        return [], _retrieval_round_row(
            round_index=round_index,
            query_index=query_index,
            query=query,
            source="local_files",
            status="skipped",
            returned=0,
            reason="no local_documents",
        )
    ctx.emit(
        "stage_message",
        f"Searching {len(source_plan.local_documents)} local document(s) for `{query}`.",
    )
    response = LocalFileConnector(source_plan.local_documents).search(
        SearchQuery(query=query, max_results=source_plan.max_results_per_query)
    )
    status = "ok" if response.papers else "empty"
    return response.papers, _retrieval_round_row(
        round_index=round_index,
        query_index=query_index,
        query=query,
        source="local_files",
        status=status,
        returned=len(response.papers),
    )


def _search_openalex_once(
    ctx: Context,
    query: str,
    max_papers: int,
    *,
    query_index: int,
    round_index: int,
    cache_enabled: bool,
) -> tuple[list[Paper], dict[str, object]]:
    try:
        ctx.emit("stage_message", f"Searching OpenAlex for up to {max_papers} paper(s) with `{query}`.")
        response = OpenAlexConnector(OpenAlexSearchClient()).search(
            SearchQuery(query=query, max_results=max_papers)
        )
        papers = response.papers
        if papers:
            put_cache(query, "openalex", max_papers, [paper.to_row() for paper in papers])
        return papers, _retrieval_round_row(
            round_index=round_index,
            query_index=query_index,
            query=query,
            source="openalex",
            status="ok" if papers else "empty",
            returned=len(papers),
        )
    except OpenAlexSearchError as exc:
        ctx.emit("stage_message", f"OpenAlex search failed. {exc}")
        if ctx.config.get("strict_search") is not True and cache_enabled:
            cached = _cached_papers(ctx, query, max_papers, source="openalex")
            if cached is not None:
                return cached, _retrieval_round_row(
                    round_index=round_index,
                    query_index=query_index,
                    query=query,
                    source="openalex",
                    returned_source="openalex_cache",
                    status="cache",
                    returned=len(cached),
                )
        return [], _retrieval_round_row(
            round_index=round_index,
            query_index=query_index,
            query=query,
            source="openalex",
            status="error",
            returned=0,
            error=str(exc),
        )


def _search_semantic_scholar_once(
    ctx: Context,
    query: str,
    max_papers: int,
    *,
    query_index: int,
    round_index: int,
    cache_enabled: bool,
) -> tuple[list[Paper], dict[str, object]]:
    try:
        ctx.emit("stage_message", f"Searching Semantic Scholar for up to {max_papers} paper(s) with `{query}`.")
        response = SemanticScholarConnector(SemanticScholarSearchClient()).search(
            SearchQuery(query=query, max_results=max_papers)
        )
        papers = response.papers
        if papers:
            put_cache(query, "semantic_scholar", max_papers, [paper.to_row() for paper in papers])
        return papers, _retrieval_round_row(
            round_index=round_index,
            query_index=query_index,
            query=query,
            source="semantic_scholar",
            status="ok" if papers else "empty",
            returned=len(papers),
        )
    except SemanticScholarSearchError as exc:
        ctx.emit("stage_message", f"Semantic Scholar search failed. {exc}")
        if ctx.config.get("strict_search") is not True and cache_enabled:
            cached = _cached_papers(ctx, query, max_papers, source="semantic_scholar")
            if cached is not None:
                return cached, _retrieval_round_row(
                    round_index=round_index,
                    query_index=query_index,
                    query=query,
                    source="semantic_scholar",
                    returned_source="semantic_scholar_cache",
                    status="cache",
                    returned=len(cached),
                )
        return [], _retrieval_round_row(
            round_index=round_index,
            query_index=query_index,
            query=query,
            source="semantic_scholar",
            status="error",
            returned=0,
            error=str(exc),
        )


def _search_arxiv_once(
    ctx: Context,
    query: str,
    max_papers: int,
    *,
    query_index: int,
    round_index: int,
    cache_enabled: bool,
) -> tuple[list[Paper], dict[str, object]]:
    try:
        ctx.emit("stage_message", f"Searching arXiv for up to {max_papers} paper(s) with `{query}`.")
        response = ArxivConnector(ArxivSearchClient(page_size=max_papers)).search(
            SearchQuery(query=query, max_results=max_papers)
        )
        papers = response.papers
        if papers:
            put_cache(query, "arxiv", max_papers, [paper.to_row() for paper in papers])
        return papers, _retrieval_round_row(
            round_index=round_index,
            query_index=query_index,
            query=query,
            source="arxiv",
            status="ok" if papers else "empty",
            returned=len(papers),
        )
    except ArxivRateLimitError as exc:
        ctx.emit("stage_message", "arXiv rate limit hit; checking local cache.")
        status = "rate_limited"
        error = str(exc)
    except LiteratureSearchError as exc:
        ctx.emit("stage_message", f"arXiv search failed. {exc}")
        status = "error"
        error = str(exc)
    if ctx.config.get("strict_search") is not True and cache_enabled:
        cached = _cached_papers(ctx, query, max_papers, source="arxiv")
        if cached is not None:
            return cached, _retrieval_round_row(
                round_index=round_index,
                query_index=query_index,
                query=query,
                source="arxiv",
                returned_source="arxiv_cache",
                status="cache",
                returned=len(cached),
            )
    return [], _retrieval_round_row(
        round_index=round_index,
        query_index=query_index,
        query=query,
        source="arxiv",
        status=status,
        returned=0,
        error=error,
    )


def _retrieval_round_row(
    *,
    round_index: int,
    query_index: int,
    query: str,
    source: str,
    status: str,
    returned: int,
    returned_source: str | None = None,
    reason: str | None = None,
    error: str | None = None,
) -> dict[str, object]:
    row: dict[str, object] = {
        "schema_version": "retrieval_round.v1",
        "round": round_index,
        "query_index": query_index,
        "query": query,
        "source": source,
        "status": status,
        "returned": returned,
    }
    if returned_source:
        row["returned_source"] = returned_source
    if reason:
        row["reason"] = reason
    if error:
        row["error"] = error
    return row


def _research_document_cap(source_plan: SourcePlan, default: int) -> int:
    value = source_plan.budget.get("max_documents")
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return max(1, default)


def _candidate_collection_cap(max_documents: int) -> int:
    """Return a bounded raw-candidate cap before final retrieval selection."""

    if max_documents <= 1:
        return 1
    return min(max_documents * 2, max_documents + 10)


def _planned_query_attempts(source_plan: SourcePlan) -> list[str]:
    queries = [query.strip() for query in source_plan.queries if query.strip()]
    return queries or [primary_query(source_plan)]


def _query_specs_by_query(query_plan: QueryPlan) -> dict[str, dict[str, object]]:
    specs: dict[str, dict[str, object]] = {}
    for spec in query_plan.query_specs:
        if not isinstance(spec, dict):
            continue
        query = str(spec.get("query") or "").strip()
        if query:
            specs[query] = spec
    return specs


def _attach_query_trace(row: dict[str, object], query_spec: dict[str, object]) -> None:
    """Add compact query-intent metadata to a retrieval attempt row."""
    if not query_spec:
        return
    facet = str(query_spec.get("facet") or "").strip()
    if facet:
        row["facet"] = facet
    title_keywords = query_spec.get("title_keywords")
    if isinstance(title_keywords, list) and title_keywords:
        row["title_keywords"] = [str(item) for item in title_keywords[:5]]
    abstract_keywords = query_spec.get("abstract_keywords")
    if isinstance(abstract_keywords, list) and abstract_keywords:
        row["abstract_keywords"] = [str(item) for item in abstract_keywords[:8]]
    rationale = str(query_spec.get("rationale") or "").strip()
    if rationale:
        row["query_rationale"] = rationale[:240]


def _try_openalex(
    ctx: Context,
    query: str,
    max_papers: int,
    attempts: list[dict[str, object]],
    *,
    cache_enabled: bool,
) -> tuple[list[Paper], str] | None:
    """Try OpenAlex live search and, if allowed, its cache."""
    try:
        ctx.emit("stage_message", f"Searching OpenAlex for up to {max_papers} paper(s).")
        response = OpenAlexConnector(OpenAlexSearchClient()).search(
            SearchQuery(query=query, max_results=max_papers)
        )
        papers = response.papers
        if papers:
            put_cache(query, "openalex", max_papers, [paper.to_row() for paper in papers])
            attempts.append({"source": "openalex", "status": "ok", "returned": len(papers)})
            return papers, "openalex"
        attempts.append({"source": "openalex", "status": "empty", "returned": 0})
    except OpenAlexSearchError as exc:
        attempts.append({"source": "openalex", "status": "error", "error": str(exc)})
        ctx.emit("stage_message", f"OpenAlex search failed. {exc}")

    if ctx.config.get("strict_search") is True or not cache_enabled:
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
    *,
    cache_enabled: bool,
) -> tuple[list[Paper], str] | None:
    """Try arXiv live search and, if allowed, its cache."""
    try:
        ctx.emit("stage_message", f"Searching arXiv for up to {max_papers} paper(s).")
        response = ArxivConnector(ArxivSearchClient(page_size=max_papers)).search(
            SearchQuery(query=query, max_results=max_papers)
        )
        papers = response.papers
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

    if ctx.config.get("strict_search") is True or not cache_enabled:
        return None
    cached = _cached_papers(ctx, query, max_papers, source="arxiv")
    if cached is not None:
        attempts.append({"source": "arxiv_cache", "status": "cache", "returned": len(cached)})
        return cached, "arxiv_cache"
    return None


def _try_local_files(
    ctx: Context,
    source_plan: SourcePlan,
    attempts: list[dict[str, object]],
) -> tuple[list[Paper], str] | None:
    """Try user-provided local Markdown/text documents as metadata records."""
    if not source_plan.local_documents:
        attempts.append(
            {"source": "local_files", "status": "skipped", "reason": "no local_documents"}
        )
        return None
    query = primary_query(source_plan)
    ctx.emit(
        "stage_message",
        f"Searching {len(source_plan.local_documents)} local document(s) for research metadata.",
    )
    response = LocalFileConnector(source_plan.local_documents).search(
        SearchQuery(query=query, max_results=source_plan.max_results_per_query)
    )
    papers = response.papers
    attempts.append(
        {"source": "local_files", "status": "ok" if papers else "empty", "returned": len(papers)}
    )
    return (papers, "local_files") if papers else None


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


def _should_use_source_plan(ctx: Context, source_plan: SourcePlan) -> bool:
    """Return whether search should execute planned sources instead of fixture rows."""
    if ctx.config.get("use_arxiv") is True:
        return True
    return any(source == "local_files" for source in source_plan.sources)


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
    papers = load_search_paper_rows(ctx)
    client = _llm_client(ctx)
    _write_read_review(ctx, papers, client)
    reading_papers = _shortlisted_papers(ctx, papers)
    evidence = _stage_evidence(ctx, "read")
    evidence_snippets = format_evidence_snippets(evidence)
    if client is not None and reading_papers:
        try:
            notes = _read_paper_notes_with_llm(ctx, client, reading_papers, evidence_snippets)
            write_json(ctx.artifact_path("paper_notes.json"), notes)
            write_text(ctx.artifact_path("notes.md"), _notes_markdown(notes))
            if _debug_artifacts_enabled(ctx):
                _write_read_cards(ctx)
            return
        except LLMError as exc:
            ctx.emit("stage_message", f"LLM reading failed; using offline fallback. {exc}")
            pass

    notes = [
        {
            "paper_id": paper["id"],
            "title": paper.get("title", ""),
            "evidence_role": _shortlist_value(ctx, str(paper.get("id") or ""), "evidence_role") or "other",
            "one_sentence_summary": "Metadata-only fallback note; no deeper interpretation was generated.",
            "problem": "unknown from available metadata",
            "method": "unknown from available metadata",
            "datasets": [],
            "metrics": [],
            "key_claims": [],
            "limitations": ["Offline reading fallback only saw metadata and generated no LLM interpretation."],
            "relation_to_topic": "Kept for structured reading because it was retrieved within the search budget.",
            "synthesis_hint": _shortlist_value(ctx, str(paper.get("id") or ""), "synthesis_hint"),
            "possible_experiment_hooks": [],
            "open_questions": ["Use LLM reading or full-text parsing before making strong claims."],
            "confidence": "low",
        }
        for paper in reading_papers
    ]
    write_json(ctx.artifact_path("paper_notes.json"), notes)
    write_text(ctx.artifact_path("notes.md"), _notes_markdown(notes))
    if _debug_artifacts_enabled(ctx):
        _write_read_cards(ctx)


def _write_read_review(ctx: Context, papers: list[dict[str, Any]], client: LLMClient | None) -> None:
    """Write read-stage paper review, shortlist, and reading table artifacts."""
    llm_decisions: list[dict[str, Any]] | None = None
    if client is not None and papers and _read_screening_mode(ctx) != "deterministic":
        try:
            llm_decisions = _read_screening_with_llm(ctx, client, papers)
        except LLMError as exc:
            ctx.emit("stage_message", f"LLM read screening failed; using deterministic shortlist. {exc}")
    meta = write_read_review_artifacts(
        stage_dir=ctx.stage_dir(),
        papers=papers,
        retrieval_selection=_read_jsonl_artifact(ctx, SEARCH_RETRIEVAL_SELECTION),
        coverage_report=_safe_read_json_artifact(ctx, SEARCH_COVERAGE_JSON),
        llm_decisions=llm_decisions,
    )
    ctx.emit(
        "stage_message",
        "Built read-stage shortlist and reading table.",
        shortlist_count=meta.get("shortlist_count", 0),
    )


def _read_screening_mode(ctx: Context) -> str:
    value = str(ctx.config.get("read_screening") or ctx.config.get("research_read_screening") or "auto")
    return value if value in {"auto", "llm", "deterministic"} else "auto"


def _read_screening_with_llm(
    ctx: Context,
    client: LLMClient,
    papers: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Ask the LLM to coarse-screen, then rerank papers for structured reading."""
    problem = load_problem_markdown(ctx)
    research_plan_json = json.dumps(
        _safe_read_json_artifact(ctx, SEARCH_RESEARCH_PLAN),
        ensure_ascii=False,
        indent=2,
    )
    max_shortlist = _read_screening_max_shortlist(ctx, len(papers))
    coarse_decisions = _read_coarse_screening_with_llm(
        ctx,
        client,
        papers,
        problem_markdown=problem,
        research_plan_json=research_plan_json,
    )
    if not coarse_decisions:
        return []

    known_ids = {str(paper.get("id") or "") for paper in papers}
    valid_coarse = [row for row in coarse_decisions if str(row.get("paper_id") or "") in known_ids]
    kept_ids = {
        str(row.get("paper_id") or "")
        for row in valid_coarse
        if _decision_value(row.get("decision")) == "keep"
    }
    if not kept_ids:
        return valid_coarse

    paper_by_id = {str(paper.get("id") or ""): paper for paper in papers}
    kept_papers = [paper_by_id[paper_id] for paper_id in kept_ids if paper_id in paper_by_id]
    rerank_input = _rerank_input_papers(kept_papers, valid_coarse, max_shortlist=max_shortlist)
    reranked = _read_rerank_with_llm(
        ctx,
        client,
        rerank_input,
        coarse_decisions=valid_coarse,
        problem_markdown=problem,
        research_plan_json=research_plan_json,
        max_shortlist=max_shortlist,
    )
    if not reranked:
        return _coarse_decisions_with_priorities(valid_coarse, max_shortlist=max_shortlist)
    return _merge_read_screening_decisions(
        coarse_decisions=valid_coarse,
        rerank_decisions=reranked,
        reranked_ids={str(paper.get("id") or "") for paper in rerank_input},
        max_shortlist=max_shortlist,
    )


def _read_coarse_screening_with_llm(
    ctx: Context,
    client: LLMClient,
    papers: list[dict[str, Any]],
    *,
    problem_markdown: str,
    research_plan_json: str,
) -> list[dict[str, Any]]:
    """Run abstract-level LLM screening in small concurrent batches."""
    batches = list(_batched(papers, _read_screening_batch_size(ctx)))
    if not batches:
        return []
    workers = min(_read_screening_workers(ctx), len(batches))
    ctx.emit(
        "stage_message",
        f"Calling LLM for read-stage coarse screening ({len(batches)} batch(es), {workers} worker(s)).",
    )
    requests = [
        LLMRequest(
            READ_SYSTEM,
            read_coarse_screening_user_prompt(
                topic=ctx.topic,
                problem_markdown=problem_markdown,
                papers_json=json.dumps(
                    [_paper_screening_record(paper, index) for index, paper in enumerate(batch, start=1)],
                    ensure_ascii=False,
                    indent=2,
                ),
                research_plan_json=research_plan_json,
            ),
            label=f"read-coarse-{batch_index:03d}",
        )
        for batch_index, batch in enumerate(batches, start=1)
    ]
    responses = client.ask_json_many(requests, max_workers=workers)
    decisions: list[dict[str, Any]] = []
    for response in responses:
        raw = response.get("decisions")
        if not isinstance(raw, list):
            continue
        for row in raw:
            if isinstance(row, dict):
                decisions.append(_normalize_coarse_decision(row))
    return decisions


def _read_rerank_with_llm(
    ctx: Context,
    client: LLMClient,
    papers: list[dict[str, Any]],
    *,
    coarse_decisions: list[dict[str, Any]],
    problem_markdown: str,
    research_plan_json: str,
    max_shortlist: int,
) -> list[dict[str, Any]]:
    """Run the focused reranking pass over coarsely kept papers."""
    if not papers:
        return []
    ctx.emit(
        "stage_message",
        f"Calling LLM for read-stage reranking ({len(papers)} candidate paper(s)).",
    )
    response = client.ask_json(
        READ_SYSTEM,
        read_rerank_user_prompt(
            topic=ctx.topic,
            problem_markdown=problem_markdown,
            papers_json=json.dumps(
                [_paper_screening_record(paper, index) for index, paper in enumerate(papers, start=1)],
                ensure_ascii=False,
                indent=2,
            ),
            research_plan_json=research_plan_json,
            coarse_decisions_json=json.dumps(coarse_decisions, ensure_ascii=False, indent=2),
            max_shortlist=max_shortlist,
        ),
        label="read-rerank",
    )
    raw = response.get("ranked_papers")
    if not isinstance(raw, list):
        raw = response.get("decisions")
    if not isinstance(raw, list):
        return []
    known_ids = {str(paper.get("id") or "") for paper in papers}
    decisions: list[dict[str, Any]] = []
    for row in raw:
        if not isinstance(row, dict):
            continue
        paper_id = str(row.get("paper_id") or "").strip()
        if paper_id not in known_ids:
            continue
        decisions.append(_normalize_rerank_decision(row))
    return decisions


def _read_screening_batch_size(ctx: Context) -> int:
    return _bounded_config_int(
        ctx,
        ("read_screening_batch_size", "research_read_batch_size"),
        default=4,
        minimum=1,
        maximum=8,
    )


def _read_screening_workers(ctx: Context) -> int:
    return _bounded_config_int(
        ctx,
        ("read_screening_workers", "research_read_workers"),
        default=min(3, _llm_max_workers(ctx)),
        minimum=1,
        maximum=max(1, _llm_max_workers(ctx)),
    )


def _read_screening_max_shortlist(ctx: Context, paper_count: int) -> int:
    default = paper_count if paper_count <= 24 else 24
    return _bounded_config_int(
        ctx,
        ("read_screening_max_shortlist", "research_read_max_shortlist"),
        default=default,
        minimum=1,
        maximum=max(1, paper_count),
    )


def _bounded_config_int(
    ctx: Context,
    keys: tuple[str, ...],
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    for key in keys:
        value = ctx.config.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            return max(minimum, min(maximum, value))
    return max(minimum, min(maximum, default))


def _batched(items: list[dict[str, Any]], batch_size: int) -> list[list[dict[str, Any]]]:
    return [items[index : index + batch_size] for index in range(0, len(items), batch_size)]


def _paper_screening_record(paper: dict[str, Any], index: int) -> dict[str, Any]:
    """Return compact paper metadata suitable for read-stage screening prompts."""
    return {
        "paper_id": str(paper.get("id") or _paper_id(paper, index)),
        "title": _truncate_text(str(paper.get("title") or ""), 240),
        "abstract": _truncate_text(str(paper.get("abstract") or ""), 1400),
        "source": str(paper.get("source") or ""),
        "published": paper.get("published"),
        "authors": _string_items(paper.get("authors"), limit=5),
        "categories": _string_items(paper.get("categories"), limit=6),
        "url": paper.get("url"),
        "doi": paper.get("doi"),
    }


def _truncate_text(text: str, max_chars: int) -> str:
    stripped = " ".join(text.split())
    if len(stripped) <= max_chars:
        return stripped
    return stripped[: max_chars - 3].rstrip() + "..."


def _normalize_coarse_decision(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "paper_id": str(row.get("paper_id") or "").strip(),
        "decision": _decision_value(row.get("decision")),
        "coarse_relevance_score": _optional_int(row.get("coarse_relevance_score")),
        "reason": str(row.get("reason") or "").strip(),
        "likely_facet": str(row.get("likely_facet") or row.get("facet") or "").strip(),
        "confidence": str(row.get("confidence") or "").strip(),
    }


def _normalize_rerank_decision(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "paper_id": str(row.get("paper_id") or "").strip(),
        "decision": _decision_value(row.get("decision")),
        "reading_priority": _optional_int(row.get("reading_priority")),
        "relevance_score": _optional_int(row.get("relevance_score")),
        "quality_score": _optional_int(row.get("quality_score")),
        "evidence_role": str(row.get("evidence_role") or "").strip(),
        "reason": str(row.get("reason") or "").strip(),
        "synthesis_hint": str(row.get("synthesis_hint") or "").strip(),
        "confidence": str(row.get("confidence") or "").strip(),
    }


def _decision_value(value: object) -> str:
    text = str(value or "keep").strip().lower()
    return text if text in {"keep", "drop"} else "keep"


def _optional_int(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _rerank_input_papers(
    papers: list[dict[str, Any]],
    coarse_decisions: list[dict[str, Any]],
    *,
    max_shortlist: int,
) -> list[dict[str, Any]]:
    score_by_id = {
        str(row.get("paper_id") or ""): int(row.get("coarse_relevance_score") or 0)
        for row in coarse_decisions
    }
    limit = max(max_shortlist, min(max_shortlist * 2, 48))
    return sorted(
        papers,
        key=lambda paper: (-score_by_id.get(str(paper.get("id") or ""), 0), str(paper.get("id") or "")),
    )[:limit]


def _coarse_decisions_with_priorities(
    coarse_decisions: list[dict[str, Any]],
    *,
    max_shortlist: int,
) -> list[dict[str, Any]]:
    kept = [
        row
        for row in coarse_decisions
        if _decision_value(row.get("decision")) == "keep"
    ]
    kept.sort(
        key=lambda row: (
            -int(row.get("coarse_relevance_score") or 0),
            str(row.get("paper_id") or ""),
        )
    )
    keep_ids = {str(row.get("paper_id") or "") for row in kept[:max_shortlist]}
    output: list[dict[str, Any]] = []
    priority = 1
    for row in coarse_decisions:
        paper_id = str(row.get("paper_id") or "")
        item = dict(row)
        if paper_id in keep_ids:
            item["decision"] = "keep"
            item["reading_priority"] = priority
            item["relevance_score"] = item.get("coarse_relevance_score")
            item.setdefault("quality_score", None)
            priority += 1
        elif _decision_value(row.get("decision")) == "keep":
            item["decision"] = "drop"
            item["reason"] = str(item.get("reason") or "") + " Outside read-stage shortlist budget."
        output.append(item)
    return output


def _merge_read_screening_decisions(
    *,
    coarse_decisions: list[dict[str, Any]],
    rerank_decisions: list[dict[str, Any]],
    reranked_ids: set[str],
    max_shortlist: int,
) -> list[dict[str, Any]]:
    coarse_by_id = {str(row.get("paper_id") or ""): row for row in coarse_decisions}
    rerank_by_id = {str(row.get("paper_id") or ""): row for row in rerank_decisions}
    output: list[dict[str, Any]] = []
    kept_priorities = 0
    for paper_id, coarse in coarse_by_id.items():
        row = dict(coarse)
        rerank = rerank_by_id.get(paper_id)
        if rerank is not None:
            row.update({key: value for key, value in rerank.items() if value not in (None, "")})
            if _decision_value(row.get("decision")) == "keep":
                kept_priorities += 1
                if _optional_int(row.get("reading_priority")) is None:
                    row["reading_priority"] = kept_priorities
        elif _decision_value(row.get("decision")) == "keep" and paper_id not in reranked_ids:
            row["decision"] = "drop"
            row["reason"] = str(row.get("reason") or "") + " Outside read-stage rerank budget."
        output.append(row)

    kept_rows = [
        row for row in output if _decision_value(row.get("decision")) == "keep"
    ]
    kept_rows.sort(
        key=lambda row: (
            int(row.get("reading_priority") or 9999),
            -int(row.get("relevance_score") or row.get("coarse_relevance_score") or 0),
            str(row.get("paper_id") or ""),
        )
    )
    allowed_keep_ids = {str(row.get("paper_id") or "") for row in kept_rows[:max_shortlist]}
    for row in output:
        paper_id = str(row.get("paper_id") or "")
        if _decision_value(row.get("decision")) == "keep" and paper_id not in allowed_keep_ids:
            row["decision"] = "drop"
            row["reason"] = str(row.get("reason") or "") + " Outside read-stage shortlist budget."
    return output


def _shortlisted_papers(ctx: Context, papers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return papers selected by read-stage shortlist, preserving shortlist order."""
    if not ctx.artifact_path(READ_SHORTLIST).exists():
        return papers
    shortlist = _read_jsonl_artifact(ctx, READ_SHORTLIST)
    paper_by_id = {str(paper.get("id") or ""): paper for paper in papers}
    selected: list[dict[str, Any]] = []
    for row in shortlist:
        paper = paper_by_id.get(str(row.get("paper_id") or ""))
        if paper is not None:
            selected.append(paper)
    return selected


def _shortlisted_document_ids(ctx: Context) -> set[str]:
    return {str(row.get("paper_id") or "") for row in _read_jsonl_artifact(ctx, READ_SHORTLIST) if row.get("paper_id")}


def _shortlist_value(ctx: Context, paper_id: str, key: str) -> str:
    for row in _read_jsonl_artifact(ctx, READ_SHORTLIST):
        if str(row.get("paper_id") or "") == paper_id:
            return str(row.get(key) or "")
    return ""


def _debug_artifacts_enabled(ctx: Context) -> bool:
    value = ctx.config.get("debug_artifacts", False)
    return value is True or str(value).strip().lower() in {"1", "true", "yes", "on"}


def _downstream_source_plan(ctx: Context) -> dict[str, Any]:
    """Return source-plan metadata for downstream stages after compaction.

    Verbose runs keep ``planning/research_plan.json``. Compact runs remove that
    debug artifact, so search also stores a compact source-plan copy in
    ``search_meta.json``. Older compact runs may lack that copy; for those we
    reconstruct the minimum reliable fields from retained search manifests.
    """

    research_plan = _safe_read_json_artifact(ctx, SEARCH_RESEARCH_PLAN)
    if isinstance(research_plan, dict):
        source_plan = research_plan.get("source_plan")
        if _usable_source_plan(source_plan):
            return dict(source_plan)

    search_meta = _safe_read_json_artifact(ctx, SEARCH_META)
    source_plan = search_meta.get("source_plan") if isinstance(search_meta, dict) else None
    if _usable_source_plan(source_plan):
        return dict(source_plan)

    return _source_plan_from_search_manifests(ctx, search_meta if isinstance(search_meta, dict) else {})


def _usable_source_plan(value: object) -> bool:
    return isinstance(value, dict) and bool(value.get("queries") or value.get("sources"))


def _source_plan_from_search_manifests(ctx: Context, search_meta: dict[str, Any]) -> dict[str, Any]:
    cache_manifest = _safe_read_json_artifact(ctx, SEARCH_CACHE_MANIFEST)
    fulltext_manifest = _safe_read_json_artifact(ctx, SEARCH_FULLTEXT_MANIFEST)
    fulltext_budget = fulltext_manifest.get("budget") if isinstance(fulltext_manifest, dict) else {}
    budget = dict(fulltext_budget) if isinstance(fulltext_budget, dict) else {}
    return {
        "schema_version": "source_plan.reconstructed.v1",
        "queries": _string_sequence(search_meta.get("queries")) or _string_sequence([search_meta.get("query")])
        or [ctx.topic],
        "sources": _string_sequence(search_meta.get("sources")) or _string_sequence(search_meta.get("sources_used")),
        "mode": str(ctx.config.get("research_mode") or "standard"),
        "require_fulltext": bool(cache_manifest.get("require_fulltext") or fulltext_manifest.get("enabled")),
        "allow_pdf_download": bool(cache_manifest.get("allow_pdf_download") or fulltext_manifest.get("allow_pdf_download")),
        "index_backend": str(search_meta.get("index_backend") or "keyword"),
        "budget": budget,
    }


def _string_sequence(value: object) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _write_read_cards(ctx: Context) -> None:
    """Write semantic reading cards from retrieved documents and chunks."""
    documents = _read_jsonl_artifact(ctx, SEARCH_DOCUMENTS)
    chunks = _read_jsonl_artifact(ctx, SEARCH_CHUNKS)
    if not documents:
        return
    if ctx.artifact_path(READ_SHORTLIST).exists():
        shortlisted_ids = _shortlisted_document_ids(ctx)
        documents = [
            row
            for row in documents
            if str(row.get("document_id") or row.get("source_id") or "") in shortlisted_ids
        ]
        chunks = [row for row in chunks if str(row.get("document_id") or "") in shortlisted_ids]
    meta = write_read_card_artifacts(
        stage_dir=ctx.stage_dir(),
        documents=documents,
        chunks=chunks,
    )
    ctx.emit(
        "stage_message",
        "Built reading cards from retrieved documents.",
        paper_card_count=meta.get("paper_card_count", 0),
        claim_card_count=meta.get("claim_card_count", 0),
    )


def execute_synthesize(ctx: Context) -> None:
    notes = load_notes_markdown(ctx)
    paper_notes = json.dumps(load_paper_notes_json(ctx), indent=2, ensure_ascii=False)
    _write_synthesis_evidence(ctx)
    evidence = _stage_evidence(ctx, "synthesize")
    evidence_snippets = format_evidence_snippets(evidence)
    client = _llm_client(ctx)
    if client is not None:
        try:
            ctx.emit("stage_message", "Calling LLM for synthesis.")
            response = client.ask_json(
                SYNTHESIZE_SYSTEM,
                synthesize_user_prompt(
                    notes,
                    paper_notes,
                    evidence_snippets=evidence_snippets,
                    structured_context_json=_synthesis_structured_context(ctx),
                ),
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


def _synthesis_structured_context(ctx: Context) -> str:
    """Return compact JSON context for LLM synthesis."""
    context = {
        "schema_version": "synthesis_prompt_context.v1",
        "reading": {
            "shortlist": _read_jsonl_artifact(ctx, READ_SHORTLIST)[:12],
            "screening_decisions": _read_jsonl_artifact(ctx, READ_SCREENING_DECISIONS)[:24],
            "paper_briefs": load_paper_notes_json(ctx)[:12],
        },
        "retrieval_coverage": _safe_read_json_artifact(ctx, SEARCH_COVERAGE_JSON),
        "synthesis_brief": _safe_read_json_artifact(ctx, SYNTHESIS_BRIEF_JSON),
    }
    if _debug_artifacts_enabled(ctx):
        context["debug_cards"] = {
            "paper_cards": _read_jsonl_artifact(ctx, READ_PAPER_CARDS)[:12],
            "claim_cards": _read_jsonl_artifact(ctx, READ_CLAIM_CARDS)[:24],
            "method_cards": _read_jsonl_artifact(ctx, READ_METHOD_CARDS)[:12],
            "dataset_cards": _read_jsonl_artifact(ctx, READ_DATASET_CARDS)[:12],
            "code_links": _read_jsonl_artifact(ctx, READ_CODE_LINKS)[:12],
        }
        context["debug_synthesis_evidence_pack"] = _safe_read_json_artifact(ctx, SYNTHESIS_EVIDENCE_PACK_JSON)
    return json.dumps(context, ensure_ascii=False, indent=2)


def _write_synthesis_evidence(ctx: Context) -> None:
    """Write synthesis-owned compact brief and optional debug evidence tables."""
    documents = _read_jsonl_artifact(ctx, SEARCH_DOCUMENTS)
    chunks = _read_jsonl_artifact(ctx, SEARCH_CHUNKS)
    if not documents:
        return
    source_plan = _downstream_source_plan(ctx)
    paper_notes = load_paper_notes_json(ctx)
    meta = write_synthesis_brief_artifact(
        stage_dir=ctx.stage_dir(),
        topic=ctx.topic,
        source_plan=source_plan,
        papers=load_search_paper_rows(ctx),
        paper_notes=paper_notes,
        coverage_report=_safe_read_json_artifact(ctx, SEARCH_COVERAGE_JSON),
        index_meta=_safe_read_json_artifact(ctx, SEARCH_INDEX_META),
        fulltext_manifest=_safe_read_json_artifact(ctx, SEARCH_FULLTEXT_MANIFEST),
        fulltext_extraction=_safe_read_json_artifact(ctx, SEARCH_FULLTEXT_EXTRACTION),
    )
    ctx.emit(
        "stage_message",
        "Built compact synthesis brief from paper notes.",
        idea_candidate_count=meta.get("idea_candidate_count", 0),
    )
    if not _debug_artifacts_enabled(ctx):
        return
    paper_cards = _read_jsonl_artifact(ctx, READ_PAPER_CARDS)
    claim_cards = _read_jsonl_artifact(ctx, READ_CLAIM_CARDS)
    debug_meta = write_synthesis_evidence_artifacts(
        stage_dir=ctx.stage_dir(),
        topic=ctx.topic,
        source_plan=source_plan,
        papers=load_search_paper_rows(ctx),
        documents=documents,
        sections=_read_jsonl_artifact(ctx, SEARCH_SECTIONS),
        chunks=chunks,
        index_meta=_safe_read_json_artifact(ctx, SEARCH_INDEX_META),
        paper_cards=paper_cards,
        claim_cards=claim_cards,
        method_cards=_read_jsonl_artifact(ctx, READ_METHOD_CARDS),
        dataset_cards=_read_jsonl_artifact(ctx, READ_DATASET_CARDS),
        code_links=_read_jsonl_artifact(ctx, READ_CODE_LINKS),
        coverage_report=_safe_read_json_artifact(ctx, SEARCH_COVERAGE_JSON),
        fulltext_manifest=_safe_read_json_artifact(ctx, SEARCH_FULLTEXT_MANIFEST),
        fulltext_extraction=_safe_read_json_artifact(ctx, SEARCH_FULLTEXT_EXTRACTION),
    )
    ctx.emit(
        "stage_message",
        "Built debug synthesis evidence pack and bounded idea candidates.",
        idea_candidate_count=debug_meta.get("idea_candidate_count", 0),
    )


def execute_design(ctx: Context) -> None:
    hypothesis = load_hypothesis_markdown(ctx)
    _write_design_handoff(ctx)
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


def _write_design_handoff(ctx: Context) -> None:
    """Write design-owned experiment contract and optional tool handoff artifacts."""
    evidence_pack = _safe_read_json_artifact(ctx, SYNTHESIS_EVIDENCE_PACK_JSON)
    synthesis_brief = _safe_read_json_artifact(ctx, SYNTHESIS_BRIEF_JSON)
    if not evidence_pack and synthesis_brief:
        evidence_pack = _evidence_pack_from_synthesis_brief(synthesis_brief)
    if not evidence_pack:
        return
    source_plan = _downstream_source_plan(ctx)
    budget = source_plan.get("budget") if isinstance(source_plan, dict) else {}
    compact_artifacts = ctx.config.get("debug_artifacts") is not True
    if isinstance(budget, dict) and "compact_artifacts" in budget:
        compact_artifacts = bool(budget.get("compact_artifacts"))
    meta = write_design_handoff_artifacts(
        stage_dir=ctx.stage_dir(),
        evidence_pack=evidence_pack,
        idea_candidates=_idea_candidates_for_design(ctx, synthesis_brief),
        novelty_checks=_novelty_checks_for_design(ctx, synthesis_brief),
        compact_artifacts=compact_artifacts,
    )
    ctx.emit(
        "stage_message",
        "Built design experiment contract from synthesized evidence.",
        experiment_contract=meta.get("experiment_contract", ""),
    )


def _evidence_pack_from_synthesis_brief(brief: dict[str, Any]) -> dict[str, Any]:
    """Return an evidence-pack-like view for design from the compact brief."""
    return {
        "schema_version": "synthesis_brief_handoff.v1",
        "topic": brief.get("topic"),
        "source_plan": brief.get("source_plan", {}),
        "counts": brief.get("counts", {}),
        "coverage": brief.get("coverage", {}),
        "provenance": brief.get("provenance", {}),
        "papers": [
            {
                "id": row.get("paper_id"),
                "title": row.get("title"),
                "source": row.get("source"),
            }
            for row in _list_value(brief.get("paper_briefs"))
            if isinstance(row, dict)
        ],
        "paper_cards": [],
        "claim_cards": [],
        "method_cards": [],
        "dataset_cards": [],
        "code_links": [],
        "limitations": _list_value(brief.get("limitations")),
    }


def _idea_candidates_for_design(ctx: Context, synthesis_brief: dict[str, Any]) -> list[dict[str, Any]]:
    if synthesis_brief:
        ideas = _list_value(synthesis_brief.get("idea_candidates"))
        if ideas:
            return [row for row in ideas if isinstance(row, dict)]
    return _read_jsonl_artifact(ctx, SYNTHESIS_IDEA_CANDIDATES)


def _novelty_checks_for_design(ctx: Context, synthesis_brief: dict[str, Any]) -> list[dict[str, Any]]:
    if synthesis_brief:
        checks = _list_value(synthesis_brief.get("novelty_checks"))
        if checks:
            return [row for row in checks if isinstance(row, dict)]
    return _read_jsonl_artifact(ctx, SYNTHESIS_NOVELTY_CHECKS)


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
    plan = load_experiment_plan(ctx)
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
    experiment_path = Path(load_experiment_script_path(ctx))
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


def _safe_read_artifact(ctx: Context, filename: str) -> str:
    """Read an artifact when present, otherwise return an empty string."""
    return safe_read_artifact(ctx, filename)


def _safe_read_json_artifact(ctx: Context, filename: str) -> dict[str, Any]:
    """Read a JSON artifact as a dictionary when present."""
    return safe_read_json_artifact(ctx, filename)


def _read_jsonl_artifact(ctx: Context, filename: str) -> list[dict[str, Any]]:
    """Read a JSONL artifact when present, otherwise return an empty list."""
    path = ctx.find_artifact(filename)
    if path is None or not path.exists():
        return []
    try:
        return read_jsonl(path)
    except (OSError, json.JSONDecodeError):
        return []


def _list_value(value: object) -> list[Any]:
    return value if isinstance(value, list) else []


def _compact_field(value: object, *, default: str, limit: int = 220) -> str:
    text = str(value or "").strip()
    if not text or text.lower() == "unknown":
        return default
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3].rsplit(" ", 1)[0].strip() + "..."


def _string_items(value: object, *, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    rows: list[str] = []
    for item in value:
        text = str(item).strip()
        if text:
            rows.append(text)
        if len(rows) >= limit:
            break
    return rows


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
) -> list[dict[str, Any]]:
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
) -> dict[str, Any]:
    """Merge model output with source metadata into a stable note schema."""
    limitation_text = _text_field(response, "limitation")
    limitations = _string_list_field(response, "limitations")
    if limitation_text and limitation_text not in limitations:
        limitations.append(limitation_text)
    return {
        "paper_id": _text_field(response, "paper_id") or _paper_id(paper, index),
        "title": _text_field(response, "title") or str(paper.get("title", "")),
        "evidence_role": _text_field(response, "evidence_role") or "other",
        "one_sentence_summary": _text_field(response, "one_sentence_summary") or _text_field(response, "summary"),
        "problem": _text_field(response, "problem") or "Not specified.",
        "method": _text_field(response, "method") or "Not specified.",
        "datasets": _string_list_field(response, "datasets"),
        "metrics": _string_list_field(response, "metrics"),
        "key_claims": _string_list_field(response, "key_claims") or _string_list_field(response, "main_claims"),
        "limitations": limitations or ["Not specified."],
        "relation_to_topic": _text_field(response, "relation_to_topic")
        or _text_field(response, "relevance")
        or "Not specified.",
        "synthesis_hint": _text_field(response, "synthesis_hint"),
        "possible_experiment_hooks": _string_list_field(response, "possible_experiment_hooks"),
        "open_questions": _string_list_field(response, "open_questions"),
        "evidence_refs": _string_list_field(response, "evidence_refs"),
        "confidence": _text_field(response, "confidence") or "unknown",
        "limitation": limitation_text or (limitations[0] if limitations else "Not specified."),
        "relevance": _text_field(response, "relevance") or _text_field(response, "relation_to_topic") or "Not specified.",
    }


def _notes_markdown(notes: list[dict[str, Any]]) -> str:
    """Render structured paper notes as inspectable Markdown."""
    lines = ["# Literature Notes", ""]
    for note in notes:
        lines.append(f"## {note['paper_id']}")
        if note.get("title"):
            lines.append(f"Title: {note['title']}")
        if note.get("evidence_role"):
            lines.append(f"Role: {note['evidence_role']}")
        lines.extend(
            [
                f"- Summary: {note.get('one_sentence_summary') or 'Not specified.'}",
                f"- Problem: {note['problem']}",
                f"- Method: {note['method']}",
                f"- Datasets: {_join_inline(note.get('datasets'))}",
                f"- Metrics: {_join_inline(note.get('metrics'))}",
                f"- Key claims: {_join_inline(note.get('key_claims'))}",
                f"- Limitations: {_join_inline(note.get('limitations'))}",
                f"- Relation to topic: {note.get('relation_to_topic') or note.get('relevance') or 'Not specified.'}",
                f"- Synthesis hint: {note.get('synthesis_hint') or 'Not specified.'}",
                f"- Experiment hooks: {_join_inline(note.get('possible_experiment_hooks'))}",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def _string_list_field(row: dict[str, Any], key: str) -> list[str]:
    value = row.get(key)
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _join_inline(value: object) -> str:
    rows = value if isinstance(value, list) else []
    return ", ".join(str(item) for item in rows if str(item).strip()) or "Not specified."


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
