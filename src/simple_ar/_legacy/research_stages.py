"""Compatibility facade for the historical eight-stage research prefix.

The V2.8 application path lives under :mod:`simple_ar.app` and the reusable
capabilities under :mod:`simple_ar.research`.  This module deliberately keeps
the old ``Context`` and ``01-plan`` ... ``04-synthesize`` artifact contract
working for ``simple-ar run`` and SurveyBench, but it no longer owns a second
Search/Read/Synthesis implementation.

The functions below do three things only:

* adapt the old ``Context`` to canonical planning and source capabilities;
* preserve the old artifact names and bounded legacy retrieval trace;
* project canonical Read/Synthesis results into the old stage directories.

New behavior must be added to the canonical modules, never here.
"""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
from typing import Any, Mapping

from simple_ar.core.artifacts import write_json, write_jsonl, write_text
from simple_ar.core.pipeline import Context
from simple_ar.core.runtime import (
    ensure_heading as _ensure_heading,
    handle_llm_failure as _handle_llm_failure,
    llm_client as _llm_client,
    read_jsonl_artifact as _read_jsonl_artifact,
    safe_read_json_artifact as _safe_read_json_artifact,
)
from simple_ar.integrations.llm import LLMError
from simple_ar.literature.arxiv_client import ArxivSearchClient, LiteratureSearchError
from simple_ar.literature.cache import DEFAULT_CACHE_DIR, get_cached, put_cache
from simple_ar.literature.models import Paper
from simple_ar.literature.openalex_client import OpenAlexSearchClient
from simple_ar.literature.semantic_scholar_client import SemanticScholarSearchClient
from simple_ar.research.brief import evidence_pack_from_read
from simple_ar.research.connectors import (
    ArxivConnector,
    OpenAlexConnector,
    SemanticScholarConnector,
)
from simple_ar.research.contracts import QueryPlan, SourcePlan
from simple_ar.research.documents.ingest import DocumentBundle
from simple_ar.research.evidence.coverage import build_coverage_report, coverage_report_markdown
from simple_ar.research.evidence.reader import ReadRequest, ReadResult, read_documents
from simple_ar.research.outputs.artifacts import (
    READ_SHORTLIST,
    SEARCH_COVERAGE_JSON,
    SEARCH_COVERAGE_MD,
    SEARCH_FULLTEXT_EXTRACTION,
    SEARCH_FULLTEXT_MANIFEST,
    SEARCH_INDEX_META,
    SEARCH_META,
    SEARCH_PAPERS,
    SEARCH_RESEARCH_PLAN,
    SEARCH_RETRIEVAL_ROUNDS,
    SEARCH_RETRIEVAL_SELECTION,
    write_read_card_artifacts_from_result,
    write_read_review_artifacts,
    write_search_document_artifacts,
    write_synthesis_brief_artifact,
    write_synthesis_evidence_artifacts,
)
from simple_ar.research.planning.capability import (
    ResearchPlanRequest,
    ResearchPlanResult,
    build_research_plan,
    build_requested_research_plan,
    build_research_scope,
)
from simple_ar.research.service import (
    load_notes_markdown,
    load_paper_notes_json,
    load_problem_markdown,
    load_search_document_bundle,
    load_search_paper_rows,
)
from simple_ar.research.sources import SearchProviderRegistry, default_search_provider_registry
from simple_ar.research.sources.base import SearchResponse, primary_query
from simple_ar.research.sources.capability import (
    SearchRequest,
    SearchResult,
    SearchSelectionPolicy,
    search_sources,
    select_search_result,
)
from simple_ar.research.synthesis import SynthesisRequest, synthesize_evidence
from simple_ar.retrieval.evidence import collect_stage_evidence, ensure_source_plan


def execute_plan(ctx: Context) -> None:
    """Project the canonical scope planner into the old plan directory."""

    client = _llm_client(ctx)
    if client is not None:
        try:
            ctx.emit("stage_message", "Calling LLM for research planning.")
            goal, problem = build_research_scope(ctx.topic, llm_client=client)
        except LLMError as exc:
            _handle_llm_failure(ctx, "LLM planning failed", exc)
            goal, problem = build_research_scope(ctx.topic)
    else:
        goal, problem = build_research_scope(ctx.topic)
    write_text(ctx.artifact_path("goal.md"), goal)
    write_text(ctx.artifact_path("problem.md"), problem)


