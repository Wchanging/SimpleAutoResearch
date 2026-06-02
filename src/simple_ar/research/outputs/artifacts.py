from __future__ import annotations

from pathlib import Path
from typing import Any

from simple_ar.core.artifacts import read_json, write_json, write_jsonl, write_text
from simple_ar.literature.models import Paper
from simple_ar.research.contracts import QueryPlan, ResearchQuestion, SourcePlan
from simple_ar.research.evidence.cards import (
    build_code_links,
    build_dataset_cards,
    build_evidence_cards,
    build_method_cards,
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
from simple_ar.research.store.chunking import build_text_chunks
from simple_ar.research.documents.records import build_cache_manifest, build_document_records
from simple_ar.research.documents.extractors import apply_fulltext_extraction
from simple_ar.research.documents.fulltext import build_fulltext_manifest
from simple_ar.research.documents.sections import build_document_sections
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
SEARCH_SCREENING_DECISIONS = "traces/screening_decisions.jsonl"
SEARCH_COVERAGE_JSON = "review/coverage_report.json"
SEARCH_COVERAGE_MD = "review/coverage_report.md"
SEARCH_DOCUMENTS = "documents/documents.jsonl"
SEARCH_CACHE_MANIFEST = "documents/cache_manifest.json"
SEARCH_FULLTEXT_MANIFEST = "documents/fulltext_manifest.json"
SEARCH_FULLTEXT_EXTRACTION = "documents/fulltext_extraction.json"
SEARCH_SECTIONS = "documents/sections.jsonl"
SEARCH_CHUNKS = "research_index/chunks.jsonl"
SEARCH_INDEX_META = "research_index/index_meta.json"
SEARCH_PAPER_CARDS = "cards/paper_cards.jsonl"
SEARCH_CLAIM_CARDS = "cards/claim_cards.jsonl"
SEARCH_METHOD_CARDS = "cards/method_cards.jsonl"
SEARCH_DATASET_CARDS = "cards/dataset_cards.jsonl"
SEARCH_CODE_LINKS = "cards/code_links.jsonl"
SEARCH_EVIDENCE_PACK_JSON = "evidence/evidence_pack.json"
SEARCH_EVIDENCE_PACK_MD = "evidence/evidence_pack.md"
SEARCH_GAP_SUMMARY = "evidence/gap_summary.md"
SEARCH_IDEA_CANDIDATES = "evidence/idea_candidates.jsonl"
SEARCH_NOVELTY_CHECKS = "evidence/novelty_checks.jsonl"
SEARCH_EXPERIMENT_CONTRACT_JSON = "evidence/experiment_contract.json"
SEARCH_EXPERIMENT_CONTRACT_MD = "evidence/experiment_contract.md"
SEARCH_TOOL_CONTEXT_JSON = "evidence/tool_context.json"
SEARCH_TOOL_CONTEXT_MD = "evidence/tool_context.md"
SEARCH_EVIDENCE_REVIEW_MD = "evidence/evidence_review.md"
SEARCH_DECISION_LOG = "evidence/decision_log.jsonl"
SEARCH_EVAL_JSON = "evidence/eval_report.json"
SEARCH_EVAL_MD = "evidence/eval_report.md"
SEARCH_TOOL_ADAPTER_CONTRACT_JSON = "tools/tool_adapter_contract.json"
SEARCH_TOOL_ADAPTER_CONTRACT_MD = "tools/tool_adapter_contract.md"
SEARCH_TOOL_TRACE = "tools/tool_trace.jsonl"
SEARCH_EXTERNAL_AGENT_BACKEND = "tools/external_agent_backend.md"
SEARCH_RETENTION_POLICY_JSON = "governance/artifact_retention_policy.json"
SEARCH_RETENTION_POLICY_MD = "governance/artifact_retention_policy.md"
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
    """Write document-store and local-index artifacts for the search stage.

    Args:
        stage_dir: Current ``02-search`` directory.
        topic: Original run topic used for downstream evidence handoffs.
        papers: Selected paper metadata after screening.
        source_plan: Search source plan controlling local documents, chunk cap,
            and index backend.

    Returns:
        Metadata fields to merge into ``search_meta.json``.
    """
    compact_artifacts = bool(source_plan.budget.get("compact_artifacts", False))
    documents = build_document_records(papers=papers, source_plan=source_plan)
    fulltext_manifest = build_fulltext_manifest(
        records=documents,
        source_plan=source_plan,
        cache_dir=stage_dir / "documents" / "fulltext_cache",
    )
    documents, fulltext_extraction = apply_fulltext_extraction(
        records=documents,
        fulltext_manifest=fulltext_manifest,
        source_plan=source_plan,
        extraction_dir=stage_dir / "documents" / "extracted_text",
    )
    sections = build_document_sections(documents)
    chunks = build_text_chunks(documents, sections=sections, max_chunks=_chunk_cap(source_plan))
    index_meta = write_research_index(
        index_dir=stage_dir / Path(SEARCH_INDEX_META).parent,
        chunks=chunks,
        backend=source_plan.index_backend,
        run_id=stage_dir.parent.name,
        shared_root=source_plan.index_root,
    )
    paper_cards, claim_cards = build_evidence_cards(documents=documents, chunks=chunks)
    method_cards = build_method_cards(documents=documents, chunks=chunks)
    dataset_cards = build_dataset_cards(documents=documents, chunks=chunks)
    code_links = build_code_links(documents=documents, chunks=chunks)
    coverage_report = _read_optional_json(stage_dir / SEARCH_COVERAGE_JSON)
    evidence_pack = build_evidence_pack(
        topic=topic,
        source_plan=source_plan,
        papers=papers,
        documents=documents,
        sections=sections,
        chunks=chunks,
        index_meta=index_meta,
        paper_cards=paper_cards,
        claim_cards=claim_cards,
        method_cards=method_cards,
        dataset_cards=dataset_cards,
        code_links=code_links,
        coverage_report=coverage_report,
        fulltext_manifest=fulltext_manifest,
        fulltext_extraction=fulltext_extraction,
    )
    idea_candidates = build_idea_candidates(evidence_pack)
    novelty_checks = build_novelty_checks(
        idea_candidates,
        evidence_pack,
        backend=str(source_plan.budget.get("novelty_backend") or "local"),
    )
    experiment_contract = build_experiment_contract(idea_candidates, evidence_pack)
    stored_evidence_pack = compact_evidence_pack_for_storage(evidence_pack)
    write_jsonl(stage_dir / SEARCH_DOCUMENTS, [record.to_row() for record in documents])
    write_json(stage_dir / SEARCH_CACHE_MANIFEST, build_cache_manifest(records=documents, source_plan=source_plan))
    write_json(stage_dir / SEARCH_FULLTEXT_MANIFEST, fulltext_manifest)
    write_json(stage_dir / SEARCH_FULLTEXT_EXTRACTION, fulltext_extraction)
    if not compact_artifacts:
        write_jsonl(stage_dir / SEARCH_SECTIONS, [section.to_row() for section in sections])
    write_jsonl(stage_dir / SEARCH_PAPER_CARDS, [card.to_row() for card in paper_cards])
    write_jsonl(stage_dir / SEARCH_CLAIM_CARDS, [card.to_row() for card in claim_cards])
    write_jsonl(stage_dir / SEARCH_METHOD_CARDS, [card.to_row() for card in method_cards])
    write_jsonl(stage_dir / SEARCH_DATASET_CARDS, [card.to_row() for card in dataset_cards])
    write_jsonl(stage_dir / SEARCH_CODE_LINKS, [link.to_row() for link in code_links])
    write_json(stage_dir / SEARCH_EVIDENCE_PACK_JSON, stored_evidence_pack)
    write_text(stage_dir / SEARCH_EVIDENCE_PACK_MD, evidence_pack_markdown(stored_evidence_pack))
    write_text(stage_dir / SEARCH_GAP_SUMMARY, build_gap_summary(evidence_pack))
    write_jsonl(stage_dir / SEARCH_IDEA_CANDIDATES, [idea.to_row() for idea in idea_candidates])
    write_jsonl(stage_dir / SEARCH_NOVELTY_CHECKS, [check.to_row() for check in novelty_checks])
    write_json(stage_dir / SEARCH_EXPERIMENT_CONTRACT_JSON, experiment_contract.to_row())
    write_text(stage_dir / SEARCH_EXPERIMENT_CONTRACT_MD, experiment_contract_markdown(experiment_contract))
    if not compact_artifacts:
        tool_context, tool_context_markdown = build_tool_context(
            pack=evidence_pack,
            contract=experiment_contract,
            novelty_checks=novelty_checks,
        )
        evidence_review_markdown, decision_log = build_evidence_review(
            pack=evidence_pack,
            ideas=idea_candidates,
            novelty_checks=novelty_checks,
            contract=experiment_contract,
        )
        eval_report, eval_markdown = build_research_eval(
            pack=evidence_pack,
            ideas=idea_candidates,
            contract=experiment_contract,
        )
        tool_adapter_contract = build_tool_adapter_contract(
            pack=evidence_pack,
            contract=experiment_contract,
            tool_context=tool_context,
        )
        retention_policy = build_artifact_retention_policy(
            compact_artifacts=compact_artifacts,
            source_plan=source_plan.to_row(),
        )
        write_json(stage_dir / SEARCH_TOOL_CONTEXT_JSON, tool_context)
        write_text(stage_dir / SEARCH_TOOL_CONTEXT_MD, tool_context_markdown)
        write_text(stage_dir / SEARCH_EVIDENCE_REVIEW_MD, evidence_review_markdown)
        write_jsonl(stage_dir / SEARCH_DECISION_LOG, decision_log)
        write_json(stage_dir / SEARCH_EVAL_JSON, eval_report)
        write_text(stage_dir / SEARCH_EVAL_MD, eval_markdown)
        write_json(stage_dir / SEARCH_TOOL_ADAPTER_CONTRACT_JSON, tool_adapter_contract)
        write_text(stage_dir / SEARCH_TOOL_ADAPTER_CONTRACT_MD, tool_adapter_contract_markdown(tool_adapter_contract))
        write_jsonl(stage_dir / SEARCH_TOOL_TRACE, build_tool_trace_rows(tool_adapter_contract))
        write_text(
            stage_dir / SEARCH_EXTERNAL_AGENT_BACKEND,
            external_agent_backend_markdown(
                contract=experiment_contract,
                tool_context=tool_context,
                adapter_contract=tool_adapter_contract,
            ),
        )
        write_json(stage_dir / SEARCH_RETENTION_POLICY_JSON, retention_policy)
        write_text(stage_dir / SEARCH_RETENTION_POLICY_MD, artifact_retention_policy_markdown(retention_policy))
    meta = {
        "documents": SEARCH_DOCUMENTS,
        "cache_manifest": SEARCH_CACHE_MANIFEST,
        "fulltext_manifest": SEARCH_FULLTEXT_MANIFEST,
        "fulltext_extraction": SEARCH_FULLTEXT_EXTRACTION,
        "chunks": SEARCH_CHUNKS,
        "index_meta": SEARCH_INDEX_META,
        "paper_cards": SEARCH_PAPER_CARDS,
        "claim_cards": SEARCH_CLAIM_CARDS,
        "method_cards": SEARCH_METHOD_CARDS,
        "dataset_cards": SEARCH_DATASET_CARDS,
        "code_links": SEARCH_CODE_LINKS,
        "evidence_pack": SEARCH_EVIDENCE_PACK_JSON,
        "evidence_pack_markdown": SEARCH_EVIDENCE_PACK_MD,
        "gap_summary": SEARCH_GAP_SUMMARY,
        "idea_candidates": SEARCH_IDEA_CANDIDATES,
        "novelty_checks": SEARCH_NOVELTY_CHECKS,
        "experiment_contract": SEARCH_EXPERIMENT_CONTRACT_JSON,
        "experiment_contract_markdown": SEARCH_EXPERIMENT_CONTRACT_MD,
        "document_count": len(documents),
        "section_count": len(sections),
        "chunk_count": len(chunks),
        "paper_card_count": len(paper_cards),
        "claim_card_count": len(claim_cards),
        "method_card_count": len(method_cards),
        "dataset_card_count": len(dataset_cards),
        "code_link_count": len(code_links),
        "idea_candidate_count": len(idea_candidates),
        "novelty_check_count": len(novelty_checks),
        "index_backend": index_meta.get("backend"),
    }
    if not compact_artifacts:
        meta.update(
            {
                "sections": SEARCH_SECTIONS,
                "tool_context": SEARCH_TOOL_CONTEXT_JSON,
                "tool_context_markdown": SEARCH_TOOL_CONTEXT_MD,
                "evidence_review": SEARCH_EVIDENCE_REVIEW_MD,
                "decision_log": SEARCH_DECISION_LOG,
                "research_eval": SEARCH_EVAL_JSON,
                "research_eval_markdown": SEARCH_EVAL_MD,
                "tool_adapter_contract": SEARCH_TOOL_ADAPTER_CONTRACT_JSON,
                "tool_adapter_contract_markdown": SEARCH_TOOL_ADAPTER_CONTRACT_MD,
                "tool_trace": SEARCH_TOOL_TRACE,
                "external_agent_backend": SEARCH_EXTERNAL_AGENT_BACKEND,
                "artifact_retention_policy": SEARCH_RETENTION_POLICY_JSON,
                "artifact_retention_policy_markdown": SEARCH_RETENTION_POLICY_MD,
            }
        )
    return meta


def _chunk_cap(source_plan: SourcePlan) -> int | None:
    value = source_plan.budget.get("max_chunks")
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return None


def _read_optional_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    data = read_json(path)
    return data if isinstance(data, dict) else None
