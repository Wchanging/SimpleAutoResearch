from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any, Mapping


def normalize_plan_path(value: object) -> str:
    """Return a safe path relative to the generated project root."""

    text = _strip_generated_root_prefix(str(value or ""))
    text = text.replace("\\", "/").strip().lstrip("/")
    if not text or text.startswith("../") or "/../" in text or text == "..":
        return ""
    path = PurePosixPath(text)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        return ""
    return path.as_posix()


def normalize_dependency_paths(value: object, *, limit: int) -> list[str]:
    return [path for path in (normalize_plan_path(item) for item in _list(value)[:limit]) if path]


def dedupe_file_rows(
    files: list[dict[str, Any]],
    *,
    dependency_limit: int,
    public_api_limit: int,
    acceptance_limit: int,
) -> list[dict[str, Any]]:
    by_path: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for row in files:
        path = normalize_plan_path(row.get("path"))
        if not path:
            continue
        row = dict(row)
        row["path"] = path
        if path not in by_path:
            by_path[path] = row
            order.append(path)
            continue
        by_path[path] = _merge_file_rows(
            by_path[path],
            row,
            dependency_limit=dependency_limit,
            public_api_limit=public_api_limit,
            acceptance_limit=acceptance_limit,
        )
    return [by_path[path] for path in order if path in by_path]


def entrypoint_first(files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    entrypoints = [row for row in files if row.get("entrypoint") or row.get("path") == "main.py"]
    entrypoint_ids = {id(row) for row in entrypoints}
    rest = [row for row in files if id(row) not in entrypoint_ids]
    return entrypoints + rest


def _merge_file_rows(
    left: Mapping[str, Any],
    right: Mapping[str, Any],
    *,
    dependency_limit: int,
    public_api_limit: int,
    acceptance_limit: int,
) -> dict[str, Any]:
    primary = right if _file_row_score(right) >= _file_row_score(left) else left
    secondary = left if primary is right else right
    return {
        "path": str(primary.get("path", "")),
        "purpose": _text(primary.get("purpose")) or _text(secondary.get("purpose")),
        "dependencies": _merge_unique(
            _list(primary.get("dependencies")),
            _list(secondary.get("dependencies")),
            limit=dependency_limit,
        ),
        "public_api": _merge_unique(
            _list(primary.get("public_api")),
            _list(secondary.get("public_api")),
            limit=public_api_limit,
        ),
        "acceptance_criteria": _merge_unique(
            _list(primary.get("acceptance_criteria")),
            _list(secondary.get("acceptance_criteria")),
            limit=acceptance_limit,
        ),
        "entrypoint": bool(primary.get("entrypoint")) or bool(secondary.get("entrypoint")),
    }


def _file_row_score(row: Mapping[str, Any]) -> int:
    return (
        len(_text(row.get("purpose")))
        + 30 * len(_list(row.get("acceptance_criteria")))
        + 20 * len(_list(row.get("public_api")))
        + 10 * len(_list(row.get("dependencies")))
    )


def _merge_unique(first: list[str], second: list[str], *, limit: int) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in [*first, *second]:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
        if len(result) >= limit:
            break
    return result


def _strip_generated_root_prefix(value: str) -> str:
    text = value.replace("\\", "/").strip().lstrip("/")
    for prefix in ("generated_project/",):
        if text == prefix[:-1]:
            return ""
        if text.startswith(prefix):
            return text[len(prefix) :]
    return text


def _text(value: object) -> str:
    return str(value).strip() if value is not None else ""


def _list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []
