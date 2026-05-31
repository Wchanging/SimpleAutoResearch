from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from simple_ar.artifacts import read_json, read_jsonl, write_json, write_jsonl, write_text
from simple_ar.code_task.analysis.locate import locate_code_task_context
from simple_ar.code_task.runtime.state import (
    code_task_paths,
    is_relative_to,
    load_code_task_manifest,
    save_code_task_manifest,
    utcnow_iso,
    workspace_file,
)


DEFAULT_CONTEXT_TOP_K = 8
DEFAULT_MAX_FILES = 8
DEFAULT_MAX_SOURCE_CHARS_PER_FILE = 4_000
DEFAULT_MAX_TOTAL_CHARS = 20_000


@dataclass(frozen=True)
class CodeTaskContextPackResult:
    """Result returned after building a bounded code-task context pack.

    Args:
        run_dir: Code-task run directory.
        context_dir: Directory containing the generated context pack.
        context_pack_path: JSON manifest for the context pack.
        prompt_context_path: Markdown prompt-ready context.
        snippets_path: JSONL rows containing selected source snippets.
        locate_results_path: Source locate artifact used for selection.
        selected_files: Workspace-relative files included in the context pack.
    """

    run_dir: Path
    context_dir: Path
    context_pack_path: Path
    prompt_context_path: Path
    snippets_path: Path
    locate_results_path: Path
    selected_files: tuple[str, ...]


@dataclass(frozen=True)
class LoadedCodeTaskContextPack:
    """Latest context pack loaded from a code-task run.

    Args:
        run_dir: Code-task run directory.
        context_pack_path: Absolute path to ``context_pack.json``.
        prompt_context_path: Absolute path to ``prompt_context.md``.
        snippets_path: Absolute path to ``selected_snippets.jsonl``.
        context_pack: Parsed context-pack JSON object.
        snippets: Parsed selected snippet rows.
        selected_files: Workspace-relative selected file paths.
    """

    run_dir: Path
    context_pack_path: Path
    prompt_context_path: Path
    snippets_path: Path
    context_pack: dict[str, Any]
    snippets: tuple[dict[str, Any], ...]
    selected_files: tuple[str, ...]


