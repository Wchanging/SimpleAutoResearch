"""Opt-in project venv preparation through the existing process backend.

The preparation attempt owns the environment and receipts; this is not another
executor, installer service or environment database. Installation is executable
project input, not evidence that an experiment is scientifically valid.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
import os
from pathlib import Path
import sys
from typing import Any

from simple_ar.core.capabilities import CapabilityContext, CapabilityResult
from simple_ar.experiment.execution.backend import ExecutionBackend, LocalExecutionBackend
from simple_ar.code_task.execution.environment import resolve_code_task_command


def environment_profile(value: Any, *, project: Path | None = None) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) - {"mode", "requirements", "install_project", "python_executable", "timeout_sec"}:
        raise ValueError("execution.environment accepts mode, requirements, install_project, python_executable and timeout_sec only.")
    if value.get("mode") != "venv":
        raise ValueError('execution.environment.mode must be "venv"; omit the table to use the current environment.')
    requirements = value.get("requirements", [])
    if not isinstance(requirements, (list, tuple)) or any(not isinstance(item, str) or not item.strip() for item in requirements):
        raise ValueError("environment.requirements must list project-relative requirements files.")
    for item in requirements:
        path = Path(item)
        if path.is_absolute() or ".." in path.parts or "\\" in item or item.startswith("-"):
            raise ValueError("Requirements paths must stay inside the named execution project.")
        if project is not None:
            resolved = (project / path).resolve()
            if not resolved.is_relative_to(project.resolve()) or not resolved.is_file():
                raise ValueError(f"Requirements file not found inside the project: {item}")
    install_project = value.get("install_project", False)
    if type(install_project) is not bool:
        raise ValueError("environment.install_project must be a boolean; omission does not install the project.")
    if install_project and project is not None:
        if not any((project / name).is_file() for name in ("pyproject.toml", "setup.py", "setup.cfg")):
            raise ValueError("Project installation requires pyproject.toml, setup.py or setup.cfg in execution.cwd; a declaration does not guarantee a build succeeds.")
    python = value.get("python_executable", sys.executable)
    if not isinstance(python, str) or not python.strip():
        raise ValueError("environment.python_executable must be an executable name/path.")
    timeout = value.get("timeout_sec", 300)
    if type(timeout) is not int or timeout < 1:
        raise ValueError("environment.timeout_sec must be positive.")
    return {"mode": "venv", "requirements": list(dict.fromkeys(requirements)), "install_project": install_project,
            "python_executable": python, "timeout_sec": timeout}


def prepare_project_environment(*, context: CapabilityContext, request: Any,
                                backend: ExecutionBackend | None = None) -> CapabilityResult:
    config = dict(request.execution)
    if request.run is None or "code_task" in config or "dataset" in config or config.get("pairs"):
        raise ValueError("Venv preparation currently supports a single declared command, not CodeTask, text-baseline or paired execution.")
    project = request.run.cwd.resolve()
    profile = environment_profile(config["environment"], project=project)
    if not project.is_dir():
        raise ValueError(f"Execution project not found: {project}")
    # Only the ordinary 'python' aliases are substituted. An explicit binary,
    # uv/conda launcher or shell remains the user's selected execution protocol.
    if request.run.command[0] not in {"python", "python3", "python.exe", "python3.exe"}:
        raise ValueError("Task venv requires the command's first argument to be python/python3; explicit interpreters and non-Python launchers are not silently replaced.")
    directory = context.store.root / "environment"
    if directory.exists():
        raise ValueError("Preparation environment already exists; use its recorded result or an explicit new attempt, not an in-place replay.")
    python = directory / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    steps = [[profile["python_executable"], "-m", "venv", str(directory)]]
    if profile["requirements"] or profile["install_project"]:
        steps.append([str(python), "-m", "pip", "install", "--disable-pip-version-check",
                      *[arg for path in profile["requirements"] for arg in ("-r", str(project / path))],
                      *([str(project)] if profile["install_project"] else [])])
    steps.append([str(python), "-m", "pip", "check"])
    observed = []
    refs = []
    selected_backend = backend or LocalExecutionBackend()
    limitations = ["Isolated task venv; not an OS sandbox. Approved requirements/project installation may run build code, access package indexes and write build metadata in the source project.",
                   "Successful installation and pip check do not prove project imports, binary/GPU compatibility, data splits or scientific validity.",
                   "No automatic data download, framework-directed project-code edits or changes to the scientific command's arguments."]
    for number, argv in enumerate(steps, 1):
        # Allocate before launch. An interrupted attempt retains this unknown
        # result; normal completed recovery uses the existing prepared_execution.
        observed.append({"command": argv, "status": "allocated_result_unknown"})
        context.store.write_json("environment_setup.json", {"profile": profile, "steps": observed,
            "limitations": limitations}, kind="environment_setup", schema="environment_setup.v1")
        result = selected_backend.run(replace(request.run, command=argv, timeout_sec=profile["timeout_sec"],
            label=f"environment:{number}", output_dir=context.store.root / "environment_processes"))
        observed[-1] = result.to_json()
        refs.extend((context.store.write_text(f"environment_logs/{number}.stdout.txt", result.stdout, kind="execution_log"),
                     context.store.write_text(f"environment_logs/{number}.stderr.txt", result.stderr, kind="execution_log")))
        ref = context.store.write_json("environment_setup.json", {"profile": profile, "steps": observed,
            "limitations": limitations}, kind="environment_setup", schema="environment_setup.v1")
        if result.status != "passed":
            return CapabilityResult(status="failed", artifacts=(*refs, ref),
                diagnostics=(f"Environment preparation step {number} {result.status}; the scientific command was not run.",))
    config.pop("environment")
    config["command"] = resolve_code_task_command(request.run.command, env_mode="external", python_executable=str(python))
    prepared = context.store.write_json("execution.json", {"schema_version": "prepared_execution.v1",
        "execution": config, "source_project": str(project), "workspace": str(project),
        "environment": {"python_executable": str(python), "setup_ref": ref.to_dict(), "requirements": profile["requirements"],
                        "install_project": profile["install_project"]},
        "limitations": limitations}, kind="prepared_execution", schema="prepared_execution.v1", producer="research.preparation")
    return CapabilityResult(status="completed", artifacts=(*refs, ref, prepared))
