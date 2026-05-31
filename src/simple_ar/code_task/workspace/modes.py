from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from simple_ar.code_task.workspace.copy import (
    CopyReport,
    copy_code_workspace,
    empty_copy_report,
    sparse_copy_code_workspace,
)


SUPPORTED_WORKSPACE_MODES = {"copy", "git_worktree", "sparse_copy"}
DEPENDENCY_FILE_NAMES = (
    "pyproject.toml",
    "requirements.txt",
    "requirements-dev.txt",
    "environment.yml",
    "environment.yaml",
    "Pipfile",
    "poetry.lock",
    "uv.lock",
)


class WorkspaceModeError(RuntimeError):
    """Raised when a code-task workspace mode cannot be created safely."""


@dataclass(frozen=True)
class WorkspaceSpec:
    """Configuration for creating an editable code-task workspace.

    Args:
        code_root: Source project directory provided by the user.
        task_dir: Code-task artifact directory.
        mode: Workspace strategy. V2.2 supports ``copy`` and
            ``git_worktree``; ``sparse_copy`` is experimental.
        max_file_bytes: Maximum copied file size for ``copy`` and
            ``sparse_copy`` modes.
        include: POSIX glob patterns copied by ``sparse_copy``.
        exclude: Additional POSIX glob patterns skipped by ``sparse_copy``.
        reuse_source_venv: Whether a detected source ``.venv`` may be recorded
            and selected as the initial execution interpreter.
        setup_hook: Optional setup command recorded for future managed
            environment support. V2.2 does not execute it during init.
    """

    code_root: Path
    task_dir: Path
    mode: str = "copy"
    max_file_bytes: int = 2_000_000
    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    reuse_source_venv: bool = False
    setup_hook: str = ""


@dataclass(frozen=True)
class WorkspaceResult:
    """Result returned by workspace creation.

    Args:
        mode: Workspace strategy used.
        source_root: Resolved user source directory.
        workspace_dir: Editable root consumed by downstream code-task stages.
        writable_root: Root that patching and benchmark execution may mutate.
        read_only_roots: Additional source roots used for provenance only.
        created_at: UTC creation timestamp.
        copy_report: Copy summary for ``copy`` mode; empty for worktrees.
        git: Git provenance for ``git_worktree`` mode.
        environment_mapping: Dependency and interpreter mapping notes.
        patterns: Include/exclude patterns used by sparse workspace modes.
        cleanup_hint: Human-facing cleanup note for this mode.
    """

    mode: str
    source_root: Path
    workspace_dir: Path
    writable_root: Path
    read_only_roots: tuple[Path, ...]
    created_at: str
    copy_report: CopyReport
    git: dict[str, Any] | None
    environment_mapping: dict[str, Any]
    patterns: dict[str, Any]
    cleanup_hint: str

    def to_manifest(self, *, run_dir: Path) -> dict[str, Any]:
        """Return a compact manifest section for this workspace."""
        return {
            "schema_version": 1,
            "mode": self.mode,
            "source_root": str(self.source_root),
            "workspace_dir": _relative_or_string(run_dir, self.workspace_dir),
            "writable_root": _relative_or_string(run_dir, self.writable_root),
            "read_only_roots": [
                _relative_or_string(run_dir, path) for path in self.read_only_roots
            ],
            "created_at": self.created_at,
            "copy_report": self.copy_report.to_json(),
            "git": self.git or {},
            "environment_mapping": self.environment_mapping,
            "patterns": self.patterns,
            "cleanup_hint": self.cleanup_hint,
        }


def create_workspace(spec: WorkspaceSpec) -> WorkspaceResult:
    """Create the editable workspace requested by ``spec``."""
    mode = _normalize_mode(spec.mode)
    if mode == "copy":
        return _create_copy_workspace(spec)
    if mode == "git_worktree":
        return _create_git_worktree_workspace(spec)
    if mode == "sparse_copy":
        return _create_sparse_copy_workspace(spec)
    raise WorkspaceModeError(f"Unsupported workspace mode: {spec.mode}")


def suggested_python_executable(result: WorkspaceResult) -> str | None:
    """Return a mapped Python executable when workspace options request one."""
    value = result.environment_mapping.get("python_executable")
    return value if isinstance(value, str) and value else None