def execute_search(
    ctx: Context,
    *,
    provider_registry: SearchProviderRegistry | None = None,
) -> None:
    """Run canonical search and project its result into ``02-search``."""

    problem = load_problem_markdown(ctx)
    query = _search_query(ctx, problem)
    plan = _plan_research(ctx, problem, query)
    source_plan = plan.source_plan
    write_json(ctx.artifact_path(SEARCH_RESEARCH_PLAN), plan.to_handoff_dict())

    max_documents = _research_document_cap(source_plan, _max_papers(ctx))
    used_fixture_fallback = False
    if _should_use_source_plan(ctx, source_plan):
        result, retrieval_rows = _run_compat_search(
            ctx,
            plan,
            max_documents=max_documents,
            provider_registry=provider_registry,
        )
        if not result.selected_papers:
            if not _allow_fixture_fallback(ctx):
                write_jsonl(ctx.artifact_path(SEARCH_RETRIEVAL_ROUNDS), retrieval_rows)
                write_jsonl(ctx.artifact_path(SEARCH_RETRIEVAL_SELECTION), [])
                raise LiteratureSearchError(_live_search_failure_message(result))
            ctx.emit(
                "stage_message",
                "No live or cached literature metadata available; using explicit fixture fallback.",
            )
            result, retrieval_rows = _fixture_search(plan, ctx.topic, max_documents)
            used_fixture_fallback = True
    else:
        ctx.emit(
            "stage_message",
            "Using fixture paper metadata because --offline-search is enabled.",
        )
        result, retrieval_rows = _fixture_search(plan, ctx.topic, max_documents)

    coverage = result.coverage_report
    write_jsonl(ctx.artifact_path(SEARCH_RETRIEVAL_ROUNDS), retrieval_rows)
    write_jsonl(ctx.artifact_path(SEARCH_RETRIEVAL_SELECTION), list(result.selection_rows))
    write_json(ctx.artifact_path(SEARCH_COVERAGE_JSON), coverage)
    write_text(ctx.artifact_path(SEARCH_COVERAGE_MD), coverage_report_markdown(coverage))

    papers = list(result.selected_papers)
    meta: dict[str, object] = {
        "query": primary_query(source_plan) or query,
        "queries": list(source_plan.queries),
        "max_papers": _max_papers(ctx),
        "sources": list(source_plan.sources),
        "source": _result_source(papers),
        "sources_used": sorted({paper.source for paper in papers}),
        "source_plan": source_plan.to_row(),
        "research_plan": SEARCH_RESEARCH_PLAN,
        "research_planner": plan.query_plan.planner,
        "query_plan_max_rounds": plan.query_plan.max_rounds,
        "query_plan_auto_expansion": plan.query_plan.auto_expansion,
        "required_facets": list(plan.query_plan.required_facets),
        "status": (
            "fixture_fallback"
            if used_fixture_fallback
            else ("offline_fixture" if _all_fixture(papers) else ("ok" if papers else result.status))
        ),
        "allow_fixture_fallback": used_fixture_fallback,
        "returned": len(papers),
        "retrieval_rounds": SEARCH_RETRIEVAL_ROUNDS,
        "retrieval_selection": SEARCH_RETRIEVAL_SELECTION,
        "coverage_report": SEARCH_COVERAGE_JSON,
        "coverage_status": coverage.get("status", "unknown"),
        "missing_facets": coverage.get("missing_facets", []),
        "attempt_count": len(retrieval_rows),
        "candidate_selection_count": len(result.selection_rows),
        "kept_after_retrieval_selection": len(papers),
        "executed_retrieval_rounds": _executed_rounds(coverage),
        "planned_retrieval_rounds": plan.query_plan.max_rounds,
    }
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


