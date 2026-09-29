"""Bounded, read-only source lookup shared by editing and research design."""

import ast
import os
import re
from pathlib import Path
from typing import Any

from simple_ar.code_task.analysis.index import IGNORED_DIR_NAMES
from simple_ar.code_task.editing.planning import select_relevant_files


SOURCE_SUFFIXES = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".go", ".rs",
    ".c", ".cc", ".cpp", ".h", ".hpp", ".cs", ".rb", ".r", ".jl", ".sh",
}
CONTEXT_SUFFIXES = SOURCE_SUFFIXES | {".toml", ".yaml", ".yml", ".md", ".txt", ".json", ".ini", ".cfg"}


def _construction_call_positions(text: str, symbols: list[str], query: str) -> list[int]:
    """Prefer a named Python call site when the question asks how it is built."""
    if not re.search(r"\b(?:build|construct|instantiat|creat|call.?site)", query.lower()):
        return []
    names = list(dict.fromkeys(
        symbol.rsplit(".", 1)[0] if symbol.endswith(".__init__") else symbol.rsplit(".", 1)[-1]
        for symbol in symbols if symbol
    ))
    if not names:
        return []
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    offsets = [0]
    for line in text.splitlines(keepends=True):
        offsets.append(offsets[-1] + len(line))
    calls: dict[str, list[int]] = {name: [] for name in names}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""
        if name in calls and 1 <= node.lineno < len(offsets):
            calls[name].append(offsets[node.lineno - 1])
    return [position for name in names for position in sorted(calls[name])]


