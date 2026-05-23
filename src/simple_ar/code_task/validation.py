from __future__ import annotations

import ast
import importlib.util
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from simple_ar.artifacts import write_json
from simple_ar.code_task.attempts import (
    load_latest_code_task_batch,
    update_code_task_batch_state,
)
from simple_ar.code_task.state import (
    code_task_paths,
    load_code_task_manifest,
    manifest_section,
    save_code_task_manifest,
    utcnow_iso,
)


RISKY_IMPORTS = {
    "ctypes",
    "ftplib",
    "http",
    "requests",
    "shutil",
    "signal",
    "smtplib",
    "socket",
    "subprocess",
    "urllib",
}

RISKY_CALLS = {
    "__import__",
    "compile",
    "eval",
    "exec",
    "os.popen",
    "os.system",
    "shutil.rmtree",
    "subprocess.Popen",
    "subprocess.call",
    "subprocess.check_call",
    "subprocess.check_output",
    "subprocess.run",
}


@dataclass(frozen=True)
class CodeTaskValidationResult:
    """Result returned after validating a code-task workspace.

    Args:
        run_dir: Code-task run directory.
        report_path: Path to ``validation_report.json``.
        status: ``passed`` when no errors were found, otherwise ``failed``.
        error_count: Number of validation errors.
        warning_count: Number of validation warnings.
        issue_count: Total number of issues.
    """

    run_dir: Path
    report_path: Path
    status: str
    error_count: int
    warning_count: int
    issue_count: int


def validate_code_task(
    run_dir: Path,
    *,
    strict: bool = False,
    max_file_bytes: int = 500_000,
) -> CodeTaskValidationResult:
    """Validate Python files in a code-task workspace.

    The validator is intentionally lightweight. Syntax errors are always
    errors. Risky imports and calls are warnings by default, and become errors
    in strict mode. This keeps ordinary benchmark projects usable while still
    making security-sensitive behavior visible.

    Args:
        run_dir: Code-task run directory.
        strict: Treat risky imports/calls as errors.
        max_file_bytes: Per-file scan budget. Larger files are warned and
            skipped for static analysis.

    Returns:
        Validation summary and report location.

    Raises:
        FileNotFoundError: If the run or workspace is missing.
        RuntimeError: If ``run_dir`` is not a code-task run.
    """
    manifest = load_code_task_manifest(run_dir)
    paths = code_task_paths(run_dir)
    if not paths.workspace_dir.is_dir():
        raise FileNotFoundError(f"Missing code-task workspace: {paths.workspace_dir}")

    issues: list[dict[str, Any]] = []
    scanned_files = 0
    python_files = 0
    for path in _iter_workspace_files(paths.workspace_dir):
        scanned_files += 1
        rel_path = path.relative_to(paths.workspace_dir).as_posix()
        size = path.stat().st_size
        if max_file_bytes > 0 and size > max_file_bytes:
            issues.append(
                _issue(
                    severity="warning",
                    code="file_too_large",
                    path=rel_path,
                    message=f"Skipped static scan because file is larger than {max_file_bytes} bytes.",
                )
            )
            continue
        if path.suffix.lower() != ".py":
            continue
        python_files += 1
        _validate_python_file(
            path,
            rel_path=rel_path,
            workspace_dir=paths.workspace_dir,
            strict=strict,
            issues=issues,
        )

    error_count = sum(1 for item in issues if item["severity"] == "error")
    warning_count = sum(1 for item in issues if item["severity"] == "warning")
    status = "failed" if error_count else "passed"
    report = {
        "schema_version": 1,
        "generated_at": utcnow_iso(),
        "status": status,
        "strict": strict,
        "max_file_bytes": max_file_bytes,
        "workspace": str(paths.workspace_dir),
        "file_count": scanned_files,
        "python_file_count": python_files,
        "issue_count": len(issues),
        "error_count": error_count,
        "warning_count": warning_count,
        "issues": issues,
    }
    report_path = paths.meta_dir / "validation_report.json"
    write_json(report_path, report)
    _update_manifest_after_validation(
        run_dir,
        manifest,
        status=status,
        strict=strict,
        error_count=error_count,
        warning_count=warning_count,
    )
    if _patch_applied(manifest):
        _update_latest_batch_after_validation(run_dir, report_path, status)
    return CodeTaskValidationResult(
        run_dir=paths.run_dir,
        report_path=report_path,
        status=status,
        error_count=error_count,
        warning_count=warning_count,
        issue_count=len(issues),
    )