def execute_read(ctx: Context) -> None:
    """Read the canonical document bundle and project old read artifacts."""

    papers = load_search_paper_rows(ctx)
    bundle = load_search_document_bundle(ctx)
    client = _llm_client(ctx)
    existing_shortlist = _existing_shortlist_ids(ctx)
    read_result = read_documents(
        ReadRequest(
            bundle=bundle,
            paper_ids=None if client is not None else existing_shortlist,
            topic=ctx.topic,
            problem_markdown=load_problem_markdown(ctx),
            research_plan_json=json.dumps(
                _safe_read_json_artifact(ctx, SEARCH_RESEARCH_PLAN),
                ensure_ascii=False,
                indent=2,
            ),
            config=ctx.config,
            use_llm=client is not None,
            llm_client=client,
        )
    )
    review_meta = write_read_review_artifacts(
        stage_dir=ctx.stage_dir(),
        papers=papers,
        retrieval_selection=_read_jsonl_artifact(ctx, SEARCH_RETRIEVAL_SELECTION),
        coverage_report=_safe_read_json_artifact(ctx, SEARCH_COVERAGE_JSON),
        llm_decisions=list(read_result.screening_decisions) or None,
    )
    _stage_evidence(ctx, "read")
    ctx.emit(
        "stage_message",
        "Built read-stage shortlist and reading table.",
        shortlist_count=review_meta.get("shortlist_count", 0),
    )

    notes = list(read_result.paper_notes)
    if not notes and read_result.bundle.records:
        notes = _fallback_notes(_papers_for_bundle(papers, read_result.bundle))
    write_json(ctx.artifact_path("paper_notes.json"), notes)
    write_text(
        ctx.artifact_path("notes.md"),
        read_result.notes_markdown or _render_fallback_notes(notes),
    )
    if _debug_artifacts_enabled(ctx) and read_result.bundle.records:
        meta = write_read_card_artifacts_from_result(
            stage_dir=ctx.stage_dir(),
            result=read_result,
        )
        ctx.emit(
            "stage_message",
            "Built reading cards from the canonical Read result.",
            paper_card_count=meta.get("paper_card_count", 0),
            claim_card_count=meta.get("claim_card_count", 0),
        )


def _write_read_cards(ctx: Context) -> None:
    """Keep the old debug-card helper as a projection-only compatibility API."""

    bundle = load_search_document_bundle(ctx)
    if not bundle.records:
        return
    result = read_documents(
        ReadRequest(bundle=bundle, paper_ids=_existing_shortlist_ids(ctx))
    )
    meta = write_read_card_artifacts_from_result(
        stage_dir=ctx.stage_dir(),
        result=result,
    )
    ctx.emit(
        "stage_message",
        "Built reading cards from the canonical Read result.",
        paper_card_count=meta.get("paper_card_count", 0),
        claim_card_count=meta.get("claim_card_count", 0),
    )


def execute_synthesize(ctx: Context) -> None:
    """Synthesize the canonical Read result and project old handoff files."""

    papers = load_search_paper_rows(ctx)
    bundle = load_search_document_bundle(ctx)
    read_result = read_documents(
        ReadRequest(bundle=bundle, paper_ids=_existing_shortlist_ids(ctx))
    )
    notes = load_paper_notes_json(ctx)
    if notes:
        read_result = replace(
            read_result,
            paper_notes=tuple(notes),
            notes_markdown=load_notes_markdown(ctx),
        )
    source_plan = _downstream_source_plan(ctx)
    coverage = _safe_read_json_artifact(ctx, SEARCH_COVERAGE_JSON)
    _stage_evidence(ctx, "synthesize")
    evidence_pack = evidence_pack_from_read(
        ctx.topic,
        read_result,
        coverage=coverage,
        source_plan=source_plan,
        execution_context=str(ctx.config.get("research_execution_context") or ""),
    )
    client = _llm_client(ctx)
    try:
        synthesis = synthesize_evidence(
            SynthesisRequest(
                evidence_pack=evidence_pack,
                idea_limit=_idea_limit(ctx),
                novelty_backend=str(source_plan.get("budget", {}).get("novelty_backend") or "local"),
                use_llm=client is not None,
                llm_client=client,
            )
        )
    except LLMError as exc:
        _handle_llm_failure(ctx, "LLM synthesis failed", exc)
        synthesis = synthesize_evidence(
            SynthesisRequest(
                evidence_pack=evidence_pack,
                idea_limit=_idea_limit(ctx),
                novelty_backend="local",
            )
        )

    _write_legacy_synthesis_projection(
        ctx,
        source_plan=source_plan,
        papers=papers,
        read_result=read_result,
        coverage=coverage,
    )
    synthesis_markdown = synthesis.synthesis_markdown.strip() or _fallback_synthesis_markdown(
        ctx.topic,
        notes,
    )
    hypothesis_markdown = synthesis.hypothesis_markdown.strip() or (
        "# Hypothesis\n\n"
        "A bounded, evidence-grounded change can be evaluated against the prepared "
        "baseline without changing the task boundary.\n"
    )
    write_text(ctx.artifact_path("synthesis.md"), _ensure_heading(synthesis_markdown, "Synthesis"))
    write_text(ctx.artifact_path("hypothesis.md"), _ensure_heading(hypothesis_markdown, "Hypothesis"))


