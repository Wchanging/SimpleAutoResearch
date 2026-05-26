from __future__ import annotations

import hashlib
from collections import Counter
from pathlib import Path
from typing import Any

from simple_ar.literature.models import Paper, normalize_paper_id
from simple_ar.research.contracts import DocumentRecord, ExtractionStatus, SourcePlan
from simple_ar.research.fulltext import fulltext_hints_for_paper


TEXT_SUFFIXES = {".md", ".txt"}


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
        available. PDF inputs are recorded with extraction status and can be wired
        to a stronger parser later without changing downstream schemas.
    """
    records: list[DocumentRecord] = []
    records.extend(_record_from_paper(paper) for paper in papers)
    records.extend(_record_from_local_path(Path(path), source_plan=source_plan) for path in source_plan.local_documents)
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
            "Local Markdown/text files are parsed locally; PDF parsing is optional and best-effort.",
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
        authors=list(paper.authors),
        abstract=paper.abstract,
        local_path=local_path,
        extraction_status="metadata_only",
        metadata={"paper_id": paper.id, "fulltext_hints": hints},
    )


def _record_from_local_path(path: Path, *, source_plan: SourcePlan) -> DocumentRecord:
    suffix = path.suffix.lower()
    document_id = normalize_paper_id(f"local-{path.resolve() if path.exists() else path}")
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
        return _record_from_pdf(path, base=base, source_plan=source_plan)
    return DocumentRecord(
        **base,
        content_hash=_sha256(path),
        extraction_status="skipped",
        parser="unsupported_local_file",
        metadata={"suffix": suffix, "reason": "unsupported_suffix"},
    )


def _record_from_pdf(path: Path, *, base: dict[str, Any], source_plan: SourcePlan) -> DocumentRecord:
    if not source_plan.allow_pdf_download and not source_plan.require_fulltext:
        return DocumentRecord(
            **base,
            content_hash=_sha256(path),
            extraction_status="skipped",
            parser="pdf_optional",
            metadata={"suffix": ".pdf", "reason": "fulltext_disabled"},
        )
    try:
        text = _read_pdf_with_optional_parser(path)
    except Exception as exc:  # pragma: no cover - optional parser behavior varies by environment.
        return DocumentRecord(
            **base,
            content_hash=_sha256(path),
            extraction_status="failed",
            parser="pypdf_optional",
            metadata={"suffix": ".pdf", "error": str(exc)[:300]},
        )
    if not text.strip():
        return DocumentRecord(
            **base,
            content_hash=_sha256(path),
            extraction_status="failed",
            parser="pypdf_optional",
            metadata={"suffix": ".pdf", "error": "empty_extraction"},
        )
    return DocumentRecord(
        **base,
        abstract=_abstract(text),
        content_hash=_sha256(path),
        extraction_status="parsed",
        parser="pypdf_optional",
        metadata={"suffix": ".pdf", "bytes": path.stat().st_size},
    )


def _read_pdf_with_optional_parser(path: Path) -> str:
    try:
        from pypdf import PdfReader  # type: ignore[import-not-found]
    except ModuleNotFoundError as exc:
        raise RuntimeError("pypdf is not installed") from exc
    reader = PdfReader(str(path))
    parts: list[str] = []
    for page in reader.pages[:20]:
        parts.append(page.extract_text() or "")
    return "\n".join(parts)


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
