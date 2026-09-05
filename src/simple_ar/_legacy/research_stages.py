from __future__ import annotations

import json
from typing import Any
from simple_ar.core.artifacts import (
    write_json,
    write_jsonl,
    write_text,
)
from simple_ar.literature.arxiv_client import (
    ArxivRateLimitError,
    ArxivSearchClient,
    LiteratureSearchError,
)
from simple_ar.literature.cache import (
    get_cached,
    put_cache,
)
from simple_ar.literature.models import Paper
from simple_ar.literature.openalex_client import (
    OpenAlexSearchClient,
    OpenAlexSearchError,
)
from simple_ar.literature.semantic_scholar_client import (
    SemanticScholarSearchClient,
    SemanticScholarSearchError,
)
from simple_ar.integrations.llm import (
    LLMClient,
    LLMError,
)
from simple_ar.core.pipeline import Context
from simple_ar.research.connectors import (
    ArxivConnector,
    OpenAlexConnector,
    SemanticScholarConnector,
)
from simple_ar.research.contracts import (
    QueryPlan,
    ResearchQuestion,
    SourcePlan,
)
from simple_ar.research.evidence.coverage import (
    build_coverage_report,
    coverage_report_markdown,
)
from simple_ar.research.outputs.artifacts import (
    READ_CLAIM_CARDS,
    READ_CODE_LINKS,
    READ_DATASET_CARDS,
    READ_METHOD_CARDS,
    READ_PAPER_CARDS,
    READ_SCREENING_DECISIONS,
    READ_SHORTLIST,
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
    SYNTHESIS_BRIEF_JSON,
    build_research_plan_artifact,
    write_read_card_artifacts_from_result,
    write_read_review_artifacts,
    write_search_document_artifacts,
    write_synthesis_brief_artifact,
    write_synthesis_evidence_artifacts,
)
from simple_ar.research.prompts import (
    SYNTHESIZE_SYSTEM,
    synthesize_user_prompt,
)
from simple_ar.research.planning.capability import (
    ResearchPlanRequest,
    build_research_plan,
    build_requested_research_plan,
    build_research_scope,
)
from simple_ar.research.evidence.retrieval import (
    RetrievalCandidate,
    select_retrieval_candidates,
)
from simple_ar.research.service import (
    load_search_document_bundle,
    load_notes_markdown,
    load_paper_notes_json,
    load_problem_markdown,
    load_search_paper_rows,
)
from simple_ar.research.sources.base import (
    SearchQuery,
    build_source_plan,
    primary_query,
)
from simple_ar.research.evidence.reader import ReadRequest, read_documents
from simple_ar.research.evidence.screening import (
    read_paper_notes_with_llm,
    render_paper_notes_markdown,
    screen_papers_with_llm,
)
from simple_ar.research.sources.registry import (
    SearchProviderRegistry,
    default_search_provider_registry,
)
from simple_ar.retrieval.evidence import format_evidence_snippets
from simple_ar.core.runtime import (
    ensure_heading as _ensure_heading,
    handle_llm_failure as _handle_llm_failure,
    llm_client as _llm_client,
    read_jsonl_artifact as _read_jsonl_artifact,
    safe_read_json_artifact as _safe_read_json_artifact,
    text_field as _text_field,
)
from simple_ar.pipeline_stages.common import (
    _downstream_source_plan,
    _stage_evidence,
)

def execute_plan(ctx: Context) -> None:
    client = _llm_client(ctx)
    if client is not None:
        try:
            ctx.emit("stage_message", "Calling LLM for research planning.")
            goal, problem = build_research_scope(ctx.topic, llm_client=client)
            write_text(ctx.artifact_path("goal.md"), goal)
            write_text(ctx.artifact_path("problem.md"), problem)
            return
        except LLMError as exc:
            _handle_llm_failure(ctx, "LLM planning failed", exc)

        _handle_llm_failure(
            ctx,
            "LLM planning returned no usable goal/problem",
            LLMError("response did not contain both goal_markdown and problem_markdown"),
        )

    goal, problem = build_research_scope(ctx.topic)
    write_text(ctx.artifact_path("goal.md"), goal)
    write_text(ctx.artifact_path("problem.md"), problem)

