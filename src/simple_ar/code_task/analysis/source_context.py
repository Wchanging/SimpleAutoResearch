"""Bounded, read-only source lookup shared by editing and research design."""

import os
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
    candidates = [path for path in request.get("files", []) if path in known]
    if query:
        candidates.extend(select_relevant_files(index, query, max_files=max_files))
    previous = {item["path"]: item["text"] for item in supplied}
    terms = [symbol.rsplit(".", 1)[-1] for symbol in request.get("symbols", [])] or query.split()
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
        start = 0
        if len(text) > max_chars:
            matches = [text.find(term) for term in terms if term and text.find(term) >= 0]
            if matches:
                start = max(0, matches[0] - max_chars // 4)
            elif relative in previous:
                start = max(0, min(len(previous[relative]), len(text) - max_chars))
        excerpt = text[start:start + min(max_chars, remaining)]
        if excerpt and excerpt not in previous.get(relative, ""):
            result.append({"path": relative, "access_role": "read_only", "text": excerpt,
                           "source_offset": start, "truncated": start > 0 or start + len(excerpt) < len(text)})
            remaining -= len(excerpt)
        if len(result) >= max_files:
            break
    return result
