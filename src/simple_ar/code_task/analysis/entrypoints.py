from __future__ import annotations

"""Static checks for generated-project entrypoint debuggability.

Generated benchmark projects may print user-friendly error messages, but they
must not erase the original exception signal. Repair loops depend on traceback
files and line numbers to find the real producer/consumer bug.
"""

import ast
from pathlib import Path, PurePosixPath
from typing import Any


ENTRYPOINT_NAMES = {"main.py", "__main__.py", "cli.py", "app.py"}
TRACEBACK_HELPERS = {
    "traceback.print_exc",
    "traceback.format_exc",
    "traceback.print_exception",
    "traceback.format_exception",
    "logging.exception",
    "logger.exception",
}


def is_entrypoint_path(path: str | Path) -> bool:
    rel = str(path).replace("\\", "/")
    name = PurePosixPath(rel).name.lower()
    stem = PurePosixPath(rel).stem.lower()
    return name in ENTRYPOINT_NAMES or stem in {"main", "__main__", "cli", "app"}


def analyze_entrypoint_debuggability(project_dir: Path) -> dict[str, Any]:
    """Return broad exception handlers that suppress traceback signals."""

    findings: list[dict[str, Any]] = []
    if not project_dir.is_dir():
        return {"schema_version": "code_task_entrypoint_debuggability.v1", "findings": findings}
    for path in sorted(project_dir.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        rel = path.relative_to(project_dir).as_posix()
        if not is_entrypoint_path(rel):
            continue
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        findings.extend(suppressed_exception_handlers(source, path=rel))
    return {"schema_version": "code_task_entrypoint_debuggability.v1", "findings": findings}


def suppressed_exception_handlers(source: str, *, path: str = "") -> list[dict[str, Any]]:
    """Find broad ``except`` blocks that neither re-raise nor report traceback."""

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    rows: list[dict[str, Any]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        for handler in node.handlers:
            if not _is_broad_handler(handler):
                continue
            if _handler_preserves_traceback(handler):
                continue
            rows.append(
                {
                    "path": path,
                    "line": int(getattr(handler, "lineno", 0) or 0),
                    "handler": _handler_name(handler),
                    "summary": (
                        "Broad entrypoint exception handler suppresses the original traceback. "
                        "Emit traceback/logging.exception or re-raise after any friendly message."
                    ),
                }
            )
    return rows


def source_suppresses_entrypoint_traceback(source: str, *, path: str) -> str:
    if not is_entrypoint_path(path):
        return ""
    rows = suppressed_exception_handlers(source, path=path)
    if not rows:
        return ""
    first = rows[0]
    line = first.get("line") or "unknown"
    return f"broad_exception_without_traceback:{path}:{line}"


def _is_broad_handler(handler: ast.ExceptHandler) -> bool:
    typ = handler.type
    if typ is None:
        return True
    names = _exception_type_names(typ)
    return any(name in {"Exception", "BaseException"} for name in names)


def _exception_type_names(node: ast.AST) -> set[str]:
    if isinstance(node, ast.Name):
        return {node.id}
    if isinstance(node, ast.Attribute):
        return {node.attr}
    if isinstance(node, ast.Tuple):
        names: set[str] = set()
        for item in node.elts:
            names.update(_exception_type_names(item))
        return names
    return set()


def _handler_preserves_traceback(handler: ast.ExceptHandler) -> bool:
    for node in ast.walk(ast.Module(body=handler.body, type_ignores=[])):
        if isinstance(node, ast.Raise):
            return True
        if isinstance(node, ast.Call) and _call_name(node.func) in TRACEBACK_HELPERS:
            return True
    return False


def _handler_name(handler: ast.ExceptHandler) -> str:
    if handler.type is None:
        return "bare"
    try:
        return ast.unparse(handler.type)
    except Exception:
        return handler.type.__class__.__name__


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _call_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""