def _create_copy_workspace(spec: WorkspaceSpec) -> WorkspaceResult:
    source = _validated_source_root(spec.code_root)
    workspace = (spec.task_dir / "workspace").resolve()
    copy_report = copy_code_workspace(
        source,
        workspace,
        max_file_bytes=spec.max_file_bytes,
    )
    return WorkspaceResult(
        mode="copy",
        source_root=source,
        workspace_dir=workspace,
        writable_root=workspace,
        read_only_roots=(),
        created_at=_utcnow_iso(),
        copy_report=copy_report,
        git=None,
        environment_mapping=_environment_mapping(
            source_root=source,
            workspace_dir=workspace,
            reuse_source_venv=spec.reuse_source_venv,
            setup_hook=spec.setup_hook,
            mode="copy",
        ),
        patterns={},
        cleanup_hint="Delete the run directory to remove this copied workspace.",
    )


def _create_sparse_copy_workspace(spec: WorkspaceSpec) -> WorkspaceResult:
    source = _validated_source_root(spec.code_root)
    workspace = (spec.task_dir / "workspace").resolve()
    copy_report = sparse_copy_code_workspace(
        source,
        workspace,
        include_patterns=spec.include,
        exclude_patterns=spec.exclude,
        max_file_bytes=spec.max_file_bytes,
    )
    return WorkspaceResult(
        mode="sparse_copy",
        source_root=source,
        workspace_dir=workspace,
        writable_root=workspace,
        read_only_roots=(),
        created_at=_utcnow_iso(),
        copy_report=copy_report,
        git=None,
        environment_mapping=_environment_mapping(
            source_root=source,
            workspace_dir=workspace,
            reuse_source_venv=spec.reuse_source_venv,
            setup_hook=spec.setup_hook,
            mode="sparse_copy",
        ),
        patterns={
            "include": list(_normalize_patterns(spec.include)),
            "exclude": list(_normalize_patterns(spec.exclude)),
            "default_includes_used": not bool(spec.include),
            "default_excludes_always_applied": True,
            "risk": (
                "sparse_copy may omit runtime dependencies. Prefer copy or "
                "git_worktree unless you know the needed project subset."
            ),
        },
        cleanup_hint="Delete the run directory to remove this sparse workspace.",
    )


def _create_git_worktree_workspace(spec: WorkspaceSpec) -> WorkspaceResult:
    source = _validated_source_root(spec.code_root)
    workspace = (spec.task_dir / "workspace").resolve()
    if workspace.exists() and any(workspace.iterdir()):
        raise FileExistsError(f"Workspace already contains files: {workspace}")

    repo_root = _git_repo_root(source)
    if not _same_path(source, repo_root):
        raise WorkspaceModeError(
            "git_worktree mode currently requires code_root to be the git "
            f"repository root.\n"
            f"code_root: {source}\n"
            f"detected repo root: {repo_root}\n"
            "Next steps:\n"
            "- Pass the detected repo root as --code-root and adjust the benchmark path if needed.\n"
            "- Or make the intended baseline directory its own git repository: "
            "`git init`, `git add .`, `git commit -m \"initial baseline\"`.\n"
            "- Or rerun with --workspace-mode copy for a guarded physical copy."
        )

    commit = _git_head_commit(repo_root)
    branch = _git_output(repo_root, "branch", "--show-current") or "detached"
    dirty_status = _git_output(repo_root, "status", "--short")
    workspace.parent.mkdir(parents=True, exist_ok=True)
    _run_git(repo_root, "worktree", "add", "--detach", str(workspace), commit)

    return WorkspaceResult(
        mode="git_worktree",
        source_root=source,
        workspace_dir=workspace,
        writable_root=workspace,
        read_only_roots=(source,),
        created_at=_utcnow_iso(),
        copy_report=empty_copy_report(),
        git={
            "repo_root": str(repo_root),
            "origin_branch": branch,
            "origin_commit": commit,
            "origin_dirty": bool(dirty_status.strip()),
            "origin_status_sample": dirty_status.splitlines()[:50],
            "worktree_path": str(workspace),
            "worktree_commit": commit,
            "mode": "detached",
        },
        environment_mapping=_environment_mapping(
            source_root=source,
            workspace_dir=workspace,
            reuse_source_venv=spec.reuse_source_venv,
            setup_hook=spec.setup_hook,
            mode="git_worktree",
        ),
        patterns={},
        cleanup_hint=(
            "Remove this workspace with `git worktree remove <workspace>` or "
            "delete the run directory and prune stale worktrees."
        ),
    )


def _validated_source_root(code_root: Path) -> Path:
    source = Path(code_root).resolve()
    if not source.exists():
        raise FileNotFoundError(f"Code root does not exist: {source}")
    if not source.is_dir():
        raise NotADirectoryError(f"Code root is not a directory: {source}")
    return source


