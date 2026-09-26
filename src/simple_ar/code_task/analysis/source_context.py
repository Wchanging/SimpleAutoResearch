"""Bounded, read-only source lookup shared by editing and research design."""

from pathlib import Path
from typing import Any

from simple_ar.code_task.editing.planning import select_relevant_files


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