def execute_search(
    ctx: Context,
    *,
    provider_registry: SearchProviderRegistry | None = None,
) -> None:
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
        registry = provider_registry or _default_search_provider_registry(source_plan, max_papers)
        papers, meta_update = _live_literature_search(
            ctx,
            source_plan,
            problem,
            query_plan,
            research_questions,
            provider_registry=registry,
        )
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
    """Use the canonical planning capability for the legacy search projection."""
    planner_mode = _research_planner_mode(ctx.config.get("research_planner"))
    client = _llm_client(ctx)
    use_llm = planner_mode != "deterministic" and client is not None
    request = ResearchPlanRequest(
        topic=ctx.topic,
        problem_markdown=problem,
        config=ctx.config,
        default_query=query,
        default_max_results=_max_papers(ctx),
        use_llm=use_llm,
        llm_client=client,
    )
    try:
        result = build_requested_research_plan(request)
    except (LLMError, ValueError) as exc:
        _handle_llm_failure(ctx, "LLM research planning failed", exc)
        result = build_research_plan(
            ResearchPlanRequest(
                topic=ctx.topic,
                problem_markdown=problem,
                config=ctx.config,
                default_query=query,
                default_max_results=_max_papers(ctx),
            )
        )
    return list(result.questions), result.query_plan

def _research_planner_mode(value: object) -> str:
    text = str(value or "auto").strip().lower()
    return text if text in {"auto", "llm", "deterministic"} else "auto"

def _default_search_provider_registry(
    source_plan: SourcePlan,
    max_papers: int,
) -> SearchProviderRegistry:
    """Build the default registry while preserving legacy client injection.

    The client aliases remain in this module for compatibility with existing
    tests and integrations that replace them at runtime. The registry still
    owns connector construction; this helper only supplies those legacy
    factories to it.
    """
    return default_search_provider_registry(
        local_documents=source_plan.local_documents,
        arxiv_page_size=max_papers,
        connector_factories={
            "openalex": lambda: OpenAlexConnector(OpenAlexSearchClient()),
            "semantic_scholar": lambda: SemanticScholarConnector(SemanticScholarSearchClient()),
            "arxiv": lambda: ArxivConnector(ArxivSearchClient(page_size=max_papers)),
        },
    )

def _live_literature_search(
    ctx: Context,
    source_plan: SourcePlan,
    problem: str,
    query_plan: QueryPlan,
    research_questions: list[ResearchQuestion],
    *,
    provider_registry: SearchProviderRegistry | None = None,
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
    provider_registry = provider_registry or _default_search_provider_registry(source_plan, max_papers)
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
        provider_registry=provider_registry,
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
                provider_registry=provider_registry,
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
    provider_registry: SearchProviderRegistry,
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
                provider_registry=provider_registry,
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
    provider_registry: SearchProviderRegistry,
) -> tuple[list[Paper], dict[str, object]]:
    max_papers = source_plan.max_results_per_query
    if not provider_registry.has(source):
        return [], _retrieval_round_row(
            round_index=round_index,
            query_index=query_index,
            query=query,
            source=source,
            status="unsupported",
            returned=0,
            reason="provider not registered",
        )
    if source == "local_files":
        return _search_local_files_once(
            ctx,
            source_plan,
            query,
            query_index,
            round_index,
            provider_registry=provider_registry,
        )
    if source == "openalex":
        return _search_openalex_once(
            ctx,
            query,
            max_papers,
            query_index=query_index,
            round_index=round_index,
            cache_enabled=source_plan.cache_enabled,
            provider_registry=provider_registry,
        )
    if source == "semantic_scholar":
        return _search_semantic_scholar_once(
            ctx,
            query,
            max_papers,
            query_index=query_index,
            round_index=round_index,
            cache_enabled=source_plan.cache_enabled,
            provider_registry=provider_registry,
        )
    if source == "arxiv":
        return _search_arxiv_once(
            ctx,
            query,
            max_papers,
            query_index=query_index,
            round_index=round_index,
            cache_enabled=source_plan.cache_enabled,
            provider_registry=provider_registry,
        )
    return _search_registered_source_once(
        ctx,
        source_plan,
        source=source,
        query=query,
        query_index=query_index,
        round_index=round_index,
        provider_registry=provider_registry,
    )


def _search_registered_source_once(
    ctx: Context,
    source_plan: SourcePlan,
    *,
    source: str,
    query: str,
    query_index: int,
    round_index: int,
    provider_registry: SearchProviderRegistry,
) -> tuple[list[Paper], dict[str, object]]:
    """Run a connector registered outside the built-in provider set.

    Built-in sources keep their existing exception-specific cache behavior.
    Extensions have only the source-agnostic connector contract, so failures
    are converted into a normal retrieval trace at this boundary.
    """
    try:
        ctx.emit(
            "stage_message",
            f"Searching {source} for up to {source_plan.max_results_per_query} paper(s) with `{query}`.",
        )
        response = provider_registry.resolve(source).search(
            SearchQuery(query=query, max_results=source_plan.max_results_per_query)
        )
        papers = response.papers
        if papers and source_plan.cache_enabled:
            put_cache(
                query,
                source,
                source_plan.max_results_per_query,
                [paper.to_row() for paper in papers],
            )
        status = str(response.status or ("ok" if papers else "empty"))
        return papers, _retrieval_round_row(
            round_index=round_index,
            query_index=query_index,
            query=query,
            source=source,
            status=status,
            returned=len(papers),
        )
    except Exception as exc:
        ctx.emit("stage_message", f"{source} search failed. {exc}")
        return [], _retrieval_round_row(
            round_index=round_index,
            query_index=query_index,
            query=query,
            source=source,
            status="error",
            returned=0,
            error=str(exc),
        )

