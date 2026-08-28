from __future__ import annotations

from dataclasses import fields
from pathlib import Path
from typing import Any

from simple_ar.core.artifacts import write_json, write_jsonl, write_text
from simple_ar.literature.models import Paper
from simple_ar.research.contracts import (
    CodeLink,
    DatasetCard,
    DocumentRecord,
    DocumentSection,
    IdeaCandidate,
    MethodCard,
    NoveltyCheck,
    PaperCard,
    ClaimCard,
    QueryPlan,
    ResearchQuestion,
    SourcePlan,
    TextChunk,
)
from simple_ar.research.evidence.derivation import (
    build_evidence_review,
    build_experiment_contract,
    build_gap_summary,
    build_idea_candidates,
    build_novelty_checks,
    build_research_eval,
    build_tool_context,
    experiment_contract_markdown,
)
from simple_ar.research.evidence.pack import (
    build_evidence_pack,
    compact_evidence_pack_for_storage,
    evidence_pack_markdown,
)
from simple_ar.research.documents.ingest import build_document_bundle
from simple_ar.research.evidence.reader import ReadRequest, ReadResult, read_documents
from simple_ar.research.documents.records import build_cache_manifest
from simple_ar.research.store.index import write_research_index
from simple_ar.research.tools.contract import (
    artifact_retention_policy_markdown,
    build_artifact_retention_policy,
    build_tool_adapter_contract,
    build_tool_trace_rows,
    external_agent_backend_markdown,
    tool_adapter_contract_markdown,
)


SEARCH_RESEARCH_PLAN = "planning/research_plan.json"
SEARCH_RETRIEVAL_ROUNDS = "traces/retrieval_rounds.jsonl"
SEARCH_RETRIEVAL_SELECTION = "traces/retrieval_selection.jsonl"
SEARCH_COVERAGE_JSON = "review/coverage_report.json"
SEARCH_COVERAGE_MD = "review/coverage_report.md"
SEARCH_DOCUMENTS = "documents/documents.jsonl"
SEARCH_CACHE_MANIFEST = "documents/cache_manifest.json"
SEARCH_FULLTEXT_MANIFEST = "documents/fulltext_manifest.json"
SEARCH_FULLTEXT_EXTRACTION = "documents/fulltext_extraction.json"
SEARCH_SECTIONS = "documents/sections.jsonl"
SEARCH_CHUNKS = "research_index/chunks.jsonl"
SEARCH_INDEX_META = "research_index/index_meta.json"

READ_PAPER_CARDS = "cards/paper_cards.jsonl"
READ_CLAIM_CARDS = "cards/claim_cards.jsonl"
READ_METHOD_CARDS = "cards/method_cards.jsonl"
READ_DATASET_CARDS = "cards/dataset_cards.jsonl"
READ_CODE_LINKS = "cards/code_links.jsonl"
READ_SCREENING_DECISIONS = "review/screening_decisions.jsonl"
READ_SHORTLIST = "review/shortlist.jsonl"
READ_READING_TABLE = "review/reading_table.md"

SYNTHESIS_EVIDENCE_PACK_JSON = "evidence/evidence_pack.json"
SYNTHESIS_EVIDENCE_PACK_MD = "evidence/evidence_pack.md"
SYNTHESIS_GAP_SUMMARY = "evidence/gap_summary.md"
SYNTHESIS_IDEA_CANDIDATES = "evidence/idea_candidates.jsonl"
SYNTHESIS_NOVELTY_CHECKS = "evidence/novelty_checks.jsonl"
SYNTHESIS_BRIEF_JSON = "synthesis_brief.json"

DESIGN_EXPERIMENT_CONTRACT_JSON = "evidence/experiment_contract.json"
DESIGN_EXPERIMENT_CONTRACT_MD = "evidence/experiment_contract.md"
DESIGN_TOOL_CONTEXT_JSON = "evidence/tool_context.json"
DESIGN_TOOL_CONTEXT_MD = "evidence/tool_context.md"
DESIGN_EVIDENCE_REVIEW_MD = "evidence/evidence_review.md"
DESIGN_DECISION_LOG = "evidence/decision_log.jsonl"
DESIGN_EVAL_JSON = "evidence/eval_report.json"
DESIGN_EVAL_MD = "evidence/eval_report.md"
DESIGN_TOOL_ADAPTER_CONTRACT_JSON = "tools/tool_adapter_contract.json"
DESIGN_TOOL_ADAPTER_CONTRACT_MD = "tools/tool_adapter_contract.md"
DESIGN_TOOL_TRACE = "tools/tool_trace.jsonl"
DESIGN_EXTERNAL_AGENT_BACKEND = "tools/external_agent_backend.md"
DESIGN_RETENTION_POLICY_JSON = "governance/artifact_retention_policy.json"
DESIGN_RETENTION_POLICY_MD = "governance/artifact_retention_policy.md"

