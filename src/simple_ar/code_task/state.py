from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from simple_ar.artifacts import read_json, write_json


@dataclass(frozen=True)
class CodeTaskPaths:
    """Common paths for a code-task run.

    Args:
        run_dir: Root run directory.
        task_dir: Directory containing code-task artifacts.
        workspace_dir: Copied editable workspace.
        meta_dir: Metadata directory.
        run_artifact_dir: Latest benchmark execution directory.
        repairs_dir: Directory containing repair attempts.
        manifest_path: Root code-task manifest.
    """

    run_dir: Path
    task_dir: Path
    workspace_dir: Path
    meta_dir: Path
    run_artifact_dir: Path
    repairs_dir: Path
    manifest_path: Path


def code_task_paths(run_dir: Path) -> CodeTaskPaths:
    """Return the standard path layout for a code-task run."""
    root = Path(run_dir)
    task_dir = root / "code_task"
    return CodeTaskPaths(
        run_dir=root,
        task_dir=task_dir,
        workspace_dir=task_dir / "workspace",
        meta_dir=task_dir / "meta",
        run_artifact_dir=task_dir / "run",
        repairs_dir=task_dir / "repairs",
        manifest_path=root / "manifest.json",
    )


def load_code_task_manifest(run_dir: Path) -> dict[str, Any]:
    """Load and validate a code-task manifest."""
    paths = code_task_paths(run_dir)
    if not paths.manifest_path.exists():
        raise FileNotFoundError(f"Missing code-task manifest: {paths.manifest_path}")
    data = read_json(paths.manifest_path)
    if not isinstance(data, dict):
        raise RuntimeError(f"Expected JSON object in {paths.manifest_path}")
    if data.get("workflow") != "code_task":
        raise RuntimeError(f"Run is not a code-task workflow: {paths.run_dir}")
    return data


def save_code_task_manifest(run_dir: Path, manifest: dict[str, Any]) -> None:
    """Write a code-task manifest back to disk."""
    write_json(code_task_paths(run_dir).manifest_path, manifest)


def manifest_section(manifest: dict[str, Any], key: str) -> dict[str, Any]:
    """Return a mutable manifest section, creating it when necessary."""
    value = manifest.get(key)
    return value if isinstance(value, dict) else {}


def workspace_file(workspace_dir: Path, relative_path: str) -> Path | None:
    """Resolve a workspace-relative path without allowing traversal."""
    rel = Path(relative_path)
    if rel.is_absolute() or ".." in rel.parts:
        return None
    workspace = Path(workspace_dir).resolve()
    path = (workspace / rel).resolve()
    if not is_relative_to(path, workspace):
        return None
    return path


def is_relative_to(path: Path, parent: Path) -> bool:
    """Return true when ``path`` is inside ``parent``."""
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def utcnow_iso() -> str:
    """Return a compact UTC timestamp for artifacts."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
