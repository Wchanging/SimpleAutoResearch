from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from simple_ar.artifacts import write_json, write_text
from simple_ar.code_task.environment import build_code_task_environment_policy
from simple_ar.code_task.index import build_codebase_index
from simple_ar.code_task.workspace import CopyReport, copy_code_workspace


@dataclass(frozen=True)
class CodeTaskInitResult:
    """Result returned after initializing a code-task workspace.

    Args:
        run_dir: Root run directory for this code task.
        task_dir: Directory containing all code-task artifacts.
        workspace_dir: Isolated editable copy of the source code.
        meta_dir: Metadata directory for indexes and manifests.
        manifest_path: Root workflow manifest path.
        codebase_index_path: Path to the generated codebase index.
        copy_report: Summary of copied and skipped source paths.
        codebase_index: Generated index data.
        environment_policy: Initial execution environment policy.
    """

    run_dir: Path
    task_dir: Path
    workspace_dir: Path
    meta_dir: Path
    manifest_path: Path
    codebase_index_path: Path
    copy_report: CopyReport
    codebase_index: dict[str, Any]
    environment_policy: dict[str, Any]


def initialize_code_task(
    *,
    run_dir: Path,
    code_root: Path,
    task_file: Path,
    benchmark_command: str | None = None,
    max_file_bytes: int = 2_000_000,
    env_mode: str = "current",
    python_executable: str | Path | None = None,
) -> CodeTaskInitResult:
    """Initialize a local code-task run without modifying the source project.

    Args:
        run_dir: New run directory. It may exist, but must be empty.
        code_root: Existing codebase or benchmark directory to copy.
        task_file: Markdown or text file describing the requested change.
        benchmark_command: Optional validation command to record for later
            stages. It is not executed during init.
        max_file_bytes: Maximum file size copied into the workspace. Use ``0``
            to disable the size guard.
        env_mode: Execution environment mode. V2.1 supports ``current`` and
            ``external``.
        python_executable: External interpreter path or executable name when
            ``env_mode`` is ``external``.

    Returns:
        Paths and metadata for the initialized code-task run.

    Raises:
        FileNotFoundError: If ``code_root`` or ``task_file`` is missing.
        NotADirectoryError: If ``code_root`` is not a directory.
        FileExistsError: If ``run_dir`` already contains files.
    """
    source_root = Path(code_root).resolve()
    task_source = Path(task_file).resolve()
    root = Path(run_dir)
    if root.exists() and any(root.iterdir()):
        raise FileExistsError(f"Run directory already contains files: {root}")
    if not task_source.exists():
        raise FileNotFoundError(f"Task file does not exist: {task_source}")
    if not task_source.is_file():
        raise FileNotFoundError(f"Task file is not a regular file: {task_source}")

    task_dir = root / "code_task"
    workspace_dir = task_dir / "workspace"
    meta_dir = task_dir / "meta"
    task_dir.mkdir(parents=True, exist_ok=True)
    meta_dir.mkdir(parents=True, exist_ok=True)

    write_text(task_dir / "task.md", task_source.read_text(encoding="utf-8", errors="replace"))
    copy_report = copy_code_workspace(
        source_root,
        workspace_dir,
        max_file_bytes=max_file_bytes,
    )
    codebase_index_path = meta_dir / "codebase_index.json"
    codebase_index = build_codebase_index(workspace_dir, output_path=codebase_index_path)
    environment_policy = build_code_task_environment_policy(
        env_mode=env_mode,
        python_executable=python_executable,
    )
    manifest_path = root / "manifest.json"
    write_json(
        manifest_path,
        _manifest(
            run_dir=root,
            code_root=source_root,
            task_file=task_source,
            benchmark_command=benchmark_command,
            max_file_bytes=max_file_bytes,
            copy_report=copy_report,
            codebase_index=codebase_index,
            environment_policy=environment_policy,
        ),
    )

    return CodeTaskInitResult(
        run_dir=root,
        task_dir=task_dir,
        workspace_dir=workspace_dir,
        meta_dir=meta_dir,
        manifest_path=manifest_path,
        codebase_index_path=codebase_index_path,
        copy_report=copy_report,
        codebase_index=codebase_index,
        environment_policy=environment_policy,
    )


def _manifest(
    *,
    run_dir: Path,
    code_root: Path,
    task_file: Path,
    benchmark_command: str | None,
    max_file_bytes: int,
    copy_report: CopyReport,
    codebase_index: dict[str, Any],
    environment_policy: dict[str, Any],
) -> dict[str, Any]:
    project = codebase_index.get("project", {})
    return {
        "schema_version": 1,
        "workflow": "code_task",
        "status": "initialized",
        "created_at": _utcnow_iso(),
        "run_dir": str(run_dir),
        "source": {
            "code_root": str(code_root),
            "task_file": str(task_file),
        },
        "layout": {
            "task": "code_task/task.md",
            "workspace": "code_task/workspace",
            "meta": "code_task/meta",
            "codebase_index": "code_task/meta/codebase_index.json",
        },
        "copy": {
            **copy_report.to_json(),
            "max_file_bytes": max_file_bytes,
        },
        "codebase": {
            "file_count": project.get("file_count", 0),
            "python_file_count": project.get("python_file_count", 0),
            "test_file_count": project.get("test_file_count", 0),
            "entrypoint_candidates": project.get("entrypoint_candidates", []),
        },
        "environment": {
            "status": "not_probed",
            "policy": environment_policy,
        },
        "benchmark": {
            "command": benchmark_command,
            "executed": False,
        },
    }


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
