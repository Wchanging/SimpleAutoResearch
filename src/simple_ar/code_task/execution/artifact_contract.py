from __future__ import annotations

"""Runtime artifact contract checks for code-task executions.

The scanner is intentionally deterministic and framework-level: it checks
where required runtime artifacts landed, whether they are readable, and
whether a same-name artifact appears in a nearby but wrong location. It does
not judge benchmark-specific scientific content.
"""

import json
import re
from pathlib import Path
from typing import Any, Mapping

from simple_ar.code_task.generation.common import safe_relative_path, string_list
from simple_ar.code_task.generation.task_contract import load_task_contract
from simple_ar.code_task.runtime.state import code_task_paths, is_relative_to


ARTIFACT_SCAN_SCHEMA_VERSION = "code_task_artifact_scan.v1"
DEFAULT_GREENFIELD_ARTIFACTS = ("artifacts/results.json", "artifacts/report.md")
SKIP_PARTS = {".git", ".venv", "venv", "__pycache__", "node_modules"}


def scan_required_artifacts(
    run_dir: Path,
    manifest: Mapping[str, Any],
    *,
    required_artifacts: list[str] | None = None,
    max_candidates_per_artifact: int = 12,
) -> dict[str, Any]:
    """Scan required artifacts under the code-task workspace.

    Args:
        run_dir: Code-task run directory.
        manifest: Loaded code-task manifest.
        required_artifacts: Optional project-relative artifact paths. When
            omitted, paths are read from ``task_contract.json`` and fall back to
            a minimal greenfield artifact set.
        max_candidates_per_artifact: Maximum same-name candidates to retain per
            artifact.

    Returns:
        A compact JSON-serializable scan report.
    """

    paths = code_task_paths(run_dir)
    workspace = paths.workspace_dir
    code_task = manifest.get("code_task")
    kind = str(code_task.get("kind", "")) if isinstance(code_task, Mapping) else ""
    contract = load_task_contract(paths.meta_dir)
    if required_artifacts is not None:
        artifacts = list(required_artifacts)
    else:
        artifacts = _required_artifacts_from_contract(contract)
    # A current task contract with an explicit empty artifact list means that
    # the task only requires its declared outputs (for example, printed
    # metrics). Keep the legacy default only for older runs with no contract.
    if kind == "greenfield" and not artifacts and not contract:
        artifacts = list(DEFAULT_GREENFIELD_ARTIFACTS)

    expected_rows: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    for artifact in artifacts:
        normalized = _normalize_artifact_path(artifact)
        if not normalized:
            continue
        expected = _workspace_artifact_path(normalized, kind=kind)
        expected_path = workspace / expected
        row = _artifact_row(
            workspace=workspace,
            contract_path=normalized,
            expected_path=expected,
            path=expected_path,
        )
        candidates = _same_name_candidates(
            workspace=workspace,
            expected_path=expected_path,
            name=Path(normalized).name,
            limit=max_candidates_per_artifact,
        )
        row["same_name_candidates"] = candidates
        expected_rows.append(row)
        findings.extend(_artifact_findings(row, candidates))

    return {
        "schema_version": ARTIFACT_SCAN_SCHEMA_VERSION,
        "workspace": str(workspace),
        "kind": kind or "unknown",
        "artifact_count": len(expected_rows),
        "artifacts": expected_rows,
        "findings": findings,
        "status": "warning" if findings else "passed",
    }


def compact_artifact_scan(scan: Mapping[str, Any], *, max_artifacts: int = 8) -> dict[str, Any]:
    """Return the prompt-safe subset of an artifact scan."""

    rows = scan.get("artifacts")
    findings = scan.get("findings")
    return {
        "schema_version": scan.get("schema_version", ARTIFACT_SCAN_SCHEMA_VERSION),
        "status": scan.get("status", "unknown"),
        "artifact_count": scan.get("artifact_count", 0),
        "findings": list(findings if isinstance(findings, list) else [])[:max_artifacts],
        "artifacts": [
            {
                "contract_path": row.get("contract_path", ""),
                "expected_path": row.get("expected_path", ""),
                "exists": row.get("exists", False),
                "size": row.get("size", 0),
                "parse_status": row.get("parse_status", ""),
                "same_name_candidates": list(row.get("same_name_candidates", []))[:4]
                if isinstance(row.get("same_name_candidates"), list)
                else [],
            }
            for row in list(rows if isinstance(rows, list) else [])[:max_artifacts]
            if isinstance(row, Mapping)
        ],
    }


def has_artifact_path_mismatch(scan: Mapping[str, Any]) -> bool:
    """Return true when the scan found a valid same-name artifact elsewhere."""

    findings = scan.get("findings")
    return any(
        isinstance(row, Mapping) and row.get("code") == "artifact_path_mismatch"
        for row in findings if isinstance(findings, list)
    )


def expected_artifact_row(scan: Mapping[str, Any], suffix: str) -> dict[str, Any] | None:
    """Return the expected artifact row whose path ends with ``suffix``."""

    rows = scan.get("artifacts")
    suffix = suffix.replace("\\", "/").strip("/")
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, Mapping):
            continue
        expected = str(row.get("expected_path", "")).replace("\\", "/").strip("/")
        contract = str(row.get("contract_path", "")).replace("\\", "/").strip("/")
        if expected.endswith(suffix) or contract.endswith(suffix):
            return dict(row)
    return None