def build_code_task_context_pack(
    run_dir: Path,
    *,
    query: str | None = None,
    top_k: int = DEFAULT_CONTEXT_TOP_K,
    max_files: int = DEFAULT_MAX_FILES,
    max_source_chars_per_file: int = DEFAULT_MAX_SOURCE_CHARS_PER_FILE,
    max_total_chars: int = DEFAULT_MAX_TOTAL_CHARS,
    refresh_map: bool = False,
) -> CodeTaskContextPackResult:
    """Build a prompt-ready context pack from locate results and workspace files.

    Args:
        run_dir: Code-task run directory created by ``code-task init``.
        query: Optional locate query. When omitted, ``code_task/task.md`` is
            used by the locate step.
        top_k: Candidate budget passed to locate for each candidate group.
        max_files: Maximum snippets included across editable and evidence
            groups.
        max_source_chars_per_file: Per-file snippet character budget.
        max_total_chars: Total text budget across all snippets.
        refresh_map: Rebuild repo-map artifacts before locating context.

    Returns:
        Paths and selected files for the generated context pack.

    Raises:
        ValueError: If any budget is less than one.
        FileNotFoundError: If required code-task artifacts are missing.
        RuntimeError: If ``run_dir`` is not a code-task workflow.
    """

    if top_k < 1:
        raise ValueError("top_k must be at least 1")
    if max_files < 1:
        raise ValueError("max_files must be at least 1")
    if max_source_chars_per_file < 1:
        raise ValueError("max_source_chars_per_file must be at least 1")
    if max_total_chars < 1:
        raise ValueError("max_total_chars must be at least 1")

    root = Path(run_dir)
    paths = code_task_paths(root)
    manifest = load_code_task_manifest(root)
    locate = locate_code_task_context(
        root,
        query=query,
        top_k=top_k,
        refresh_map=refresh_map,
        include_read_only=True,
    )
    locate_data = read_json(locate.results_path)
    if not isinstance(locate_data, dict):
        raise RuntimeError(f"Expected JSON object in {locate.results_path}")

    context_dir = _next_context_dir(paths.task_dir / "context_packs")
    snippets_path = context_dir / "selected_snippets.jsonl"
    context_pack_path = context_dir / "context_pack.json"
    prompt_context_path = context_dir / "prompt_context.md"

    snippets, omitted = _collect_snippets(
        paths.workspace_dir,
        locate_data,
        max_files=max_files,
        max_chars_per_file=max_source_chars_per_file,
        max_total_chars=max_total_chars,
    )
    context_pack = {
        "schema_version": 1,
        "generated_at": utcnow_iso(),
        "query": locate_data.get("query", ""),
        "source": {
            "locate_results": "code_task/meta/locate_results.json",
            "repo_map": "code_task/meta/repo_map.json",
        },
        "budget": {
            "top_k": top_k,
            "max_files": max_files,
            "max_source_chars_per_file": max_source_chars_per_file,
            "max_total_chars": max_total_chars,
            "used_chars": sum(int(row.get("chars", 0)) for row in snippets),
        },
        "selected_files": [
            _snippet_manifest_row(row)
            for row in snippets
        ],
        "omitted": omitted,
        "artifacts": {
            "selected_snippets": "selected_snippets.jsonl",
            "prompt_context": "prompt_context.md",
        },
    }
    write_jsonl(snippets_path, snippets)
    write_json(context_pack_path, context_pack)
    write_text(prompt_context_path, render_prompt_context(context_pack, snippets))
    _update_manifest_after_context(root, manifest, context_dir, context_pack)
    return CodeTaskContextPackResult(
        run_dir=root,
        context_dir=context_dir,
        context_pack_path=context_pack_path,
        prompt_context_path=prompt_context_path,
        snippets_path=snippets_path,
        locate_results_path=locate.results_path,
        selected_files=tuple(str(row["path"]) for row in snippets),
    )


def load_latest_code_task_context_pack(run_dir: Path) -> LoadedCodeTaskContextPack | None:
    """Load the latest context pack recorded in ``manifest.json``.

    Args:
        run_dir: Code-task run directory.

    Returns:
        Loaded context-pack data, or ``None`` when no latest context pack has
        been generated yet.

    Raises:
        RuntimeError: If the manifest points outside the run directory or a
            context-pack artifact is malformed.
    """

    root = Path(run_dir)
    manifest = load_code_task_manifest(root)
    context_section = manifest.get("context_pack")
    if not isinstance(context_section, dict):
        return None
    latest = context_section.get("latest")
    if not isinstance(latest, str) or not latest.strip():
        return None
    context_pack_path = _run_file(root, latest)
    if context_pack_path is None or not context_pack_path.is_file():
        return None
    context_pack = read_json(context_pack_path)
    if not isinstance(context_pack, dict):
        raise RuntimeError(f"Expected JSON object in {context_pack_path}")

    artifacts = context_pack.get("artifacts")
    artifacts = artifacts if isinstance(artifacts, dict) else {}
    snippets_name = artifacts.get("selected_snippets", "selected_snippets.jsonl")
    prompt_name = artifacts.get("prompt_context", "prompt_context.md")
    snippets_path = _context_child(context_pack_path.parent, str(snippets_name))
    prompt_context_path = _context_child(context_pack_path.parent, str(prompt_name))
    snippets = tuple(row for row in read_jsonl(snippets_path) if isinstance(row, dict))
    return LoadedCodeTaskContextPack(
        run_dir=root,
        context_pack_path=context_pack_path,
        prompt_context_path=prompt_context_path,
        snippets_path=snippets_path,
        context_pack=context_pack,
        snippets=snippets,
        selected_files=tuple(_snippet_paths(snippets)),
    )