def _validate_python_file(
    path: Path,
    *,
    rel_path: str,
    workspace_dir: Path,
    strict: bool,
    issues: list[dict[str, Any]],
) -> None:
    text = path.read_text(encoding="utf-8", errors="replace")
    try:
        tree = ast.parse(text, filename=rel_path)
    except SyntaxError as exc:
        issues.append(
            _issue(
                severity="error",
                code="syntax_error",
                path=rel_path,
                line=exc.lineno,
                column=exc.offset,
                message=exc.msg,
            )
        )
        return

    imported_names = _imports(tree)
    for name, line in imported_names:
        if name in RISKY_IMPORTS:
            issues.append(
                _issue(
                    severity="error" if strict else "warning",
                    code="risky_import",
                    path=rel_path,
                    line=line,
                    message=f"Import `{name}` can perform external, destructive, or network operations.",
                )
            )
        if not _import_available(name, workspace_dir):
            issues.append(
                _issue(
                    severity="warning",
                    code="missing_import",
                    path=rel_path,
                    line=line,
                    message=f"Import `{name}` was not found in the workspace, stdlib, or current environment.",
                )
            )

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        call_name = _call_name(node.func)
        if call_name in RISKY_CALLS:
            issues.append(
                _issue(
                    severity="error" if strict else "warning",
                    code="risky_call",
                    path=rel_path,
                    line=getattr(node, "lineno", None),
                    message=f"Call `{call_name}` may mutate the system or execute dynamic code.",
                )
            )


def _imports(tree: ast.AST) -> list[tuple[str, int | None]]:
    names: list[tuple[str, int | None]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.append((alias.name.split(".", 1)[0], node.lineno))
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                continue
            if node.module:
                names.append((node.module.split(".", 1)[0], node.lineno))
    return names


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _call_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return ""


def _import_available(name: str, workspace_dir: Path) -> bool:
    if not name:
        return True
    if name in sys.builtin_module_names:
        return True
    stdlib_names = getattr(sys, "stdlib_module_names", set())
    if name in stdlib_names:
        return True
    for root in (workspace_dir, workspace_dir / "src"):
        if (root / f"{name}.py").is_file():
            return True
        if (root / name / "__init__.py").is_file():
            return True
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, AttributeError, ValueError):
        return False


def _iter_workspace_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for current, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            dirname
            for dirname in dirnames
            if dirname not in {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
        ]
        current_path = Path(current)
        for filename in filenames:
            path = current_path / filename
            if path.is_file():
                files.append(path)
    files.sort(key=lambda item: item.relative_to(root).as_posix())
    return files


def _issue(
    *,
    severity: str,
    code: str,
    path: str,
    message: str,
    line: int | None = None,
    column: int | None = None,
) -> dict[str, Any]:
    return {
        "severity": severity,
        "code": code,
        "path": path,
        "line": line,
        "column": column,
        "message": message,
    }


def _update_manifest_after_validation(
    run_dir: Path,
    manifest: dict[str, Any],
    *,
    status: str,
    strict: bool,
    error_count: int,
    warning_count: int,
) -> None:
    layout = manifest_section(manifest, "layout")
    layout["validation_report"] = "code_task/meta/validation_report.json"
    validation = manifest_section(manifest, "validation")
    validation.update(
        {
            "status": status,
            "generated_at": utcnow_iso(),
            "strict": strict,
            "report": "code_task/meta/validation_report.json",
            "error_count": error_count,
            "warning_count": warning_count,
        }
    )
    manifest["layout"] = layout
    manifest["validation"] = validation
    manifest["status"] = "validated" if status == "passed" else "validation_failed"
    save_code_task_manifest(run_dir, manifest)


def _update_latest_batch_after_validation(run_dir: Path, report_path: Path, status: str) -> None:
    batch = load_latest_code_task_batch(run_dir)
    if batch is None:
        return
    update_code_task_batch_state(
        run_dir,
        batch.batch_state_path,
        state="failed" if status == "failed" else "validating",
        artifacts={"validation_report": _relative_to_run(run_dir, report_path)},
        detail=f"Static validation {status}.",
        extra={"validation_status": status},
    )


def _relative_to_run(run_dir: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(Path(run_dir).resolve()).as_posix()
    except ValueError:
        return str(path)


def _patch_applied(manifest: dict[str, Any]) -> bool:
    patch = manifest.get("patch")
    return isinstance(patch, dict) and patch.get("status") == "applied"
