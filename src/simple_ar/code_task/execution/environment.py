from __future__ import annotations

import importlib.util
import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from simple_ar.artifacts import read_json, write_json
from simple_ar.code_task.runtime.state import (
    code_task_paths,
    load_code_task_manifest,
    manifest_section,
    save_code_task_manifest,
    utcnow_iso,
)
from simple_ar.code_task.execution.summary import write_code_task_summary


DEPENDENCY_FILE_NAMES = {
    "environment.yaml",
    "environment.yml",
    "Pipfile",
    "poetry.lock",
    "pyproject.toml",
    "requirements-dev.txt",
    "requirements.txt",
    "setup.cfg",
    "setup.py",
    "uv.lock",
}
SUPPORTED_ENV_MODES = {"current", "external"}


@dataclass(frozen=True)
class CodeTaskEnvironmentResult:
    """Result returned after probing a copied code-task workspace.

    Args:
        run_dir: Code-task run directory.
        report_path: Path to ``code_task/meta/environment_report.json``.
        status: ``ok`` or ``warning`` depending on probe findings.
        warnings: Non-fatal limitations discovered during probing.
        tools: Tool availability mapping from the environment report.
        gpu: GPU availability summary from the environment report.
    """

    run_dir: Path
    report_path: Path
    status: str
    warnings: tuple[str, ...] = field(default_factory=tuple)
    tools: dict[str, Any] = field(default_factory=dict)
    gpu: dict[str, Any] = field(default_factory=dict)


def probe_code_task_environment(
    run_dir: Path,
    *,
    env_mode: str | None = None,
    python_executable: str | Path | None = None,
) -> CodeTaskEnvironmentResult:
    """Probe runtime and project environment for a code-task workspace.

    The probe is intentionally observational: it does not install dependencies,
    import project modules, run tests, or mutate the copied workspace. The report
    gives later planning and benchmark stages a stable view of the local
    constraints they should respect.

    Args:
        run_dir: Code-task run directory.
        env_mode: Optional execution environment mode to record before the
            probe. Supported now: ``current`` and ``external``.
        python_executable: External interpreter path or executable name when
            ``env_mode`` is ``external``.

    Returns:
        Paths and compact status for the generated environment report.

    Raises:
        FileNotFoundError: If the run is not a code-task run or the workspace is
            missing.
    """
    return _probe_code_task_environment(
        run_dir,
        env_mode=env_mode,
        python_executable=python_executable,
    )


def ensure_code_task_environment_policy(
    run_dir: Path,
    manifest: dict[str, Any],
    *,
    env_mode: str | None = None,
    python_executable: str | Path | None = None,
) -> dict[str, Any]:
    """Return a normalized environment policy and persist it if needed."""
    policy = _resolved_environment_policy(
        manifest,
        env_mode=env_mode,
        python_executable=python_executable,
    )
    environment = manifest_section(manifest, "environment")
    if environment.get("policy") != policy:
        environment.setdefault("status", "not_probed")
        environment["policy"] = policy
        manifest["environment"] = environment
        save_code_task_manifest(run_dir, manifest)
    return policy


def build_code_task_environment_policy(
    *,
    env_mode: str = "current",
    python_executable: str | Path | None = None,
) -> dict[str, Any]:
    """Build the initial execution environment policy for a code-task run."""
    mode = _normalize_env_mode(env_mode)
    executable = _resolve_policy_python(mode, python_executable)
    return {
        "schema_version": 1,
        "mode": mode,
        "python_executable": executable,
        "python_version": _python_version_for_policy(mode, executable),
        "allow_dependency_install": False,
        "dependency_install": "disabled",
        "managed": False,
        "created_at": utcnow_iso(),
        "notes": [
            "This policy selects the interpreter used for benchmark commands.",
            "It does not create environments or install dependencies.",
        ],
    }


def _probe_code_task_environment(
    run_dir: Path,
    *,
    env_mode: str | None = None,
    python_executable: str | Path | None = None,
) -> CodeTaskEnvironmentResult:
    manifest = load_code_task_manifest(run_dir)
    paths = code_task_paths(run_dir)
    if not paths.workspace_dir.is_dir():
        raise FileNotFoundError(f"Missing code-task workspace: {paths.workspace_dir}")
    paths.meta_dir.mkdir(parents=True, exist_ok=True)

    policy = ensure_code_task_environment_policy(
        paths.run_dir,
        manifest,
        env_mode=env_mode,
        python_executable=python_executable,
    )
    manifest = load_code_task_manifest(run_dir)
    codebase_index = _load_codebase_index(paths.meta_dir / "codebase_index.json")
    report = _build_environment_report(paths.workspace_dir, manifest, codebase_index, policy)
    report_path = paths.meta_dir / "environment_report.json"
    write_json(report_path, report)
    _update_manifest_after_probe(run_dir, manifest, report)
    write_code_task_summary(run_dir)
    return CodeTaskEnvironmentResult(
        run_dir=paths.run_dir,
        report_path=report_path,
        status=str(report.get("status", "unknown")),
        warnings=tuple(str(item) for item in report.get("warnings", []) if item),
        tools=report.get("tools", {}) if isinstance(report.get("tools"), dict) else {},
        gpu=report.get("gpu", {}) if isinstance(report.get("gpu"), dict) else {},
    )