def render_prompt_context(
    context_pack: dict[str, Any],
    snippets: list[dict[str, Any]],
) -> str:
    """Render a context pack into Markdown suitable for an LLM prompt."""

    editable = [row for row in snippets if row.get("access_role") == "editable"]
    evidence = [row for row in snippets if row.get("access_role") != "editable"]
    budget = context_pack.get("budget")
    budget = budget if isinstance(budget, dict) else {}
    omitted = context_pack.get("omitted")
    omitted = omitted if isinstance(omitted, dict) else {}
    lines = [
        "# Code Task Context Pack",
        "",
        f"Generated: `{context_pack.get('generated_at', '')}`",
        "",
        "## Query",
        "",
        _truncate(str(context_pack.get("query", "")).strip(), 1000) or "(empty)",
        "",
        "## Budget",
        "",
        f"- Selected files: `{len(snippets)}` / `{budget.get('max_files', '')}`",
        f"- Used chars: `{budget.get('used_chars', 0)}` / `{budget.get('max_total_chars', '')}`",
        "",
        "## Editable Targets",
        "",
        _render_snippet_group(editable),
        "",
        "## Read-Only Evidence",
        "",
        _render_snippet_group(evidence),
        "",
        "## Omitted",
        "",
        f"- Candidate files omitted by file budget: `{omitted.get('candidate_files', 0)}`",
        f"- Snippets omitted by char budget: `{omitted.get('char_budget_files', 0)}`",
        f"- Missing/unreadable files: `{omitted.get('missing_or_unreadable_files', 0)}`",
        "",
    ]
    return "\n".join(lines)


