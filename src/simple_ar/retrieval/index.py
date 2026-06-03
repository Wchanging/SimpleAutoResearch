from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from simple_ar.core.artifacts import write_json


IGNORED_DIR_NAMES = {
    ".git",
    ".hg",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".simple_ar_cache",
    ".svn",
    ".tox",
    ".venv",
    "__pycache__",
    "node_modules",
    "venv",
}

IGNORED_FILE_NAMES = {
    "activity_log.jsonl",
    "artifact_chunks.jsonl",
    "artifact_index.json",
    "artifact_search_results.json",
    "cache_manifest.json",
    "contract.json",
    "code_links.jsonl",
    "fulltext_manifest.json",
    "claim_cards.jsonl",
    "chunks.jsonl",
    "coverage_report.json",
    "dataset_cards.jsonl",
    "decision_log.jsonl",
    "documents.jsonl",
    "evidence_ledger.jsonl",
    "evidence_pack.json",
    "evidence_pack.md",
    "evidence_review.md",
    "eval_report.json",
    "eval_report.md",
    "experiment_contract.json",
    "experiment_contract.md",
    "external_agent_backend.md",
    "gap_summary.md",
    "index_meta.json",
    "idea_candidates.jsonl",
    "method_cards.jsonl",
    "novelty_checks.jsonl",
    "paper_cards.jsonl",
    "retrieval_selection.jsonl",
    "research_plan.json",
    "retrieval_rounds.jsonl",
    "screening_decisions.jsonl",
    "tool_adapter_contract.json",
    "tool_adapter_contract.md",
    "tool_context.json",
    "tool_context.md",
    "tool_trace.jsonl",
    "state.json",
    "source_plan.json",
}

IGNORED_SUFFIXES = {".pyc", ".pyo", ".pyd"}
TEXT_SUFFIXES = {
    ".bib",
    ".cfg",
    ".csv",
    ".ini",
    ".log",
    ".rst",
    ".text",
    ".toml",
    ".tsv",
    ".txt",
    ".yaml",
    ".yml",
}

STAGE_DIR_RE = re.compile(r"^(?P<number>\d{2})-(?P<slug>[a-z0-9-]+)(?:/|$)")
MAX_SUMMARY_BYTES = 8192


def build_artifact_index(run_dir: Path, *, write: bool = True) -> dict[str, Any]:
    """Index inspectable files under a run directory.

    Args:
        run_dir: Root run directory to scan.
        write: When true, save the index to ``artifact_index.json`` in ``run_dir``.

    Returns:
        A JSON-serializable index containing relative paths, file kinds, stages,
        hashes, sizes, timestamps, summaries, and simple tags.

    Raises:
        FileNotFoundError: If ``run_dir`` does not exist.
        NotADirectoryError: If ``run_dir`` is not a directory.
    """
    root = Path(run_dir)
    if not root.exists():
        raise FileNotFoundError(f"Run directory does not exist: {root}")
    if not root.is_dir():
        raise NotADirectoryError(f"Run path is not a directory: {root}")

    artifacts = [_index_file(root, path) for path in _iter_artifact_files(root)]
    artifacts.sort(key=lambda item: item["path"])

    index = {
        "schema_version": 1,
        "root": str(root),
        "generated_at": _utcnow_iso(),
        "artifact_count": len(artifacts),
        "artifacts": artifacts,
    }
    if write:
        write_json(root / "artifact_index.json", index)
    return index


def infer_stage(relative_path: str) -> str | None:
    """Infer the pipeline stage slug from a relative artifact path."""
    match = STAGE_DIR_RE.match(relative_path)
    if match is None:
        return None
    return match.group("slug")


def kind_for_path(path: Path) -> str:
    """Classify a path into a coarse artifact kind used by retrieval."""
    suffix = path.suffix.lower()
    if suffix in {".md", ".markdown"}:
        return "markdown"
    if suffix == ".json":
        return "json"
    if suffix == ".jsonl":
        return "jsonl"
    if suffix == ".py":
        return "python"
    if suffix in TEXT_SUFFIXES:
        return "text"
    return "text" if _looks_like_text(path) else "other"