def _build_environment_report(
    workspace_dir: Path,
    manifest: dict[str, Any],
    codebase_index: dict[str, Any],
    policy: dict[str, Any],
) -> dict[str, Any]:
    warnings: list[str] = []
    if not codebase_index:
        warnings.append("codebase_index_missing")
    benchmark = manifest.get("benchmark", {})
    if not (isinstance(benchmark, dict) and benchmark.get("command")):
        warnings.append("benchmark_command_missing")

    gpu = _gpu_summary(warnings)
    project = _project_summary(workspace_dir, codebase_index)
    if not project["dependency_files"]:
        warnings.append("dependency_file_not_found")
    if not project["test_dirs"]:
        warnings.append("test_directory_not_found")

    return {
        "schema_version": 1,
        "generated_at": utcnow_iso(),
        "status": "warning" if warnings else "ok",
        "platform": _platform_summary(),
        "python": _python_summary(),
        "execution_policy": policy,
        "tools": _tools_summary(),
        "python_modules": _python_modules_summary(),
        "execution_context": _execution_context_summary(),
        "gpu": gpu,
        "project": project,
        "warnings": warnings,
        "notes": [
            "This probe is observational. It does not install dependencies or run project code.",
            "Missing tools or files are recorded as planning constraints, not automatic failures.",
        ],
    }


def _resolved_environment_policy(
    manifest: dict[str, Any],
    *,
    env_mode: str | None,
    python_executable: str | Path | None,
) -> dict[str, Any]:
    environment = manifest_section(manifest, "environment")
    existing = environment.get("policy")
    existing_policy = existing if isinstance(existing, dict) else {}
    if env_mode is None and python_executable is None and _policy_is_supported(existing_policy):
        return dict(existing_policy)
    mode = _normalize_env_mode(env_mode or str(existing_policy.get("mode") or "current"))
    executable = python_executable
    if (
        executable is None
        and mode == "external"
        and existing_policy.get("mode") == "external"
    ):
        executable = existing_policy.get("python_executable")
    return build_code_task_environment_policy(
        env_mode=mode,
        python_executable=executable,
    )


def _policy_is_supported(policy: dict[str, Any]) -> bool:
    mode = policy.get("mode")
    executable = policy.get("python_executable")
    return isinstance(mode, str) and mode in SUPPORTED_ENV_MODES and bool(executable)


def _normalize_env_mode(value: str) -> str:
    mode = value.strip().lower().replace("_", "-")
    if mode not in SUPPORTED_ENV_MODES:
        raise ValueError(
            "env_mode must be one of: " + ", ".join(sorted(SUPPORTED_ENV_MODES))
        )
    return mode


def _resolve_policy_python(mode: str, python_executable: str | Path | None) -> str:
    if mode == "current":
        return sys.executable
    if mode == "external":
        if python_executable is None or str(python_executable).strip() == "":
            raise ValueError("--python is required when --env-mode external is used")
        return _resolve_external_python(str(python_executable).strip())
    raise ValueError(f"Unsupported env_mode: {mode}")


def _resolve_external_python(value: str) -> str:
    has_path_separator = any(sep and sep in value for sep in (os.sep, os.altsep))
    candidate = Path(value).expanduser()
    if candidate.is_absolute() or has_path_separator:
        path = candidate.resolve()
        if not path.exists():
            raise FileNotFoundError(f"External Python executable does not exist: {path}")
        if path.is_dir():
            raise ValueError(f"External Python path is a directory: {path}")
        return str(path)
    resolved = shutil.which(value)
    if not resolved:
        raise FileNotFoundError(f"External Python executable not found on PATH: {value}")
    return str(Path(resolved).resolve())