SEARCH_PAPERS = "papers.jsonl"
SEARCH_META = "search_meta.json"


def build_research_plan_artifact(
    *,
    questions: list[ResearchQuestion],
    query_plan: QueryPlan,
    source_plan: SourcePlan,
) -> dict[str, Any]:
    """Return the compact planning artifact for the search stage.

    Args:
        questions: Research questions and required evidence facets.
        query_plan: Executable query plan produced by deterministic or LLM planning.
        source_plan: Provider and budget plan derived from the query plan.

    Returns:
        A single JSON-friendly artifact that replaces several small planning
        files while keeping each section independently inspectable.
    """
    return {
        "schema_version": "research_plan.v1",
        "planner": query_plan.planner,
        "research_questions": {
            "schema_version": "research_questions.v1",
            "planner": query_plan.planner,
            "questions": [question.to_row() for question in questions],
        },
        "query_plan": query_plan.to_row(),
        "source_plan": source_plan.to_row(),
    }


def write_search_document_artifacts(
    *,
    stage_dir: Path,
    topic: str,
    papers: list[Paper],
    source_plan: SourcePlan,
) -> dict[str, Any]:
    """Write retrieval-owned document and index artifacts for the search stage.

    Args:
        stage_dir: Current ``02-search`` directory.
        topic: Original run topic used for downstream evidence handoffs.
        papers: Selected paper metadata after screening.
        source_plan: Search source plan controlling local documents, chunk cap,
            and index backend.

    Returns:
        Metadata fields to merge into ``search_meta.json``.

    This function intentionally stops at retrieval and ingestion. Semantic
    reading artifacts such as paper cards, claim cards, evidence packs, idea
    candidates, and experiment contracts belong to later pipeline stages.
    """
    compact_artifacts = bool(source_plan.budget.get("compact_artifacts", False))
    bundle = build_document_bundle(
        papers=papers,
        source_plan=source_plan,
        cache_dir=stage_dir / "documents" / "fulltext_cache",
        extraction_dir=stage_dir / "documents" / "extracted_text",
        max_chunks=_chunk_cap(source_plan),
    )
    documents = bundle.records
    fulltext_manifest = bundle.fulltext_manifest
    fulltext_extraction = bundle.fulltext_extraction
    sections = bundle.sections
    chunks = bundle.chunks
    index_meta = write_research_index(
        index_dir=stage_dir / Path(SEARCH_INDEX_META).parent,
        chunks=chunks,
        backend=source_plan.index_backend,
        run_id=stage_dir.parent.name,
        shared_root=source_plan.index_root,
    )
    write_jsonl(stage_dir / SEARCH_DOCUMENTS, [record.to_row() for record in documents])
    write_json(stage_dir / SEARCH_CACHE_MANIFEST, build_cache_manifest(records=documents, source_plan=source_plan))
    write_json(stage_dir / SEARCH_FULLTEXT_MANIFEST, fulltext_manifest)
    write_json(stage_dir / SEARCH_FULLTEXT_EXTRACTION, fulltext_extraction)
    if not compact_artifacts:
        write_jsonl(stage_dir / SEARCH_SECTIONS, [section.to_row() for section in sections])
    meta = {
        "documents": SEARCH_DOCUMENTS,
        "cache_manifest": SEARCH_CACHE_MANIFEST,
        "fulltext_manifest": SEARCH_FULLTEXT_MANIFEST,
        "fulltext_extraction": SEARCH_FULLTEXT_EXTRACTION,
        "chunks": SEARCH_CHUNKS,
        "index_meta": SEARCH_INDEX_META,
        "document_count": len(documents),
        "section_count": len(sections),
        "chunk_count": len(chunks),
        "index_backend": index_meta.get("backend"),
    }
    if not compact_artifacts:
        meta["sections"] = SEARCH_SECTIONS
    return meta


