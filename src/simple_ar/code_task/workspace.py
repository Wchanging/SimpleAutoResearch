from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_IGNORED_DIR_NAMES = {
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
    "build",
    "dist",
    "node_modules",
    "runs",
    "venv",
}

DEFAULT_IGNORED_FILE_NAMES = {
    ".DS_Store",
    ".env",
}

DEFAULT_IGNORED_SUFFIXES = {
    ".pyc",
    ".pyd",
    ".pyo",
}

MAX_RECORDED_SKIPS = 200


@dataclass(frozen=True)
class CopyReport:
    """Summary of a safe workspace copy operation.

    Args:
        files_copied: Number of regular files copied into the workspace.
        bytes_copied: Total bytes copied.
        skipped_count: Total number of skipped files and directories.
        skipped: Bounded list of skipped files or directories with reasons.
    """

    files_copied: int
    bytes_copied: int
    skipped_count: int
    skipped: tuple[dict[str, Any], ...]

    def to_json(self) -> dict[str, Any]:
        """Return a JSON-serializable representation of the copy report."""
        return {
            "files_copied": self.files_copied,
            "bytes_copied": self.bytes_copied,
            "skipped_count": self.skipped_count,
            "skipped_record_limit": MAX_RECORDED_SKIPS,
            "skipped": list(self.skipped),
        }


def copy_code_workspace(
    code_root: Path,
    workspace_dir: Path,
    *,
    max_file_bytes: int = 2_000_000,
) -> CopyReport:
    """Copy a code root into an isolated, editable workspace.

    The copy is intentionally conservative: it skips common cache/build
    directories, secrets such as ``.env``, symlinks, bytecode, and files above
    ``max_file_bytes``. Later code-task stages should mutate only this copied
    workspace, never the original source directory.

    Args:
        code_root: Existing project or benchmark directory to copy.
        workspace_dir: Destination workspace inside the code-task run.
        max_file_bytes: Maximum size for files to copy. Use ``0`` to disable
            this size guard.

    Returns:
        Summary of copied and skipped paths.

    Raises:
        FileNotFoundError: If ``code_root`` does not exist.
        NotADirectoryError: If ``code_root`` is not a directory.
        FileExistsError: If ``workspace_dir`` already contains files.
    """
    source = Path(code_root).resolve()
    workspace = Path(workspace_dir).resolve()
    if not source.exists():
        raise FileNotFoundError(f"Code root does not exist: {source}")
    if not source.is_dir():
        raise NotADirectoryError(f"Code root is not a directory: {source}")
    if max_file_bytes < 0:
        raise ValueError("max_file_bytes must be >= 0")
    if workspace.exists() and any(workspace.iterdir()):
        raise FileExistsError(f"Workspace already contains files: {workspace}")

    workspace.mkdir(parents=True, exist_ok=True)
    skipped: list[dict[str, Any]] = []
    skipped_count = 0
    files_copied = 0
    bytes_copied = 0

    for current, dirnames, filenames in os.walk(source):
        current_path = Path(current)
        filtered_dirnames: list[str] = []
        for dirname in dirnames:
            path = current_path / dirname
            reason = _skip_dir_reason(path, source, workspace)
            if reason:
                skipped_count += 1
                _record_skip(skipped, source, path, "dir", reason)
                continue
            filtered_dirnames.append(dirname)
        dirnames[:] = filtered_dirnames

        for filename in filenames:
            path = current_path / filename
            reason = _skip_file_reason(path, max_file_bytes=max_file_bytes)
            if reason:
                skipped_count += 1
                _record_skip(skipped, source, path, "file", reason)
                continue

            relative_path = path.relative_to(source)
            destination = _safe_destination(workspace, relative_path)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)
            copied_size = destination.stat().st_size
            files_copied += 1
            bytes_copied += copied_size

    return CopyReport(
        files_copied=files_copied,
        bytes_copied=bytes_copied,
        skipped_count=skipped_count,
        skipped=tuple(skipped),
    )


def _skip_dir_reason(path: Path, source: Path, workspace: Path) -> str | None:
    name = path.name
    if path.is_symlink():
        return "symlink_dir"
    if name in DEFAULT_IGNORED_DIR_NAMES or name.startswith("."):
        return "ignored_dir"
    resolved = path.resolve()
    if _is_relative_to(workspace, resolved) or _is_relative_to(resolved, workspace):
        return "output_workspace"
    if not _is_relative_to(resolved, source):
        return "outside_source"
    return None


def _skip_file_reason(path: Path, *, max_file_bytes: int) -> str | None:
    name = path.name
    if path.is_symlink():
        return "symlink_file"
    if name in DEFAULT_IGNORED_FILE_NAMES:
        return "ignored_file"
    if name.startswith(".env") and not name.endswith(".example"):
        return "possible_secret"
    if path.suffix.lower() in DEFAULT_IGNORED_SUFFIXES:
        return "ignored_suffix"
    try:
        size = path.stat().st_size
    except OSError:
        return "stat_failed"
    if max_file_bytes > 0 and size > max_file_bytes:
        return "file_too_large"
    return None


def _safe_destination(workspace: Path, relative_path: Path) -> Path:
    destination = (workspace / relative_path).resolve()
    if not _is_relative_to(destination, workspace):
        raise ValueError(f"Refusing to copy outside workspace: {relative_path}")
    return destination


def _record_skip(
    skipped: list[dict[str, Any]],
    source: Path,
    path: Path,
    kind: str,
    reason: str,
) -> None:
    if len(skipped) >= MAX_RECORDED_SKIPS:
        return
    try:
        relative = path.relative_to(source).as_posix()
    except ValueError:
        relative = str(path)
    skipped.append({"path": relative, "kind": kind, "reason": reason})


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False
