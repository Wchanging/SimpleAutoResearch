from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from simple_ar.core.artifacts import write_json, write_text
from simple_ar.code_task.execution.comparison import normalize_metric_directions
from simple_ar.code_task.editing.scope import default_edit_scope
from simple_ar.code_task.execution.environment import build_code_task_environment_policy
from simple_ar.code_task.analysis.index import build_codebase_index
from simple_ar.code_task.analysis.repo_map import build_repo_map
from simple_ar.code_task.workspace.copy import CopyReport
from simple_ar.code_task.workspace.modes import (
    WorkspaceResult,
    WorkspaceSpec,
    create_workspace,
    suggested_python_executable,
)


@dataclass(frozen=True)
class CodeTaskInitResult:
    """Result returned after initializing a code-task workspace.

    Args:
        run_dir: Root run directory for this code task.
        task_dir: Directory containing all code-task artifacts.
        workspace_dir: Isolated editable workspace for the source code.
        meta_dir: Metadata directory for indexes and manifests.
        manifest_path: Root workflow manifest path.
        codebase_index_path: Path to the generated codebase index.
        repo_map_path: Path to the generated layered repository map.
        repo_map_summary_path: Path to the generated repository map summary.
        copy_report: Summary of copied and skipped source paths.
        codebase_index: Generated index data.
        repo_map: Generated layered repository map.
        environment_policy: Initial execution environment policy.
        workspace: Workspace creation metadata.
    """

    run_dir: Path
    task_dir: Path
    workspace_dir: Path
    meta_dir: Path
    manifest_path: Path
    codebase_index_path: Path
    repo_map_path: Path
    repo_map_summary_path: Path
    copy_report: CopyReport
    codebase_index: dict[str, Any]
    repo_map: dict[str, Any]
    environment_policy: dict[str, Any]
    workspace: WorkspaceResult


