from __future__ import annotations

from collections import Counter
from typing import Any

from simple_ar.literature.models import Paper
from simple_ar.research.contracts import (
    ClaimCard,
    CodeLink,
    DatasetCard,
    DocumentRecord,
    DocumentSection,
    MethodCard,
    PaperCard,
    SourcePlan,
    TextChunk,
)


def build_evidence_pack(
    *,
    topic: str,
    source_plan: SourcePlan,
    papers: list[Paper],
    documents: list[DocumentRecord],
    sections: list[DocumentSection],
    chunks: list[TextChunk],
    index_meta: dict[str, Any],
    paper_cards: list[PaperCard],
    claim_cards: list[ClaimCard],
    method_cards: list[MethodCard],
    dataset_cards: list[DatasetCard],
    code_links: list[CodeLink],
    coverage_report: dict[str, Any] | None,
    fulltext_manifest: dict[str, Any],
    fulltext_extraction: dict[str, Any],
) -> dict[str, Any]:
    """Build a compact, provenance-first evidence package.

    The pack intentionally avoids embedding raw full text. It keeps counts,
    identifiers, cards, coverage status, and parser/index provenance so later
    stages can reason over evidence without repeatedly loading every artifact.
    """
    status_counts = Counter(document.extraction_status for document in documents)
    section_counts = Counter(section.section for section in sections)
    source_counts = Counter(document.source for document in documents)
    coverage = coverage_report or {}
    return {
        "schema_version": "evidence_pack.v1",
        "topic": topic,
        "source_plan": {
            "sources": list(source_plan.sources),
            "queries": list(source_plan.queries),
            "mode": source_plan.mode,
            "require_fulltext": source_plan.require_fulltext,
            "allow_pdf_download": source_plan.allow_pdf_download,
            "index_backend": source_plan.index_backend,
            "budget": dict(source_plan.budget),
        },
        "counts": {
            "papers": len(papers),
            "documents": len(documents),
            "sections": len(sections),
            "chunks": len(chunks),
            "paper_cards": len(paper_cards),
            "claim_cards": len(claim_cards),
            "method_cards": len(method_cards),
            "dataset_cards": len(dataset_cards),
            "code_links": len(code_links),
        },
        "coverage": {
            "status": coverage.get("status", "unknown"),
            "covered_facets": _string_list(coverage.get("covered_facets")),
            "missing_facets": _string_list(coverage.get("missing_facets")),
            "next_queries": _string_list(coverage.get("next_queries")),
        },
        "provenance": {
            "document_sources": dict(source_counts),
            "extraction_status": dict(status_counts),
            "section_types": dict(section_counts),
            "fulltext": {
                "enabled": fulltext_manifest.get("enabled", False),
                "selected_count": fulltext_manifest.get("selected_count", 0),
                "parsed_count": fulltext_extraction.get("parsed_count", 0),
                "failed_count": _status_count(fulltext_extraction, "failed"),
            },
            "index": _compact_index_meta(index_meta),
        },
        "papers": [_compact_paper(paper) for paper in papers],
        "paper_cards": [card.to_row() for card in paper_cards],
        "claim_cards": [card.to_row() for card in claim_cards],
        "method_cards": [card.to_row() for card in method_cards],
        "dataset_cards": [card.to_row() for card in dataset_cards],
        "code_links": [link.to_row() for link in code_links],
        "limitations": _pack_limitations(
            documents=documents,
            coverage_status=str(coverage.get("status", "unknown")),
            fulltext_manifest=fulltext_manifest,
        ),
    }


def evidence_pack_markdown(pack: dict[str, Any]) -> str:
    """Render a short human-readable evidence package summary."""
    counts = _dict(pack.get("counts"))
    coverage = _dict(pack.get("coverage"))
    provenance = _dict(pack.get("provenance"))
    fulltext = _dict(provenance.get("fulltext"))
    lines = [
        "# Evidence Pack",
        "",
        f"Topic: {pack.get('topic', 'unknown')}",
        "",
        "## Counts",
        "",
        f"- Papers: {counts.get('papers', 0)}",
        f"- Documents: {counts.get('documents', 0)}",
        f"- Sections: {counts.get('sections', 0)}",
        f"- Chunks: {counts.get('chunks', 0)}",
        f"- Paper cards: {counts.get('paper_cards', 0)}",
        f"- Claim cards: {counts.get('claim_cards', 0)}",
        f"- Method cards: {counts.get('method_cards', 0)}",
        f"- Dataset cards: {counts.get('dataset_cards', 0)}",
        f"- Code links: {counts.get('code_links', 0)}",
        "",
        "## Coverage",
        "",
        f"- Status: {coverage.get('status', 'unknown')}",
        f"- Covered facets: {_join_or_none(coverage.get('covered_facets'))}",
        f"- Missing facets: {_join_or_none(coverage.get('missing_facets'))}",
        "",
        "## Full Text",
        "",
        f"- Enabled: {fulltext.get('enabled', False)}",
        f"- Selected documents: {fulltext.get('selected_count', 0)}",
        f"- Parsed documents: {fulltext.get('parsed_count', 0)}",
        f"- Failed documents: {fulltext.get('failed_count', 0)}",
        "",
        "## Top Papers",
        "",
    ]
    for paper in _list(pack.get("papers"))[:8]:
        lines.append(f"- {paper.get('title', 'unknown')} ({paper.get('source', 'unknown')})")
    limitations = _string_list(pack.get("limitations"))
    if limitations:
        lines.extend(["", "## Limitations", ""])
        lines.extend(f"- {item}" for item in limitations)
    return "\n".join(lines).rstrip() + "\n"


