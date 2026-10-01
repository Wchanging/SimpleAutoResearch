from __future__ import annotations

import hashlib
from collections import Counter
from pathlib import Path
from typing import Any

from simple_ar.literature.models import Paper, normalize_paper_id
from simple_ar.research.contracts import DocumentRecord, ExtractionStatus, SourcePlan
from simple_ar.research.documents.fulltext import fulltext_hints_for_paper


TEXT_SUFFIXES = {".md", ".markdown", ".txt"}


def build_document_records(
    *,
    papers: list[Paper],
    source_plan: SourcePlan,
) -> list[DocumentRecord]:
    """Build provenance records for retrieved papers and configured local files.

    Args:
        papers: Selected paper metadata rows that will be passed to the read stage.
        source_plan: Search source plan, including local document hints and
            extraction/cache preferences.

    Returns:
        Deduplicated document records. Metadata records are always preserved; local
        Markdown/text inputs are parsed into inspectable document records when
        available. Supplied PDFs are passed to the bounded extraction stage so
        parsing succeeds or fails once with an inspectable manifest row.
    """
    records: list[DocumentRecord] = []
    # Explicitly supplied inputs get first access to a bounded chunk budget;
    # retrieved metadata must not crowd out the paper the user actually gave us.
    records.extend(_record_from_local_path(Path(path)) for path in source_plan.local_documents)
    records.extend(_record_from_paper(paper) for paper in papers)
    return _deduplicate_records(records)


def build_cache_manifest(
    *,
    records: list[DocumentRecord],
    source_plan: SourcePlan,
) -> dict[str, Any]:
    """Return a compact cache/extraction manifest for the search stage."""
    status_counts = Counter(record.extraction_status for record in records)
    source_counts = Counter(record.source for record in records)
    return {
        "schema_version": "research_cache_manifest.v1",
        "cache_enabled": source_plan.cache_enabled,
        "index_backend": source_plan.index_backend,
        "require_fulltext": source_plan.require_fulltext,
        "allow_pdf_download": source_plan.allow_pdf_download,
        "document_count": len(records),
        "local_document_count": len(source_plan.local_documents),
        "status_counts": dict(sorted(status_counts.items())),
        "source_counts": dict(sorted(source_counts.items())),
        "notes": [
            "Metadata records are stored without downloading restricted full text.",
            "Local Markdown/text files are parsed locally; supplied PDFs use bounded best-effort parsing.",
        ],
    }


def _record_from_paper(paper: Paper) -> DocumentRecord:
    local_path = paper.source_id if paper.source == "local_files" and paper.source_id else None
    hints = [hint.to_row() for hint in fulltext_hints_for_paper(paper, document_id=normalize_paper_id(f"{paper.source}-{paper.id}"))]
    return DocumentRecord(
        document_id=normalize_paper_id(f"{paper.source}-{paper.id}"),
        title=paper.title,
        source=paper.source,
        source_id=paper.source_id or paper.id,
        url=paper.url,
        doi=paper.doi,
        published=paper.published,
        authors=list(paper.authors),
        abstract=paper.abstract,
        local_path=local_path,
        extraction_status="metadata_only",
        metadata={"paper_id": paper.id, "fulltext_hints": hints,
                  "bibliographic_notes": list(paper.bibliographic_notes)},
    )


def _record_from_local_path(path: Path) -> DocumentRecord:
    suffix = path.suffix.lower()
    # Keep the path in the provenance record, but never embed it in a citation
    # identifier: those identifiers can appear in exported BibTeX and reports.
    path_digest = hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()[:20]
    document_id = f"local-{path_digest}"
    base = {
        "document_id": document_id,
        "title": path.stem.replace("_", " ").replace("-", " ").strip() or path.name,
        "source": "local_files",
        "source_id": str(path),
        "url": str(path),
        "local_path": str(path),
    }
    if not path.exists() or not path.is_file():
        return DocumentRecord(
            **base,
            extraction_status="failed",
            parser="local_file",
            metadata={"error": "file_not_found"},
        )
    if suffix in TEXT_SUFFIXES:
        text = _read_text(path)
        return DocumentRecord(
            **base,
            abstract=_abstract(text),
            content_hash=_sha256(path),
            extraction_status="parsed",
            parser="plain_text",
            metadata={"suffix": suffix, "bytes": path.stat().st_size},
        )
    if suffix == ".pdf":
        return _record_from_pdf(path, base=base)
    return DocumentRecord(
        **base,
        content_hash=_sha256(path),
        extraction_status="skipped",
        parser="unsupported_local_file",
        metadata={"suffix": suffix, "reason": "unsupported_suffix"},
    )


def _record_from_pdf(path: Path, *, base: dict[str, Any]) -> DocumentRecord:
    # The extraction stage parses this once and records parser failures. A
    # supplied PDF does not require permission to download remote full text.
    return DocumentRecord(
        **base,
        content_hash=_sha256(path),
        extraction_status="metadata_only",
        parser="pdf_pending",
        metadata={"suffix": ".pdf", "bytes": path.stat().st_size},
    )


def _deduplicate_records(records: list[DocumentRecord]) -> list[DocumentRecord]:
    by_key: dict[str, DocumentRecord] = {}
    for record in records:
        key = _record_key(record)
        existing = by_key.get(key)
        if existing is None or _status_rank(record.extraction_status) > _status_rank(existing.extraction_status):
            by_key[key] = record
    return list(by_key.values())


def _record_key(record: DocumentRecord) -> str:
    if record.source == "local_files" and record.local_path:
        return f"local:{Path(record.local_path)}"
    if record.source_id:
        return f"{record.source}:{record.source_id}"
    return record.document_id


def _status_rank(status: ExtractionStatus) -> int:
    return {
        "failed": 0,
        "skipped": 1,
        "pending": 2,
        "metadata_only": 3,
        "parsed": 4,
    }.get(status, 0)


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="replace")


def _abstract(text: str, *, limit: int = 1200) -> str:
    return " ".join(text.split())[:limit]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
