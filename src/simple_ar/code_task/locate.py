from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from simple_ar.artifacts import read_json, read_text, write_json, write_text
from simple_ar.code_task.repo_map import build_code_task_repo_map
from simple_ar.code_task.state import (
    code_task_paths,
    load_code_task_manifest,
    save_code_task_manifest,
    utcnow_iso,
)


DEFAULT_TOP_K = 8
MIN_TOKEN_LENGTH = 2
STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "be",
    "by",
    "can",
    "code",
    "for",
    "from",
    "in",
    "into",
    "is",
    "it",
    "of",
    "on",
    "or",
    "project",
    "task",
    "the",
    "this",
    "to",
    "with",
}


@dataclass(frozen=True)
class CodeTaskLocateResult:
    """Result returned after ranking likely code-task context files.

    Args:
        run_dir: Code-task run directory.
        results_path: JSON artifact containing ranked candidates.
        summary_path: Markdown summary for human review.
        query: Query text used for ranking.
        editable_targets: Ranked files that may be edited by later steps.
        read_only_evidence: Ranked protected files kept as evidence only.
    """

    run_dir: Path
    results_path: Path
    summary_path: Path
    query: str
    editable_targets: tuple[dict[str, Any], ...]
    read_only_evidence: tuple[dict[str, Any], ...]


def locate_code_task_context(
    run_dir: Path,
    *,
    query: str | None = None,
    top_k: int = DEFAULT_TOP_K,
    refresh_map: bool = False,
    include_read_only: bool = True,
) -> CodeTaskLocateResult:
    """Rank workspace files that are likely relevant to a code-task request.

    Args:
        run_dir: Code-task run directory created by ``code-task init``.
        query: Optional ranking query. When omitted, ``code_task/task.md`` is
            used so standalone runs can locate context before planning.
        top_k: Maximum candidates kept in each editable/evidence group.
        refresh_map: Rebuild ``codebase_index.json`` and ``repo_map.json``
            before ranking.
        include_read_only: Whether protected test/benchmark/config evidence
            should be included in the output.

    Returns:
        Paths and ranked rows for the generated locate artifacts.

    Raises:
        FileNotFoundError: If required code-task artifacts are missing.
        RuntimeError: If ``run_dir`` is not a code-task workflow.
        ValueError: If ``top_k`` is less than one.
    """

    if top_k < 1:
        raise ValueError("top_k must be at least 1")

    root = Path(run_dir)
    paths = code_task_paths(root)
    manifest = load_code_task_manifest(root)
    repo_map_path = paths.meta_dir / "repo_map.json"
    if refresh_map or not repo_map_path.exists():
        build_code_task_repo_map(root, refresh_index=True)
    repo_map = read_json(repo_map_path)
    if not isinstance(repo_map, dict):
        raise RuntimeError(f"Expected JSON object in {repo_map_path}")

    query_text = query if query is not None else read_text(paths.task_dir / "task.md")
    results_path = paths.meta_dir / "locate_results.json"
    summary_path = paths.meta_dir / "locate_results.md"
    ranked = _rank_files(repo_map, query_text)
    editable = [
        row
        for row in ranked
        if row.get("access_role") == "editable"
    ][:top_k]
    evidence = [
        row
        for row in ranked
        if row.get("access_role") != "editable"
    ][:top_k] if include_read_only else []

    fallback = _fallback_candidates(repo_map, existing_paths={str(row["path"]) for row in editable + evidence})
    if len(editable) < top_k:
        editable.extend(fallback["editable"][: top_k - len(editable)])
    if include_read_only and len(evidence) < top_k:
        evidence.extend(fallback["read_only_evidence"][: top_k - len(evidence)])

    artifact = {
        "schema_version": 1,
        "generated_at": utcnow_iso(),
        "query": query_text,
        "query_terms": sorted(_terms(query_text)),
        "source": {
            "repo_map": "code_task/meta/repo_map.json",
            "repo_map_generated_at": repo_map.get("generated_at"),
        },
        "top_k": top_k,
        "editable_targets": editable,
        "read_only_evidence": evidence,
        "omitted": {
            "ranked_file_count": len(ranked),
            "editable_omitted": max(0, _count_role(ranked, "editable") - len(editable)),
            "read_only_omitted": max(0, _count_read_only(ranked) - len(evidence)),
        },
    }
    write_json(results_path, artifact)
    write_text(summary_path, render_locate_summary(artifact))
    _update_manifest_after_locate(root, manifest, artifact)
    return CodeTaskLocateResult(
        run_dir=root,
        results_path=results_path,
        summary_path=summary_path,
        query=query_text,
        editable_targets=tuple(editable),
        read_only_evidence=tuple(evidence),
    )


