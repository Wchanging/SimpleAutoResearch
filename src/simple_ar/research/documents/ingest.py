"""Deterministic document-ingest composition for the research pipeline.

This module owns the handoff between paper metadata, optional full text, and
the derived section/chunk records consumed by reading and evidence code.  It
does not write stage artifacts or call an LLM; callers decide how to persist
the returned bundle.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from simple_ar.literature.models import Paper
from simple_ar.research.contracts import DocumentRecord, DocumentSection, SourcePlan, TextChunk
from simple_ar.research.documents.extractors import apply_fulltext_extraction
from simple_ar.research.documents.fulltext import build_fulltext_manifest
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


def build_document_bundle(
    *,
    papers: list[Paper],
    source_plan: SourcePlan,
    cache_dir: Path | None,
    extraction_dir: Path,
    max_chunks: int | None = None,
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