def initialize_code_task(
    *,
    run_dir: Path,
    code_root: Path,
    task_file: Path,
    benchmark_command: str | None = None,
    max_file_bytes: int = 2_000_000,
    workspace_mode: str = "copy",
    workspace_include: tuple[str, ...] = (),
    workspace_exclude: tuple[str, ...] = (),
    workspace_reuse_source_venv: bool = False,
    workspace_setup_hook: str = "",
    env_mode: str = "current",
    python_executable: str | Path | None = None,
    primary_metric: str | None = None,
    metric_directions: dict[str, str] | None = None,
) -> CodeTaskInitResult:
    """Initialize a local code-task run without modifying the source project.

    Args:
        run_dir: New run directory. It may exist, but must be empty.
        code_root: Existing codebase or benchmark directory to copy.
        task_file: Markdown or text file describing the requested change.
        benchmark_command: Optional validation command to record for later
            stages. It is not executed during init.
        max_file_bytes: Maximum file size copied in ``copy`` and
            ``sparse_copy`` modes. Use ``0`` to disable the size guard.
        workspace_mode: Workspace strategy. ``copy`` preserves the V2.1
            behavior; ``git_worktree`` creates a detached git worktree when
            ``code_root`` is a repository root; ``sparse_copy`` is experimental.
        workspace_include: POSIX glob patterns copied by sparse mode.
        workspace_exclude: Additional POSIX glob patterns skipped by sparse
            mode.
        workspace_reuse_source_venv: Whether a detected source ``.venv`` may
            be used as the initial external Python interpreter.
        workspace_setup_hook: Optional setup command recorded for future
            managed-environment support. It is not executed during init.
        env_mode: Execution environment mode. V2.1 supports ``current`` and
            ``external``.
        python_executable: External interpreter path or executable name when
            ``env_mode`` is ``external``.
        primary_metric: Optional primary metric used for conservative
            baseline-vs-patched verdicts.
        metric_directions: Optional mapping from metric name to direction.
            Supported normalized directions are ``higher_is_better``,
            ``lower_is_better``, ``resource``, and ``ignore``.

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
    primary_metric_value = (primary_metric or "").strip()
    normalized_metric_directions = normalize_metric_directions(metric_directions)

    task_dir = root / "code_task"
    meta_dir = task_dir / "meta"
    task_dir.mkdir(parents=True, exist_ok=True)
    meta_dir.mkdir(parents=True, exist_ok=True)

    write_text(task_dir / "task.md", task_source.read_text(encoding="utf-8", errors="replace"))
    workspace = create_workspace(
        WorkspaceSpec(
            code_root=source_root,
            task_dir=task_dir,
            mode=workspace_mode,
            max_file_bytes=max_file_bytes,
            include=workspace_include,
            exclude=workspace_exclude,
            reuse_source_venv=workspace_reuse_source_venv,
            setup_hook=workspace_setup_hook,
        )
    )
    workspace_dir = workspace.workspace_dir
    copy_report = workspace.copy_report
    codebase_index_path = meta_dir / "codebase_index.json"
    codebase_index = build_codebase_index(workspace_dir, output_path=codebase_index_path)
    edit_scope = default_edit_scope()
    protected_patterns = tuple(str(item) for item in edit_scope["protected_patterns"])
    repo_map_path = meta_dir / "repo_map.json"
    repo_map_summary_path = meta_dir / "repo_map_summary.md"
    repo_map = build_repo_map(
        codebase_index,
        output_path=repo_map_path,
        summary_path=repo_map_summary_path,
        protected_patterns=protected_patterns,
    )
    resolved_env_mode = env_mode
    resolved_python = python_executable
    mapped_python = suggested_python_executable(workspace)
    if mapped_python and resolved_python is None and resolved_env_mode in {"current", "external"}:
        resolved_env_mode = "external"
        resolved_python = mapped_python
    environment_policy = build_code_task_environment_policy(
        env_mode=resolved_env_mode,
        python_executable=resolved_python,
    )
    manifest_path = root / "manifest.json"
    write_json(
        manifest_path,
        _manifest(
            run_dir=root,
            code_root=source_root,
            task_file=task_source,
            benchmark_command=benchmark_command,
            primary_metric=primary_metric_value,
            metric_directions=normalized_metric_directions,
            max_file_bytes=max_file_bytes,
            copy_report=copy_report,
            workspace=workspace,
            codebase_index=codebase_index,
            repo_map=repo_map,
            edit_scope=edit_scope,
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
        repo_map_path=repo_map_path,
        repo_map_summary_path=repo_map_summary_path,
        copy_report=copy_report,
        codebase_index=codebase_index,
        repo_map=repo_map,
        environment_policy=environment_policy,
        workspace=workspace,
    )


def _manifest(
    *,
    run_dir: Path,
    code_root: Path,
    task_file: Path,
    benchmark_command: str | None,
    primary_metric: str | None,
    metric_directions: dict[str, str] | None,
    max_file_bytes: int,
    copy_report: CopyReport,
    workspace: WorkspaceResult,
    codebase_index: dict[str, Any],
    repo_map: dict[str, Any],
    edit_scope: dict[str, Any],
    environment_policy: dict[str, Any],
) -> dict[str, Any]:
    project = codebase_index.get("project", {})
    repo_project = repo_map.get("project", {})
    primary = (primary_metric or "").strip()
    directions = dict(metric_directions or {})
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
            "repo_map": "code_task/meta/repo_map.json",
            "repo_map_summary": "code_task/meta/repo_map_summary.md",
        },
        "copy": {
            **copy_report.to_json(),
            "max_file_bytes": max_file_bytes,
        },
        "workspace": workspace.to_manifest(run_dir=run_dir),
        "codebase": {
            "file_count": project.get("file_count", 0),
            "python_file_count": project.get("python_file_count", 0),
            "test_file_count": project.get("test_file_count", 0),
            "entrypoint_candidates": project.get("entrypoint_candidates", []),
            "repo_map": {
                "schema_version": repo_map.get("schema_version"),
                "path": "code_task/meta/repo_map.json",
                "summary": "code_task/meta/repo_map_summary.md",
                "directory_count": repo_project.get("directory_count", 0),
                "symbol_count": repo_project.get("symbol_count", 0),
                "benchmark_file_count": repo_project.get("benchmark_file_count", 0),
                "config_file_count": repo_project.get("config_file_count", 0),
            },
        },
        "environment": {
            "status": "not_probed",
            "policy": environment_policy,
        },
        "edit_scope": edit_scope,
        "benchmark": {
            "command": benchmark_command,
            "executed": False,
            "primary_metric": primary,
            "metric_directions": directions,
        },
    }


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