def _iter_artifact_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for current, dirnames, filenames in os.walk(root):
        current_path = Path(current)
        dirnames[:] = [
            dirname
            for dirname in dirnames
            if not _should_ignore_dir(dirname)
            and not _has_ignored_parent((current_path / dirname).relative_to(root))
        ]
        for filename in filenames:
            path = current_path / filename
            if _should_ignore_file(path):
                continue
            rel = path.relative_to(root)
            if _has_ignored_parent(rel):
                continue
            files.append(path)
    return files


def _index_file(root: Path, path: Path) -> dict[str, Any]:
    rel_path = path.relative_to(root).as_posix()
    stat = path.stat()
    kind = kind_for_path(path)
    stage = infer_stage(rel_path)
    return {
        "path": rel_path,
        "stage": stage,
        "kind": kind,
        "bytes": stat.st_size,
        "sha256": _sha256(path),
        "mtime": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(
            timespec="seconds"
        ),
        "summary": _summary_for_file(path, kind),
        "tags": _tags_for_file(rel_path, kind, stage),
    }


def _should_ignore_dir(dirname: str) -> bool:
    return dirname in IGNORED_DIR_NAMES or dirname.startswith(".")


def _should_ignore_file(path: Path) -> bool:
    name = path.name
    return (
        name in IGNORED_FILE_NAMES
        or name.startswith(".")
        or path.suffix.lower() in IGNORED_SUFFIXES
    )


def _has_ignored_parent(relative_path: Path) -> bool:
    return any(part in IGNORED_DIR_NAMES or part.startswith(".") for part in relative_path.parts[:-1])


def _looks_like_text(path: Path) -> bool:
    try:
        sample = path.read_bytes()[:2048]
    except OSError:
        return False
    if b"\0" in sample:
        return False
    try:
        sample.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _summary_for_file(path: Path, kind: str) -> str:
    if kind == "other":
        return ""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    preview = text[:MAX_SUMMARY_BYTES]
    if kind == "json":
        summary = _json_summary(preview)
        if summary:
            return summary
    if kind == "python":
        summary = _python_summary(preview)
        if summary:
            return summary
    for line in preview.splitlines():
        stripped = " ".join(line.strip().split())
        if stripped:
            return _truncate(stripped, 180)
    return ""


def _json_summary(text: str) -> str:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return ""
    if isinstance(data, dict):
        keys = ", ".join(str(key) for key in list(data)[:8])
        suffix = "..." if len(data) > 8 else ""
        return f"json object keys: {keys}{suffix}"
    if isinstance(data, list):
        return f"json list with {len(data)} item(s)"
    return f"json {type(data).__name__}"


def _python_summary(text: str) -> str:
    imports: list[str] = []
    definitions: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("import ") or stripped.startswith("from "):
            imports.append(stripped)
        elif stripped.startswith("def ") or stripped.startswith("class "):
            definitions.append(stripped.split(":", 1)[0])
    pieces: list[str] = []
    if definitions:
        pieces.append("definitions: " + ", ".join(definitions[:5]))
    if imports:
        pieces.append("imports: " + ", ".join(imports[:5]))
    return _truncate("; ".join(pieces), 180)


def _tags_for_file(relative_path: str, kind: str, stage: str | None) -> list[str]:
    tags = [kind]
    if stage:
        tags.append(stage)
    name = Path(relative_path).name.lower()
    if "result" in name or "metric" in name:
        tags.append("results")
    if "report" in name or kind == "markdown":
        tags.append("narrative")
    if "paper" in name or "reference" in name or name.endswith(".bib"):
        tags.append("literature")
    return sorted(set(tags))


def _truncate(text: str, max_chars: int) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= max_chars:
        return normalized
    return normalized[: max_chars - 3].rstrip() + "..."


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