def render_locate_summary(locate_results: dict[str, Any]) -> str:
    """Render ``locate_results.json`` into a compact Markdown summary."""

    query = str(locate_results.get("query", "")).strip()
    editable = _object_list(locate_results.get("editable_targets"))
    evidence = _object_list(locate_results.get("read_only_evidence"))
    omitted = _object_dict(locate_results.get("omitted"))
    lines = [
        "# Locate Results",
        "",
        f"Generated: `{locate_results.get('generated_at', '')}`",
        "",
        "## Query",
        "",
        _truncate(query, 800) or "(empty)",
        "",
        "## Editable Targets",
        "",
        _candidate_table(editable),
        "",
        "## Read-Only Evidence",
        "",
        _candidate_table(evidence),
        "",
        "## Omitted",
        "",
        f"- Ranked files: `{omitted.get('ranked_file_count', 0)}`",
        f"- Editable omitted: `{omitted.get('editable_omitted', 0)}`",
        f"- Read-only omitted: `{omitted.get('read_only_omitted', 0)}`",
        "",
    ]
    return "\n".join(lines)


def _rank_files(repo_map: dict[str, Any], query: str) -> list[dict[str, Any]]:
    terms = _terms(query)
    files = _object_list(repo_map.get("files"))
    symbols = _symbols_by_path(_object_list(repo_map.get("symbols")))
    rows: list[dict[str, Any]] = []
    for file_row in files:
        path = str(file_row.get("path", "")).strip()
        if not path:
            continue
        symbol_rows = symbols.get(path, [])
        score, matched_terms, reasons = _score_file(file_row, symbol_rows, terms)
        role_tags = _string_list(file_row.get("role_tags"))
        access_role = str(file_row.get("access_role", "editable"))
        if "source" in role_tags and access_role == "editable":
            score += 0.5
        if ("test" in role_tags or _is_benchmark_path(path)) and access_role != "editable":
            score += 0.5
        if score <= 0:
            continue
        rows.append(
            {
                "path": path,
                "score": round(score, 3),
                "access_role": access_role,
                "role_tags": role_tags,
                "matched_terms": sorted(matched_terms),
                "reasons": reasons,
                "summary": str(file_row.get("summary", "")),
                "symbols": [
                    {
                        "kind": str(symbol.get("kind", "")),
                        "name": str(symbol.get("qualified_name") or symbol.get("name", "")),
                        "line_start": symbol.get("line_start"),
                        "line_end": symbol.get("line_end"),
                    }
                    for symbol in symbol_rows[:8]
                ],
            }
        )
    rows.sort(key=lambda row: (-float(row["score"]), str(row["path"])))
    return rows


def _score_file(
    file_row: dict[str, Any],
    symbols: list[dict[str, Any]],
    terms: set[str],
) -> tuple[float, set[str], list[str]]:
    matched: set[str] = set()
    reasons: list[str] = []
    score = 0.0
    fields = [
        ("path", _path_text(str(file_row.get("path", ""))), 4.0),
        ("name", str(file_row.get("name", "")), 4.0),
        ("summary", str(file_row.get("summary", "")), 2.0),
        ("imports", " ".join(_string_list(file_row.get("imports"))), 1.0),
        ("roles", " ".join(_string_list(file_row.get("role_tags"))), 1.0),
    ]
    symbol_text = " ".join(
        str(symbol.get("qualified_name") or symbol.get("name", ""))
        for symbol in symbols
    )
    fields.append(("symbols", symbol_text, 3.0))
    for label, text, weight in fields:
        text_tokens = _terms(text)
        hits = terms & text_tokens
        if not hits:
            continue
        matched.update(hits)
        score += len(hits) * weight
        reasons.append(f"{label}: " + ", ".join(sorted(hits)[:6]))
    return score, matched, reasons