def _plan_research(ctx: Context, problem: str, query: str) -> ResearchPlanResult:
    """Build one canonical research plan, retaining old failure semantics."""

    mode = str(ctx.config.get("research_planner") or "auto").strip().lower()
    client = _llm_client(ctx)
    request = ResearchPlanRequest(
        topic=ctx.topic,
        problem_markdown=problem,
        config=ctx.config,
        default_query=query,
        default_max_results=_max_papers(ctx),
        use_llm=mode != "deterministic" and client is not None,
        llm_client=client,
    )
    try:
        return build_requested_research_plan(request)
    except (LLMError, ValueError) as exc:
        _handle_llm_failure(ctx, "LLM research planning failed", exc)
        return build_research_plan(
            ResearchPlanRequest(
                topic=ctx.topic,
                problem_markdown=problem,
                config=ctx.config,
                default_query=query,
                default_max_results=_max_papers(ctx),
            )
        )


def _run_compat_search(
    ctx: Context,
    plan: ResearchPlanResult,
    *,
    max_documents: int,
    provider_registry: SearchProviderRegistry | None,
) -> tuple[SearchResult, list[dict[str, object]]]:
    """Use canonical search with the old bounded trace policy.

    The old facade historically stopped after a useful source filled the
    budget and could spend one extra round on missing facets. That policy is
    retained only here as a compatibility concern; provider invocation,
    caching, failure normalization, ranking, and selection remain canonical.
    """

    registry = provider_registry or _default_search_provider_registry(plan.source_plan)
    initial_queries = _planned_queries(plan.query_plan, plan.source_plan)
    candidate_cap = _candidate_collection_cap(max_documents)
    initial = _search_round(
        ctx,
        plan.source_plan,
        queries=initial_queries,
        provider_registry=registry,
        stop_after_papers=candidate_cap,
    )
    policy = SearchSelectionPolicy(
        topic=ctx.topic,
        questions=plan.questions,
        query_plan=plan.query_plan,
        max_documents=max_documents,
        next_query_limit=_follow_up_query_limit(plan.source_plan),
    )
    selected = select_search_result(initial, policy=policy)
    retrieval_rows = _retrieval_rows(initial, plan.query_plan, round_index=1)

    if len(selected.selected_papers) < max_documents and plan.query_plan.max_rounds > 1:
        followups = _coverage_follow_up_queries(selected.coverage_report)
        if followups:
            ctx.emit(
                "stage_message",
                f"Coverage gaps remain; running {len(followups)} bounded follow-up query attempt(s).",
            )
            augmented_plan = replace(
                plan.query_plan,
                queries=_unique_strings([*initial_queries, *followups]),
                query_specs=[
                    *plan.query_plan.query_specs,
                    *_coverage_follow_up_specs(selected.coverage_report),
                ],
            )
            followup = _search_round(
                ctx,
                plan.source_plan,
                queries=followups,
                provider_registry=registry,
                stop_after_papers=candidate_cap,
            )
            combined = _merge_search_results(initial, followup)
            selected = select_search_result(
                combined,
                policy=replace(
                    policy,
                    query_plan=augmented_plan,
                    next_query_limit=0,
                ),
            )
            retrieval_rows.extend(
                _retrieval_rows(followup, augmented_plan, round_index=2)
            )
            selection_rows = _annotate_selection_rounds(
                selected.selection_rows,
                retrieval_rows,
            )
            selected = replace(
                selected,
                selection_rows=tuple(selection_rows),
                coverage_report=build_coverage_report(
                    topic=ctx.topic,
                    questions=list(plan.questions),
                    query_plan=augmented_plan,
                    selection_rows=selection_rows,
                    retrieval_rows=retrieval_rows,
                    max_documents=max_documents,
                    next_query_limit=0,
                ),
            )
    return selected, retrieval_rows


