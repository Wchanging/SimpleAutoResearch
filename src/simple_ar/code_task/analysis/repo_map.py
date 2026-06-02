from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any, Iterable

from simple_ar.core.artifacts import read_json, write_json, write_text
from simple_ar.code_task.editing.scope import (
    DEFAULT_ALLOWED_EDIT_PATTERNS,
    DEFAULT_PROTECTED_EDIT_PATTERNS,
    allowed_patterns_from_manifest,
    is_edit_allowed_path,
    protected_patterns_from_manifest,
)
from simple_ar.code_task.analysis.index import build_codebase_index
from simple_ar.code_task.runtime.state import (
    code_task_paths,
    load_code_task_manifest,
    save_code_task_manifest,
)


DEFAULT_REPO_MAP_BUDGET = {
    "max_prompt_summary_chars": 12_000,
    "max_prompt_files": 24,
    "max_prompt_symbols": 120,
    "max_chars_per_file": 3_000,
}


@dataclass(frozen=True)
class RepoMapBuildResult:
    """Result returned after building repo-map artifacts for a code-task run.

    Args:
        run_dir: Code-task run directory.
        repo_map_path: Path to ``code_task/meta/repo_map.json``.
        summary_path: Path to ``code_task/meta/repo_map_summary.md``.
        codebase_index_path: Path to the source ``codebase_index.json``.
        repo_map: Generated layered repository map.
        refreshed_index: Whether the codebase index was rebuilt from the
            current workspace before creating the repo map.
    """

    run_dir: Path
    repo_map_path: Path
    summary_path: Path
    codebase_index_path: Path
    repo_map: dict[str, Any]
    refreshed_index: bool


