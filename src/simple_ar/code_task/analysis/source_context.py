"""Bounded, read-only source lookup shared by editing and research design."""

import os
import re
from pathlib import Path
from typing import Any

from simple_ar.code_task.analysis.index import IGNORED_DIR_NAMES
from simple_ar.code_task.editing.planning import select_relevant_files


def source_file_inventory(
    workspace: Path, *, max_files: int = 400, required_paths: tuple[str, ...] = (),
) -> dict[str, Any]:
    """List candidate source files for design lookup without hashing dataset assets."""
    files: list[dict[str, Any]] = []
    scanned = 0
    for current, dirnames, filenames in os.walk(workspace):
        dirnames[:] = sorted(
            (name for name in dirnames if name not in IGNORED_DIR_NAMES and not name.startswith(".")),
            key=lambda name: (name.lower() in {"data", "datasets", "outputs", "runs", "results", "artifacts"}, name),
        )
        for name in sorted(filenames):
            scanned += 1
            if scanned > 10000:
                break
            path = Path(current) / name
            if name.startswith(".env") or path.suffix.lower() not in {
                ".py", ".toml", ".yaml", ".yml", ".md", ".txt", ".json",
            }:
                continue
            files.append({"path": path.relative_to(workspace).as_posix(),
                          "kind": "python" if path.suffix.lower() == ".py" else "text",
                          "role_tags": ["source"] if path.suffix.lower() == ".py" else ["config"]})
        if scanned > 10000:
            break
    files.sort(key=lambda row: (row["kind"] != "python", row["path"]))
    selected = files[:max_files]
    # An explicitly declared active experiment config must not disappear in a
    # large Python project merely because the generic inventory is capped.
    root = workspace.resolve()
    known = {row["path"] for row in selected}
    for relative in required_paths[:8]:
        rel = Path(relative)
        path = (root / rel).resolve()
        normalized = rel.as_posix()
        if (rel.is_absolute() or ".." in rel.parts or path.name.startswith(".env")
                or not path.is_relative_to(root) or not path.is_file()
                or path.suffix.lower() not in {".toml", ".yaml", ".yml", ".json", ".ini", ".txt"}
                or path.stat().st_size > 500_000 or normalized in known):
            continue
        selected.append({"path": normalized, "kind": "text", "role_tags": ["config"]})
        known.add(normalized)
    return {"files": selected}


def requested_source_context(workspace: Path, index: dict[str, Any], request: dict[str, Any],
                             *, supplied: list[dict[str, str]], max_files: int, max_chars: int,
                             max_total_chars: int | None = None) -> list[dict[str, Any]]:
    query = " ".join([request.get("query", ""), *request.get("symbols", [])]).strip()
    known = {str(item["path"]) for item in index.get("files", [])}
    requested_files = request.get("files", [])
    candidates = [path for path in requested_files if path in known]
    if not requested_files and query:
        candidates.extend(select_relevant_files(index, query, max_files=max_files))
    previous: dict[str, list[tuple[int, int]]] = {}
    for item in supplied:
        start = int(item.get("source_offset", 0))
        previous.setdefault(item["path"], []).append((start, start + len(item["text"])))
    symbols = [symbol.rsplit(".", 1)[-1] for symbol in request.get("symbols", [])]
    query_terms = [term for term in re.findall(r"[A-Za-z_][A-Za-z_0-9]*", request.get("query", ""))
                   if len(term) >= 5 and term.lower() not in {"where", "which", "about", "their", "these", "those"}]
    result = []
    remaining = max_total_chars if max_total_chars is not None else max_files * max_chars
    workspace = workspace.resolve()
    for relative in dict.fromkeys(candidates):
        if remaining <= 0:
            break
        rel = Path(relative)
        path = (workspace / rel).resolve()
        if (rel.is_absolute() or ".." in rel.parts or not path.is_relative_to(workspace)
                or not path.is_file() or path.name.startswith(".env")):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        size = min(max_chars, remaining)
        seen = previous.get(relative, [])
        def positions_for(terms: list[str]) -> list[int]:
            return sorted({match.start() for term in terms if term
                           for match in re.finditer(re.escape(term), text, flags=re.IGNORECASE)})

        symbol_positions = positions_for(symbols)
        query_positions = positions_for(query_terms)
        positions = symbol_positions + query_positions
        novel_positions = [pos for pos in symbol_positions if not any(lo <= pos < hi for lo, hi in seen)]
        if not novel_positions:
            novel_positions = [pos for pos in query_positions if not any(lo <= pos < hi for lo, hi in seen)]
        starts = [max(0, min(pos - size // 4, len(text) - size)) for pos in novel_positions]
        if not positions and not symbols and not query_terms:
            starts = [min(max((hi for _, hi in seen), default=0), max(0, len(text) - size))]
        elif positions and not starts:
            # A requested method can start in an already supplied window but
            # continue beyond its clipped end. Return one adjacent window.
            starts = [min(hi, max(0, len(text) - size)) for lo, hi in seen
                      if hi < len(text) and any(lo <= pos < hi and pos >= hi - size // 3 for pos in positions)]
        for start in starts:
            excerpt = text[start:start + size]
            novel = sum(not any(lo <= pos < hi for lo, hi in seen)
                        for pos in range(start, start + len(excerpt)))
            if novel < min(256, max(1, len(excerpt) // 4)):
                continue
            result.append({"path": relative, "access_role": "read_only", "text": excerpt,
                           "source_offset": start, "truncated": start > 0 or start + len(excerpt) < len(text)})
            remaining -= len(excerpt)
            break
        if len(result) >= max_files:
            break
    return result
