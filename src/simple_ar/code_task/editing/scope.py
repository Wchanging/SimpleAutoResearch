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
    ".env",
    ".env.*",
    "**/.env",
    "**/.env.*",
    "*secret*",
    "**/*secret*",
    "*credential*",
    "**/*credential*",
)

DEFAULT_EDIT_SCOPE_MODE = "source_only_default"
DEFAULT_ALLOWED_EDIT_PATTERNS: tuple[str, ...] = ()


def default_edit_scope(
    *,
    mode: str | None = None,
    allowed_patterns: Iterable[str] | None = None,
    protected_patterns: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Return the default code-task edit policy stored in manifests.

    The policy keeps benchmark and test files available as read-only evidence
    while preventing automated patches from changing validation targets.
    User supplied protected patterns are additive so local configs cannot
    accidentally remove the default safety baseline.
    """

    allowed = _normalized_unique(allowed_patterns or DEFAULT_ALLOWED_EDIT_PATTERNS)
    protected = _normalized_unique(
        [*DEFAULT_PROTECTED_EDIT_PATTERNS, *(protected_patterns or ())]
    )
    return {
        "mode": mode or DEFAULT_EDIT_SCOPE_MODE,
        "allowed_patterns": list(allowed),
        "protected_patterns": list(protected),
        "notes": [
            "When allowed_patterns is empty, any normalized workspace path may be edited unless protected.",
            "When allowed_patterns is set, patch proposals must match one allowed pattern.",
            "Protected files may be indexed and read as context.",
            "Patch proposals and apply-edits must not modify protected files.",
        ],
    }


def allowed_patterns_from_manifest(manifest: dict[str, Any]) -> tuple[str, ...]:
    """Resolve edit allowlist patterns from a code-task manifest.

    An empty tuple means the run uses the historical behavior: all normalized
    workspace paths are candidates unless they match a protected pattern.
    """

    edit_scope = manifest.get("edit_scope", {})
    if not isinstance(edit_scope, dict):
        return DEFAULT_ALLOWED_EDIT_PATTERNS
    patterns = edit_scope.get("allowed_patterns")
    if not isinstance(patterns, list):
        return DEFAULT_ALLOWED_EDIT_PATTERNS
    return _normalized_unique(patterns)


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
    normalized = _normalized_unique(patterns)
    return normalized or DEFAULT_PROTECTED_EDIT_PATTERNS


def edit_scope_from_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    """Return normalized edit-scope policy fields from a manifest."""

    edit_scope = manifest.get("edit_scope", {})
    mode = DEFAULT_EDIT_SCOPE_MODE
    if isinstance(edit_scope, dict) and isinstance(edit_scope.get("mode"), str):
        mode = str(edit_scope["mode"]).strip() or DEFAULT_EDIT_SCOPE_MODE
    return {
        "mode": mode,
        "allowed_patterns": list(allowed_patterns_from_manifest(manifest)),
        "protected_patterns": list(protected_patterns_from_manifest(manifest)),
    }


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


def is_edit_allowed_path(
    relative_path: str,
    *,
    allowed_patterns: Iterable[str] | None = None,
    protected_patterns: Iterable[str] | None = None,
) -> bool:
    """Return whether a workspace-relative path may be modified."""

    return edit_scope_rejection_reason(
        relative_path,
        allowed_patterns=allowed_patterns,
        protected_patterns=protected_patterns,
    ) is None


def edit_scope_rejection_reason(
    relative_path: str,
    *,
    allowed_patterns: Iterable[str] | None = None,
    protected_patterns: Iterable[str] | None = None,
) -> str | None:
    """Return why a path is not editable, or ``None`` when it is allowed."""

    normalized = normalize_workspace_path(relative_path)
    if not normalized:
        return "invalid_workspace_path"
    allowed = tuple(allowed_patterns or DEFAULT_ALLOWED_EDIT_PATTERNS)
    if allowed:
        lower_path = normalized.lower()
        if not any(fnmatchcase(lower_path, pattern.lower()) for pattern in allowed):
            return "outside_allowed_patterns"
    if is_protected_edit_path(normalized, protected_patterns=protected_patterns):
        return "protected_path"
    return None


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
    allowed_patterns: Iterable[str] | None = None,
    protected_patterns: Iterable[str] | None = None,
) -> list[str]:
    """Return paths that may be used as editable patch context."""

    result: list[str] = []
    for path in paths:
        normalized = normalize_workspace_path(path)
        if normalized and is_edit_allowed_path(
            normalized,
            allowed_patterns=allowed_patterns,
            protected_patterns=protected_patterns,
        ):
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


def _normalized_unique(values: Iterable[object]) -> tuple[str, ...]:
    result: list[str] = []
    for value in values:
        pattern = _normalize_pattern(value)
        if pattern and pattern not in result:
            result.append(pattern)
    return tuple(result)