def _search_round(
    ctx: Context,
    source_plan: SourcePlan,
    *,
    queries: list[str],
    provider_registry: SearchProviderRegistry,
    stop_after_papers: int,
) -> SearchResult:
    providers = tuple(source for source in source_plan.sources if source != "fixture")
    if not providers:
        return SearchResult(
            status="empty",
            responses=(),
            papers=(),
            diagnostics=("No live source provider was configured.",),
        )
    for query in queries:
        ctx.emit("stage_message", f"Searching {', '.join(providers)} for `{query}`.")
    return search_sources(
        SearchRequest(
            queries=tuple(queries),
            providers=providers,
            max_results_per_query=source_plan.max_results_per_query,
            filters=dict(source_plan.filters),
            cache_dir=_cache_dir(ctx),
            cache_enabled=source_plan.cache_enabled,
            stop_after_papers=stop_after_papers,
            cache_get=get_cached,
            cache_put=put_cache,
        ),
        registry=provider_registry,
    )


def _fixture_search(
    plan: ResearchPlanResult,
    topic: str,
    max_documents: int,
) -> tuple[SearchResult, list[dict[str, object]]]:
    query = primary_query(plan.source_plan) or topic
    papers = _fixture_papers()
    result = SearchResult(
        status="completed",
        responses=(
            SearchResponse(
                source="fixture",
                query=query,
                papers=papers,
                status="offline_fixture",
            ),
        ),
        papers=tuple(papers),
    )
    selected = select_search_result(
        result,
        policy=SearchSelectionPolicy(
            topic=topic,
            questions=plan.questions,
            query_plan=plan.query_plan,
            max_documents=max_documents,
        ),
    )
    return selected, _retrieval_rows(selected, plan.query_plan, round_index=1)


def _merge_search_results(first: SearchResult, second: SearchResult) -> SearchResult:
    papers: list[Paper] = []
    seen: set[str] = set()
    for paper in (*first.papers, *second.papers):
        if paper.id in seen:
            continue
        papers.append(paper)
        seen.add(paper.id)
    responses = (*first.responses, *second.responses)
    failed = [
        response
        for response in responses
        if response.status.lower() in {"failed", "error", "blocked"}
    ]
    status = "failed" if responses and len(failed) == len(responses) else (
        "partial" if failed else ("completed" if papers else "empty")
    )
    return SearchResult(
        status=status,  # type: ignore[arg-type]
        responses=responses,
        papers=tuple(papers),
        diagnostics=tuple((*first.diagnostics, *second.diagnostics)),
    )


def _retrieval_rows(
    result: SearchResult,
    query_plan: QueryPlan,
    *,
    round_index: int,
) -> list[dict[str, object]]:
    query_numbers = {
        query: index
        for index, query in enumerate(query_plan.queries, start=1)
        if query.strip()
    }
    specs = {
        str(row.get("query") or "").strip(): row
        for row in query_plan.query_specs
        if isinstance(row, Mapping) and str(row.get("query") or "").strip()
    }
    rows: list[dict[str, object]] = []
    for index, response in enumerate(result.responses, start=1):
        query = response.query.strip()
        spec = specs.get(query, {})
        row: dict[str, object] = {
            "schema_version": "retrieval_round.v1",
            "round": round_index,
            "query_index": query_numbers.get(query, index),
            "query": query,
            "source": response.source,
            "status": "error" if response.status.lower() == "failed" else response.status,
            "returned": len(response.papers),
        }
        if response.message:
            row["error"] = response.message
        facet = str(spec.get("facet") or "").strip()
        rationale = str(spec.get("rationale") or "").strip()
        if facet:
            row["facet"] = facet
        if rationale:
            row["query_rationale"] = rationale[:240]
        for key, limit in (("title_keywords", 5), ("abstract_keywords", 8)):
            value = spec.get(key)
            if isinstance(value, list) and value:
                row[key] = [str(item) for item in value[:limit]]
        rows.append(row)
    return rows


