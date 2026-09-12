"""Read an archived eight-stage Search directory without a runtime context."""

from pathlib import Path

from simple_ar.core.artifacts import read_json, read_jsonl
from simple_ar.research.documents.ingest import DocumentBundle


def load_search_document_bundle(search_dir: Path) -> DocumentBundle:
    """Keep historical JSON/JSONL readable; never create or advance a run."""
    return DocumentBundle.from_rows(
        documents=_rows(search_dir / "documents/documents.jsonl"),
        chunks=_rows(search_dir / "research_index/chunks.jsonl"),
        sections=_rows(search_dir / "documents/sections.jsonl"),
        fulltext_manifest=_object(search_dir / "documents/fulltext_manifest.json"),
        fulltext_extraction=_object(search_dir / "documents/fulltext_extraction.json"),
    )


def _rows(path: Path) -> list[dict]:
    return read_jsonl(path) if path.exists() else []


def _object(path: Path) -> dict:
    return read_json(path) if path.exists() else {}
