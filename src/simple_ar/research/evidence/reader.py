"""Reusable document-to-evidence reading boundary.

The reader owns deterministic card derivation from an already ingested
``DocumentBundle``. LLM screening and paper-note prompts remain stage policy;
this module is intentionally usable without Search or a pipeline Context.
"""

from __future__ import annotations

from dataclasses import dataclass, fields, replace
from typing import Any, Literal, Mapping

from simple_ar.core.capabilities import CapabilityContext, CapabilityResult
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

    def to_handoff_dict(self) -> dict[str, object]:
        """Return cards and source locations for an explicit downstream handoff.

        The handoff keeps document identity and evidence locations, but does
        not duplicate chunk text.  Callers can therefore persist it beside an
        attempt without turning the read result into a second document store.
        """
        return {
            "schema_version": "read_result.v1",
            "status": self.status,
            "documents": [_document_handoff_row(record) for record in self.bundle.records],
            "source_spans": [
                {
                    "chunk_id": chunk.chunk_id,
                    "document_id": chunk.document_id,
                    "source_path": chunk.source_path,
                    "page": chunk.page,
                    "line_start": chunk.line_start,
                    "line_end": chunk.line_end,
                }
                for chunk in self.bundle.chunks
            ],
            "paper_cards": [card.to_row() for card in self.paper_cards],
            "claim_cards": [card.to_row() for card in self.claim_cards],
            "method_cards": [card.to_row() for card in self.method_cards],
            "dataset_cards": [card.to_row() for card in self.dataset_cards],
            "code_links": [link.to_row() for link in self.code_links],
            "diagnostics": list(self.diagnostics),
        }

    @classmethod
    def from_handoff_dict(
        cls,
        data: Mapping[str, Any],
        *,
        bundle: DocumentBundle,
    ) -> "ReadResult":
        """Restore a persisted read handoff without network or parser calls.

        The source bundle is required explicitly because the read handoff
        keeps chunk text in the document artifact rather than copying it a
        second time. Cards and source locations are restored from the handoff.
        """

        if str(data.get("schema_version") or "") != "read_result.v1":
            raise ValueError("Expected a read_result.v1 object.")
        status = str(data.get("status") or "")
        if status not in {"completed", "partial", "empty"}:
            raise ValueError(f"Unsupported read handoff status: {status!r}")
        result = cls(
            status=status,  # type: ignore[arg-type]
            bundle=bundle,
            paper_cards=_card_rows(data.get("paper_cards"), PaperCard),
            claim_cards=_card_rows(data.get("claim_cards"), ClaimCard),
            method_cards=_card_rows(data.get("method_cards"), MethodCard),
            dataset_cards=_card_rows(data.get("dataset_cards"), DatasetCard),
            code_links=_card_rows(data.get("code_links"), CodeLink),
            diagnostics=tuple(
                str(item) for item in data.get("diagnostics", [])
            ),
        )
        return _with_evidence_validation(result)


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
    return _with_evidence_validation(ReadResult(
        status=status,
        bundle=bundle,
        paper_cards=tuple(paper_cards),
        claim_cards=tuple(claim_cards),
        method_cards=tuple(method_cards),
        dataset_cards=tuple(dataset_cards),
        code_links=tuple(code_links),
        diagnostics=tuple(diagnostics),
    ))


def run_read_capability(
    *,
    context: CapabilityContext,
    request: ReadRequest,
) -> CapabilityResult:
    """Persist one deterministic read handoff through the session boundary.

    Reading remains a pure transformation over a caller-provided bundle.  The
    adapter owns only the attempt-local artifact and status mapping; it does
    not fetch documents, call an LLM, or silently expand the selection.
    """
    result = read_documents(request)
    output = context.store.write_json(
        "read_result.json",
        result.to_handoff_dict(),
        kind="read_result",
        schema="read_result.v1",
        producer="research.read",
    )
    status = {
        "completed": "completed",
        "partial": "partial",
        "empty": "blocked",
    }[result.status]
    return CapabilityResult(
        status=status,  # type: ignore[arg-type]
        artifacts=(output,),
        diagnostics=result.diagnostics,
        usage={
            "documents": len(result.bundle.records),
            "chunks": len(result.bundle.chunks),
            "paper_cards": len(result.paper_cards),
        },
        provenance={
            "capability": "read",
            "result_schema": "read_result.v1",
        },
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


def _document_handoff_row(record: DocumentRecord) -> dict[str, object]:
    """Keep document identity and provenance without duplicating full text."""
    return {
        "document_id": record.document_id,
        "source_id": record.source_id,
        "title": record.title,
        "source": record.source,
        "url": record.url,
        "doi": record.doi,
        "published": record.published,
        "extraction_status": record.extraction_status,
        "parser": record.parser,
    }


def validate_read_evidence(result: ReadResult) -> tuple[str, ...]:
    """Return diagnostics for card references absent from the source bundle.

    Cards deliberately point to chunk IDs rather than copying source text. A
    persisted handoff is only trustworthy when those references still resolve
    against the bundle supplied by the caller. The check is intentionally
    narrow: it validates declared references and does not scan files or infer
    semantic correctness from prose.
    """

    chunk_ids = {chunk.chunk_id for chunk in result.bundle.chunks}
    references: list[str] = []
    for cards in (
        result.paper_cards,
        result.claim_cards,
        result.method_cards,
        result.dataset_cards,
        result.code_links,
    ):
        for card in cards:
            for reference in card.evidence_refs:
                reference = str(reference).strip()
                if reference:
                    references.append(reference)
    missing = sorted({reference for reference in references if reference not in chunk_ids})
    if not missing:
        return ()
    return (
        f"{len(missing)} read evidence reference(s) do not resolve to the document bundle: "
        + ", ".join(missing[:8])
        + (" ..." if len(missing) > 8 else ""),
    )


def _with_evidence_validation(result: ReadResult) -> ReadResult:
    diagnostics = validate_read_evidence(result)
    if not diagnostics:
        return result
    status = "partial" if result.status == "completed" else result.status
    return replace(
        result,
        status=status,  # type: ignore[arg-type]
        diagnostics=tuple(dict.fromkeys((*result.diagnostics, *diagnostics))),
    )


def _card_rows(value: object, card_type: type[Any]) -> tuple[Any, ...]:
    if not isinstance(value, list):
        return ()
    allowed = {field.name for field in fields(card_type)}
    return tuple(
        card_type(**{key: row[key] for key in allowed if key in row})
        for row in value
        if isinstance(row, Mapping)
    )


__all__ = [
    "ReadRequest",
    "ReadResult",
    "ReadStatus",
    "read_documents",
    "run_read_capability",
    "validate_read_evidence",
]