def _annotate_selection_rounds(
    rows: tuple[dict[str, Any], ...],
    retrieval_rows: list[dict[str, object]],
) -> list[dict[str, Any]]:
    rounds = {
        (str(row.get("source") or ""), str(row.get("query") or "")): int(row.get("round") or 1)
        for row in retrieval_rows
    }
    result: list[dict[str, Any]] = []
    for row in rows:
        value = dict(row)
        key = (str(value.get("source") or ""), str(value.get("query") or ""))
        value["round"] = rounds.get(key, value.get("round", 1))
        result.append(value)
    return result


def _default_search_provider_registry(source_plan: SourcePlan) -> SearchProviderRegistry:
    """Build canonical connectors while retaining old test injection points."""

    max_results = source_plan.max_results_per_query
    return default_search_provider_registry(
        local_documents=source_plan.local_documents,
        arxiv_page_size=max_results,
        connector_factories={
            "openalex": lambda: OpenAlexConnector(OpenAlexSearchClient()),
            "semantic_scholar": lambda: SemanticScholarConnector(SemanticScholarSearchClient()),
            "arxiv": lambda: ArxivConnector(ArxivSearchClient(page_size=max_results)),
        },
    )


def _write_legacy_synthesis_projection(
    ctx: Context,
    *,
    source_plan: dict[str, Any],
    papers: list[dict[str, Any]],
    read_result: ReadResult,
    coverage: dict[str, Any],
) -> None:
    """Write old debug/brief files from canonical typed results."""

    meta = _safe_read_json_artifact(ctx, SEARCH_INDEX_META)
    fulltext_manifest = _safe_read_json_artifact(ctx, SEARCH_FULLTEXT_MANIFEST)
    fulltext_extraction = _safe_read_json_artifact(ctx, SEARCH_FULLTEXT_EXTRACTION)
    paper_notes = list(read_result.paper_notes) or _fallback_notes(
        _papers_for_bundle(papers, read_result.bundle)
    )
    brief_meta = write_synthesis_brief_artifact(
        stage_dir=ctx.stage_dir(),
        topic=ctx.topic,
        source_plan=source_plan,
        papers=papers,
        paper_notes=paper_notes,
        coverage_report=coverage,
        index_meta=meta,
        fulltext_manifest=fulltext_manifest,
        fulltext_extraction=fulltext_extraction,
    )
    ctx.emit(
        "stage_message",
        "Built compact synthesis brief from the canonical evidence handoff.",
        idea_candidate_count=brief_meta.get("idea_candidate_count", 0),
    )
    if not _debug_artifacts_enabled(ctx):
        return
    write_synthesis_evidence_artifacts(
        stage_dir=ctx.stage_dir(),
        topic=ctx.topic,
        source_plan=source_plan,
        papers=papers,
        documents=[record.to_row() for record in read_result.bundle.records],
        sections=[section.to_row() for section in read_result.bundle.sections],
        chunks=[chunk.to_row() for chunk in read_result.bundle.chunks],
        index_meta=meta,
        paper_cards=[card.to_row() for card in read_result.paper_cards],
        claim_cards=[card.to_row() for card in read_result.claim_cards],
        method_cards=[card.to_row() for card in read_result.method_cards],
        dataset_cards=[card.to_row() for card in read_result.dataset_cards],
        code_links=[link.to_row() for link in read_result.code_links],
        coverage_report=coverage,
        fulltext_manifest=fulltext_manifest,
        fulltext_extraction=fulltext_extraction,
    )


def _downstream_source_plan(ctx: Context) -> dict[str, Any]:
    plan = _safe_read_json_artifact(ctx, SEARCH_RESEARCH_PLAN)
    if isinstance(plan.get("source_plan"), dict):
        return dict(plan["source_plan"])
    meta = _safe_read_json_artifact(ctx, SEARCH_META)
    if isinstance(meta.get("source_plan"), dict):
        return dict(meta["source_plan"])
    return {
        "schema_version": "source_plan.reconstructed.v1",
        "queries": list(meta.get("queries") or [meta.get("query") or ctx.topic]),
        "sources": list(meta.get("sources") or meta.get("sources_used") or []),
        "mode": str(ctx.config.get("research_mode") or "standard"),
        "budget": {},
    }


def _stage_evidence(ctx: Context, stage: str) -> list[dict[str, Any]]:
    """Retain the old run-level evidence ledger as a compatibility projection."""

    if ctx.config.get("use_retrieval", True) is False:
        return []
    ensure_source_plan(ctx.run_dir, ctx.topic)
    try:
        rows = collect_stage_evidence(
            ctx.run_dir,
            ctx.topic,
            stage,
            top_k=_retrieval_top_k(ctx),
        )
        ledger = ctx.run_dir / "evidence_ledger.jsonl"
        if not ledger.exists():
            write_text(ledger, "")
        return rows
    except Exception as exc:
        ctx.emit("stage_message", f"Legacy retrieval projection failed for {stage}; continuing. {exc}")
        ledger = ctx.run_dir / "evidence_ledger.jsonl"
        if not ledger.exists():
            write_text(ledger, "")
        return []


def _retrieval_top_k(ctx: Context) -> int:
    try:
        value = int(ctx.config.get("retrieval_top_k", 4))
    except (TypeError, ValueError):
        value = 4
    return min(max(1, value), 20)


def _existing_shortlist_ids(ctx: Context) -> tuple[str, ...] | None:
    path = ctx.artifact_path(READ_SHORTLIST)
    if not path.exists():
        return None
    return tuple(
        str(row.get("paper_id") or "").strip()
        for row in _read_jsonl_artifact(ctx, READ_SHORTLIST)
        if str(row.get("paper_id") or "").strip()
    )


def _papers_for_bundle(
    papers: list[dict[str, Any]],
    bundle: DocumentBundle,
) -> list[dict[str, Any]]:
    by_id = {str(paper.get("id") or ""): paper for paper in papers}
    result: list[dict[str, Any]] = []
    for record in bundle.records:
        identifiers = {
            record.document_id,
            str(record.source_id or ""),
            str(record.metadata.get("paper_id") or ""),
        }
        paper = next((by_id[item] for item in identifiers if item in by_id), None)
        if paper is not None and paper not in result:
            result.append(paper)
    return result


def _fallback_notes(papers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "paper_id": str(paper.get("id") or ""),
            "title": str(paper.get("title") or ""),
            "evidence_role": "other",
            "one_sentence_summary": "Metadata-only fallback note; no deeper interpretation was generated.",
            "problem": "unknown from available metadata",
            "method": "unknown from available metadata",
            "datasets": [],
            "metrics": [],
            "key_claims": [],
            "limitations": ["Offline reading fallback only saw metadata."],
            "relation_to_topic": "Kept within the bounded search selection.",
            "synthesis_hint": "Use only as a cautious metadata reference.",
            "possible_experiment_hooks": [],
            "open_questions": ["Use full text or LLM reading before making strong claims."],
            "confidence": "low",
        }
        for paper in papers
    ]


