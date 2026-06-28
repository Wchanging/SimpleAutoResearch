"""Layered code-task review indexing and context selection.

This module is intentionally benchmark-agnostic. It builds a local project
index, groups files into review clusters, and prepares bounded snippets for
LLM review. Deterministic checks can inspect the whole project; LLM reviewers
should receive only the relevant cluster context.
"""

from __future__ import annotations

import ast
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

from simple_ar.code_task.analysis.interfaces import public_api
from simple_ar.reviewing.schema import ReviewFinding


TEXT_SUFFIXES = {".py", ".json", ".toml", ".md", ".txt", ".yaml", ".yml"}
SKIP_PARTS = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    "node_modules",
    "dist",
    "build",
    ".ipynb_checkpoints",
}


def build_review_index(
    project_dir: Path,
    *,
    result_schema: Mapping[str, Any] | None = None,
    contract: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a complete local review index without calling an LLM."""

    files: list[dict[str, Any]] = []
    required_metrics = _required_metrics(result_schema or {})
    for path in sorted(project_dir.rglob("*")):
        if not path.is_file() or _should_skip_path(path):
            continue
        rel = _safe_rel(project_dir, path)
        if not rel or not _is_reviewable_file(path):
            continue
        text = _read_text(path)
        role = classify_review_role(rel)
        row: dict[str, Any] = {
            "path": rel,
            "suffix": path.suffix.lower(),
            "role": role,
            "size_bytes": path.stat().st_size,
            "line_count": max(1, len(text.splitlines())) if text else 0,
            "mentions_required_metrics": [metric for metric in required_metrics if metric and metric in text],
        }
        if path.suffix == ".py":
            row["public_api"] = public_api(path)
            row["imports"] = _python_imports(text)
            row["entrypoint_candidate"] = _is_entrypoint_candidate(rel, text)
        files.append(row)

    return {
        "schema_version": "code_task_review_index.v1",
        "project_dir": str(project_dir),
        "file_count": len(files),
        "python_file_count": sum(1 for row in files if row.get("suffix") == ".py"),
        "required_metrics": required_metrics,
        "entrypoints": [row["path"] for row in files if row.get("entrypoint_candidate")],
        "roles": _role_counts(files),
        "task_markers": _task_markers(contract or {}),
        "files": files,
    }


def build_review_clusters(
    review_index: Mapping[str, Any],
    *,
    deterministic_findings: Sequence[ReviewFinding] = (),
    max_clusters: int = 6,
    max_files_per_cluster: int = 5,
) -> list[dict[str, Any]]:
    """Select bounded semantic clusters for LLM review."""

    files = [row for row in review_index.get("files", []) if isinstance(row, Mapping)]
    by_role: dict[str, list[Mapping[str, Any]]] = {}
    for row in files:
        by_role.setdefault(str(row.get("role") or "support"), []).append(row)

    cluster_defs = [
        ("entrypoint", "Entrypoint and orchestration", ["entrypoint", "orchestration"]),
        ("data_flow", "Data loading and preprocessing", ["data", "preprocess"]),
        ("core_logic", "Core algorithm and model logic", ["core", "model"]),
        ("metrics_artifacts", "Metrics, result schema, and artifact writers", ["metrics", "reporting"]),
        ("config_docs", "Configuration, documentation, and user-facing surface", ["config", "docs"]),
        ("support", "Support modules and remaining project surface", ["support"]),
    ]
    clusters: list[dict[str, Any]] = []
    used: set[str] = set()
    for cluster_id, title, roles in cluster_defs:
        selected: list[Mapping[str, Any]] = []
        for role in roles:
            selected.extend(by_role.get(role, []))
        selected = _rank_cluster_files(selected)
        paths = [str(row.get("path")) for row in selected if row.get("path") and str(row.get("path")) not in used]
        paths = paths[:max_files_per_cluster]
        if not paths:
            continue
        used.update(paths)
        findings = _findings_for_paths(deterministic_findings, paths)
        clusters.append(
            {
                "cluster_id": cluster_id,
                "title": title,
                "objective": _cluster_objective(cluster_id),
                "roles": roles,
                "files": paths,
                "deterministic_findings": findings,
            }
        )
        if len(clusters) >= max_clusters:
            break

    remaining = [
        str(row.get("path"))
        for row in _rank_cluster_files(files)
        if row.get("path") and str(row.get("path")) not in used
    ][:max_files_per_cluster]
    if remaining and len(clusters) < max_clusters:
        clusters.append(
            {
                "cluster_id": "remaining",
                "title": "Remaining high-signal files",
                "objective": "Look for integration risks not covered by earlier clusters.",
                "roles": ["support"],
                "files": remaining,
                "deterministic_findings": _findings_for_paths(deterministic_findings, remaining),
            }
        )
    return clusters


def compact_review_index(review_index: Mapping[str, Any], *, max_files: int = 40) -> dict[str, Any]:
    files = review_index.get("files")
    rows = [row for row in files if isinstance(row, Mapping)] if isinstance(files, list) else []
    return {
        "schema_version": review_index.get("schema_version", "code_task_review_index.v1"),
        "file_count": review_index.get("file_count", 0),
        "python_file_count": review_index.get("python_file_count", 0),
        "required_metrics": review_index.get("required_metrics", []),
        "entrypoints": review_index.get("entrypoints", []),
        "roles": review_index.get("roles", {}),
        "task_markers": review_index.get("task_markers", {}),
        "files": [
            {
                "path": row.get("path", ""),
                "role": row.get("role", "support"),
                "line_count": row.get("line_count", 0),
                "public_api": row.get("public_api", [])[:16] if isinstance(row.get("public_api"), list) else [],
                "mentions_required_metrics": row.get("mentions_required_metrics", []),
            }
            for row in rows[:max_files]
        ],
    }


def snippets_for_cluster(
    project_dir: Path,
    cluster: Mapping[str, Any],
    *,
    chars_per_file: int = 4_000,
) -> list[str]:
    snippets: list[str] = []
    paths = cluster.get("files")
    for rel in paths if isinstance(paths, list) else []:
        safe = _safe_path(str(rel))
        if not safe:
            continue
        path = project_dir / safe
        if not path.is_file():
            continue
        text = _review_snippet(path, limit=chars_per_file)
        language = "python" if path.suffix == ".py" else ""
        snippets.append(f"### {safe}\n```{language}\n{text}\n```")
    return snippets


def classify_review_role(path: str) -> str:
    lowered = path.lower()
    name = PurePosixPath(path).name.lower()
    stem = PurePosixPath(path).stem.lower()
    if name in {"main.py", "__main__.py", "cli.py", "app.py"} or stem in {"main", "cli", "app"}:
        return "entrypoint"
    if _contains_any(lowered, ("runner", "run_", "execute", "executor", "orchestr", "workflow", "pipeline", "experiment", "train", "eval")):
        return "orchestration"
    if _contains_any(lowered, ("input", "data", "dataset", "loader", "source", "ingest", "feature", "label")):
        return "data"
    if _contains_any(lowered, ("process", "preprocess", "transform", "prepare", "clean", "split")):
        return "preprocess"
    if _contains_any(lowered, ("core", "model", "algorithm", "logic", "method", "estimator", "classif", "regress")):
        return "model"
    if _contains_any(lowered, ("analysis", "metric", "score", "report", "artifact", "output", "result", "summary", "writer")):
        return "metrics"
    if _contains_any(lowered, ("config", "setting", "schema", "option")):
        return "config"
    if name.lower() in {"readme.md", "usage.md"} or lowered.endswith((".md", ".txt")):
        return "docs"
    return "support"


def _rank_cluster_files(files: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return sorted(
        files,
        key=lambda row: (
            _role_rank(str(row.get("role") or "support")),
            -int(row.get("line_count") or 0),
            str(row.get("path") or ""),
        ),
    )


def _role_rank(role: str) -> int:
    ranks = {
        "entrypoint": 0,
        "orchestration": 1,
        "data": 2,
        "preprocess": 3,
        "model": 4,
        "core": 4,
        "metrics": 5,
        "reporting": 5,
        "config": 6,
        "docs": 7,
    }
    return ranks.get(role, 100)


def _findings_for_paths(findings: Sequence[ReviewFinding], paths: Sequence[str]) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    path_set = set(paths)
    for finding in findings:
        blob = " ".join([finding.summary, finding.recommendation, *finding.evidence])
        if any(path in blob for path in path_set):
            selected.append(finding.model_dump(mode="json"))
    return selected[:12]


def _cluster_objective(cluster_id: str) -> str:
    objectives = {
        "entrypoint": "Verify the command path, orchestration, and metric printing contract.",
        "data_flow": "Verify data loading, preprocessing, leakage boundaries, and record schemas.",
        "core_logic": "Verify core implementation logic and dependency boundaries.",
        "metrics_artifacts": "Verify metric computation, required outputs, and artifact writers.",
        "config_docs": "Verify configuration/documentation match the executable project surface.",
    }
    return objectives.get(cluster_id, "Review this cluster for local correctness and integration risk.")


def _task_markers(contract: Mapping[str, Any]) -> dict[str, Any]:
    text = "\n".join(str(contract.get(key) or "") for key in ("objective", "task")).lower()
    criteria = contract.get("success_criteria")
    if isinstance(criteria, list):
        text += "\n" + "\n".join(str(item).lower() for item in criteria)
    return {
        "mentions_readme": "readme" in text,
        "mentions_results_json": "results.json" in text,
        "mentions_report": "report.md" in text or "report" in text,
        "mentions_multiple_tasks": any(word in text for word in ("multiple", "several", "suite", "tasks")),
    }


def _required_metrics(schema: Mapping[str, Any]) -> list[str]:
    value = schema.get("required_metrics")
    metrics = [str(item) for item in value if str(item).strip()] if isinstance(value, list) else []
    primary = str(schema.get("primary_metric") or "").strip()
    if primary and primary not in metrics:
        metrics.insert(0, primary)
    return metrics


def _python_imports(source: str) -> list[str]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    imports: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            prefix = "." * int(node.level or 0) + (node.module or "")
            imports.extend(f"{prefix}.{alias.name}".strip(".") for alias in node.names if alias.name != "*")
    return sorted(set(imports))[:40]


def _is_entrypoint_candidate(path: str, source: str) -> bool:
    name = PurePosixPath(path).name.lower()
    return name in {"main.py", "__main__.py", "cli.py", "app.py"} or "if __name__ == \"__main__\"" in source


def _role_counts(files: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in files:
        role = str(row.get("role") or "support")
        counts[role] = counts.get(role, 0) + 1
    return counts


def _is_reviewable_file(path: Path) -> bool:
    return path.suffix.lower() in TEXT_SUFFIXES and path.stat().st_size <= 1_000_000


def _should_skip_path(path: Path) -> bool:
    parts = set(path.parts)
    if parts & SKIP_PARTS:
        return True
    return any(part.endswith(".egg-info") or part.endswith(".dist-info") for part in path.parts)


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _review_snippet(path: Path, *, limit: int) -> str:
    text = _read_text(path)
    if len(text) <= limit:
        return text
    half = max(600, limit // 2)
    return text[:half].rstrip() + "\n\n# ... middle omitted for layered review ...\n\n" + text[-half:].lstrip()


def _safe_rel(root: Path, path: Path) -> str:
    try:
        return _safe_path(path.relative_to(root).as_posix())
    except ValueError:
        return ""


def _safe_path(value: str) -> str:
    path = PurePosixPath(value.replace("\\", "/").strip().lstrip("/"))
    if not value or path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        return ""
    return path.as_posix()


def _contains_any(value: str, needles: tuple[str, ...]) -> bool:
    return any(needle in value for needle in needles)