def _normalize_mode(value: str) -> str:
    mode = str(value or "copy").strip().lower().replace("-", "_")
    if mode not in SUPPORTED_WORKSPACE_MODES:
        raise WorkspaceModeError(
            "workspace mode must be one of: "
            + ", ".join(sorted(SUPPORTED_WORKSPACE_MODES))
        )
    return mode


def _normalize_patterns(patterns: tuple[str, ...]) -> tuple[str, ...]:
    result: list[str] = []
    for pattern in patterns:
        text = str(pattern).replace("\\", "/").strip().strip("/")
        if text:
            result.append(text)
    return tuple(dict.fromkeys(result))


def _git_repo_root(path: Path) -> Path:
    try:
        output = _git_output(path, "rev-parse", "--show-toplevel")
    except WorkspaceModeError as exc:
        if "git executable" in str(exc) or "timed out" in str(exc):
            raise
        raise WorkspaceModeError(
            "git_worktree mode requires code_root to be inside a local git "
            f"repository, but Git could not find a usable repository at: {path}\n"
            "Next steps:\n"
            "- If this project does not need git isolation, rerun with --workspace-mode copy.\n"
            "- If you want git_worktree, run these commands in the baseline project root: "
            "`git init`, `git add .`, `git commit -m \"initial baseline\"`.\n"
            "- If the project lives in a subdirectory, make that subdirectory its own git repository root."
        ) from exc
    if not output:
        raise WorkspaceModeError(f"Path is not inside a git repository: {path}")
    return Path(output).resolve()


def _git_head_commit(repo_root: Path) -> str:
    try:
        return _git_output(repo_root, "rev-parse", "HEAD")
    except WorkspaceModeError as exc:
        raise WorkspaceModeError(
            "git_worktree mode requires the baseline repository to have at "
            f"least one commit: {repo_root}\n"
            "Next steps:\n"
            "- Commit the baseline first: `git add .` then `git commit -m \"initial baseline\"`.\n"
            "- Or rerun with --workspace-mode copy if you do not want to use git yet."
        ) from exc


def _git_output(cwd: Path, *args: str) -> str:
    completed = _run_git(cwd, *args)
    return completed.stdout.strip()


def _run_git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    try:
        completed = subprocess.run(
            ["git", "-c", "safe.directory=*", "-C", str(cwd), *args],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except FileNotFoundError as exc:
        raise WorkspaceModeError("git executable was not found on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise WorkspaceModeError(f"git command timed out: {' '.join(args)}") from exc
    if completed.returncode != 0:
        message = completed.stderr.strip() or completed.stdout.strip()
        raise WorkspaceModeError(
            f"git command failed ({' '.join(args)}): {message}"
        )
    return completed


def _environment_mapping(
    *,
    source_root: Path,
    workspace_dir: Path,
    reuse_source_venv: bool,
    setup_hook: str,
    mode: str,
) -> dict[str, Any]:
    source_venv_python = _source_venv_python(source_root)
    selected_python = str(source_venv_python) if reuse_source_venv and source_venv_python else ""
    dependency_files = _dependency_files(workspace_dir)
    if not dependency_files and not workspace_dir.exists():
        dependency_files = _dependency_files(source_root)
    notes = [
        "Workspace creation does not install dependencies.",
        "Use env_mode=external or workspace.reuse_source_venv for existing project environments.",
    ]
    return {
        "schema_version": 1,
        "mode": mode,
        "dependency_files": dependency_files,
        "reuse_source_venv": reuse_source_venv,
        "source_venv_detected": str(source_venv_python) if source_venv_python else "",
        "python_executable": selected_python,
        "setup_hook": setup_hook.strip(),
        "setup_hook_executed": False,
        "notes": notes,
    }


def _source_venv_python(source_root: Path) -> Path | None:
    candidates = (
        source_root / ".venv" / "Scripts" / "python.exe",
        source_root / ".venv" / "bin" / "python",
        source_root / "venv" / "Scripts" / "python.exe",
        source_root / "venv" / "bin" / "python",
    )
    for path in candidates:
        if path.exists() and path.is_file():
            return path.resolve()
    return None


def _dependency_files(root: Path) -> list[str]:
    if not root.exists() or not root.is_dir():
        return []
    return [name for name in DEPENDENCY_FILE_NAMES if (root / name).is_file()]


def _relative_or_string(root: Path, path: Path) -> str:
    try:
        return Path(path).resolve().relative_to(Path(root).resolve()).as_posix()
    except ValueError:
        return str(path)


def _same_path(left: Path, right: Path) -> bool:
    try:
        return left.samefile(right)
    except OSError:
        return os.path.normcase(str(left.resolve())) == os.path.normcase(str(right.resolve()))


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
