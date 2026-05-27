from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from simple_ar.artifacts import write_jsonl
from simple_ar.retrieval.index import build_artifact_index


MAX_CHUNK_CHARS = 4000
TEXT_WINDOW_LINES = 40
HEADING_RE = re.compile(r"^(?P<marks>#{1,6})\s+(?P<title>.+?)\s*$")
OPERATIONAL_FILENAMES = {
    "activity_log.jsonl",
    "artifact_chunks.jsonl",
    "artifact_index.json",
    "artifact_search_results.json",
    "cache_manifest.json",
    "fulltext_extraction.json",
    "fulltext_manifest.json",
    "claim_cards.jsonl",
    "chunks.jsonl",
    "config_snapshot.json",
    "documents.jsonl",
    "evidence_ledger.jsonl",
    "llm_usage.jsonl",
    "llm_usage_summary.json",
    "manifest.json",
    "pipeline_state.json",
    "paper_cards.jsonl",
    "research_plan.json",
    "index_meta.json",
    "retrieval_rounds.jsonl",
    "screening_decisions.jsonl",
    "source_plan.json",
    "stage_meta.json",
    "coverage_report.json",
}


@dataclass(frozen=True)
class ArtifactChunk:
    """A searchable text span with file provenance.

    Args:
        path: POSIX-style path relative to the indexed root.
        kind: Coarse file kind such as ``markdown`` or ``python``.
        chunk_kind: More specific chunk source such as ``markdown-section``.
        line_start: One-based inclusive start line.
        line_end: One-based inclusive end line.
        text: Chunk text used for lexical search and LLM snippets.
        title: Optional section, function, class, key, or row label.
    """

    path: str
    kind: str
    chunk_kind: str
    line_start: int
    line_end: int
    text: str
    title: str | None = None

    def to_row(self) -> dict[str, Any]:
        """Return a JSONL-friendly representation of the chunk."""
        return {
            "path": self.path,
            "kind": self.kind,
            "chunk_kind": self.chunk_kind,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "title": self.title,
            "text": self.text,
        }


def build_artifact_chunks(
    run_dir: Path,
    *,
    index: dict[str, Any] | None = None,
    write: bool = True,
    include_operational: bool = False,
) -> list[ArtifactChunk]:
    """Chunk indexed artifacts into line-addressable searchable spans.

    Args:
        run_dir: Root run directory.
        index: Optional prebuilt artifact index. When omitted, a fresh index is
            created and saved first.
        write: When true, save chunks to ``artifact_chunks.jsonl``.
        include_operational: Include runner metadata such as manifests and
            ``stage_meta.json``. Keep this false for evidence retrieval.

    Returns:
        Ordered chunks for supported source artifacts.
    """
    root = Path(run_dir)
    artifact_index = index or build_artifact_index(root)
    chunks: list[ArtifactChunk] = []
    for artifact in artifact_index.get("artifacts", []):
        if not isinstance(artifact, dict):
            continue
        rel_path = str(artifact.get("path", ""))
        kind = str(artifact.get("kind", "other"))
        if not rel_path or kind == "other":
            continue
        if not include_operational and is_operational_artifact(rel_path):
            continue
        path = root / rel_path
        if not path.is_file():
            continue
        chunks.extend(chunk_file(path, rel_path=rel_path, kind=kind))

    if write:
        write_jsonl(root / "artifact_chunks.jsonl", [chunk.to_row() for chunk in chunks])
    return chunks


def is_operational_artifact(relative_path: str) -> bool:
    """Return whether a run artifact is pipeline bookkeeping, not source evidence."""
    name = Path(relative_path).name
    if name in OPERATIONAL_FILENAMES:
        return True
    if relative_path.startswith(".simple_ar_cache/"):
        return True
    return False


def chunk_file(path: Path, *, rel_path: str, kind: str) -> list[ArtifactChunk]:
    """Chunk one file according to its artifact kind."""
    lines = _read_lines(path)
    if not lines:
        return []
    if kind == "markdown":
        return _chunk_markdown(rel_path, kind, lines)
    if kind == "json":
        return _chunk_json(path, rel_path, kind, lines)
    if kind == "jsonl":
        return _chunk_jsonl(rel_path, kind, lines)
    if kind == "python":
        return _chunk_python(path, rel_path, kind, lines)
    return _chunk_text_windows(rel_path, kind, lines, chunk_kind="text-window")


def _chunk_markdown(rel_path: str, kind: str, lines: list[str]) -> list[ArtifactChunk]:
    heading_rows: list[tuple[int, str]] = []
    for index, line in enumerate(lines, start=1):
        match = HEADING_RE.match(line)
        if match:
            heading_rows.append((index, match.group("title").strip()))

    if not heading_rows:
        return _chunk_text_windows(rel_path, kind, lines, chunk_kind="markdown-window")

    chunks: list[ArtifactChunk] = []
    if heading_rows[0][0] > 1:
        chunks.append(
            _make_chunk(
                rel_path,
                kind,
                "markdown-preamble",
                1,
                heading_rows[0][0] - 1,
                lines,
                title="preamble",
            )
        )

    for idx, (start, title) in enumerate(heading_rows):
        end = heading_rows[idx + 1][0] - 1 if idx + 1 < len(heading_rows) else len(lines)
        chunks.append(
            _make_chunk(
                rel_path,
                kind,
                "markdown-section",
                start,
                end,
                lines,
                title=title,
            )
        )
    return [chunk for chunk in chunks if chunk.text.strip()]


