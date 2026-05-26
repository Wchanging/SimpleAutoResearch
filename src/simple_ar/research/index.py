from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from simple_ar.artifacts import write_json, write_jsonl
from simple_ar.research.contracts import TextChunk


SQLITE_INDEX_NAME = "sqlite_fts.db"


def write_research_index(
    *,
    index_dir: Path,
    chunks: list[TextChunk],
    backend: str,
) -> dict[str, Any]:
    """Write local research chunks and an optional SQLite FTS index.

    Args:
        index_dir: Destination directory under the search stage.
        chunks: Text chunks to index.
        backend: Configured backend: ``keyword``, ``sqlite_fts``, ``hybrid``, or
            future adapter names.

    Returns:
        Index metadata written to ``index_meta.json``.
    """
    normalized_backend = _normalize_backend(backend)
    index_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(index_dir / "chunks.jsonl", [chunk.to_row() for chunk in chunks])
    sqlite_status = "not_requested"
    sqlite_path: str | None = None
    if normalized_backend in {"sqlite_fts", "hybrid"}:
        sqlite_status = _write_sqlite_fts(index_dir / SQLITE_INDEX_NAME, chunks)
        sqlite_path = SQLITE_INDEX_NAME
    meta = {
        "schema_version": "research_index_meta.v1",
        "backend": normalized_backend,
        "chunk_count": len(chunks),
        "sqlite_fts": {
            "status": sqlite_status,
            "path": sqlite_path,
        },
        "notes": [
            "chunks.jsonl is the portable source of truth.",
            "SQLite FTS is an optional local acceleration layer for standard/strong modes.",
        ],
    }
    write_json(index_dir / "index_meta.json", meta)
    return meta


def _write_sqlite_fts(path: Path, chunks: list[TextChunk]) -> str:
    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(path)
        conn.execute("PRAGMA journal_mode=DELETE")
        conn.execute("PRAGMA synchronous=OFF")
        conn.execute("DROP TABLE IF EXISTS chunks")
        conn.execute(
            "CREATE VIRTUAL TABLE chunks USING fts5("
            "chunk_id UNINDEXED, document_id UNINDEXED, title, source UNINDEXED, text)"
        )
        conn.executemany(
            "INSERT INTO chunks(chunk_id, document_id, title, source, text) VALUES (?, ?, ?, ?, ?)",
            [
                (
                    chunk.chunk_id,
                    chunk.document_id,
                    str(chunk.metadata.get("title") or ""),
                    str(chunk.metadata.get("source") or ""),
                    chunk.text,
                )
                for chunk in chunks
            ],
        )
        conn.commit()
        return "ready"
    except sqlite3.Error as exc:
        if path.exists():
            path.unlink()
        return f"failed: {exc}"
    finally:
        if conn is not None:
            conn.close()


def _normalize_backend(value: str) -> str:
    text = str(value or "keyword").strip().lower()
    if text in {"keyword", "sqlite_fts", "hybrid"}:
        return text
    return "keyword"
