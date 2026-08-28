"""Reusable document-to-evidence reading boundary.

The reader owns deterministic card derivation from an already ingested
``DocumentBundle``. LLM screening and paper-note prompts remain stage policy;
this module is intentionally usable without Search or a pipeline Context.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from simple_ar.research.contracts import (
    ClaimCard,
    CodeLink,
    DatasetCard,
    DocumentRecord,
    MethodCard,
    PaperCard,
)
from simple_ar.research.documents.ingest import DocumentBundle
from simple_ar.research.evidence.cards import (
    build_code_links,
    build_dataset_cards,
    build_evidence_cards,
    build_method_cards,
)


ReadStatus = Literal["completed", "partial", "empty"]


@dataclass(frozen=True, slots=True)
class ReadRequest:
    """Input to the reusable document/evidence reader.

    ``None`` means no filtering. An empty tuple is an explicit empty
    selection, which lets a caller preserve the meaning of an empty shortlist.
    ``document_ids`` and ``paper_ids`` are alternative identifier families;
    when both are supplied, either family may select a record.
    """

    bundle: DocumentBundle
    document_ids: tuple[str, ...] | None = None
    paper_ids: tuple[str, ...] | None = None


@dataclass(frozen=True, slots=True)
class ReadResult:
    """Typed evidence output produced from one selected document bundle."""

    status: ReadStatus
    bundle: DocumentBundle
    paper_cards: tuple[PaperCard, ...] = ()
    claim_cards: tuple[ClaimCard, ...] = ()
    method_cards: tuple[MethodCard, ...] = ()
    dataset_cards: tuple[DatasetCard, ...] = ()
    code_links: tuple[CodeLink, ...] = ()
    diagnostics: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        """Return a compact summary without copying document text."""
        return {
            "schema_version": "read_result.v1",
            "status": self.status,
            "document_count": len(self.bundle.records),
            "chunk_count": len(self.bundle.chunks),
            "paper_card_count": len(self.paper_cards),
            "claim_card_count": len(self.claim_cards),
            "method_card_count": len(self.method_cards),
            "dataset_card_count": len(self.dataset_cards),
            "code_link_count": len(self.code_links),
            "diagnostics": list(self.diagnostics),
        }


def read_documents(request: ReadRequest) -> ReadResult:
    """Read selected documents into deterministic evidence cards.

    The function does not search, call an LLM, write files, or inspect paths.
    It can therefore be embedded by the existing Read stage or called by a
    future standalone reader with the same typed request/result boundary.
    """
    bundle = _select_bundle(request)
    if not bundle.records:
        return ReadResult(
            status="empty",
            bundle=bundle,
            diagnostics=("No documents matched the requested read selection.",),
        )

    paper_cards, claim_cards = build_evidence_cards(
        documents=bundle.records,
        chunks=bundle.chunks,
    )
    method_cards = build_method_cards(documents=bundle.records, chunks=bundle.chunks)
    dataset_cards = build_dataset_cards(documents=bundle.records, chunks=bundle.chunks)
    code_links = build_code_links(documents=bundle.records, chunks=bundle.chunks)
    diagnostics: list[str] = []
    status: ReadStatus = "completed"
    if not bundle.chunks:
        status = "partial"
        diagnostics.append(
            "No text chunks were available; cards may rely on metadata abstracts."
        )
    return ReadResult(
        status=status,
        bundle=bundle,
        paper_cards=tuple(paper_cards),
        claim_cards=tuple(claim_cards),
        method_cards=tuple(method_cards),
        dataset_cards=tuple(dataset_cards),
        code_links=tuple(code_links),
        diagnostics=tuple(diagnostics),
    )


def _select_bundle(request: ReadRequest) -> DocumentBundle:
    if request.document_ids is None and request.paper_ids is None:
        return request.bundle

    document_ids = set(request.document_ids or ())
    paper_ids = set(request.paper_ids or ())
    records = [
        record
        for record in request.bundle.records
        if _record_matches(record, document_ids=document_ids, paper_ids=paper_ids)
    ]
    selected_document_ids = {record.document_id for record in records}
    return DocumentBundle(
        records=records,
        fulltext_manifest=request.bundle.fulltext_manifest,
        fulltext_extraction=request.bundle.fulltext_extraction,
        sections=[
            section
            for section in request.bundle.sections
            if section.document_id in selected_document_ids
        ],
        chunks=[
            chunk
            for chunk in request.bundle.chunks
            if chunk.document_id in selected_document_ids
        ],
    )


def _record_matches(
    record: DocumentRecord,
    *,
    document_ids: set[str],
    paper_ids: set[str],
) -> bool:
    if record.document_id in document_ids:
        return True
    paper_identifiers = {record.document_id, record.source_id or ""}
    metadata_paper_id = record.metadata.get("paper_id")
    if metadata_paper_id:
        paper_identifiers.add(str(metadata_paper_id))
    return bool({item for item in paper_identifiers if item} & paper_ids)