def _chunk_json(path: Path, rel_path: str, kind: str, lines: list[str]) -> list[ArtifactChunk]:
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except json.JSONDecodeError:
        return _chunk_text_windows(rel_path, kind, lines, chunk_kind="json-text-window")

    if isinstance(data, dict):
        chunks: list[ArtifactChunk] = []
        for key, value in data.items():
            line = _find_json_key_line(lines, str(key)) or 1
            text = json.dumps({key: value}, ensure_ascii=False, indent=2)
            chunks.append(
                ArtifactChunk(
                    path=rel_path,
                    kind=kind,
                    chunk_kind="json-key",
                    line_start=line,
                    line_end=line,
                    title=str(key),
                    text=_truncate(text),
                )
            )
        return chunks

    if isinstance(data, list):
        chunks = []
        for index, item in enumerate(data, start=1):
            text = json.dumps(item, ensure_ascii=False, indent=2)
            chunks.append(
                ArtifactChunk(
                    path=rel_path,
                    kind=kind,
                    chunk_kind="json-list-item",
                    line_start=1,
                    line_end=len(lines),
                    title=f"item {index}",
                    text=_truncate(text),
                )
            )
        return chunks

    return [
        ArtifactChunk(
            path=rel_path,
            kind=kind,
            chunk_kind="json-value",
            line_start=1,
            line_end=len(lines),
            title=type(data).__name__,
            text=_truncate(json.dumps(data, ensure_ascii=False, indent=2)),
        )
    ]


def _chunk_jsonl(rel_path: str, kind: str, lines: list[str]) -> list[ArtifactChunk]:
    chunks: list[ArtifactChunk] = []
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            data = json.loads(line)
            text = json.dumps(data, ensure_ascii=False, indent=2)
        except json.JSONDecodeError:
            text = line
        chunks.append(
            ArtifactChunk(
                path=rel_path,
                kind=kind,
                chunk_kind="jsonl-row",
                line_start=line_number,
                line_end=line_number,
                title=f"row {line_number}",
                text=_truncate(text),
            )
        )
    return chunks


def _chunk_python(path: Path, rel_path: str, kind: str, lines: list[str]) -> list[ArtifactChunk]:
    text = "\n".join(lines)
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return _chunk_text_windows(rel_path, kind, lines, chunk_kind="python-syntax-fallback")

    chunks: list[ArtifactChunk] = []
    import_lines = [
        node.lineno
        for node in tree.body
        if isinstance(node, ast.Import | ast.ImportFrom)
    ]
    if import_lines:
        chunks.append(
            _make_chunk(
                rel_path,
                kind,
                "python-imports",
                min(import_lines),
                max(import_lines),
                lines,
                title="imports",
            )
        )

    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            chunks.append(
                _python_node_chunk(rel_path, kind, "python-class", node.name, node, lines)
            )
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            chunks.append(
                _python_node_chunk(rel_path, kind, "python-function", node.name, node, lines)
            )

    if not chunks:
        return _chunk_text_windows(rel_path, kind, lines, chunk_kind="python-window")
    return [chunk for chunk in chunks if chunk.text.strip()]


def _python_node_chunk(
    rel_path: str,
    kind: str,
    chunk_kind: str,
    title: str,
    node: ast.AST,
    lines: list[str],
) -> ArtifactChunk:
    start = int(getattr(node, "lineno", 1))
    end = int(getattr(node, "end_lineno", start))
    return _make_chunk(rel_path, kind, chunk_kind, start, end, lines, title=title)


def _chunk_text_windows(
    rel_path: str,
    kind: str,
    lines: list[str],
    *,
    chunk_kind: str,
) -> list[ArtifactChunk]:
    chunks: list[ArtifactChunk] = []
    for start in range(1, len(lines) + 1, TEXT_WINDOW_LINES):
        end = min(start + TEXT_WINDOW_LINES - 1, len(lines))
        chunks.append(_make_chunk(rel_path, kind, chunk_kind, start, end, lines))
    return [chunk for chunk in chunks if chunk.text.strip()]


def _make_chunk(
    rel_path: str,
    kind: str,
    chunk_kind: str,
    line_start: int,
    line_end: int,
    lines: list[str],
    *,
    title: str | None = None,
) -> ArtifactChunk:
    text = "\n".join(lines[line_start - 1 : line_end])
    return ArtifactChunk(
        path=rel_path,
        kind=kind,
        chunk_kind=chunk_kind,
        line_start=line_start,
        line_end=line_end,
        title=title,
        text=_truncate(text),
    )


def _find_json_key_line(lines: list[str], key: str) -> int | None:
    needle = json.dumps(key, ensure_ascii=False)
    for line_number, line in enumerate(lines, start=1):
        if needle in line:
            return line_number
    return None


def _read_lines(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8", errors="replace")
    return text.splitlines()


def _truncate(text: str, max_chars: int = MAX_CHUNK_CHARS) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3].rstrip() + "..."
