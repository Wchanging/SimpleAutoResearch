from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any

from simple_ar.core.artifacts import write_json, write_jsonl
from simple_ar.research.contracts import TextChunk


SQLITE_INDEX_NAME = "sqlite_fts.db"
DEFAULT_SHARED_INDEX_ROOT = ".simple_ar_cache/research_index"


def write_research_index(
    *,
    index_dir: Path,
    chunks: list[TextChunk],
    backend: str,
    run_id: str | None = None,
    shared_root: str | Path | None = None,
) -> dict[str, Any]:
    """Write portable chunks and update optional shared research indexes.

    Args:
        index_dir: Destination directory under the search stage for run-local
            portable artifacts.
        chunks: Text chunks to index.
        backend: Configured backend: ``keyword``, ``sqlite_fts``, ``hybrid``, or
            future adapter names.
        run_id: Stable run identifier used to replace this run's rows in shared
            indexes.
        shared_root: Shared index root. ``None`` uses
            ``SIMPLE_AR_RESEARCH_INDEX_ROOT`` or
            ``.simple_ar_cache/research_index``. Use ``"run"`` or ``"local"``
            to keep accelerators under ``index_dir``.

    Returns:
        Index metadata written to ``index_meta.json``.
    """
    normalized_backend = _normalize_backend(backend)
    run_id = _normalize_run_id(run_id or _infer_run_id(index_dir))
    store_root = _shared_store_root(shared_root)
    store_scope = "shared" if store_root is not None else "run"
    index_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(index_dir / "chunks.jsonl", [chunk.to_row() for chunk in chunks])
    sqlite_status = "not_requested"
    sqlite_path: str | None = None
    if normalized_backend in {"sqlite_fts", "hybrid"}:
        sqlite_file = (store_root or index_dir) / SQLITE_INDEX_NAME
        sqlite_status = _write_sqlite_fts(sqlite_file, chunks, run_id=run_id)
        sqlite_path = str(sqlite_file)
    lancedb_status = "not_requested"
    lancedb_path: str | None = None
    if normalized_backend in {"lancedb", "hybrid_lancedb"}:
        lancedb_dir = (store_root or index_dir) / "lancedb"
        lancedb_path = str(lancedb_dir)
        lancedb_status = _write_lancedb(lancedb_dir, chunks, run_id=run_id)
    meta = {
        "schema_version": "research_index_meta.v1",
        "backend": normalized_backend,
        "chunk_count": len(chunks),
        "store": {
            "scope": store_scope,
            "run_id": run_id,
            "root": str(store_root) if store_root is not None else str(index_dir),
            "portable_chunks": "chunks.jsonl",
        },
        "sqlite_fts": {
            "status": sqlite_status,
            "path": sqlite_path,
            "scope": store_scope if sqlite_path else "not_requested",
        },
        "lancedb": {
            "status": lancedb_status,
            "path": lancedb_path,
            "scope": store_scope if lancedb_path else "not_requested",
        },
        "notes": [
            "chunks.jsonl is the portable source of truth.",
            "SQLite FTS and LanceDB are shared acceleration layers by default, keyed by run_id.",
            "Set [research].index_root to 'run' or 'local' when per-run accelerator stores are required.",
        ],
    }
    write_json(index_dir / "index_meta.json", meta)
    return meta


def _write_sqlite_fts(path: Path, chunks: list[TextChunk], *, run_id: str) -> str:
    conn: sqlite3.Connection | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path)
        conn.execute("PRAGMA journal_mode=DELETE")
        conn.execute("PRAGMA synchronous=OFF")
        _ensure_sqlite_chunks_table(conn)
        conn.execute("DELETE FROM chunks WHERE run_id = ?", (run_id,))
        conn.executemany(
            "INSERT INTO chunks(run_id, chunk_id, document_id, title, source, text) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [
                (
                    run_id,
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
        return f"failed: {exc}"
    finally:
        if conn is not None:
            conn.close()


def _ensure_sqlite_chunks_table(conn: sqlite3.Connection) -> None:
    columns = [str(row[1]) for row in conn.execute("PRAGMA table_info(chunks)").fetchall()]
    if columns and "run_id" not in columns:
        conn.execute(
            "DROP TABLE IF EXISTS chunks"
        )
    conn.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS chunks USING fts5("
        "run_id UNINDEXED, chunk_id UNINDEXED, document_id UNINDEXED, "
        "title, source UNINDEXED, text)"
    )


def _write_lancedb(path: Path, chunks: list[TextChunk], *, run_id: str) -> str:
    try:
        import lancedb  # type: ignore[import-not-found]
    except ModuleNotFoundError:
        return "failed: lancedb is not installed"
    try:
        path.mkdir(parents=True, exist_ok=True)
        db = lancedb.connect(str(path))
        rows = [
            {
                "run_id": run_id,
                "chunk_id": chunk.chunk_id,
                "document_id": chunk.document_id,
                "title": str(chunk.metadata.get("title") or ""),
                "source": str(chunk.metadata.get("source") or ""),
                "text": chunk.text,
            }
            for chunk in chunks
        ]
        if not rows:
            return "ready"
        try:
            table = db.open_table("chunks")
            table.delete(f"run_id = '{_lancedb_literal(run_id)}'")
            table.add(rows)
        except Exception:
            db.create_table("chunks", rows, mode="overwrite")
        return "ready"
    except Exception as exc:  # pragma: no cover - backend behavior varies by version/platform.
        return f"failed: {exc}"


def _normalize_backend(value: str) -> str:
    text = str(value or "keyword").strip().lower()
    if text in {"keyword", "sqlite_fts", "hybrid", "lancedb", "hybrid_lancedb"}:
        return text
    return "keyword"


def _shared_store_root(value: str | Path | None) -> Path | None:
    if value is not None:
        text = str(value).strip()
        if text.lower() in {"run", "local"}:
            return None
        if text:
            return Path(text)
    env_value = os.environ.get("SIMPLE_AR_RESEARCH_INDEX_ROOT", "").strip()
    return Path(env_value or DEFAULT_SHARED_INDEX_ROOT)


def _infer_run_id(index_dir: Path) -> str:
    try:
        if index_dir.parent.name == "02-search":
            return index_dir.parent.parent.name
    except IndexError:
        pass
    return index_dir.parent.name or "unknown-run"


def _normalize_run_id(value: str) -> str:
    text = "".join(char if char.isalnum() or char in {"-", "_", "."} else "-" for char in value.strip())
    return text.strip("-") or "unknown-run"


def _lancedb_literal(value: str) -> str:
    return value.replace("'", "''")