def write_read_review_artifacts(
    *,
    stage_dir: Path,
    papers: list[dict[str, Any]],
    retrieval_selection: list[dict[str, Any]],
    coverage_report: dict[str, Any] | None = None,
    llm_decisions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Write reading-owned review artifacts for retrieved papers.

    The search stage may rank and truncate retrieval candidates to stay within
    budget, but it should not be treated as semantic paper review. This helper
    records the read-stage decision layer: what was shortlisted for structured
    reading, why, and what priority each paper receives.
    """
    retrieval_by_paper = {
        str(row.get("paper_id") or ""): row
        for row in retrieval_selection
        if isinstance(row, dict) and row.get("paper_id")
    }
    llm_by_paper = {
        str(row.get("paper_id") or ""): row
        for row in llm_decisions or []
        if isinstance(row, dict) and row.get("paper_id")
    }
    decisions: list[dict[str, Any]] = []
    shortlist: list[dict[str, Any]] = []
    for index, paper in enumerate(papers, start=1):
        paper_id = str(paper.get("id") or "").strip()
        retrieval_row = retrieval_by_paper.get(paper_id, {})
        llm_row = llm_by_paper.get(paper_id, {})
        priority = int(retrieval_row.get("rank") or index)
        relevance_score = int(retrieval_row.get("relevance_score") or 0)
        llm_decision = str(llm_row.get("decision") or "keep").strip().lower()
        decision_value = llm_decision if llm_decision in {"keep", "drop"} else "keep"
        llm_priority = _optional_int(llm_row.get("reading_priority"))
        if llm_priority is not None:
            priority = llm_priority
        decision = {
            "schema_version": "reading_screening_decision.v1",
            "paper_id": paper_id,
            "title": str(paper.get("title") or ""),
            "source": str(paper.get("source") or ""),
            "decision": decision_value,
            "reason": str(llm_row.get("reason") or "retrieved_within_budget"),
            "reading_priority": priority,
            "retrieval_relevance_score": relevance_score,
            "coarse_relevance_score": _optional_int(llm_row.get("coarse_relevance_score")),
            "reading_relevance_score": _optional_int(llm_row.get("relevance_score")),
            "quality_score": _optional_int(llm_row.get("quality_score")),
            "retrieval_reason": str(retrieval_row.get("reason") or ""),
            "facet": str(
                llm_row.get("evidence_role")
                or llm_row.get("likely_facet")
                or retrieval_row.get("facet")
                or ""
            ),
            "likely_facet": str(llm_row.get("likely_facet") or ""),
            "evidence_role": str(llm_row.get("evidence_role") or ""),
            "synthesis_hint": str(llm_row.get("synthesis_hint") or ""),
            "confidence": str(
                llm_row.get("confidence")
                or ("metadata_or_fulltext_available" if paper.get("abstract") else "metadata_thin")
            ),
        }
        decisions.append(decision)
        if decision_value != "keep":
            continue
        shortlist.append(
            {
                "schema_version": "reading_shortlist_item.v1",
                "paper_id": paper_id,
                "title": str(paper.get("title") or ""),
                "source": str(paper.get("source") or ""),
                "url": paper.get("url"),
                "published": paper.get("published"),
                "reading_priority": priority,
                "facet": decision["facet"],
                "evidence_role": decision["evidence_role"],
                "synthesis_hint": decision["synthesis_hint"],
                "decision": "keep",
                "reason": decision["reason"],
            }
        )
    decisions.sort(key=lambda row: (int(row.get("reading_priority") or 9999), str(row.get("paper_id") or "")))
    shortlist.sort(key=lambda row: (int(row.get("reading_priority") or 9999), str(row.get("paper_id") or "")))
    write_jsonl(stage_dir / READ_SCREENING_DECISIONS, decisions)
    write_jsonl(stage_dir / READ_SHORTLIST, shortlist)
    write_text(
        stage_dir / READ_READING_TABLE,
        _reading_table_markdown(shortlist, coverage_report or {}),
    )
    return {
        "screening_decisions": READ_SCREENING_DECISIONS,
        "shortlist": READ_SHORTLIST,
        "reading_table": READ_READING_TABLE,
        "shortlist_count": len(shortlist),
    }


def write_read_card_artifacts(
    *,
    stage_dir: Path,
    documents: list[dict[str, Any]],
    chunks: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compatibility wrapper for the reusable Read boundary."""
    result = read_documents(
        ReadRequest(bundle=DocumentBundle.from_rows(documents=documents, chunks=chunks))
    )
    return write_read_card_artifacts_from_result(stage_dir=stage_dir, result=result)


def write_read_card_artifacts_from_result(
    *,
    stage_dir: Path,
    result: ReadResult,
) -> dict[str, Any]:
    """Project a typed Read result into the existing JSONL card artifacts."""
    paper_cards = result.paper_cards
    claim_cards = result.claim_cards
    method_cards = result.method_cards
    dataset_cards = result.dataset_cards
    code_links = result.code_links
    write_jsonl(stage_dir / READ_PAPER_CARDS, [card.to_row() for card in paper_cards])
    write_jsonl(stage_dir / READ_CLAIM_CARDS, [card.to_row() for card in claim_cards])
    write_jsonl(stage_dir / READ_METHOD_CARDS, [card.to_row() for card in method_cards])
    write_jsonl(stage_dir / READ_DATASET_CARDS, [card.to_row() for card in dataset_cards])
    write_jsonl(stage_dir / READ_CODE_LINKS, [link.to_row() for link in code_links])
    return {
        "paper_cards": READ_PAPER_CARDS,
        "claim_cards": READ_CLAIM_CARDS,
        "method_cards": READ_METHOD_CARDS,
        "dataset_cards": READ_DATASET_CARDS,
        "code_links": READ_CODE_LINKS,
        "paper_card_count": len(paper_cards),
        "claim_card_count": len(claim_cards),
        "method_card_count": len(method_cards),
        "dataset_card_count": len(dataset_cards),
        "code_link_count": len(code_links),
        "read_status": result.status,
        "read_diagnostics": list(result.diagnostics),
    }


def write_synthesis_brief_artifact(
    *,
    stage_dir: Path,
    topic: str,
    source_plan: dict[str, Any],
    papers: list[dict[str, Any]],
    paper_notes: list[dict[str, Any]],
    coverage_report: dict[str, Any] | None,
    index_meta: dict[str, Any],
    fulltext_manifest: dict[str, Any],
    fulltext_extraction: dict[str, Any],
) -> dict[str, Any]:
    """Write the canonical compact handoff from reading to synthesis/design.

    ``paper_notes.json`` is the read-stage source of truth. The synthesis brief
    keeps only the cross-paper structure needed by the synthesize and design
    stages so default runs do not need to persist several card and evidence-pack
    tables.
    """
    brief = build_synthesis_brief(
        topic=topic,
        source_plan=source_plan,
        papers=papers,
        paper_notes=paper_notes,
        coverage_report=coverage_report,
        index_meta=index_meta,
        fulltext_manifest=fulltext_manifest,
        fulltext_extraction=fulltext_extraction,
    )
    write_json(stage_dir / SYNTHESIS_BRIEF_JSON, brief)
    return {
        "synthesis_brief": SYNTHESIS_BRIEF_JSON,
        "idea_candidate_count": len(_list(brief.get("idea_candidates"))),
        "novelty_check_count": len(_list(brief.get("novelty_checks"))),
    }


def build_synthesis_brief(
    *,
    topic: str,
    source_plan: dict[str, Any],
    papers: list[dict[str, Any]],
    paper_notes: list[dict[str, Any]],
    coverage_report: dict[str, Any] | None,
    index_meta: dict[str, Any],
    fulltext_manifest: dict[str, Any],
    fulltext_extraction: dict[str, Any],
) -> dict[str, Any]:
    """Return a compact research brief derived from enriched paper notes."""
    paper_by_id = {str(paper.get("id") or ""): paper for paper in papers}
    brief_notes = [_compact_paper_note(note, paper_by_id) for note in paper_notes if isinstance(note, dict)]
    role_counts = _count_values(note.get("evidence_role") or note.get("relevance") for note in brief_notes)
    coverage = coverage_report or {}
    ideas = _ideas_from_paper_briefs(topic, brief_notes)
    novelty = _novelty_checks_from_brief(ideas, brief_notes)
    limitations = _brief_limitations(
        paper_notes=brief_notes,
        coverage=coverage,
        fulltext_manifest=fulltext_manifest,
        fulltext_extraction=fulltext_extraction,
    )
    return {
        "schema_version": "synthesis_brief.v1",
        "topic": topic,
        "source_plan": _compact_source_plan(source_plan),
        "counts": {
            "papers": len(papers),
            "paper_briefs": len(brief_notes),
            "roles": role_counts,
            "chunks": int(index_meta.get("chunk_count", 0) or 0),
        },
        "coverage": {
            "status": coverage.get("status", "unknown"),
            "covered_facets": _string_list(coverage.get("covered_facets")),
            "missing_facets": _string_list(coverage.get("missing_facets")),
            "next_queries": _string_list(coverage.get("next_queries")),
        },
        "provenance": {
            "fulltext": {
                "enabled": bool(fulltext_manifest.get("enabled", False)),
                "selected_count": int(fulltext_manifest.get("selected_count") or 0),
                "parsed_count": int(fulltext_extraction.get("parsed_count") or 0),
                "failed_count": _status_count(fulltext_extraction, "failed"),
            },
            "index": _compact_index_meta(index_meta),
        },
        "paper_briefs": brief_notes,
        "themes": _themes_from_paper_briefs(brief_notes),
        "gaps": _gaps_from_brief(brief_notes, coverage),
        "idea_candidates": ideas,
        "novelty_checks": novelty,
        "limitations": limitations,
    }


def _optional_int(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _reading_table_markdown(shortlist: list[dict[str, Any]], coverage_report: dict[str, Any]) -> str:
    """Render a compact review table for the read stage."""
    lines = [
        "# Reading Shortlist",
        "",
        "This table is the read-stage review view of retrieved papers. It records",
        "what was kept for structured reading; it is not a novelty or quality claim.",
        "",
        "| Priority | Paper | Source | Role | Reason |",
        "|---:|---|---|---|---|",
    ]
    for row in shortlist:
        title = str(row.get("title") or row.get("paper_id") or "untitled").replace("|", "\\|")
        lines.append(
            f"| {row.get('reading_priority', '')} | {title} | "
            f"{row.get('source', '')} | {row.get('evidence_role') or row.get('facet') or 'unknown'} | "
            f"{row.get('reason', '')} |"
        )
    if not shortlist:
        lines.append("| - | No papers shortlisted | - | - | - |")
    missing_facets = coverage_report.get("missing_facets") if isinstance(coverage_report, dict) else []
    if isinstance(missing_facets, list) and missing_facets:
        lines.extend(["", "## Coverage Caveats", ""])
        lines.append("- Missing facets from retrieval coverage: " + ", ".join(str(item) for item in missing_facets))
    return "\n".join(lines).rstrip() + "\n"


def write_synthesis_evidence_artifacts(
    *,
    stage_dir: Path,
    topic: str,
    source_plan: dict[str, Any],
    papers: list[dict[str, Any]],
    documents: list[dict[str, Any]],
    sections: list[dict[str, Any]],
    chunks: list[dict[str, Any]],
    index_meta: dict[str, Any],
    paper_cards: list[dict[str, Any]],
    claim_cards: list[dict[str, Any]],
    method_cards: list[dict[str, Any]],
    dataset_cards: list[dict[str, Any]],
    code_links: list[dict[str, Any]],
    coverage_report: dict[str, Any] | None,
    fulltext_manifest: dict[str, Any],
    fulltext_extraction: dict[str, Any],
) -> dict[str, Any]:
    """Write synthesis-owned evidence pack, gaps, ideas, and novelty hints."""
    source_plan_obj = _from_row(SourcePlan, source_plan)
    paper_rows = [Paper.from_row(row) for row in papers]
    evidence_pack = build_evidence_pack(
        topic=topic,
        source_plan=source_plan_obj,
        papers=paper_rows,
        documents=[_from_row(DocumentRecord, row) for row in documents],
        sections=[_from_row(DocumentSection, row) for row in sections],
        chunks=[_from_row(TextChunk, row) for row in chunks],
        index_meta=index_meta,
        paper_cards=[_from_row(PaperCard, row) for row in paper_cards],
        claim_cards=[_from_row(ClaimCard, row) for row in claim_cards],
        method_cards=[_from_row(MethodCard, row) for row in method_cards],
        dataset_cards=[_from_row(DatasetCard, row) for row in dataset_cards],
        code_links=[_from_row(CodeLink, row) for row in code_links],
        coverage_report=coverage_report,
        fulltext_manifest=fulltext_manifest,
        fulltext_extraction=fulltext_extraction,
    )
    idea_candidates = build_idea_candidates(evidence_pack)
    novelty_checks = build_novelty_checks(
        idea_candidates,
        evidence_pack,
        backend=str(source_plan_obj.budget.get("novelty_backend") or "local"),
    )
    stored_evidence_pack = compact_evidence_pack_for_storage(evidence_pack)
    write_json(stage_dir / SYNTHESIS_EVIDENCE_PACK_JSON, stored_evidence_pack)
    write_text(stage_dir / SYNTHESIS_EVIDENCE_PACK_MD, evidence_pack_markdown(stored_evidence_pack))
    write_text(stage_dir / SYNTHESIS_GAP_SUMMARY, build_gap_summary(evidence_pack))
    write_jsonl(stage_dir / SYNTHESIS_IDEA_CANDIDATES, [idea.to_row() for idea in idea_candidates])
    write_jsonl(stage_dir / SYNTHESIS_NOVELTY_CHECKS, [check.to_row() for check in novelty_checks])
    return {
        "evidence_pack": SYNTHESIS_EVIDENCE_PACK_JSON,
        "evidence_pack_markdown": SYNTHESIS_EVIDENCE_PACK_MD,
        "gap_summary": SYNTHESIS_GAP_SUMMARY,
        "idea_candidates": SYNTHESIS_IDEA_CANDIDATES,
        "novelty_checks": SYNTHESIS_NOVELTY_CHECKS,
        "idea_candidate_count": len(idea_candidates),
        "novelty_check_count": len(novelty_checks),
    }


def write_design_handoff_artifacts(
    *,
    stage_dir: Path,
    evidence_pack: dict[str, Any],
    idea_candidates: list[dict[str, Any]],
    novelty_checks: list[dict[str, Any]],
    compact_artifacts: bool = True,
) -> dict[str, Any]:
    """Write design-owned experiment contract and optional tool handoff drafts."""
    ideas = [_from_row(IdeaCandidate, row) for row in idea_candidates]
    checks = [_from_row(type_hint=NoveltyCheck, row=row) for row in novelty_checks]
    experiment_contract = build_experiment_contract(ideas, evidence_pack)
    write_json(stage_dir / DESIGN_EXPERIMENT_CONTRACT_JSON, experiment_contract.to_row())
    write_text(stage_dir / DESIGN_EXPERIMENT_CONTRACT_MD, experiment_contract_markdown(experiment_contract))
    meta = {
        "experiment_contract": DESIGN_EXPERIMENT_CONTRACT_JSON,
        "experiment_contract_markdown": DESIGN_EXPERIMENT_CONTRACT_MD,
    }
    if not compact_artifacts:
        tool_context, tool_context_markdown = build_tool_context(
            pack=evidence_pack,
            contract=experiment_contract,
            novelty_checks=checks,
        )
        evidence_review_markdown, decision_log = build_evidence_review(
            pack=evidence_pack,
            ideas=ideas,
            novelty_checks=checks,
            contract=experiment_contract,
        )
        eval_report, eval_markdown = build_research_eval(
            pack=evidence_pack,
            ideas=ideas,
            contract=experiment_contract,
        )
        tool_adapter_contract = build_tool_adapter_contract(
            pack=evidence_pack,
            contract=experiment_contract,
            tool_context=tool_context,
        )
        retention_policy = build_artifact_retention_policy(
            compact_artifacts=compact_artifacts,
            source_plan=evidence_pack.get("source_plan", {}),
        )
        write_json(stage_dir / DESIGN_TOOL_CONTEXT_JSON, tool_context)
        write_text(stage_dir / DESIGN_TOOL_CONTEXT_MD, tool_context_markdown)
        write_text(stage_dir / DESIGN_EVIDENCE_REVIEW_MD, evidence_review_markdown)
        write_jsonl(stage_dir / DESIGN_DECISION_LOG, decision_log)
        write_json(stage_dir / DESIGN_EVAL_JSON, eval_report)
        write_text(stage_dir / DESIGN_EVAL_MD, eval_markdown)
        write_json(stage_dir / DESIGN_TOOL_ADAPTER_CONTRACT_JSON, tool_adapter_contract)
        write_text(stage_dir / DESIGN_TOOL_ADAPTER_CONTRACT_MD, tool_adapter_contract_markdown(tool_adapter_contract))
        write_jsonl(stage_dir / DESIGN_TOOL_TRACE, build_tool_trace_rows(tool_adapter_contract))
        write_text(
            stage_dir / DESIGN_EXTERNAL_AGENT_BACKEND,
            external_agent_backend_markdown(
                contract=experiment_contract,
                tool_context=tool_context,
                adapter_contract=tool_adapter_contract,
            ),
        )
        write_json(stage_dir / DESIGN_RETENTION_POLICY_JSON, retention_policy)
        write_text(stage_dir / DESIGN_RETENTION_POLICY_MD, artifact_retention_policy_markdown(retention_policy))
        meta.update(
            {
                "tool_context": DESIGN_TOOL_CONTEXT_JSON,
                "tool_context_markdown": DESIGN_TOOL_CONTEXT_MD,
                "evidence_review": DESIGN_EVIDENCE_REVIEW_MD,
                "decision_log": DESIGN_DECISION_LOG,
                "research_eval": DESIGN_EVAL_JSON,
                "research_eval_markdown": DESIGN_EVAL_MD,
                "tool_adapter_contract": DESIGN_TOOL_ADAPTER_CONTRACT_JSON,
                "tool_adapter_contract_markdown": DESIGN_TOOL_ADAPTER_CONTRACT_MD,
                "tool_trace": DESIGN_TOOL_TRACE,
                "external_agent_backend": DESIGN_EXTERNAL_AGENT_BACKEND,
                "artifact_retention_policy": DESIGN_RETENTION_POLICY_JSON,
                "artifact_retention_policy_markdown": DESIGN_RETENTION_POLICY_MD,
            }
        )
    return meta


def _compact_paper_note(note: dict[str, Any], paper_by_id: dict[str, dict[str, Any]]) -> dict[str, Any]:
    paper_id = str(note.get("paper_id") or note.get("id") or "")
    paper = paper_by_id.get(paper_id, {})
    evidence_role = _first_text(
        note.get("evidence_role"),
        note.get("role"),
        note.get("facet"),
        "other",
    )
    return {
        "paper_id": paper_id,
        "title": _first_text(note.get("title"), paper.get("title"), "untitled"),
        "source": _first_text(paper.get("source"), note.get("source"), "unknown"),
        "evidence_role": evidence_role,
        "one_sentence_summary": _first_text(
            note.get("one_sentence_summary"),
            note.get("summary"),
            note.get("relevance"),
            "",
        ),
        "problem": _first_text(note.get("problem"), ""),
        "method": _first_text(note.get("method"), note.get("method_summary"), ""),
        "datasets": _string_list(note.get("datasets")),
        "metrics": _string_list(note.get("metrics")),
        "key_claims": _string_list(note.get("key_claims")) or _string_list(note.get("main_claims")),
        "limitations": _string_list(note.get("limitations")) or _string_list(note.get("limitation")),
        "relation_to_topic": _first_text(note.get("relation_to_topic"), note.get("relevance"), ""),
        "synthesis_hint": _first_text(note.get("synthesis_hint"), ""),
        "possible_experiment_hooks": _string_list(note.get("possible_experiment_hooks")),
        "open_questions": _string_list(note.get("open_questions")),
        "evidence_refs": _string_list(note.get("evidence_refs")),
        "confidence": _first_text(note.get("confidence"), "unknown"),
    }


def _ideas_from_paper_briefs(topic: str, notes: list[dict[str, Any]], *, limit: int = 3) -> list[dict[str, Any]]:
    ideas: list[dict[str, Any]] = []
    hook_notes = [
        note for note in notes
        if _string_list(note.get("possible_experiment_hooks")) or str(note.get("synthesis_hint") or "").strip()
    ]
    for index, note in enumerate(hook_notes[:limit], start=1):
        hooks = _string_list(note.get("possible_experiment_hooks"))
        proposed_change = hooks[0] if hooks else _first_text(note.get("synthesis_hint"), "Design a bounded ablation from the paper brief.")
        metrics = _string_list(note.get("metrics"))
        datasets = _string_list(note.get("datasets"))
        ideas.append(
            IdeaCandidate(
                idea_id=f"idea-{index:03d}",
                title=f"Bounded experiment from {note.get('title') or topic}",
                hypothesis=_first_text(
                    note.get("synthesis_hint"),
                    note.get("one_sentence_summary"),
                    f"A small controlled experiment can clarify an evidence gap around {topic}.",
                ),
                motivation_refs=[str(note.get("paper_id") or f"paper-{index}")],
                proposed_change=proposed_change,
                expected_outcome=f"Measure {metrics[0]} against a baseline." if metrics else "Produce a measurable baseline comparison.",
                required_datasets=datasets[:5],
                metrics=metrics[:6],
                feasibility="medium" if proposed_change else "low",
                risks=_string_list(note.get("limitations"))[:5] or ["The paper brief may be too thin for strong claims."],
            ).to_row()
        )
    if not ideas:
        ideas.append(
            IdeaCandidate(
                idea_id="idea-001",
                title=f"Clarify a literature-backed baseline for {topic}",
                hypothesis="A bounded baseline should be established before stronger implementation claims are attempted.",
                proposed_change="Summarize available methods and defer code changes until the experiment target is clear.",
                expected_outcome="A clearer baseline task definition and evidence checklist.",
                feasibility="low",
                risks=["No paper brief exposed a concrete experiment hook."],
            ).to_row()
        )
    return ideas


def _novelty_checks_from_brief(ideas: list[dict[str, Any]], notes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    known_text = [
        (str(note.get("paper_id") or "paper"), " ".join(_string_list(note.get("key_claims")) + [_first_text(note.get("title"), "")]))
        for note in notes
    ]
    checks: list[dict[str, Any]] = []
    for idea in ideas:
        idea_obj = _from_row(IdeaCandidate, idea)
        terms = _terms(" ".join([idea_obj.title, idea_obj.hypothesis, idea_obj.proposed_change]))
        similar_refs = _similar_refs(terms, known_text)
        risk_level = "high" if len(similar_refs) >= 3 else "medium" if similar_refs else "unknown"
        checks.append(
            NoveltyCheck(
                idea_id=idea_obj.idea_id,
                status="local_risk_hint",
                similar_work_refs=similar_refs,
                risk_level=risk_level,
                rationale=(
                    "Local brief overlap suggests possible prior work; this is not a definitive novelty judgment."
                    if similar_refs
                    else "No close local overlap was found, but the evidence set may be incomplete."
                ),
            ).to_row()
        )
    return checks


def _themes_from_paper_briefs(notes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for note in notes:
        role = _first_text(note.get("evidence_role"), "other")
        grouped.setdefault(role, []).append(note)
    themes: list[dict[str, Any]] = []
    for role, rows in sorted(grouped.items()):
        themes.append(
            {
                "role": role,
                "paper_ids": [str(row.get("paper_id") or "") for row in rows if row.get("paper_id")],
                "summary": _first_text(
                    *(row.get("synthesis_hint") for row in rows),
                    *(row.get("one_sentence_summary") for row in rows),
                    f"{len(rows)} paper(s) associated with {role}.",
                ),
            }
        )
    return themes


def _gaps_from_brief(notes: list[dict[str, Any]], coverage: dict[str, Any]) -> list[str]:
    gaps = [
        f"Missing facet `{facet}` should be resolved before strong claims."
        for facet in _string_list(coverage.get("missing_facets"))
    ]
    open_questions: list[str] = []
    for note in notes:
        open_questions.extend(_string_list(note.get("open_questions")))
    gaps.extend(open_questions[:5])
    if not notes:
        gaps.append("No paper briefs were available for synthesis.")
    return gaps or ["No major brief-level gap was detected, but human review is still required."]


def _brief_limitations(
    *,
    paper_notes: list[dict[str, Any]],
    coverage: dict[str, Any],
    fulltext_manifest: dict[str, Any],
    fulltext_extraction: dict[str, Any],
) -> list[str]:
    limitations: list[str] = []
    if not paper_notes:
        limitations.append("No shortlisted paper briefs were available.")
    if str(coverage.get("status") or "unknown") not in {"covered", "partially_covered"}:
        limitations.append("Retrieval coverage is not sufficient for strong research claims.")
    if not fulltext_manifest.get("enabled"):
        limitations.append("Full-text retrieval was disabled; briefs may be abstract-only.")
    elif _briefs_report_fulltext_gap(paper_notes):
        limitations.append("At least one shortlisted paper lacks parsed full text or abstract evidence; synthesis should remain conservative.")
    elif int(fulltext_extraction.get("parsed_count") or 0) == 0:
        limitations.append("No full-text documents were parsed for the selected papers.")
    return limitations


def _briefs_report_fulltext_gap(notes: list[dict[str, Any]]) -> bool:
    for note in notes:
        text = " ".join(
            str(value)
            for value in (
                note.get("limitation"),
                note.get("limitations"),
                note.get("confidence"),
                note.get("synthesis_hint"),
            )
        ).lower()
        if "full text unavailable" in text or "abstract and full text unavailable" in text:
            return True
    return False


def _status_count(report: dict[str, Any], status: str) -> int:
    rows = report.get("records")
    if not isinstance(rows, list):
        rows = report.get("documents")
    if not isinstance(rows, list):
        return 0
    return sum(1 for row in rows if isinstance(row, dict) and row.get("status") == status)


def _compact_index_meta(index_meta: dict[str, Any]) -> dict[str, Any]:
    return {
        "backend": index_meta.get("backend"),
        "store": index_meta.get("store"),
        "chunk_count": index_meta.get("chunk_count"),
        "sqlite_fts": _status_only(index_meta.get("sqlite_fts")),
        "lancedb": _status_only(index_meta.get("lancedb")),
    }


def _status_only(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {
        key: value.get(key)
        for key in ("status", "path", "table", "error")
        if key in value
    }


def _compact_source_plan(source_plan: dict[str, Any]) -> dict[str, Any]:
    return {
        "sources": _string_list(source_plan.get("sources")),
        "queries": _string_list(source_plan.get("queries")),
        "mode": _first_text(source_plan.get("mode"), "standard"),
        "require_fulltext": bool(source_plan.get("require_fulltext", False)),
        "allow_pdf_download": bool(source_plan.get("allow_pdf_download", False)),
        "index_backend": _first_text(source_plan.get("index_backend"), "keyword"),
        "budget": dict(source_plan.get("budget")) if isinstance(source_plan.get("budget"), dict) else {},
    }


def _count_values(values: object) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        key = _first_text(value, "unknown")
        counts[key] = counts.get(key, 0) + 1
    return counts


def _similar_refs(terms: set[str], known_text: list[tuple[str, str]], *, limit: int = 5) -> list[str]:
    scored: list[tuple[int, str]] = []
    for ref, text in known_text:
        overlap = len(terms & _terms(text))
        if overlap:
            scored.append((overlap, ref))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [ref for _, ref in scored[:limit]]


def _terms(text: str) -> set[str]:
    import re

    return {term for term in re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}", text.lower()) if len(term) > 2}


def _first_text(*values: object) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _list(value: object) -> list[Any]:
    return value if isinstance(value, list) else []


def _string_list(value: object) -> list[str]:
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _chunk_cap(source_plan: SourcePlan) -> int | None:
    value = source_plan.budget.get("max_chunks")
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return None


def _from_row(type_hint: type[Any], row: dict[str, Any]) -> Any:
    """Instantiate a dataclass from a JSON row while ignoring unknown keys."""
    allowed = {field.name for field in fields(type_hint)}
    return type_hint(**{key: value for key, value in row.items() if key in allowed})
