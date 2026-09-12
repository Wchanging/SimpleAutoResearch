from __future__ import annotations

"""Locate fit calls inside loops; these observations do not predict resource use."""

import ast
from pathlib import Path
from typing import Any


def analyze_resource_risks(project_dir: Path, *, max_files: int = 80) -> dict[str, Any]:
    """Return bounded source observations, not an execution safety verdict."""
    files: list[dict[str, Any]] = []
    for path in sorted(Path(project_dir).rglob("*.py"))[:max_files]:
        if "__pycache__" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, SyntaxError):
            continue
        visitor = _ResourceVisitor()
        visitor.visit(tree)
        if visitor.fit_call_count:
            files.append({
                "path": path.relative_to(project_dir).as_posix(),
                "fit_call_count": visitor.fit_call_count,
                "nested_fit_call_count": visitor.nested_fit_call_count,
                "max_fit_loop_depth": visitor.max_fit_loop_depth,
            })
    total = sum(row["fit_call_count"] for row in files)
    nested = sum(row["nested_fit_call_count"] for row in files)
    depth = max((row["max_fit_loop_depth"] for row in files), default=0)
    return {
        "schema_version": "code_task_resource_static.v1",
        "file_count": len(files),
        "total_fit_call_count": total,
        "nested_fit_call_count": nested,
        "max_fit_loop_depth": depth,
        "files": sorted(files, key=lambda row: (-row["nested_fit_call_count"], row["path"]))[:12],
        "summary": f"{total} fit call(s), {nested} inside loop(s), max loop depth {depth}",
    }


def resource_review_findings(project_dir: Path) -> list[dict[str, str]]:
    analysis = analyze_resource_risks(project_dir)
    if not analysis["nested_fit_call_count"]:
        return []
    paths = ", ".join(row["path"] for row in analysis["files"] if row["nested_fit_call_count"])
    return [{
        "severity": "warning",
        "category": "resource_fit_loop_risk",
        "summary": f"Fit calls occur inside loops ({analysis['summary']}). Files: {paths}. "
                   "Static nesting does not establish runtime cost.",
        "recommendation": "Check loop sizes against the execution budget; use measured runtime "
                          "to decide whether to reduce work or reuse fitted artifacts.",
    }]


class _ResourceVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.loop_depth = 0
        self.fit_call_count = 0
        self.nested_fit_call_count = 0
        self.max_fit_loop_depth = 0

    def visit_For(self, node: ast.For) -> None:
        self._visit_loop(node)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        self._visit_loop(node)

    def visit_While(self, node: ast.While) -> None:
        self._visit_loop(node)

    def visit_Call(self, node: ast.Call) -> None:
        if _is_fit_call(node):
            self.fit_call_count += 1
            if self.loop_depth:
                self.nested_fit_call_count += 1
                self.max_fit_loop_depth = max(self.max_fit_loop_depth, self.loop_depth)
        self.generic_visit(node)

    def _visit_loop(self, node: ast.AST) -> None:
        self.loop_depth += 1
        self.generic_visit(node)
        self.loop_depth -= 1


def _is_fit_call(node: ast.Call) -> bool:
    func = node.func
    if isinstance(func, ast.Attribute):
        name = func.attr
    elif isinstance(func, ast.Name):
        name = func.id
    else:
        return False
    return name == "fit" or name.startswith("fit_") or name.endswith("_fit")
