from __future__ import annotations

from fnmatch import fnmatchcase
from pathlib import PurePosixPath
from typing import Any, Iterable


DEFAULT_PROTECTED_EDIT_PATTERNS = (
    "tests/**",
    "**/tests/**",
    "test_*.py",
    "**/test_*.py",
    "*_test.py",
    "**/*_test.py",
    "conftest.py",
    "**/conftest.py",
    "benchmark.py",
    "**/benchmark.py",
    "bench.py",
    "**/bench.py",
    "*benchmark*.py",
    "**/*benchmark*.py",
)

DEFAULT_EDIT_SCOPE_MODE = "source_only_default"


def default_edit_scope() -> dict[str, Any]:
    """Return the default code-task edit policy stored in manifests.

    The policy keeps benchmark and test files available as read-only evidence
    while preventing automated patches from changing validation targets.
    """

    return {
        "mode": DEFAULT_EDIT_SCOPE_MODE,
        "protected_patterns": list(DEFAULT_PROTECTED_EDIT_PATTERNS),
        "notes": [
            "Protected files may be indexed and read as context.",
            "Patch proposals and apply-edits must not modify protected files.",
        ],
    }


def protected_patterns_from_manifest(manifest: dict[str, Any]) -> tuple[str, ...]:
    """Resolve protected edit patterns from a code-task manifest.

    Older runs do not have an ``edit_scope`` section, so callers fall back to
    the default source-only policy.
    """

    edit_scope = manifest.get("edit_scope", {})
    if not isinstance(edit_scope, dict):
        return DEFAULT_PROTECTED_EDIT_PATTERNS
    patterns = edit_scope.get("protected_patterns")
    if not isinstance(patterns, list):
        return DEFAULT_PROTECTED_EDIT_PATTERNS
    normalized = tuple(_normalize_pattern(item) for item in patterns if _normalize_pattern(item))
    return normalized or DEFAULT_PROTECTED_EDIT_PATTERNS


def is_protected_edit_path(
    relative_path: str,
    *,
    protected_patterns: Iterable[str] | None = None,
) -> bool:
    """Return whether a workspace-relative path is read-only for patches."""

    normalized = normalize_workspace_path(relative_path)
    if not normalized:
        return True
    patterns = tuple(protected_patterns or DEFAULT_PROTECTED_EDIT_PATTERNS)
    lower_path = normalized.lower()
    return any(fnmatchcase(lower_path, pattern.lower()) for pattern in patterns)


def protected_edit_paths(
    paths: Iterable[str],
    *,
    protected_patterns: Iterable[str] | None = None,
) -> list[str]:
    """Return paths that are protected by the current edit policy."""

    result: list[str] = []
    for path in paths:
        normalized = normalize_workspace_path(path)
        if normalized and is_protected_edit_path(normalized, protected_patterns=protected_patterns):
            result.append(normalized)
    return result


def editable_paths(
    paths: Iterable[str],
    *,
    protected_patterns: Iterable[str] | None = None,
) -> list[str]:
    """Return paths that may be used as editable patch context."""

    result: list[str] = []
    for path in paths:
        normalized = normalize_workspace_path(path)
        if normalized and not is_protected_edit_path(normalized, protected_patterns=protected_patterns):
            result.append(normalized)
    return result


def normalize_workspace_path(path: str) -> str:
    """Normalize a user or model supplied path to a POSIX relative form."""

    text = str(path).replace("\\", "/").strip()
    if not text:
        return ""
    pure = PurePosixPath(text)
    if pure.is_absolute() or ".." in pure.parts:
        return ""
    return pure.as_posix()


def _normalize_pattern(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return value.replace("\\", "/").strip()