def _search_local_files_once(
    ctx: Context,
    source_plan: SourcePlan,
    query: str,
    query_index: int,
    round_index: int,
    *,
    provider_registry: SearchProviderRegistry,
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
    response = provider_registry.resolve("local_files").search(
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
    provider_registry: SearchProviderRegistry,
) -> tuple[list[Paper], dict[str, object]]:
    try:
        ctx.emit("stage_message", f"Searching OpenAlex for up to {max_papers} paper(s) with `{query}`.")
        response = provider_registry.resolve("openalex").search(
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
    provider_registry: SearchProviderRegistry,
) -> tuple[list[Paper], dict[str, object]]:
    try:
        ctx.emit("stage_message", f"Searching Semantic Scholar for up to {max_papers} paper(s) with `{query}`.")
        response = provider_registry.resolve("semantic_scholar").search(
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
    provider_registry: SearchProviderRegistry,
) -> tuple[list[Paper], dict[str, object]]:
    try:
        ctx.emit("stage_message", f"Searching arXiv for up to {max_papers} paper(s) with `{query}`.")
        response = provider_registry.resolve("arxiv").search(
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
    if any(source == "local_files" for source in source_plan.sources):
        return True
    configured_sources = ctx.config.get("research_sources")
    if isinstance(configured_sources, list):
        return any(str(source).strip() and str(source).strip() != "fixture" for source in configured_sources)
    return False

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
            notes = read_paper_notes_with_llm(
                client,
                papers=reading_papers,
                evidence_snippets=evidence_snippets,
                config=ctx.config,
                emit=lambda message: ctx.emit("stage_message", message),
            )
            write_json(ctx.artifact_path("paper_notes.json"), notes)
            write_text(ctx.artifact_path("notes.md"), render_paper_notes_markdown(notes))
            if _debug_artifacts_enabled(ctx):
                _write_read_cards(ctx)
            return
        except LLMError as exc:
            _handle_llm_failure(ctx, "LLM reading failed", exc)

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
    write_text(ctx.artifact_path("notes.md"), render_paper_notes_markdown(notes))
    if _debug_artifacts_enabled(ctx):
        _write_read_cards(ctx)

def _write_read_review(ctx: Context, papers: list[dict[str, Any]], client: LLMClient | None) -> None:
    """Write read-stage paper review, shortlist, and reading table artifacts."""
    llm_decisions: list[dict[str, Any]] | None = None
    if client is not None and papers and _read_screening_mode(ctx) != "deterministic":
        try:
            llm_decisions = screen_papers_with_llm(
                client,
                topic=ctx.topic,
                problem_markdown=load_problem_markdown(ctx),
                research_plan_json=json.dumps(
                    _safe_read_json_artifact(ctx, SEARCH_RESEARCH_PLAN),
                    ensure_ascii=False,
                    indent=2,
                ),
                papers=papers,
                config=ctx.config,
                emit=lambda message: ctx.emit("stage_message", message),
            )
        except LLMError as exc:
            _handle_llm_failure(ctx, "LLM read screening failed", exc)
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

def _write_read_cards(ctx: Context) -> None:
    """Write semantic reading cards from retrieved documents and chunks."""
    bundle = load_search_document_bundle(ctx)
    if not bundle.records:
        return
    paper_ids = None
    if ctx.artifact_path(READ_SHORTLIST).exists():
        paper_ids = tuple(_shortlisted_document_ids(ctx))
    result = read_documents(
        ReadRequest(bundle=bundle, paper_ids=paper_ids)
    )
    meta = write_read_card_artifacts_from_result(stage_dir=ctx.stage_dir(), result=result)
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
            _handle_llm_failure(ctx, "LLM synthesis failed", exc)

        _handle_llm_failure(
            ctx,
            "LLM synthesis returned incomplete output",
            LLMError("response did not contain both synthesis_markdown and hypothesis_markdown"),
        )

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

__all__ = [
    "execute_plan",
    "execute_search",
    "execute_read",
    "execute_synthesize",
]