def compact_evidence_pack_for_storage(pack: dict[str, Any]) -> dict[str, Any]:
    """Return the persisted evidence-pack shape without duplicating card tables.

    The in-memory pack keeps card rows because idea generation and experiment
    contracts need them immediately. The stored pack should be a stable handoff
    index, not a second copy of ``cards/*.jsonl``.
    """
    compact = dict(pack)
    card_refs = {
        "paper_cards": _row_ids(_list(pack.get("paper_cards")), "paper_id"),
        "claim_cards": _row_ids(_list(pack.get("claim_cards")), "claim_id"),
        "method_cards": _row_ids(_list(pack.get("method_cards")), "method_id"),
        "dataset_cards": _row_ids(_list(pack.get("dataset_cards")), "dataset_id"),
        "code_links": _row_ids(_list(pack.get("code_links")), "link_id"),
    }
    for key in card_refs:
        compact.pop(key, None)
    compact["storage_profile"] = "compact"
    compact["artifact_refs"] = {
        "paper_cards": "02-search/cards/paper_cards.jsonl",
        "claim_cards": "02-search/cards/claim_cards.jsonl",
        "method_cards": "02-search/cards/method_cards.jsonl",
        "dataset_cards": "02-search/cards/dataset_cards.jsonl",
        "code_links": "02-search/cards/code_links.jsonl",
        "documents": "02-search/documents/documents.jsonl",
        "chunks": "02-search/research_index/chunks.jsonl",
    }
    compact["card_refs"] = card_refs
    return compact


def _pack_limitations(
    *,
    documents: list[DocumentRecord],
    coverage_status: str,
    fulltext_manifest: dict[str, Any],
) -> list[str]:
    rows: list[str] = []
    if coverage_status not in {"covered", "partially_covered"}:
        rows.append("Coverage is not sufficient for strong research claims.")
    if not documents:
        rows.append("No document records were available.")
    if not fulltext_manifest.get("enabled"):
        rows.append("Full-text retrieval was disabled; evidence may be abstract-only.")
    elif int(fulltext_manifest.get("selected_count") or 0) == 0:
        rows.append("No full-text documents were selected or downloaded.")
    if any(document.extraction_status != "parsed" for document in documents):
        rows.append("Some documents remain metadata-only or failed parsing.")
    return rows


def _compact_paper(paper: Paper) -> dict[str, Any]:
    row = paper.to_row()
    return {
        "id": row.get("id"),
        "title": row.get("title"),
        "source": row.get("source"),
        "url": row.get("url"),
        "published": row.get("published"),
        "fulltext_url": row.get("fulltext_url"),
    }


def _compact_index_meta(index_meta: dict[str, Any]) -> dict[str, Any]:
    return {
        "backend": index_meta.get("backend"),
        "store": index_meta.get("store"),
        "chunk_count": index_meta.get("chunk_count"),
        "sqlite_fts": _status_only(index_meta.get("sqlite_fts")),
        "lancedb": _status_only(index_meta.get("lancedb")),
    }


def _status_only(value: object) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    keep = {key: value.get(key) for key in ("status", "scope", "path", "root") if key in value}
    return keep or None


def _status_count(manifest: dict[str, Any], status: str) -> int:
    counts = manifest.get("status_counts")
    if isinstance(counts, dict):
        value = counts.get(status)
        if isinstance(value, int):
            return value
    return 0


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def _dict(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _list(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _row_ids(rows: list[dict[str, Any]], key: str) -> list[str]:
    return [str(row.get(key, "")) for row in rows if str(row.get(key, "")).strip()]


def _join_or_none(value: object) -> str:
    rows = _string_list(value)
    return ", ".join(rows) if rows else "none"