def _fallback_candidates(
    repo_map: dict[str, Any],
    *,
    existing_paths: set[str],
) -> dict[str, list[dict[str, Any]]]:
    files = _object_list(repo_map.get("files"))
    symbols = _symbols_by_path(_object_list(repo_map.get("symbols")))
    editable: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []
    for file_row in files:
        path = str(file_row.get("path", ""))
        if not path or path in existing_paths:
            continue
        role_tags = _string_list(file_row.get("role_tags"))
        access_role = str(file_row.get("access_role", "editable"))
        is_evidence = access_role != "editable"
        is_source = "source" in role_tags and not is_evidence
        if not (is_source or is_evidence):
            continue
        row = {
            "path": path,
            "score": 0.0,
            "access_role": access_role,
            "role_tags": role_tags,
            "matched_terms": [],
            "reasons": ["fallback: source candidate" if is_source else "fallback: protected evidence"],
            "summary": str(file_row.get("summary", "")),
            "symbols": [
                {
                    "kind": str(symbol.get("kind", "")),
                    "name": str(symbol.get("qualified_name") or symbol.get("name", "")),
                    "line_start": symbol.get("line_start"),
                    "line_end": symbol.get("line_end"),
                }
                for symbol in symbols.get(path, [])[:8]
            ],
        }
        if is_evidence:
            evidence.append(row)
        else:
            editable.append(row)
    editable.sort(key=lambda row: (_fallback_sort_rank(row), str(row["path"])))
    evidence.sort(key=lambda row: (_fallback_sort_rank(row), str(row["path"])))
    return {"editable": editable, "read_only_evidence": evidence}


def _fallback_sort_rank(row: dict[str, Any]) -> int:
    tags = set(_string_list(row.get("role_tags")))
    if "entrypoint" in tags:
        return 0
    if "source" in tags:
        return 1
    if "test" in tags:
        return 2
    return 3


def _update_manifest_after_locate(
    run_dir: Path,
    manifest: dict[str, Any],
    locate_results: dict[str, Any],
) -> None:
    layout = manifest.get("layout")
    if not isinstance(layout, dict):
        layout = {}
    layout.update(
        {
            "locate_results": "code_task/meta/locate_results.json",
            "locate_summary": "code_task/meta/locate_results.md",
        }
    )
    manifest["layout"] = layout
    manifest["locate"] = {
        "status": "completed",
        "generated_at": locate_results.get("generated_at"),
        "path": "code_task/meta/locate_results.json",
        "summary": "code_task/meta/locate_results.md",
        "editable_count": len(_object_list(locate_results.get("editable_targets"))),
        "read_only_count": len(_object_list(locate_results.get("read_only_evidence"))),
    }
    save_code_task_manifest(run_dir, manifest)


def _candidate_table(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "- No candidates."
    lines = ["| Path | Score | Role | Why |", "| --- | ---: | --- | --- |"]
    for row in rows:
        reasons = "; ".join(_string_list(row.get("reasons"))) or "fallback"
        lines.append(
            "| "
            f"`{row.get('path', '')}` | "
            f"{row.get('score', 0)} | "
            f"{row.get('access_role', '')} | "
            f"{_escape_table(_truncate(reasons, 140))} |"
        )
    return "\n".join(lines)


def _terms(text: str) -> set[str]:
    terms: set[str] = set()
    for raw in re.findall(r"[A-Za-z][A-Za-z0-9_]*|[0-9]+", text.lower()):
        for token in raw.split("_"):
            if len(token) >= MIN_TOKEN_LENGTH and token not in STOPWORDS:
                terms.add(token)
    return terms


def _path_text(path: str) -> str:
    return path.replace("/", " ").replace("\\", " ").replace(".", " ").replace("-", " ")


def _symbols_by_path(symbols: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for symbol in symbols:
        path = str(symbol.get("path", ""))
        if not path:
            continue
        grouped.setdefault(path, []).append(symbol)
    return grouped


def _count_role(rows: Iterable[dict[str, Any]], access_role: str) -> int:
    return sum(1 for row in rows if row.get("access_role") == access_role)


def _count_read_only(rows: Iterable[dict[str, Any]]) -> int:
    return sum(1 for row in rows if row.get("access_role") != "editable")


def _is_benchmark_path(path: str) -> bool:
    name = Path(path).name.lower()
    return name in {"benchmark.py", "bench.py"} or "benchmark" in name


def _object_list(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _object_dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if isinstance(item, (str, int, float))]


def _escape_table(text: str) -> str:
    return text.replace("|", "\\|")


def _truncate(text: str, max_chars: int) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= max_chars:
        return normalized
    return normalized[: max(0, max_chars - 3)].rstrip() + "..."
