"""Deterministic document-ingest composition for the research pipeline.

This module owns the handoff between paper metadata, optional full text, and
the derived section/chunk records consumed by reading and evidence code.  It
does not write stage artifacts or call an LLM; callers decide how to persist
the returned bundle.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any, Iterable, Mapping

from simple_ar.core.capabilities import CapabilityContext, CapabilityResult
from simple_ar.literature.models import Paper
from simple_ar.research.contracts import DocumentRecord, DocumentSection, SourcePlan, TextChunk
from simple_ar.research.documents.extractors import apply_fulltext_extraction
from simple_ar.research.documents.fulltext import build_fulltext_manifest
from simple_ar.research.documents.ports import DocumentParser, DocumentResolver
from simple_ar.research.documents.records import build_document_records
from simple_ar.research.documents.sections import build_document_sections
from simple_ar.research.store.chunking import build_text_chunks


@dataclass(frozen=True)
class DocumentBundle:
    """Ingested document data before stage-specific serialization.

    The bundle is deliberately limited to data already represented by the
    existing research contracts.  Index persistence and JSON/JSONL projection
    stay outside this boundary so the same ingest result can serve a future
    standalone reader without taking on the Search stage's output layout.
    """

    records: list[DocumentRecord]
    fulltext_manifest: dict[str, Any]
    fulltext_extraction: dict[str, Any]
    sections: list[DocumentSection]
    chunks: list[TextChunk]

    def to_handoff_dict(self) -> dict[str, Any]:
        """Return one canonical, restorable document handoff."""

        return {
            "schema_version": "document_bundle.v1",
            "documents": [record.to_row() for record in self.records],
            "fulltext_manifest": dict(self.fulltext_manifest),
            "fulltext_extraction": dict(self.fulltext_extraction),
            "sections": [section.to_row() for section in self.sections],
            "chunks": [chunk.to_row() for chunk in self.chunks],
        }

    @classmethod
    def from_rows(
        cls,
        *,
        documents: Iterable[dict[str, Any]],
        chunks: Iterable[dict[str, Any]],
        sections: Iterable[dict[str, Any]] = (),
        fulltext_manifest: dict[str, Any] | None = None,
        fulltext_extraction: dict[str, Any] | None = None,
    ) -> "DocumentBundle":
        """Hydrate the bundle from persisted, schema-compatible rows."""
        return cls(
            records=[_from_row(DocumentRecord, row) for row in documents],
            fulltext_manifest=dict(fulltext_manifest or {}),
            fulltext_extraction=dict(fulltext_extraction or {}),
            sections=[_from_row(DocumentSection, row) for row in sections],
            chunks=[_from_row(TextChunk, row) for row in chunks],
        )

    @classmethod
    def from_handoff_dict(cls, data: Mapping[str, Any]) -> "DocumentBundle":
        """Restore a ``document_bundle.v1`` without network or parser calls."""

        if str(data.get("schema_version") or "") != "document_bundle.v1":
            raise ValueError("Expected a document_bundle.v1 object.")
        return cls.from_rows(
            documents=_mapping_rows(data.get("documents")),
            sections=_mapping_rows(data.get("sections")),
            chunks=_mapping_rows(data.get("chunks")),
            fulltext_manifest=(
                dict(data["fulltext_manifest"])
                if isinstance(data.get("fulltext_manifest"), Mapping)
                else {}
            ),
            fulltext_extraction=(
                dict(data["fulltext_extraction"])
                if isinstance(data.get("fulltext_extraction"), Mapping)
                else {}
            ),
        )


@dataclass(frozen=True, slots=True)
class DocumentIngestRequest:
    """Inputs for one reusable document-ingest attempt."""

    papers: tuple[Paper, ...]
    source_plan: SourcePlan
    extraction_dir: Path
    cache_dir: Path | None = None
    max_chunks: int | None = None
    resolver: DocumentResolver | None = None
    parser: DocumentParser | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "papers", tuple(self.papers))
        if self.max_chunks is not None and self.max_chunks < 1:
            raise ValueError("DocumentIngestRequest.max_chunks must be positive.")


def build_document_bundle(
    *,
    papers: list[Paper],
    source_plan: SourcePlan,
    cache_dir: Path | None,
    extraction_dir: Path,
    max_chunks: int | None = None,
    resolver: DocumentResolver | None = None,
    parser: DocumentParser | None = None,
) -> DocumentBundle:
    """Build the reusable document/section/chunk handoff.

    Full-text fetch and parsing retain their existing permission and budget
    behavior.  In particular, failures remain rows in the extraction manifest
    and do not turn a document-ingest failure into a pipeline exception.
    """

    records = build_document_records(papers=papers, source_plan=source_plan)
    fulltext_manifest = build_fulltext_manifest(
        records=records,
        source_plan=source_plan,
        cache_dir=cache_dir,
    )
    records, fulltext_extraction = apply_fulltext_extraction(
        records=records,
        fulltext_manifest=fulltext_manifest,
        source_plan=source_plan,
        extraction_dir=extraction_dir,
        resolver=resolver,
        parser=parser,
    )
    sections = build_document_sections(records)
    chunks = build_text_chunks(records, sections=sections, max_chunks=max_chunks)
    return DocumentBundle(
        records=records,
        fulltext_manifest=fulltext_manifest,
        fulltext_extraction=fulltext_extraction,
        sections=sections,
        chunks=chunks,
    )


def build_local_document_bundle(
    paths: Iterable[str | Path],
    *,
    extraction_dir: Path,
    max_chunks: int | None = None,
    parser_backend: str = "basic",
    resolver: DocumentResolver | None = None,
    parser: DocumentParser | None = None,
) -> DocumentBundle:
    """Build a document bundle from local files without running Search."""
    local_documents = [str(path) for path in paths]
    source_plan = SourcePlan(
        queries=["local documents"],
        sources=["local_files"],
        max_results_per_query=max(1, len(local_documents)),
        require_fulltext=True,
        allow_pdf_download=False,
        local_documents=local_documents,
        budget={"parser_backend": parser_backend},
    )
    return build_document_bundle(
        papers=[],
        source_plan=source_plan,
        cache_dir=None,
        extraction_dir=extraction_dir,
        max_chunks=max_chunks,
        resolver=resolver,
        parser=parser,
    )


def run_document_ingest_capability(
    *,
    context: CapabilityContext,
    request: DocumentIngestRequest,
) -> CapabilityResult:
    """Persist one document bundle as an explicit session handoff.

    Fetching, parsing, and cache policy remain owned by the existing ingest
    implementation. This adapter only adds attempt-local persistence and a
    stable status mapping for downstream Read capabilities.
    """

    bundle = build_document_bundle(
        papers=list(request.papers),
        source_plan=request.source_plan,
        cache_dir=request.cache_dir,
        extraction_dir=request.extraction_dir,
        max_chunks=request.max_chunks,
        resolver=request.resolver,
        parser=request.parser,
    )
    output = context.store.write_json(
        "document_bundle.json",
        bundle.to_handoff_dict(),
        kind="document_bundle",
        schema="document_bundle.v1",
        producer="research.documents",
    )
    diagnostics: list[str] = []
    failed_count = int(bundle.fulltext_extraction.get("failed_count") or 0)
    if not bundle.records:
        status = "blocked"
        diagnostics.append("No documents were available for ingest.")
    elif not bundle.chunks:
        status = "partial"
        diagnostics.append("Ingest produced no text chunks.")
    elif failed_count:
        status = "partial"
        diagnostics.append(f"{failed_count} document extraction(s) failed.")
    else:
        status = "completed"
    return CapabilityResult(
        status=status,  # type: ignore[arg-type]
        artifacts=(output,),
        diagnostics=tuple(diagnostics),
        usage={
            "documents": len(bundle.records),
            "sections": len(bundle.sections),
            "chunks": len(bundle.chunks),
            "extraction_failures": failed_count,
        },
        provenance={
            "capability": "document_ingest",
            "result_schema": "document_bundle.v1",
        },
    )


def _from_row(type_hint: type[Any], row: dict[str, Any]) -> Any:
    """Instantiate a persisted dataclass row while ignoring unknown fields."""
    allowed = {field.name for field in fields(type_hint)}
    return type_hint(**{key: value for key, value in row.items() if key in allowed})


def _mapping_rows(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(row) for row in value if isinstance(row, Mapping)]