def _render_fallback_notes(notes: list[dict[str, Any]]) -> str:
    lines = ["# Literature Notes", ""]
    for note in notes:
        lines.extend(
            [
                f"## {note.get('paper_id') or 'unknown'}",
                f"- Summary: {note.get('one_sentence_summary') or 'Not specified.'}",
                f"- Method: {note.get('method') or 'Not specified.'}",
                f"- Limitations: {', '.join(str(item) for item in note.get('limitations', [])) or 'Not specified.'}",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def _fallback_synthesis_markdown(topic: str, notes: list[dict[str, Any]]) -> str:
    excerpt = json.dumps(notes[:3], ensure_ascii=False, indent=2)[:1200]
    return (
        "# Synthesis\n\n"
        f"The canonical evidence boundary produced a bounded synthesis for `{topic}`.\n\n"
        "Available evidence was insufficient for model-generated prose; the retained metadata is:\n\n"
        f"```json\n{excerpt}\n```\n"
    )


def _coverage_follow_up_queries(report: Mapping[str, Any]) -> list[str]:
    rows = report.get("follow_up_queries")
    if not isinstance(rows, list):
        return []
    return [
        query
        for row in rows
        if isinstance(row, Mapping)
        for query in [str(row.get("query") or "").strip()]
        if query
    ]


def _coverage_follow_up_specs(report: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = report.get("follow_up_queries")
    if not isinstance(rows, list):
        return []
    return [
        {
            "query": query,
            "facet": str(row.get("facet") or ""),
            "rationale": str(row.get("reason") or "coverage_follow_up"),
        }
        for row in rows
        if isinstance(row, Mapping)
        for query in [str(row.get("query") or "").strip()]
        if query
    ]


def _follow_up_query_limit(source_plan: SourcePlan) -> int:
    value = source_plan.budget.get("max_follow_up_queries")
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return min(value, 5)
    return 3


def _planned_queries(query_plan: QueryPlan, source_plan: SourcePlan) -> list[str]:
    queries = [query.strip() for query in query_plan.queries if query.strip()]
    if queries:
        return queries
    return [query.strip() for query in source_plan.queries if query.strip()] or [query_plan.topic]


def _search_query(ctx: Context, problem: str) -> str:
    configured = ctx.config.get("search_query")
    if isinstance(configured, str) and configured.strip():
        return configured.strip()
    return (ctx.topic.strip() or " ".join(problem.split()))[:240]


def _max_papers(ctx: Context) -> int:
    try:
        value = int(ctx.config.get("max_papers", 5))
    except (TypeError, ValueError):
        value = 5
    return min(max(1, value), 20)


def _research_document_cap(source_plan: SourcePlan, default: int) -> int:
    value = source_plan.budget.get("max_documents")
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else max(1, default)


def _candidate_collection_cap(max_documents: int) -> int:
    return 1 if max_documents <= 1 else min(max_documents * 2, max_documents + 10)


def _cache_dir(ctx: Context) -> Path:
    value = ctx.config.get("research_cache_dir")
    return Path(value) if isinstance(value, str) and value.strip() else DEFAULT_CACHE_DIR


def _allow_fixture_fallback(ctx: Context) -> bool:
    return ctx.config.get("allow_fixture_fallback") is True


def _should_use_source_plan(ctx: Context, source_plan: SourcePlan) -> bool:
    if ctx.config.get("use_arxiv") is True:
        return True
    if any(source == "local_files" for source in source_plan.sources):
        return True
    configured = ctx.config.get("research_sources")
    return isinstance(configured, list) and any(
        str(source).strip() and str(source).strip() != "fixture" for source in configured
    )


def _live_search_failure_message(result: SearchResult) -> str:
    attempts = "; ".join(
        f"{response.source}={response.status}" for response in result.responses
    ) or "no provider attempts recorded"
    return (
        "No live or cached literature metadata is available. Default runs do not use fixture metadata "
        "because that would make the report look literature-backed when it is not. Retry later, lower "
        "--max-papers, run with --offline-search for tests, or add --allow-fixture-fallback for demos. "
        f"Provider attempts: {attempts}"
    )


def _result_source(papers: list[Paper]) -> str:
    sources = sorted({paper.source for paper in papers if paper.source})
    return sources[0] if len(sources) == 1 else ("mixed" if sources else "unknown")


def _all_fixture(papers: list[Paper]) -> bool:
    return bool(papers) and all(paper.source == "fixture" for paper in papers)


def _executed_rounds(coverage: Mapping[str, Any]) -> int:
    retrieval = coverage.get("retrieval")
    return int(retrieval.get("executed_rounds") or 0) if isinstance(retrieval, Mapping) else 0


def _idea_limit(ctx: Context) -> int:
    value = ctx.config.get("research_idea_limit", 3)
    try:
        return min(max(1, int(value)), 8)
    except (TypeError, ValueError):
        return 3


def _debug_artifacts_enabled(ctx: Context) -> bool:
    value = ctx.config.get("debug_artifacts", False)
    return value is True or str(value).strip().lower() in {"1", "true", "yes", "on"}


def _unique_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = value.strip()
        if text and text.lower() not in seen:
            seen.add(text.lower())
            result.append(text)
    return result


def _fixture_papers() -> list[Paper]:
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
    "_write_read_cards",
]