def _required_artifacts_from_contract(contract: Mapping[str, Any]) -> list[str]:
    artifact_contract = contract.get("artifact_contract")
    if not isinstance(artifact_contract, Mapping):
        return []
    values: list[str] = []
    for item in string_list(artifact_contract.get("required_artifacts"), limit=80):
        extracted = _artifact_paths_from_text(item)
        if extracted and _artifact_line_requires_runtime_write(item):
            values.extend(extracted)
        elif _looks_like_clean_artifact_path(item):
            values.append(item)
    return list(dict.fromkeys(values))


def _artifact_paths_from_text(value: str) -> list[str]:
    text = str(value or "").replace("\\", "/")
    paths = re.findall(r"(?<![\w./-])(?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+\.[A-Za-z0-9]+", text)
    return [path for path in dict.fromkeys(paths) if safe_relative_path(path)]


def _artifact_line_requires_runtime_write(value: str) -> bool:
    text = str(value or "").strip().lower()
    if _looks_like_clean_artifact_path(text):
        return True
    if any(token in text for token in ("downstream", "adapter", "convert this run", "will convert")):
        return False
    return any(
        token in text
        for token in (
            "write ",
            "writes ",
            "written ",
            "produce ",
            "produces ",
            "save ",
            "saves ",
            "generate ",
            "generates ",
            "emit ",
            "emits ",
            "create ",
            "creates ",
        )
    )


def _looks_like_clean_artifact_path(value: str) -> bool:
    text = str(value or "").strip().strip("`'\"").replace("\\", "/")
    if not text or any(char.isspace() for char in text) or ":" in text:
        return False
    return bool(
        re.fullmatch(r"(?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+\.[A-Za-z0-9]+", text)
        and safe_relative_path(text)
    )


def _normalize_artifact_path(value: str) -> str:
    text = str(value or "").strip().strip("`'\"")
    if not text:
        return ""
    # Keep explicit paths and ignore prose-only deliverables.
    if not _looks_like_clean_artifact_path(text):
        return ""
    normalized = safe_relative_path(text.replace("\\", "/"))
    if not normalized:
        return ""
    if normalized.endswith("/"):
        return ""
    return normalized


def _workspace_artifact_path(contract_path: str, *, kind: str) -> str:
    path = contract_path.strip("/").replace("\\", "/")
    if kind == "greenfield" and not path.startswith("generated_project/"):
        return f"generated_project/{path}"
    return path


def _artifact_row(
    *,
    workspace: Path,
    contract_path: str,
    expected_path: str,
    path: Path,
) -> dict[str, Any]:
    status = _parse_status(path)
    return {
        "contract_path": contract_path,
        "expected_path": expected_path,
        "workspace_relative": _relative_to_workspace(workspace, path),
        "exists": path.is_file(),
        "size": _file_size(path),
        "parse_status": status,
    }


def _same_name_candidates(
    *,
    workspace: Path,
    expected_path: Path,
    name: str,
    limit: int,
) -> list[dict[str, Any]]:
    if not workspace.is_dir() or not name:
        return []
    expected = expected_path.resolve()
    candidates: list[dict[str, Any]] = []
    workspace_resolved = workspace.resolve()
    for path in workspace.rglob(name):
        if len(candidates) >= limit:
            break
        if not path.is_file() or _should_skip(path, workspace=workspace_resolved):
            continue
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if resolved == expected:
            continue
        if not is_relative_to(resolved, workspace_resolved):
            continue
        candidates.append(
            {
                "path": _relative_to_workspace(workspace, path),
                "size": _file_size(path),
                "parse_status": _parse_status(path),
            }
        )
    return candidates


def _artifact_findings(row: Mapping[str, Any], candidates: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    expected_path = str(row.get("expected_path", ""))
    parse_status = str(row.get("parse_status", ""))
    exists = bool(row.get("exists", False))
    usable_candidates = [
        candidate for candidate in candidates if _usable_artifact_status(str(candidate.get("parse_status", "")))
    ]
    if (not exists or not _usable_artifact_status(parse_status)) and usable_candidates:
        findings.append(
            {
                "code": "artifact_path_mismatch",
                "severity": "blocking",
                "expected_path": expected_path,
                "expected_status": parse_status,
                "candidate_paths": [str(item.get("path", "")) for item in usable_candidates[:4]],
                "message": (
                    "Expected runtime artifact is missing or unusable, but a same-name artifact "
                    "was written elsewhere in the workspace."
                ),
            }
        )
    if exists and parse_status == "empty":
        findings.append(
            {
                "code": "empty_expected_artifact",
                "severity": "blocking",
                "expected_path": expected_path,
                "message": "Expected runtime artifact exists but is empty.",
            }
        )
    elif exists and parse_status in {"invalid_json", "unreadable"}:
        findings.append(
            {
                "code": f"{parse_status}_expected_artifact",
                "severity": "blocking",
                "expected_path": expected_path,
                "message": f"Expected runtime artifact is {parse_status}.",
            }
        )
    return findings


def _usable_artifact_status(status: str) -> bool:
    return status in {"valid_json", "nonempty_text"}


def _parse_status(path: Path) -> str:
    if not path.is_file():
        return "missing"
    try:
        size = path.stat().st_size
    except OSError:
        return "unreadable"
    if size <= 0:
        return "empty"
    if path.suffix.lower() == ".json":
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return "invalid_json"
        return "valid_json"
    return "nonempty_text"


def _file_size(path: Path) -> int:
    try:
        return int(path.stat().st_size)
    except OSError:
        return 0


def _relative_to_workspace(workspace: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(workspace.resolve()).as_posix()
    except (OSError, ValueError):
        return str(path)


def _should_skip(path: Path, *, workspace: Path) -> bool:
    try:
        parts = path.resolve().relative_to(workspace).parts
    except (OSError, ValueError):
        parts = path.parts
    return any(part in SKIP_PARTS or part.startswith(".") for part in parts)