def _python_version_for_policy(mode: str, executable: str) -> str:
    if mode == "current" and executable == sys.executable:
        return platform.python_version()
    try:
        completed = subprocess.run(
            [executable, "--version"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"
    text = (completed.stdout or completed.stderr).strip()
    return text.replace("Python ", "", 1) if text else "unknown"


def _platform_summary() -> dict[str, Any]:
    return {
        "system": platform.system(),
        "release": platform.release(),
        "version": platform.version(),
        "machine": platform.machine(),
        "processor": platform.processor(),
    }


def _python_summary() -> dict[str, Any]:
    return {
        "executable": sys.executable,
        "version": platform.python_version(),
        "implementation": platform.python_implementation(),
        "prefix": sys.prefix,
        "base_prefix": sys.base_prefix,
        "in_virtualenv": sys.prefix != sys.base_prefix,
        "conda_prefix": os.environ.get("CONDA_PREFIX"),
    }


def _tools_summary() -> dict[str, Any]:
    tools = {
        "python": {"available": True, "path": sys.executable},
    }
    for name in ("uv", "pip", "pytest", "git", "docker", "conda", "nvidia-smi"):
        tools[name] = _tool_summary(name)
    return tools


def _tool_summary(name: str) -> dict[str, Any]:
    path = shutil.which(name)
    return {
        "available": path is not None,
        "path": path,
    }


def _python_modules_summary() -> dict[str, bool]:
    return {
        name: importlib.util.find_spec(name) is not None
        for name in ("pip", "pytest", "numpy", "pandas", "sklearn")
    }


def _execution_context_summary() -> dict[str, Any]:
    release = platform.release().lower()
    return {
        "inside_docker": Path("/.dockerenv").exists() or os.environ.get("container") is not None,
        "inside_wsl": "microsoft" in release or bool(os.environ.get("WSL_DISTRO_NAME")),
        "cwd": str(Path.cwd()),
    }


def _gpu_summary(warnings: list[str]) -> dict[str, Any]:
    nvidia_smi = shutil.which("nvidia-smi")
    if not nvidia_smi:
        return {
            "available": False,
            "count": 0,
            "backend": None,
            "devices": [],
            "probe": "nvidia-smi_not_found",
        }
    try:
        completed = subprocess.run(
            [
                nvidia_smi,
                "--query-gpu=name,memory.total",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        warnings.append("gpu_probe_failed")
        return {
            "available": False,
            "count": 0,
            "backend": "nvidia-smi",
            "devices": [],
            "probe": "failed",
            "error": str(exc),
        }
    if completed.returncode != 0:
        warnings.append("gpu_probe_failed")
        return {
            "available": False,
            "count": 0,
            "backend": "nvidia-smi",
            "devices": [],
            "probe": "failed",
            "stderr": completed.stderr.strip(),
        }
    devices = [_gpu_device(line) for line in completed.stdout.splitlines() if line.strip()]
    return {
        "available": bool(devices),
        "count": len(devices),
        "backend": "nvidia-smi",
        "devices": devices,
        "probe": "ok",
    }


def _gpu_device(line: str) -> dict[str, Any]:
    parts = [part.strip() for part in line.split(",", maxsplit=1)]
    memory_mib: int | None = None
    if len(parts) > 1:
        try:
            memory_mib = int(float(parts[1]))
        except ValueError:
            memory_mib = None
    return {
        "name": parts[0],
        "memory_total_mib": memory_mib,
    }


def _project_summary(workspace_dir: Path, codebase_index: dict[str, Any]) -> dict[str, Any]:
    project = codebase_index.get("project", {}) if isinstance(codebase_index, dict) else {}
    return {
        "workspace": str(workspace_dir),
        "dependency_files": _dependency_files(workspace_dir),
        "test_dirs": _test_dirs(workspace_dir, codebase_index),
        "entrypoint_candidates": _list_value(project.get("entrypoint_candidates")),
        "top_level_entries": _list_value(project.get("top_level_entries")),
        "file_count": int(project.get("file_count", 0)) if isinstance(project, dict) else 0,
        "python_file_count": (
            int(project.get("python_file_count", 0)) if isinstance(project, dict) else 0
        ),
        "test_file_count": int(project.get("test_file_count", 0)) if isinstance(project, dict) else 0,
        "total_bytes": int(project.get("total_bytes", 0)) if isinstance(project, dict) else 0,
    }


def _dependency_files(workspace_dir: Path) -> list[str]:
    found: list[str] = []
    for path in workspace_dir.iterdir():
        if path.is_file() and path.name in DEPENDENCY_FILE_NAMES:
            found.append(path.name)
    return sorted(found)


def _test_dirs(workspace_dir: Path, codebase_index: dict[str, Any]) -> list[str]:
    dirs = {
        path.name
        for path in workspace_dir.iterdir()
        if path.is_dir() and path.name.lower() in {"test", "tests"}
    }
    files = codebase_index.get("files", []) if isinstance(codebase_index, dict) else []
    if isinstance(files, list):
        for item in files:
            if not isinstance(item, dict):
                continue
            role_tags = item.get("role_tags", [])
            if not (isinstance(role_tags, list) and "test" in role_tags):
                continue
            path = Path(str(item.get("path", "")))
            if path.parts:
                dirs.add(path.parts[0])
    return sorted(dirs)


def _list_value(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    return []


def _load_codebase_index(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = read_json(path)
    return data if isinstance(data, dict) else {}


def _update_manifest_after_probe(
    run_dir: Path,
    manifest: dict[str, Any],
    report: dict[str, Any],
) -> None:
    layout = manifest_section(manifest, "layout")
    layout["environment_report"] = "code_task/meta/environment_report.json"
    manifest["layout"] = layout
    environment = manifest_section(manifest, "environment")
    environment.update(
        {
            "status": report.get("status", "unknown"),
            "probed_at": report.get("generated_at"),
            "report": "code_task/meta/environment_report.json",
            "policy": report.get("execution_policy", environment.get("policy", {})),
            "platform": report.get("platform", {}),
            "python": report.get("python", {}),
            "gpu": report.get("gpu", {}),
            "warnings": report.get("warnings", []),
        }
    )
    manifest["environment"] = environment
    if manifest.get("status") == "initialized":
        manifest["status"] = "environment_probed"
    save_code_task_manifest(run_dir, manifest)