def build_repo_map(
    codebase_index: dict[str, Any],
    *,
    output_path: Any | None = None,
    summary_path: Any | None = None,
    allowed_patterns: Iterable[str] | None = None,
    protected_patterns: Iterable[str] | None = None,
    budget: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Build a layered repository map from a code-task codebase index.

    Args:
        codebase_index: Data produced by ``build_codebase_index``.
        output_path: Optional JSON destination for ``repo_map.json``.
        summary_path: Optional Markdown destination for ``repo_map_summary.md``.
        allowed_patterns: Optional edit allowlist. Empty means any
            non-protected workspace path can be editable.
        protected_patterns: Edit-scope patterns used to mark read-only evidence.
        budget: Optional prompt-rendering budget overrides for future context
            pack generation.

    Returns:
        JSON-serializable repo map with project, directory, file, symbol,
        entrypoint, test, benchmark, and config layers.
    """

    allowed = tuple(allowed_patterns or DEFAULT_ALLOWED_EDIT_PATTERNS)
    protected = tuple(protected_patterns or DEFAULT_PROTECTED_EDIT_PATTERNS)
    resolved_budget = {**DEFAULT_REPO_MAP_BUDGET, **(budget or {})}
    index_files = _index_files(codebase_index)
    files = _file_rows(
        index_files,
        allowed_patterns=allowed,
        protected_patterns=protected,
    )
    symbols = _symbol_rows(index_files, files)
    repo_map = {
        "schema_version": 1,
        "generated_at": _utcnow_iso(),
        "source": {
            "kind": "codebase_index",
            "schema_version": codebase_index.get("schema_version"),
            "generated_at": codebase_index.get("generated_at"),
            "workspace": codebase_index.get("workspace"),
        },
        "budget": resolved_budget,
        "project": _project_row(codebase_index, files, symbols),
        "directories": _directory_rows(files),
        "files": files,
        "symbols": symbols,
        "entrypoints": _entrypoint_rows(files, symbols),
        "tests": _test_rows(files, symbols),
        "benchmarks": _benchmark_rows(files, symbols),
        "configs": _config_rows(files),
    }
    if output_path is not None:
        write_json(output_path, repo_map)
    if summary_path is not None:
        write_text(summary_path, render_repo_map_summary(repo_map))
    return repo_map


def build_code_task_repo_map(
    run_dir: Path,
    *,
    refresh_index: bool = True,
) -> RepoMapBuildResult:
    """Build or rebuild repo-map artifacts for an initialized code-task run.

    Args:
        run_dir: Code-task run directory.
        refresh_index: Rebuild ``codebase_index.json`` from the current
            workspace before building ``repo_map.json``. Keep this enabled
            when workspace files may have changed.

    Returns:
        Paths and metadata for the repo-map artifacts.

    Raises:
        FileNotFoundError: If the run, workspace, or required index is missing.
        RuntimeError: If ``run_dir`` is not a code-task run.
    """

    root = Path(run_dir)
    paths = code_task_paths(root)
    manifest = load_code_task_manifest(root)
    if not paths.workspace_dir.exists():
        raise FileNotFoundError(f"Missing code-task workspace: {paths.workspace_dir}")
    if not paths.workspace_dir.is_dir():
        raise NotADirectoryError(f"Code-task workspace is not a directory: {paths.workspace_dir}")

    codebase_index_path = paths.meta_dir / "codebase_index.json"
    repo_map_path = paths.meta_dir / "repo_map.json"
    summary_path = paths.meta_dir / "repo_map_summary.md"
    if refresh_index or not codebase_index_path.exists():
        codebase_index = build_codebase_index(
            paths.workspace_dir,
            output_path=codebase_index_path,
        )
        refreshed = True
    else:
        data = read_json(codebase_index_path)
        if not isinstance(data, dict):
            raise RuntimeError(f"Expected JSON object in {codebase_index_path}")
        codebase_index = data
        refreshed = False

    repo_map = build_repo_map(
        codebase_index,
        output_path=repo_map_path,
        summary_path=summary_path,
        allowed_patterns=allowed_patterns_from_manifest(manifest),
        protected_patterns=protected_patterns_from_manifest(manifest),
    )
    _update_manifest_repo_map(root, manifest, repo_map)
    return RepoMapBuildResult(
        run_dir=root,
        repo_map_path=repo_map_path,
        summary_path=summary_path,
        codebase_index_path=codebase_index_path,
        repo_map=repo_map,
        refreshed_index=refreshed,
    )


def render_repo_map_summary(repo_map: dict[str, Any]) -> str:
    """Render a compact human-readable summary for a repo map."""

    project = _object_dict(repo_map.get("project"))
    directories = _object_list(repo_map.get("directories"))
    files = _object_list(repo_map.get("files"))
    entrypoints = _object_list(repo_map.get("entrypoints"))
    tests = _object_list(repo_map.get("tests"))
    benchmarks = _object_list(repo_map.get("benchmarks"))
    configs = _object_list(repo_map.get("configs"))
    budget = _object_dict(repo_map.get("budget"))
    lines = [
        "# Repo Map Summary",
        "",
        f"Generated: `{repo_map.get('generated_at', '')}`",
        "",
        "## Project",
        "",
        f"- Files: `{project.get('file_count', 0)}`",
        f"- Python files: `{project.get('python_file_count', 0)}`",
        f"- Directories: `{project.get('directory_count', 0)}`",
        f"- Symbols: `{project.get('symbol_count', 0)}`",
        f"- Tests: `{project.get('test_file_count', 0)}`",
        f"- Benchmarks: `{project.get('benchmark_file_count', 0)}`",
        f"- Config files: `{project.get('config_file_count', 0)}`",
        f"- Total bytes: `{project.get('total_bytes', 0)}`",
        "",
        "## Entrypoints",
        "",
        _entrypoint_markdown(entrypoints),
        "",
        "## Tests And Benchmarks",
        "",
        _path_list_markdown("Tests", tests),
        "",
        _path_list_markdown("Benchmarks", benchmarks),
        "",
        "## Config Files",
        "",
        _path_list_markdown("Configs", configs),
        "",
        "## Top Directories",
        "",
        _directory_markdown(directories),
        "",
        "## Files",
        "",
        _file_markdown(files),
        "",
        "## Prompt Budget",
        "",
        f"- Max prompt summary chars: `{budget.get('max_prompt_summary_chars', '')}`",
        f"- Max prompt files: `{budget.get('max_prompt_files', '')}`",
        f"- Max prompt symbols: `{budget.get('max_prompt_symbols', '')}`",
        f"- Max chars per file: `{budget.get('max_chars_per_file', '')}`",
        "",
    ]
    return "\n".join(lines)


def _update_manifest_repo_map(
    run_dir: Path,
    manifest: dict[str, Any],
    repo_map: dict[str, Any],
) -> None:
    project = _object_dict(repo_map.get("project"))
    layout = manifest.get("layout")
    if not isinstance(layout, dict):
        layout = {}
    layout.update(
        {
            "codebase_index": "code_task/meta/codebase_index.json",
            "repo_map": "code_task/meta/repo_map.json",
            "repo_map_summary": "code_task/meta/repo_map_summary.md",
        }
    )
    codebase = manifest.get("codebase")
    if not isinstance(codebase, dict):
        codebase = {}
    codebase.update(
        {
            "file_count": project.get("file_count", 0),
            "python_file_count": project.get("python_file_count", 0),
            "test_file_count": project.get("test_file_count", 0),
            "entrypoint_candidates": project.get("entrypoint_candidates", []),
            "repo_map": _repo_map_manifest_summary(repo_map),
        }
    )
    manifest["layout"] = layout
    manifest["codebase"] = codebase
    save_code_task_manifest(run_dir, manifest)


def _repo_map_manifest_summary(repo_map: dict[str, Any]) -> dict[str, Any]:
    project = _object_dict(repo_map.get("project"))
    return {
        "schema_version": repo_map.get("schema_version"),
        "path": "code_task/meta/repo_map.json",
        "summary": "code_task/meta/repo_map_summary.md",
        "directory_count": project.get("directory_count", 0),
        "symbol_count": project.get("symbol_count", 0),
        "benchmark_file_count": project.get("benchmark_file_count", 0),
        "config_file_count": project.get("config_file_count", 0),
    }


def _file_rows(
    index_files: list[dict[str, Any]],
    *,
    allowed_patterns: tuple[str, ...],
    protected_patterns: tuple[str, ...],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in index_files:
        path = str(item.get("path", "")).strip()
        if not path:
            continue
        role_tags = _string_list(item.get("role_tags"))
        access_role = (
            "editable"
            if is_edit_allowed_path(
                path,
                allowed_patterns=allowed_patterns,
                protected_patterns=protected_patterns,
            )
            else "read_only_evidence"
        )
        row = {
            "path": path,
            "directory": _directory_for_path(path),
            "name": PurePosixPath(path).name,
            "kind": item.get("kind"),
            "bytes": int(item.get("bytes", 0) or 0),
            "sha256": item.get("sha256"),
            "role_tags": role_tags,
            "access_role": access_role,
            "summary": str(item.get("summary", "")),
            "symbol_count": _symbol_count_for_file(item),
            "imports": _imports_for_file(item),
            "has_main_guard": _has_main_guard(item),
        }
        rows.append(row)
    rows.sort(key=lambda row: str(row["path"]))
    return rows


def _symbol_rows(
    index_files: list[dict[str, Any]],
    files: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    file_by_path = {str(item.get("path", "")): item for item in files}
    for file_row in files:
        path = str(file_row["path"])
        source = file_by_path.get(path)
        source = next((item for item in index_files if str(item.get("path", "")) == path), source)
        python = source.get("python")
        if not isinstance(python, dict):
            continue
        for function in _object_list(python.get("functions")):
            name = str(function.get("name", ""))
            if not name:
                continue
            rows.append(
                _symbol_row(
                    path=path,
                    kind="function",
                    name=name,
                    parent="",
                    line_start=function.get("line_start"),
                    line_end=function.get("line_end"),
                    args=function.get("args", []),
                    is_test=function.get("is_test") is True,
                    access_role=str(file_row.get("access_role", "editable")),
                )
            )
        for klass in _object_list(python.get("classes")):
            class_name = str(klass.get("name", ""))
            if not class_name:
                continue
            rows.append(
                _symbol_row(
                    path=path,
                    kind="class",
                    name=class_name,
                    parent="",
                    line_start=klass.get("line_start"),
                    line_end=klass.get("line_end"),
                    args=[],
                    is_test=class_name.lower().startswith("test"),
                    access_role=str(file_row.get("access_role", "editable")),
                )
            )
            for method in _object_list(klass.get("methods")):
                method_name = str(method.get("name", ""))
                if not method_name:
                    continue
                rows.append(
                    _symbol_row(
                        path=path,
                        kind="method",
                        name=method_name,
                        parent=class_name,
                        line_start=method.get("line_start"),
                        line_end=method.get("line_end"),
                        args=method.get("args", []),
                        is_test=method.get("is_test") is True,
                        access_role=str(file_row.get("access_role", "editable")),
                    )
                )
    rows.sort(key=lambda row: (str(row["path"]), int(row.get("line_start") or 0), str(row["id"])))
    return rows


def _symbol_row(
    *,
    path: str,
    kind: str,
    name: str,
    parent: str,
    line_start: object,
    line_end: object,
    args: object,
    is_test: bool,
    access_role: str,
) -> dict[str, Any]:
    qualified_name = f"{parent}.{name}" if parent else name
    return {
        "id": f"{path}::{qualified_name}",
        "path": path,
        "kind": kind,
        "name": name,
        "qualified_name": qualified_name,
        "parent": parent,
        "line_start": line_start,
        "line_end": line_end,
        "args": _string_list(args),
        "is_test": is_test,
        "access_role": access_role,
    }


def _project_row(
    codebase_index: dict[str, Any],
    files: list[dict[str, Any]],
    symbols: list[dict[str, Any]],
) -> dict[str, Any]:
    project = codebase_index.get("project", {})
    project = project if isinstance(project, dict) else {}
    file_by_path = {str(item["path"]): item for item in files}
    role_counter: Counter[str] = Counter()
    for file_row in files:
        role_counter.update(str(tag) for tag in file_row.get("role_tags", []))
    directories = {_directory_for_path(str(file_row["path"])) for file_row in files}
    return {
        "file_count": len(files),
        "python_file_count": sum(1 for item in files if item.get("kind") == "python"),
        "test_file_count": sum(1 for item in files if "test" in item.get("role_tags", [])),
        "benchmark_file_count": sum(1 for item in files if _is_benchmark_path(str(item["path"]))),
        "config_file_count": sum(1 for item in files if "config" in item.get("role_tags", [])),
        "directory_count": len(directories),
        "symbol_count": len(symbols),
        "total_bytes": sum(int(item.get("bytes", 0)) for item in files),
        "top_level_entries": project.get("top_level_entries", []),
        "entrypoint_candidates": [
            path
            for path in _string_list(project.get("entrypoint_candidates", []))
            if "test" not in file_by_path.get(path, {}).get("role_tags", [])
        ],
        "common_imports": project.get("common_imports", []),
        "role_counts": dict(sorted(role_counter.items())),
    }


def _directory_rows(files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for file_row in files:
        path = str(file_row["path"])
        for directory in _ancestor_directories(path):
            groups[directory].append(file_row)

    rows: list[dict[str, Any]] = []
    for directory, items in groups.items():
        role_counter: Counter[str] = Counter()
        for item in items:
            role_counter.update(str(tag) for tag in item.get("role_tags", []))
        rows.append(
            {
                "path": directory,
                "file_count": len(items),
                "python_file_count": sum(1 for item in items if item.get("kind") == "python"),
                "test_file_count": sum(1 for item in items if "test" in item.get("role_tags", [])),
                "benchmark_file_count": sum(1 for item in items if _is_benchmark_path(str(item["path"]))),
                "config_file_count": sum(1 for item in items if "config" in item.get("role_tags", [])),
                "bytes": sum(int(item.get("bytes", 0)) for item in items),
                "role_tags": sorted(role_counter),
            }
        )
    rows.sort(key=lambda row: (0 if row["path"] == "." else 1, str(row["path"])))
    return rows


def _entrypoint_rows(files: list[dict[str, Any]], symbols: list[dict[str, Any]]) -> list[dict[str, Any]]:
    entrypoints: list[dict[str, Any]] = []
    for file_row in files:
        path = str(file_row["path"])
        role_tags = file_row.get("role_tags", [])
        reasons: list[str] = []
        if file_row.get("has_main_guard") is True and "test" not in role_tags:
            reasons.append("main_guard")
        if "entrypoint" in role_tags:
            reasons.append("entrypoint_name")
        if _is_benchmark_path(path):
            reasons.append("benchmark_file")
        if reasons:
            entrypoints.append(
                {
                    "path": path,
                    "kind": "file",
                    "reasons": reasons,
                    "access_role": file_row.get("access_role", "editable"),
                }
            )
    for symbol in symbols:
        if symbol.get("name") in {"main", "cli"}:
            entrypoints.append(
                {
                    "path": symbol.get("path"),
                    "symbol": symbol.get("qualified_name"),
                    "kind": "symbol",
                    "reasons": ["entrypoint_symbol"],
                    "access_role": symbol.get("access_role", "editable"),
                }
            )
    return entrypoints


def _test_rows(files: list[dict[str, Any]], symbols: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    symbols_by_path: dict[str, list[str]] = defaultdict(list)
    for symbol in symbols:
        if symbol.get("is_test") is True:
            symbols_by_path[str(symbol.get("path", ""))].append(str(symbol.get("qualified_name", "")))
    for file_row in files:
        if "test" not in file_row.get("role_tags", []):
            continue
        path = str(file_row["path"])
        rows.append(
            {
                "path": path,
                "kind": file_row.get("kind"),
                "access_role": file_row.get("access_role", "read_only_evidence"),
                "test_symbols": sorted(name for name in symbols_by_path[path] if name),
                "summary": file_row.get("summary", ""),
            }
        )
    return rows


def _benchmark_rows(files: list[dict[str, Any]], symbols: list[dict[str, Any]]) -> list[dict[str, Any]]:
    symbol_paths = {str(symbol.get("path", "")) for symbol in symbols}
    rows: list[dict[str, Any]] = []
    for file_row in files:
        path = str(file_row["path"])
        if not _is_benchmark_path(path):
            continue
        rows.append(
            {
                "path": path,
                "kind": file_row.get("kind"),
                "access_role": file_row.get("access_role", "read_only_evidence"),
                "has_symbols": path in symbol_paths,
                "summary": file_row.get("summary", ""),
            }
        )
    return rows


def _config_rows(files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for file_row in files:
        if "config" not in file_row.get("role_tags", []):
            continue
        rows.append(
            {
                "path": file_row.get("path"),
                "kind": file_row.get("kind"),
                "access_role": file_row.get("access_role", "editable"),
                "summary": file_row.get("summary", ""),
            }
        )
    return rows


def _directory_for_path(path: str) -> str:
    parent = PurePosixPath(path).parent
    return "." if str(parent) == "." else parent.as_posix()


def _ancestor_directories(path: str) -> list[str]:
    parent = PurePosixPath(path).parent
    if str(parent) == ".":
        return ["."]
    directories = ["."]
    parts = parent.parts
    for index in range(1, len(parts) + 1):
        directories.append(PurePosixPath(*parts[:index]).as_posix())
    return directories


def _is_benchmark_path(path: str) -> bool:
    name = PurePosixPath(path).name.lower()
    return name in {"benchmark.py", "bench.py"} or "benchmark" in name


def _symbol_count_for_file(item: dict[str, Any]) -> int:
    python = item.get("python")
    if not isinstance(python, dict):
        return 0
    count = len(_object_list(python.get("functions"))) + len(_object_list(python.get("classes")))
    for klass in _object_list(python.get("classes")):
        count += len(_object_list(klass.get("methods")))
    return count


def _imports_for_file(item: dict[str, Any]) -> list[str]:
    python = item.get("python")
    if not isinstance(python, dict):
        return []
    return _string_list(python.get("imports"))


def _has_main_guard(item: dict[str, Any]) -> bool:
    python = item.get("python")
    return isinstance(python, dict) and python.get("has_main_guard") is True


def _index_files(index: dict[str, Any]) -> list[dict[str, Any]]:
    files = index.get("files")
    if not isinstance(files, list):
        return []
    rows: list[dict[str, Any]] = []
    for item in files:
        if isinstance(item, dict):
            row = dict(item)
            row["_index_source"] = item
            rows.append(row)
    return rows


def _entrypoint_markdown(entrypoints: list[dict[str, Any]]) -> str:
    if not entrypoints:
        return "- No entrypoints detected."
    return "\n".join(
        "- "
        f"`{item.get('path', '')}`"
        + (f"::{item.get('symbol')}" if item.get("symbol") else "")
        + " "
        + f"({', '.join(str(reason) for reason in item.get('reasons', []))})"
        for item in entrypoints[:12]
    )


def _path_list_markdown(label: str, rows: list[dict[str, Any]]) -> str:
    if not rows:
        return f"- {label}: none detected."
    return "\n".join(
        f"- `{item.get('path', '')}` ({item.get('access_role', 'editable')})"
        for item in rows[:16]
    )


def _directory_markdown(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "- No directories detected."
    lines = ["| Directory | Files | Python | Tests | Benchmarks | Bytes |", "| --- | ---: | ---: | ---: | ---: | ---: |"]
    for row in rows[:16]:
        lines.append(
            "| "
            f"`{row.get('path', '')}` | "
            f"{row.get('file_count', 0)} | "
            f"{row.get('python_file_count', 0)} | "
            f"{row.get('test_file_count', 0)} | "
            f"{row.get('benchmark_file_count', 0)} | "
            f"{row.get('bytes', 0)} |"
        )
    return "\n".join(lines)


def _file_markdown(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "- No files detected."
    lines = ["| Path | Kind | Access | Roles | Summary |", "| --- | --- | --- | --- | --- |"]
    for row in rows[:40]:
        roles = ", ".join(str(tag) for tag in row.get("role_tags", []))
        summary = str(row.get("summary", "")).replace("|", "\\|")
        lines.append(
            "| "
            f"`{row.get('path', '')}` | "
            f"`{row.get('kind', '')}` | "
            f"`{row.get('access_role', '')}` | "
            f"{roles} | "
            f"{summary[:120]} |"
        )
    if len(rows) > 40:
        lines.append(f"| ... | ... | ... | ... | {len(rows) - 40} file(s) omitted from summary. |")
    return "\n".join(lines)


def _object_dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _object_list(value: object) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if isinstance(item, str) and item]


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
