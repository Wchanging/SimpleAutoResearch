from __future__ import annotations

"""Deterministic failure-evidence graph for code-task repair.

The graph is deliberately compact and benchmark-agnostic. It does not try to
solve the bug; it preserves the strongest runtime/validation signals and a
small ranked set of source files that can be handed to an LLM repair step.
"""

import re
from pathlib import Path
from typing import Any, Mapping

from simple_ar.code_task.runtime.state import is_relative_to


TRACEBACK_RE = re.compile(r"Traceback \(most recent call last\):[\s\S]*", re.MULTILINE)
FILE_LINE_RE = re.compile(r'File "([^"]+)", line (\d+)(?:, in ([^\n]+))?')

RUNTIME_SIGNAL_TOKENS = (
    "Traceback",
    "Experiment failed",
    "Failed to execute",
    "AssertionError",
    "AttributeError",
    "ModuleNotFoundError",
    "ImportError",
    "SyntaxError",
    "NameError",
    "TypeError",
    "ValueError",
    "KeyError",
    "IndexError",
    "RuntimeError",
    "has no attribute",
    "not found",
    "missing",
    "cannot proceed",
    "ERROR",
    "FAILED",
    "Timed out",
)


def build_failure_graph(
    *,
    workspace_dir: Path,
    stdout: str,
    stderr: str,
    validation: Mapping[str, Any] | None = None,
    changed_files: list[str] | None = None,
    max_candidates: int = 10,
) -> dict[str, Any]:
    """Build a deterministic repair-evidence graph from execution artifacts."""

    validation = validation or {}
    changed_files = changed_files or []
    combined_runtime = "\n".join([stderr, stdout])
    traceback_block = _traceback_block(combined_runtime)
    runtime_signals = _runtime_signal_lines(stdout=stdout, stderr=stderr)
    validation_signals = _validation_signal_lines(validation)
    traceback_files = _implicated_files(traceback_block or combined_runtime, workspace_dir)
    validation_files = _validation_issue_files(validation, workspace_dir)
    signal_files = _source_signal_files(
        workspace_dir,
        "\n".join([combined_runtime, "\n".join(runtime_signals), "\n".join(validation_signals)]),
    )
    primary_signal = _primary_signal(
        traceback_block=traceback_block,
        runtime_signals=runtime_signals,
        validation_signals=validation_signals,
    )
    candidates = _rank_candidates(
        traceback_files=traceback_files,
        signal_files=signal_files,
        validation_files=validation_files,
        changed_files=changed_files,
        max_candidates=max_candidates,
    )
    return {
        "schema_version": "code_task_failure_graph.v1",
        "primary_signal": primary_signal,
        "runtime_signals": runtime_signals[:16],
        "validation_signals": validation_signals[:12],
        "traceback_files": traceback_files,
        "signal_matched_files": signal_files,
        "validation_files": validation_files,
        "changed_files": changed_files[:24],
        "candidate_files": candidates,
        "traceback": _clip(traceback_block, max_chars=6000),
        "signal_terms": _failure_terms("\n".join([combined_runtime, primary_signal]))[:24],
    }


def _traceback_block(text: str) -> str:
    match = TRACEBACK_RE.search(text)
    return match.group(0).strip() if match else ""


def _runtime_signal_lines(*, stdout: str, stderr: str) -> list[str]:
    found: list[str] = []
    for source_name, text in (("stderr", stderr), ("stdout", stdout)):
        for line in text.splitlines():
            stripped = " ".join(line.strip().split())
            if not stripped:
                continue
            if any(token in stripped for token in RUNTIME_SIGNAL_TOKENS):
                found.append(_clip(f"{source_name}: {stripped}", max_chars=260))
    return _dedupe(found)


def _primary_signal(
    *,
    traceback_block: str,
    runtime_signals: list[str],
    validation_signals: list[str],
) -> str:
    if traceback_block:
        last = next((line.strip() for line in reversed(traceback_block.splitlines()) if line.strip()), "")
        if last:
            return _clip(last, max_chars=260)
    if runtime_signals:
        return runtime_signals[0]
    if validation_signals:
        return validation_signals[0]
    return ""


