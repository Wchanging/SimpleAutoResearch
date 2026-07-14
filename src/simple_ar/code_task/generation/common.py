from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any, Mapping


def safe_relative_path(value: str) -> str:
    """Return a normalized relative POSIX path, or an empty string if unsafe."""

    text = value.replace("\\", "/").strip().lstrip("/")
    path = PurePosixPath(text)
    if not text or path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        return ""
    return path.as_posix()


def text(value: object) -> str:
    return str(value).strip() if value is not None else ""


def string_list(value: Any, *, limit: int | None = None, tail: bool = False) -> list[str]:
    rows = [str(item).strip() for item in value if str(item).strip()] if isinstance(value, list) else []
    if limit is None:
        return rows
    return rows[-limit:] if tail else rows[:limit]


def mapping_list(value: Any, *, limit: int | None = None, tail: bool = False) -> list[dict[str, Any]]:
    rows = [dict(row) for row in value if isinstance(row, Mapping)] if isinstance(value, list) else []
    if limit is None:
        return rows
    return rows[-limit:] if tail else rows[:limit]


def scalar_list(value: object, *, limit: int | None = None, tail: bool = False) -> list[str]:
    if isinstance(value, list):
        rows = [str(item).strip() for item in value if str(item).strip()]
    elif isinstance(value, str) and value.strip():
        rows = [value.strip()]
    else:
        rows = []
    if limit is None:
        return rows
    return rows[-limit:] if tail else rows[:limit]


def contains_any(value: str, needles: tuple[str, ...]) -> bool:
    return any(needle in value for needle in needles)


def clip_text(value: str, *, max_chars: int) -> str:
    if len(value) <= max_chars:
        return value
    return value[: max(0, max_chars - 1)].rstrip() + "..."
