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
    LLMRequest,
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
    READ_SYSTEM,
    SYNTHESIZE_SYSTEM,
    paper_note_user_prompt,
    read_coarse_screening_user_prompt,
    read_rerank_user_prompt,
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
from simple_ar.research.sources.registry import (
    SearchProviderRegistry,
    default_search_provider_registry,
)
from simple_ar.retrieval.evidence import format_evidence_snippets
from simple_ar.pipeline_stages.common import (
    _downstream_source_plan,
    _ensure_heading,
    _handle_llm_failure,
    _llm_client,
    _read_jsonl_artifact,
    _safe_read_json_artifact,
    _stage_evidence,
    _string_items,
    _text_field,
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
            notes = _read_paper_notes_with_llm(ctx, client, reading_papers, evidence_snippets)
            write_json(ctx.artifact_path("paper_notes.json"), notes)
            write_text(ctx.artifact_path("notes.md"), _notes_markdown(notes))
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
    min_shortlist = _read_screening_min_shortlist(ctx, len(papers), max_shortlist)
    required_facets = _read_required_facets(ctx)
    coarse_decisions = _read_coarse_screening_with_llm(
        ctx,
        client,
        papers,
        problem_markdown=problem,
        research_plan_json=research_plan_json,
        min_shortlist=min_shortlist,
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
        min_shortlist=min_shortlist,
    )
    if not reranked:
        return _coarse_decisions_with_priorities(
            valid_coarse,
            max_shortlist=max_shortlist,
        )
    return _merge_read_screening_decisions(
        coarse_decisions=valid_coarse,
        rerank_decisions=reranked,
        reranked_ids={str(paper.get("id") or "") for paper in rerank_input},
        max_shortlist=max_shortlist,
        min_shortlist=min_shortlist,
        required_facets=required_facets,
        papers_by_id=paper_by_id,
    )

def _read_coarse_screening_with_llm(
    ctx: Context,
    client: LLMClient,
    papers: list[dict[str, Any]],
    *,
    problem_markdown: str,
    research_plan_json: str,
    min_shortlist: int = 0,
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
                min_shortlist=min_shortlist,
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
    min_shortlist: int = 0,
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
            min_shortlist=min_shortlist,
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

def _read_screening_min_shortlist(ctx: Context, paper_count: int, max_shortlist: int) -> int:
    return _bounded_config_int(
        ctx,
        ("read_screening_min_shortlist", "research_read_min_shortlist"),
        default=0,
        minimum=0,
        maximum=max(0, min(paper_count, max_shortlist)),
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
    min_shortlist: int = 0,
    required_facets: list[str] | None = None,
    papers_by_id: dict[str, dict[str, Any]] | None = None,
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
    _backfill_read_shortlist(
        output,
        coarse_by_id=coarse_by_id,
        min_shortlist=min_shortlist,
        max_shortlist=max_shortlist,
        required_facets=required_facets or [],
        papers_by_id=papers_by_id or {},
    )
    return output

def _backfill_read_shortlist(
    rows: list[dict[str, Any]],
    *,
    coarse_by_id: dict[str, dict[str, Any]],
    min_shortlist: int,
    max_shortlist: int,
    required_facets: list[str],
    papers_by_id: dict[str, dict[str, Any]],
) -> None:
    """Deterministically backfill plausible papers when LLM reranking is too conservative."""
    if min_shortlist <= 0:
        return
    kept = [row for row in rows if _decision_value(row.get("decision")) == "keep"]
    target = min(max_shortlist, min_shortlist)
    if len(kept) >= target:
        return

    covered_facets = {
        _facet_value(row)
        for row in kept
        if _facet_value(row)
    }
    covered_required_facets = {
        facet
        for facet in required_facets
        if any(_facet_matches(covered, facet) for covered in covered_facets)
    }
    next_priority = max((_optional_int(row.get("reading_priority")) or 0 for row in kept), default=0) + 1

    candidates: list[dict[str, Any]] = []
    for row in rows:
        if _decision_value(row.get("decision")) == "keep":
            continue
        paper_id = str(row.get("paper_id") or "")
        coarse = coarse_by_id.get(paper_id, {})
        coarse_decision = _decision_value(coarse.get("decision"))
        relevance = _score_value(row.get("relevance_score"), row.get("coarse_relevance_score"), coarse.get("coarse_relevance_score"))
        if coarse_decision != "keep" and relevance < 3:
            continue
        candidate = dict(row)
        candidate["_backfill_score"] = relevance + _score_value(row.get("quality_score"), coarse.get("quality_score"))
        candidate["_backfill_facet"] = (
            _facet_value(row)
            or _facet_value(coarse)
            or _infer_required_facet(papers_by_id.get(paper_id, {}), required_facets)
        )
        candidates.append(candidate)

    candidates.sort(
        key=lambda row: (
            0 if _backfill_targets_missing_required(row, covered_required_facets, required_facets) else 1,
            0 if str(row.get("_backfill_facet") or "") not in covered_facets else 1,
            -int(row.get("_backfill_score") or 0),
            str(row.get("paper_id") or ""),
        )
    )
    row_by_id = {str(row.get("paper_id") or ""): row for row in rows}
    for candidate in candidates:
        if len(kept) >= target:
            break
        paper_id = str(candidate.get("paper_id") or "")
        row = row_by_id.get(paper_id)
        if row is None:
            continue
        row["decision"] = "keep"
        row["reading_priority"] = next_priority
        relevance_score = row.get("relevance_score")
        if relevance_score is None or relevance_score == "":
            row["relevance_score"] = candidate.get("coarse_relevance_score") or candidate.get("relevance_score")
        quality_score = row.get("quality_score")
        if quality_score is None or quality_score == "":
            row["quality_score"] = candidate.get("quality_score")
        row["reason"] = (
            str(row.get("reason") or "").strip()
            + " Backfilled from plausible coarse-screened candidates to meet the configured read-stage coverage target."
        ).strip()
        facet = str(candidate.get("_backfill_facet") or "").strip()
        if facet:
            covered_facets.add(facet)
            matched_required = _matched_required_facet(facet, required_facets)
            if matched_required:
                covered_required_facets.add(matched_required)
                row["reason"] = (str(row.get("reason") or "").strip() + f" Target facet: {matched_required}.").strip()
            if not row.get("likely_facet") and not row.get("evidence_role"):
                row["likely_facet"] = facet
        kept.append(row)
        next_priority += 1

def _score_value(*values: object) -> int:
    for value in values:
        parsed = _optional_int(value)
        if parsed is not None:
            return parsed
    return 0

def _facet_value(row: dict[str, Any]) -> str:
    return str(row.get("evidence_role") or row.get("likely_facet") or "").strip().lower()

def _read_required_facets(ctx: Context) -> list[str]:
    raw = ctx.config.get("research_required_facets") or ctx.config.get("required_facets")
    if not isinstance(raw, list):
        return []
    output: list[str] = []
    seen: set[str] = set()
    for item in raw:
        clean = _normalize_facet(str(item or ""))
        if clean and clean not in seen:
            seen.add(clean)
            output.append(clean)
    return output

def _infer_required_facet(paper: dict[str, Any], required_facets: list[str]) -> str:
    if not paper or not required_facets:
        return ""
    text = " ".join(
        str(value or "")
        for value in (
            paper.get("title"),
            paper.get("abstract"),
            " ".join(_string_items(paper.get("categories"), limit=12)),
        )
    ).lower()
    best_facet = ""
    best_score = 0
    for facet in required_facets:
        score = sum(1 for term in _facet_terms(facet) if term and term in text)
        if score > best_score:
            best_score = score
            best_facet = facet
    return best_facet if best_score > 0 else ""

def _backfill_targets_missing_required(
    row: dict[str, Any],
    covered_required_facets: set[str],
    required_facets: list[str],
) -> bool:
    facet = str(row.get("_backfill_facet") or "").strip().lower()
    matched = _matched_required_facet(facet, required_facets)
    return bool(matched and matched not in covered_required_facets)

def _matched_required_facet(facet: str, required_facets: list[str]) -> str:
    for required in required_facets:
        if _facet_matches(facet, required):
            return required
    return ""

def _facet_matches(value: str, required: str) -> bool:
    left = _normalize_facet(value)
    right = _normalize_facet(required)
    if not left or not right:
        return False
    if left == right:
        return True
    left_terms = _facet_terms(left)
    right_terms = _facet_terms(right)
    return bool(left_terms and right_terms and len(left_terms & right_terms) >= min(2, len(right_terms)))

def _facet_terms(value: str) -> set[str]:
    normalized = _normalize_facet(value)
    terms: set[str] = set()
    for chunk in normalized.replace("-", "_").replace("/", "_").split("_"):
        clean = chunk.strip()
        if len(clean) >= 3 and clean not in {"and", "the", "for", "with", "from"}:
            terms.add(clean)
    return terms

def _normalize_facet(value: str) -> str:
    text = str(value or "").strip().lower()
    cleaned = []
    previous_sep = False
    for char in text:
        if char.isalnum():
            cleaned.append(char)
            previous_sep = False
        elif not previous_sep:
            cleaned.append("_")
            previous_sep = True
    return "".join(cleaned).strip("_")

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

__all__ = [
    "execute_plan",
    "execute_search",
    "execute_read",
    "execute_synthesize",
]
