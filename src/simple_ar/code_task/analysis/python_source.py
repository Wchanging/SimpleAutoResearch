from __future__ import annotations

"""Small Python-source quality checks used by generated code-task flows."""

import ast
from typing import Any


def non_ascii_identifiers(source: str, *, path: str = "") -> list[dict[str, Any]]:
    """Return Python identifiers that contain non-ASCII characters.

    String literals and comments may legitimately contain non-ASCII text. Public
    identifiers in generated experiment code should stay ASCII so imports,
    structured edits, and cross-file API checks remain stable across terminals
    and providers.
    """

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    rows: list[dict[str, Any]] = []
    for node in ast.walk(tree):
        for kind, value in _identifier_values(node):
            if value and not value.isascii():
                rows.append(
                    {
                        "path": path,
                        "line": int(getattr(node, "lineno", 0) or 0),
                        "kind": kind,
                        "identifier": value,
                    }
                )
    seen: set[tuple[str, int, str, str]] = set()
    unique: list[dict[str, Any]] = []
    for row in rows:
        key = (str(row["path"]), int(row["line"]), str(row["kind"]), str(row["identifier"]))
        if key in seen:
            continue
        seen.add(key)
        unique.append(row)
    return unique


def has_non_ascii_identifier(source: str, *, path: str = "") -> bool:
    return bool(non_ascii_identifiers(source, path=path))


def _identifier_values(node: ast.AST) -> list[tuple[str, str]]:
    if isinstance(node, ast.Name):
        return [("name", node.id)]
    if isinstance(node, ast.Attribute):
        return [("attribute", node.attr)]
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return [("function", node.name)]
    if isinstance(node, ast.ClassDef):
        return [("class", node.name)]
    if isinstance(node, ast.arg):
        return [("argument", node.arg)]
    if isinstance(node, ast.alias):
        values = [("import", node.name)]
        if node.asname:
            values.append(("import_alias", node.asname))
        return values
    return []
