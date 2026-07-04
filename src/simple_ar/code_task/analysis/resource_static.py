from __future__ import annotations

"""Static resource-risk signals for generated or patched Python projects.

The analyzer is intentionally conservative and benchmark-agnostic. It does not
try to prove runtime complexity; it identifies code shapes that often cause
local experiment tasks to hang or exceed budgets, such as estimator ``fit``
calls nested under candidate/seed/fold loops.
"""

import ast
from pathlib import Path
from typing import Any, Mapping


LOOP_HINT_TERMS = {
    "candidate",
    "class",
    "condition",
    "dataset",
    "epoch",
    "fold",
    "grid",
    "query",
    "round",
    "seed",
    "split",
    "trial",
}
CAP_HINT_TERMS = {
    "batch",
    "budget",
    "cap",
    "early",
    "limit",
    "max",
    "sample",
    "subsample",
    "timeout",
}


def analyze_resource_risks(project_dir: Path, *, max_files: int = 80) -> dict[str, Any]:
    """Return compact static resource-risk signals for a Python project."""

    files: list[dict[str, Any]] = []
    for path in sorted(Path(project_dir).rglob("*.py"))[:max_files]:
        if "__pycache__" in path.parts:
            continue
        rel = path.relative_to(project_dir).as_posix()
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(source)
        except (OSError, SyntaxError):
            continue
        row = _analyze_file(tree, source=source, path=rel)
        if row["fit_call_count"] or row["nested_fit_call_count"] or row["risk_score"]:
            files.append(row)
    total_fit_calls = sum(int(row.get("fit_call_count", 0)) for row in files)
    nested_fit_calls = sum(int(row.get("nested_fit_call_count", 0)) for row in files)
    max_depth = max([int(row.get("max_fit_loop_depth", 0)) for row in files] or [0])
    risk_score = sum(int(row.get("risk_score", 0)) for row in files)
    return {
        "schema_version": "code_task_resource_static.v1",
        "file_count": len(files),
        "total_fit_call_count": total_fit_calls,
        "nested_fit_call_count": nested_fit_calls,
        "max_fit_loop_depth": max_depth,
        "risk_score": risk_score,
        "files": sorted(files, key=lambda row: (-int(row.get("risk_score", 0)), str(row.get("path", ""))))[:12],
        "summary": _summary(total_fit_calls=total_fit_calls, nested_fit_calls=nested_fit_calls, max_depth=max_depth),
    }


def resource_review_findings(
    project_dir: Path,
    *,
    resource_plan: Mapping[str, Any],
) -> list[dict[str, str]]:
    """Return review findings derived from static resource signals."""

    analysis = analyze_resource_risks(project_dir)
    if not analysis.get("files"):
        return []
    budget = resource_plan.get("execution_budget")
    budget_map = budget if isinstance(budget, Mapping) else {}
    score = int(analysis.get("risk_score", 0) or 0)
    warn_threshold = _int(budget_map.get("resource_risk_warning_score"), 4)
    block_threshold = _int(budget_map.get("resource_risk_blocking_score"), 10**9)
    if score < warn_threshold:
        return []
    severity = "blocking" if score >= block_threshold else "warning"
    paths = ", ".join(f"`{row.get('path')}`" for row in analysis.get("files", [])[:4])
    return [
        {
            "severity": severity,
            "category": "resource_fit_loop_risk",
            "summary": (
                f"Static resource scan found model-fit calls inside loop-heavy code ({analysis.get('summary')}). "
                f"High-signal file(s): {paths}."
            ),
            "recommendation": (
                "Bound candidate/seed/fold loops, batch expensive queries, reuse fitted artifacts where valid, "
                "add preprocessing pipelines for iterative estimators when needed, and make runtime budgets explicit "
                "before trusting benchmark execution."
            ),
        }
    ]


def _analyze_file(tree: ast.AST, *, source: str, path: str) -> dict[str, Any]:
    visitor = _ResourceVisitor()
    visitor.visit(tree)
    lowered_source = source.lower()
    cap_terms = sorted(term for term in CAP_HINT_TERMS if term in source.lower())
    risk_score = visitor.nested_fit_call_count * 3 + max(0, visitor.max_fit_loop_depth - 1) * 2
    if visitor.fit_call_count and visitor.loop_hint_terms:
        risk_score += 1
    uses_logistic_regression = (
        "logisticregression" in lowered_source or "fit_logistic_regression" in lowered_source
    )
    uses_scaling_pipeline = any(
        marker in lowered_source
        for marker in (
            "standardscaler",
            "minmaxscaler",
            "robustscaler",
            "make_pipeline",
            "pipeline(",
        )
    )
    logistic_without_scaling = bool(
        uses_logistic_regression and visitor.nested_fit_call_count and not uses_scaling_pipeline
    )
    if logistic_without_scaling:
        risk_score += 4
    if cap_terms:
        risk_score = max(0, risk_score - 1)
    return {
        "path": path,
        "fit_call_count": visitor.fit_call_count,
        "nested_fit_call_count": visitor.nested_fit_call_count,
        "max_fit_loop_depth": visitor.max_fit_loop_depth,
        "loop_hint_terms": sorted(visitor.loop_hint_terms)[:12],
        "cap_hint_terms": cap_terms[:12],
        "uses_logistic_regression": uses_logistic_regression,
        "uses_scaling_pipeline": uses_scaling_pipeline,
        "logistic_without_scaling_in_loop": logistic_without_scaling,
        "risk_score": risk_score,
    }


class _ResourceVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.loop_stack: list[set[str]] = []
        self.fit_call_count = 0
        self.nested_fit_call_count = 0
        self.max_fit_loop_depth = 0
        self.loop_hint_terms: set[str] = set()

    def visit_For(self, node: ast.For) -> Any:
        self._visit_loop(node, hint_text=_node_text(node.target))

    def visit_AsyncFor(self, node: ast.AsyncFor) -> Any:
        self._visit_loop(node, hint_text=_node_text(node.target))

    def visit_While(self, node: ast.While) -> Any:
        self._visit_loop(node, hint_text=_node_text(node.test))

    def visit_Call(self, node: ast.Call) -> Any:
        if _is_fit_call(node):
            self.fit_call_count += 1
            depth = len(self.loop_stack)
            if depth:
                self.nested_fit_call_count += 1
                self.max_fit_loop_depth = max(self.max_fit_loop_depth, depth)
                for terms in self.loop_stack:
                    self.loop_hint_terms.update(terms)
        self.generic_visit(node)

    def _visit_loop(self, node: ast.AST, *, hint_text: str) -> None:
        terms = {term for term in LOOP_HINT_TERMS if term in hint_text.lower()}
        self.loop_stack.append(terms)
        self.generic_visit(node)
        self.loop_stack.pop()


def _is_fit_call(node: ast.Call) -> bool:
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr == "fit" or func.attr.startswith("fit_") or func.attr.endswith("_fit")
    if isinstance(func, ast.Name):
        return func.id == "fit" or func.id.startswith("fit_") or func.id.endswith("_fit")
    return False


def _node_text(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_node_text(node.value)}.{node.attr}"
    if isinstance(node, ast.Tuple):
        return " ".join(_node_text(item) for item in node.elts)
    if isinstance(node, ast.Call):
        return _node_text(node.func)
    if isinstance(node, ast.Compare):
        return _node_text(node.left)
    if isinstance(node, ast.Constant):
        return str(node.value)
    return node.__class__.__name__.lower()


def _summary(*, total_fit_calls: int, nested_fit_calls: int, max_depth: int) -> str:
    return f"{total_fit_calls} fit call(s), {nested_fit_calls} inside loop(s), max loop depth {max_depth}"


def _int(value: object, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