def _implicated_files(text: str, workspace_dir: Path) -> list[str]:
    workspace = workspace_dir.resolve()
    files: list[str] = []
    for match in FILE_LINE_RE.finditer(text):
        raw = match.group(1)
        path = Path(raw)
        candidates = [path] if path.is_absolute() else [workspace / path, path]
        for candidate in candidates:
            try:
                resolved = candidate.resolve()
            except OSError:
                continue
            if not is_relative_to(resolved, workspace):
                continue
            rel = resolved.relative_to(workspace).as_posix()
            if rel not in files:
                files.append(rel)
            break
    return files


def _validation_issue_files(validation: Mapping[str, Any], workspace_dir: Path) -> list[str]:
    issues = validation.get("issues", [])
    rows = issues if isinstance(issues, list) else []
    workspace = workspace_dir.resolve()
    files: list[str] = []
    for issue in rows:
        if not isinstance(issue, Mapping):
            continue
        value = issue.get("path")
        if not isinstance(value, str) or not value:
            continue
        rel = Path(value)
        if rel.is_absolute() or ".." in rel.parts:
            continue
        resolved = (workspace / rel).resolve()
        if not is_relative_to(resolved, workspace):
            continue
        normalized = rel.as_posix()
        if normalized not in files:
            files.append(normalized)
    return files


def _validation_signal_lines(validation: Mapping[str, Any]) -> list[str]:
    issues = validation.get("issues", [])
    rows = issues if isinstance(issues, list) else []
    result: list[str] = []
    for issue in rows:
        if not isinstance(issue, Mapping):
            continue
        severity = str(issue.get("severity", "issue"))
        code = str(issue.get("code", ""))
        path = str(issue.get("path", ""))
        message = str(issue.get("message", ""))
        result.append(_clip(f"{severity} {code} in {path}: {message}", max_chars=260))
    return result


def _source_signal_files(workspace_dir: Path, signal_text: str) -> list[str]:
    terms = _failure_terms(signal_text)
    if not terms or not workspace_dir.is_dir():
        return []
    ranked: list[tuple[int, str]] = []
    workspace = workspace_dir.resolve()
    for path in workspace.rglob("*.py"):
        if not path.is_file():
            continue
        if any(part.startswith(".") or part in {"__pycache__", ".venv", "venv"} for part in path.parts):
            continue
        try:
            source = path.read_text(encoding="utf-8", errors="ignore").lower()
        except OSError:
            continue
        score = sum(source.count(term.lower()) for term in terms)
        if score <= 0:
            continue
        rel = path.resolve().relative_to(workspace).as_posix()
        ranked.append((-score, rel))
    return [rel for _, rel in sorted(ranked)[:12]]


def _rank_candidates(
    *,
    traceback_files: list[str],
    signal_files: list[str],
    validation_files: list[str],
    changed_files: list[str],
    max_candidates: int,
) -> list[str]:
    result: list[str] = []
    for group in (traceback_files, signal_files, validation_files, changed_files):
        for path in group:
            normalized = path.replace("\\", "/").strip().lstrip("/")
            if normalized and normalized not in result:
                result.append(normalized)
            if len(result) >= max_candidates:
                return result
    return result


def _failure_terms(text: str) -> list[str]:
    terms: list[str] = []
    for quoted in re.findall(r"'([^']{2,80})'|\"([^\"]{2,80})\"", text):
        value = next((part for part in quoted if part), "")
        if value:
            terms.append(value.lower())
    stop = {
        "the",
        "and",
        "for",
        "with",
        "object",
        "failed",
        "error",
        "cannot",
        "proceed",
        "traceback",
        "most",
        "recent",
        "call",
        "last",
    }
    terms.extend(
        token.lower()
        for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", text)
        if token.lower() not in stop
    )
    return _dedupe(terms)[:40]


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value not in result:
            result.append(value)
    return result


def _clip(value: str, *, max_chars: int) -> str:
    if len(value) <= max_chars:
        return value
    return value[: max_chars - 3].rstrip() + "..."