def _unfinished_python_symbol_starts(
    text: str, symbols: list[str], seen: list[tuple[int, int]],
) -> list[int]:
    """Continue a named definition whose signature was read but body was clipped.

    A later call site is not a substitute for the unseen body of the requested
    method. Only definitions named by the caller qualify; unrelated truncated
    functions do not consume another source window.
    """
    if not symbols or not seen:
        return []
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    offsets = [0]
    for line in text.splitlines(keepends=True):
        offsets.append(offsets[-1] + len(line))
    wanted = set(symbols)
    starts: list[int] = []

    def visit(nodes: list[ast.stmt], parents: tuple[str, ...] = ()) -> None:
        for node in nodes:
            if not isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            qualified = ".".join((*parents, node.name))
            if qualified in wanted or (not parents and node.name in wanted):
                begin = offsets[node.lineno - 1]
                end = offsets[min(node.end_lineno or node.lineno, len(offsets) - 1)]
                for lo, hi in seen:
                    if lo <= begin < hi < end:
                        starts.append(hi)
            visit(node.body, (*parents, node.name))

    visit(tree.body)
    return list(dict.fromkeys(starts))


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
            suffix = path.suffix.lower()
            if name.startswith(".env") or suffix not in CONTEXT_SUFFIXES:
                continue
            files.append({"path": path.relative_to(workspace).as_posix(),
                          "kind": "python" if suffix == ".py" else "text",
                          "role_tags": ["source"] if suffix in SOURCE_SUFFIXES else ["config"]})
        if scanned > 10000:
            break
    # The bounded inventory must not let configuration files crowd out source
    # code, regardless of the project's implementation language.
    files.sort(key=lambda row: ("source" not in row["role_tags"], row["path"]))
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
                             max_total_chars: int | None = None,
                             max_windows_per_file: int = 1) -> list[dict[str, Any]]:
    """Read unseen source windows without changing edit scope or cached source.

    ``max_windows_per_file`` defaults to the historical single window. Editors
    may spend a small, explicit extra window budget on a truncated target; the
    total character and result-count limits still apply.
    """
    if max_windows_per_file < 1:
        raise ValueError("max_windows_per_file must be positive.")
    literal = request.get("literal", "")
    if not isinstance(literal, str) or len(literal) > 200:
        raise ValueError("Source literal must be a string of at most 200 characters.")
    line_range = request.get("line_range")
    if line_range is not None:
        if (not isinstance(line_range, dict) or set(line_range) != {"start", "end"}
                or any(type(line_range[key]) is not int for key in ("start", "end"))
                or line_range["start"] < 1 or line_range["end"] < line_range["start"]
                or line_range["end"] - line_range["start"] >= 200
                or len(request.get("files", [])) != 1
                or literal or request.get("query") or request.get("symbols")):
            raise ValueError("A source line_range needs one file, 1-200 lines, and no search terms.")
    query = " ".join([request.get("query", ""), *request.get("symbols", []), literal]).strip()
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
                   if len(term) >= 5 and term.lower() not in {
                       "where", "which", "about", "their", "these", "those", "current",
                       "currently", "happen", "implementation", "source", "model", "method",
                   }]
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
        if line_range is not None:
            lines = text.splitlines(keepends=True)
            if line_range["start"] > len(lines):
                continue
            start = sum(len(line) for line in lines[:line_range["start"] - 1])
            end = sum(len(line) for line in lines[:min(line_range["end"], len(lines))])
            for lo, hi in sorted(seen):
                if lo <= start < hi:
                    start = hi
                elif lo > start:
                    end = min(end, lo)
                    break
            if start < end and size > 0:
                excerpt = text[start:min(end, start + size)]
                result.append({"path": relative, "access_role": "read_only", "text": excerpt,
                               "source_offset": start,
                               "start_line": text.count("\n", 0, start) + 1,
                               "end_line": text.count("\n", 0, start + len(excerpt) - 1) + 1,
                               "truncated": start > 0 or start + len(excerpt) < len(text)})
            break
        def positions_for(terms: list[str]) -> list[int]:
            # Preserve the caller's term priority rather than selecting the
            # earliest generic mention anywhere in the file.
            return list(dict.fromkeys(match.start() for term in terms if term
                for match in re.finditer(re.escape(term), text, flags=re.IGNORECASE)))

        literal_positions = ([match.start() for match in re.finditer(re.escape(literal), text)]
                             if literal else [])
        call_positions = _construction_call_positions(text, request.get("symbols", []), request.get("query", "")) if path.suffix == ".py" and not literal else []
        symbol_positions = positions_for(symbols) if not literal else []
        query_positions = positions_for(query_terms) if not literal else []
        positions = literal_positions + call_positions + query_positions + symbol_positions
        unseen = lambda items: [pos for pos in items if not any(lo <= pos < hi for lo, hi in seen)]
        if literal and not literal_positions:
            continue
        # The query expresses the requested behavior; named symbols can be
        # implementation helpers or imports that appear far from that behavior.
        # Exact matches often name a use site; include its preceding setup and
        # call arguments as well as the continuation after it.
        starts = [max(0, min(pos - size // 2, len(text) - size)) for pos in unseen(literal_positions)]
        if not literal and path.suffix == ".py":
            starts += _unfinished_python_symbol_starts(text, request.get("symbols", []), seen)
        starts += [max(0, min(pos - size // 2, len(text) - size)) for pos in unseen(call_positions)]
        starts += [max(0, min(pos - size // 4, len(text) - size)) for pos in unseen(query_positions)]
        starts += [max(0, min(pos - size // 4, len(text) - size)) for pos in unseen(symbol_positions)]
        if not positions and not symbols and not query_terms:
            first = min(max((hi for _, hi in seen), default=0), max(0, len(text) - size))
            starts = [min(first + offset * size, max(0, len(text) - size))
                      for offset in range(max_windows_per_file)]
        elif positions and not starts and not literal:
            # A requested method can start in an already supplied window but
            # continue beyond its clipped end. Return one adjacent window.
            starts = [min(hi, max(0, len(text) - size)) for lo, hi in seen
                      if hi < len(text) and any(lo <= pos < hi and pos >= hi - size // 3 for pos in positions)]
        if max_windows_per_file > 1 and starts and len(text) > size:
            # A symbol hit near the start of a long function is not the whole
            # behavior. When the caller explicitly budgets another window,
            # inspect the adjacent continuation before unrelated later hits.
            continuation = min(starts[0] + size, len(text) - size)
            if continuation > starts[0] and continuation not in starts:
                starts.insert(1, continuation)
        file_windows = 0
        for start in starts:
            if file_windows >= max_windows_per_file or len(result) >= max_files or remaining <= 0:
                break
            excerpt = text[start:start + min(size, remaining)]
            novel = sum(not any(lo <= pos < hi for lo, hi in seen)
                        for pos in range(start, start + len(excerpt)))
            required_novel = min(1500, max(1, len(excerpt) // 4))
            if start + len(excerpt) == len(text):
                required_novel = min(required_novel, 256)
            if novel < required_novel:
                continue
            result.append({"path": relative, "access_role": "read_only", "text": excerpt,
                           "source_offset": start,
                           "start_line": text.count("\n", 0, start) + 1,
                           "end_line": text.count("\n", 0, start + len(excerpt) - 1) + 1,
                           "truncated": start > 0 or start + len(excerpt) < len(text)})
            seen.append((start, start + len(excerpt)))
            remaining -= len(excerpt)
            file_windows += 1
        if len(result) >= max_files:
            break
    return result