def _collect_snippets(
    workspace_dir: Path,
    locate_data: dict[str, Any],
    *,
    max_files: int,
    max_chars_per_file: int,
    max_total_chars: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    candidates = _ordered_candidates(locate_data)
    snippets: list[dict[str, Any]] = []
    omitted = {
        "candidate_files": 0,
        "char_budget_files": 0,
        "missing_or_unreadable_files": 0,
        "details": [],
    }
    used_chars = 0
    seen: set[str] = set()
    for candidate in candidates:
        path = str(candidate.get("path", "")).strip()
        if not path or path in seen:
            continue
        seen.add(path)
        if len(snippets) >= max_files:
            omitted["candidate_files"] += 1
            _append_detail(omitted, path, "file_budget")
            continue
        remaining = max_total_chars - used_chars
        if remaining <= 0:
            omitted["char_budget_files"] += 1
            _append_detail(omitted, path, "total_char_budget")
            continue
        file_path = workspace_file(workspace_dir, path)
        if file_path is None or not file_path.is_file():
            omitted["missing_or_unreadable_files"] += 1
            _append_detail(omitted, path, "missing_or_outside_workspace")
            continue
        try:
            text = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            omitted["missing_or_unreadable_files"] += 1
            _append_detail(omitted, path, "unreadable")
            continue
        limit = min(max_chars_per_file, remaining)
        snippet_text, truncated = _clip_text(text, limit)
        snippets.append(
            {
                "schema_version": 1,
                "path": path,
                "access_role": candidate.get("access_role", "editable"),
                "score": candidate.get("score", 0),
                "reasons": candidate.get("reasons", []),
                "chars": len(snippet_text),
                "source_chars": len(text),
                "truncated": truncated,
                "text": snippet_text,
            }
        )
        used_chars += len(snippet_text)
    return snippets, omitted


def _ordered_candidates(locate_data: dict[str, Any]) -> list[dict[str, Any]]:
    editable = _object_list(locate_data.get("editable_targets"))
    evidence = _object_list(locate_data.get("read_only_evidence"))
    combined: list[dict[str, Any]] = []
    combined.extend(editable)
    combined.extend(evidence)
    return combined


def _next_context_dir(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    max_id = 0
    for child in root.iterdir():
        if not child.is_dir():
            continue
        match = re.fullmatch(r"context-(\d{3})", child.name)
        if match:
            max_id = max(max_id, int(match.group(1)))
    return root / f"context-{max_id + 1:03d}"


def _snippet_manifest_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "path": row.get("path"),
        "access_role": row.get("access_role"),
        "score": row.get("score"),
        "chars": row.get("chars"),
        "source_chars": row.get("source_chars"),
        "truncated": row.get("truncated"),
        "reasons": row.get("reasons", []),
    }


def _update_manifest_after_context(
    run_dir: Path,
    manifest: dict[str, Any],
    context_dir: Path,
    context_pack: dict[str, Any],
) -> None:
    rel_context = context_dir.relative_to(run_dir).as_posix()
    layout = manifest.get("layout")
    if not isinstance(layout, dict):
        layout = {}
    layout.update(
        {
            "context_packs": "code_task/context_packs",
            "latest_context_pack": f"{rel_context}/context_pack.json",
            "latest_prompt_context": f"{rel_context}/prompt_context.md",
        }
    )
    manifest["layout"] = layout
    manifest["context_pack"] = {
        "status": "completed",
        "generated_at": context_pack.get("generated_at"),
        "latest": f"{rel_context}/context_pack.json",
        "prompt_context": f"{rel_context}/prompt_context.md",
        "selected_files": [
            row.get("path")
            for row in _object_list(context_pack.get("selected_files"))
            if row.get("path")
        ],
        "budget": context_pack.get("budget", {}),
    }
    save_code_task_manifest(run_dir, manifest)


def _run_file(run_dir: Path, relative_path: str) -> Path | None:
    rel = Path(relative_path)
    if rel.is_absolute() or ".." in rel.parts:
        return None
    root = run_dir.resolve()
    path = (root / rel).resolve()
    if not is_relative_to(path, root):
        return None
    return path


def _context_child(context_dir: Path, name: str) -> Path:
    rel = Path(name)
    if rel.is_absolute() or ".." in rel.parts:
        raise RuntimeError(f"Context-pack artifact path escapes context directory: {name}")
    root = context_dir.resolve()
    path = (root / rel).resolve()
    if not is_relative_to(path, root):
        raise RuntimeError(f"Context-pack artifact path escapes context directory: {name}")
    if not path.is_file():
        raise FileNotFoundError(f"Missing context-pack artifact: {path}")
    return path


def _snippet_paths(snippets: tuple[dict[str, Any], ...]) -> list[str]:
    paths: list[str] = []
    for row in snippets:
        path = row.get("path")
        if isinstance(path, str) and path and path not in paths:
            paths.append(path)
    return paths


def _render_snippet_group(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "- No snippets selected."
    blocks: list[str] = []
    for row in rows:
        path = str(row.get("path", ""))
        role = str(row.get("access_role", ""))
        reason = "; ".join(str(item) for item in row.get("reasons", []) if item)
        header = [
            f"### `{path}`",
            "",
            f"- Role: `{role}`",
            f"- Score: `{row.get('score', 0)}`",
            f"- Chars: `{row.get('chars', 0)}` / `{row.get('source_chars', 0)}`",
        ]
        if reason:
            header.append(f"- Why: {_truncate(reason, 220)}")
        header.extend(["", "~~~text", str(row.get("text", "")), "~~~"])
        blocks.append("\n".join(header))
    return "\n\n".join(blocks)


def _append_detail(omitted: dict[str, Any], path: str, reason: str) -> None:
    details = omitted.get("details")
    if not isinstance(details, list):
        details = []
        omitted["details"] = details
    details.append({"path": path, "reason": reason})


def _clip_text(text: str, max_chars: int) -> tuple[str, bool]:
    if len(text) <= max_chars:
        return text, False
    if max_chars <= 20:
        return text[:max_chars], True
    return text[: max_chars - 18].rstrip() + "\n... [truncated]\n", True


def _object_list(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _truncate(text: str, max_chars: int) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= max_chars:
        return normalized
    return normalized[: max(0, max_chars - 3)].rstrip() + "..."
