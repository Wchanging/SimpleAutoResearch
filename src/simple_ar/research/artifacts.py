from __future__ import annotations

from pathlib import Path
from typing import Any

from simple_ar.artifacts import write_json, write_jsonl
from simple_ar.literature.models import Paper
from simple_ar.research.contracts import QueryPlan, ResearchQuestion, SourcePlan
from simple_ar.research.cards import build_evidence_cards
from simple_ar.research.chunking import build_text_chunks
from simple_ar.research.documents import build_cache_manifest, build_document_records
from simple_ar.research.fulltext import build_fulltext_manifest
from simple_ar.research.index import write_research_index


SEARCH_RESEARCH_PLAN = "planning/research_plan.json"
SEARCH_RETRIEVAL_ROUNDS = "traces/retrieval_rounds.jsonl"
SEARCH_SCREENING_DECISIONS = "traces/screening_decisions.jsonl"
SEARCH_COVERAGE_JSON = "review/coverage_report.json"
SEARCH_COVERAGE_MD = "review/coverage_report.md"
SEARCH_DOCUMENTS = "documents/documents.jsonl"
SEARCH_CACHE_MANIFEST = "documents/cache_manifest.json"
SEARCH_FULLTEXT_MANIFEST = "documents/fulltext_manifest.json"
SEARCH_CHUNKS = "research_index/chunks.jsonl"
SEARCH_INDEX_META = "research_index/index_meta.json"
SEARCH_PAPER_CARDS = "cards/paper_cards.jsonl"
SEARCH_CLAIM_CARDS = "cards/claim_cards.jsonl"
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
    papers: list[Paper],
    source_plan: SourcePlan,
) -> dict[str, Any]:
    """Write document-store and local-index artifacts for the search stage.

    Args:
        stage_dir: Current ``02-search`` directory.
        papers: Selected paper metadata after screening.
        source_plan: Search source plan controlling local documents, chunk cap,
            and index backend.

    Returns:
        Metadata fields to merge into ``search_meta.json``.
    """
    documents = build_document_records(papers=papers, source_plan=source_plan)
    chunks = build_text_chunks(documents, max_chunks=_chunk_cap(source_plan))
    index_meta = write_research_index(
        index_dir=stage_dir / Path(SEARCH_INDEX_META).parent,
        chunks=chunks,
        backend=source_plan.index_backend,
    )
    paper_cards, claim_cards = build_evidence_cards(documents=documents, chunks=chunks)
    write_jsonl(stage_dir / SEARCH_DOCUMENTS, [record.to_row() for record in documents])
    write_json(stage_dir / SEARCH_CACHE_MANIFEST, build_cache_manifest(records=documents, source_plan=source_plan))
    write_json(stage_dir / SEARCH_FULLTEXT_MANIFEST, build_fulltext_manifest(records=documents, source_plan=source_plan))
    write_jsonl(stage_dir / SEARCH_PAPER_CARDS, [card.to_row() for card in paper_cards])
    write_jsonl(stage_dir / SEARCH_CLAIM_CARDS, [card.to_row() for card in claim_cards])
    return {
        "documents": SEARCH_DOCUMENTS,
        "cache_manifest": SEARCH_CACHE_MANIFEST,
        "fulltext_manifest": SEARCH_FULLTEXT_MANIFEST,
        "chunks": SEARCH_CHUNKS,
        "index_meta": SEARCH_INDEX_META,
        "paper_cards": SEARCH_PAPER_CARDS,
        "claim_cards": SEARCH_CLAIM_CARDS,
        "document_count": len(documents),
        "chunk_count": len(chunks),
        "paper_card_count": len(paper_cards),
        "claim_card_count": len(claim_cards),
        "index_backend": index_meta.get("backend"),
    }


def _chunk_cap(source_plan: SourcePlan) -> int | None:
    value = source_plan.budget.get("max_chunks")
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return None
