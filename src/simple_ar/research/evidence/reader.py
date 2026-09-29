"""Reusable document-to-evidence reading boundary.

The reader owns card derivation from an already ingested ``DocumentBundle``
and can optionally apply the bounded screening/note policy shared with the
legacy facade. It does not search, write files, or require a pipeline Context.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields, replace
from typing import Any, Callable, Literal, Mapping

from simple_ar.core.capabilities import CapabilityContext, CapabilityResult
from simple_ar.research.contracts import (
    ClaimCard,
    CodeLink,
    DatasetCard,
    DocumentRecord,
    EvidenceRef,
    MethodCard,
    PaperCard,
    TextChunk,
)
from simple_ar.research.documents.ingest import DocumentBundle
from simple_ar.research.evidence.cards import (
    build_code_links,
    build_dataset_cards,
    build_evidence_cards,
    build_method_cards,
)
from simple_ar.research.evidence.screening import (
    _screening_max_shortlist,
    read_paper_notes_with_llm,
    render_paper_notes_markdown,
    screen_papers_with_llm,
)


ReadStatus = Literal["completed", "partial", "empty"]


def query_evidence(
    bundle: DocumentBundle,
    *,
    document_id: str | None = None,
    chunk_ids: tuple[str, ...] | None = None,
    adjacent_chunks: int = 0,
) -> tuple[EvidenceRef, ...]:
    """Resolve real source chunks into stable evidence references.

    ``chunk_ids=None`` selects all chunks, while ``chunk_ids=()`` explicitly
    selects none.  Unknown IDs and duplicate chunk identities are errors so a
    caller cannot silently turn a dangling citation into a different source.
    Neighboring context is restricted to the same document and follows the
    bundle's persisted order.
    """

    if adjacent_chunks < 0:
        raise ValueError("adjacent_chunks must be non-negative.")
    chunks_by_id: dict[str, Any] = {}
    positions_by_document: dict[str, list[Any]] = {}
    for position, chunk in enumerate(bundle.chunks):
        if chunk.chunk_id in chunks_by_id:
            raise ValueError(f"Duplicate evidence chunk ID: {chunk.chunk_id}")
        entry = (position, chunk)
        chunks_by_id[chunk.chunk_id] = entry
        positions_by_document.setdefault(chunk.document_id, []).append(entry)

    records = {record.document_id: record for record in bundle.records}
    if document_id is not None and document_id not in records:
        raise ValueError(f"Unknown evidence document ID: {document_id}")
    if chunk_ids == ():
        return ()
    selected_ids = (
        tuple(chunk.chunk_id for chunk in bundle.chunks
              if document_id is None or chunk.document_id == document_id)
        if chunk_ids is None
        else tuple(dict.fromkeys(chunk_ids))
    )
    missing = [chunk_id for chunk_id in selected_ids if chunk_id not in chunks_by_id]
    if missing:
        raise ValueError("Unknown evidence chunk ID(s): " + ", ".join(missing))

    refs: list[EvidenceRef] = []
    for chunk_id in selected_ids:
        _, chunk = chunks_by_id[chunk_id]
        if document_id is not None and chunk.document_id != document_id:
            raise ValueError(
                f"Evidence chunk {chunk_id} does not belong to document {document_id}."
            )
        record = records.get(chunk.document_id)
        if record is None:
            raise ValueError(f"Missing document record for evidence chunk {chunk_id}.")
        document_entries = positions_by_document[chunk.document_id]
        local_index = next(index for index, (_, item) in enumerate(document_entries) if item.chunk_id == chunk_id)
        start = max(0, local_index - adjacent_chunks)
        end = min(len(document_entries), local_index + adjacent_chunks + 1)
        context_entries = document_entries[start:end]
        context_ids = tuple(item.chunk_id for _, item in context_entries if item.chunk_id != chunk_id)
        context_text = "\n\n".join(item.text for _, item in context_entries if item.text.strip())
        revision = str(
            record.content_hash
            or record.metadata.get("revision")
            or record.metadata.get("document_revision")
            or ""
        ).strip() or None
        refs.append(
            EvidenceRef(
                evidence_id=chunk.chunk_id,
                document_id=record.document_id,
                chunk_id=chunk.chunk_id,
                source=record.source,
                source_id=record.source_id,
                document_revision=revision,
                source_path=chunk.source_path,
                page=chunk.page,
                line_start=chunk.line_start,
                line_end=chunk.line_end,
                extraction_status=record.extraction_status,
                text=chunk.text,
                adjacent_chunk_ids=context_ids,
                context_text=context_text,
            )
        )
    return tuple(refs)


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
    required_document_ids: tuple[str, ...] = ()
    topic: str = ""
    problem_markdown: str = ""
    research_plan_json: str = "{}"
    config: Mapping[str, object] = field(default_factory=dict)
    use_llm: bool = False
    llm_client: Any | None = field(default=None, repr=False, compare=False)
    emit: Callable[[str], None] | None = field(default=None, repr=False, compare=False)

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
                {key: value for key, value in ref.to_row().items()
                 if key not in {"text", "context_text"}}
                for ref in query_evidence(self.bundle)
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
                emit=request.emit,
                papers=[record.to_row() for record in bundle.records],
                config=request.config,
            )
            decisions = _preserve_required_screening(
                bundle, decisions, request.required_document_ids, request.config,
            )
            screening_decisions = tuple(decisions)
            bundle = _bundle_for_screening(bundle, decisions)
        if bundle.records:
            notes = read_paper_notes_with_llm(
                client,
                papers=[record.to_row() for record in bundle.records],
                evidence_snippets_by_document={
                    record.document_id: format_bundle_evidence_snippets(
                        bundle, document_id=record.document_id,
                    )
                    for record in bundle.records
                },
                emit=request.emit,
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


def _preserve_required_screening(
    bundle: DocumentBundle,
    decisions: list[dict[str, Any]],
    required_document_ids: tuple[str, ...],
    config: Mapping[str, object],
) -> list[dict[str, Any]]:
    """Keep explicitly supplied documents without silently widening the shortlist."""

    required = tuple(dict.fromkeys(required_document_ids))
    if not required or not decisions:
        return decisions
    records = {record.document_id: record for record in bundle.records}
    missing = set(required) - records.keys()
    if missing:
        raise ValueError("Required read document(s) are absent: " + ", ".join(sorted(missing)))
    limit = _screening_max_shortlist(config, len(bundle.records))
    if len(required) > limit:
        raise ValueError(
            f"{len(required)} supplied documents exceed the read shortlist limit {limit}; "
            "increase research.read_max_shortlist or narrow the supplied documents."
        )

    output = [dict(row) for row in decisions]
    by_id = {str(row.get("paper_id") or ""): row for row in output}
    for index, document_id in enumerate(required):
        record = records[document_id]
        identifiers = (document_id, record.source_id or "", str(record.metadata.get("paper_id") or ""))
        row = next((by_id[identifier] for identifier in identifiers if identifier in by_id), None)
        if row is None:
            row = {"paper_id": document_id}
            output.append(row)
        row.update({
            "decision": "keep",
            "reading_priority": index - len(required),
            "reason": "Explicitly supplied document; retained for evidence-grounded reading.",
        })

    required_set = set(required)
    selected = [row for row in output if str(row.get("decision") or "keep").lower() == "keep"]
    selected.sort(key=lambda row: (
        _optional_int(row.get("reading_priority")) or 9999,
        str(row.get("paper_id") or ""),
    ))
    for row in selected[limit:]:
        identifier = str(row.get("paper_id") or "")
        if identifier in required_set:
            continue
        row["decision"] = "drop"
        row["reason"] = "Displaced by explicitly supplied document within read shortlist limit."
    return output


def _optional_int(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def select_representative_chunks(
    chunks: tuple[TextChunk, ...] | list[TextChunk], *, max_chunks: int,
    required_chunk_ids: tuple[str, ...] = (),
) -> tuple[TextChunk, ...]:
    """Bound a reading overview while covering documents and observed sections.

    This is a sampling policy, not a claim that the selected text represents
    unseen parts of a paper. Bibliography chunks are excluded when the same
    document has substantive text; section-less extraction is spread across
    the document instead of taking only its opening pages.
    """
    if max_chunks < 1:
        return ()
    required = set(required_chunk_ids)
    pinned = [chunk for chunk in chunks
              if chunk.chunk_id in required and chunk.metadata.get("section") != "references"][:max_chunks]
    pinned_ids = {chunk.chunk_id for chunk in pinned}
    max_chunks -= len(pinned)
    documents: dict[str, list[TextChunk]] = {}
    for chunk in chunks:
        if chunk.chunk_id in pinned_ids:
            continue
        documents.setdefault(chunk.document_id, []).append(chunk)
    candidates = {
        document_id: [row for row in rows if row.metadata.get("section") != "references"] or rows
        for document_id, rows in documents.items()
    }
    quotas = {document_id: 0 for document_id in candidates}
    remaining = max_chunks
    while remaining:
        advanced = False
        for document_id, rows in candidates.items():
            if remaining == 0:
                break
            if quotas[document_id] < len(rows):
                quotas[document_id] += 1
                remaining -= 1
                advanced = True
        if not advanced:
            break

    selected_ids: set[str] = set(pinned_ids)
    for document_id, rows in candidates.items():
        quota = quotas[document_id]
        if not quota:
            continue
        sections: dict[str, list[int]] = {}
        for index, row in enumerate(rows):
            section_id = str(row.metadata.get("section_id") or document_id)
            sections.setdefault(section_id, []).append(index)
        anchors = [positions[len(positions) // 2] for positions in sections.values()]
        if len(anchors) > quota:
            # Section type is useful for an overview, but never substitutes
            # for the actual text. Reserve a few distinct kinds before
            # spreading the remaining windows across the document.
            by_kind: dict[str, list[int]] = {}
            for index in anchors:
                by_kind.setdefault(str(rows[index].metadata.get("section") or "body"), []).append(index)
            chosen: set[int] = set()
            for kind in ("abstract", "method", "experiments", "results", "limitations",
                         "discussion", "conclusion", "related_work", "introduction"):
                if len(chosen) == quota:
                    break
                options = by_kind.get(kind, [])
                if options:
                    chosen.add(options[len(options) // 2])
        else:
            chosen = set(anchors)
        while len(chosen) < quota:
            pool = anchors if len(chosen) < len(anchors) else list(range(len(rows)))
            next_index = max(
                (index for index in pool if index not in chosen),
                key=lambda index: (min(abs(index - prior) for prior in chosen) if chosen else 0, -index),
            )
            chosen.add(next_index)
        selected_ids.update(rows[index].chunk_id for index in chosen)
    return tuple(chunk for chunk in chunks if chunk.chunk_id in selected_ids)


def format_bundle_evidence_snippets(
    bundle: DocumentBundle,
    *,
    max_chunks: int = 12,
    max_chars: int = 900,
    document_id: str | None = None,
) -> str:
    """Render bounded, source-labelled text for model reading prompts."""

    lines: list[str] = []
    available = tuple(
        chunk for chunk in bundle.chunks
        if document_id is None or chunk.document_id == document_id
    )
    selected = select_representative_chunks(available, max_chunks=max_chunks)
    for chunk in selected:
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
        heading = str(chunk.metadata.get("heading") or chunk.metadata.get("section") or "").strip()
        label = f" ({heading})" if heading else ""
        lines.append(f"[{chunk.chunk_id}] {location}{label}: {text}")
    if len(selected) < len(available):
        lines.append(
            f"[coverage] Bounded overview sampled {len(selected)} of {len(available)} chunks; "
            "unseen details require a targeted source read."
        )
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
    "select_representative_chunks",
    "read_documents",
    "run_read_capability",
    "validate_read_evidence",
]
