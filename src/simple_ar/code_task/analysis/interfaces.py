from __future__ import annotations

"""Shared local Python interface contracts for planning and review."""

import ast
import json
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence


def order_file_specs(files: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """Return file specs in stable dependency-first order."""

    rows = [row for row in files if _safe_path(str(row.get("path", "")))]
    by_path = {_safe_path(str(row.get("path", ""))): row for row in rows}
    ordered: list[Mapping[str, Any]] = []
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(path: str) -> None:
        if path in visited:
            return
        if path in visiting:
            return
        visiting.add(path)
        row = by_path[path]
        dependencies = row.get("dependencies")
        if isinstance(dependencies, list):
            for dependency in dependencies:
                dep_path = _safe_path(str(dependency))
                if dep_path in by_path:
                    visit(dep_path)
        visiting.remove(path)
        visited.add(path)
        ordered.append(row)

    for row in rows:
        visit(_safe_path(str(row.get("path", ""))))
    return ordered


def dependency_context(
    project_dir: Path,
    file_spec: Mapping[str, Any],
    *,
    max_source_chars: int = 2_400,
) -> dict[str, Any]:
    """Describe actual generated dependency APIs for the next file prompt."""

    rows: list[dict[str, Any]] = []
    dependencies = file_spec.get("dependencies")
    for raw_path in dependencies if isinstance(dependencies, list) else []:
        rel_path = _safe_path(str(raw_path))
        if not rel_path:
            continue
        target = project_dir / rel_path
        row: dict[str, Any] = {
            "path": rel_path,
            "planned": True,
            "available": target.is_file(),
        }
        if target.is_file() and target.suffix == ".py":
            row["public_api"] = public_api(target)
            source = target.read_text(encoding="utf-8", errors="replace")
            row["source_excerpt"] = source[:max_source_chars]
        elif target.is_file() and target.suffix == ".json":
            try:
                payload = json.loads(target.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                payload = {}
            row["json_shape"] = _json_shape(payload)
        rows.append(row)
    return {
        "rule": "Use these exact existing APIs. Do not invent alternate names for generated dependencies.",
        "dependencies": rows,
    }


def public_api(path: Path) -> list[str]:
    """Extract concise top-level Python API signatures."""

    try:
        source = path.read_text(encoding="utf-8", errors="replace")
    except (OSError, SyntaxError):
        return []
    return public_api_from_source(source)


def public_api_from_source(source: str) -> list[str]:
    """Extract concise top-level API signatures from Python source text."""

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    rows: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("_"):
            rows.append(_function_signature(node))
        elif isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
            fields = [
                child.target.id
                for child in node.body
                if isinstance(child, ast.AnnAssign) and isinstance(child.target, ast.Name)
            ]
            suffix = f" fields={fields}" if fields else ""
            rows.append(f"class {node.name}{suffix}")
            rows.extend(
                f"{node.name}.{_function_signature(child)}"
                for child in node.body
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and not child.name.startswith("_")
            )
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            for name in _assignment_names(node):
                if not name.startswith("_"):
                    rows.append(name)
    return rows[:80]


def snippet_api_contract(snippets: Sequence[Mapping[str, Any]]) -> dict[str, list[str]]:
    """Build a compact API contract from already-selected prompt snippets."""

    result: dict[str, list[str]] = {}
    for row in snippets:
        path = _safe_path(str(row.get("path", "")))
        text = str(row.get("text", ""))
        if not path or not path.endswith(".py") or not text:
            continue
        result[path] = public_api_from_source(text)
    return result


def project_api_contract(
    project_dir: Path,
    *,
    relevant_paths: Sequence[str] | None = None,
) -> dict[str, list[str]]:
    """Return public APIs for every generated Python module."""

    selected = {_safe_path(path) for path in relevant_paths or []}
    return {
        path.relative_to(project_dir).as_posix(): public_api(path)
        for path in sorted(project_dir.rglob("*.py"))
        if not selected or path.relative_to(project_dir).as_posix() in selected
    }


def find_local_api_mismatches(
    project_dir: Path,
    *,
    relevant_paths: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    """Find references to attributes absent from generated local modules."""

    modules: dict[str, tuple[Path, set[str]]] = {}
    trees: dict[Path, ast.Module] = {}
    for path in sorted(project_dir.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, SyntaxError):
            continue
        module = _module_name(project_dir, path)
        if not module:
            continue
        trees[path] = tree
        modules[module] = (path, _exported_names(tree))

    findings: list[dict[str, Any]] = []
    seen: set[tuple[str, int, str, str]] = set()
    for path, tree in trees.items():
        caller_module = _module_name(project_dir, path)
        aliases: dict[str, str] = {}
        for node in tree.body:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in modules:
                        aliases[alias.asname or alias.name.split(".")[-1]] = alias.name
            elif isinstance(node, ast.ImportFrom):
                base = _resolve_import_module(caller_module, node.module, node.level, path.name == "__init__.py")
                for alias in node.names:
                    if alias.name == "*":
                        continue
                    child_module = f"{base}.{alias.name}" if base else alias.name
                    local_name = alias.asname or alias.name
                    if child_module in modules:
                        aliases[local_name] = child_module
                    elif base in modules and alias.name not in modules[base][1]:
                        _append_mismatch(
                            findings,
                            seen,
                            caller=path.relative_to(project_dir).as_posix(),
                            line=node.lineno,
                            target=base,
                            target_path=modules[base][0].relative_to(project_dir).as_posix(),
                            symbol=alias.name,
                            available=modules[base][1],
                        )
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute) or not isinstance(node.value, ast.Name):
                continue
            target_module = aliases.get(node.value.id)
            if target_module not in modules:
                continue
            if node.attr in modules[target_module][1]:
                continue
            _append_mismatch(
                findings,
                seen,
                caller=path.relative_to(project_dir).as_posix(),
                line=getattr(node, "lineno", 0),
                target=target_module,
                target_path=modules[target_module][0].relative_to(project_dir).as_posix(),
                symbol=node.attr,
                available=modules[target_module][1],
            )
    selected = {_safe_path(path) for path in relevant_paths or []}
    if not selected:
        return findings
    return [
        row
        for row in findings
        if row.get("caller") in selected or row.get("target_path") in selected
    ]


def _append_mismatch(
    findings: list[dict[str, Any]],
    seen: set[tuple[str, int, str, str]],
    *,
    caller: str,
    line: int,
    target: str,
    target_path: str,
    symbol: str,
    available: set[str],
) -> None:
    key = (caller, line, target, symbol)
    if key in seen:
        return
    seen.add(key)
    findings.append(
        {
            "caller": caller,
            "line": line,
            "target_module": target,
            "target_path": target_path,
            "missing_symbol": symbol,
            "available_symbols": sorted(name for name in available if not name.startswith("_"))[:20],
        }
    )


def _module_name(project_dir: Path, path: Path) -> str:
    rel = path.relative_to(project_dir).with_suffix("")
    parts = list(rel.parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _resolve_import_module(current: str, module: str | None, level: int, is_package: bool) -> str:
    if level <= 0:
        return module or ""
    package = current if is_package else current.rpartition(".")[0]
    parts = package.split(".") if package else []
    trim = max(0, level - 1)
    if trim:
        parts = parts[:-trim] if trim <= len(parts) else []
    if module:
        parts.extend(module.split("."))
    return ".".join(part for part in parts if part)


def _exported_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            names.update(_assignment_names(node))
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            names.update(alias.asname or alias.name.split(".")[-1] for alias in node.names if alias.name != "*")
    return names


def _assignment_names(node: ast.Assign | ast.AnnAssign) -> list[str]:
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    return [target.id for target in targets if isinstance(target, ast.Name)]


def _function_signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    try:
        args = ast.unparse(node.args)
        returns = f" -> {ast.unparse(node.returns)}" if node.returns is not None else ""
    except Exception:
        args, returns = "...", ""
    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    return f"{prefix} {node.name}({args}){returns}"


def _json_shape(value: Any, *, depth: int = 0) -> Any:
    if depth >= 3:
        return type(value).__name__
    if isinstance(value, dict):
        return {str(key): _json_shape(item, depth=depth + 1) for key, item in list(value.items())[:30]}
    if isinstance(value, list):
        return [_json_shape(value[0], depth=depth + 1)] if value else []
    return type(value).__name__


def _safe_path(value: str) -> str:
    value = value.replace("\\", "/").strip().lstrip("/")
    path = PurePosixPath(value)
    if not value or path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        return ""
    return path.as_posix()
