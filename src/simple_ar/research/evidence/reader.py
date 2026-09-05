"""Reusable document-to-evidence reading boundary.

The reader owns card derivation from an already ingested ``DocumentBundle``
and can optionally apply the bounded screening/note policy shared with the
legacy facade. It does not search, write files, or require a pipeline Context.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields, replace
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
from simple_ar.research.evidence.screening import (
    read_paper_notes_with_llm,
    render_paper_notes_markdown,
    screen_papers_with_llm,
)


ReadStatus = Literal["completed", "partial", "empty"]


@dataclass(frozen=True, slots=True)
class ReadRequest:
    """Input to the reusable document/evidence reader.

    ``None`` means no filtering. An empty tuple is an explicit empty
    selection, which lets a caller preserve the meaning of an empty shortlist.
    ``document_ids`` and ``paper_ids`` are alternative identifier families;
    when both are supplied, either family may select a record.  Model-assisted
    screening and notes are opt-in; the default remains a pure deterministic
    document-to-card transformation.
    """

    bundle: DocumentBundle
    document_ids: tuple[str, ...] | None = None
    paper_ids: tuple[str, ...] | None = None
    topic: str = ""
    problem_markdown: str = ""
    research_plan_json: str = "{}"
    config: Mapping[str, object] = field(default_factory=dict)
    use_llm: bool = False
    llm_client: Any | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.use_llm and self.llm_client is None:
            raise ValueError("ReadRequest.llm_client is required when use_llm is true.")
        object.__setattr__(self, "config", dict(self.config))


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
    screening_decisions: tuple[dict[str, Any], ...] = ()
    paper_notes: tuple[dict[str, Any], ...] = ()
    notes_markdown: str = ""
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
            "screening_decision_count": len(self.screening_decisions),
            "paper_note_count": len(self.paper_notes),
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
            "screening_decisions": [dict(row) for row in self.screening_decisions],
            "paper_notes": [dict(row) for row in self.paper_notes],
            "notes_markdown": self.notes_markdown,
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
            screening_decisions=tuple(
                dict(row)
                for row in data.get("screening_decisions", [])
                if isinstance(row, Mapping)
            ),
            paper_notes=tuple(
                dict(row)
                for row in data.get("paper_notes", [])
                if isinstance(row, Mapping)
            ),
            notes_markdown=str(data.get("notes_markdown") or ""),
            diagnostics=tuple(
                str(item) for item in data.get("diagnostics", [])
            ),
        )
        return _with_evidence_validation(result)


def read_documents(request: ReadRequest) -> ReadResult:
    """Read selected documents into evidence cards and optional model notes.

    The function does not search, write files, or inspect paths.  Without
    ``use_llm`` it performs only deterministic card derivation.  With explicit
    model assistance it applies bounded screening and note generation before
    deriving cards from the selected records.
    """
    bundle = _select_bundle(request)
    screening_decisions: tuple[dict[str, Any], ...] = ()
    paper_notes: tuple[dict[str, Any], ...] = ()
    notes_markdown = ""
    if request.use_llm and bundle.records:
        client = request.llm_client
        if client is None:
            raise ValueError("ReadRequest.llm_client is required when use_llm is true.")
        if _read_screening_mode(request.config) != "deterministic":
            decisions = screen_papers_with_llm(
                client,
                topic=request.topic or "research topic",
                problem_markdown=request.problem_markdown,
                research_plan_json=request.research_plan_json or "{}",
                papers=[record.to_row() for record in bundle.records],
                config=request.config,
            )
            screening_decisions = tuple(decisions)
            bundle = _bundle_for_screening(bundle, decisions)
        if bundle.records:
            notes = read_paper_notes_with_llm(
                client,
                papers=[record.to_row() for record in bundle.records],
                evidence_snippets=format_bundle_evidence_snippets(bundle),
                config=request.config,
            )
            paper_notes = tuple(notes)
            notes_markdown = render_paper_notes_markdown(notes)
    if not bundle.records:
        return ReadResult(
            status="empty",
            bundle=bundle,
            screening_decisions=screening_decisions,
            paper_notes=paper_notes,
            notes_markdown=notes_markdown,
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
        screening_decisions=screening_decisions,
        paper_notes=paper_notes,
        notes_markdown=notes_markdown,
        diagnostics=tuple(diagnostics),
    ))


def run_read_capability(
    *,
    context: CapabilityContext,
    request: ReadRequest,
) -> CapabilityResult:
    """Persist one read handoff through the session boundary.

    Reading remains a transformation over a caller-provided bundle.  The
    adapter owns only the attempt-local artifact and status mapping; it does
    not fetch documents or silently expand the selection.  Model assistance is
    still explicit on ``ReadRequest``.
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
            "screening_decisions": len(result.screening_decisions),
            "paper_notes": len(result.paper_notes),
        },
        provenance={
            "capability": "read",
            "result_schema": "read_result.v1",
            "mode": "llm" if request.use_llm else "deterministic",
            "model": str(getattr(request.llm_client, "model", ""))
            if request.use_llm
            else "",
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


def _read_screening_mode(config: Mapping[str, object]) -> str:
    value = str(
        config.get("read_screening")
        or config.get("research_read_screening")
        or "auto"
    ).strip().lower()
    return value if value in {"auto", "llm", "deterministic"} else "auto"


def _bundle_for_screening(
    bundle: DocumentBundle,
    decisions: list[dict[str, Any]],
) -> DocumentBundle:
    """Keep model-selected records in priority order without copying text."""

    if not decisions:
        return bundle
    record_by_identifier: dict[str, DocumentRecord] = {}
    for record in bundle.records:
        identifiers = {
            record.document_id,
            record.source_id or "",
            str(record.metadata.get("paper_id") or ""),
        }
        for identifier in identifiers:
            if identifier:
                record_by_identifier.setdefault(identifier, record)
    selected_records: list[DocumentRecord] = []
    selected_ids: set[str] = set()
    kept = [
        row
        for row in decisions
        if str(row.get("decision") or "keep").strip().lower() == "keep"
    ]
    kept.sort(
        key=lambda row: (
            _optional_int(row.get("reading_priority")) or 9999,
            str(row.get("paper_id") or ""),
        )
    )
    for row in kept:
        identifier = str(row.get("paper_id") or "").strip()
        record = record_by_identifier.get(identifier)
        if record is None or record.document_id in selected_ids:
            continue
        selected_records.append(record)
        selected_ids.add(record.document_id)
    return DocumentBundle(
        records=selected_records,
        fulltext_manifest=bundle.fulltext_manifest,
        fulltext_extraction=bundle.fulltext_extraction,
        sections=[
            section for section in bundle.sections if section.document_id in selected_ids
        ],
        chunks=[
            chunk for chunk in bundle.chunks if chunk.document_id in selected_ids
        ],
    )


def _optional_int(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def format_bundle_evidence_snippets(
    bundle: DocumentBundle,
    *,
    max_chunks: int = 12,
    max_chars: int = 900,
) -> str:
    """Render bounded, source-labelled text for model reading prompts."""

    lines: list[str] = []
    for chunk in bundle.chunks[:max_chunks]:
        text = " ".join(chunk.text.split())
        if len(text) > max_chars:
            text = text[: max_chars - 3].rstrip() + "..."
        if not text:
            continue
        location = chunk.source_path or chunk.document_id
        if chunk.line_start is not None:
            location += f":{chunk.line_start}"
            if chunk.line_end is not None and chunk.line_end != chunk.line_start:
                location += f"-{chunk.line_end}"
        lines.append(f"[{chunk.chunk_id}] {location}: {text}")
    return "\n".join(lines)


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
    "format_bundle_evidence_snippets",
    "read_documents",
    "run_read_capability",
    "validate_read_evidence",
]
